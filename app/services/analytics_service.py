from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from app.database.engine import async_session
from app.utils.cache import acquire_lock, get_redis, release_lock
from app.utils.redis_keys import (
    ANALYTICS_FLUSH_LOCK,
    AN_SCAN_PATTERN,
    TTL_AN,
    TTL_REPORT_CACHE,
    an_counter_key,
    instance_key,
    strip_instance_namespace,
)

logger = logging.getLogger(__name__)


def _incr(pipe, day: str, hour: str, scope: str, scope_key: str, metric: str, value: int) -> None:
    key = an_counter_key(day, hour, scope, scope_key, metric)
    pipe.incrby(key, value)
    pipe.expire(key, TTL_AN)


async def track_event(
    event_name: str,
    chat_type: str = "private",
    role: str = "user",
    feature: str | None = None,
    status: str = "success",
    value: int = 1,
    sudo_id: int | None = None,
) -> None:
    """Increment Redis analytics counters. Never crashes user flows."""
    try:
        r = await get_redis()
        now = datetime.now(timezone.utc)
        day = now.strftime("%Y%m%d")
        hour = str(now.hour)

        pipe = r.pipeline()

        _incr(pipe, day, hour, "global", "global", "events_total", value)
        _incr(pipe, day, hour, "global", "global", event_name, value)

        _incr(pipe, day, hour, "chat_type", chat_type, "events_total", value)
        _incr(pipe, day, hour, "chat_type", chat_type, event_name, value)

        _incr(pipe, day, hour, "role", role, "events_total", value)

        if feature:
            _incr(pipe, day, hour, "feature", feature, "events_total", value)
            _incr(pipe, day, hour, "feature", feature, event_name, value)

        if sudo_id:
            _incr(pipe, day, hour, "sudo", str(sudo_id), event_name, value)

        await pipe.execute()
    except Exception:
        logger.debug("Analytics tracking failed for %s", event_name)


async def track_error(category: str, feature: str = "general") -> None:
    """Track an error event. Categories: telegram_rpc, db, redis, ffmpeg, pytgcalls."""
    await track_event(f"errors.{category}", feature=feature, status="fail")


async def flush_analytics() -> None:
    """Flush Redis analytics counters to Postgres. Multi-instance safe."""
    token = await acquire_lock(ANALYTICS_FLUSH_LOCK, ttl_ms=60_000)
    if not token:
        return
    try:
        r = await get_redis()

        cursor_val = 0
        batch: list[str] = []
        while True:
            cursor_val, keys = await r.scan(cursor_val, match=AN_SCAN_PATTERN, count=500)
            batch.extend(keys)
            if cursor_val == 0:
                break

        if not batch:
            return

        pipe = r.pipeline()
        for key in batch:
            pipe.getdel(key)
        values = await pipe.execute()

        rows = []
        for key, val in zip(batch, values):
            if val is None:
                continue
            parts = strip_instance_namespace(key).split(":", 5)
            if len(parts) != 6:
                continue
            _, day_str, hour_str, scope, scope_key, metric = parts
            try:
                parsed_day = date(int(day_str[:4]), int(day_str[4:6]), int(day_str[6:8]))
            except (ValueError, IndexError):
                continue
            rows.append({
                "day": parsed_day,
                "hour": int(hour_str),
                "scope": scope,
                "scope_key": scope_key,
                "metric": metric,
                "delta": int(val),
            })

        if rows:
            async with async_session() as session:
                async with session.begin():
                    for row in rows:
                        await session.execute(text("""
                            INSERT INTO analytics_hourly (day, hour, scope, scope_key, metric, value)
                            VALUES (:day, :hour, :scope, :scope_key, :metric, :delta)
                            ON CONFLICT (day, hour, scope, scope_key, metric)
                            DO UPDATE SET value = analytics_hourly.value + EXCLUDED.value
                        """), row)

        logger.info("Analytics flush: %d keys processed", len(rows))
    except Exception:
        logger.exception("Analytics flush failed")
    finally:
        await release_lock(ANALYTICS_FLUSH_LOCK, token)


