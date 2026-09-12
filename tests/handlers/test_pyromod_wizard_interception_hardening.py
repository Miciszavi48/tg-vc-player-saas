"""Regression coverage for pyromod listener and Redis wizard interception hardening."""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
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

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from pyromod.exceptions import ListenerStopped

from app.config.settings import settings
from app.handlers import (
    broadcast_wizard,
    callbacks,
    dev_panel,
    force_join_panel,
    helper_otp_wizard,
    helper_panel,
    owner_panel,
    start,
)
from app.handlers.priority import (
    HELPER_OTP_INPUT_GROUP,
    HELPER_PROXY_INPUT_GROUP,
    PRIVATE_COMMAND_GROUP,
)
from app.services.wizard_ui import TOKEN_HELPER_HOME
from app.utils.ask_result import AskResult, notify_ask_abort, safe_stop_listening
from app.utils.i18n import t
from app.utils.redis_keys import wizard_return_key
from app.utils.ui import CB

ROOT = Path(__file__).resolve().parents[2]


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list[tuple] = []
        self.message_handlers: list[tuple] = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append((fn, args, kwargs))
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.message_handlers.append((fn, args, kwargs))
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
        self.deleted: list[tuple[str, ...]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def delete(self, *keys: str) -> int:
        self.deleted.append(tuple(keys))
        for key in keys:
            self.store.pop(key, None)
        return len(keys)


def _message_handler_kwargs(bot: _RecorderBot, name: str) -> dict:
    for fn, _args, kwargs in bot.message_handlers:
        if fn.__name__ == name:
            return kwargs
    raise AssertionError(f"message handler not found: {name}")


def _message_handler(bot: _RecorderBot, name: str):
    for fn, _args, _kwargs in bot.message_handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"message handler not found: {name}")


def _module_source(module_name: str) -> str:
    return (
        (ROOT / module_name.replace(".", "/"))
        .with_suffix(".py")
        .read_text(encoding="utf-8")
    )


def _dev_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        text=text,
        caption=None,
        photo=None,
        video=None,
        document=None,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        id=42,
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
        entities=None,
        caption_entities=None,
    )


def test_critical_wizard_entries_use_safe_stop_listening():
    checks = {
        "app.handlers.helper_otp_wizard": ["otp_start", "import_start"],
        "app.handlers.helper_panel": ["hlp_set_proxy_start"],
        "app.handlers.broadcast_wizard": ["begin_broadcast_wizard"],
        "app.handlers.dev_panel": ["async def _ask"],
        "app.handlers.owner_panel": ["async def _ask"],
        "app.handlers.force_join_panel": ["async def _ask"],
    }
    for module_name, markers in checks.items():
        source = _module_source(module_name)
        assert "safe_stop_listening" in source
        for marker in markers:
            assert marker in source


@pytest.mark.asyncio
async def test_safe_stop_listening_keyword_positional_and_user_fallback():
    keyword_client = SimpleNamespace(stop_listening=AsyncMock())
    assert await safe_stop_listening(keyword_client, 100, user_id=200) is True
    assert keyword_client.stop_listening.await_count >= 1

    positional_calls: list[tuple[tuple, dict]] = []

    async def positional_stop(*args, **kwargs):
        positional_calls.append((args, kwargs))
        if kwargs:
            raise TypeError("keyword unsupported")

    assert (
        await safe_stop_listening(SimpleNamespace(stop_listening=positional_stop), 101)
        is True
    )
    assert positional_calls[-1] == ((101,), {})

    user_calls: list[tuple[tuple, dict]] = []

    async def user_stop(*args, **kwargs):
        user_calls.append((args, kwargs))
        if kwargs not in (
            {"chat_id": 102},
            {"chat_id": 102, "user_id": 202},
            {"user_id": 202},
        ) and args not in ((102,), (102, 202)):
            raise TypeError("signature unsupported")

    assert (
        await safe_stop_listening(
            SimpleNamespace(stop_listening=user_stop), 102, user_id=202
        )
        is True
    )
    assert ((), {"chat_id": 102, "user_id": 202}) in user_calls


def test_critical_private_fsm_handlers_are_early_and_narrow():
    otp_bot = _RecorderBot()
    helper_otp_wizard.register(otp_bot, None)
    assert (
        _message_handler_kwargs(otp_bot, "otp_text_handler").get("group")
        == HELPER_OTP_INPUT_GROUP
    )
    assert (
        _message_handler_kwargs(otp_bot, "otp_contact_handler").get("group")
        == HELPER_OTP_INPUT_GROUP
    )

    proxy_bot = _RecorderBot()
    helper_panel.register(proxy_bot, None)
    assert (
        _message_handler_kwargs(proxy_bot, "hlp_proxy_input").get("group")
        == HELPER_PROXY_INPUT_GROUP
    )
    assert HELPER_OTP_INPUT_GROUP < HELPER_PROXY_INPUT_GROUP

    bcw_bot = _RecorderBot()
    broadcast_wizard.register(bcw_bot, None)
    assert _message_handler_kwargs(bcw_bot, "bcw_text_input").get("group") == -85
    assert _message_handler_kwargs(bcw_bot, "bcw_capture_payload").get("group") == -80

    start_bot = _RecorderBot()
    start.register(start_bot, None)
    assert _message_handler_kwargs(start_bot, "start_handler").get("group") == -100

    callbacks_bot = _RecorderBot()
    callbacks.register(callbacks_bot, None)
    assert (
        _message_handler_kwargs(callbacks_bot, "cancel_command").get("group")
        == PRIVATE_COMMAND_GROUP
    )


