"""NoSilent-3: helper proxy Redis FSM expired-state reply tests."""
from __future__ import annotations

import json
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
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import helper_panel
from app.utils.bot_guards import is_developer
from app.utils.i18n import t
from app.services.wizard_ui import TOKEN_HELPER_HOME
from app.utils.redis_keys import helper_proxy_state_key, wizard_return_key
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _noop(*args, **kwargs):  # noqa: ANN002, ANN003
                def _decorator(fn):
                    return fn

                return _decorator

            return _noop
        raise AttributeError(name)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.store.pop(key, None)
        return len(keys)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _dev_message(text: str = "", user_id: int | None = None) -> SimpleNamespace:
    uid = user_id if user_id is not None else settings.DEVELOPER_ID
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid),
        text=text,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
        stop_propagation=AsyncMock(),
    )


@pytest.fixture
def proxy_handler():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    return _handler_by_name(bot.message_handlers, "hlp_proxy_input")


@pytest.mark.asyncio
async def test_proxy_input_missing_state_replies_expired(proxy_handler):
    redis = _FakeRedis()
    await redis.set(
        wizard_return_key(settings.DEVELOPER_ID),
        TOKEN_HELPER_HOME,
    )
    msg = _dev_message(text="socks5://host.example:1080")
    repeated = _dev_message(text="socks5://host.example:1080")
    session_mock = AsyncMock()

    with patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)):
        await proxy_handler(SimpleNamespace(), msg)
        await proxy_handler(SimpleNamespace(), repeated)

    msg.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.proxy_session_expired") in msg.reply.await_args.args[0]
    repeated.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_proxy_input_wrong_step_replies_expired(proxy_handler):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        helper_proxy_state_key(user_id),
        json.dumps({"step": "other", "helper_id": 7}),
    )
    msg = _dev_message(text="socks5://host.example:1080")

    with patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)):
        await proxy_handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.proxy_session_expired") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_proxy_input_valid_proxy_saves(proxy_handler):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        helper_proxy_state_key(user_id),
        json.dumps({"step": "awaiting_proxy", "helper_id": 7}),
    )
    msg = _dev_message(text="socks5://host.example:1080")

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock()
    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin_ctx
    session.execute = AsyncMock()

    with (
        patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.database.engine.async_session", return_value=session),
    ):
        await proxy_handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    reply_text = msg.reply.await_args.args[0]
    assert t("fa", "admin.helpers.proxy_set_ok", id=7, proxy="socks5://host.example:1080") in reply_text
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_proxy_input_valid_none_clears_proxy(proxy_handler):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        helper_proxy_state_key(user_id),
        json.dumps({"step": "awaiting_proxy", "helper_id": 9}),
    )
    msg = _dev_message(text="none")

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock()
    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = begin_ctx
    session.execute = AsyncMock()

    with (
        patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)),
        patch("app.database.engine.async_session", return_value=session),
    ):
        await proxy_handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.proxy_removed", id=9) in msg.reply.await_args.args[0]
    session.execute.assert_awaited_once()


def test_non_developer_blocked_by_dev_filter():
    assert is_developer(999_999_999) is False


def test_helper_proxy_callback_data_unchanged():
    assert CB["HLP_HOME"].startswith("hlp:")
    assert CB["HLP_DETAIL_PREFIX"].startswith("hlp:d:")
