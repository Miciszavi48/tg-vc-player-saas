from __future__ import annotations

import functools
from typing import Any, Callable

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from app.repositories import admin_repo, user_repo
from app.services.language_service import resolve_lang_from_update
from app.utils.bot_guards import deny_if_bot_disabled, deny_if_sudo_panel_disabled, is_developer
from app.utils.i18n import t
from app.utils.sudo_permissions import can_use_sudo_admin_bypass, notify_sudo_permission_denied


def _get_user_id(update: Message | CallbackQuery) -> int | None:
    if isinstance(update, CallbackQuery):
        return update.from_user.id if update.from_user else None
    if isinstance(update, Message):
        return update.from_user.id if update.from_user else None
    user = getattr(update, "from_user", None)
    if user is not None:
        return getattr(user, "id", None)
    return None


def _get_chat_id(update: Message | CallbackQuery) -> int | None:
    if isinstance(update, CallbackQuery):
        return update.message.chat.id if update.message else None
    if isinstance(update, Message):
        return update.chat.id
    message = getattr(update, "message", None)
    if message is not None:
        chat = getattr(message, "chat", None)
        if chat is not None:
            return getattr(chat, "id", None)
    chat = getattr(update, "chat", None)
    if chat is not None:
        return getattr(chat, "id", None)
    return None


async def _group_player_global_bypass(user_id: int) -> bool:
    """Bot owner and permitted sudo bypass for in-group player decorators."""
    if await user_repo.is_owner(user_id):
        return True
    return await can_use_sudo_admin_bypass(user_id)


async def _deny_access(update: Message | CallbackQuery) -> None:
    lang = await resolve_lang_from_update(update)
    if isinstance(update, CallbackQuery):
        try:
            await update.answer(t(lang, "common.errors.no_access"), show_alert=True)
        except Exception:
            return
        return
    if hasattr(update, "answer") and callable(getattr(update, "answer")):
        try:
            await update.answer(t(lang, "common.errors.no_access"), show_alert=True)
        except Exception:
            return
        return
    if isinstance(update, Message):
        try:
            await update.reply(t(lang, "common.errors.no_access"))
        except Exception:
            return
    if hasattr(update, "reply") and callable(getattr(update, "reply")):
        try:
            await update.reply(t(lang, "common.errors.no_access"))
        except Exception:
            return


async def _remember_authorized_panel(update: Any) -> None:
    """Keep privileged callback navigation anchored to the current bot message."""
    if not isinstance(update, CallbackQuery):
        return
    try:
        from app.services.panel_message_service import remember_panel_from_query

        await remember_panel_from_query(update)
    except Exception:
        # Panel anchoring is UX state and must not block an authorized action.
        return


