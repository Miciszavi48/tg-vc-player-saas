from __future__ import annotations

import json
import logging
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import sys

import pytest

from app.config.settings import settings
from app.services import helper_otp_pre_auth_registry
from app.utils.i18n import t
from app.utils.redis_keys import helper_otp_state_key
from app.utils.ui import CB

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.last_deleted: tuple[str, ...] = ()

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        self.last_deleted = tuple(keys)
        removed = 0
        for key in keys:
            if key in self.store:
                removed += 1
                self.store.pop(key, None)
        return removed


def _msg(user_id: int = 42, text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        text=text,
        reply=AsyncMock(),
        delete=AsyncMock(),
        chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
    )


def _phone_check_session(existing: object | None = None):
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    return session


def _db_insert_session(*, duplicate_identity: object | None = None, assigned_id: int = 77):
    added: list[object] = []
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_ctx)

    result = MagicMock()
    result.scalar_one_or_none.return_value = duplicate_identity
    session.execute = AsyncMock(return_value=result)

    def _add(obj):
        added.append(obj)

    async def _flush():
        if added and getattr(added[-1], "id", None) is None:
            setattr(added[-1], "id", assigned_id)

    session.add = MagicMock(side_effect=_add)
    session.flush = AsyncMock(side_effect=_flush)
    return session, added


def _state(**extra) -> dict:
    state = {
        "step": "awaiting_code",
        "phone": "+989123456789",
        "phone_code_hash": "hash_abc",
        "pre_auth_session_enc": "enc_pre_auth",
        "fp_device": "Pixel 8",
        "fp_system": "Android 14",
        "fp_app": "10.14.5",
        "fp_lang": "en",
        "fp_system_lang": "en",
    }
    state.update(extra)
    return state


def _client_for_code(side_effect=None):
    client = AsyncMock()
    client.connect = AsyncMock()
    client.sign_in = AsyncMock(side_effect=side_effect)
    client.export_session_string = AsyncMock(return_value="BQ_partial_pending")
    client.disconnect = AsyncMock()
    return client


class _FakeFloodWait(Exception):
    value = 42


@pytest.fixture(autouse=True)
def _reset_pre_auth_registry():
    helper_otp_pre_auth_registry.clear_all()
    yield
    helper_otp_pre_auth_registry.clear_all()


async def _register_pre_auth_client(
    user_id: int,
    client,
    *,
    phone: str = "+989123456789",
    phone_code_hash: str = "hash_abc",
) -> None:
    await helper_otp_pre_auth_registry.put(
        user_id,
        client=client,
        phone=phone,
        phone_code_hash=phone_code_hash,
    )


