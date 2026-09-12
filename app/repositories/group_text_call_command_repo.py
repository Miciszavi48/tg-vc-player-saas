"""Persistence helpers for slash-free group call text commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, select

from app.database.engine import async_session
from app.database.models import BotSetting, CallReport, GroupMemberMembership, PlaybackState, User

KEY_PREFIX = "group_text_call"
SETTING_AUTO_STATS = "auto_stats_enabled"
SETTING_CALL_MUTE = "call_mute_enabled"
SETTING_CALL_COMMENT = "call_comment_enabled"
SETTING_SCHEDULED_END = "scheduled_end"
SETTING_TITLE = "title"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "active"}
_TEHRAN = ZoneInfo("Asia/Tehran")


@dataclass(frozen=True)
class ScheduledEndConfig:
    chat_id: int
    minutes: int
    end_at: datetime
    requested_by: int | None


@dataclass(frozen=True)
class CallStatsRow:
    user_id: int
    display_name: str
    total_seconds: int
    report_count: int


def setting_key(chat_id: int, name: str) -> str:
    return f"{KEY_PREFIX}:{chat_id}:{name}"


async def _set_value(
    chat_id: int,
    name: str,
    value: str | None,
    *,
    updated_by: int | None = None,
) -> None:
    key = setting_key(chat_id, name)
    async with async_session() as session:
        result = await session.execute(select(BotSetting).where(BotSetting.key == key))
        row = result.scalar_one_or_none()
        if row is None:
            row = BotSetting(key=key, value=value, updated_by=updated_by)
            session.add(row)
        else:
            row.value = value
            if updated_by is not None:
                row.updated_by = updated_by
        await session.commit()


async def _get_value(chat_id: int, name: str) -> str | None:
    key = setting_key(chat_id, name)
    async with async_session() as session:
        result = await session.execute(select(BotSetting.value).where(BotSetting.key == key))
        return result.scalar_one_or_none()


async def set_bool_setting(
    chat_id: int,
    name: str,
    enabled: bool,
    *,
    updated_by: int | None = None,
) -> None:
    await _set_value(chat_id, name, "1" if enabled else "0", updated_by=updated_by)


async def get_bool_setting(chat_id: int, name: str, *, default: bool = False) -> bool:
    value = await _get_value(chat_id, name)
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE_VALUES


async def list_enabled_setting_chat_ids(name: str) -> list[int]:
    pattern = f"{KEY_PREFIX}:%:{name}"
    async with async_session() as session:
        result = await session.execute(
            select(BotSetting.key, BotSetting.value).where(BotSetting.key.like(pattern))
        )
        rows = result.all()
    chat_ids: list[int] = []
    for key, value in rows:
        if str(value).strip().lower() not in _TRUE_VALUES:
            continue
        try:
            chat_ids.append(int(str(key).split(":")[1]))
        except (IndexError, ValueError):
            continue
    return chat_ids


async def set_auto_stats_enabled(
    chat_id: int,
    enabled: bool,
    *,
    updated_by: int | None = None,
) -> None:
    await set_bool_setting(chat_id, SETTING_AUTO_STATS, enabled, updated_by=updated_by)


async def is_auto_stats_enabled(chat_id: int) -> bool:
    return await get_bool_setting(chat_id, SETTING_AUTO_STATS)


async def list_auto_stats_enabled_chat_ids() -> list[int]:
    return await list_enabled_setting_chat_ids(SETTING_AUTO_STATS)


async def set_call_mute_enabled(
    chat_id: int,
    enabled: bool,
    *,
    updated_by: int | None = None,
) -> None:
    await set_bool_setting(chat_id, SETTING_CALL_MUTE, enabled, updated_by=updated_by)


async def is_call_mute_enabled(chat_id: int) -> bool:
    return await get_bool_setting(chat_id, SETTING_CALL_MUTE)


async def set_call_comment_enabled(
    chat_id: int,
    enabled: bool,
    *,
    updated_by: int | None = None,
) -> None:
    await set_bool_setting(chat_id, SETTING_CALL_COMMENT, enabled, updated_by=updated_by)


async def is_call_comment_enabled(chat_id: int) -> bool:
    return await get_bool_setting(chat_id, SETTING_CALL_COMMENT)


async def set_call_title(
    chat_id: int,
    title: str,
    *,
    updated_by: int | None = None,
) -> None:
    await _set_value(chat_id, SETTING_TITLE, title, updated_by=updated_by)


async def get_call_title(chat_id: int) -> str | None:
    value = await _get_value(chat_id, SETTING_TITLE)
    if value is None:
        return None
    value = value.strip()
    return value or None


async def set_scheduled_end(
    chat_id: int,
    minutes: int,
    end_at: datetime,
    *,
    requested_by: int | None = None,
) -> ScheduledEndConfig:
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)
    payload = {
        "chat_id": int(chat_id),
        "minutes": int(minutes),
        "end_at": end_at.astimezone(timezone.utc).isoformat(),
        "requested_by": requested_by,
    }
    await _set_value(
        chat_id,
        SETTING_SCHEDULED_END,
        json.dumps(payload, separators=(",", ":")),
        updated_by=requested_by,
    )
    return ScheduledEndConfig(chat_id, int(minutes), end_at.astimezone(timezone.utc), requested_by)


def _scheduled_from_value(chat_id: int, raw: str | None) -> ScheduledEndConfig | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        end_at = datetime.fromisoformat(str(data["end_at"]))
        if end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=timezone.utc)
        return ScheduledEndConfig(
            chat_id=int(data.get("chat_id", chat_id)),
            minutes=int(data["minutes"]),
            end_at=end_at.astimezone(timezone.utc),
            requested_by=(
                int(data["requested_by"]) if data.get("requested_by") is not None else None
            ),
        )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


async def get_scheduled_end(chat_id: int) -> ScheduledEndConfig | None:
    return _scheduled_from_value(
        chat_id,
        await _get_value(chat_id, SETTING_SCHEDULED_END),
    )


async def clear_scheduled_end(chat_id: int) -> None:
    await _set_value(chat_id, SETTING_SCHEDULED_END, None)


async def list_pending_scheduled_ends(
    *,
    now: datetime | None = None,
) -> list[ScheduledEndConfig]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    pattern = f"{KEY_PREFIX}:%:{SETTING_SCHEDULED_END}"
    async with async_session() as session:
        result = await session.execute(
            select(BotSetting.key, BotSetting.value).where(BotSetting.key.like(pattern))
        )
        rows = result.all()
    configs: list[ScheduledEndConfig] = []
    for key, value in rows:
        try:
            chat_id = int(str(key).split(":")[1])
        except (IndexError, ValueError):
            continue
        cfg = _scheduled_from_value(chat_id, value)
        if cfg is not None and cfg.end_at >= now:
            configs.append(cfg)
    return configs


async def get_playback_state(chat_id: int) -> PlaybackState | None:
    async with async_session() as session:
        result = await session.execute(
            select(PlaybackState).where(PlaybackState.chat_id == chat_id)
        )
        return result.scalar_one_or_none()


async def has_persisted_playback_state(chat_id: int) -> bool:
    return await get_playback_state(chat_id) is not None


def _weekday_target(day: str | None, now: datetime | None = None) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(_TEHRAN)
    if day is None:
        target_date = current.date()
    else:
        target_index = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }[day]
        delta_days = (current.weekday() - target_index) % 7
        target_date = current.date() - timedelta(days=delta_days)
    return datetime.combine(target_date, time.min, tzinfo=_TEHRAN)


def day_bounds_utc(day: str | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    start = _weekday_target(day, now).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


def today_bounds_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return UTC bounds for today at 00:00 Asia/Tehran."""
    return day_bounds_utc(None, now)


