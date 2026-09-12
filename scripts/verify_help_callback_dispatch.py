#!/usr/bin/env python3
"""Read-only Help callback dispatch verification (no Telegram/Redis/DB).

Run from repo root on dev or production server after deploy:

    python scripts/verify_help_callback_dispatch.py

Expected stdout:
    FOUND group=-845 name=help_route_promote check=True pattern=...
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_pyromod_stub() -> None:
    from types import ModuleType

    if "pyromod" in sys.modules:
        return
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


_ensure_pyromod_stub()

from pyrogram import Client
from pyrogram.types import CallbackQuery

from app.handlers import register_all
from app.handlers.help_center import (
    HELP_CALLBACK_PATTERN,
    _find_help_callback_handlers,
)


async def main() -> int:
    bot = Client(
        "help_dispatch_verify",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.1)

    query = MagicMock(spec=CallbackQuery)
    query.data = "h:promote:U6909288370"

    found = False
    ok = False
    for group, name, handler in _find_help_callback_handlers(bot):
        route_ok = await handler.check(bot, query)
        if route_ok:
            ok = True
        found = True
        print(
            f"FOUND group={group} name={name} check={route_ok} "
            f"pattern={HELP_CALLBACK_PATTERN}"
        )

    if not found:
        print("MISSING help_route_* handlers in dispatcher")
        print("groups:", sorted(bot.dispatcher.groups.keys()))
        return 1

    if not ok:
        print("FAIL: Handler.check returned False for h:promote:U6909288370")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
