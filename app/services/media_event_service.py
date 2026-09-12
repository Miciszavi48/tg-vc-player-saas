"""Best-effort media play/download event tracking for developer URL ranking."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.database.engine import async_session
from app.database.models import MediaEvent
from app.utils.media_source_fingerprint import (
    MediaSourceFields,
    build_media_source_fields,
    build_telegram_media_fields,
)

logger = logging.getLogger(__name__)

MEDIA_EVENT_RETENTION_DAYS = 90
RANKING_TOP_LIMIT = 10
RANKING_HOST_LIMIT = 5
RANKING_RECENT_DAYS = 7


@dataclass(frozen=True)
class RankingEntry:
    """Aggregated ranking row for one fingerprint."""

    redacted_url: str
    host: str | None
    count: int
    url_fingerprint: str


@dataclass(frozen=True)
class HostRankingEntry:
    """Aggregated ranking row for one host."""

    host: str
    count: int


@dataclass(frozen=True)
class MediaRankingSummary:
    """Developer ranking snapshot."""

    top_played: tuple[RankingEntry, ...]
    top_downloaded: tuple[RankingEntry, ...]
    top_hosts: tuple[HostRankingEntry, ...]
    recent_top_played: tuple[RankingEntry, ...]
    total_events: int
    has_data: bool


async def _insert_media_event(
    event_type: str,
    fields: MediaSourceFields,
    *,
    media_type: str = "audio",
    chat_id: int | None = None,
    user_id: int | None = None,
    title: str | None = None,
) -> None:
    async with async_session() as session:
        session.add(
            MediaEvent(
                event_type=event_type,
                url_fingerprint=fields.url_fingerprint,
                redacted_url=fields.redacted_url,
                host=fields.host,
                media_type=media_type,
                chat_id=chat_id,
                user_id=user_id,
                title=(title or None)[:512] if title else None,
                source_kind=fields.source_kind,
            )
        )
        await session.commit()


async def track_media_play(
    source: str | None,
    *,
    media_type: str = "audio",
    chat_id: int | None = None,
    user_id: int | None = None,
    title: str | None = None,
    source_kind_hint: str | None = None,
) -> None:
    """Record a successful media play event. Never raises."""
    try:
        fields = build_media_source_fields(source, source_kind_hint=source_kind_hint)
        if fields is None:
            return
        await _insert_media_event(
            "play",
            fields,
            media_type=media_type,
            chat_id=chat_id,
            user_id=user_id,
            title=title,
        )
    except Exception:
        logger.debug("media play event tracking failed", exc_info=True)


async def track_media_download(
    source: str | None,
    *,
    media_type: str = "audio",
    chat_id: int | None = None,
    user_id: int | None = None,
    title: str | None = None,
    source_kind_hint: str | None = None,
    telegram_file_unique_id: str | None = None,
) -> None:
    """Record a successful media download event. Never raises."""
    try:
        if telegram_file_unique_id:
            fields = build_telegram_media_fields(telegram_file_unique_id, media_type)
        else:
            fields = build_media_source_fields(source, source_kind_hint=source_kind_hint)
        if fields is None:
            return
        await _insert_media_event(
            "download",
            fields,
            media_type=media_type,
            chat_id=chat_id,
            user_id=user_id,
            title=title,
        )
    except Exception:
        logger.debug("media download event tracking failed", exc_info=True)


async def _aggregate_ranking(
    event_type: str,
    *,
    limit: int,
    since: datetime | None = None,
) -> tuple[RankingEntry, ...]:
    fingerprint = MediaEvent.url_fingerprint
    redacted = func.max(MediaEvent.redacted_url)
    host = func.max(MediaEvent.host)
    count = func.count(MediaEvent.id)

    stmt = (
        select(fingerprint, redacted, host, count)
        .where(MediaEvent.event_type == event_type)
        .group_by(fingerprint)
        .order_by(count.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(MediaEvent.created_at >= since)

    async with async_session() as session:
        rows = (await session.execute(stmt)).all()

    return tuple(
        RankingEntry(
            url_fingerprint=str(row[0]),
            redacted_url=str(row[1] or "[redacted]"),
            host=str(row[2]) if row[2] else None,
            count=int(row[3]),
        )
        for row in rows
    )


async def _aggregate_hosts(*, limit: int, since: datetime | None = None) -> tuple[HostRankingEntry, ...]:
    stmt = (
        select(MediaEvent.host, func.count(MediaEvent.id))
        .where(MediaEvent.host.is_not(None))
        .group_by(MediaEvent.host)
        .order_by(func.count(MediaEvent.id).desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(MediaEvent.created_at >= since)

    async with async_session() as session:
        rows = (await session.execute(stmt)).all()

    return tuple(
        HostRankingEntry(host=str(row[0]), count=int(row[1]))
        for row in rows
        if row[0]
    )


async def get_media_ranking_summary() -> MediaRankingSummary:
    """Load all-time and recent ranking aggregates from media_events."""
    try:
        recent_since = datetime.now(timezone.utc) - timedelta(days=RANKING_RECENT_DAYS)

        async with async_session() as session:
            total = await session.scalar(select(func.count(MediaEvent.id))) or 0

        top_played, top_downloaded, top_hosts, recent_top_played = await _aggregate_ranking_batch(
            recent_since,
        )

        return MediaRankingSummary(
            top_played=top_played,
            top_downloaded=top_downloaded,
            top_hosts=top_hosts,
            recent_top_played=recent_top_played,
            total_events=int(total),
            has_data=int(total) > 0,
        )
    except Exception:
        logger.debug("media ranking query failed", exc_info=True)
        return MediaRankingSummary((), (), (), (), 0, False)


async def _aggregate_ranking_batch(
    recent_since: datetime,
) -> tuple[tuple[RankingEntry, ...], tuple[RankingEntry, ...], tuple[HostRankingEntry, ...], tuple[RankingEntry, ...]]:
    top_played = await _aggregate_ranking("play", limit=RANKING_TOP_LIMIT)
    top_downloaded = await _aggregate_ranking("download", limit=RANKING_TOP_LIMIT)
    top_hosts = await _aggregate_hosts(limit=RANKING_HOST_LIMIT)
    recent_top_played = await _aggregate_ranking(
        "play",
        limit=RANKING_TOP_LIMIT,
        since=recent_since,
    )
    return top_played, top_downloaded, top_hosts, recent_top_played


def ranking_summary_to_json(summary: MediaRankingSummary) -> dict:
    """Serialize ranking summary for JSON export."""
    note = (
        "Only events recorded after tracking was enabled are included."
        if summary.has_data
        else "No media ranking data yet. New plays and downloads will be tracked from now on."
    )

    def _rows(entries: tuple[RankingEntry, ...]) -> list[dict]:
        return [
            {
                "redacted_url": entry.redacted_url,
                "host": entry.host,
                "count": entry.count,
                "url_fingerprint": entry.url_fingerprint,
            }
            for entry in entries
        ]

    return {
        "generated_from": "media_events",
        "total_events": summary.total_events,
        "top_played": _rows(summary.top_played),
        "top_downloaded": _rows(summary.top_downloaded),
        "top_hosts": [
            {"host": entry.host, "count": entry.count}
            for entry in summary.top_hosts
        ],
        "recent_top_played_7d": _rows(summary.recent_top_played),
        "note": note,
    }


async def purge_old_media_events(retention_days: int = MEDIA_EVENT_RETENTION_DAYS) -> int:
    """Delete media events older than retention_days. Returns rows removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with async_session() as session:
        result = await session.execute(
            delete(MediaEvent).where(MediaEvent.created_at < cutoff)
        )
        await session.commit()
        return int(result.rowcount or 0)
