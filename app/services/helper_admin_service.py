"""Promote helper user accounts to Telegram admins for group-call actions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pyrogram.types import ChatAdministratorRights
from sqlalchemy import update

from app.database.engine import async_session
from app.database.models import HelperAccount
from app.services.admin_title_service import _bot_can_promote_members, _status_value
from app.services.helper_pool_service import HelperPoolService
from app.utils.diagnostic_logging import safe_exc_name
from app.utils.helper_admin_rights import helper_has_call_admin_rights, member_is_in_group

if TYPE_CHECKING:
    from pyrogram import Client

logger = logging.getLogger(__name__)

_ADMIN_STATUSES = {"administrator", "creator"}


@dataclass(frozen=True)
class HelperAdminResult:
    """Outcome of ensuring a helper has call-management admin rights."""

    ok: bool
    reason: str


def build_helper_call_admin_privileges() -> ChatAdministratorRights:
    """Return minimal admin rights needed for helper group-call management."""
    return ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=False,
        can_manage_video_chats=True,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_post_messages=False,
        can_edit_messages=False,
        can_pin_messages=False,
    )


async def resolve_helper_tg_user_id(helper_id: int) -> int | None:
    """Resolve the helper Telegram user id from DB or a live helper session."""
    helper = await HelperPoolService._get_helper_row(helper_id)
    if helper is None:
        return None
    if helper.tg_user_id is not None:
        return int(helper.tg_user_id)

    session_str = await HelperPoolService.get_helper_session(helper_id)
    if session_str is None:
        return None

    try:
        client = HelperPoolService.build_client(
            f"helper_resolve_uid_{helper_id}",
            session_str,
            helper,
        )
        async with client:
            me = await client.get_me()
            if me is None or me.id is None:
                return None
            tg_user_id = int(me.id)
    except Exception as exc:
        logger.warning(
            "failed to resolve helper tg_user_id helper_id=%s exc=%s",
            helper_id,
            safe_exc_name(exc),
        )
        return None

    async with async_session() as session:
        async with session.begin():
            await session.execute(
                update(HelperAccount)
                .where(HelperAccount.id == helper_id)
                .values(tg_user_id=tg_user_id)
            )
    return tg_user_id


async def ensure_helper_call_admin(
    bot_client: Client | None,
    chat_id: int,
    helper_id: int,
) -> HelperAdminResult:
    """Ensure the helper user is a Telegram admin with call-management rights.

    Idempotent: skips promotion when rights are already sufficient and upgrades
    existing administrators when call permissions are missing.
    """
    if bot_client is None:
        return HelperAdminResult(ok=False, reason="bot_unavailable")

    tg_user_id = await resolve_helper_tg_user_id(helper_id)
    if tg_user_id is None:
        return HelperAdminResult(ok=False, reason="helper_user_unknown")

    try:
        bot_member = await bot_client.get_chat_member(chat_id, "me")
    except Exception as exc:
        logger.warning(
            "helper promote bot membership check failed chat_id=%s exc=%s",
            chat_id,
            safe_exc_name(exc),
        )
        return HelperAdminResult(ok=False, reason="promote_failed")

    bot_status = _status_value(bot_member)
    if bot_status not in _ADMIN_STATUSES:
        return HelperAdminResult(ok=False, reason="bot_no_promote_rights")
    if not _bot_can_promote_members(bot_member):
        return HelperAdminResult(ok=False, reason="bot_no_promote_rights")

    try:
        member = await bot_client.get_chat_member(chat_id, tg_user_id)
    except Exception as exc:
        logger.warning(
            "helper promote target lookup failed chat_id=%s helper_id=%s tg_user_id=%s exc=%s",
            chat_id,
            helper_id,
            tg_user_id,
            safe_exc_name(exc),
        )
        return HelperAdminResult(ok=False, reason="not_in_group")

    if not member_is_in_group(member):
        return HelperAdminResult(ok=False, reason="not_in_group")

    if helper_has_call_admin_rights(member):
        logger.info(
            "helper already has call admin rights chat_id=%s helper_id=%s tg_user_id=%s",
            chat_id,
            helper_id,
            tg_user_id,
        )
        return HelperAdminResult(ok=True, reason="already_admin")

    privileges = build_helper_call_admin_privileges()
    try:
        promoted = await bot_client.promote_chat_member(
            chat_id,
            tg_user_id,
            privileges=privileges,
        )
        if not promoted:
            return HelperAdminResult(ok=False, reason="promote_failed")
    except Exception as exc:
        logger.warning(
            "helper promote failed chat_id=%s helper_id=%s tg_user_id=%s exc=%s",
            chat_id,
            helper_id,
            tg_user_id,
            safe_exc_name(exc),
        )
        return HelperAdminResult(ok=False, reason="promote_failed")

    try:
        verified = await bot_client.get_chat_member(chat_id, tg_user_id)
    except Exception:
        logger.info(
            "helper promoted but verify lookup failed chat_id=%s helper_id=%s",
            chat_id,
            helper_id,
        )
        return HelperAdminResult(ok=True, reason="promoted")

    if helper_has_call_admin_rights(verified):
        logger.info(
            "helper promoted for call admin chat_id=%s helper_id=%s tg_user_id=%s",
            chat_id,
            helper_id,
            tg_user_id,
        )
        return HelperAdminResult(ok=True, reason="promoted")

    return HelperAdminResult(ok=False, reason="promote_failed")
