from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from app.handlers.priority import (
    BOT_DISABLED_CALLBACK_GROUP,
    CALLBACK_TRACE_GROUP,
    FALLBACK_CALLBACK_GROUP,
    LANG_BIND_GROUP,
    PRIVATE_COMMAND_GROUP,
)
from app.services import start_customization_runtime as start_runtime
from app.services import start_customization_service as start_custom
from app.services.bot_settings_service import get_about_text
from app.services.bot_settings_service import resolve_single_active_owner_user_id
from app.services.playback_callback_dispatcher import (
    PlaybackCallbackAction,
    dispatch_playback_callback,
    expected_callbacks_match,
    parse_playback_callback,
    playback_controls_regex,
)
from app.services.language_service import bind_lang_from_update
from app.services.panel_message_service import deliver_panel_outcome, panel_callback_edit
from app.services.panel_router import (
    build_private_root_payload,
    build_private_start_home_payload,
)
from app.services.wizard_ui import (
    TOKEN_ROLE_ROOT,
    cancel_and_resolve,
    clear_runtime_state,
    resolve_navigation_payload,
)
from app.utils.ask_result import (
    safe_stop_chat_callback_listeners,
    safe_stop_listening,
    safe_stop_message_callback_listeners,
)
from app.utils.bot_guards import is_bot_enabled, is_developer
from app.utils.callback_trace import (
    safe_answer_callback,
    trace_callback_event,
    trace_edit,
    trace_received,
    trace_unhandled,
)
from app.utils.filters import group_chat_filter, private_chat_filter
from app.utils.i18n import AUTO_LANG, t
from app.utils.playback_auth import authorize_playback_action
from app.utils.ask_result import safe_delete_user_input
from app.utils.telegram_message import safe_edit_message
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_grp = group_chat_filter()
_pm = private_chat_filter()

_PB_VOLUME_MIN = 1
_PB_VOLUME_MAX = 200
_PB_VOLUME_STEP = 20
_PB_SPEED_MIN = 50
_PB_SPEED_MAX = 200
_PB_SPEED_STEP = 25
_pb_volume_state: dict[int, int] = {}
_pb_speed_state: dict[int, int] = {}
_CALLBACK_EDIT_FALLBACK_SENT: set[tuple[int | None, int | None, str]] = set()
_CALLBACK_EDIT_FALLBACK_LIMIT = 512


def _parse_prefixed_nonnegative_int(
    data: str | None,
    prefix: str,
    *,
    min_value: int = 0,
) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    if not raw or ":" in raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value < min_value:
        return None
    return value


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _current_volume(chat_id: int) -> int:
    return _pb_volume_state.get(chat_id, 100)


def _current_speed(chat_id: int) -> int:
    return _pb_speed_state.get(chat_id, 100)


def _playback_snapshot(chat_id: int) -> dict | None:
    from app.services import CallService

    return CallService.get_active_calls().get(chat_id)


def _is_playback_active(chat_id: int) -> bool:
    from app.services import CallService

    return CallService.is_chat_playing(chat_id)


def _callback_fallback_key(query: CallbackQuery) -> tuple[int | None, int | None, str]:
    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None) if message is not None else None
    message_id = (
        getattr(message, "id", None)
        or getattr(message, "message_id", None)
        if message is not None
        else None
    )
    return (getattr(chat, "id", None), message_id, str(getattr(query, "data", "") or ""))


def _remember_callback_fallback(key: tuple[int | None, int | None, str]) -> None:
    if len(_CALLBACK_EDIT_FALLBACK_SENT) >= _CALLBACK_EDIT_FALLBACK_LIMIT:
        _CALLBACK_EDIT_FALLBACK_SENT.clear()
    _CALLBACK_EDIT_FALLBACK_SENT.add(key)


async def _safe_edit_callback_message(
    client: Client,
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
) -> str:
    message = getattr(query, "message", None)
    if message is not None and await safe_edit_message(message, text, reply_markup=reply_markup):
        return "edited"

    key = _callback_fallback_key(query)
    if key in _CALLBACK_EDIT_FALLBACK_SENT:
        return "fallback_skipped"

    if message is not None:
        reply = getattr(message, "reply", None)
        if callable(reply):
            try:
                await reply(text, reply_markup=reply_markup)
                _remember_callback_fallback(key)
                return "fallback_sent"
            except Exception:
                logger.debug("callback fallback reply failed", exc_info=True)

    chat_id = key[0]
    send_message = getattr(client, "send_message", None) if client is not None else None
    if callable(send_message) and chat_id is not None:
        try:
            await send_message(chat_id, text, reply_markup=reply_markup)
            _remember_callback_fallback(key)
            return "fallback_sent"
        except Exception:
            logger.debug("callback fallback send failed", exc_info=True)

    return "failed"


