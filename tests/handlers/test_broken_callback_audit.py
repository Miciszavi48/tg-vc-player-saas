"""Broken visible callback audit: grp:callstats:sel, an:*, hlp:* and global inventory guard."""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import analytics_panel, call_stats_panel, dev_panel, helper_panel
from app.handlers.call_stats_panel import _SEL_RE
from app.utils.i18n import t
from app.utils.ui import CB

_CHAT_ID = -1003700458073
_REQUESTER = 6909288370
_SCOPES = ("all", "vip", "admin")
_PERIODS = ("all", "week", "today")


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.callback_filters: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            self.callback_filters.append(args[0] if args else kwargs.get("filters"))
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator

    def __getattr__(self, name):
        if name.startswith("on_"):
            def _other(*args, **kwargs):
                def _decorator(fn):
                    return fn

                return _decorator

            return _other
        raise AttributeError(name)


def _handler_by_name(bot: _RecorderBot, name: str):
    return next(fn for fn in bot.callback_handlers if fn.__name__ == name)


def _collect_regexes(flt, out: list) -> None:
    if flt is None:
        return
    pattern = getattr(flt, "p", None)
    if pattern is not None and hasattr(pattern, "search"):
        out.append(pattern)
    for attr in ("base", "other"):
        sub = getattr(flt, attr, None)
        if sub is not None:
            _collect_regexes(sub, out)


def _query(
    data: str,
    *,
    user_id: int = _REQUESTER,
    chat_id: int = _CHAT_ID,
    chat_type: str = "supergroup",
):
    return SimpleNamespace(
        id=f"query-{data}",
        data=data,
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            id=55,
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type)),
            text="menu",
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


def _client():
    return SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
    )


# ── grp:callstats:sel:* — regex, negative chat id, distinct render ──────────


@pytest.mark.parametrize("scope", _SCOPES)
@pytest.mark.parametrize("period", _PERIODS)
def test_sel_regex_matches_all_nine_combos_with_negative_chat_id(scope, period):
    data = f"grp:callstats:sel:{scope}:{period}:{_CHAT_ID}:{_REQUESTER}"
    match = _SEL_RE.match(data)
    assert match is not None
    assert match.group("scope") == scope
    assert match.group("period") == period
    assert int(match.group("chat_id")) == _CHAT_ID
    assert int(match.group("user_id")) == _REQUESTER


@pytest.mark.parametrize(
    "data",
    [
        "grp:callstats:sel:all:week:-1003700458073",
        "grp:callstats:sel:everyone:week:-1003700458073:6909288370",
        "grp:callstats:sel:all:month:-1003700458073:6909288370",
        "grp:callstats:sel:all:week:abc:6909288370",
        "grp:callstats:sel:all:week:-1003700458073:-6909288370",
        "grp:callstats:sel:all:week:-1003700458073:6909288370:extra",
    ],
)
def test_sel_regex_rejects_malformed_payloads(data):
    assert _SEL_RE.match(data) is None


@pytest.mark.asyncio
async def test_build_panel_report_renders_distinct_text_for_all_nine_selections():
    """Empty data must NOT collapse to one identical text (MESSAGE_NOT_MODIFIED no-op bug)."""
    from app.services.call_stats_panel_service import build_panel_report

    texts: dict[tuple[str, str], str] = {}
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
        for scope in _SCOPES:
            for period in _PERIODS:
                texts[(scope, period)] = await build_panel_report(
                    _CHAT_ID, scope=scope, period=period, lang="fa"
                )

    assert len(set(texts.values())) == 9
    for text in texts.values():
        assert t("fa", "call_stats_panel.empty") in text


