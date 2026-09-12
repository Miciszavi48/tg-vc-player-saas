from __future__ import annotations

import json
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
from app.handlers import callbacks, helper_otp_wizard
from app.utils.redis_keys import (
    bcw_state_key,
    helper_otp_state_key,
    helper_proxy_state_key,
    wizard_return_key,
)
from app.utils.ui import KeyboardFactory


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


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.last_deleted: tuple[str, ...] = ()

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def delete(self, *keys: str) -> int:
        self.last_deleted = tuple(keys)
        count = 0
        for key in keys:
            if key in self.store:
                self.store.pop(key, None)
                count += 1
        return count


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


@pytest.mark.asyncio
async def test_cancel_command_clears_all_wizard_states_and_returns_root():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    cancel_handler = _handler_by_name(bot.message_handlers, "cancel_command")

    user_id = settings.DEVELOPER_ID
    redis = _FakeRedis()
    redis.store[helper_otp_state_key(user_id)] = json.dumps({"step": "awaiting_phone"})
    redis.store[helper_proxy_state_key(user_id)] = json.dumps({"step": "awaiting_proxy"})
    redis.store[bcw_state_key(user_id)] = json.dumps({"step": "confirm"})
    redis.store[wizard_return_key(user_id)] = "dev_broadcast"

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
    )

    kb = KeyboardFactory.back_button("en")
    client = SimpleNamespace(stop_listening=AsyncMock(), send_message=AsyncMock())
    with (
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.wizard_ui.resolve_navigation_payload", AsyncMock(return_value=("root", kb))),
    ):
        await cancel_handler(client, message)

    assert helper_otp_state_key(user_id) in redis.last_deleted
    assert helper_proxy_state_key(user_id) in redis.last_deleted
    assert bcw_state_key(user_id) in redis.last_deleted
    assert wizard_return_key(user_id) in redis.last_deleted
    client.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_wz_home_clears_all_wizard_states_and_routes_root():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    wz_home = _handler_by_name(bot.callback_handlers, "wz_home")

    user_id = settings.DEVELOPER_ID
    redis = _FakeRedis()
    redis.store[helper_otp_state_key(user_id)] = json.dumps({"step": "awaiting_phone"})
    redis.store[helper_proxy_state_key(user_id)] = json.dumps({"step": "awaiting_proxy"})

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Dev"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            text="old",
            caption=None,
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            delete=AsyncMock(),
        ),
    )

    kb = KeyboardFactory.back_button("en")
    with (
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.callbacks.resolve_navigation_payload", AsyncMock(return_value=("root", kb))),
    ):
        client = SimpleNamespace(stop_listening=AsyncMock(), send_message=AsyncMock())
        await wz_home(client, query)

    assert helper_otp_state_key(user_id) in redis.last_deleted
    assert helper_proxy_state_key(user_id) in redis.last_deleted
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_helper_proxy_state_does_not_hijack_otp_text_flow():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_start = _handler_by_name(bot.callback_handlers, "otp_start")
    otp_text_handler = _handler_by_name(bot.message_handlers, "otp_text_handler")

    user_id = settings.DEVELOPER_ID
    redis = _FakeRedis()
    await redis.set(
        helper_proxy_state_key(user_id),
        json.dumps({"step": "awaiting_proxy", "helper_id": 55}),
    )

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )

    client = SimpleNamespace(stop_listening=AsyncMock())
    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.remember_return_token", AsyncMock()),
    ):
        await otp_start.__wrapped__(client, query)

    client.stop_listening.assert_awaited_once_with(chat_id=100, user_id=user_id)

    assert await redis.get(helper_proxy_state_key(user_id)) is None
    state_raw = await redis.get(helper_otp_state_key(user_id))
    assert state_raw is not None and json.loads(state_raw).get("step") == "awaiting_phone"

    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        text="+989123456789",
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
        stop_propagation=AsyncMock(),
    )
    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard._handle_phone", AsyncMock()) as handle_phone,
    ):
        await otp_text_handler(SimpleNamespace(), msg)

    handle_phone.assert_awaited_once()
