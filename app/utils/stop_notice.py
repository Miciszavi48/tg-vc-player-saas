"""Auto-clearing delivery for the bot's own 'playback stopped' notice.

MISC-06: when a chat enables `auto_clear_stopped_enabled`, the stopped notice is
removed from the group ~10 seconds after it is sent so the chat stays clean. The
toggle is off by default, so the notice behaves exactly as before unless a group
admin opts in.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

AUTO_CLEAR_DELAY_SECONDS = 10

# Keeps a strong reference to in-flight deletions so the event loop cannot
# garbage-collect a pending task before it runs.
_pending: set[asyncio.Task[None]] = set()


async def is_auto_clear_enabled(chat_id: int) -> bool:
    """Return whether this chat auto-deletes the playback-stopped notice."""
    try:
        from app.repositories import settings_repo

        row = await settings_repo.get_chat_settings(chat_id)
        return bool(getattr(row, "auto_clear_stopped_enabled", False))
    except Exception:
        logger.debug("auto-clear lookup failed chat_id=%s", chat_id, exc_info=True)
        return False


async def _delete_later(sent: Any, delay: int) -> None:
    try:
        await asyncio.sleep(delay)
        delete = getattr(sent, "delete", None)
        if callable(delete):
            await delete()
    except asyncio.CancelledError:
        raise
    except Exception:
        # A notice already removed by a moderator/cleanup is not an error.
        logger.debug("auto-clear delete failed", exc_info=True)


def schedule_auto_clear(sent: Any, delay: int = AUTO_CLEAR_DELAY_SECONDS) -> None:
    """Schedule deletion of an already-sent message, if a loop is running."""
    if sent is None:
        return
    try:
        task = asyncio.create_task(_delete_later(sent, delay))
    except RuntimeError:
        # No running loop (e.g. synchronous test context) - nothing to schedule.
        return
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def reply_stop_notice(message: Any, text: str, chat_id: int) -> Any:
    """Reply with a playback-stopped notice, honouring the per-chat auto-clear."""
    sent = await message.reply(text)
    if await is_auto_clear_enabled(chat_id):
        schedule_auto_clear(sent)
    return sent
