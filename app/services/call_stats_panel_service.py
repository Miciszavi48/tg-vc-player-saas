"""Build formatted voice-call statistics reports for the selection panel and Id command."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

import jdatetime

from app.repositories import admin_repo, call_stats_settings_repo
from app.repositories import group_text_call_command_repo as stats_repo
from app.utils.i18n import t

Scope = Literal["all", "vip", "admin"]
Period = Literal["all", "week", "today"]

_TEHRAN = ZoneInfo("Asia/Tehran")
_WEEKDAY_FA = [
    "دوشنبه",
    "سه‌شنبه",
    "چهارشنبه",
    "پنجشنبه",
    "جمعه",
    "شنبه",
    "یکشنبه",
]
_MEDALS = ("🥇", "🥈", "🥉")
_REPORT_LIMIT = 10
_AGGREGATE_LIMIT = 500
_TELEGRAM_MESSAGE_MAX = 4096


def format_persian_duration(seconds: int, lang: str) -> str:
    """Format seconds as human-readable Persian/English duration text."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if lang == "fa":
        parts: list[str] = []
        if days:
            parts.append(f"{days} روز")
        if hours:
            parts.append(f"{hours} ساعت")
        if minutes:
            parts.append(f"{minutes} دقیقه")
        if secs or not parts:
            parts.append(f"{secs} ثانیه")
        return " ".join(parts)
    parts_en: list[str] = []
    if days:
        parts_en.append(f"{days}d")
    if hours:
        parts_en.append(f"{hours}h")
    if minutes:
        parts_en.append(f"{minutes}m")
    if secs or not parts_en:
        parts_en.append(f"{secs}s")
    return " ".join(parts_en)


def format_jalali_start(start: datetime) -> str:
    """Format period start as Jalali weekday and date."""
    local = start.astimezone(_TEHRAN)
    jdt = jdatetime.datetime.fromgregorian(datetime=local.replace(tzinfo=None))
    weekday = _WEEKDAY_FA[jdt.weekday()]
    return f"{weekday} , {jdt.year}/{jdt.month:02d}/{jdt.day:02d}"


async def _scope_user_ids(chat_id: int, scope: Scope) -> set[int] | None:
    if scope == "all":
        return None
    if scope == "vip":
        rows, _, _ = await admin_repo.get_player_vips_page(chat_id, 0, 1000)
        return {int(row.user_id) for row in rows}
    owners = await admin_repo.get_player_owners(chat_id)
    deputies = await admin_repo.get_player_deputies(chat_id)
    music = await admin_repo.get_music_admins(chat_id)
    video = await admin_repo.get_video_admins(chat_id)
    return {int(row.user_id) for row in (*owners, *deputies, *music, *video)}


async def period_bounds(
    chat_id: int,
    period: Period,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return UTC start/end for a report period.

    The window start is clamped forward to the chat's call-stats reset
    watermark (CALLSEC-02) so a completed daily/monthly rollover hides older
    aggregates without destroying the underlying CallReport rows.
    """
    current = now or datetime.now(timezone.utc)
    if period == "today":
        start, end = stats_repo.today_bounds_utc(current)[0], current
    elif period == "week":
        start, end = stats_repo.week_bounds_utc(current)
    else:
        start, end = await stats_repo.all_time_bounds_utc(chat_id, current)
    watermark = await call_stats_settings_repo.get_reset_watermark(chat_id)
    if watermark is not None and watermark > start:
        start = min(watermark, end)
    return start, end


async def fetch_today_user_stats(
    chat_id: int,
    user_id: int,
) -> tuple[int, int, int | None]:
    """Return user seconds, percent, and rank for today's call stats."""
    start, end = await period_bounds(chat_id, "today")
    user_stats = await stats_repo.get_user_call_stats_aggregate(
        chat_id,
        user_id,
        start=start,
        end=end,
    )
    if user_stats is None:
        return 0, 0, None
    user_seconds, _user_reports = user_stats
    total = await stats_repo.get_call_stats_period_total_seconds(
        chat_id,
        start=start,
        end=end,
    )
    rank = await stats_repo.get_call_stats_user_rank(
        chat_id,
        user_id,
        start=start,
        end=end,
    )
    percent = int(round((user_seconds / total) * 100)) if total > 0 else 0
    return user_seconds, percent, rank


async def build_panel_report(
    chat_id: int,
    *,
    scope: Scope,
    period: Period,
    lang: str,
    now: datetime | None = None,
) -> str:
    """Build a full call-stats report message for the panel."""
    allowed = await _scope_user_ids(chat_id, scope)
    start, end = await period_bounds(chat_id, period, now=now)
    full_total_seconds = await stats_repo.get_call_stats_period_total_seconds(
        chat_id,
        start=start,
        end=end,
        allowed_user_ids=allowed,
    )
    all_rows = await stats_repo.get_call_stats_scoped(
        chat_id,
        start=start,
        end=end,
        allowed_user_ids=allowed,
        limit=_AGGREGATE_LIMIT + 1,
    )
    title_key = {
        "all": "call_stats_panel.title_group",
        "vip": "call_stats_panel.title_vip",
        "admin": "call_stats_panel.title_admin",
    }[scope]
    period_key = {
        "all": "call_stats_panel.period_all",
        "week": "call_stats_panel.period_week",
        "today": "call_stats_panel.period_today",
    }[period]
    header = [
        t(lang, title_key),
        "",
        t(lang, "call_stats_panel.period_line", period=t(lang, period_key)),
        t(lang, "call_stats_panel.from_date", date=format_jalali_start(start)),
        t(lang, "call_stats_panel.from_time", time="00:00"),
        "",
    ]

    if not all_rows:
        return "\n".join([*header, t(lang, "call_stats_panel.empty")])

    aggregate_capped = len(all_rows) > _AGGREGATE_LIMIT
    if aggregate_capped:
        all_rows = all_rows[:_AGGREGATE_LIMIT]

    show_top_ten_note = len(all_rows) > _REPORT_LIMIT
    rows = all_rows[:_REPORT_LIMIT]
    total_all_seconds = full_total_seconds

    lines = [
        *header,
        t(
            lang,
            "call_stats_panel.total_duration",
            duration=format_persian_duration(total_all_seconds, lang),
        ),
        "",
    ]

    for index, row in enumerate(rows, start=1):
        medal = _MEDALS[index - 1] if index <= len(_MEDALS) else str(index)
        percent = 0
        if total_all_seconds > 0:
            percent = int(round((row.total_seconds / total_all_seconds) * 100))
        lines.append(
            t(
                lang,
                "call_stats_panel.user_line",
                medal=medal,
                user=row.display_name,
                duration=format_persian_duration(row.total_seconds, lang),
                percent=str(percent),
            )
        )
        if index < len(rows):
            lines.append(t(lang, "call_stats_panel.separator"))

    if show_top_ten_note:
        lines.extend(["", t(lang, "call_stats_panel.truncated_note")])
    if aggregate_capped:
        lines.extend(["", t(lang, "call_stats_panel.capped_note")])

    body = "\n".join(lines)
    if len(body) > _TELEGRAM_MESSAGE_MAX:
        body = body[: _TELEGRAM_MESSAGE_MAX - 1] + "…"
    return body
