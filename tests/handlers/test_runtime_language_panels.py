from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import help_center, sudo_panel
from app.utils.i18n import reset_current_lang, set_current_lang
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


@pytest.mark.asyncio
async def test_help_center_uses_runtime_en_language():
    bot = _RecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "help_route_home")

    token = set_current_lang("en")
    try:
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
                edit_text=AsyncMock(),
            ),
            answer=AsyncMock(),
            data=CB["HELP_HOME"],
        )
        with patch("app.handlers.help_center._get_role", AsyncMock(return_value="regular")):
            await handler(SimpleNamespace(), query)
    finally:
        reset_current_lang(token)

    rendered = query.message.edit_text.await_args.args[0]
    assert "Help" in rendered
    assert "Select" in rendered


@pytest.mark.asyncio
async def test_sudo_panel_uses_runtime_en_language():
    bot = _RecorderBot()
    sudo_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "sudo_stats")

    token = set_current_lang("en")
    try:
        query = SimpleNamespace(
            from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
            message=SimpleNamespace(
                chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
                edit_text=AsyncMock(),
            ),
            answer=AsyncMock(),
            data=CB["SUDO_STATS"],
        )
        logs = [
            SimpleNamespace(chat_type="group", action="install"),
            SimpleNamespace(chat_type="channel", action="install"),
        ]
        with patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock(return_value=logs)):
            await handler.__wrapped__(SimpleNamespace(), query)
    finally:
        reset_current_lang(token)

    rendered = query.message.edit_text.await_args.args[0]
    assert "Install Report" in rendered