@pytest.mark.asyncio
async def test_helper_otp_live_phone_reaches_handler_and_stops_propagation():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    handler = _message_handler(bot, "otp_text_handler")
    msg = _dev_message("+989123456789")
    redis = _FakeRedis()
    from app.utils.redis_keys import helper_otp_state_key

    await redis.set(
        helper_otp_state_key(settings.DEVELOPER_ID), '{"step": "awaiting_phone"}'
    )
    with (
        patch(
            "app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)
        ),
        patch("app.handlers.helper_otp_wizard.safe_stop_listening", AsyncMock()),
        patch(
            "app.handlers.helper_otp_wizard._handle_phone", AsyncMock()
        ) as handle_phone,
    ):
        await handler(SimpleNamespace(), msg)

    handle_phone.assert_awaited_once()
    msg.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_helper_proxy_expired_proxy_like_input_replies_and_stops():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _message_handler(bot, "hlp_proxy_input")
    msg = _dev_message("socks5://host.example:1080")
    redis = _FakeRedis()
    await redis.set(wizard_return_key(settings.DEVELOPER_ID), TOKEN_HELPER_HOME)

    with patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)):
        await handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert (
        t("fa", "admin.helpers.proxy_session_expired") in msg.reply.await_args.args[0]
    )
    msg.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_broadcast_schedule_handler_does_not_swallow_numeric_payload_step():
    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _message_handler(bot, "bcw_text_input")
    msg = _dev_message("24")
    redis = _FakeRedis()
    from app.utils.redis_keys import bcw_state_key

    await redis.set(
        bcw_state_key(settings.DEVELOPER_ID), '{"step": "awaiting_payload"}'
    )
    with patch(
        "app.handlers.broadcast_wizard.get_redis", AsyncMock(return_value=redis)
    ):
        await handler(SimpleNamespace(), msg)

    msg.reply.assert_not_awaited()
    msg.stop_propagation.assert_not_called()


@pytest.mark.asyncio
async def test_start_command_clears_runtime_state_before_rendering_root():
    source = inspect.getsource(start.register)
    assert "clear_runtime_state(client, user_id, message.chat.id)" in source
    assert "group=PRIVATE_COMMAND_GROUP" in source


@pytest.mark.asyncio
async def test_cancel_command_routes_through_runtime_clear():
    source = inspect.getsource(callbacks.register)
    assert 'filters.command("cancel"), group=PRIVATE_COMMAND_GROUP' in source
    assert "cancel_and_resolve(" in source


@pytest.mark.asyncio
async def test_dev_owner_ask_listener_stopped_defers_to_callback_owned_redraw():
    for ask_fn in (dev_panel._ask, owner_panel._ask):
        client = SimpleNamespace(
            stop_listening=AsyncMock(),
            ask=AsyncMock(side_effect=ListenerStopped()),
            send_message=AsyncMock(return_value=SimpleNamespace(id=1)),
        )
        result = await ask_fn(client, 100, "ask.user_id", user_id=settings.DEVELOPER_ID)
        assert isinstance(result, AskResult)
        assert result.message is None
        assert result.abort_reason == "listener_stopped"
        assert result.user_notified is True
        client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_join_ask_listener_stopped_defers_to_callback_owned_redraw():
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
    )
    assert result is None
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_ask_abort_does_not_double_send_when_ask_already_notified():
    client = SimpleNamespace(send_message=AsyncMock())
    result = AskResult(
        message=None, abort_reason="listener_stopped", user_notified=True
    )
    assert await notify_ask_abort(client, 100, result, return_to="role_root") is True
    client.send_message.assert_not_called()


def test_no_critical_input_handler_uses_except_pass_or_bare_silent_return():
    critical_sources = [
        inspect.getsource(helper_otp_wizard.register),
        inspect.getsource(helper_panel.register),
        inspect.getsource(broadcast_wizard.register),
        inspect.getsource(dev_panel._ask),
        inspect.getsource(owner_panel._ask),
        inspect.getsource(force_join_panel._ask),
    ]
    for source in critical_sources:
        assert "except Exception:\n        pass" not in source
        assert "except:\n" not in source


def test_callback_data_constants_unchanged_for_hardened_flows():
    assert CB["HLP_ADD_OTP"] == "hlp:add:otp"
    assert CB["BCW_START"] == "bcw:start"
    assert CB["BCW_CANCEL"] == "bcw:cancel"
    assert CB["FM_ADD"] == "fm:add"
    assert CB["DEV_BANALL_ADD"] == "dev:banall:add"
    assert CB["DEV_BANALL_REMOVE"] == "dev:banall:remove"
