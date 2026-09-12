from __future__ import annotations

import inspect

import pyromod  # noqa: F401 — must import before pyrogram handlers to trigger monkey-patching
import pyrogram.handlers.callback_query_handler
import pyrogram.handlers.message_handler


def _fix_pyromod_async_wrapping() -> None:
    """Undo ``async_to_sync`` on pyromod-patched handler methods.

    pyromod's ``patch_into`` passes every patched method through
    ``pyrogram.sync.async_to_sync``, which wraps the original
    ``async def`` inside a plain ``def async_to_sync_wrap``.
    kurigram's dispatcher checks
    ``inspect.iscoroutinefunction(handler.callback)`` — when the
    wrapper makes it return *False*, the dispatcher falls back to
    ``run_in_executor`` (thread-pool), creating a **different event
    loop** that breaks every ``await`` inside the handler with
    ``RuntimeError: Future attached to a different loop``.

    Fix: for each patched method whose ``__wrapped__`` is an async
    function, replace the class attribute with the original async
    version so the dispatcher ``await``s it on the main loop.
    """
    for cls in [
        pyrogram.handlers.callback_query_handler.CallbackQueryHandler,
        pyrogram.handlers.message_handler.MessageHandler,
    ]:
        for attr_name in list(vars(cls)):
            if attr_name.startswith("__"):
                continue
            attr = getattr(cls, attr_name)
            wrapped = getattr(attr, "__wrapped__", None)
            if wrapped is not None and inspect.iscoroutinefunction(wrapped):
                setattr(cls, attr_name, wrapped)


_fix_pyromod_async_wrapping()


def _fix_pyromod_listener_veto_on_matching_handler() -> None:
    """Stop stale pyromod listeners from vetoing unrelated handler regex matches."""
    try:
        from inspect import iscoroutinefunction

        from pyromod.config import config
        from pyromod.listen.callback_query_handler import CallbackQueryHandler as PyromodCQH
        from pyromod.types import Identifier
    except ImportError:
        return

    if getattr(PyromodCQH.check, "_musicbot_patched", False):
        return

    async def _check(self, client, query):
        listener_does_match, listener = await self.check_if_has_matching_listener(
            client, query
        )

        if callable(self.filters):
            if iscoroutinefunction(self.filters.__call__):
                handler_does_match = await self.filters(client, query)
            else:
                handler_does_match = await client.loop.run_in_executor(
                    None, self.filters, client, query
                )
        else:
            handler_does_match = True

        data = self.compose_data_identifier(query)

        if config.unallowed_click_alert:
            permissive_identifier = Identifier(
                chat_id=data.chat_id,
                message_id=data.message_id,
                inline_message_id=data.inline_message_id,
                from_user_id=None,
            )
            matches = permissive_identifier.matches(data)

            if (
                listener
                and (matches and not listener_does_match)
                and listener.unallowed_click_alert
                and not handler_does_match
            ):
                alert = (
                    listener.unallowed_click_alert
                    if isinstance(listener.unallowed_click_alert, str)
                    else config.unallowed_click_alert_text
                )
                await query.answer(alert)
                return False

        return listener_does_match or handler_does_match

    _check._musicbot_patched = True
    PyromodCQH.check = _check


_fix_pyromod_listener_veto_on_matching_handler()

from app.handlers import (  # noqa: E402
    add_helper,
    analytics_panel,
    broadcast,
    broadcast_panel,
    broadcast_wizard,
    call_security_panel,
    call_security_runtime,
    call_stats_panel,
    callbacks,
    credit_commands,
    dev_banall_panel,
    dev_panel,
    download,
    filter_words,
    service_messages,
    hot_seat,
    user_panel,
    force_join,
    force_join_panel,
    fast_creat_token_panel,
    global_ban_guard,
    group_text_call_commands,
    group_guard,
    group_panel,
    help_center,
    helper_otp_wizard,
    helper_panel,
    install,
    manager_text_commands,
    owner_panel,
    playback,
    playlist,
    promotion,
    search,
    start,
    start_customization_commands,
    sudo_panel,
    tv_radio,
    youtube_session_panel,
)

