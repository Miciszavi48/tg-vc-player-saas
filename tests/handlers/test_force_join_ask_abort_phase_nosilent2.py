"""NoSilent-2: force_join_panel ask abort tests."""
from __future__ import annotations

import os
import sys
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

    class ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from pyromod.exceptions import ListenerStopped

from app.config.settings import settings
from app.handlers import force_join_panel
from app.services.wizard_ui import TOKEN_DEV_SETTINGS
from app.utils.i18n import t


@pytest.mark.asyncio
async def test_force_join_ask_listener_stopped_avoids_duplicate_abort():
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(side_effect=ListenerStopped()),
        send_message=AsyncMock(return_value=SimpleNamespace(id=1)),
    )

    result = await force_join_panel._ask(
        client,
        100,
        "admin.fm.add_prompt",
        user_id=settings.DEVELOPER_ID,
        return_to=TOKEN_DEV_SETTINGS,
    )

    assert result is None
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_join_ask_timeout_sends_timeout_message():
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(side_effect=TimeoutError("timed out")),
        send_message=AsyncMock(),
    )

    result = await force_join_panel._ask(client, 100, "admin.fm.add_prompt")

    assert result is None
    client.send_message.assert_awaited_once()
    assert t("fa", "ask.timeout") in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_force_join_ask_cancel_text_sends_cancel_flow():
    cancel_msg = SimpleNamespace(text="/cancel")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=cancel_msg),
        send_message=AsyncMock(return_value=SimpleNamespace(id=1)),
    )

    with patch(
        "app.handlers.force_join_panel.cancel_and_resolve",
        AsyncMock(return_value=(t("fa", "common.cancelled"), SimpleNamespace())),
    ):
        result = await force_join_panel._ask(
            client,
            100,
            "admin.fm.add_prompt",
            user_id=123456789,
            return_to=TOKEN_DEV_SETTINGS,
        )

    assert result is None
    client.send_message.assert_awaited_once()
    assert t("fa", "common.cancelled") in client.send_message.await_args.args[1]