async def _query_metrics(
    start_day: date,
    end_day: date,
    scope: str = "global",
    scope_key: str = "global",
) -> dict[str, int]:
    """Return {metric: total_value} for the date range and scope."""
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT metric, SUM(value) as total
            FROM analytics_hourly
            WHERE day BETWEEN :start AND :end
              AND scope = :scope AND scope_key = :scope_key
            GROUP BY metric
            ORDER BY total DESC
        """), {"start": start_day, "end": end_day, "scope": scope, "scope_key": scope_key})
        return {row.metric: row.total for row in result}


async def _query_peak_hours(
    start_day: date,
    end_day: date,
    limit: int = 3,
) -> list[tuple[int, int]]:
    """Return top N (hour, total) pairs for events_total in the range."""
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT hour, SUM(value) as total
            FROM analytics_hourly
            WHERE day BETWEEN :start AND :end
              AND scope = 'global' AND scope_key = 'global'
              AND metric = 'events_total'
            GROUP BY hour
            ORDER BY total DESC
            LIMIT :limit
        """), {"start": start_day, "end": end_day, "limit": limit})
        return [(row.hour, row.total) for row in result]


async def _query_drilldown(
    start_day: date,
    end_day: date,
    scope: str,
) -> dict[str, int]:
    """Return {scope_key: total events} for the given scope in the range."""
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT scope_key, SUM(value) as total
            FROM analytics_hourly
            WHERE day BETWEEN :start AND :end
              AND scope = :scope AND metric = 'events_total'
            GROUP BY scope_key
            ORDER BY total DESC
        """), {"start": start_day, "end": end_day, "scope": scope})
        return {row.scope_key: row.total for row in result}


def _cache_key(report_type: str, range_str: str, scope: str, scope_key: str) -> str:
    return instance_key(
        f"cache:analytics:report:{report_type}:{range_str}:{scope}:{scope_key}"
    )


async def _get_cached(key: str) -> dict | None:
    try:
        r = await get_redis()
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _set_cached(key: str, data: dict) -> None:
    try:
        r = await get_redis()
        await r.set(key, json.dumps(data, default=str), ex=TTL_REPORT_CACHE)
    except Exception:
        pass


async def get_report(
    report_type: str,
    days: int = 1,
    scope: str = "global",
    scope_key: str = "global",
) -> dict:
    """Unified report generator with caching."""
    range_str = str(days)
    ck = _cache_key(report_type, range_str, scope, scope_key)
    cached = await _get_cached(ck)
    if cached:
        return cached

    today = date.today()

    if report_type == "yesterday":
        yesterday = today - timedelta(days=1)
        metrics = await _query_metrics(yesterday, yesterday, scope, scope_key)
        result = _format_summary(metrics, str(yesterday))
    elif report_type == "range":
        start = today - timedelta(days=days)
        end = today - timedelta(days=1)
        metrics = await _query_metrics(start, end, scope, scope_key)
        result = _format_summary(metrics, f"{start} → {end}")
    elif report_type == "peak":
        start = today - timedelta(days=7)
        peaks = await _query_peak_hours(start, today - timedelta(days=1))
        result = {"peaks": [{"hour": h, "count": c} for h, c in peaks], "range": f"{start} → {today - timedelta(days=1)}"}
    elif report_type == "errors":
        yesterday = today - timedelta(days=1)
        metrics = await _query_metrics(yesterday, yesterday, scope, scope_key)
        error_metrics = {k: v for k, v in metrics.items() if k.startswith("errors.")}
        result = {"errors": error_metrics, "range": str(yesterday)}
    elif report_type == "drilldown":
        yesterday = today - timedelta(days=1)
        data = await _query_drilldown(yesterday, yesterday, scope)
        result = {"breakdown": data, "scope": scope, "range": str(yesterday)}
    else:
        result = {"error": "unknown_report_type"}

    await _set_cached(ck, result)
    return result


def _format_summary(metrics: dict[str, int], range_label: str) -> dict:
    """Format metrics dict into a structured report."""
    return {
        "range": range_label,
        "events_total": metrics.get("events_total", 0),
        "install_groups": metrics.get("install.created.group", 0),
        "install_channels": metrics.get("install.created.channel", 0),
        "install_failed": metrics.get("install.failed", 0),
        "credit_charge": metrics.get("credit.charge", 0),
        "credit_deduct": metrics.get("credit.deduct", 0),
        "credit_trial": metrics.get("credit.trial_activated", 0),
        "play_audio": metrics.get("playback.play_audio", 0),
        "play_video": metrics.get("playback.play_video", 0),
        "playback_stop": metrics.get("playback.stop", 0),
        "bc_created_send": metrics.get("broadcast.created.send", 0),
        "bc_sent": metrics.get("broadcast.sent", 0),
        "bc_failed": metrics.get("broadcast.failed", 0),
        "fm_check": metrics.get("fm.check", 0),
        "fm_blocked": metrics.get("fm.blocked", 0),
        "fm_passed": metrics.get("fm.passed", 0),
        "errors_total": sum(v for k, v in metrics.items() if k.startswith("errors.")),
    }
