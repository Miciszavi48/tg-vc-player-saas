from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.redis_keys import helper_otp_state_key


def _msg(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        text=text,
        reply=AsyncMock(),
        delete=AsyncMock(),
        chat=SimpleNamespace(id=42, type=SimpleNamespace(value="private")),
    )


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.store:
                deleted += 1
                self.store.pop(key, None)
        return deleted


@pytest.mark.asyncio
async def test_import_session_stored_encrypted_in_state_only():
    from app.handlers.helper_otp_wizard import _handle_import_session

    msg = _msg("AQB" + "x" * 40)
    state = {"step": "awaiting_import_session"}
    redis = _FakeRedis()

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc_import"),
    ):
        await _handle_import_session(msg, state)

    raw = await redis.get(helper_otp_state_key(42))
    assert raw is not None
    saved = json.loads(raw)
    assert saved["step"] == "awaiting_import_phone"
    assert saved.get("import_session_enc") == "enc_import"
    assert "import_session" not in saved


@pytest.mark.asyncio
async def test_sensitive_messages_delete_attempted_for_import_otp_2fa():
    from app.handlers.helper_otp_wizard import _handle_2fa, _handle_code, _handle_import_session

    msg_import = _msg("too_short")
    msg_code = _msg("1 2")
    msg_2fa = _msg("secret")

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=_FakeRedis())):
        await _handle_import_session(msg_import, {"step": "awaiting_import_session"})

    await _handle_code(AsyncMock(), msg_code, {"step": "awaiting_code", "phone": "+9891", "phone_code_hash": "h"})
    await _handle_2fa(AsyncMock(), msg_2fa, {"step": "awaiting_2fa"})

    msg_import.delete.assert_awaited()
    msg_code.delete.assert_awaited()
    msg_2fa.delete.assert_awaited()


@pytest.mark.asyncio
async def test_delete_failure_does_not_break_flow():
    from app.handlers.helper_otp_wizard import _handle_import_session

    msg = _msg("AQB" + "x" * 40)
    msg.delete = AsyncMock(side_effect=RuntimeError("delete failed"))
    redis = _FakeRedis()

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc_import"),
    ):
        await _handle_import_session(msg, {"step": "awaiting_import_session"})

    msg.reply.assert_awaited()


@pytest.mark.asyncio
async def test_generic_errors_do_not_expose_exception_names_to_user():
    from app.handlers.helper_otp_wizard import _handle_phone

    msg = _msg("+989123456789")
    state = {"step": "awaiting_phone"}
    mock_client = AsyncMock()
    mock_client.connect = AsyncMock()
    mock_client.send_code = AsyncMock(side_effect=RuntimeError("BoomSecret"))
    mock_client.disconnect = AsyncMock()

    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    sess.execute = AsyncMock(return_value=result)
    identity = SimpleNamespace(credential_id=7, device_profile_id=11)
    identity_state = {
        "app_api_id": 67890,
        "app_api_hash_enc": "enc_api_hash",
        "app_credential_id": 7,
        "device_profile_id": 11,
        "fp_device": "Pixel",
        "fp_system": "Android 14",
        "fp_app": "10.14.5",
        "fp_lang": "en",
        "fp_system_lang": "en-US",
    }

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=sess),
        patch("app.handlers.helper_otp_wizard.Client", return_value=mock_client),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=_FakeRedis())),
    ):
        await _handle_phone(AsyncMock(), msg, state)

    text = str(msg.reply.call_args_list[-1])
    assert "BoomSecret" not in text
