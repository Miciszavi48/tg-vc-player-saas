"""Tests for helper pool bug fixes and OTP wizard infrastructure."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# Bug 1: Redis key for idem:helper:join
# ═══════════════════════════════════════════════════════════════════════════

def test_helper_join_idem_key_in_registry():
    from app.utils.redis_keys import helper_join_idem_key, instance_key

    # Instance-owned key: the logical name is stable, the namespace is per instance.
    assert helper_join_idem_key(5, -100) == instance_key("idem:helper:join:5:-100")
    assert helper_join_idem_key(5, -100).endswith("idem:helper:join:5:-100")


def test_helper_otp_state_key_in_registry():
    from app.utils.redis_keys import (
        TTL_HELPER_OTP,
        helper_otp_state_key,
        helper_proxy_state_key,
        instance_key,
    )

    assert helper_otp_state_key(42) == instance_key("wz:helper_otp:42")
    assert helper_proxy_state_key(42) == instance_key("wz:helper_proxy:42")
    assert TTL_HELPER_OTP == 300


# ═══════════════════════════════════════════════════════════════════════════
# Bug 2: Active calls counter
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_increment_active_calls():
    from app.services.helper_pool_service import HelperPoolService

    mock_binding = MagicMock()
    mock_binding.helper_account_id = 7
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_binding

    with patch("app.services.helper_pool_service.async_session") as mock_sess:
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.execute = AsyncMock(return_value=mock_result)
        sess.begin = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
        mock_sess.return_value = sess

        await HelperPoolService.increment_active_calls(-100)
        assert sess.execute.await_count == 2


@pytest.mark.asyncio
async def test_decrement_active_calls():
    from app.services.helper_pool_service import HelperPoolService

    mock_binding = MagicMock()
    mock_binding.helper_account_id = 7
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_binding

    with patch("app.services.helper_pool_service.async_session") as mock_sess:
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.execute = AsyncMock(return_value=mock_result)
        sess.begin = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
        mock_sess.return_value = sess

        await HelperPoolService.decrement_active_calls(-100)
        assert sess.execute.await_count == 2


@pytest.mark.asyncio
async def test_increment_noop_on_no_binding():
    from app.services.helper_pool_service import HelperPoolService

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    with patch("app.services.helper_pool_service.async_session") as mock_sess:
        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.execute = AsyncMock(return_value=mock_result)
        sess.begin = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
        mock_sess.return_value = sess

        await HelperPoolService.increment_active_calls(-999)
        assert sess.execute.await_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Bug 3: Health watchdog upgrade
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_health_watchdog_uses_get_me():
    """Watchdog should attempt full login test, not just decryption."""
    from app.scheduler import helper_health_watchdog

    mock_helper = SimpleNamespace(
        id=1, status="active", session_string_enc="encrypted_data")

    with (
        patch("app.scheduler.HelperPoolService") as mock_svc,
        patch("app.scheduler.async_session"),
        patch("app.scheduler.settings") as mock_settings,
    ):
        mock_settings.API_ID = 12345
        mock_settings.API_HASH = "test_hash"
        mock_svc.get_all_helpers = AsyncMock(return_value=[mock_helper])
        mock_svc.get_helper_session = AsyncMock(return_value="session_str")

        mock_client_instance = AsyncMock()
        mock_client_instance.get_me = AsyncMock(
            return_value=SimpleNamespace(id=111, username="test", first_name="Test"))
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        with patch("pyrogram.Client", return_value=mock_client_instance):
            await helper_health_watchdog()
            mock_client_instance.get_me.assert_awaited_once()


@pytest.mark.asyncio
async def test_health_watchdog_fatal_error_disables():
    """Fatal auth errors should disable (not just quarantine) the helper."""
    from app.scheduler import helper_health_watchdog

    mock_helper = SimpleNamespace(
        id=1, status="active", session_string_enc="enc")

    class FakeAuthKeyUnregistered(Exception):
        pass

    FakeAuthKeyUnregistered.__name__ = "AuthKeyUnregistered"

    with (
        patch("app.scheduler.HelperPoolService") as mock_svc,
        patch("app.scheduler.async_session") as mock_as,
        patch("app.scheduler.settings") as mock_settings,
    ):
        mock_settings.API_ID = 12345
        mock_settings.API_HASH = "test_hash"
        mock_svc.get_all_helpers = AsyncMock(return_value=[mock_helper])
        mock_svc.get_helper_session = AsyncMock(return_value="session_str")

        mock_client_instance = AsyncMock()
        mock_client_instance.get_me = AsyncMock(side_effect=FakeAuthKeyUnregistered("dead"))
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=False)

        sess = AsyncMock()
        sess.__aenter__ = AsyncMock(return_value=sess)
        sess.__aexit__ = AsyncMock(return_value=False)
        sess.execute = AsyncMock()
        sess.begin = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
        mock_as.return_value = sess

        with patch("pyrogram.Client", return_value=mock_client_instance):
            await helper_health_watchdog()
            assert sess.execute.await_count >= 1


# ═══════════════════════════════════════════════════════════════════════════
# OTP Wizard infrastructure
# ═══════════════════════════════════════════════════════════════════════════

def test_otp_cb_constant_exists():
    from app.utils.ui import CB
    assert "HLP_ADD_OTP" in CB
    assert CB["HLP_ADD_OTP"] == "hlp:add:otp"


def test_helper_home_keyboard_has_add_button():
    from app.utils.ui import CB, KeyboardFactory
    kb = KeyboardFactory.helper_home("en", active=2, disabled=0, quarantined=0)
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert CB["HLP_ADD"] in cbs


def test_otp_i18n_keys_exist():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa = load_fa_i18n()
    en = load_en_i18n()
    required = [
        "otp_ask_phone", "otp_sending_code", "otp_ask_code", "otp_ask_2fa",
        "otp_success", "otp_fail_phone", "otp_fail_code",         "otp_fail_2fa",
        "otp_2fa_expired",
        "import_phone_mismatch",
        "otp_fail_flood", "otp_fail_generic", "otp_cancelled", "otp_duplicate",
        "add_btn",
    ]
    fa_helpers = fa.get("admin", {}).get("helpers", {})
    en_helpers = en.get("admin", {}).get("helpers", {})
    for key in required:
        assert key in fa_helpers, f"Missing FA key: admin.helpers.{key}"
        assert key in en_helpers, f"Missing EN key: admin.helpers.{key}"


@pytest.mark.asyncio
async def test_otp_state_roundtrip():
    from app.handlers.helper_otp_wizard import _clear_state, _get_state, _set_state
    with patch("app.handlers.helper_otp_wizard.get_redis") as mock_redis:
        r = AsyncMock()
        mock_redis.return_value = r
        r.set = AsyncMock()
        r.get = AsyncMock(return_value=json.dumps({"step": "awaiting_phone"}))
        r.delete = AsyncMock()

        await _set_state(42, {"step": "awaiting_phone"})
        r.set.assert_called_once()

        state = await _get_state(42)
        assert state["step"] == "awaiting_phone"

        await _clear_state(42)
        r.delete.assert_called_once()


def test_phone_regex():
    from app.handlers.helper_otp_wizard import _PHONE_RE
    assert _PHONE_RE.match("+989123456789")
    assert _PHONE_RE.match("989123456789")
    assert not _PHONE_RE.match("abc")
    assert not _PHONE_RE.match("+1234")


def test_code_digit_extraction():
    from app.handlers.helper_otp_wizard import _CODE_RE
    raw = "1 2 3 4 5"
    digits = "".join(_CODE_RE.findall(raw))
    assert digits == "12345"

    raw2 = "1-2-3-4-5"
    digits2 = "".join(_CODE_RE.findall(raw2))
    assert digits2 == "12345"

    raw3 = "12345"
    digits3 = "".join(_CODE_RE.findall(raw3))
    assert digits3 == "12345"
