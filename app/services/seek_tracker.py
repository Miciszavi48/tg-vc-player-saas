"""G29: Seek state persistence (§A5.4).

Tracks playback position per chat:
- Redis updated every SEEK_REDIS_INTERVAL seconds (default 5)
- DB flushed every SEEK_DB_INTERVAL seconds (default 30)
- Immediate flush on stop/pause/next/prev

Background task per active chat, auto-cancelled on playback end.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import update

from app.database.engine import async_session
from app.database.models import PlaybackState
from app.utils.diagnostic_logging import create_logged_task
from app.utils.redis_keys import seek_key

logger = logging.getLogger(__name__)

SEEK_REDIS_INTERVAL = 5
SEEK_DB_INTERVAL = 30

_seek_tasks: dict[int, asyncio.Task] = {}
_seek_positions: dict[int, int] = {}


async def start_seek_tracker(chat_id: int, initial_seek: int = 0) -> None:
    """Start a seek tracking background task for a chat."""
    stop_seek_tracker(chat_id)
    _seek_positions[chat_id] = initial_seek
    task = create_logged_task(_seek_loop(chat_id), name=f"seek_tracker_{chat_id}")
    _seek_tasks[chat_id] = task


def stop_seek_tracker(chat_id: int) -> None:
    """Cancel and remove the seek tracker for a chat."""
    task = _seek_tasks.pop(chat_id, None)
    if task is not None and not task.done():
        task.cancel()
    _seek_positions.pop(chat_id, None)


async def flush_seek_to_db(chat_id: int, seek_sec: int) -> None:
    """Write current seek position to DB immediately."""
    try:
        async with async_session() as session:
            async with session.begin():
                stmt = (
                    update(PlaybackState)
                    .where(PlaybackState.chat_id == chat_id)
                    .values(
                        seek_sec=seek_sec,
                        last_update_at=datetime.now(timezone.utc),
                    )
                )
                await session.execute(stmt)
    except Exception:
        logger.debug("Failed to flush seek to DB for chat %s", chat_id)


async def flush_seek_to_redis(chat_id: int, seek_sec: int) -> None:
    """Write current seek position to Redis."""
    try:
        from app.utils.cache import get_redis
        r = await get_redis()
        await r.set(seek_key(chat_id), str(seek_sec), ex=60)
    except Exception:
        logger.debug("Failed to flush seek to Redis for chat %s", chat_id)


async def get_seek_from_redis(chat_id: int) -> int | None:
    """Read cached seek position from Redis."""
    try:
        from app.utils.cache import get_redis
        r = await get_redis()
        val = await r.get(seek_key(chat_id))
        return int(val) if val is not None else None
    except Exception:
        return None


def get_current_seek(chat_id: int) -> int:
    """Return in-memory seek position for a chat."""
    return _seek_positions.get(chat_id, 0)


def update_seek(chat_id: int, seek_sec: int) -> None:
    """Update in-memory seek (called by playback engine if available)."""
    _seek_positions[chat_id] = seek_sec


async def _seek_loop(chat_id: int) -> None:
    """Background loop: updates Redis every 5s, DB every 30s."""
    db_counter = 0
    try:
        while True:
            await asyncio.sleep(SEEK_REDIS_INTERVAL)
            pos = _seek_positions.get(chat_id, 0)
            pos += SEEK_REDIS_INTERVAL
            _seek_positions[chat_id] = pos

            await flush_seek_to_redis(chat_id, pos)

            db_counter += SEEK_REDIS_INTERVAL
            if db_counter >= SEEK_DB_INTERVAL:
                await flush_seek_to_db(chat_id, pos)
                db_counter = 0
    except asyncio.CancelledError:
        pos = _seek_positions.get(chat_id, 0)
        await flush_seek_to_db(chat_id, pos)
        await flush_seek_to_redis(chat_id, pos)