def developer_only(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        if user_id is None or not is_developer(user_id):
            if isinstance(update, CallbackQuery):
                from app.utils.callback_trace import trace_guard

                trace_guard(
                    update,
                    guard="developer_only",
                    result="deny",
                    reason="not_developer",
                    handler=getattr(func, "__qualname__", getattr(func, "__name__", "callback")),
                )
            await _deny_access(update)
            return None
        if isinstance(update, CallbackQuery):
            from app.utils.callback_trace import trace_guard

            trace_guard(
                update,
                guard="developer_only",
                result="allow",
                handler=getattr(func, "__qualname__", getattr(func, "__name__", "callback")),
            )
            await _remember_authorized_panel(update)
        return await func(client, update, *args, **kwargs)
    return wrapper


def owner_or_above(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        if user_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            await _remember_authorized_panel(update)
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        if await user_repo.is_owner(user_id):
            await _remember_authorized_panel(update)
            return await func(client, update, *args, **kwargs)
        await _deny_access(update)
        return None
    return wrapper


def sudo_or_above(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        if user_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            await _remember_authorized_panel(update)
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        if await user_repo.is_owner(user_id):
            await _remember_authorized_panel(update)
            return await func(client, update, *args, **kwargs)
        if await user_repo.is_sudo(user_id):
            if await deny_if_sudo_panel_disabled(update, user_id):
                return None
            await _remember_authorized_panel(update)
            return await func(client, update, *args, **kwargs)
        await _deny_access(update)
        return None
    return wrapper


def group_music_admin(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        chat_id = _get_chat_id(update)
        if user_id is None or chat_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        if await _group_player_global_bypass(user_id):
            return await func(client, update, *args, **kwargs)
        from app.utils.player_permissions import can_open_group_settings

        if await can_open_group_settings(client, chat_id, user_id):
            return await func(client, update, *args, **kwargs)
        if await user_repo.is_sudo(user_id):
            await notify_sudo_permission_denied(update, "auto_admin_bypass")
            return None
        await _deny_access(update)
        return None
    return wrapper


def group_video_admin(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        chat_id = _get_chat_id(update)
        if user_id is None or chat_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        if await can_use_sudo_admin_bypass(user_id):
            return await func(client, update, *args, **kwargs)
        if await admin_repo.is_video_admin(user_id, chat_id):
            return await func(client, update, *args, **kwargs)
        if await admin_repo.is_player_deputy_or_above(user_id, chat_id):
            return await func(client, update, *args, **kwargs)
        if await user_repo.is_sudo(user_id):
            await notify_sudo_permission_denied(update, "auto_admin_bypass")
            return None
        await _deny_access(update)
        return None
    return wrapper


def player_owner_or_above(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        chat_id = _get_chat_id(update)
        if user_id is None or chat_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        if await _group_player_global_bypass(user_id):
            return await func(client, update, *args, **kwargs)
        from app.utils.player_permissions import can_manage_deputy

        if await can_manage_deputy(client, chat_id, user_id):
            return await func(client, update, *args, **kwargs)
        if await user_repo.is_sudo(user_id):
            await notify_sudo_permission_denied(update, "auto_admin_bypass")
            return None
        await _deny_access(update)
        return None
    return wrapper


def player_deputy_or_above(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        chat_id = _get_chat_id(update)
        if user_id is None or chat_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        if await _group_player_global_bypass(user_id):
            return await func(client, update, *args, **kwargs)
        from app.utils.player_permissions import can_manage_admin

        if await can_manage_admin(client, chat_id, user_id):
            return await func(client, update, *args, **kwargs)
        if await user_repo.is_sudo(user_id):
            await notify_sudo_permission_denied(update, "auto_admin_bypass")
            return None
        await _deny_access(update)
        return None
    return wrapper


def music_admin_or_above(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        chat_id = _get_chat_id(update)
        if user_id is None or chat_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        if await _group_player_global_bypass(user_id):
            return await func(client, update, *args, **kwargs)
        from app.utils.player_permissions import can_manage_vip

        if await can_manage_vip(client, chat_id, user_id):
            return await func(client, update, *args, **kwargs)
        if await user_repo.is_sudo(user_id):
            await notify_sudo_permission_denied(update, "auto_admin_bypass")
            return None
        await _deny_access(update)
        return None
    return wrapper


def player_owner_management(func: Callable) -> Callable:
    @functools.wraps(func)
    async def wrapper(client: Client, update: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = _get_user_id(update)
        chat_id = _get_chat_id(update)
        if user_id is None or chat_id is None:
            await _deny_access(update)
            return None
        if is_developer(user_id):
            return await func(client, update, *args, **kwargs)
        if await deny_if_bot_disabled(update, user_id):
            return None
        from app.utils.player_permissions import can_manage_player_owner

        if await can_manage_player_owner(client, chat_id, user_id):
            return await func(client, update, *args, **kwargs)
        if await user_repo.is_sudo(user_id):
            await notify_sudo_permission_denied(update, "auto_admin_bypass")
            return None
        await _deny_access(update)
        return None
    return wrapper
