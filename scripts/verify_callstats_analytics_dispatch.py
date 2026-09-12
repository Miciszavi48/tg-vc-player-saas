#!/usr/bin/env python3
"""Read-only analytics/callstats callback dispatch verification.

This script constructs the real Kurigram dispatcher, registers application
handlers, and checks whether production-visible callback samples match a
non-fallback route handler. It does not start a Telegram session.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.callback-dispatch-verify.db")
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

from pyrogram import Client

from app.handlers import _MODULES, register_all
from app.handlers.callback_route_diag import (
    all_callback_route_samples,
    run_callback_route_checks,
)


def _module_included(name: str) -> bool:
    return any(getattr(module, "__name__", "") == name for module in _MODULES)


def _print_table(rows) -> None:
    print("| sample | matched? | group | handler name | filter repr | module | reason |")
    print("|---|---:|---:|---|---|---|---|")
    for row in rows:
        group = "" if row.group is None else str(row.group)
        print(
            f"| {row.sample} | {row.matched} | {group} | {row.handler_name} | "
            f"{row.filter_repr} | {row.module} | {row.reason} |"
        )


async def main() -> int:
    bot = Client(
        "callstats_analytics_dispatch_verify",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.2)

    print(f"analytics_panel_imported={_module_included('app.handlers.analytics_panel')}")
    print(f"call_stats_panel_imported={_module_included('app.handlers.call_stats_panel')}")

    rows = await run_callback_route_checks(bot, all_callback_route_samples())
    _print_table(rows)

    failed = [row for row in rows if not row.matched]
    if failed:
        print("FAIL: one or more callback samples do not match a route handler")
        for row in failed:
            print(f"- {row.sample}: {row.reason}")
        return 1

    print("PASS: all analytics and call-stats samples match route handlers")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
