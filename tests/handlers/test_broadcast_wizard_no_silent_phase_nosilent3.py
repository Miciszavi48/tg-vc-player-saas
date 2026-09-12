"""NoSilent-3: broadcast wizard Redis FSM expired-state reply tests."""
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
from app.handlers import broadcast_wizard
from app.services.wizard_ui import TOKEN_DEV_BROADCAST
from app.utils.i18n import t
from app.utils.redis_keys import bcw_state_key, wizard_return_key
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
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


def _dev_message(text: str = "", user_id: int | None = None) -> SimpleNamespace:
    uid = user_id if user_id is not None else settings.DEVELOPER_ID
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid),
        text=text,
        caption=None,
        photo=None,
        video=None,
        document=None,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        id=42,
        reply=AsyncMock(),
        entities=None,
        caption_entities=None,
    )


@pytest.fixture
def handlers():
    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    return {
        "capture": _handler_by_name(bot.message_handlers, "bcw_capture_payload"),
        "text": _handler_by_name(bot.message_handlers, "bcw_text_input"),
    }


@pytest.mark.asyncio
async def test_capture_payload_missing_state_with_return_token_replies_expired(handlers):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(wizard_return_key(user_id), TOKEN_DEV_BROADCAST)
    msg = _dev_message(text="Hello broadcast payload")
    repeated = _dev_message(text="Hello broadcast payload")

    with patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)):
        await handlers["capture"](SimpleNamespace(), msg)
        await handlers["capture"](SimpleNamespace(), repeated)

    msg.reply.assert_awaited_once()
    assert t("fa", "broadcast.wizard.session_expired") in msg.reply.await_args.args[0]
    repeated.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_payload_missing_state_no_return_token_silent(handlers):
    redis = _FakeRedis()
    msg = _dev_message(text="Hello broadcast payload")

    with patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)):
        await handlers["capture"](SimpleNamespace(), msg)

    msg.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_payload_wrong_step_replies_expired(handlers):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        bcw_state_key(user_id),
        json.dumps({"step": "mode_selection"}),
    )
    msg = _dev_message(text="Another payload attempt")

    with patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)):
        await handlers["capture"](SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "broadcast.wizard.session_expired") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_text_input_missing_state_schedule_text_replies_expired(handlers):
    redis = _FakeRedis()
    await redis.set(
        wizard_return_key(settings.DEVELOPER_ID),
        TOKEN_DEV_BROADCAST,
    )
    msg = _dev_message(text="1403/12/05 18:30")

    with patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)):
        await handlers["text"](SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "broadcast.wizard.session_expired") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_text_input_wrong_step_hours_replies_expired(handlers):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        bcw_state_key(user_id),
        json.dumps({"step": "confirm", "mode": "send"}),
    )
    msg = _dev_message(text="24")

    with patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)):
        await handlers["text"](SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "broadcast.wizard.session_expired") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_capture_payload_valid_awaiting_payload_advances_step(handlers):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        bcw_state_key(user_id),
        json.dumps({"step": "awaiting_payload"}),
    )
    msg = _dev_message(text="Broadcast body")
    set_state = AsyncMock()

    with (
        patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.broadcast_wizard._set_state", set_state),
        patch(
            "app.handlers.broadcast_wizard.BroadcastServiceV2.extract_payload",
            return_value={"payload_type": "text", "text_content": "Broadcast body"},
        ),
    ):
        await handlers["capture"](SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "broadcast.wizard.step2_title") in msg.reply.await_args.args[0]
    set_state.assert_awaited_once()
    saved_state = set_state.await_args.args[1]
    assert saved_state["step"] == "mode_selection"
    assert saved_state["payload"]["text_content"] == "Broadcast body"


@pytest.mark.asyncio
async def test_text_input_invalid_datetime_with_valid_state_unchanged(handlers):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        bcw_state_key(user_id),
        json.dumps({"step": "awaiting_datetime", "mode": "send"}),
    )
    msg = _dev_message(text="not-a-date")
    set_state = AsyncMock()

    with (
        patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.broadcast_wizard._set_state", set_state),
    ):
        await handlers["text"](SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "broadcast.wizard.invalid_datetime") in msg.reply.await_args.args[0]
    set_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_input_invalid_hours_with_valid_state_unchanged(handlers):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        bcw_state_key(user_id),
        json.dumps({"step": "awaiting_hours", "mode": "send"}),
    )
    msg = _dev_message(text="0")
    set_state = AsyncMock()

    with (
        patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.broadcast_wizard._set_state", set_state),
    ):
        await handlers["text"](SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "common.errors.invalid_number") in msg.reply.await_args.args[0]
    set_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_capture_does_not_send_broadcast(handlers):
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(wizard_return_key(user_id), TOKEN_DEV_BROADCAST)
    msg = _dev_message(text="Expired payload")
    extract = AsyncMock()
    set_state = AsyncMock()

    with (
        patch("app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.broadcast_wizard.BroadcastServiceV2.extract_payload", extract),
        patch("app.handlers.broadcast_wizard._set_state", set_state),
    ):
        await handlers["capture"](SimpleNamespace(), msg)

    extract.assert_not_called()
    set_state.assert_not_called()
    msg.reply.assert_awaited_once()


def test_bcw_cb_constants_unchanged():
    required = [
        "BCW_START", "BCW_MODE_SEND", "BCW_MODE_FWD",
        "BCW_TGT_NEXT", "BCW_SEND_NOW", "BCW_SEND_AT",
        "BCW_SEND_AFTER", "BCW_SEND_RECURRING",
        "BCW_CONFIRM", "BCW_CANCEL",
    ]
    for key in required:
        assert key in CB
        assert CB[key].startswith("bcw:")