@pytest.mark.asyncio
async def test_phone_input_calls_send_code_and_stores_phone_code_hash():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))
    pyrogram_client.disconnect = AsyncMock()
    identity = SimpleNamespace(credential_id=7, device_profile_id=11)
    identity_state = {
        "app_api_id": 67890,
        "app_api_hash_enc": "enc_api_hash",
        "app_credential_id": 7,
        "device_profile_id": 11,
        "fp_device": "Pixel 8",
        "fp_system": "Android 14",
        "fp_app": "10.14.5",
        "fp_lang": "en",
        "fp_system_lang": "en-US",
    }

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=_phone_check_session()),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.Client", return_value=pyrogram_client),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
    ):
        await _handle_phone(AsyncMock(), msg, {"step": "awaiting_phone"})

    pyrogram_client.send_code.assert_awaited_once_with("+989123456789")
    pyrogram_client.disconnect.assert_not_awaited()
    assert helper_otp_pre_auth_registry.has(settings.DEVELOPER_ID)
    raw = await redis.get(helper_otp_state_key(settings.DEVELOPER_ID))
    saved = json.loads(raw or "{}")
    assert saved["step"] == "awaiting_code"
    assert saved["phone"] == "+989123456789"
    assert saved["phone_code_hash"] == "hash_saved"
    assert "pre_auth_session_enc" not in saved
    assert saved["app_credential_id"] == 7
    assert saved["device_profile_id"] == 11


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_code", "normalized"),
    [
        ("1 2 3 4 5", "12345"),
        ("1-2-3-4-5", "12345"),
        ("۱ ۲ ۳ ۴ ۵", "12345"),
    ],
)
async def test_code_input_normalizes_supported_telegram_code_formats(raw_code: str, normalized: str):
    from app.handlers.helper_otp_wizard import _handle_code

    msg = _msg(settings.DEVELOPER_ID, raw_code)
    client = _client_for_code()
    await _register_pre_auth_client(settings.DEVELOPER_ID, client)
    with patch("app.handlers.helper_otp_wizard._finalize_helper", AsyncMock()) as finalize:
        await _handle_code(AsyncMock(), msg, _state())

    client.sign_in.assert_awaited_once_with("+989123456789", "hash_abc", normalized)
    finalize.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_phone_code_hash_replies_state_expired_not_generic():
    from app.handlers.helper_otp_wizard import _handle_code

    redis = _FakeRedis()
    user_id = settings.DEVELOPER_ID
    await redis.set(helper_otp_state_key(user_id), json.dumps({"step": "awaiting_code"}))
    msg = _msg(user_id, "12345")

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await _handle_code(AsyncMock(), msg, {"step": "awaiting_code", "phone": "+989123456789"})

    assert t("fa", "admin.helpers.otp_auth_context_lost") in msg.reply.call_args_list[-1].args[0]
    assert await redis.get(helper_otp_state_key(user_id)) is None


@pytest.mark.asyncio
async def test_invalid_code_format_replies_visible_format_string_before_auth_call():
    from app.handlers.helper_otp_wizard import _handle_code

    msg = _msg(settings.DEVELOPER_ID, "12-ab")
    with patch("app.handlers.helper_otp_wizard._build_otp_client", return_value=AsyncMock()) as build_client:
        await _handle_code(AsyncMock(), msg, _state())

    assert t("fa", "admin.helpers.otp_fail_code_format") in msg.reply.call_args_list[-1].args[0]
    build_client.assert_not_called()
    msg.delete.assert_awaited()


@pytest.mark.asyncio
async def test_phone_code_invalid_maps_to_invalid_code_string_and_keeps_state():
    from pyrogram.errors import PhoneCodeInvalid
    from app.handlers.helper_otp_wizard import _handle_code

    redis = _FakeRedis()
    msg = _msg(42, "12345")
    client = _client_for_code(PhoneCodeInvalid("bad"))
    await _register_pre_auth_client(42, client, phone="+989123456789")
    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await _handle_code(AsyncMock(), msg, _state(phone="+989123456789"))

    assert t("fa", "admin.helpers.otp_fail_code") in msg.reply.call_args_list[-1].args[0]
    assert helper_otp_state_key(42) not in redis.last_deleted
    assert helper_otp_pre_auth_registry.has(42)


@pytest.mark.asyncio
async def test_phone_code_expired_maps_to_expired_code_string_and_clears_state():
    from pyrogram.errors import PhoneCodeExpired
    from app.handlers.helper_otp_wizard import _handle_code

    redis = _FakeRedis()
    msg = _msg(42, "12345")
    client = _client_for_code(PhoneCodeExpired("expired"))
    await _register_pre_auth_client(42, client, phone="+989123456789")
    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await _handle_code(AsyncMock(), msg, _state(phone="+989123456789"))

    assert t("fa", "admin.helpers.otp_fail_code_expired") in msg.reply.call_args_list[-1].args[0]
    assert helper_otp_state_key(42) in redis.last_deleted
    assert not helper_otp_pre_auth_registry.has(42)