_MODULES = [
    group_guard,
    global_ban_guard,
    filter_words,
    service_messages,
    hot_seat,
    user_panel,
    start,
    group_text_call_commands,
    start_customization_commands,
    add_helper,
    manager_text_commands,
    dev_panel,
    dev_banall_panel,
    callbacks,
    owner_panel,
    sudo_panel,
    group_panel,
    call_security_panel,
    call_security_runtime,
    call_stats_panel,
    tv_radio,
    playback,
    playlist,
    promotion,
    broadcast,
    force_join,
    force_join_panel,
    broadcast_panel,
    broadcast_wizard,
    analytics_panel,
    help_center,
    helper_otp_wizard,
    helper_panel,
    install,
    search,
    download,
    credit_commands,
    youtube_session_panel,
    fast_creat_token_panel,
]


def _wrap_callback_handlers_with_auto_answer(bot) -> None:
    """Wrap every registered CallbackQueryHandler to auto-answer the query.

    This ensures the Telegram loading spinner is killed immediately,
    even if the handler takes time to query the DB or fails with an
    early return.  Also catches ``MessageNotModified`` so that
    edit_text calls with identical content don't crash the handler.

    Applied once after all modules are registered, so every handler
    benefits without manual changes.
    """
    import functools
    import logging

    import pyrogram

    from app.handlers.priority import (
        BOT_DISABLED_CALLBACK_GROUP,
        CALLBACK_TRACE_GROUP,
        FALLBACK_CALLBACK_GROUP,
        GLOBAL_BAN_GROUP,
        HELP_CALLBACK_GROUP,
        LANG_BIND_GROUP,
        PANEL_CALLBACK_GROUP,
        PYROMOD_CALLBACK_GROUP,
    )
    from app.utils.callback_trace import (
        get_or_create_callback_trace,
        mark_route_seen,
        trace_answer,
        trace_callback_event,
        trace_done,
        trace_failed,
        trace_handler_start,
        trace_route,
    )
    from app.utils.diagnostic_logging import log_callback_failure
    from app.utils.button_style import (
        bind_button_style_scope_from_update,
        reset_button_style_scope,
    )
    from app.utils.telegram_message import install_private_callback_edit_adapter

    _log = logging.getLogger("handlers.auto_answer")
    non_route_groups = {
        LANG_BIND_GROUP,
        CALLBACK_TRACE_GROUP,
        PYROMOD_CALLBACK_GROUP,
        BOT_DISABLED_CALLBACK_GROUP,
        GLOBAL_BAN_GROUP,
        FALLBACK_CALLBACK_GROUP,
    }
    # Pass-through guards/listeners must leave the answer for the selected route.
    no_auto_answer_groups = {
        LANG_BIND_GROUP,
        CALLBACK_TRACE_GROUP,
        PYROMOD_CALLBACK_GROUP,
        BOT_DISABLED_CALLBACK_GROUP,
        GLOBAL_BAN_GROUP,
    }
    terminal_route_groups = {HELP_CALLBACK_GROUP, PANEL_CALLBACK_GROUP}

    for group, group_handlers in bot.dispatcher.groups.items():
        is_route_group = group not in non_route_groups
        auto_answer_group = group not in no_auto_answer_groups
        for handler in group_handlers:
            if not isinstance(handler, pyrogram.handlers.callback_query_handler.CallbackQueryHandler):
                continue

            original_cb = handler.callback
            handler_name = getattr(original_cb, "__qualname__", getattr(original_cb, "__name__", "callback"))
            module_name = getattr(original_cb, "__module__", None)

            @functools.wraps(original_cb)
            async def _safe_cb(
                client,
                query,
                *args,
                _orig=original_cb,
                _handler_name=handler_name,
                _module_name=module_name,
                _group=group,
                _mark_route=is_route_group,
                _auto_answer=auto_answer_group,
                _stop_after_route=group in terminal_route_groups,
                **kwargs,
            ):
                style_scope_token = await bind_button_style_scope_from_update(query)
                get_or_create_callback_trace(query)
                original_answer = query.answer
                answered = False
                patch_answer = _auto_answer or _group == BOT_DISABLED_CALLBACK_GROUP

                async def _answer_once(*a, _source="handler", **k):
                    nonlocal answered
                    if answered or getattr(query, "_musicbot_callback_answered", False):
                        trace_answer(
                            query,
                            result="skipped_duplicate",
                            show_alert=k.get("show_alert"),
                            source=_source,
                        )
                        return None
                    answered = True
                    try:
                        result = await original_answer(*a, **k)
                        try:
                            setattr(query, "_musicbot_callback_answered", True)
                        except Exception:
                            pass
                        trace_answer(query, result="ok", show_alert=k.get("show_alert"), source=_source)
                        return result
                    except Exception as exc:
                        trace_answer(
                            query,
                            result="failed",
                            show_alert=k.get("show_alert"),
                            error=exc,
                            source=_source,
                        )
                        return None

                if patch_answer:
                    try:
                        query.answer = _answer_once
                    except Exception:
                        patch_answer = False

                if _mark_route:
                    mark_route_seen(query)
                    trace_route(
                        query,
                        handler=_handler_name,
                        module=_module_name,
                        group=_group,
                    )
                    trace_handler_start(
                        query,
                        handler=_handler_name,
                        module=_module_name,
                    )

                done_result = "ok"
                restore_panel_edits = install_private_callback_edit_adapter(
                    client,
                    query,
                )
                try:
                    try:
                        result = await _orig(client, query, *args, **kwargs)
                    except pyrogram.StopPropagation:
                        if _mark_route:
                            trace_done(
                                query,
                                handler=_handler_name,
                                module=_module_name,
                                result="stopped",
                            )
                        raise
                    except Exception as exc:
                        if "MessageNotModified" in type(exc).__name__:
                            _log.debug("MessageNotModified suppressed for %s", query.data)
                            done_result = "message_not_modified"
                            result = None
                        elif "MESSAGE_NOT_MODIFIED" in str(exc):
                            _log.debug("MESSAGE_NOT_MODIFIED suppressed for %s", query.data)
                            done_result = "message_not_modified"
                            result = None
                        else:
                            chat_id = None
                            if query.message and query.message.chat:
                                chat_id = query.message.chat.id
                            user_id = query.from_user.id if query.from_user else None
                            trace_failed(
                                query,
                                handler=_handler_name,
                                module=_module_name,
                                error=exc,
                            )
                            log_callback_failure(
                                exc,
                                handler=_handler_name,
                                user_id=user_id,
                                chat_id=chat_id,
                                callback_data=query.data,
                            )
                            raise

                    if _auto_answer and not answered and not getattr(query, "_musicbot_callback_answered", False):
                        trace_callback_event(
                            "callback.answer.auto",
                            query,
                            handler=_handler_name,
                            module=_module_name,
                            group=_group,
                            result="sent",
                        )
                        await _answer_once(_source="auto")
                    if _mark_route:
                        trace_done(
                            query,
                            handler=_handler_name,
                            module=_module_name,
                            result=done_result,
                        )
                    if _stop_after_route:
                        raise pyrogram.StopPropagation
                    return result
                finally:
                    restore_panel_edits()
                    reset_button_style_scope(style_scope_token)
                    if patch_answer:
                        try:
                            query.answer = original_answer
                        except Exception:
                            pass

            handler.callback = _safe_cb


