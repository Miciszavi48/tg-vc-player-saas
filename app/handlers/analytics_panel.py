from __future__ import annotations

import logging
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.handlers.priority import PANEL_CALLBACK_GROUP
from app.services.analytics_service import get_report
from app.services.panel_message_service import panel_callback_edit
from app.utils.ask_result import safe_stop_listening
from app.utils.decorators import developer_only, sudo_or_above
from app.utils.filters import private_chat_filter, sudo_filter
from app.utils.callback_trace import mark_route_seen, trace_route
from app.utils.i18n import label, t
from app.utils.telegram_message import answer_callback_safe
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = "fa"
_TZ_LABEL = "Asia/Tehran"
_TZ_OFFSET_HOURS = 3.5


def _is_private(query: CallbackQuery) -> bool:
    chat = query.message.chat if query.message else None
    return chat is not None and chat.type.value == "private"


def _mark_analytics_route(query: CallbackQuery, handler_name: str) -> None:
    if getattr(query, "_musicbot_callback_route_seen", False):
        return
    mark_route_seen(query)
    trace_route(
        query,
        handler=handler_name,
        module=__name__,
        group=PANEL_CALLBACK_GROUP,
    )


async def _guard_private(query: CallbackQuery, bot_username: str) -> bool:
    """If not private chat, show warning + URL to open in PM. Returns True if blocked."""
    if _is_private(query):
        return False
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            t(_LANG, "admin.analytics.open_private_btn"),
            url=f"https://t.me/{bot_username}",
        )],
        [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=CB["NAV_BACK"])],
    ])
    await query.message.edit_text(t(_LANG, "admin.analytics.private_only"), reply_markup=kb)
    return True


def _render_summary(lang: str, data: dict) -> str:
    now_str = (
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} "
        f"{t(lang, 'labels.timezone.utc')}"
    )
    lines = [
        t(lang, "admin.analytics.report_header", type="", range=data.get("range", "")),
        t(lang, "admin.analytics.generated_at", timestamp=now_str),
        "",
        t(lang, "admin.analytics.total_events", count=f"{data.get('events_total', 0):,}"),
        t(lang, "admin.analytics.installs_summary",
          groups=data.get("install_groups", 0), channels=data.get("install_channels", 0)),
        t(lang, "admin.analytics.credits_summary",
          charged=data.get("credit_charge", 0), deducted=data.get("credit_deduct", 0)),
        t(lang, "admin.analytics.playback_summary",
          audio=data.get("play_audio", 0), video=data.get("play_video", 0)),
        t(lang, "admin.analytics.broadcast_summary",
          created=data.get("bc_created_send", 0), sent=data.get("bc_sent", 0)),
        t(lang, "admin.analytics.fm_summary",
          checks=data.get("fm_check", 0), blocked=data.get("fm_blocked", 0)),
        t(lang, "admin.analytics.errors_summary", count=data.get("errors_total", 0)),
    ]
    return "\n".join(lines)


def _render_drilldown(lang: str, title_key: str, data: dict, range_str: str) -> str:
    lines = [t(lang, title_key, range=range_str)]
    breakdown = data.get("breakdown", {})
    if not breakdown:
        lines.append(t(lang, "admin.analytics.no_data"))
    else:
        for key, value in breakdown.items():
            lines.append(t(lang, "admin.analytics.drill_row", key=key, value=f"{value:,}"))
    return "\n".join(lines)


def _render_peak(lang: str, data: dict) -> str:
    lines = [
        t(
            lang,
            "admin.analytics.report_header",
            type=label(lang, "analytics_type", "peak_hours"),
            range=data.get("range", ""),
        ),
        t(lang, "admin.analytics.peak_timezone_note", tz=_TZ_LABEL),
        "",
    ]
    peaks = data.get("peaks", [])
    if not peaks:
        lines.append(t(lang, "admin.analytics.no_data"))
    else:
        for i, p in enumerate(peaks, 1):
            utc_hour = p["hour"]
            local_hour = int((utc_hour + _TZ_OFFSET_HOURS) % 24)
            local_end = int((local_hour + 1) % 24)
            lines.append(t(lang, "admin.analytics.peak_hour_item",
                           rank=i, hour=local_hour, hour_end=local_end, count=f"{p['count']:,}"))
    return "\n".join(lines)


def _render_errors(lang: str, data: dict) -> str:
    lines = [
        t(
            lang,
            "admin.analytics.report_header",
            type=label(lang, "analytics_type", "errors"),
            range=data.get("range", ""),
        ),
    ]
    errors = data.get("errors", {})
    if not errors:
        lines.append(t(lang, "admin.analytics.no_data"))
    else:
        for key, value in errors.items():
            lines.append(t(lang, "admin.analytics.drill_row", key=key, value=f"{value:,}"))
    return "\n".join(lines)


