"""Centralized sudo permission matrix enforcement helpers."""

from __future__ import annotations

from typing import Any

from pyrogram.types import CallbackQuery, Message

from app.repositories import admin_repo, user_repo
from app.repositories.user_repo import SUDO_PERMISSION_FIELD_NAMES
from app.services.language_service import resolve_lang_from_update
from app.utils.bot_guards import is_developer
from app.utils.i18n import AUTO_LANG, t

SUDO_PERMISSION_DENIED_KEYS: dict[str, str] = {
    "can_manage_groups": "sudo_enforce.denied_groups",
    "can_manage_channels": "sudo_enforce.denied_channels",
    "can_manage_credit": "sudo_enforce.denied_credit",
    "can_remove_bot": "sudo_enforce.denied_remove_bot",
    "can_manage_chat_settings": "sudo_enforce.denied_chat_settings",
    "auto_admin_bypass": "sudo_enforce.denied_auto_admin",
}


def permission_denial_key(field: str) -> str:
    """Return i18n key for a permission denial message."""
    if field in SUDO_PERMISSION_DENIED_KEYS:
        return SUDO_PERMISSION_DENIED_KEYS[field]
    return "sudo_enforce.denied_generic"


async def get_effective_sudo_permissions(user_id: int) -> dict[str, bool] | None:
    """Return permission flags for an active sudo user, or None if not sudo."""
    if not await user_repo.is_sudo(user_id):
        return None
    return await user_repo.get_sudo_permissions(user_id)


async def sudo_has_permission(
    user_id: int,
    field: str,
    *,
    developer_bypass: bool = True,
    owner_bypass: bool = True,
) -> bool:
    """Check whether user has the given sudo permission (dev/owner bypass optional)."""
    if field not in SUDO_PERMISSION_FIELD_NAMES:
        return False
    if developer_bypass and is_developer(user_id):
        return True
    if owner_bypass and await user_repo.is_owner(user_id):
        return True
    if not await user_repo.is_sudo(user_id):
        return False
    perms = await user_repo.get_sudo_permissions(user_id)
    if perms is None:
        return False
    return perms.get(field, True)


async def is_pure_sudo(user_id: int) -> bool:
    """Return True when user is an active sudo but not developer or owner."""
    if is_developer(user_id):
        return False
    if await user_repo.is_owner(user_id):
        return False
    return await user_repo.is_sudo(user_id)


async def can_use_sudo_admin_bypass(user_id: int) -> bool:
    """Return True when sudo implicit in-chat admin bypass is allowed."""
    return await sudo_has_permission(user_id, "auto_admin_bypass")


async def allow_group_chat_settings_change(user_id: int, chat_id: int) -> bool:
    """Return True when user may change group/chat settings in this chat."""
    if is_developer(user_id):
        return True
    if await user_repo.is_owner(user_id):
        return True
    if await admin_repo.is_music_admin_or_above(user_id, chat_id):
        return True
    if await admin_repo.is_player_owner(user_id, chat_id):
        return True
    if not await user_repo.is_sudo(user_id):
        return False
    return await sudo_has_permission(
        user_id,
        "can_manage_chat_settings",
        developer_bypass=False,
        owner_bypass=False,
    )


async def notify_sudo_permission_denied(
    update: Message | CallbackQuery | Any,
    field: str,
    lang: str = AUTO_LANG,
) -> None:
    """Send localized denial for a missing sudo permission."""
    text = t(lang, permission_denial_key(field))
    if isinstance(update, CallbackQuery) or hasattr(update, "answer"):
        try:
            await update.answer(text, show_alert=True)
        except Exception:
            return
        return
    if isinstance(update, Message):
        try:
            await update.reply(text)
        except Exception:
            return


async def deny_unless_sudo_permission(
    update: Message | CallbackQuery | Any,
    user_id: int,
    field: str,
    *,
    lang: str | None = None,
    developer_bypass: bool = True,
    owner_bypass: bool = True,
) -> bool:
    """Return True when allowed; otherwise notify denial and return False."""
    if await sudo_has_permission(
        user_id,
        field,
        developer_bypass=developer_bypass,
        owner_bypass=owner_bypass,
    ):
        return True
    resolved_lang = lang or await resolve_lang_from_update(update)
    await notify_sudo_permission_denied(update, field, resolved_lang)
    return False