def week_bounds_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return UTC bounds from Saturday 00:00 Asia/Tehran through now."""
    current = (now or datetime.now(timezone.utc)).astimezone(_TEHRAN)
    start_local = _weekday_target("saturday", now)
    end_local = current
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


async def all_time_bounds_utc(
    chat_id: int,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return UTC bounds from earliest call report for chat through now."""
    current = (now or datetime.now(timezone.utc)).astimezone(_TEHRAN)
    end = current.astimezone(timezone.utc)
    async with async_session() as session:
        result = await session.execute(
            select(func.min(CallReport.started_at)).where(CallReport.chat_id == chat_id)
        )
        earliest = result.scalar_one_or_none()
    if earliest is None:
        start, _ = today_bounds_utc(now)
        return start, end
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    return earliest, end


async def get_call_stats_scoped(
    chat_id: int,
    *,
    start: datetime,
    end: datetime,
    allowed_user_ids: set[int] | None = None,
    limit: int = 10,
) -> list[CallStatsRow]:
    """Return aggregated call stats optionally filtered to allowed user ids."""
    return await get_call_stats_between(
        chat_id,
        start=start,
        end=end,
        limit=limit,
        allowed_user_ids=allowed_user_ids,
    )


async def get_call_stats(
    chat_id: int,
    *,
    day: str | None = None,
    now: datetime | None = None,
    limit: int = 30,
) -> list[CallStatsRow]:
    start, end = day_bounds_utc(day, now)
    return await get_call_stats_between(chat_id, start=start, end=end, limit=limit)


