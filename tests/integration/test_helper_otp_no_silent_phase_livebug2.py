"""LiveBug-2: helper OTP phone wizard no-silent path tests."""
from __future__ import annotations

import json
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.handlers import helper_otp_wizard
from app.services.wizard_ui import TOKEN_HELPER_HOME
from app.utils.i18n import t
from app.utils.redis_keys import (
    helper_otp_state_key,
    helper_proxy_state_key,
    wizard_return_key,
)
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


def _dev_query(user_id: int | None = None) -> SimpleNamespace:
    uid = user_id if user_id is not None else settings.DEVELOPER_ID
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _dev_message(user_id: int | None = None, text: str = "") -> SimpleNamespace:
    uid = user_id if user_id is not None else settings.DEVELOPER_ID
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid),
        text=text,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
        stop_propagation=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_otp_start_calls_stop_listening_before_set_state():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_start = _handler_by_name(bot.callback_handlers, "otp_start")
    query = _dev_query()
    events: list[str] = []

    client = SimpleNamespace(
        stop_listening=AsyncMock(side_effect=lambda **_: events.append("stop")),
    )
    set_state = AsyncMock(side_effect=lambda *_a, **_k: events.append("set"))

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=_FakeRedis())),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.remember_return_token", AsyncMock()),
        patch("app.handlers.helper_otp_wizard._set_state", set_state),
    ):
        await otp_start.__wrapped__(client, query)

    assert client.stop_listening.await_count >= 1
    assert client.stop_listening.await_args_list[0].kwargs == {"chat_id": 100}
    assert events.index("stop") < events.index("set")


@pytest.mark.asyncio
async def test_otp_start_sets_awaiting_phone_in_redis():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_start = _handler_by_name(bot.callback_handlers, "otp_start")
    redis = _FakeRedis()
    query = _dev_query()
    client = SimpleNamespace(stop_listening=AsyncMock())

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.remember_return_token", AsyncMock()),
    ):
        await otp_start.__wrapped__(client, query)

    raw = await redis.get(helper_otp_state_key(settings.DEVELOPER_ID))
    assert raw is not None
    assert json.loads(raw)["step"] == "awaiting_phone"


@pytest.mark.asyncio
async def test_valid_phone_with_state_invokes_handle_phone():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_text_handler = _handler_by_name(bot.message_handlers, "otp_text_handler")
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(helper_otp_state_key(user_id), json.dumps({"step": "awaiting_phone"}))
    msg = _dev_message(text="+989123456789")

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard._handle_phone", AsyncMock()) as handle_phone,
    ):
        await otp_text_handler(SimpleNamespace(), msg)

    handle_phone.assert_awaited_once()


@pytest.mark.asyncio
async def test_otp_sending_code_reply_before_send_code():
    from app.handlers.helper_otp_wizard import _handle_phone

    user_id = settings.DEVELOPER_ID
    msg = _dev_message(text="+989123456789")
    state = {"step": "awaiting_phone"}
    events: list[str] = []

    async def _track_reply(text, **kwargs):  # noqa: ANN001, ANN002
        if t("fa", "admin.helpers.otp_phone_received") in text:
            events.append("phone_received")

    msg.reply = AsyncMock(side_effect=_track_reply)

    dup_result = MagicMock()
    dup_result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=dup_result)

    mock_client = AsyncMock()

    async def _connect():
        events.append("connect")

    async def _send_code(*_args, **_kwargs):
        events.append("send_code")
        return SimpleNamespace(phone_code_hash="hash")

    mock_client.connect = AsyncMock(side_effect=_connect)
    mock_client.send_code = AsyncMock(side_effect=_send_code)
    mock_client.disconnect = AsyncMock()
    identity = SimpleNamespace(credential_id=7, device_profile_id=11)
    identity_state = {
        "app_api_id": 67890,
        "app_api_hash_enc": "enc_api_hash",
        "app_credential_id": 7,
        "device_profile_id": 11,
        "fp_device": "Pixel",
        "fp_system": "Android 14",
        "fp_app": "10.0",
        "fp_lang": "en",
        "fp_system_lang": "en-US",
    }

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=session),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.Client", return_value=mock_client),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=_FakeRedis())),
    ):
        await _handle_phone(SimpleNamespace(), msg, state)

    assert "phone_received" in events
    assert events.index("phone_received") < events.index("connect")
    assert events.index("connect") < events.index("send_code")


