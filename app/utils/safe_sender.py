"""Centralized safe message sender — handles Telegram API exceptions gracefully.

Catches UserIsBlocked, ChatWriteForbidden, PeerIdInvalid, FloodWait
and optionally marks unreachable chats/users in the database.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def safe_send_message(
    bot,
    chat_id: int,
    text: str,
    mark_unreachable: bool = True,
    **kwargs,
) -> bool:
    """Send a message, handling all common Telegram exceptions.

    Returns True if sent successfully, False otherwise.
    Never raises — safe for use in loops, crons, and notification flows.
    """
    try:
        await bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as exc:
        exc_name = type(exc).__name__
        exc_str = str(exc)

        if "FloodWait" in exc_name:
            wait = getattr(exc, "value", 5)
            if not isinstance(wait, (int, float)):
                wait = 5
            logger.warning("safe_send: FloodWait %ds for chat %d", wait, chat_id)
            await asyncio.sleep(min(wait, 60))
            try:
                await bot.send_message(chat_id, text, **kwargs)
                return True
            except Exception:
                return False

        if any(sig in exc_name for sig in ("UserIsBlocked", "InputUserDeactivated", "PeerIdInvalid")):
            logger.info("safe_send: user unreachable (%s) chat_id=%d", exc_name, chat_id)
            if mark_unreachable:
                await _mark_user_unreachable(chat_id)
            return False

        if "ChatWriteForbidden" in exc_name or "CHAT_WRITE_FORBIDDEN" in exc_str:
            logger.info("safe_send: chat write forbidden, chat_id=%d", chat_id)
            if mark_unreachable:
                await _mark_chat_inactive(chat_id)
            return False

        if "ChatAdminRequired" in exc_name or "CHAT_ADMIN_REQUIRED" in exc_str:
            logger.info("safe_send: bot not admin, chat_id=%d", chat_id)
            return False

        logger.debug("safe_send: unhandled error for chat %d: %s", chat_id, exc_name)
        return False


async def _mark_user_unreachable(user_id: int) -> None:
    """Flag a user as banned/unreachable in the DB to skip future sends."""
    try:
        from app.database.engine import async_session
        from app.database.models import User
        from sqlalchemy import update

        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(User)
                    .where(User.user_id == user_id)
                    .values(is_banned=True)
                )
    except Exception:
        logger.debug("_mark_user_unreachable failed for %d", user_id)


async def _mark_chat_inactive(chat_id: int) -> None:
    """Mark a group/channel as inactive when bot can no longer write."""
    try:
        from app.database.engine import async_session
        from app.database.models import Group
        from sqlalchemy import update

        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(Group)
                    .where(Group.chat_id == chat_id)
                    .values(status="inactive")
                )
    except Exception:
        logger.debug("_mark_chat_inactive failed for %d", chat_id)
