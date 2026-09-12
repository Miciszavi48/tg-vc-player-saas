"""Tests for call-stats selection panel, settings toggles, and Id sync."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import json
from pathlib import Path

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.group_text_commands import GroupTextCommandType, parse_group_text_command
from app.utils.i18n import AUTO_LANG, t
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _message(text: str, *, chat_id: int = -8001, user_id: int = 42):
    return SimpleNamespace(
        text=text,
        caption=None,
        chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
        from_user=SimpleNamespace(id=user_id, first_name="Tester", username="tester"),
        reply=AsyncMock(),
        reply_text=AsyncMock(),
        continue_propagation=lambda: None,
    )


def _query(data: str, *, chat_id: int = -8001, user_id: int = 42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
            delete=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def _call_handler():
    from app.handlers import group_text_call_commands

    bot = _RecorderBot()
    group_text_call_commands.register(bot, MagicMock())
    return bot.message_handlers[0]


def _panel_handlers():
    from app.handlers import call_stats_panel

    bot = _RecorderBot()
    call_stats_panel.register(bot, MagicMock())
    return {
        "select": next(fn for fn in bot.callback_handlers if fn.__name__ == "call_stats_select"),
        "close": next(fn for fn in bot.callback_handlers if fn.__name__ == "call_stats_close"),
    }


def _group_panel():
    from app.handlers import group_panel

    bot = _RecorderBot()
    group_panel.register(bot, MagicMock())
    return bot


@pytest.mark.parametrize(
    "raw",
    [
        "آمار کال",
        "امار کال",
        "Call Stats",
        "Voice Call Stats",
        "CallStatis",
    ],
)
def test_parser_accepts_exact_panel_aliases(raw: str):
    parsed = parse_group_text_command(raw)
    assert parsed is not None
    assert parsed.command == GroupTextCommandType.CALL_STATS_PANEL
    assert parsed.error is None


@pytest.mark.parametrize(
    "raw",
    [
        "آمار کال شنبه",
        "آمار کال امروز",
        "آمار کال هفتگی",
        "Call Stats today",
        "Voice Call Stats all",
        "CallStatis Saturday",
        "CallStatis now",
    ],
)
def test_parser_rejects_day_suffix_stats_commands(raw: str):
    assert parse_group_text_command(raw) is None


@pytest.mark.asyncio
async def test_stats_panel_opens_when_enabled_and_authorized():
    handler = _call_handler()
    message = _message("آمار کال")
    with (
        patch(
            "app.handlers.group_text_call_commands.group_runtime_state_service.require_active_group",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_text_call_commands._can_manage_call_command",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_text_call_commands.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
    ):
        await handler(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert t("fa", "call_stats_panel.menu_title") in message.reply.await_args.args[0]
    markup = message.reply.await_args.kwargs.get("reply_markup")
    assert markup is not None
    assert len(markup.inline_keyboard) == 7


@pytest.mark.asyncio
async def test_stats_panel_disabled_message():
    handler = _call_handler()
    message = _message("CallStatis")
    with (
        patch(
            "app.handlers.group_text_call_commands.group_runtime_state_service.require_active_group",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_text_call_commands._can_manage_call_command",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_text_call_commands.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=False),
        ),
    ):
        await handler(AsyncMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "call_stats_panel.feature_disabled"))


@pytest.mark.asyncio
async def test_stats_panel_requires_manage_permission():
    handler = _call_handler()
    message = _message("آمار کال", user_id=90003)
    with (
        patch(
            "app.handlers.group_text_call_commands.group_runtime_state_service.require_active_group",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_text_call_commands._can_manage_call_command",
            AsyncMock(return_value=False),
        ),
    ):
        await handler(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert "دسترسی" in message.reply.await_args.args[0]


def test_selection_menu_has_nine_buttons_plus_close():
    kb = KeyboardFactory.call_stats_selection_menu("fa", -8001, 42)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 10
    for btn in buttons:
        assert len(btn.callback_data) <= 64
        assert str(-8001) in btn.callback_data
        assert btn.callback_data.endswith(":42")


def test_group_settings_keyboard_exposes_call_stats_toggles():
    kb = KeyboardFactory.group_settings(
        "fa",
        {
            "call_stats": True,
            "id_call_stats": False,
            "language": "fa",
            "default_media_type": "audio",
        },
    )
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    callback_data = {btn.callback_data for btn in buttons}

    assert CB["GRP_CALL_STATS"] in callback_data
    assert CB["GRP_ID_CALL_STATS"] in callback_data
    assert any(t("fa", "panels.group.settings.call_stats") in btn.text for btn in buttons)
    assert any(t("fa", "panels.group.settings.id_call_stats") in btn.text for btn in buttons)


@pytest.mark.asyncio
async def test_select_callback_rejects_wrong_user():
    handlers = _panel_handlers()
    data = "grp:callstats:sel:all:today:-8001:42"
    query = _query(data, user_id=99)
    with patch(
        "app.handlers.call_stats_panel.can_manage_call_command_user",
        AsyncMock(return_value=True),
    ):
        await handlers["select"](AsyncMock(), query)
    query.message.edit_text.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_select_callback_builds_report():
    handlers = _panel_handlers()
    data = "grp:callstats:sel:all:today:-8001:42"
    query = _query(data)
    with (
        patch(
            "app.handlers.call_stats_panel.can_manage_call_command_user",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_stats_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_stats_panel.panel_svc.build_panel_report",
            AsyncMock(return_value="report body"),
        ) as build_report,
    ):
        await handlers["select"](AsyncMock(), query)

    build_report.assert_awaited_once_with(-8001, scope="all", period="today", lang=AUTO_LANG)
    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == "report body"
    assert query.message.edit_text.await_args.kwargs["reply_markup"] is not None


@pytest.mark.asyncio
async def test_close_callback_deletes_message():
    handlers = _panel_handlers()
    data = "grp:callstats:close:-8001:42"
    query = _query(data)
    with (
        patch(
            "app.handlers.call_stats_panel.can_manage_call_command_user",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_stats_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
    ):
        await handlers["close"](AsyncMock(), query)
    query.message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_call_stats_toggle_flips_kv():
    bot = _group_panel()
    handler = next(fn for fn in bot.callback_handlers if fn.__name__ == "grp_toggle_call_stats")
    query = _query(CB["GRP_CALL_STATS"])
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.set_enabled",
            AsyncMock(),
        ) as set_enabled,
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value={"call_stats": False, "id_call_stats": True}),
        ),
        patch(
            "app.handlers.group_panel._build_group_settings_summary",
            AsyncMock(return_value="summary"),
        ),
    ):
        await handler(AsyncMock(), query)

    set_enabled.assert_awaited_once_with(query.message.chat.id, False, updated_by=query.from_user.id)


@pytest.mark.asyncio
async def test_main_call_stats_toggle_requires_permission():
    bot = _group_panel()
    handler = next(fn for fn in bot.callback_handlers if fn.__name__ == "grp_toggle_call_stats")
    query = _query(CB["GRP_CALL_STATS"])
    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=False)),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.set_enabled",
            AsyncMock(),
        ) as set_enabled,
    ):
        await handler(AsyncMock(), query)

    set_enabled.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_id_uses_effective_call_stats_toggle():
    bot = _group_panel()
    handler = next(fn for fn in bot.message_handlers if fn.__name__ == "user_id_command")
    message = _message("Id")
    client = AsyncMock()
    with (
        patch(
            "app.handlers.group_panel.id_command_settings_repo.get_output_mode",
            AsyncMock(return_value="simple"),
        ),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.is_effective_id_call_stats",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.handlers.group_panel.user_info_formatter_service.reply_user_info",
            AsyncMock(),
        ) as reply_info,
    ):
        await handler(client, message)

    reply_info.assert_awaited_once_with(
        client,
        message,
        output_mode="simple",
        include_call_stats=False,
        lang=AUTO_LANG,
    )


@pytest.mark.asyncio
async def test_settings_summary_shows_effective_id_off_when_main_disabled():
    from app.handlers.group_panel import _build_group_settings_summary

    sd = {
        "security_call": True,
        "download_users": True,
        "auto_clean": True,
        "call_message": True,
        "auto_ready_call": True,
        "music_video": True,
        "call_report": True,
        "queue": True,
        "show_id": True,
        "show_photo": True,
        "show_text": True,
        "call_stats": False,
        "id_call_stats": True,
    }
    with patch(
        "app.handlers.group_panel.call_security_repo.get_call_security_settings",
        AsyncMock(return_value=SimpleNamespace(enabled=False)),
    ):
        summary = await _build_group_settings_summary(sd, -7001)
    assert t("fa", "panels.group.settings.id_call_stats_off_reason") in summary


@pytest.mark.asyncio
async def test_build_panel_report_formats_medals_and_percent():
    from app.services.call_stats_panel_service import build_panel_report

    row = SimpleNamespace(user_id=1, display_name="Ali", total_seconds=90)
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=90),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            AsyncMock(return_value=[row]),
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="all", period="today", lang="fa")

    assert "🥇" in report
    assert "Ali" in report
    assert "100%" in report


@pytest.mark.asyncio
async def test_is_effective_id_call_stats_requires_main_toggle():
    from app.repositories import call_stats_settings_repo

    with (
        patch(
            "app.repositories.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.repositories.id_command_settings_repo.get_show_call_stats",
            AsyncMock(return_value=True),
        ),
    ):
        assert await call_stats_settings_repo.is_effective_id_call_stats(-1) is False


@pytest.mark.parametrize(
    "main_enabled,id_enabled,expected",
    [
        (True, True, True),
        (True, False, False),
        (False, True, False),
        (False, False, False),
    ],
)
@pytest.mark.asyncio
async def test_is_effective_id_call_stats_all_combinations(
    main_enabled: bool,
    id_enabled: bool,
    expected: bool,
):
    from app.repositories import call_stats_settings_repo

    with (
        patch(
            "app.repositories.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=main_enabled),
        ),
        patch(
            "app.repositories.id_command_settings_repo.get_show_call_stats",
            AsyncMock(return_value=id_enabled),
        ),
    ):
        assert await call_stats_settings_repo.is_effective_id_call_stats(-1) is expected


@pytest.mark.asyncio
async def test_select_callback_rejects_forged_chat_id():
    handlers = _panel_handlers()
    data = "grp:callstats:sel:all:today:-9999:42"
    query = _query(data, chat_id=-8001, user_id=42)
    with patch(
        "app.handlers.call_stats_panel.can_manage_call_command_user",
        AsyncMock(return_value=True),
    ):
        await handlers["select"](AsyncMock(), query)
    query.message.edit_text.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_select_callback_rejects_when_feature_disabled():
    handlers = _panel_handlers()
    data = "grp:callstats:sel:all:today:-8001:42"
    query = _query(data)
    with (
        patch(
            "app.handlers.call_stats_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.handlers.call_stats_panel.panel_svc.build_panel_report",
            AsyncMock(),
        ) as build_report,
    ):
        await handlers["select"](AsyncMock(), query)
    build_report.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_select_callback_rejects_without_manage_permission():
    handlers = _panel_handlers()
    data = "grp:callstats:sel:all:today:-8001:42"
    query = _query(data)
    with (
        patch(
            "app.handlers.call_stats_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_stats_panel.can_manage_call_command_user",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.handlers.call_stats_panel.panel_svc.build_panel_report",
            AsyncMock(),
        ) as build_report,
    ):
        await handlers["select"](AsyncMock(), query)
    build_report.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()
    query.answer.assert_awaited()


def test_invalid_period_callback_does_not_match_select_regex():
    import re

    from app.handlers.call_stats_panel import _SEL_RE

    assert _SEL_RE.match("grp:callstats:sel:all:bad:-8001:42") is None


def test_callback_data_length_for_large_ids():
    chat_id = -1001234567890
    user_id = 9999999999
    kb = KeyboardFactory.call_stats_selection_menu("fa", chat_id, user_id)
    for btn in (btn for row in kb.inline_keyboard for btn in row):
        assert len(btn.callback_data) <= 64


@pytest.mark.asyncio
async def test_build_panel_report_empty_state():
    from app.services.call_stats_panel_service import build_panel_report

    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=0),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="all", period="week", lang="fa")
    assert t("fa", "call_stats_panel.empty") in report
    assert t("fa", "call_stats_panel.title_group") in report
    assert t("fa", "call_stats_panel.period_week") in report


@pytest.mark.asyncio
async def test_build_panel_report_sorts_and_truncates():
    from app.services.call_stats_panel_service import build_panel_report

    rows = [
        SimpleNamespace(user_id=i, display_name=f"User{i}", total_seconds=100 - i)
        for i in range(12)
    ]
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=sum(row.total_seconds for row in rows)),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            AsyncMock(return_value=rows),
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="all", period="today", lang="fa")

    assert "User0" in report
    assert "User9" in report
    assert "User11" not in report
    assert t("fa", "call_stats_panel.truncated_note") in report
    assert "4." in report or "4 " in report


@pytest.mark.asyncio
async def test_build_panel_report_zero_total_is_safe():
    from app.services.call_stats_panel_service import build_panel_report

    row = SimpleNamespace(user_id=1, display_name="Ali", total_seconds=0)
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=0),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            AsyncMock(return_value=[row]),
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="vip", period="today", lang="fa")
    assert "0%" in report


@pytest.mark.asyncio
async def test_vip_scope_passes_allowed_user_ids():
    from app.services.call_stats_panel_service import build_panel_report

    vip_ids = {101, 102}
    scoped = AsyncMock(return_value=[])
    total_seconds = AsyncMock(return_value=0)
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=vip_ids),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            total_seconds,
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            scoped,
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        await build_panel_report(-1, scope="vip", period="all", lang="fa")
    scoped.assert_awaited_once()
    assert scoped.await_args.kwargs["allowed_user_ids"] == vip_ids
    total_seconds.assert_awaited_once()
    assert total_seconds.await_args.kwargs["allowed_user_ids"] == vip_ids


@pytest.mark.asyncio
async def test_empty_vip_scope_returns_empty_without_query():
    from app.services.call_stats_panel_service import build_panel_report

    scoped = AsyncMock(return_value=[])
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=set()),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=0),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            scoped,
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="vip", period="today", lang="fa")
    scoped.assert_awaited_once()
    assert t("fa", "call_stats_panel.empty") in report
    assert t("fa", "call_stats_panel.title_vip") in report
    assert t("fa", "call_stats_panel.period_today") in report


def test_week_bounds_start_saturday_tehran():
    from datetime import datetime, timezone

    from app.repositories.group_text_call_command_repo import week_bounds_utc

    # Wednesday 2024-01-10 12:00 UTC = Wed evening Tehran; week should start prior Sat 00:00 Tehran
    now = datetime(2024, 1, 10, 12, 0, tzinfo=timezone.utc)
    start, end = week_bounds_utc(now)
    assert start < end
    assert (end - start).total_seconds() >= 0


def test_today_bounds_start_at_tehran_midnight():
    from datetime import datetime, timezone

    from app.repositories.group_text_call_command_repo import today_bounds_utc

    now = datetime(2024, 6, 15, 20, 30, tzinfo=timezone.utc)
    start, end = today_bounds_utc(now)
    assert start.tzinfo is not None
    assert end - start == __import__("datetime").timedelta(days=1)


@pytest.mark.asyncio
async def test_id_stats_block_skips_extra_query_when_user_has_activity():
    from app.services import user_info_formatter_service

    with (
        patch(
            "app.services.call_stats_panel_service.fetch_today_user_stats",
            AsyncMock(return_value=(120, 40, 2)),
        ),
        patch(
            "app.repositories.group_text_call_command_repo.get_call_stats_scoped",
            AsyncMock(),
        ) as scoped,
    ):
        block = await user_info_formatter_service.build_call_stats_block(-1, 42, "fa")
    scoped.assert_not_awaited()
    assert block is not None
    assert "00:02:00" in block


@pytest.mark.asyncio
async def test_build_panel_report_exactly_ten_users_no_truncation_note():
    from app.services.call_stats_panel_service import build_panel_report

    rows = [
        SimpleNamespace(user_id=i, display_name=f"User{i}", total_seconds=100 - i)
        for i in range(10)
    ]
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=sum(row.total_seconds for row in rows)),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            AsyncMock(return_value=rows),
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="all", period="today", lang="fa")

    assert t("fa", "call_stats_panel.truncated_note") not in report
    assert t("fa", "call_stats_panel.capped_note") not in report


@pytest.mark.asyncio
async def test_build_panel_report_aggregate_cap_note():
    from app.services.call_stats_panel_service import build_panel_report

    rows = [
        SimpleNamespace(user_id=i, display_name=f"User{i}", total_seconds=1)
        for i in range(501)
    ]
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=5010),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            AsyncMock(return_value=rows),
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="all", period="today", lang="fa")

    assert t("fa", "call_stats_panel.truncated_note") in report
    assert t("fa", "call_stats_panel.capped_note") in report
    assert "User10" not in report
    assert "1 ساعت 23 دقیقه 30 ثانیه" in report


@pytest.mark.asyncio
async def test_build_panel_report_percentages_use_full_scope_total():
    from app.services.call_stats_panel_service import build_panel_report

    rows = [
        SimpleNamespace(user_id=0, display_name="Leader", total_seconds=100),
        *[
            SimpleNamespace(user_id=i, display_name=f"User{i}", total_seconds=1)
            for i in range(1, 501)
        ],
    ]
    with (
        patch(
            "app.services.call_stats_panel_service._scope_user_ids",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=10_000),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_scoped",
            AsyncMock(return_value=rows),
        ),
        patch(
            "app.services.call_stats_panel_service.format_jalali_start",
            return_value="شنبه , 1404/01/01",
        ),
    ):
        report = await build_panel_report(-1, scope="all", period="today", lang="fa")

    assert "Leader" in report
    assert "1%" in report
    assert "100%" not in report
    assert t("fa", "call_stats_panel.truncated_note") in report
    assert t("fa", "call_stats_panel.capped_note") in report


@pytest.mark.asyncio
async def test_get_call_stats_period_total_seconds_db_scoped_to_chat_and_vip():
    from datetime import datetime, timezone

    from app.database.engine import async_session
    from app.database.models import CallReport
    from app.repositories import group_text_call_command_repo as repo

    chat_a = -10099011
    chat_b = -10099012
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(day=start.day + 1) if start.day < 28 else start.replace(
        month=start.month + 1, day=1
    )

    async with async_session() as session:
        session.add_all(
            [
                CallReport(
                    chat_id=chat_a,
                    chat_title="A",
                    title="t",
                    media_type="audio",
                    played_by=601,
                    duration_seconds=100,
                    started_at=now,
                ),
                CallReport(
                    chat_id=chat_a,
                    chat_title="A",
                    title="t",
                    media_type="audio",
                    played_by=602,
                    duration_seconds=50,
                    started_at=now,
                ),
                CallReport(
                    chat_id=chat_b,
                    chat_title="B",
                    title="t",
                    media_type="audio",
                    played_by=601,
                    duration_seconds=999,
                    started_at=now,
                ),
            ]
        )
        await session.commit()

    total_all = await repo.get_call_stats_period_total_seconds(
        chat_a,
        start=start,
        end=end,
    )
    total_vip = await repo.get_call_stats_period_total_seconds(
        chat_a,
        start=start,
        end=end,
        allowed_user_ids={602},
    )
    assert total_all == 150
    assert total_vip == 50


@pytest.mark.asyncio
async def test_fetch_today_user_stats_uses_exact_rank_query():
    from app.services.call_stats_panel_service import fetch_today_user_stats

    with (
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_user_call_stats_aggregate",
            AsyncMock(return_value=(45, 2)),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=150),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_user_rank",
            AsyncMock(return_value=12),
        ),
    ):
        seconds, percent, rank = await fetch_today_user_stats(-1, 42)

    assert seconds == 45
    assert percent == 30
    assert rank == 12


@pytest.mark.asyncio
async def test_id_stats_match_panel_percent_for_same_fixture():
    from app.services import call_stats_panel_service, user_info_formatter_service

    with (
        patch(
            "app.services.call_stats_panel_service.period_bounds",
            AsyncMock(return_value=(SimpleNamespace(), SimpleNamespace())),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_user_call_stats_aggregate",
            AsyncMock(return_value=(30, 1)),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_period_total_seconds",
            AsyncMock(return_value=100),
        ),
        patch(
            "app.services.call_stats_panel_service.stats_repo.get_call_stats_user_rank",
            AsyncMock(return_value=2),
        ),
    ):
        user_seconds, percent, rank = await call_stats_panel_service.fetch_today_user_stats(
            -1, 42
        )
        block = await user_info_formatter_service.build_call_stats_block(-1, 42, "fa")
    assert user_seconds == 30
    assert percent == 30
    assert rank == 2
    assert "30" in block
    assert "00:00:30" in block
    assert "2" in block


def test_call_stats_panel_registered_in_handler_chain():
    import app.handlers as handlers_pkg

    assert handlers_pkg.call_stats_panel in handlers_pkg._MODULES


def test_call_stats_resources_are_registered_and_resolve():
    manifest = json.loads(Path("app/resources/i18n/manifest.json").read_text(encoding="utf-8"))
    fragments = {fragment["path"]: set(fragment["namespaces"]) for fragment in manifest["fragments"]}
    assert "call_stats_panel" in fragments["features/call_stats_panel.json"]
    assert "public_cmd" in fragments["features/public_cmd.json"]

    keys = [
        "call_stats_panel.menu_title",
        "call_stats_panel.feature_disabled",
        "call_stats_panel.invalid_callback",
        "panels.group.settings.call_stats",
        "panels.group.settings.call_stats_summary_label",
        "panels.group.settings.id_call_stats",
        "panels.group.settings.id_call_stats_summary_label",
        "panels.group.settings.id_call_stats_off_reason",
        "public_cmd.user_info_name",
        "public_cmd.user_info_stats_duration",
        "public_cmd.user_info_stats_percent",
        "public_cmd.user_info_stats_rank",
        "public_cmd.player_status_active",
    ]
    for lang in ("fa", "en"):
        for key in keys:
            value = t(lang, key, chat_id="-1", name="Ali", duration="00:01:00", percent="25", rank="#1", state="on", title="Song", media_type="audio", speed="1.0", volume="100", queue="0")
            assert "[missing:" not in value
            assert "[invalid:" not in value


def test_call_stats_callback_patterns_are_runtime_registered():
    import app.handlers as handlers_pkg
    from app.handlers.call_stats_panel import _CLOSE_RE, _SEL_RE

    assert handlers_pkg.call_stats_panel in handlers_pkg._MODULES
    assert _SEL_RE.match("grp:callstats:sel:all:today:-8001:42") is not None
    assert _CLOSE_RE.match("grp:callstats:close:-8001:42") is not None


@pytest.mark.asyncio
async def test_get_call_stats_user_rank_db_scoped_to_chat():
    from datetime import datetime, timezone

    from app.database.engine import async_session
    from app.database.models import CallReport
    from app.repositories import group_text_call_command_repo as repo

    chat_a = -10099001
    chat_b = -10099002
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(day=start.day + 1) if start.day < 28 else start.replace(month=start.month + 1, day=1)

    async with async_session() as session:
        session.add_all(
            [
                CallReport(
                    chat_id=chat_a,
                    chat_title="A",
                    title="t",
                    media_type="audio",
                    played_by=501,
                    duration_seconds=100,
                    started_at=now,
                ),
                CallReport(
                    chat_id=chat_a,
                    chat_title="A",
                    title="t",
                    media_type="audio",
                    played_by=502,
                    duration_seconds=50,
                    started_at=now,
                ),
                CallReport(
                    chat_id=chat_b,
                    chat_title="B",
                    title="t",
                    media_type="audio",
                    played_by=501,
                    duration_seconds=999,
                    started_at=now,
                ),
            ]
        )
        await session.commit()

    rank = await repo.get_call_stats_user_rank(
        chat_a,
        502,
        start=start,
        end=end,
    )
    assert rank == 2
    other_chat_rank = await repo.get_call_stats_user_rank(
        chat_b,
        501,
        start=start,
        end=end,
    )
    assert other_chat_rank == 1
