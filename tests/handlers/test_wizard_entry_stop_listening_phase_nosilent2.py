"""NoSilent-2: wizard entry stop_listening tests."""
from __future__ import annotations

import json
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
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import broadcast_wizard, helper_otp_wizard, helper_panel
from app.utils.redis_keys import bcw_state_key, helper_proxy_state_key
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
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


def test_helper_otp_state_captures_panel_anchor():
    query = _pm_query(settings.DEVELOPER_ID, CB["HLP_ADD_OTP"])

    state = helper_otp_wizard._new_panel_state(query, "awaiting_phone")

    assert state == {
        "step": "awaiting_phone",
        "panel_chat_id": 100,
        "panel_message_id": 321,
    }


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            id=321,
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_begin_broadcast_wizard_calls_safe_stop_listening():
    query = _pm_query(settings.DEVELOPER_ID, CB["BCW_START"])
    client = SimpleNamespace(stop_listening=AsyncMock())
    fake_redis = _FakeRedis()

    with (
        patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.handlers.broadcast_wizard.remember_return_token", AsyncMock()),
    ):
        await broadcast_wizard.begin_broadcast_wizard(client, query)

    client.stop_listening.assert_awaited_once_with(chat_id=100)
    raw = fake_redis.store.get(bcw_state_key(settings.DEVELOPER_ID))
    assert raw is not None
    state = json.loads(raw)
    assert state["panel_chat_id"] == 100
    assert state["panel_message_id"] == 321


@pytest.mark.asyncio
async def test_helper_proxy_start_calls_safe_stop_listening():
    bot = _RecorderBot()
    helper_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "hlp_set_proxy_start")
    helper_id = 42
    query = _pm_query(settings.DEVELOPER_ID, f"hlp:proxy:{helper_id}")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="testbot")),
    )
    fake_redis = _FakeRedis()

    with (
        patch("app.handlers.helper_panel._guard_private", AsyncMock(return_value=False)),
        patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch("app.handlers.helper_panel.remember_return_token", AsyncMock()),
    ):
        await handler(client, query)

    client.stop_listening.assert_awaited_once_with(chat_id=100)
    raw = fake_redis.store.get(helper_proxy_state_key(settings.DEVELOPER_ID))
    assert raw is not None
    state = json.loads(raw)
    assert state["helper_id"] == helper_id
    assert state["panel_chat_id"] == 100
    assert state["panel_message_id"] == 321


@pytest.mark.asyncio
async def test_broadcast_wizard_start_survives_stop_listening_failure():
    query = _pm_query(settings.DEVELOPER_ID, CB["BCW_START"])
    client = SimpleNamespace(stop_listening=AsyncMock(side_effect=RuntimeError("boom")))
    fake_redis = _FakeRedis()

    with (
        patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.handlers.broadcast_wizard.remember_return_token", AsyncMock()),
    ):
        await broadcast_wizard.begin_broadcast_wizard(client, query)

    assert fake_redis.store.get(bcw_state_key(settings.DEVELOPER_ID)) is not None


@pytest.mark.asyncio
async def test_safe_stop_listening_falls_back_to_positional_signature():
    from app.utils.ask_result import safe_stop_listening

    calls: list[int] = []

    async def _stop_fn(*args, **kwargs):
        if kwargs:
            raise TypeError("keyword not supported")
        calls.append(int(args[0]))

    client = SimpleNamespace(stop_listening=_stop_fn)
    await safe_stop_listening(client, 200)
    assert calls == [200]


def test_wizard_entry_callback_constants_unchanged():
    assert CB["BCW_START"] == "bcw:start"
    assert CB["BCW_START"].startswith("bcw:")
