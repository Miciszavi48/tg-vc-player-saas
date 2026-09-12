"""Regression tests for callback routing hardening (pyromod listener + fallback prefixes)."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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

from app.handlers import callbacks, global_ban_guard
from app.handlers.priority import (
    CALLBACK_TRACE_GROUP,
    GLOBAL_BAN_GROUP,
    PYROMOD_CALLBACK_GROUP,
)
from app.utils.ui import CB

ROOT = Path(__file__).resolve().parents[2]


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self._cb_meta: list[dict] = []

    def on_callback_query(self, *args, **kwargs):
        self._cb_meta.append({"args": args, "kwargs": kwargs})

        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):
        def _decorator(fn):
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _known_prefix_pattern() -> re.Pattern[str]:
    source = (ROOT / "app/handlers/callbacks.py").read_text(encoding="utf-8")
    block_start = source.index("_KNOWN_CB_PREFIX = re.compile(")
    block_end = source.index("re.IGNORECASE", block_start)
    block = source[block_start:block_end]
    parts = re.findall(r'r"([^"]*)"', block)
    return re.compile("".join(parts), re.IGNORECASE)


def test_global_ban_group_runs_before_callback_trace():
    assert GLOBAL_BAN_GROUP == -995
    assert GLOBAL_BAN_GROUP < CALLBACK_TRACE_GROUP
    assert PYROMOD_CALLBACK_GROUP == -980
    assert CALLBACK_TRACE_GROUP < PYROMOD_CALLBACK_GROUP


def test_pyromod_group_excluded_from_route_seen():
    source = (ROOT / "app/handlers/__init__.py").read_text(encoding="utf-8")
    assert "PYROMOD_CALLBACK_GROUP" in source


def test_global_ban_guard_uses_priority_constant():
    source = (ROOT / "app/handlers/global_ban_guard.py").read_text(encoding="utf-8")
    assert "group=GLOBAL_BAN_GROUP" in source
    assert "group=-980" not in source


def test_global_ban_guard_registers_at_global_ban_group():
    bot = _RecorderBot()
    global_ban_guard.register(bot, None)
    groups = {meta["kwargs"].get("group", 0) for meta in bot._cb_meta}
    assert groups == {GLOBAL_BAN_GROUP}


@pytest.mark.parametrize(
    "callback_data",
    [
        CB["NAV_BACK"],
        CB["AN_HOME"],
        CB["BC_HISTORY"],
        CB["POST_INSTALL_PANEL"],
        "pg:dev:owners:1",
        CB["NOOP"],
    ],
)
def test_known_cb_prefix_covers_ui_families(callback_data: str):
    pattern = _known_prefix_pattern()
    assert pattern.match(callback_data), callback_data


def test_helper_panel_visible_home_routes_are_identity_filtered():
    """Visible helper-home routes must match on callback identity only and
    enforce dev/private INSIDE the handler (@developer_only + _guard_private).

    A filter-level ``_pm_dev`` makes the Pyrogram dispatcher *skip* the handler
    whenever the filter denies (e.g. a stale panel where ``query.message`` is
    None), leaking the tap to the ``^hlp:`` fallback -> ``unknown_callback``.
    This mirrors the analytics_panel contract enforced just below.
    """
    source = (ROOT / "app/handlers/helper_panel.py").read_text(encoding="utf-8")
    # _pm_dev stays defined for the deeper hlp:* sub-handlers (hlp:d/en/dis/...),
    # which are out of scope here and audited separately.
    assert "_pm_dev = dev_filter() & private_chat_filter()" in source
    assert "& _dev)" not in source
    for cb in ("HLP_HOME", "HLP_ADD", "HLP_ROTATE_KEY", "HLP_STATS"):
        old_pattern = "filters.regex(f\"^{CB['%s']}$\") & _pm_dev" % cb
        assert old_pattern not in source, f"{cb} must be identity-filtered, not filter-guarded"
    # Permission/scope is still enforced, just in-handler (explicit rejects).
    assert "@developer_only" in source
    assert "_guard_private(" in source


def test_analytics_panel_visible_callbacks_route_before_group_zero():
    source = (ROOT / "app/handlers/analytics_panel.py").read_text(encoding="utf-8")
    assert "PANEL_CALLBACK_GROUP" in source
    assert "group=PANEL_CALLBACK_GROUP" in source
    assert "& _pm_dev" not in source
    assert "& _dev)" not in source
    assert "async def an_home" in source
    assert "_mark_analytics_route(query, \"an_home\")" in source


def test_startup_callback_diagnostics_are_non_fatal():
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    assert "await log_callback_route_checks(bot)" in source
    assert "Callback route diagnostics failed during startup; continuing" in source


@pytest.mark.asyncio
async def test_trace_callback_received_clears_stale_listeners():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "_trace_callback_received")
    trace_meta = next(
        meta
        for meta in bot._cb_meta
        if meta["kwargs"].get("group") == CALLBACK_TRACE_GROUP
    )
    assert trace_meta is not None

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=123456789),
        message=SimpleNamespace(
            id=42,
            chat=SimpleNamespace(id=123456789, type=SimpleNamespace(value="private")),
        ),
        data=CB["NAV_BACK"],
    )
    client = MagicMock()

    with patch(
        "app.handlers.callbacks.safe_stop_listening", AsyncMock(return_value=True)
    ) as stop_mock, patch(
        "app.handlers.callbacks.safe_stop_chat_callback_listeners",
        AsyncMock(return_value=True),
    ) as chat_stop_mock, patch(
        "app.handlers.callbacks.safe_stop_message_callback_listeners",
        AsyncMock(return_value=True),
    ) as message_stop_mock:
        await handler(client, query)

    stop_mock.assert_awaited_once_with(client, 123456789, user_id=123456789)
    chat_stop_mock.assert_awaited_once_with(client, 123456789)
    message_stop_mock.assert_awaited_once_with(client, 123456789, 42)


def test_audit_callback_risk_check_passes():
    import subprocess

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_callback_risk.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