@pytest.mark.asyncio
async def test_auth_key_unregistered_maps_to_auth_session_error():
    from pyrogram.errors import AuthKeyUnregistered
    from app.handlers.helper_otp_wizard import _handle_code

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "12345")
    client = _client_for_code(AuthKeyUnregistered("auth lost"))
    await _register_pre_auth_client(settings.DEVELOPER_ID, client)
    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await _handle_code(AsyncMock(), msg, _state())

    assert t("fa", "admin.helpers.otp_fail_auth_session") in msg.reply.call_args_list[-1].args[0]
    assert helper_otp_state_key(settings.DEVELOPER_ID) in redis.last_deleted
    assert not helper_otp_pre_auth_registry.has(settings.DEVELOPER_ID)


@pytest.mark.asyncio
async def test_session_password_needed_moves_to_2fa_step_and_prompts_password():
    from pyrogram.errors import SessionPasswordNeeded
    from app.handlers.helper_otp_wizard import _handle_code

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "12345")
    client = _client_for_code(SessionPasswordNeeded("need 2fa"))
    await _register_pre_auth_client(settings.DEVELOPER_ID, client)
    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await _handle_code(AsyncMock(), msg, _state())

    raw = await redis.get(helper_otp_state_key(settings.DEVELOPER_ID))
    saved = json.loads(raw or "{}")
    assert saved["step"] == "awaiting_2fa"
    assert "pending_session_enc" in saved
    assert t("fa", "admin.helpers.otp_ask_2fa") in msg.reply.call_args_list[-1].args[0]
    assert not helper_otp_pre_auth_registry.has(settings.DEVELOPER_ID)


@pytest.mark.asyncio
async def test_password_input_calls_check_password():
    from app.handlers.helper_otp_wizard import _handle_2fa

    msg = _msg(settings.DEVELOPER_ID, "correct-password")
    client = AsyncMock()
    client.connect = AsyncMock()
    client.check_password = AsyncMock()
    client.disconnect = AsyncMock()
    await _register_pre_auth_client(settings.DEVELOPER_ID, client)
    with patch("app.handlers.helper_otp_wizard._finalize_helper", AsyncMock()) as finalize:
        await _handle_2fa(AsyncMock(), msg, _state(step="awaiting_2fa"))

    client.check_password.assert_awaited_once_with("correct-password")
    finalize.assert_awaited_once()


@pytest.mark.asyncio
async def test_password_hash_invalid_maps_to_invalid_password():
    from pyrogram.errors import PasswordHashInvalid
    from app.handlers.helper_otp_wizard import _handle_2fa

    msg = _msg(settings.DEVELOPER_ID, "wrong-password")
    client = AsyncMock()
    client.connect = AsyncMock()
    client.check_password = AsyncMock(side_effect=PasswordHashInvalid("bad password"))
    client.disconnect = AsyncMock()
    await _register_pre_auth_client(settings.DEVELOPER_ID, client)
    await _handle_2fa(AsyncMock(), msg, _state(step="awaiting_2fa"))

    assert t("fa", "admin.helpers.otp_fail_2fa") in msg.reply.call_args_list[-1].args[0]
    assert helper_otp_pre_auth_registry.has(settings.DEVELOPER_ID)


@pytest.mark.asyncio
async def test_successful_finalize_exports_saves_session_and_registers_helper():
    from app.handlers.helper_otp_wizard import _finalize_helper

    msg = _msg(settings.DEVELOPER_ID, "")
    temp_client = AsyncMock()
    temp_client.get_me = AsyncMock(return_value=SimpleNamespace(id=501, username="helper501", first_name="Helper"))
    temp_client.export_session_string = AsyncMock(return_value="plain-session")
    temp_client.disconnect = AsyncMock()
    db_session, added = _db_insert_session(assigned_id=55)

    await _register_pre_auth_client(settings.DEVELOPER_ID, temp_client)
    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=db_session),
        patch("app.handlers.helper_otp_wizard.acquire_lock", AsyncMock(return_value="lock-token")),
        patch("app.handlers.helper_otp_wizard.release_lock", AsyncMock(return_value=True)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc-session"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.fingerprint_session", return_value="fp-session"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.is_duplicate_session", AsyncMock(return_value=False)),
        patch("app.handlers.helper_otp_wizard.helper_event_repo.log_event", AsyncMock()),
        patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()),
    ):
        await _finalize_helper(AsyncMock(), msg, temp_client, _state(phone="+989123456789"))

    temp_client.export_session_string.assert_awaited_once()
    temp_client.disconnect.assert_awaited()
    assert not helper_otp_pre_auth_registry.has(settings.DEVELOPER_ID)
    assert added
    helper = added[0]
    assert helper.phone == "+989123456789"
    assert helper.session_string_enc == "enc-session"
    assert helper.session_fingerprint == "fp-session"
    assert helper.tg_user_id == 501


