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
from app.handlers import callbacks
from app.utils.pagination import paginate_keyboard
from app.utils.ui import CB, KeyboardFactory


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


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


@pytest.mark.asyncio
async def test_noop_callback_answers_without_edit():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "noop_callback")

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        data=CB["NOOP"],
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
        ),
    )

    await handler(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    query.message.edit_text.assert_not_awaited()
    query.message.edit_caption.assert_not_awaited()


def test_pagination_page_indicator_uses_noop_callback():
    kb = paginate_keyboard("en", page=1, total_pages=3, cb_prefix="pg:test:")
    assert CB["NOOP"] in _callback_data_set(kb)


@pytest.mark.asyncio
async def test_nav_back_supports_caption_messages():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_back")

    kb = KeyboardFactory.back_button("en")
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID, first_name="Dev"),
        data=CB["NAV_BACK"],
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            text=None,
            caption="preview caption",
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            delete=AsyncMock(),
        ),
    )

    with patch(
        "app.handlers.callbacks.build_private_root_payload",
        AsyncMock(return_value=("Root", kb)),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_caption.assert_awaited_once()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_wz_home_supports_caption_messages():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_home")

    kb = KeyboardFactory.back_button("en")
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID, first_name="Dev"),
        data=CB["WZ_HOME"],
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            text=None,
            caption="preview caption",
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            delete=AsyncMock(),
        ),
    )

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()),
        patch(
            "app.handlers.callbacks.resolve_navigation_payload",
            AsyncMock(return_value=("Root", kb)),
        ),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_caption.assert_awaited_once()
    query.message.edit_text.assert_not_awaited()
