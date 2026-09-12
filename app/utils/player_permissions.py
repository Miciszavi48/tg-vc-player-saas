"""Central player role ladder permission helpers."""

from __future__ import annotations

import logging
from typing import Any

from app.repositories import admin_repo, user_repo
from app.utils.bot_guards import is_developer
from app.utils.sudo_permissions import can_use_sudo_admin_bypass, sudo_has_permission

logger = logging.getLogger(__name__)


async def is_telegram_creator(client: Any, chat_id: int, user_id: int) -> bool:
    """Return True when the user is the Telegram group/channel creator."""
    get_chat_member = getattr(client, "get_chat_member", None)
    if not callable(get_chat_member):
        return False
    try:
        member = await get_chat_member(chat_id, user_id)
    except Exception:
        logger.debug(
            "telegram creator check failed chat_id=%s user_id=%s",
            chat_id,
            user_id,
            exc_info=True,
        )
        return False
    raw_status = getattr(member, "status", None)
    status = str(getattr(raw_status, "value", raw_status) or "").lower()
    return status in {"creator", "owner"}


async def _is_global_owner_or_developer(user_id: int) -> bool:
    return is_developer(user_id) or await user_repo.is_owner(user_id)


async def _has_local_sudo_authority(user_id: int) -> bool:
    """Flag-based sudo authority for global/private owner-management surfaces."""
    if await can_use_sudo_admin_bypass(user_id):
        return True
    return await sudo_has_permission(
        user_id,
        "can_manage_chat_settings",
        developer_bypass=False,
        owner_bypass=False,
    )


async def _has_group_player_bypass(client: Any, chat_id: int, user_id: int) -> bool:
    """In-group bypass for developer, bot owner, permitted sudo, or Telegram creator."""
    if is_developer(user_id):
        return True
    if await user_repo.is_owner(user_id):
        return True
    if await can_use_sudo_admin_bypass(user_id):
        return True
    return await is_telegram_creator(client, chat_id, user_id)


async def _can_access_group_player_panel(client: Any, chat_id: int, user_id: int) -> bool:
    """Shared gate for full group/player panel and settings (roles above VIP)."""
    if await _has_group_player_bypass(client, chat_id, user_id):
        return True
    return await admin_repo.is_music_admin_or_above(user_id, chat_id)


async def can_open_group_panel(client: Any, chat_id: int, user_id: int) -> bool:
    """Return True when user may open the main group/player panel via پنل."""
    return await _can_access_group_player_panel(client, chat_id, user_id)


async def can_open_group_settings(client: Any, chat_id: int, user_id: int) -> bool:
    """Return True when user may open full group settings (same gate as group panel)."""
    return await _can_access_group_player_panel(client, chat_id, user_id)


async def can_manage_call_security(client: Any, chat_id: int, user_id: int) -> bool:
    """Return True when user may open/manage Call Security (امنیت کال); Deputy+ and bypass only."""
    if await _has_group_player_bypass(client, chat_id, user_id):
        return True
    return await admin_repo.is_player_deputy_or_above(user_id, chat_id)


async def can_open_playback_panel(
    client: Any,
    chat_id: int,
    user_id: int,
    *,
    security_enabled: bool = True,
) -> bool:
    return await can_play_media(client, chat_id, user_id, security_enabled=security_enabled)


async def can_play_media(
    client: Any,
    chat_id: int,
    user_id: int,
    *,
    security_enabled: bool,
) -> bool:
    if not security_enabled:
        return True
    if await _has_group_player_bypass(client, chat_id, user_id):
        return True
    if await admin_repo.is_music_admin_or_above(user_id, chat_id):
        return True
    return await admin_repo.is_vip(user_id, chat_id)


async def can_use_playback_controls(
    client: Any,
    chat_id: int,
    user_id: int,
    *,
    security_enabled: bool,
) -> bool:
    return await can_play_media(client, chat_id, user_id, security_enabled=security_enabled)


async def can_manage_vip(client: Any, chat_id: int, user_id: int) -> bool:
    if await _has_group_player_bypass(client, chat_id, user_id):
        return True
    return await admin_repo.is_music_admin_or_above(user_id, chat_id)


async def can_manage_admin(client: Any, chat_id: int, user_id: int) -> bool:
    if await _has_group_player_bypass(client, chat_id, user_id):
        return True
    return await admin_repo.is_player_deputy_or_above(user_id, chat_id)


async def can_manage_deputy(client: Any, chat_id: int, user_id: int) -> bool:
    if await _has_group_player_bypass(client, chat_id, user_id):
        return True
    return await admin_repo.is_player_owner(user_id, chat_id)


async def can_manage_player_owner(client: Any, chat_id: int, user_id: int) -> bool:
    _ = (client, chat_id)
    if await _is_global_owner_or_developer(user_id):
        return True
    return await _has_local_sudo_authority(user_id)