@pytest.mark.asyncio
async def test_call_stats_select_rejects_foreign_requester_with_visible_alert():
    bot = _RecorderBot()
    call_stats_panel.register(bot, None)
    handler = _handler_by_name(bot, "call_stats_select")
    data = f"grp:callstats:sel:all:week:{_CHAT_ID}:{_REQUESTER}"
    query = _query(data, user_id=999)

    await handler(_client(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "call_stats_panel.invalid_callback"), show_alert=True
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_stats_select_rejects_chat_mismatch_with_visible_alert():
    bot = _RecorderBot()
    call_stats_panel.register(bot, None)
    handler = _handler_by_name(bot, "call_stats_select")
    data = f"grp:callstats:sel:all:week:{_CHAT_ID}:{_REQUESTER}"
    query = _query(data, chat_id=-2009)

    await handler(_client(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "call_stats_panel.invalid_callback"), show_alert=True
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_stats_select_rejects_private_context_with_visible_alert():
    bot = _RecorderBot()
    call_stats_panel.register(bot, None)
    handler = _handler_by_name(bot, "call_stats_select")
    data = f"grp:callstats:sel:all:week:{_CHAT_ID}:{_REQUESTER}"
    query = _query(
        data,
        chat_id=_REQUESTER,
        user_id=_REQUESTER,
        chat_type="private",
    )

    await handler(_client(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "call_stats_panel.invalid_callback"), show_alert=True
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_stats_select_disabled_feature_shows_alert():
    bot = _RecorderBot()
    call_stats_panel.register(bot, None)
    handler = _handler_by_name(bot, "call_stats_select")
    data = f"grp:callstats:sel:vip:today:{_CHAT_ID}:{_REQUESTER}"
    query = _query(data)

    with patch(
        "app.handlers.call_stats_panel.call_stats_settings_repo.get_enabled",
        AsyncMock(return_value=False),
    ):
        await handler(_client(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "call_stats_panel.feature_disabled"), show_alert=True
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_stats_select_valid_press_edits_panel():
    bot = _RecorderBot()
    call_stats_panel.register(bot, None)
    handler = _handler_by_name(bot, "call_stats_select")
    data = f"grp:callstats:sel:admin:today:{_CHAT_ID}:{_REQUESTER}"
    query = _query(data)

    with (
        patch(
            "app.handlers.call_stats_panel.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_stats_panel.can_manage_call_command_user",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_stats_panel.panel_svc.build_panel_report",
            AsyncMock(return_value="گزارش مدیران — امروز"),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_client(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.call_args.args[0] == "گزارش مدیران — امروز"


# ── an:* — routing and failure safety ────────────────────────────────────────


def test_an_callbacks_each_match_a_dedicated_handler_regex():
    bot = _RecorderBot()
    analytics_panel.register(bot, None)
    patterns = []
    for flt in bot.callback_filters:
        _collect_regexes(flt, patterns)

    for key in ("AN_HOME", "AN_YESTERDAY", "AN_7DAYS", "AN_14DAYS", "AN_30DAYS", "AN_PEAK", "AN_ERRORS"):
        data = CB[key]
        assert any(p.search(data) for p in patterns), data


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "cb_key"),
    [
        ("an_yesterday", "AN_YESTERDAY"),
        ("an_7d", "AN_7DAYS"),
        ("an_14d", "AN_14DAYS"),
        ("an_30d", "AN_30DAYS"),
        ("an_peak", "AN_PEAK"),
        ("an_errors", "AN_ERRORS"),
    ],
)
async def test_an_report_failure_shows_unavailable_message(handler_name, cb_key):
    """A DB failure inside get_report must render a visible message, not a silent no-op."""
    bot = _RecorderBot()
    analytics_panel.register(bot, None)
    handler = _handler_by_name(bot, handler_name)
    query = _query(CB[cb_key], user_id=settings.DEVELOPER_ID, chat_id=settings.DEVELOPER_ID, chat_type="private")

    with (
        patch(
            "app.handlers.analytics_panel.get_report",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_client(), query)

    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.call_args.args[0] == t("fa", "admin.analytics.unavailable")


@pytest.mark.asyncio
async def test_an_yesterday_success_renders_summary():
    bot = _RecorderBot()
    analytics_panel.register(bot, None)
    handler = _handler_by_name(bot, "an_yesterday")
    query = _query(CB["AN_YESTERDAY"], user_id=settings.DEVELOPER_ID, chat_id=settings.DEVELOPER_ID, chat_type="private")

    with (
        patch(
            "app.handlers.analytics_panel.get_report",
            AsyncMock(return_value={"range": "2026-07-02", "events_total": 3}),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(_client(), query)

    query.message.edit_text.assert_awaited_once()
    rendered = query.message.edit_text.call_args.args[0]
    assert "2026-07-02" in rendered
    assert "[missing:" not in rendered


@pytest.mark.asyncio
async def test_an_yesterday_non_developer_gets_visible_denial():
    bot = _RecorderBot()
    analytics_panel.register(bot, None)
    handler = _handler_by_name(bot, "an_yesterday")
    query = _query(
        CB["AN_YESTERDAY"],
        user_id=999,
        chat_id=999,
        chat_type="private",
    )

    await handler(_client(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.no_access"), show_alert=True
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_yesterday_group_context_uses_private_guard_message():
    bot = _RecorderBot()
    analytics_panel.register(bot, None)
    handler = _handler_by_name(bot, "an_yesterday")
    query = _query(
        CB["AN_YESTERDAY"],
        user_id=settings.DEVELOPER_ID,
        chat_id=_CHAT_ID,
        chat_type="supergroup",
    )

    await handler(_client(), query)

    query.answer.assert_awaited_once_with()
    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.call_args.args[0] == t(
        "fa", "admin.analytics.private_only"
    )


@pytest.mark.asyncio
async def test_private_callback_language_lookup_skips_group_chat_settings():
    from app.services.language_service import resolve_lang_from_update

    update = _query(
        CB["AN_YESTERDAY"],
        user_id=settings.DEVELOPER_ID,
        chat_id=settings.DEVELOPER_ID,
        chat_type="private",
    )

    with (
        patch(
            "app.services.language_service.settings_repo.get_chat_settings",
            AsyncMock(),
        ) as settings_mock,
        patch(
            "app.services.language_service.user_repo.get_user",
            AsyncMock(return_value=None),
        ) as user_mock,
    ):
        assert await resolve_lang_from_update(update) == "fa"

    settings_mock.assert_not_awaited()
    user_mock.assert_awaited_once_with(settings.DEVELOPER_ID)


# ── hlp:* — regression guard ─────────────────────────────────────────────────


def test_hlp_shortcuts_still_have_dedicated_handlers():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    helper_panel.register(bot, None)
    names = {fn.__name__ for fn in bot.callback_handlers}
    assert "dev_shortcut_helper_home" in names
    assert "hlp_home" in names
    assert "hlp_unknown_or_denied" in names

    patterns = []
    for flt in bot.callback_filters:
        _collect_regexes(flt, patterns)
    for data in ("hlp:home", "hlp:list", "hlp:health", "hlp:stats", "hlp:add", "hlp:rotkey"):
        assert any(p.search(data) for p in patterns), data


# ── global inventory guard ───────────────────────────────────────────────────

_SAMPLE_BY_NAME = {
    "chat_id": "-1003700458073",
    "target_chat_id": "-1003700458073",
    "group_id": "-1003700458073",
    "channel_id": "-1002000000001",
    "user_id": "6909288370",
    "requester_id": "6909288370",
    "target_id": "6909288370",
    "target_user_id": "6909288370",
    "owner_id": "6909288370",
    "sudo_id": "6909288370",
    "helper_id": "6909288370",
    "message_id": "55",
    "page": "0",
    "days": "30",
    "scope": "all",
    "period": "week",
    "lang": "fa",
    "invoice_id": "7",
    "report_id": "7",
    "command_id": "7",
}


def _resolve_fstring(node: ast.JoinedStr, cb: dict) -> str | None:
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
            continue
        inner = value.value if isinstance(value, ast.FormattedValue) else None
        if inner is None:
            return None
        if (
            isinstance(inner, ast.Subscript)
            and isinstance(inner.value, ast.Name)
            and inner.value.id == "CB"
            and isinstance(inner.slice, ast.Constant)
            and inner.slice.value in cb
        ):
            parts.append(cb[inner.slice.value])
        elif isinstance(inner, ast.Name) and inner.id in _SAMPLE_BY_NAME:
            parts.append(_SAMPLE_BY_NAME[inner.id])
        elif isinstance(inner, ast.Attribute) and inner.attr in _SAMPLE_BY_NAME:
            parts.append(_SAMPLE_BY_NAME[inner.attr])
        elif isinstance(inner, ast.Constant):
            parts.append(str(inner.value))
        else:
            return None  # dynamic — cannot resolve statically
    return "".join(parts)


def _resolve_callback_expr(node: ast.expr, cb: dict) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _resolve_fstring(node, cb)
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "CB"
        and isinstance(node.slice, ast.Constant)
    ):
        return cb.get(node.slice.value)
    return None


def _extract_visible_callbacks() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_button = getattr(node.func, "attr", getattr(node.func, "id", "")) == "InlineKeyboardButton"
            for kw in node.keywords:
                if kw.arg != "callback_data" or not is_button:
                    continue
                value = _resolve_callback_expr(kw.value, CB)
                if value:
                    found.append((str(path), node.lineno, value))
    return found


def test_every_statically_visible_callback_matches_a_dedicated_handler():
    """Inventory guard: each visible callback_data must match a real handler regex,
    not merely the known-prefix fallback."""
    from app import handlers as handlers_pkg

    bot = _RecorderBot()
    for module in handlers_pkg._MODULES:
        module.register(bot, None)

    patterns = []
    for flt in bot.callback_filters:
        _collect_regexes(flt, patterns)
    dedicated = [p for p in patterns if "noop|Add:" not in p.pattern]

    visible = _extract_visible_callbacks()
    assert len(visible) > 80, "extraction should find the bulk of visible callbacks"

    unmatched = []
    for path, lineno, data in visible:
        if data == "noop":
            continue
        if not any(p.search(data) for p in dedicated):
            unmatched.append(f"{data!r} at {path}:{lineno}")

    assert not unmatched, "visible callbacks without a dedicated handler:\n" + "\n".join(unmatched)
