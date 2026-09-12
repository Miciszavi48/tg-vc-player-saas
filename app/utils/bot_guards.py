"""Global bot operational and sudo-panel access guards."""

from __future__ import annotations

from pyrogram.types import CallbackQuery, Message

from app.config.settings import settings
from app.repositories import global_ban_repo, settings_repo, user_repo
from app.services.language_service import resolve_lang_from_update
from app.utils.i18n import t


def get_developer_ids() -> frozenset[int]:
    """Return all configured developer Telegram user IDs."""
    return settings.DEVELOPER_IDS


def is_developer(user_id: int | None) -> bool:
    """Return True when the user is a configured developer."""
    return user_id is not None and int(user_id) in settings.DEVELOPER_IDS


async def is_bot_enabled() -> bool:
    """Return whether normal bot features are globally enabled."""
    return await settings_repo.get_bot_setting_bool("bot_enabled", default=True)


async def is_sudo_panel_enabled() -> bool:
    """Return whether the sudo private panel surface is enabled."""
    return await settings_repo.get_bot_setting_bool("sudo_panel_enabled", default=True)


async def _notify_blocked(
    update: Message | CallbackQuery,
    message_key: str,
) -> None:
    lang = await resolve_lang_from_update(update)
    text = t(lang, message_key)
    if isinstance(update, CallbackQuery):
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


async def deny_if_bot_disabled(
    update: Message | CallbackQuery,
    user_id: int | None,
) -> bool:
    """Return True when the update must be blocked because the bot is off."""
    if is_developer(user_id):
        return False
    if await is_bot_enabled():
        return False
    await _notify_blocked(update, "status.bot_disabled")
    return True


async def deny_if_sudo_panel_disabled(
    update: Message | CallbackQuery,
    user_id: int | None,
) -> bool:
    """Return True when a sudo-only user must be blocked from sudo panel access."""
    if user_id is None or is_developer(user_id):
        return False
    if await user_repo.is_owner(user_id):
        return False
    if not await user_repo.is_sudo(user_id):
        return False
    if await is_sudo_panel_enabled():
        return False
    await _notify_blocked(update, "status.sudo_panel_disabled")
    return True


async def deny_if_globally_banned(
    update: Message | CallbackQuery,
    user_id: int | None,
) -> bool:
    """Return True when the update must be blocked due to global ban-all."""
    if user_id is None or is_developer(user_id):
        return False
    if not await global_ban_repo.is_globally_banned(user_id):
        return False
    await _notify_blocked(update, "global_ban.user_blocked")
    return True
