"""FSM escape paths must clear wizard state without stop_propagation."""
from __future__ import annotations

import os
import sys
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

from app.config.settings import settings
from app.handlers import broadcast_wizard, helper_otp_wizard, helper_panel
from app.services import wizard_ui


def _dev_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        text=text,
        caption=None,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        id=42,
        reply=AsyncMock(),
        reply_text=AsyncMock(),
        stop_propagation=MagicMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("escape", ["/help", "راهنما", "کمک", "/panel", "پنل", "/cancel", "لغو"])
async def test_clear_wizard_and_allow_command_no_stop_propagation(escape: str):
    message = _dev_message(escape)
    client = MagicMock()
    with patch("app.services.wizard_ui.clear_runtime_state", AsyncMock()) as clear_mock:
        handled = await wizard_ui.clear_wizard_and_allow_command(client, message, lang="fa")
    assert handled is True
    clear_mock.assert_awaited_once()
    message.reply.assert_not_awaited()
    message.stop_propagation.assert_not_called()


@pytest.mark.asyncio
async def test_bcw_text_input_escape_returns_without_propagation_stop():
    bot = MagicMock()
    handlers: list = []

    def on_message(*args, **kwargs):
        def deco(fn):
            handlers.append(fn)
            return fn

        return deco

    bot.on_message = on_message
    bot.on_callback_query = lambda *a, **k: (lambda fn: fn)
    broadcast_wizard.register(bot, None)
    handler = next(fn for fn in handlers if fn.__name__ == "bcw_text_input")
    message = _dev_message("/help")

    with (
        patch("app.services.wizard_ui.clear_runtime_state", AsyncMock()),
        patch("app.handlers.broadcast_wizard.get_redis", AsyncMock()),
    ):
        await handler(MagicMock(), message)

    message.stop_propagation.assert_not_called()


@pytest.mark.asyncio
async def test_helper_otp_escape_clears_without_stop():
    bot = MagicMock()
    handlers: list = []

    def on_message(*args, **kwargs):
        def deco(fn):
            handlers.append(fn)
            return fn

        return deco

    bot.on_message = on_message
    bot.on_callback_query = lambda *a, **k: (lambda fn: fn)
    helper_otp_wizard.register(bot, None)
    handler = next(fn for fn in handlers if fn.__name__ == "otp_text_handler")
    message = _dev_message("راهنما")

    with (
        patch("app.services.wizard_ui.clear_runtime_state", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.detect_active_wizard_state_in_redis", AsyncMock(return_value=(None, None))),
    ):
        await handler(MagicMock(), message)

    message.stop_propagation.assert_not_called()


@pytest.mark.asyncio
async def test_helper_proxy_escape_clears_without_stop():
    bot = MagicMock()
    handlers: list = []

    def on_message(*args, **kwargs):
        def deco(fn):
            handlers.append(fn)
            return fn

        return deco

    bot.on_message = on_message
    bot.on_callback_query = lambda *a, **k: (lambda fn: fn)
    helper_panel.register(bot, None)
    handler = next(fn for fn in handlers if fn.__name__ == "hlp_proxy_input")
    message = _dev_message("/panel")

    with (
        patch("app.services.wizard_ui.clear_runtime_state", AsyncMock()),
        patch(
            "app.handlers.helper_panel.get_redis",
            AsyncMock(return_value=MagicMock(get=AsyncMock(return_value=None))),
        ),
    ):
        await handler(MagicMock(), message)

    message.stop_propagation.assert_not_called()
