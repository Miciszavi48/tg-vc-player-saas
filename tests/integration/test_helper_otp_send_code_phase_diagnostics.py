from __future__ import annotations

import json
import re
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger

from app.config.settings import settings
from app.services import helper_otp_pre_auth_registry
from app.utils.i18n import t
from app.utils.redis_keys import helper_otp_state_key
from app.utils.ui import CB


class _FakeRedis:
    def __init__(self, *, fail_set: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.fail_set = fail_set

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        if self.fail_set:
            raise RuntimeError("redis_set_failed")
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
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


def _identity_bundle():
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
    return identity, identity_state


def _capture_logs():
    buffer = StringIO()
    handler_id = logger.add(buffer, format="{message}", level="DEBUG")
    return buffer, handler_id


@pytest.fixture(autouse=True)
def _reset_registry():
    helper_otp_pre_auth_registry.clear_all()
    yield
    helper_otp_pre_auth_registry.clear_all()


@pytest.mark.asyncio
async def test_successful_send_code_prompts_for_code_and_uses_registry():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))
    pyrogram_client.export_session_string = AsyncMock()
    pyrogram_client.disconnect = AsyncMock()

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

    pyrogram_client.export_session_string.assert_not_called()
    assert helper_otp_pre_auth_registry.has(settings.DEVELOPER_ID)
    saved = json.loads(await redis.get(helper_otp_state_key(settings.DEVELOPER_ID)) or "{}")
    assert saved["step"] == "awaiting_code"
    assert "pre_auth_session_enc" not in saved
    assert t("fa", "admin.helpers.otp_ask_code") in msg.reply.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_phone_code_hash_stored_in_redis():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_xyz"))

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

    saved = json.loads(await redis.get(helper_otp_state_key(settings.DEVELOPER_ID)) or "{}")
    assert saved["phone_code_hash"] == "hash_xyz"


@pytest.mark.asyncio
async def test_missing_phone_code_hash_maps_to_post_failure():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash=""))
    pyrogram_client.disconnect = AsyncMock()

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

    assert t("fa", "admin.helpers.otp_send_code_post_failure") in msg.reply.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_registry_store_failure_logs_phase_and_post_failure_message():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))
    pyrogram_client.disconnect = AsyncMock()
    buffer, handler_id = _capture_logs()

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=_phone_check_session()),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.Client", return_value=pyrogram_client),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
        patch(
            "app.handlers.helper_otp_wizard.helper_otp_pre_auth_registry.put",
            AsyncMock(side_effect=RuntimeError("registry_failed")),
        ),
    ):
        await _handle_phone(AsyncMock(), msg, {"step": "awaiting_phone"})

    logs = buffer.getvalue()
    logger.remove(handler_id)
    assert "pre_auth_registry_store_started_failed" in logs
    assert "_RegistryStoreError" in logs
    assert t("fa", "admin.helpers.otp_send_code_post_failure") in msg.reply.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_encrypt_not_called_on_phone_step():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=_phone_check_session()),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.Client", return_value=pyrogram_client),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", side_effect=AssertionError("encrypt_on_phone")),
    ):
        await _handle_phone(AsyncMock(), msg, {"step": "awaiting_phone"})

    assert t("fa", "admin.helpers.otp_ask_code") in msg.reply.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_redis_state_store_failure_maps_to_state_store_failed():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis(fail_set=True)
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))
    buffer, handler_id = _capture_logs()

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

    logs = buffer.getvalue()
    logger.remove(handler_id)
    assert "wizard_state_store_started_failed" in logs
    assert t("fa", "admin.helpers.otp_state_store_failed") in msg.reply.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_mark_used_failure_still_prompts_for_code():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=_phone_check_session()),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.Client", return_value=pyrogram_client),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
        patch(
            "app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used",
            AsyncMock(side_effect=RuntimeError("mark_failed")),
        ),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
    ):
        await _handle_phone(AsyncMock(), msg, {"step": "awaiting_phone"})

    assert t("fa", "admin.helpers.otp_ask_code") in msg.reply.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_disconnect_failure_on_error_path_is_non_blocking():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(side_effect=RuntimeError("send_failed"))
    pyrogram_client.disconnect = AsyncMock(side_effect=RuntimeError("disconnect_failed"))
    buffer, handler_id = _capture_logs()

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

    logs = buffer.getvalue()
    logger.remove(handler_id)
    assert "disconnect_failed" not in logs
    assert "disconnected=False" in logs or "cleanup step=send_code" in logs
    assert t("fa", "admin.helpers.otp_fail_unknown") in msg.reply.call_args_list[-1].args[0]


@pytest.mark.asyncio
async def test_exception_logs_include_phase_and_class():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(side_effect=RuntimeError("boom"))
    pyrogram_client.disconnect = AsyncMock()
    buffer, handler_id = _capture_logs()

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

    logs = buffer.getvalue()
    logger.remove(handler_id)
    assert "send_code_started_failed" in logs or "send_code_completed_failed" in logs
    assert "RuntimeError" in logs


def test_logs_do_not_contain_full_phone_session_or_api_hash():
    from app.handlers.helper_otp_wizard import _log_auth_event

    buffer, handler_id = _capture_logs()
    _log_auth_event(
        step="unknown",
        user_id=77,
        phone="+989123456789",
        exc=RuntimeError("secret_hash 12345 password session_string"),
        has_phone_code_hash=True,
        has_pre_auth_session=True,
        session_saved=False,
        credential_id=7,
        device_profile_id=11,
        level="warning",
    )
    logs = buffer.getvalue()
    logger.remove(handler_id)
    assert "+989123456789" not in logs
    assert "12345" not in logs
    assert "password" not in logs
    assert "secret_hash" not in logs
    assert "session_string" not in logs
    assert "***789" in logs


def test_helper_otp_event_logger_masks_phone_and_secret_inputs():
    from app.handlers.helper_otp_wizard import _log_helper_otp_event

    buffer, handler_id = _capture_logs()
    _log_helper_otp_event(
        "helper_otp.otp.input.received",
        user_id=77,
        phone="+989123456789",
        result="received",
        otp_length=5,
        raw_otp="12345",
        password="super-secret",
        session_string="session-secret",
        api_hash="api-hash-secret",
        level="info",
    )
    logs = buffer.getvalue()
    logger.remove(handler_id)
    assert "+989123456789" not in logs
    assert "+989***6789" not in logs
    assert "***789" in logs
    assert "raw_otp" not in logs
    assert "12345" not in logs
    assert "password" not in logs
    assert "super-secret" not in logs
    assert "session-secret" not in logs
    assert "api-hash-secret" not in logs


def test_otp_logger_calls_use_brace_placeholders_not_percent():
    from pathlib import Path

    source = Path("app/handlers/helper_otp_wizard.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "logger." in line and ("warning(" in line or "info(" in line):
            assert "%s" not in line and "%d" not in line, f"percent-style log line: {line}"


@pytest.mark.asyncio
async def test_happy_path_does_not_send_generic_login_failed():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity, identity_state = _identity_bundle()
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))

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

    replies = " ".join(call.args[0] for call in msg.reply.call_args_list)
    assert t("fa", "admin.helpers.otp_fail_unknown") not in replies


def test_callback_data_unchanged():
    assert CB["HLP_ADD_OTP"] == "hlp:add:otp"
    assert re.search(r"hlp:add:otp", json.dumps({"cb": CB["HLP_ADD_OTP"]}))
