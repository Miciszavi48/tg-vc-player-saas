"""Shared authorization and credit checks for group media downloads."""

from __future__ import annotations

from app.repositories import admin_repo, settings_repo
from app.services import group_runtime_state_service
from app.services.media_capability_service import is_bot_media_feature_enabled
from app.utils.bot_guards import is_developer
from app.utils.sudo_permissions import can_use_sudo_admin_bypass


async def get_download_denial_key(*, chat_id: int, user_id: int) -> str | None:
    """Return an i18n denial key, or ``None`` when a group download is allowed."""
    if not await is_bot_media_feature_enabled("download"):
        return "download_cmd.disabled"

    settings = await settings_repo.get_chat_settings(chat_id)
    if settings and not settings.download_enabled and not is_developer(user_id):
        if not await can_use_sudo_admin_bypass(user_id):
            if not await admin_repo.is_music_admin_or_above(user_id, chat_id):
                return "download_cmd.disabled"

    runtime_state = await group_runtime_state_service.get_runtime_credit_state(chat_id, "group")
    return None if runtime_state.has_runtime_credit else "playback_cmd.no_credit"
