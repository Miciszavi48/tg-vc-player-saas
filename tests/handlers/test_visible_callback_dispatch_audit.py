"""Production-visible callback dispatch audit for analytics and call stats."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

import pytest
from pyrogram import Client

from app.handlers import _MODULES, register_all
from app.handlers.callback_route_diag import (
    ANALYTICS_CALLBACK_SAMPLES,
    CALLSTATS_CALLBACK_SAMPLES,
    STARTUP_CALLBACK_ROUTE_SAMPLES,
    all_callback_route_samples,
    run_callback_route_checks,
)
from app.handlers.priority import FALLBACK_CALLBACK_GROUP, PANEL_CALLBACK_GROUP

ROOT = Path(__file__).resolve().parents[2]


async def _registered_bot() -> Client:
    bot = Client(
        "visible_callback_dispatch_audit",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.2)
    return bot


def test_analytics_and_callstats_modules_are_in_register_all():
    names = {getattr(module, "__name__", "") for module in _MODULES}
    assert "app.handlers.analytics_panel" in names
    assert "app.handlers.call_stats_panel" in names


@pytest.mark.asyncio
async def test_visible_analytics_and_callstats_callbacks_match_routes():
    bot = await _registered_bot()
    rows = await run_callback_route_checks(bot, all_callback_route_samples())

    assert {sample for _label, sample in ANALYTICS_CALLBACK_SAMPLES} <= {
        row.sample for row in rows
    }
    assert {sample for _label, sample in CALLSTATS_CALLBACK_SAMPLES} <= {
        row.sample for row in rows
    }
    assert all(row.matched for row in rows), rows
    assert all(row.group != FALLBACK_CALLBACK_GROUP for row in rows)


@pytest.mark.asyncio
async def test_visible_callback_routes_are_before_fallback_group():
    bot = await _registered_bot()
    rows = await run_callback_route_checks(bot, all_callback_route_samples())

    for row in rows:
        assert row.group is not None
        assert row.group < FALLBACK_CALLBACK_GROUP
    analytics_rows = [row for row in rows if row.sample.startswith("an:")]
    callstats_rows = [row for row in rows if row.sample.startswith("grp:callstats:")]
    assert {row.group for row in analytics_rows} == {PANEL_CALLBACK_GROUP}
    assert {row.group for row in callstats_rows} == {PANEL_CALLBACK_GROUP}


@pytest.mark.asyncio
async def test_startup_callback_samples_never_select_rejection_handlers():
    bot = await _registered_bot()
    rows = await run_callback_route_checks(bot, STARTUP_CALLBACK_ROUTE_SAMPLES)

    assert all(row.matched for row in rows), rows
    assert all(row.reason == "matched" for row in rows), rows


def test_known_visible_callback_prefixes_cannot_fall_to_unknown_when_valid():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_callstats_analytics_dispatch.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TEST_MODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: all analytics and call-stats samples match route handlers" in result.stdout


def test_global_callback_routing_coverage_verifier_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_callback_routing_coverage.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TEST_MODE": "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "representative_samples=" in result.stdout
    assert "failed_enforced_samples=0" in result.stdout
    assert "PASS: visible callback samples route to dedicated non-fallback handlers" in result.stdout
