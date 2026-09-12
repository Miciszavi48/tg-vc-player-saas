from __future__ import annotations

import json
import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.utils.redis_keys import helper_otp_state_key

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


def _mock_message(user_id: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        text=text,
        reply=AsyncMock(),
        chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
    )


@pytest.mark.asyncio
async def test_otp_2fa_branch_persists_encrypted_pending_session_and_disconnects():
    from pyrogram.errors import SessionPasswordNeeded

    from app.handlers.helper_otp_wizard import _handle_code

    user_id = settings.DEVELOPER_ID
    msg = _mock_message(user_id, "1 2 3 4 5")
    state = {
        "step": "awaiting_code",
        "phone": "+989123456789",
        "phone_code_hash": "hash_abc",
        "fp_device": "Pixel 8",
        "fp_system": "Android 14",
        "fp_app": "10.14.5",
        "fp_lang": "en",
        "pre_auth_session_enc": "enc_pre_auth",
    }

    r = _FakeRedis()

    mock_client = AsyncMock()
    mock_client.connect = AsyncMock()
    mock_client.sign_in = AsyncMock(side_effect=SessionPasswordNeeded("need 2fa"))
    mock_client.export_session_string = AsyncMock(return_value="BQ_partial_pending")
    mock_client.disconnect = AsyncMock()

    with (
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=r)),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="BQ_pre_auth"),
        patch("app.handlers.helper_otp_wizard._build_otp_client", return_value=mock_client),
        patch("app.handlers.helper_otp_wizard.HelperPoolService.encrypt_session", return_value="enc_pending"),
    ):
        await _handle_code(AsyncMock(), msg, state)

    saved = await r.get(helper_otp_state_key(user_id))
    assert saved is not None
    saved_state = json.loads(saved)
    assert saved_state["step"] == "awaiting_2fa"
    assert saved_state["pending_session_enc"] == "enc_pending"
    mock_client.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_otp_2fa_step_uses_resumed_session_string_for_check_password():
    from pyrogram.errors import PasswordHashInvalid

    from app.handlers.helper_otp_wizard import _handle_2fa

    user_id = settings.DEVELOPER_ID
    msg = _mock_message(user_id, "wrong_password")
    state = {
        "step": "awaiting_2fa",
        "phone": "+989123456789",
        "phone_code_hash": "hash_abc",
        "fp_device": "Pixel 8",
        "fp_system": "Android 14",
        "fp_app": "10.14.5",
        "fp_lang": "en",
        "pending_session_enc": "enc_pending",
    }

    mock_client = AsyncMock()
    mock_client.connect = AsyncMock()
    mock_client.check_password = AsyncMock(side_effect=PasswordHashInvalid("bad pw"))
    mock_client.disconnect = AsyncMock()

    with (
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="BQ_partial_pending"),
        patch("app.handlers.helper_otp_wizard._build_otp_client", return_value=mock_client) as build_client,
    ):
        await _handle_2fa(AsyncMock(), msg, state)

    assert build_client.call_args.kwargs.get("session_string") == "BQ_partial_pending"
    mock_client.check_password.assert_awaited_once()
    mock_client.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_otp_2fa_missing_pending_session_shows_expired_and_clears_state():
    from app.handlers.helper_otp_wizard import _handle_2fa

    user_id = settings.DEVELOPER_ID
    msg = _mock_message(user_id, "pass")
    state = {
        "step": "awaiting_2fa",
        "phone": "+989123456789",
        "phone_code_hash": "hash_abc",
        "fp_lang": "en",
    }

    r = _FakeRedis()
    await r.set(helper_otp_state_key(user_id), json.dumps(state), ex=300)

    with patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=r)):
        await _handle_2fa(AsyncMock(), msg, state)

    assert await r.get(helper_otp_state_key(user_id)) is None
    msg.reply.assert_awaited()
