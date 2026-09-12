"""G7: Startup state recovery (§A5.2–A5.3).

Staggered, FloodWait-safe recovery of active playback sessions after
restart.  Runs as a background task — never blocks startup.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

from sqlalchemy import select

from app.config.settings import settings
from app.database.engine import async_session
from app.database.models import PlaybackState
from app.utils.diagnostic_logging import create_logged_task
from app.utils.media_sources import resolve_media_source_for_playback

logger = logging.getLogger(__name__)

_recovery_task: asyncio.Task | None = None

# Live/continuous stream markers that cannot be resumed at an arbitrary offset.
_NON_SEEKABLE_SOURCE_HINTS = (".m3u8", "m3u8?", "icecast", "shoutcast")


@dataclass(frozen=True)
class ResumeDecision:
    """Resolved instructions for resuming a persisted playback session.

    ``start_at``      seconds the stream should begin at (already clamped).
    ``resume_paused`` whether the session must be paused immediately after join.
    ``seekable``      whether the source can be resumed mid-stream at all.
    ``completed``     the persisted position was at/after the end of the track.
    """

    start_at: int
    resume_paused: bool
    seekable: bool
    completed: bool


def _is_seekable_source(source: str | None) -> bool:
    """Best-effort classification of whether a persisted source can be seeked.

    Definitely-live sources (HLS/Icecast/Shoutcast) are treated as
    non-seekable. Everything else is treated as potentially seekable; the
    actual seek is attempted at play time and falls back to 0 if the resolved
    media turns out to be remote/non-finite.
    """
    if not source:
        return False
    lowered = source.lower()
    if any(hint in lowered for hint in _NON_SEEKABLE_SOURCE_HINTS):
        return False
    return True


def compute_resume_position(
    *,
    seek_sec: int,
    duration_sec: int,
    is_paused: bool,
    is_seekable: bool,
) -> ResumeDecision:
    """Decide where and how a persisted playback session should resume.

    Rules:
      * A paused session stays paused at its saved position.
      * A playing seekable session resumes at the persisted ``seek_sec``.
        Process downtime is never added — audio does not advance while the
        service is down, so the position at ``last_update_at`` is the position
        to resume from.
      * The start position is clamped to ``[0, duration)``.
      * A track whose saved position already reached its duration is treated as
        completed and restarts at 0 (safe, no out-of-range seek).
      * A non-seekable/live source cannot resume mid-stream and restarts at 0.
    """
    seek = max(0, int(seek_sec or 0))
    duration = max(0, int(duration_sec or 0))
    completed = duration > 0 and seek >= duration

    if not is_seekable:
        return ResumeDecision(
            start_at=0, resume_paused=bool(is_paused), seekable=False, completed=completed,
        )
    if completed:
        return ResumeDecision(
            start_at=0, resume_paused=bool(is_paused), seekable=True, completed=True,
        )

    start = seek
    if duration > 0:
        start = min(start, duration - 1)  # clamp to [0, duration)
    return ResumeDecision(
        start_at=max(0, start), resume_paused=bool(is_paused), seekable=True, completed=False,
    )


async def schedule_recovery(call_py) -> None:
    """Spawn recovery as a background task (called from main.py after bot starts)."""
    global _recovery_task
    if _recovery_task is not None and not _recovery_task.done():
        logger.warning("Recovery already running — skipping duplicate schedule")
        return
    _recovery_task = create_logged_task(_run_recovery(call_py), name="playback_recovery")


async def _run_recovery(call_py) -> None:
    from app.services import CallService

    async with async_session() as session:
        stmt = select(PlaybackState).order_by(PlaybackState.last_update_at.desc())
        result = await session.execute(stmt)
        states = list(result.scalars().all())

    total = len(states)
    if total == 0:
        logger.info("Recovery: no active playback sessions to recover")
        return

    logger.info("Recovery: %d sessions to recover", total)

    sem = asyncio.Semaphore(settings.RECOVERY_MAX_CONCURRENT)
    recovered = 0
    failed = 0
    skipped = 0

    rate_delay = 1.0 / max(1, settings.RECOVERY_GLOBAL_PER_SECOND)

    for state in states:
        await asyncio.sleep(rate_delay)
        jitter = random.randint(0, settings.RECOVERY_JITTER_MS) / 1000.0
        await asyncio.sleep(jitter)

        async with sem:
            try:
                source = state.source
                if not source:
                    skipped += 1
                    continue

                from app.services.media_capability_service import (
                    feature_from_media_type,
                    is_media_feature_allowed,
                )

                media_type = state.media_type or "audio"
                if not await is_media_feature_allowed(
                    state.chat_id,
                    feature_from_media_type(media_type),
                ):
                    logger.info(
                        "Recovery skipped %s for chat %s (free-mode restriction)",
                        media_type,
                        state.chat_id,
                    )
                    await CallService.clear_persisted_playback_state(state.chat_id)
                    skipped += 1
                    continue

                persisted_seek = int(getattr(state, "seek_sec", 0) or 0)
                persisted_duration = int(getattr(state, "duration_sec", 0) or 0)
                persisted_paused = bool(getattr(state, "is_paused", False))
                decision = compute_resume_position(
                    seek_sec=persisted_seek,
                    duration_sec=persisted_duration,
                    is_paused=persisted_paused,
                    is_seekable=_is_seekable_source(source),
                )
                logger.info(
                    "Recovery: chat %s persisted seek=%ss duration=%ss paused=%s "
                    "→ resume at %ss (paused=%s, seekable=%s, completed=%s)",
                    state.chat_id,
                    persisted_seek,
                    persisted_duration,
                    persisted_paused,
                    decision.start_at,
                    decision.resume_paused,
                    decision.seekable,
                    decision.completed,
                )

                ok = await CallService.join_voice_chat(
                    call_py,
                    state.chat_id,
                    source,
                    media_type,
                    skip_event_tracking=True,
                    resume_from_seconds=decision.start_at,
                    resume_paused=decision.resume_paused,
                    duration_seconds=persisted_duration,
                )
                if ok:
                    recovered += 1
                elif await resolve_media_source_for_playback(
                    source, chat_id=state.chat_id,
                ) is None:
                    logger.warning(
                        "Recovery: dropped unsafe persisted source for chat %s",
                        state.chat_id,
                    )
                    await CallService.clear_persisted_playback_state(state.chat_id)
                    skipped += 1
                else:
                    failed += 1
            except Exception as exc:
                err_str = str(exc).lower()
                if "floodwait" in err_str or "flood" in err_str:
                    wait_secs = 30
                    try:
                        wait_secs = int("".join(c for c in err_str if c.isdigit()) or "30")
                    except ValueError:
                        pass
                    logger.warning("Recovery FloodWait for chat %s — sleeping %ds", state.chat_id, wait_secs)
                    await asyncio.sleep(wait_secs + random.randint(1, 3))
                    failed += 1
                else:
                    logger.exception("Recovery failed for chat %s", state.chat_id)
                    failed += 1

        if (recovered + failed) % 50 == 0 and (recovered + failed) > 0:
            logger.info("Recovery progress: %d/%d recovered, %d failed", recovered, total, failed)

    logger.info(
        "Recovery complete: %d recovered, %d failed, %d skipped out of %d",
        recovered, failed, skipped, total,
    )