def _wrap_message_handlers_with_button_style_scope(bot) -> None:
    """Bind global/Owner style scope around every message handler."""

    import functools

    import pyrogram

    from app.utils.button_style import (
        bind_button_style_scope_from_update,
        reset_button_style_scope,
    )

    for group_handlers in bot.dispatcher.groups.values():
        for handler in group_handlers:
            if not isinstance(
                handler,
                pyrogram.handlers.message_handler.MessageHandler,
            ):
                continue
            original_cb = handler.callback
            if getattr(original_cb, "_musicbot_button_style_scope_wrapper", False):
                continue

            @functools.wraps(original_cb)
            async def _style_scoped_message(
                client,
                message,
                *args,
                _orig=original_cb,
                **kwargs,
            ):
                token = await bind_button_style_scope_from_update(message)
                try:
                    return await _orig(client, message, *args, **kwargs)
                finally:
                    reset_button_style_scope(token)

            _style_scoped_message._musicbot_button_style_scope_wrapper = True
            handler.callback = _style_scoped_message


def register_all(bot, call_py) -> None:
    """Register every handler module with the bot and call_py instances."""
    from app.handlers.help_diag import log_register_all_help_modules
    from app.utils.button_style import install_inline_keyboard_style_hook

    install_inline_keyboard_style_hook()
    log_register_all_help_modules(_MODULES)
    for module in _MODULES:
        module.register(bot, call_py)


async def apply_callback_safety_wrapper(bot) -> None:
    """Wrap every CallbackQueryHandler to auto-answer + catch MessageNotModified.

    Must be called AFTER bot.start() so dispatcher.groups is populated.
    """
    import asyncio

    await asyncio.sleep(0.5)
    _wrap_callback_handlers_with_auto_answer(bot)
    _wrap_message_handlers_with_button_style_scope(bot)