def _display_media_type_from_active(active: dict | None, fallback: str = "audio") -> str:
    if not active:
        return fallback
    feature = str(active.get("playback_feature") or "").strip().lower()
    if feature in {"radio", "tv", "satellite"}:
        return feature
    media_type = str(active.get("media_type") or fallback).strip().lower()
    if media_type in {"audio", "video", "radio", "tv", "satellite"}:
        return media_type
    return fallback


async def _refresh_now_playing_callback_message(
    query: CallbackQuery,
    *,
    fallback_title: str | None = None,
    fallback_media_type: str = "audio",
    fallback_duration: int | None = None,
) -> bool:
    message = getattr(query, "message", None)
    chat = getattr(message, "chat", None) if message is not None else None
    chat_id = getattr(chat, "id", None)
    if message is None or chat_id is None:
        return False

    active = _playback_snapshot(chat_id) or {}
    media_type = _display_media_type_from_active(active, fallback=fallback_media_type)
    title = active.get("title") or fallback_title
    duration = active.get("duration_seconds") or fallback_duration
    requester_id = active.get("requester_id")
    user_id = query.from_user.id if query.from_user else requester_id

    from app.services.language_service import resolve_lang
    from app.services.media_capability_service import build_now_playing_controls
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    lang = await resolve_lang(chat_id=chat_id, user_id=user_id)
    text = await render_now_playing_text(
        chat_id,
        NowPlayingContext(
            title=title,
            media_type=media_type,
            duration=duration,
            requester_id=requester_id,
        ),
        lang=lang,
        user_id=user_id,
    )
    ok = await safe_edit_message(
        message,
        text,
        reply_markup=await build_now_playing_controls(lang, chat_id),
    )
    if not ok:
        logger.debug(
            "playback.display.edit_fallback chat_id=%s message_id=%s action=refresh",
            chat_id,
            getattr(message, "id", None) or getattr(message, "message_id", None),
        )
    return ok


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    if getattr(bot, "_core_callbacks_registered", False):
        return
    bot._core_callbacks_registered = True

    # Bind runtime language for every update before other handlers execute.
    @bot.on_callback_query(group=LANG_BIND_GROUP)
    async def _bind_lang_callback(client: Client, query: CallbackQuery):
        await bind_lang_from_update(query)

    @bot.on_callback_query(group=CALLBACK_TRACE_GROUP)
    async def _trace_callback_received(client: Client, query: CallbackQuery):  # noqa: ARG001
        from app.handlers.help_diag import log_callback_diag_received

        trace_received(query)
        log_callback_diag_received(query, stage="pre_dispatch")
        # Drop stale pyromod ask/listen waiters before group -980 can swallow the tap.
        message = query.message
        user = query.from_user
        if message is not None and message.chat is not None and user is not None:
            chat_id = message.chat.id
            message_id = getattr(message, "id", None) or getattr(
                message, "message_id", None
            )
            cleared = await safe_stop_listening(
                client,
                chat_id,
                user_id=user.id,
            )
            cleared = (
                await safe_stop_chat_callback_listeners(client, chat_id)
            ) or cleared
            if message_id is not None:
                cleared = (
                    await safe_stop_message_callback_listeners(
                        client,
                        chat_id,
                        message_id,
                    )
                ) or cleared
            if cleared:
                logger.info(
                    "callback_diag.listener_clear chat_id=%s user_id=%s message_id=%s data=%s",
                    chat_id,
                    user.id,
                    message_id,
                    query.data,
                )

    @bot.on_message(group=LANG_BIND_GROUP)
    async def _bind_lang_message(client: Client, message: Message):
        await bind_lang_from_update(message)

    @bot.on_callback_query(group=BOT_DISABLED_CALLBACK_GROUP)
    async def _bot_operational_callback_guard(client: Client, query: CallbackQuery):
        user = query.from_user
        if user is None or is_developer(user.id):
            return
        if await is_bot_enabled():
            return
        data = query.data or ""
        if data.startswith("dev:"):
            return
        try:
            await query.answer(t(_LANG, "status.bot_disabled"), show_alert=True)
        except Exception:
            pass
        if hasattr(query, "stop_propagation"):
            query.stop_propagation()

    async def _answer_navigation_result(query: CallbackQuery, ok: bool) -> None:
        if ok:
            await query.answer()
            return
        await query.answer(t(_LANG, "common.errors.navigation_failed"), show_alert=True)

    # ── Navigation: Back ──────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['NAV_BACK']}$"))
    async def nav_back(client: Client, query: CallbackQuery):
        """Context-aware Back: Group→group panel, PM→role-based panel."""
        trace_callback_event(
            "nav_back.received", query, handler="nav_back", route_type="global"
        )
        try:
            chat = query.message.chat
            user_id = query.from_user.id
            chat_type = (
                getattr(getattr(chat, "type", None), "value", None) if chat else None
            )
            trace_callback_event(
                "nav_back.context",
                query,
                handler="nav_back",
                route_type="global",
                chat_type=chat_type,
            )

            # GROUP context: always go to Group Panel
            if chat and chat.type.value in ("group", "supergroup"):
                from app.utils.player_permissions import can_open_group_panel
                if not await can_open_group_panel(client, chat.id, user_id):
                    await query.answer(t(_LANG, "call_security.no_permission"), show_alert=True)
                    return
                trace_callback_event(
                    "nav_back.route_selected",
                    query,
                    handler="nav_back",
                    route_type="group_root",
                    result="selected",
                )
                trace_callback_event(
                    "nav_back.edit_attempt",
                    query,
                    handler="nav_back",
                    route_type="group_root",
                )
                ok = await panel_callback_edit(
                    client,
                    query,
                    t(_LANG, "panels.group.title"),
                    KeyboardFactory.group_panel(_LANG),
                    answer=False,
                )
                trace_edit(
                    query, result="edited" if ok else "failed", handler="nav_back"
                )
                trace_callback_event(
                    "nav_back.edit_success" if ok else "nav_back.edit_fallback",
                    query,
                    level="info" if ok else "warning",
                    handler="nav_back",
                    route_type="group_root",
                    result="success" if ok else "failed",
                )
                await _answer_navigation_result(query, ok)
                trace_callback_event(
                    "nav_back.answer_sent",
                    query,
                    handler="nav_back",
                    route_type="group_root",
                    result="success" if ok else "failed",
                )
                return

            # PM context: role-based navigation (single shared source of truth)
            trace_callback_event(
                "nav_back.route_selected",
                query,
                handler="nav_back",
                route_type="private_root",
                result="selected",
            )
            text, kb = await build_private_root_payload(
                client,
                user_id,
                query.from_user.first_name or str(user_id),
                lang=_LANG,
                include_welcome=False,
            )
            trace_callback_event(
                "nav_back.navigation_payload_resolved",
                query,
                handler="nav_back",
                route_type="private_root",
                has_reply_markup=kb is not None,
            )
            trace_callback_event(
                "nav_back.edit_attempt",
                query,
                handler="nav_back",
                route_type="private_root",
            )
            ok = await panel_callback_edit(client, query, text, kb, answer=False)
            trace_edit(query, result="edited" if ok else "failed", handler="nav_back")
            trace_callback_event(
                "nav_back.edit_success" if ok else "nav_back.edit_fallback",
                query,
                level="info" if ok else "warning",
                handler="nav_back",
                route_type="private_root",
                result="success" if ok else "failed",
            )
            await _answer_navigation_result(query, ok)
            trace_callback_event(
                "nav_back.answer_sent",
                query,
                handler="nav_back",
                route_type="private_root",
                result="success" if ok else "failed",
            )
        except Exception as exc:
            trace_callback_event(
                "nav_back.exception",
                query,
                level="error",
                handler="nav_back",
                route_type="global",
                result="failed",
                error=exc,
            )
            raise

    @bot.on_callback_query(filters.regex(f"^{CB['NOOP']}$"))
    async def noop_callback(client: Client, query: CallbackQuery):
        # Page-indicator and placeholder callbacks are intentionally no-op.
        await query.answer()

    @bot.on_callback_query(filters.regex(r"^wz:cancel:"))
    async def wz_cancel(client: Client, query: CallbackQuery):
        await query.answer()
        token = query.data[len(CB["WZ_CANCEL_PREFIX"]) :]
        text, kb = await cancel_and_resolve(
            client,
            query.from_user.id,
            query.message.chat.id,
            query.message.chat.type.value,
            lang=_LANG,
            return_to=token or None,
        )
        ok = await panel_callback_edit(
            client,
            query,
            text,
            kb,
            answer=False,
        )
        await _answer_navigation_result(query, ok)

    @bot.on_callback_query(filters.regex(r"^wz:back:"))
    async def wz_back(client: Client, query: CallbackQuery):
        token = query.data[len(CB["WZ_BACK_PREFIX"]) :]
        try:
            await clear_runtime_state(
                client,
                query.from_user.id,
                query.message.chat.id,
            )
        except Exception:
            logger.warning(
                "wz_back clear_runtime_state failed user_id=%s chat_id=%s token=%s",
                query.from_user.id,
                query.message.chat.id,
                token,
                exc_info=True,
            )
        text, kb = await resolve_navigation_payload(
            client,
            query.from_user.id,
            query.message.chat.type.value,
            token or TOKEN_ROLE_ROOT,
            lang=_LANG,
            chat_id=query.message.chat.id,
        )
        ok = await panel_callback_edit(
            client,
            query,
            text,
            kb,
            answer=False,
        )
        await _answer_navigation_result(query, ok)

    @bot.on_callback_query(filters.regex(f"^{CB['WZ_HOME']}$"))
    async def wz_home(client: Client, query: CallbackQuery):
        try:
            await clear_runtime_state(
                client,
                query.from_user.id,
                query.message.chat.id,
            )
        except Exception:
            logger.warning(
                "wz_home clear_runtime_state failed user_id=%s chat_id=%s",
                query.from_user.id,
                query.message.chat.id,
                exc_info=True,
            )
        text, kb = await resolve_navigation_payload(
            client,
            query.from_user.id,
            query.message.chat.type.value,
            TOKEN_ROLE_ROOT,
            lang=_LANG,
            chat_id=query.message.chat.id,
        )
        ok = await panel_callback_edit(
            client,
            query,
            text,
            kb,
            answer=False,
        )
        await _answer_navigation_result(query, ok)

    @bot.on_message(filters.command("cancel"), group=PRIVATE_COMMAND_GROUP)
    async def cancel_command(client: Client, message: Message):
        if message.from_user is None:
            return
        text, kb = await cancel_and_resolve(
            client,
            message.from_user.id,
            message.chat.id,
            message.chat.type.value,
            lang=_LANG,
        )
        await deliver_panel_outcome(
            client,
            message.chat.id,
            message.from_user.id,
            text,
            kb,
        )
        await safe_delete_user_input(message)
        if hasattr(message, "stop_propagation"):
            message.stop_propagation()

    def _callback_chat_type(query: CallbackQuery) -> str | None:
        """Normalize chat type from enum or plain string payloads."""
        chat = query.message.chat if query.message else None
        if chat is None:
            return None
        raw = getattr(chat, "type", None)
        if raw is None:
            return None
        value = getattr(raw, "value", raw)
        return str(value) if value is not None else None

    async def _return_to_private_start_home(
        client: Client,
        query: CallbackQuery,
        *,
        handler: str,
    ) -> bool:
        """Clear temporary state and redraw the normal private /start home."""
        if query.message is None or query.from_user is None:
            return False
        user_id = query.from_user.id
        chat_id = query.message.chat.id
        try:
            await clear_runtime_state(client, user_id, chat_id)
        except Exception:
            logger.warning(
                "%s clear_runtime_state failed user_id=%s chat_id=%s",
                handler,
                user_id,
                chat_id,
                exc_info=True,
            )
        first_name = getattr(query.from_user, "first_name", None) or str(user_id)
        text, kb = await build_private_start_home_payload(
            client,
            user_id,
            first_name,
            lang=_LANG,
        )
        return await panel_callback_edit(client, query, text, kb, answer=False)

    # ── Navigation: Back to private /start home (panel roots) ─────────────
    @bot.on_callback_query(filters.regex(f"^{CB['NAV_START']}$"))
    async def nav_start(client: Client, query: CallbackQuery):
        """Return from private management panel roots to the normal start home."""
        chat_type = _callback_chat_type(query)
        if chat_type in ("group", "supergroup", "channel"):
            # Management panel roots are private-only; ignore group/channel presses.
            await query.answer()
            return
        ok = await _return_to_private_start_home(
            client, query, handler="nav_start"
        )
        await _answer_navigation_result(query, ok)

    # ── Navigation: Close (legacy private → start home; group → delete) ───
    @bot.on_callback_query(filters.regex(f"^{CB['NAV_CLOSE']}$"))
    async def nav_close(client: Client, query: CallbackQuery):
        """Compatibility: private stale Close returns to /start home safely.

        Group/channel Close keeps the historical delete-panel behavior used by
        group panel, post-install, and similar surfaces.
        """
        chat_type = _callback_chat_type(query)
        if chat_type == "private" or (
            chat_type is None
            and query.message is not None
            and getattr(getattr(query.message, "chat", None), "id", 0) > 0
        ):
            # Private (or private-like positive chat id when type is missing):
            # never delete the home-bound management panel on stale Close.
            ok = await _return_to_private_start_home(
                client, query, handler="nav_close"
            )
            await _answer_navigation_result(query, ok)
            return
        if query.message is None:
            await query.answer()
            return
        try:
            await query.message.delete()
            await query.answer()
        except Exception:
            await query.answer(t(_LANG, "common.buttons.close"), show_alert=False)

    # ── Playback type menu ────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['PB_AUDIO']}$"))
    async def pb_audio(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "playback.type_hints.audio"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['PB_VIDEO']}$"))
    async def pb_video(client: Client, query: CallbackQuery):
        from app.services.media_capability_service import deny_video_playback

        if await deny_video_playback(query, lang=_LANG):
            return
        await query.answer(t(_LANG, "playback.type_hints.video"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['PB_TV']}$"))
    async def pb_tv(client: Client, query: CallbackQuery):
        from app.handlers.tv_radio import _TV_CHANNELS_PATH, _load_json
        from app.services.media_capability_service import deny_free_mode_media

        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        if await deny_free_mode_media(query, "tv", lang=_LANG):
            return

        channels = _load_json(_TV_CHANNELS_PATH)
        if not channels:
            await query.answer(t(_LANG, "tv_radio.no_channels"), show_alert=True)
            return
        await query.answer()
        await _safe_edit_callback_message(
            client,
            query,
            t(_LANG, "tv_radio.choose_channel"),
            reply_markup=KeyboardFactory.tv_channels_menu(_LANG, channels),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['PB_DOWNLOAD']}$"))
    async def pb_download(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "playback.type_hints.download"), show_alert=True)

    # ── Playback controls (group context only; parsed + dispatched) ──────
    if not expected_callbacks_match(CB):
        logger.warning("playback callback constants do not match dispatcher expectations")

    async def _pb_action_stop(query: CallbackQuery) -> None:
        from app.services import CallService

        chat_id = query.message.chat.id
        ok = await CallService.leave_voice_chat(call_py, chat_id)
        if ok:
            _pb_volume_state.pop(chat_id, None)
            _pb_speed_state.pop(chat_id, None)
        await query.answer(
            t(_LANG, "playback_cmd.stopped_audio" if ok else "playback_cmd.no_voice_chat"),
            show_alert=not ok,
        )
        try:
            await query.message.delete()
        except Exception:
            pass

    async def _pb_action_pause(query: CallbackQuery) -> None:
        from app.services import CallService

        chat_id = query.message.chat.id
        if not _is_playback_active(chat_id):
            await query.answer(t(_LANG, "playback_cmd.no_voice_chat"), show_alert=True)
            return
        snapshot = _playback_snapshot(chat_id)
        if snapshot is not None and bool(snapshot.get("is_paused")):
            await query.answer(t(_LANG, "playback_cmd.paused"), show_alert=False)
            return
        ok = await CallService.pause(call_py, chat_id)
        await query.answer(
            t(_LANG, "playback_cmd.paused" if ok else "playback_cmd.no_voice_chat"),
            show_alert=not ok,
        )

    async def _pb_action_resume(query: CallbackQuery) -> None:
        from app.services import CallService

        chat_id = query.message.chat.id
        snapshot = _playback_snapshot(chat_id)
        if snapshot is None:
            await query.answer(t(_LANG, "playback_cmd.no_voice_chat"), show_alert=True)
            return
        if not bool(snapshot.get("is_paused")):
            await query.answer(t(_LANG, "playback_cmd.resumed"), show_alert=False)
            return
        ok = await CallService.resume(call_py, chat_id)
        await query.answer(
            t(_LANG, "playback_cmd.resumed" if ok else "playback_cmd.no_voice_chat"),
            show_alert=not ok,
        )

    async def _pb_action_volume(query: CallbackQuery, delta: int) -> None:
        from app.services import CallService

        chat_id = query.message.chat.id
        if not _is_playback_active(chat_id):
            await query.answer(t(_LANG, "playback_cmd.no_voice_chat"), show_alert=True)
            return
        current = _current_volume(chat_id)
        target = _clamp(current + delta, _PB_VOLUME_MIN, _PB_VOLUME_MAX)
        if target == current:
            key = "playback.controls.vol_up" if delta > 0 else "playback.controls.vol_down"
            await query.answer(t(_LANG, key), show_alert=False)
            return
        ok = await CallService.set_volume(call_py, chat_id, target)
        if ok:
            _pb_volume_state[chat_id] = target
            await query.answer(
                t(_LANG, "playback_cmd.volume_set", volume=target), show_alert=False
            )
        else:
            await query.answer(t(_LANG, "playback_cmd.failed"), show_alert=True)

    async def _pb_action_speed(query: CallbackQuery, delta: int) -> None:
        from app.services import CallService

        chat_id = query.message.chat.id
        result = await CallService.change_playback_speed(call_py, chat_id, delta)
        await query.answer(
            t(_LANG, result.message_key, speed=result.speed_label),
            show_alert=result.show_alert,
        )

    async def _pb_action_next(query: CallbackQuery) -> None:
        from app.services import CallService

        chat_id = query.message.chat.id
        ok = await CallService.play_next(call_py, chat_id)
        await query.answer(
            t(_LANG, "playback.queue.skipped" if ok else "playback.queue.empty"),
            show_alert=not ok,
        )
        if ok:
            await _refresh_now_playing_callback_message(query)

    async def _pb_action_prev(query: CallbackQuery) -> None:
        from app.services import CallService

        chat_id = query.message.chat.id
        ok = await CallService.play_previous(call_py, chat_id)
        await query.answer(
            t(_LANG, "playback.queue.skipped" if ok else "playback.controls.previous_unavailable"),
            show_alert=not ok,
        )
        if ok:
            await _refresh_now_playing_callback_message(query)

    async def _pb_action_repeat(query: CallbackQuery) -> None:
        from app.services import CallService
        from app.services.media_capability_service import build_now_playing_controls

        chat_id = query.message.chat.id
        current = CallService.get_repeat_state(chat_id)
        new_state = not current
        CallService.set_repeat_state(chat_id, new_state)
        await query.answer(
            t(_LANG, "playback.repeat_enabled" if new_state else "playback.repeat_disabled"),
            show_alert=False,
        )
        try:
            await query.message.edit_reply_markup(
                reply_markup=await build_now_playing_controls(_LANG, chat_id),
            )
        except Exception:
            pass

    async def _pb_action_fav_add(query: CallbackQuery) -> None:
        from sqlalchemy import select

        from app.database.engine import async_session
        from app.database.models import Favorite
        from app.services import CallService

        user_id = query.from_user.id
        chat_id = query.message.chat.id
        stream_url = None
        active = CallService.get_active_calls().get(chat_id)
        title = None
        media_type = "audio"
        duration_seconds = 0
        if active:
            title = active.get("title")
            media_type = str(active.get("media_type") or "audio")
            duration_seconds = int(active.get("duration_seconds") or 0)
            source = str(active.get("source") or "").strip()
            if source.startswith(("http://", "https://")):
                stream_url = source
        if not title:
            msg_text = (
                getattr(query.message, "text", None)
                or getattr(query.message, "caption", None)
                or ""
            ).strip()
            title = (
                msg_text.split("\n")[0][:200]
                if msg_text
                else t(_LANG, "common.labels.unknown")
            )

        async with async_session() as session:
            async with session.begin():
                existing = await session.execute(
                    select(Favorite).where(
                        Favorite.user_id == user_id,
                        Favorite.chat_id == chat_id,
                        Favorite.title == title,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    await query.answer(t(_LANG, "favorites.added"), show_alert=False)
                    return
                fav = Favorite(
                    user_id=user_id,
                    chat_id=chat_id,
                    title=title,
                    stream_url=stream_url,
                    duration_seconds=duration_seconds,
                    media_type=media_type,
                )
                session.add(fav)
        await query.answer(t(_LANG, "favorites.added"), show_alert=False)

    async def _pb_action_fav_play(query: CallbackQuery) -> None:
        from sqlalchemy import select

        from app.database.engine import async_session
        from app.database.models import Favorite
        from app.repositories import playlist_repo
        from app.services import CallService

        user_id = query.from_user.id
        chat_id = query.message.chat.id
        async with async_session() as session:
            stmt = (
                select(Favorite)
                .where(Favorite.user_id == user_id)
                .order_by(Favorite.added_at.desc())
            )
            rows = list((await session.execute(stmt)).scalars().all())

        playable = [row for row in rows if row.stream_url]
        if not playable:
            await query.answer(t(_LANG, "favorites.no_track"), show_alert=True)
            return

        first = playable[0]
        for queued in playable[1:]:
            queued_media_type = queued.media_type or "audio"
            queued_stream_media_type = (
                "video" if queued_media_type in {"video", "tv", "satellite"} else "audio"
            )
            await playlist_repo.add_to_queue(
                chat_id=chat_id,
                stream_url=queued.stream_url,
                file_path=None,
                title=queued.title,
                duration=int(queued.duration_seconds or 0),
                media_type=queued_stream_media_type,
                added_by=user_id,
            )

        favorite_media_type = first.media_type or "audio"
        stream_media_type = (
            "video" if favorite_media_type in {"video", "tv", "satellite"} else "audio"
        )
        playback_feature = (
            favorite_media_type
            if favorite_media_type in {"radio", "tv", "satellite"}
            else None
        )
        ok = await CallService.join_voice_chat(
            call_py,
            chat_id,
            first.stream_url,
            stream_media_type,
            user_id=user_id,
            title=first.title,
            event_source=first.stream_url,
            source_kind_hint="favorite",
            playback_feature=playback_feature,
        )
        if ok:
            await query.answer(t(_LANG, "favorites.playing"), show_alert=False)
            await _refresh_now_playing_callback_message(
                query,
                fallback_title=first.title,
                fallback_media_type=favorite_media_type,
                fallback_duration=int(first.duration_seconds or 0),
            )
            return
        key = CallService.pop_join_failure_key() or "playback_cmd.failed"
        await query.answer(t(_LANG, key), show_alert=True)

    _PLAYBACK_ACTION_HANDLERS = {
        PlaybackCallbackAction.STOP: _pb_action_stop,
        PlaybackCallbackAction.PAUSE: _pb_action_pause,
        PlaybackCallbackAction.RESUME: _pb_action_resume,
        PlaybackCallbackAction.VOL_DOWN: lambda q: _pb_action_volume(q, -_PB_VOLUME_STEP),
        PlaybackCallbackAction.VOL_UP: lambda q: _pb_action_volume(q, _PB_VOLUME_STEP),
        PlaybackCallbackAction.SPEED_DOWN: lambda q: _pb_action_speed(q, -_PB_SPEED_STEP),
        PlaybackCallbackAction.SPEED_UP: lambda q: _pb_action_speed(q, _PB_SPEED_STEP),
        PlaybackCallbackAction.PREV: _pb_action_prev,
        PlaybackCallbackAction.NEXT: _pb_action_next,
        PlaybackCallbackAction.REPEAT: _pb_action_repeat,
        PlaybackCallbackAction.FAV_ADD: _pb_action_fav_add,
        PlaybackCallbackAction.FAV_PLAY: _pb_action_fav_play,
    }

    async def _dispatch_playback_callback_query(client: Client, query: CallbackQuery):
        parsed = parse_playback_callback(query.data)
        if parsed is None:
            return
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        handled = await dispatch_playback_callback(
            parsed,
            _PLAYBACK_ACTION_HANDLERS,
            query,
        )
        if not handled:
            await query.answer(t(_LANG, "common.errors.unknown_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(playback_controls_regex()) & _grp)
    async def pb_playback_controls(client: Client, query: CallbackQuery):
        await _dispatch_playback_callback_query(client, query)

    # ── Start menu callbacks (PM only) ──────────────────────────────────
    @bot.on_callback_query(
        filters.regex(r"^start:cat:(ability|test|use|history|note)$") & _pm
    )
    async def start_category_content(client: Client, query: CallbackQuery):
        category = str(query.data or "").rsplit(":", 1)[-1]
        if category == start_custom.CATEGORY_TEST and not await start_custom.is_test_category_exposed(
            chat_id=None
        ):
            await query.answer(
                t(_LANG, "start.customization.test_unavailable"),
                show_alert=True,
            )
            return

        message = getattr(query, "message", None)
        chat = getattr(message, "chat", None) if message is not None else None
        chat_id = getattr(chat, "id", None)
        if chat_id is None:
            await query.answer(
                t(_LANG, "common.errors.navigation_failed"),
                show_alert=True,
            )
            return

        await query.answer()
        owner_user_id = await resolve_single_active_owner_user_id()
        item = await start_custom.get_random_message_item(
            category=category,
            owner_user_id=owner_user_id,
            chat_id=None,
        )
        reply_markup = KeyboardFactory.back_button(_LANG)
        if item is None:
            await _safe_edit_callback_message(
                client,
                query,
                t(_LANG, "start.customization.no_category_content"),
                reply_markup=reply_markup,
            )
            return
        await start_runtime.deliver_message_item(
            client,
            chat_id=chat_id,
            anchor_message=message,
            item=item,
            fallback_text=t(_LANG, "start.customization.source_unavailable"),
            reply_markup=reply_markup,
        )

    @bot.on_callback_query(filters.regex(r"^start:cat:") & _pm)
    async def start_category_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['START_PRICING']}$") & _pm)
    async def start_pricing(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(
            t(_LANG, "credit.pricing_removed"),
            show_alert=True,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['START_ABOUT']}$") & _pm)
    async def start_about(client: Client, query: CallbackQuery):
        await query.answer()
        text = await get_about_text(_LANG)
        await query.message.edit_text(
            text, reply_markup=KeyboardFactory.back_button(_LANG)
        )

    # ── Favorites list (paginated with remove buttons) ──────────────
    @bot.on_callback_query(filters.regex(f"^{CB['PB_FAV_LIST']}$"))
    async def pb_fav_list(client: Client, query: CallbackQuery):
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        await query.answer()
        await _render_fav_page(client, query, query.from_user.id, 0)

    @bot.on_callback_query(filters.regex(r"^pg:fav:\d+$"))
    async def pb_fav_page(client: Client, query: CallbackQuery):
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        page = _parse_prefixed_nonnegative_int(query.data, CB["PAGE_FAV"])
        if page is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer()
        await _render_fav_page(client, query, query.from_user.id, page)

    @bot.on_callback_query(filters.regex(r"^fav:rm:\d+$"))
    async def pb_fav_rm(client: Client, query: CallbackQuery):
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        fav_id = _parse_prefixed_nonnegative_int(
            query.data, CB["FAV_RM_PREFIX"], min_value=1
        )
        if fav_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        from sqlalchemy import delete

        from app.database.engine import async_session
        from app.database.models import Favorite

        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    delete(Favorite).where(
                        Favorite.id == fav_id,
                        Favorite.user_id == query.from_user.id,
                    )
                )
        await query.answer(t(_LANG, "favorites.removed_toast"), show_alert=False)
        await _render_fav_page(client, query, query.from_user.id, 0)

    @bot.on_callback_query(filters.regex(r"^fav:info:\d+$"))
    async def pb_fav_info(client: Client, query: CallbackQuery):
        if not await authorize_playback_action(client, query, lang=_LANG):
            return
        fav_id = _parse_prefixed_nonnegative_int(
            query.data, CB["FAV_INFO_PREFIX"], min_value=1
        )
        if fav_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer(t(_LANG, "favorites.item_unavailable"), show_alert=True)

    async def _render_fav_page(client: Client, query, user_id: int, page: int):
        from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        from app.repositories import favorite_repo

        chunk, total_pages, page = await favorite_repo.get_favorites_page(user_id, page)
        if not chunk:
            await _safe_edit_callback_message(
                client,
                query,
                t(_LANG, "favorites.list_header")
                + "\n\n"
                + t(_LANG, "favorites.no_track"),
                reply_markup=KeyboardFactory.back_button(_LANG),
            )
            return

        rows = []
        for f in chunk:
            label = t(_LANG, "favorites.list_item", title=f.title or "-", duration="")
            rm_label = t(_LANG, "favorites.remove_btn")
            rows.append(
                [
                    InlineKeyboardButton(
                        label, callback_data=f"{CB['FAV_INFO_PREFIX']}{f.id}"
                    ),
                    InlineKeyboardButton(
                        rm_label, callback_data=f"{CB['FAV_RM_PREFIX']}{f.id}"
                    ),
                ]
            )

        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.prev"),
                    callback_data=f"{CB['PAGE_FAV']}{page - 1}",
                )
            )
        if total_pages > 1:
            nav_row.append(
                InlineKeyboardButton(
                    f"{page + 1}/{total_pages}", callback_data=CB["NOOP"]
                )
            )
        if page < total_pages - 1:
            nav_row.append(
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.next"),
                    callback_data=f"{CB['PAGE_FAV']}{page + 1}",
                )
            )
        if nav_row:
            rows.append(nav_row)
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.back"), callback_data=CB["NAV_BACK"]
                )
            ]
        )

        await _safe_edit_callback_message(
            client,
            query,
            t(_LANG, "favorites.list_header"),
            reply_markup=InlineKeyboardMarkup(rows),
        )

    # ── Post-install panel callbacks ──────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['POST_INSTALL_PANEL']}$"))
    async def postinst_panel(client: Client, query: CallbackQuery):
        from app.utils.player_permissions import can_open_group_panel
        chat = query.message.chat
        if chat and chat.type.value in ("group", "supergroup"):
            if not await can_open_group_panel(client, chat.id, query.from_user.id):
                await query.answer(t(_LANG, "call_security.no_permission"), show_alert=True)
                return
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "panels.group.title"),
            reply_markup=KeyboardFactory.group_panel(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['POST_INSTALL_HELP']}$"))
    async def postinst_help(client: Client, query: CallbackQuery):
        await query.answer()
        text = t(_LANG, "help_content.general_commands")
        await query.message.edit_text(
            text, reply_markup=KeyboardFactory.back_button(_LANG)
        )

    @bot.on_callback_query(filters.regex(f"^{CB['POST_INSTALL_INC_CREDIT']}$"))
    async def postinst_inc_credit(client: Client, query: CallbackQuery):
        await query.answer(
            t(_LANG, "install.increase_credit_hint"),
            show_alert=True,
        )

    @bot.on_callback_query(filters.regex(f"^{CB['POST_INSTALL_DEC_CREDIT']}$"))
    async def postinst_dec_credit(client: Client, query: CallbackQuery):
        await query.answer(
            t(_LANG, "install.decrease_credit_hint"),
            show_alert=True,
        )

    _KNOWN_CB_PREFIX = re.compile(
        r"^(?:noop|Add:|dev:|own:|grp:|h:|hlp:|nav:|pb:|wz:|bcw:|bc:|fj:|fm:|cs:|inst:|"
        r"post:|postinst:|start:|adm:|sudo:|ownr:|an:|anl:|promo:|fav:|search:|srch:|"
        r"dl:|tv:|rad:|pg:|fct:|yts:|vipd:|eq:|pl:|up:)",
        re.IGNORECASE,
    )

    @bot.on_callback_query(
        ~filters.regex(_KNOWN_CB_PREFIX), group=FALLBACK_CALLBACK_GROUP
    )
    async def unknown_callback_fallback(client: Client, query: CallbackQuery):  # noqa: ARG001
        """Answer unmatched callback data so taps never feel silently blocked."""
        trace_unhandled(query, reason="unknown_prefix")
        await safe_answer_callback(
            query,
            t(_LANG, "common.errors.unknown_callback"),
            show_alert=True,
            text_key="common.errors.unknown_callback",
        )

    @bot.on_callback_query(
        filters.regex(_KNOWN_CB_PREFIX), group=FALLBACK_CALLBACK_GROUP
    )
    async def known_prefix_unknown_callback_fallback(
        client: Client, query: CallbackQuery
    ):  # noqa: ARG001
        """Answer known-family callback data when no route handler accepted it."""
        if getattr(query, "_musicbot_callback_route_seen", False):
            return
        from app.handlers.help_diag import log_help_fallback_decision

        await log_help_fallback_decision(client, query, reason="no_registered_or_filter_denied")
        trace_unhandled(query, reason="no_registered_or_filter_denied")
        await safe_answer_callback(
            query,
            t(_LANG, "common.errors.unknown_callback"),
            show_alert=True,
            text_key="common.errors.unknown_callback",
        )