@pytest.mark.asyncio
async def test_no_state_phone_replies_session_expired():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_text_handler = _handler_by_name(bot.message_handlers, "otp_text_handler")
    redis = _FakeRedis()
    await redis.set(
        wizard_return_key(settings.DEVELOPER_ID),
        TOKEN_HELPER_HOME,
    )
    msg = _dev_message(text="+989123456789")

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await otp_text_handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.otp_session_expired") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_other_wizard_phone_replies_other_wizard_active():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_text_handler = _handler_by_name(bot.message_handlers, "otp_text_handler")
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        helper_proxy_state_key(user_id),
        json.dumps({"step": "awaiting_proxy", "helper_id": 12}),
    )
    msg = _dev_message(text="+989123456789")

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await otp_text_handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.otp_other_wizard_active") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_invalid_phone_with_state_replies_fail_phone():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_text_handler = _handler_by_name(bot.message_handlers, "otp_text_handler")
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(helper_otp_state_key(user_id), json.dumps({"step": "awaiting_phone"}))
    msg = _dev_message(text="not-a-phone")

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await otp_text_handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.otp_fail_phone") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_cancel_with_state_clears_and_defers_reply_to_command_handler():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_text_handler = _handler_by_name(bot.message_handlers, "otp_text_handler")
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(helper_otp_state_key(user_id), json.dumps({"step": "awaiting_phone"}))
    msg = _dev_message(text="/cancel")

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=redis)),
    ):
        await otp_text_handler(SimpleNamespace(), msg)

    assert helper_otp_state_key(user_id) in redis.last_deleted
    assert helper_proxy_state_key(user_id) in redis.last_deleted
    msg.reply.assert_not_awaited()
    msg.stop_propagation.assert_not_awaited()


@pytest.mark.asyncio
async def test_contact_awaiting_phone_replies_phone_as_text():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_contact_handler = _handler_by_name(bot.message_handlers, "otp_contact_handler")
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(helper_otp_state_key(user_id), json.dumps({"step": "awaiting_phone"}))
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        contact=SimpleNamespace(phone_number="+989123456789"),
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
    )

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await otp_contact_handler(SimpleNamespace(), msg)

    msg.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.otp_phone_as_text") in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_non_dev_otp_start_callback_denied():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_start = _handler_by_name(bot.callback_handlers, "otp_start")
    query = _dev_query(user_id=900001)

    with (
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()) as clear_state,
        patch("app.handlers.helper_otp_wizard._set_state", AsyncMock()) as set_state,
    ):
        await otp_start(SimpleNamespace(stop_listening=AsyncMock()), query)

    clear_state.assert_not_awaited()
    set_state.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")


@pytest.mark.asyncio
async def test_otp_start_clears_stale_proxy_key():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    otp_start = _handler_by_name(bot.callback_handlers, "otp_start")
    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(
        helper_proxy_state_key(user_id),
        json.dumps({"step": "awaiting_proxy", "helper_id": 55}),
    )
    query = _dev_query()
    client = SimpleNamespace(stop_listening=AsyncMock())

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.remember_return_token", AsyncMock()),
    ):
        await otp_start.__wrapped__(client, query)

    assert await redis.get(helper_proxy_state_key(user_id)) is None


def test_handler_registration_smoke():
    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    names = {fn.__name__ for fn in bot.message_handlers}
    assert "otp_text_handler" in names
    assert "otp_contact_handler" in names


def test_hlp_add_otp_callback_unchanged():
    assert CB["HLP_ADD_OTP"] == "hlp:add:otp"