async def render_analytics_home_panel(
    client: Client,
    query: CallbackQuery,
    *,
    answer: bool = True,
) -> bool:
    """Render analytics home via panel_callback_edit (dev shortcut + an:home)."""
    if query.message and query.message.chat and query.from_user:
        await safe_stop_listening(
            client,
            query.message.chat.id,
            user_id=query.from_user.id,
        )
    return await panel_callback_edit(
        client,
        query,
        t(_LANG, "admin.analytics.title"),
        KeyboardFactory.analytics_home(_LANG),
        answer=answer,
    )


async def _pm_analytics_edit(
    client: Client,
    query: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> bool:
    """Edit analytics panel message with shared panel_callback_edit path."""
    return await panel_callback_edit(
        client,
        query,
        text,
        reply_markup,
        answer=False,
    )


async def _safe_report(
    client: Client,
    query: CallbackQuery,
    report_type: str,
    **kwargs,
) -> dict | None:
    """Fetch a report; on failure show a truthful unavailable message instead of a no-op."""
    try:
        return await get_report(report_type, **kwargs)
    except Exception:
        logger.exception("Analytics report failed type=%s kwargs=%s", report_type, kwargs)
        await _pm_analytics_edit(
            client,
            query,
            t(_LANG, "admin.analytics.unavailable"),
            KeyboardFactory.analytics_drilldown_back(_LANG),
        )
        return None


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    _sudo = sudo_filter() & private_chat_filter()

    # ── Analytics Home ─────────────────────────────────────────────────────
    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_HOME']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_home(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_home")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        await render_analytics_home_panel(client, query, answer=False)

    # ── Yesterday ──────────────────────────────────────────────────────────
    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_YESTERDAY']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_yesterday(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_yesterday")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "yesterday")
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_summary(_LANG, data),
            KeyboardFactory.analytics_report(_LANG),
        )

    # ── Last N Days ────────────────────────────────────────────────────────
    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_7DAYS']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_7d(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_7d")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "range", days=7)
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_summary(_LANG, data),
            KeyboardFactory.analytics_report(_LANG),
        )

    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_14DAYS']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_14d(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_14d")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "range", days=14)
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_summary(_LANG, data),
            KeyboardFactory.analytics_report(_LANG),
        )

    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_30DAYS']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_30d(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_30d")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "range", days=30)
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_summary(_LANG, data),
            KeyboardFactory.analytics_report(_LANG),
        )

    # ── Peak Hours ─────────────────────────────────────────────────────────
    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_PEAK']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_peak(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_peak")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "peak")
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_peak(_LANG, data),
            KeyboardFactory.analytics_drilldown_back(_LANG),
        )

    # ── Errors / Health ────────────────────────────────────────────────────
    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_ERRORS']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_errors(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_errors")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "errors")
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_errors(_LANG, data),
            KeyboardFactory.analytics_drilldown_back(_LANG),
        )

    # ── Drilldowns (Back goes to AN_HOME) ──────────────────────────────────
    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_BY_FEATURE']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_by_feature(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_by_feature")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "drilldown", scope="feature")
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_drilldown(_LANG, "admin.analytics.drill_feature_title", data, data.get("range", "")),
            KeyboardFactory.analytics_drilldown_back(_LANG),
        )

    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_BY_CHAT_TYPE']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_by_chattype(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_by_chattype")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "drilldown", scope="chat_type")
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_drilldown(_LANG, "admin.analytics.drill_chattype_title", data, data.get("range", "")),
            KeyboardFactory.analytics_drilldown_back(_LANG),
        )

    @bot.on_callback_query(
        filters.regex(f"^{CB['AN_BY_ROLE']}$"), group=PANEL_CALLBACK_GROUP
    )
    @developer_only
    async def an_by_role(client: Client, query: CallbackQuery):
        _mark_analytics_route(query, "an_by_role")
        await answer_callback_safe(query)
        if await _guard_private(query, (await client.get_me()).username):
            return
        data = await _safe_report(client, query, "drilldown", scope="role")
        if data is None:
            return
        await _pm_analytics_edit(
            client,
            query,
            _render_drilldown(_LANG, "admin.analytics.drill_role_title", data, data.get("range", "")),
            KeyboardFactory.analytics_drilldown_back(_LANG),
        )

    # ── Sudo My Stats ──────────────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(f"^{CB['SUDO_MY_STATS']}$") & _sudo)
    @sudo_or_above
    async def sudo_my_stats(client: Client, query: CallbackQuery):
        await query.answer()
        if await _guard_private(query, (await client.get_me()).username):
            return
        user_id = query.from_user.id
        data = await _safe_report(
            client, query, "range", days=7, scope="sudo", scope_key=str(user_id)
        )
        if data is None:
            return
        installs = data.get("install_groups", 0) + data.get("install_channels", 0)
        credits = data.get("credit_charge", 0)
        lines = [
            t(_LANG, "admin.analytics.sudo_my_stats_title"),
            "",
            t(_LANG, "admin.analytics.sudo_installs_by_me", count=installs),
            t(_LANG, "admin.analytics.sudo_credits_by_me", count=credits),
        ]
        await _pm_analytics_edit(
            client,
            query,
            "\n".join(lines),
            KeyboardFactory.analytics_drilldown_back(_LANG),
        )