async def get_call_stats_between(
    chat_id: int,
    *,
    start: datetime,
    end: datetime,
    limit: int = 30,
    allowed_user_ids: set[int] | None = None,
) -> list[CallStatsRow]:
    if allowed_user_ids is not None and not allowed_user_ids:
        return []

    total_seconds = func.coalesce(func.sum(CallReport.duration_seconds), 0).label(
        "total_seconds"
    )
    report_count = func.count(CallReport.id).label("report_count")
    async with async_session() as session:
        stmt = (
            select(
                CallReport.played_by,
                User.first_name,
                User.username,
                total_seconds,
                report_count,
            )
            .outerjoin(User, User.user_id == CallReport.played_by)
            .where(
                CallReport.chat_id == chat_id,
                CallReport.played_by.is_not(None),
                CallReport.started_at >= start,
                CallReport.started_at < end,
            )
        )
        if allowed_user_ids is not None:
            stmt = stmt.where(CallReport.played_by.in_(allowed_user_ids))
        stmt = (
            stmt.group_by(CallReport.played_by, User.first_name, User.username)
            .order_by(desc(total_seconds), desc(report_count), CallReport.played_by.asc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()

    out: list[CallStatsRow] = []
    for user_id, first_name, username, seconds, count in rows:
        if user_id is None:
            continue
        display = first_name or (f"@{username}" if username else str(user_id))
        out.append(
            CallStatsRow(
                user_id=int(user_id),
                display_name=display,
                total_seconds=int(seconds or 0),
                report_count=int(count or 0),
            )
        )
    return out


async def get_user_call_stats_aggregate(
    chat_id: int,
    user_id: int,
    *,
    start: datetime,
    end: datetime,
) -> tuple[int, int] | None:
    """Return total seconds and report count for one user in a period."""
    total_seconds = func.coalesce(func.sum(CallReport.duration_seconds), 0).label(
        "total_seconds"
    )
    report_count = func.count(CallReport.id).label("report_count")
    async with async_session() as session:
        row = (
            await session.execute(
                select(total_seconds, report_count).where(
                    CallReport.chat_id == chat_id,
                    CallReport.played_by == user_id,
                    CallReport.started_at >= start,
                    CallReport.started_at < end,
                )
            )
        ).one_or_none()
    if row is None:
        return None
    seconds = int(row.total_seconds or 0)
    count = int(row.report_count or 0)
    if seconds == 0:
        return None
    return seconds, count


async def get_call_stats_period_total_seconds(
    chat_id: int,
    *,
    start: datetime,
    end: datetime,
    allowed_user_ids: set[int] | None = None,
) -> int:
    """Return summed duration for all users in a chat period."""
    if allowed_user_ids is not None and not allowed_user_ids:
        return 0
    async with async_session() as session:
        stmt = select(func.coalesce(func.sum(CallReport.duration_seconds), 0)).where(
            CallReport.chat_id == chat_id,
            CallReport.played_by.is_not(None),
            CallReport.started_at >= start,
            CallReport.started_at < end,
        )
        if allowed_user_ids is not None:
            stmt = stmt.where(CallReport.played_by.in_(allowed_user_ids))
        total = (await session.execute(stmt)).scalar_one()
    return int(total or 0)


async def get_call_stats_user_rank(
    chat_id: int,
    user_id: int,
    *,
    start: datetime,
    end: datetime,
    allowed_user_ids: set[int] | None = None,
) -> int | None:
    """Return 1-based rank for one user using panel sort order."""
    from sqlalchemy import and_, or_

    user_stats = await get_user_call_stats_aggregate(
        chat_id,
        user_id,
        start=start,
        end=end,
    )
    if user_stats is None:
        return None
    user_seconds, user_reports = user_stats

    total_seconds = func.coalesce(func.sum(CallReport.duration_seconds), 0).label(
        "total_seconds"
    )
    report_count = func.count(CallReport.id).label("report_count")
    async with async_session() as session:
        agg = (
            select(
                CallReport.played_by.label("user_id"),
                total_seconds,
                report_count,
            )
            .where(
                CallReport.chat_id == chat_id,
                CallReport.played_by.is_not(None),
                CallReport.started_at >= start,
                CallReport.started_at < end,
            )
            .group_by(CallReport.played_by)
        )
        if allowed_user_ids is not None:
            agg = agg.where(CallReport.played_by.in_(allowed_user_ids))
        ranked = agg.subquery()
        ahead = (
            select(func.count())
            .select_from(ranked)
            .where(
                ranked.c.user_id != user_id,
                or_(
                    ranked.c.total_seconds > user_seconds,
                    and_(
                        ranked.c.total_seconds == user_seconds,
                        ranked.c.report_count > user_reports,
                    ),
                    and_(
                        ranked.c.total_seconds == user_seconds,
                        ranked.c.report_count == user_reports,
                        ranked.c.user_id < user_id,
                    ),
                ),
            )
        )
        higher = int((await session.execute(ahead)).scalar_one() or 0)
    return higher + 1


async def get_recent_user_ids(chat_id: int, *, limit: int = 30) -> list[int]:
    async with async_session() as session:
        result = await session.execute(
            select(GroupMemberMembership.user_id)
            .where(
                GroupMemberMembership.chat_id == chat_id,
                GroupMemberMembership.left_at.is_(None),
            )
            .order_by(GroupMemberMembership.last_seen_at.desc())
            .limit(limit)
        )
        rows = [int(value) for value in result.scalars().all()]
    return rows