@pytest.mark.asyncio
async def test_flood_wait_maps_to_flood_message_and_disconnects():
    from app.handlers.helper_otp_wizard import _handle_code

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "12345")
    client = _client_for_code(_FakeFloodWait("wait"))
    await _register_pre_auth_client(settings.DEVELOPER_ID, client)
    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)):
        await _handle_code(AsyncMock(), msg, _state())

    assert "42" in msg.reply.call_args_list[-1].args[0]
    assert t("fa", "admin.helpers.otp_fail_flood", wait=42) in msg.reply.call_args_list[-1].args[0]
    assert helper_otp_state_key(settings.DEVELOPER_ID) in redis.last_deleted
    assert not helper_otp_pre_auth_registry.has(settings.DEVELOPER_ID)


@pytest.mark.asyncio
async def test_unknown_exception_logs_class_name_and_sends_safe_generic():
    from io import StringIO

    from loguru import logger
    from app.handlers.helper_otp_wizard import _handle_code

    class AuthExploded(Exception):
        pass

    buffer = StringIO()
    handler_id = logger.add(buffer, format="{message}", level="DEBUG")
    msg = _msg(42, "12345")
    client = _client_for_code(AuthExploded("secret 12345 +989123456789 password"))
    await _register_pre_auth_client(42, client, phone="+989123456789")
    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=_FakeRedis())):
        await _handle_code(AsyncMock(), msg, _state(phone="+989123456789"))

    log_text = buffer.getvalue()
    logger.remove(handler_id)
    assert t("fa", "admin.helpers.otp_fail_unknown") in msg.reply.call_args_list[-1].args[0]
    assert "AuthExploded" in log_text
    assert "+989123456789" not in log_text
    assert "12345" not in log_text
    assert "password" not in log_text
    assert "secret" not in log_text


def test_app_info_loader_ignores_invalid_entries_and_falls_back_safely(tmp_path: Path):
    from app.utils import device_spoof

    invalid_path = tmp_path / "app_information.json"
    invalid_path.write_text(json.dumps([{"device_model": "Only Device"}, "bad-entry"]), encoding="utf-8")

    assert device_spoof.load_device_fingerprints(invalid_path) == []
    with patch("app.utils.device_spoof._profile_candidates", return_value=[invalid_path]):
        picked = device_spoof.pick_random_fingerprint()

    assert picked in device_spoof._POOL


def test_app_info_loader_consumes_json_list_with_required_keys(tmp_path: Path):
    from app.utils.device_spoof import load_device_fingerprints

    path = tmp_path / "app_information.json"
    path.write_text(
        json.dumps(
            [
                {
                    "device_model": "Google Pixel 8 Pro",
                    "system_version": "Android 14",
                    "app_version": "10.14.5",
                    "lang_code": "fa",
                    "system_lang_code": "fa",
                }
            ]
        ),
        encoding="utf-8",
    )

    profiles = load_device_fingerprints(path)
    assert len(profiles) == 1
    assert profiles[0].device_model == "Google Pixel 8 Pro"
    assert profiles[0].system_version == "Android 14"
    assert profiles[0].app_version == "10.14.5"
    assert profiles[0].lang_code == "fa"
    assert profiles[0].system_lang_code == "fa"


def test_helper_otp_callback_data_unchanged():
    assert CB["HLP_ADD_OTP"] == "hlp:add:otp"
    assert CB["HLP_IMPORT_SESSION"] == "hlp:import"
