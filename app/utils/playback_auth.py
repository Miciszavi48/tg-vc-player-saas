from __future__ import annotations

import logging
from typing import Any

from pyrogram import Client
from pyrogram.types import CallbackQuery, Message

from app.repositories import settings_repo, user_repo
from app.services import group_runtime_state_service
from app.utils.bot_guards import deny_if_bot_disabled, is_developer
from app.utils.diagnostic_logging import log_guard_decision
from app.utils.i18n import AUTO_LANG, t
from app.utils.playback_commands import log_playback_command
from app.utils.player_permissions import can_play_media
from app.utils.sudo_permissions import notify_sudo_permission_denied

logger = logging.getLogger(__name__)


def _get_user_id(update: Message | CallbackQuery) -> int | None:
    user = getattr(update, "from_user", None)
    return getattr(user, "id", None) if user is not None else None


def _get_chat(update: Message | CallbackQuery) -> Any | None:
    if isinstance(update, CallbackQuery):
        message = getattr(update, "message", None)
        return getattr(message, "chat", None) if message is not None else None
    message = getattr(update, "message", None)
    if message is not None:
        chat = getattr(message, "chat", None)
        if chat is not None:
            return chat
    return getattr(update, "chat", None)


def _log_auth_denial(
    update: Message | CallbackQuery,
    reason: str,
) -> None:
    chat = _get_chat(update)
    user_id = _get_user_id(update)
    log_guard_decision(
        "playback_auth",
        "block",
        reason=reason,
        user_id=user_id,
        chat_id=chat.id if chat is not None else None,
    )
    log_playback_command(
        "playback_command_ignored",
        chat_id=chat.id if chat is not None else None,
        user_id=user_id,
        reason=reason,
        level="debug",
    )


async def _send_playback_denial(
    update: Message | CallbackQuery,
    lang: str,
    key: str,
    *,
    reason: str,
) -> None:
    _log_auth_denial(update, reason)
    text = t(lang, key)
    if isinstance(update, CallbackQuery):
        await update.answer(text, show_alert=True)
        return
    if isinstance(update, Message):
        reply = getattr(update, "reply", None) or getattr(update, "reply_text", None)
        if callable(reply):
            await reply(text)
        return
    answer = getattr(update, "answer", None)
    if callable(answer):
        try:
            await answer(text, show_alert=True)
        except TypeError:
            await answer(text)


async def authorize_playback_action(
    client: Client,
    update: Message | CallbackQuery,
    *,
    lang: str = AUTO_LANG,
) -> bool:
    """Apply the same playback credit/security/role checks to commands and callbacks."""
    chat = _get_chat(update)
    user_id = _get_user_id(update)
    if chat is None or user_id is None:
        await _send_playback_denial(
            update, lang, "playback_cmd.no_permission", reason="missing_chat_or_user",
        )
        return False

    if not is_developer(user_id) and await deny_if_bot_disabled(update, user_id):
        _log_auth_denial(update, "bot_disabled")
        return False

    chat_id = chat.id

    try:
        bot_member = await client.get_chat_member(chat_id, "me")
        raw_status = getattr(bot_member, "status", None)
        status = getattr(raw_status, "value", raw_status)
        if status not in ("administrator", "creator"):
            await _send_playback_denial(
                update, lang, "playback_cmd.not_admin", reason="not_admin",
            )
            return False
    except Exception as exc:
        logger.debug(
            "playback admin preflight skipped chat_id=%s reason=%s",
            chat_id,
            type(exc).__name__,
        )

    chat_type_value = getattr(getattr(chat, "type", None), "value", None) or getattr(chat, "type", None)
    chat_type = "channel" if str(chat_type_value) == "channel" else "group"
    runtime_state = await group_runtime_state_service.get_runtime_credit_state(chat_id, chat_type)
    if not runtime_state.has_runtime_credit:
        await _send_playback_denial(
            update, lang, "playback_cmd.no_credit", reason="no_credit",
        )
        return False

    cs = runtime_state.settings or await settings_repo.get_chat_settings(chat_id, chat_type)

    security_enabled = bool(cs and cs.security_call_enabled)
    if await can_play_media(client, chat_id, user_id, security_enabled=security_enabled):
        return True

    if security_enabled:
        if await user_repo.is_sudo(user_id):
            await notify_sudo_permission_denied(update, "auto_admin_bypass", lang)
            _log_auth_denial(update, "security_call_sudo")
            return False
        await _send_playback_denial(
            update, lang, "playback_cmd.no_permission", reason="security_call",
        )
        return False

    return True
