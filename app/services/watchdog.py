"""Streaming reliability watchdog (§A8).

Detects and cleans up:
- Orphan voice calls (in-memory state but no active pytgcalls session)
- Zombie FFmpeg processes (registered but process exited)
- Stale playback_states rows with no matching in-memory call
- Helper accounts stuck in cooldown past their cooldown_until

All functions are safe to call from the scheduler or manually.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import delete, select

from app.database.engine import async_session
from app.database.models import HelperAccount, PlaybackState
from app.services.call_service import _active_calls, _ffmpeg_processes
from app.utils.i18n import t

logger = logging.getLogger(__name__)


async def cleanup_orphan_calls(call_py) -> int:
    """Detect in-memory active calls that have no live pytgcalls session
    and remove them.  Returns count of orphans cleaned."""
    cleaned = 0
    for chat_id in list(_active_calls.keys()):
        is_alive = False
        try:
            active = getattr(call_py, "active_calls", None)
            if active is not None:
                is_alive = chat_id in active
            else:
                is_alive = True
        except Exception:
            is_alive = True

        if not is_alive:
            _active_calls.pop(chat_id, None)
            proc = _ffmpeg_processes.pop(chat_id, None)
            if proc is not None:
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
            cleaned += 1
            logger.info("Cleaned orphan call state for chat %s", chat_id)

    return cleaned


async def cleanup_zombie_ffmpeg() -> int:
    """Kill FFmpeg processes in the registry that have already exited
    but were not reaped.  Returns count cleaned."""
    cleaned = 0
    for chat_id in list(_ffmpeg_processes.keys()):
        proc = _ffmpeg_processes.get(chat_id)
        if proc is None:
            continue
        if proc.returncode is not None:
            _ffmpeg_processes.pop(chat_id, None)
            cleaned += 1
            logger.info("Removed dead FFmpeg process for chat %s (rc=%s)", chat_id, proc.returncode)
        else:
            try:
                os.kill(proc.pid, 0)
            except (ProcessLookupError, OSError):
                _ffmpeg_processes.pop(chat_id, None)
                cleaned += 1
                logger.info("Removed zombie FFmpeg for chat %s (pid gone)", chat_id)
    return cleaned


async def cleanup_stale_playback_states() -> int:
    """Remove playback_states rows for chats that have no in-memory
    active call (e.g. after a crash recovery that didn't resume them)."""
    active_chat_ids = set(_active_calls.keys())
    removed = 0

    async with async_session() as session:
        stmt = select(PlaybackState)
        result = await session.execute(stmt)
        states = list(result.scalars().all())

    stale_ids = [s.chat_id for s in states if s.chat_id not in active_chat_ids]
    if not stale_ids:
        return 0

    cutoff = datetime.now(timezone.utc).timestamp() - 3600
    for s in states:
        if s.chat_id in active_chat_ids:
            continue
        if s.last_update_at and s.last_update_at.timestamp() < cutoff:
            async with async_session() as session:
                async with session.begin():
                    await session.execute(
                        delete(PlaybackState).where(PlaybackState.chat_id == s.chat_id)
                    )
            removed += 1

    if removed:
        logger.info("Cleaned %d stale playback_states rows", removed)
    return removed


async def restore_cooled_down_helpers() -> int:
    """Reactivate helpers whose cooldown_until has passed."""
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        async with session.begin():
            stmt = (
                select(HelperAccount)
                .where(
                    HelperAccount.status.in_(("quarantined", "cooldown")),
                    HelperAccount.cooldown_until.isnot(None),
                    HelperAccount.cooldown_until <= now,
                )
            )
            result = await session.execute(stmt)
            helpers = list(result.scalars().all())

            for h in helpers:
                h.status = "active"
                h.cooldown_until = None

    if helpers:
        logger.info("Restored %d helpers from cooldown/quarantine", len(helpers))
    return len(helpers)


async def run_full_watchdog(call_py, bot=None) -> dict[str, int]:
    """Run all watchdog checks.  Called by scheduler."""
    results: dict[str, int] = {}

    results["orphan_calls"] = await cleanup_orphan_calls(call_py)
    results["zombie_ffmpeg"] = await cleanup_zombie_ffmpeg()
    results["stale_states"] = await cleanup_stale_playback_states()
    results["restored_helpers"] = await restore_cooled_down_helpers()

    total = sum(results.values())
    if total > 0:
        logger.info("Watchdog summary: %s", results)
        if bot is not None:
            try:
                from app.services.notification_service import NotificationService
                summary = t("fa", "gc.watchdog_summary",
                            orphans=results["orphan_calls"],
                            zombies=results["zombie_ffmpeg"],
                            stale=results["stale_states"],
                            restored=results["restored_helpers"])
                await NotificationService.send_to_log_channel(bot, summary)
            except Exception:
                logger.debug("Could not send watchdog summary to log channel")

    return results
