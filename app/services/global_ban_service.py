"""Best-effort removal of globally banned users from installed chats."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from pyrogram import Client

from app.repositories import channel_repo, group_repo

logger = logging.getLogger(__name__)

_DEFAULT_CONCURRENCY = 8
_SKIP_EXCEPTIONS = frozenset(
    {
        "UserAdminInvalid",
        "ChatAdminRequired",
        "UserNotParticipant",
        "PeerIdInvalid",
        "ChannelPrivate",
        "InputUserDeactivated",
    }
)


@dataclass(frozen=True)
class RemovalSummary:
    """Result of ban-all chat removal sweep."""

    attempted: int
    removed: int
    failed: int
    skipped: int


def _classify_exception(exc: BaseException) -> str:
    """Map a Telegram exception to a summary bucket."""
    name = type(exc).__name__
    if name in _SKIP_EXCEPTIONS:
        return "skipped"
    if "FloodWait" in name:
        return "failed"
    return "failed"


async def _ban_in_chat(client: Client, chat_id: int, user_id: int) -> str:
    """Attempt to ban a user in one chat. Returns removed/skipped/failed."""
    try:
        await client.ban_chat_member(chat_id, user_id)
        return "removed"
    except Exception as exc:
        exc_name = type(exc).__name__
        if "FloodWait" in exc_name:
            wait = min(int(getattr(exc, "value", 5) or 5), 30)
            await asyncio.sleep(wait)
            try:
                await client.ban_chat_member(chat_id, user_id)
                return "removed"
            except Exception as retry_exc:
                logger.info(
                    "Global ban retry failed for user %s in chat %s: %s",
                    user_id,
                    chat_id,
                    retry_exc,
                )
                return _classify_exception(retry_exc)
        logger.info(
            "Global ban removal failed for user %s in chat %s: %s",
            user_id,
            chat_id,
            exc,
        )
        return _classify_exception(exc)


async def remove_user_from_installed_chats(
    client: Client,
    user_id: int,
    *,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> RemovalSummary:
    """Ban a user from every active installed group and channel.

    Failures in one chat do not stop the sweep.
    """
    groups = await group_repo.get_all_active_groups()
    channels = await channel_repo.get_all_active_channels()
    chat_ids: list[int] = []
    seen: set[int] = set()
    for row in groups:
        if row.chat_id not in seen:
            seen.add(row.chat_id)
            chat_ids.append(row.chat_id)
    for row in channels:
        if row.chat_id not in seen:
            seen.add(row.chat_id)
            chat_ids.append(row.chat_id)

    if not chat_ids:
        return RemovalSummary(attempted=0, removed=0, failed=0, skipped=0)

    sem = asyncio.Semaphore(max(1, concurrency))
    removed = 0
    failed = 0
    skipped = 0

    async def _run(chat_id: int) -> str:
        async with sem:
            return await _ban_in_chat(client, chat_id, user_id)

    results = await asyncio.gather(*[_run(cid) for cid in chat_ids], return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            failed += 1
            continue
        if result == "removed":
            removed += 1
        elif result == "skipped":
            skipped += 1
        else:
            failed += 1

    return RemovalSummary(
        attempted=len(chat_ids),
        removed=removed,
        failed=failed,
        skipped=skipped,
    )
