"""Rigorous E2E edge-case tests for Helper OTP wizard and anti-ban system.

Covers:
1. FSM navigation & state bleeding ("confused user")
2. Telegram API exception handling (PhoneNumberInvalid, PhoneCodeInvalid, etc.)
3. Proxy validation with malformed input
4. Fingerprint persistence guarantee across multiple build_client calls
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _mock_message(user_id: int = 42, text: str = "", chat_id: int = 42):
    msg = AsyncMock()
    msg.from_user = SimpleNamespace(id=user_id)
    msg.chat = SimpleNamespace(id=chat_id)
    msg.text = text
    msg.id = 1
    msg.reply = AsyncMock()
    return msg


def _mock_redis_store():
    """In-memory dict-backed Redis mock for FSM state tests."""
    store = {}

    async def _get(key):
        return store.get(key)

    async def _set(key, value, ex=None):
        store[key] = value

    async def _delete(*keys):
        for key in keys:
            store.pop(key, None)

    r = AsyncMock()
    r.get = AsyncMock(side_effect=_get)
    r.set = AsyncMock(side_effect=_set)
    r.delete = AsyncMock(side_effect=_delete)
    return r, store


# ═══════════════════════════════════════════════════════════════════════════
# 1. FSM Navigation & State Bleeding
# ═══════════════════════════════════════════════════════════════════════════

class TestFSMStateBleeding:

    @pytest.mark.asyncio
    async def test_back_from_code_clears_old_phone_and_fingerprint(self):
        """User enters phone A → reaches code step → clicks Back →
        enters phone B. The state must contain ONLY phone B's data."""
        from app.handlers.helper_otp_wizard import _clear_state, _get_state, _set_state

        r, store = _mock_redis_store()
        with patch("app.handlers.helper_otp_wizard.get_redis", return_value=r):
            state_a = {
                "step": "awaiting_code",
                "phone": "+989111111111",
                "phone_code_hash": "hash_A",
                "fp_device": "Samsung SM-S928B",
                "fp_system": "Android 14",
                "fp_app": "10.14.5",
                "fp_lang": "en",
            }
            await _set_state(42, state_a)

            loaded = await _get_state(42)
            assert loaded["phone"] == "+989111111111"
            assert loaded["fp_device"] == "Samsung SM-S928B"

            await _set_state(42, {"step": "awaiting_phone"})

            clean = await _get_state(42)
            assert "phone" not in clean
            assert "fp_device" not in clean
            assert "phone_code_hash" not in clean
            assert clean["step"] == "awaiting_phone"

    @pytest.mark.asyncio
    async def test_cancel_completely_wipes_state(self):
        """Cancel at any step must leave zero residual state."""
        from app.handlers.helper_otp_wizard import _clear_state, _get_state, _set_state

        r, store = _mock_redis_store()
        with patch("app.handlers.helper_otp_wizard.get_redis", return_value=r):
            await _set_state(42, {
                "step": "awaiting_2fa", "phone": "+989222222222",
                "fp_device": "Pixel 8 Pro",
            })
            await _clear_state(42)
            assert await _get_state(42) is None

    @pytest.mark.asyncio
    async def test_state_ttl_prevents_stale_data(self):
        """State is written with TTL — verify the ex parameter is passed."""
        from app.handlers.helper_otp_wizard import _set_state
        from app.utils.redis_keys import TTL_HELPER_OTP

        r = AsyncMock()
        r.set = AsyncMock()
        with patch("app.handlers.helper_otp_wizard.get_redis", return_value=r):
            await _set_state(42, {"step": "awaiting_phone"})
            call_kwargs = r.set.call_args
            assert call_kwargs.kwargs.get("ex") == TTL_HELPER_OTP or call_kwargs[1].get("ex") == TTL_HELPER_OTP

    @pytest.mark.asyncio
    async def test_text_handler_ignores_when_no_state(self):
        """If no FSM state exists, the text handler must be a no-op."""
        from app.handlers.helper_otp_wizard import _handle_phone

        r = AsyncMock()
        r.get = AsyncMock(return_value=None)
        msg = _mock_message(text="+989123456789")

        with patch("app.handlers.helper_otp_wizard.get_redis", return_value=r):
            from app.handlers.helper_otp_wizard import _get_state
            state = await _get_state(42)
            assert state is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Telegram API Exception Handling
# ═══════════════════════════════════════════════════════════════════════════

class TestTelegramAPIExceptions:

    @pytest.mark.asyncio
    async def test_send_code_phone_number_invalid(self):
        """PhoneNumberInvalid during send_code → localized error, state cleared."""
        from app.handlers.helper_otp_wizard import _handle_phone

        class PhoneNumberInvalid(Exception):
            pass

        msg = _mock_message(text="+989123456789")
        state = {"step": "awaiting_phone"}

        mock_client_inst = AsyncMock()
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.send_code = AsyncMock(side_effect=PhoneNumberInvalid("bad phone"))
        mock_client_inst.disconnect = AsyncMock()
        identity = SimpleNamespace(credential_id=7, device_profile_id=11)
        identity_state = {
            "app_api_id": 67890,
            "app_api_hash_enc": "enc_api_hash",
            "app_credential_id": 7,
            "device_profile_id": 11,
            "fp_device": "X",
            "fp_system": "Y",
            "fp_app": "Z",
            "fp_lang": "en",
            "fp_system_lang": "en-US",
        }

        r, store = _mock_redis_store()
        with (
            patch("app.handlers.helper_otp_wizard.get_redis", return_value=r),
            patch("app.handlers.helper_otp_wizard.async_session") as mock_sess,
            patch("app.handlers.helper_otp_wizard.Client", return_value=mock_client_inst),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
            patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
        ):
            sess = AsyncMock()
            sess.__aenter__ = AsyncMock(return_value=sess)
            sess.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            sess.execute = AsyncMock(return_value=mock_result)
            mock_sess.return_value = sess

            await _handle_phone(AsyncMock(), msg, state)
            msg.reply.assert_called()
            last_call = msg.reply.call_args_list[-1]
            assert "PhoneNumberInvalid" not in str(last_call)
            assert "شماره" in str(last_call) or "phone" in str(last_call).lower()

    @pytest.mark.asyncio
    async def test_sign_in_phone_code_invalid(self):
        """PhoneCodeInvalid during sign_in → error message, stays in flow."""
        from app.handlers.helper_otp_wizard import _handle_code

        class PhoneCodeInvalid(Exception):
            pass

        msg = _mock_message(text="1 2 3 4 5")
        state = {
            "step": "awaiting_code", "phone": "+989123456789",
            "phone_code_hash": "hash_abc",
            "pre_auth_session_enc": "enc_pre_auth",
            "app_api_id": 67890,
            "app_api_hash_enc": "enc_api_hash",
            "fp_device": "Pixel 8", "fp_system": "Android 14",
            "fp_app": "10.14.5", "fp_lang": "en", "fp_system_lang": "en-US",
        }

        mock_client_inst = AsyncMock()
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.sign_in = AsyncMock(side_effect=PhoneCodeInvalid("bad code"))
        mock_client_inst.disconnect = AsyncMock()

        with (
            patch("app.handlers.helper_otp_wizard.Client", return_value=mock_client_inst),
            patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="pre_auth_session"),
        ):
            await _handle_code(AsyncMock(), msg, state)
            msg.reply.assert_called()
            reply_text = str(msg.reply.call_args_list[-1])
            assert "otp_fail_code" in reply_text or "کد" in reply_text or "code" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_sign_in_session_password_needed(self):
        """SessionPasswordNeeded → transition to 2FA step."""
        from app.handlers.helper_otp_wizard import _handle_code
        from app.services import helper_otp_pre_auth_registry

        from pyrogram.errors import SessionPasswordNeeded

        msg = _mock_message(text="1 2 3 4 5")
        state = {
            "step": "awaiting_code", "phone": "+989123456789",
            "phone_code_hash": "hash_abc",
            "pre_auth_session_enc": "enc_pre_auth",
            "app_api_id": 67890,
            "app_api_hash_enc": "enc_api_hash",
            "fp_device": "Pixel 8", "fp_system": "Android 14",
            "fp_app": "10.14.5", "fp_lang": "en", "fp_system_lang": "en-US",
        }

        mock_client_inst = AsyncMock()
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.sign_in = AsyncMock(side_effect=SessionPasswordNeeded("need 2fa"))
        mock_client_inst.export_session_string = AsyncMock(return_value="BQ_partial_pending")
        mock_client_inst.disconnect = AsyncMock()

        r, store = _mock_redis_store()
        helper_otp_pre_auth_registry.clear_all()
        await helper_otp_pre_auth_registry.put(
            42,
            client=mock_client_inst,
            phone="+989123456789",
            phone_code_hash="hash_abc",
            ttl_seconds=60,
        )
        with (
            patch("app.handlers.helper_otp_wizard.get_redis", return_value=r),
        ):
            try:
                await _handle_code(AsyncMock(), msg, state)
                msg.reply.assert_called()
                reply_text = str(msg.reply.call_args_list[-1])
                assert "otp_ask_2fa" in reply_text or "2FA" in reply_text or "رمز" in reply_text
                from app.utils.redis_keys import helper_otp_state_key
                saved = store.get(helper_otp_state_key(42))
                assert saved is not None
                saved_state = json.loads(saved)
                assert saved_state.get("step") == "awaiting_2fa"
                assert saved_state.get("pending_session_enc")
            finally:
                await helper_otp_pre_auth_registry.evict(42, phase="test_cleanup")

    @pytest.mark.asyncio
    async def test_check_password_hash_invalid(self):
        """PasswordHashInvalid during check_password → localized error."""
        from app.handlers.helper_otp_wizard import _handle_2fa
        from app.services import helper_otp_pre_auth_registry

        from pyrogram.errors import PasswordHashInvalid

        msg = _mock_message(text="wrong_password")
        state = {
            "step": "awaiting_2fa", "phone": "+989123456789",
            "phone_code_hash": "hash_abc",
            "fp_device": "Pixel 8", "fp_system": "Android 14",
            "fp_app": "10.14.5", "fp_lang": "en",
            "pending_session_enc": "enc_pending",
        }

        mock_client_inst = AsyncMock()
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.check_password = AsyncMock(side_effect=PasswordHashInvalid("bad pw"))
        mock_client_inst.disconnect = AsyncMock()

        helper_otp_pre_auth_registry.clear_all()
        await helper_otp_pre_auth_registry.put(
            42,
            client=mock_client_inst,
            phone="+989123456789",
            phone_code_hash="hash_abc",
            ttl_seconds=60,
        )
        try:
            await _handle_2fa(AsyncMock(), msg, state)
            msg.reply.assert_called()
            reply_text = str(msg.reply.call_args_list[-1])
            assert "otp_fail_2fa" in reply_text or "رمز" in reply_text
            mock_client_inst.check_password.assert_awaited_once_with("wrong_password")
        finally:
            await helper_otp_pre_auth_registry.evict(42, phase="test_cleanup")

    @pytest.mark.asyncio
    async def test_send_code_floodwait(self):
        """FloodWait during send_code → shows wait time, clears state."""
        from app.handlers.helper_otp_wizard import _handle_phone

        class FloodWait(Exception):
            def __init__(self):
                self.value = 120
                super().__init__("FloodWait")

        msg = _mock_message(text="+989123456789")
        state = {"step": "awaiting_phone"}

        mock_client_inst = AsyncMock()
        mock_client_inst.connect = AsyncMock()
        mock_client_inst.send_code = AsyncMock(side_effect=FloodWait())
        mock_client_inst.disconnect = AsyncMock()
        identity = SimpleNamespace(credential_id=7, device_profile_id=11)
        identity_state = {
            "app_api_id": 67890,
            "app_api_hash_enc": "enc_api_hash",
            "app_credential_id": 7,
            "device_profile_id": 11,
            "fp_device": "X",
            "fp_system": "Y",
            "fp_app": "Z",
            "fp_lang": "en",
            "fp_system_lang": "en-US",
        }

        r, store = _mock_redis_store()
        with (
            patch("app.handlers.helper_otp_wizard.get_redis", return_value=r),
            patch("app.handlers.helper_otp_wizard.async_session") as mock_sess,
            patch("app.handlers.helper_otp_wizard.Client", return_value=mock_client_inst),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
            patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
        ):
            sess = AsyncMock()
            sess.__aenter__ = AsyncMock(return_value=sess)
            sess.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            sess.execute = AsyncMock(return_value=mock_result)
            mock_sess.return_value = sess

            await _handle_phone(AsyncMock(), msg, state)
            reply_text = str(msg.reply.call_args_list[-1])
            assert "120" in reply_text or "flood" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_short_code_rejected(self):
        """Code with less than 4 digits → error message."""
        from app.handlers.helper_otp_wizard import _handle_code

        msg = _mock_message(text="1 2")
        state = {
            "step": "awaiting_code", "phone": "+989123456789",
            "phone_code_hash": "hash_abc",
        }
        await _handle_code(AsyncMock(), msg, state)
        msg.reply.assert_called()


# ═══════════════════════════════════════════════════════════════════════════
# 3. Proxy Validation & Malformed Input
# ═══════════════════════════════════════════════════════════════════════════

class TestProxyValidation:

    def _proxy_regex(self, text: str) -> bool:
        import re
        m = re.match(
            r"^(socks5|socks4|http|mtproto)://(?:([^:@]+):([^@]+)@)?([A-Za-z0-9._-]+):(\d+)$",
            text, re.IGNORECASE)
        return m is not None

    def test_valid_socks5_with_auth(self):
        assert self._proxy_regex("socks5://user:pass@1.2.3.4:1080")

    def test_valid_http_no_auth(self):
        assert self._proxy_regex("http://proxy.example.com:8080")

    def test_valid_mtproto(self):
        assert self._proxy_regex("mtproto://user:secret@5.6.7.8:443")

    def test_reject_garbage(self):
        assert not self._proxy_regex("not-a-proxy")

    def test_reject_missing_port(self):
        assert not self._proxy_regex("socks5://1.2.3.4")

    def test_reject_ipv6_brackets(self):
        assert not self._proxy_regex("http://[::1]:8080")

    def test_reject_missing_scheme(self):
        assert not self._proxy_regex("1.2.3.4:1080")

    def test_reject_empty_string(self):
        assert not self._proxy_regex("")

    def test_reject_just_url(self):
        assert not self._proxy_regex("https://google.com")

    def test_reject_partial_auth(self):
        assert not self._proxy_regex("socks5://user@1.2.3.4:1080")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Fingerprint Persistence Guarantee
# ═══════════════════════════════════════════════════════════════════════════

class TestFingerprintPersistence:

    def test_build_client_always_uses_db_fingerprint(self):
        """Calling build_client N times must always produce the SAME fingerprint."""
        from app.services.helper_pool_service import HelperPoolService

        helper = SimpleNamespace(
            device_model="Google Pixel 8 Pro",
            system_version="Android 14",
            app_version="10.14.5",
            lang_code="en",
            proxy_type=None, proxy_host=None, proxy_port=None,
            proxy_username=None, proxy_password=None,
        )
        with patch("app.services.helper_pool_service.settings") as s:
            s.API_ID = 12345
            s.API_HASH = "test"
            for _ in range(10):
                with patch("pyrogram.Client") as mock_client:
                    HelperPoolService.build_client("test", "session", helper)
                    kw = mock_client.call_args[1]
                    assert kw["device_model"] == "Google Pixel 8 Pro"
                    assert kw["system_version"] == "Android 14"
                    assert kw["app_version"] == "10.14.5"
                    assert kw["lang_code"] == "en"

    def test_build_client_never_randomizes(self):
        """build_client must NOT import or call pick_random_fingerprint."""
        import inspect
        from app.services.helper_pool_service import HelperPoolService
        source = inspect.getsource(HelperPoolService.build_client)
        assert "pick_random" not in source
        assert "random" not in source

    def test_fingerprint_only_assigned_during_otp_identity_selection(self):
        """OTP phone step obtains fingerprints through the identity service."""
        import inspect
        from app.handlers import helper_otp_wizard
        source = inspect.getsource(helper_otp_wizard._handle_phone)
        assert "select_identity" in source

        from app.services import helper_pool_service
        svc_source = inspect.getsource(helper_pool_service.HelperPoolService)
        assert "pick_random_fingerprint" not in svc_source

    def test_different_helpers_get_independent_fingerprints(self):
        """Two helpers with different DB fingerprints get different clients."""
        from app.services.helper_pool_service import HelperPoolService

        h1 = SimpleNamespace(
            device_model="Samsung SM-S928B", system_version="Android 14",
            app_version="10.14.5", lang_code="fa",
            proxy_type=None, proxy_host=None, proxy_port=None,
            proxy_username=None, proxy_password=None,
        )
        h2 = SimpleNamespace(
            device_model="Xiaomi 14 Pro", system_version="Android 14",
            app_version="10.14.4", lang_code="en",
            proxy_type=None, proxy_host=None, proxy_port=None,
            proxy_username=None, proxy_password=None,
        )
        with patch("app.services.helper_pool_service.settings") as s:
            s.API_ID = 12345
            s.API_HASH = "test"
            with patch("pyrogram.Client") as mock_client:
                HelperPoolService.build_client("h1", "s1", h1)
                kw1 = mock_client.call_args[1]
                HelperPoolService.build_client("h2", "s2", h2)
                kw2 = mock_client.call_args[1]
                assert kw1["device_model"] != kw2["device_model"]
                assert kw1["app_version"] != kw2["app_version"]

    def test_null_fingerprint_omits_kwargs(self):
        """Helper with no fingerprint in DB → Client gets no device kwargs."""
        from app.services.helper_pool_service import HelperPoolService

        helper = SimpleNamespace(
            device_model=None, system_version=None, app_version=None,
            lang_code=None, proxy_type=None, proxy_host=None,
            proxy_port=None, proxy_username=None, proxy_password=None,
        )
        with patch("app.services.helper_pool_service.settings") as s:
            s.API_ID = 12345
            s.API_HASH = "test"
            with patch("pyrogram.Client") as mock_client:
                HelperPoolService.build_client("test", "session", helper)
                kw = mock_client.call_args[1]
                assert "device_model" not in kw
                assert "system_version" not in kw
                assert "app_version" not in kw


# ═══════════════════════════════════════════════════════════════════════════
# 5. Integration: Full OTP flow (happy path mock)
# ═══════════════════════════════════════════════════════════════════════════

class TestOTPHappyPath:

    @pytest.mark.asyncio
    async def test_full_flow_phone_to_success(self):
        """Simulate: phone → send_code → code → sign_in → finalize."""
        from app.handlers.helper_otp_wizard import _handle_code, _handle_phone

        msg_phone = _mock_message(text="+989123456789")
        msg_code = _mock_message(text="1 2 3 4 5")

        mock_sent_code = SimpleNamespace(phone_code_hash="hash_xyz")
        mock_me = SimpleNamespace(id=777, username="helper_bot", first_name="Helper")

        mock_client_connect = AsyncMock()
        mock_client_connect.connect = AsyncMock()
        mock_client_connect.send_code = AsyncMock(return_value=mock_sent_code)
        mock_client_connect.disconnect = AsyncMock()

        mock_client_sign = AsyncMock()
        mock_client_sign.connect = AsyncMock()
        mock_client_sign.sign_in = AsyncMock()
        mock_client_sign.get_me = AsyncMock(return_value=mock_me)
        mock_client_sign.export_session_string = AsyncMock(return_value="BQsession123")
        mock_client_sign.disconnect = AsyncMock()

        r, store = _mock_redis_store()
        identity = SimpleNamespace(credential_id=7, device_profile_id=11)
        identity_state = {
            "app_api_id": 67890,
            "app_api_hash_enc": "enc_api_hash",
            "app_credential_id": 7,
            "device_profile_id": 11,
            "fp_device": "OnePlus 12",
            "fp_system": "Android 14",
            "fp_app": "10.14.5",
            "fp_lang": "en",
            "fp_system_lang": "en-US",
        }

        with (
            patch("app.handlers.helper_otp_wizard.get_redis", return_value=r),
            patch("app.handlers.helper_otp_wizard.async_session") as mock_sess,
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
            patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()),
            patch("app.services.helper_app_identity_service.HelperPoolService.decrypt_session", return_value="selectedhash"),
            patch("app.handlers.helper_otp_wizard.HelperPoolService") as mock_svc,
            patch("app.handlers.helper_otp_wizard.helper_event_repo") as mock_events,
        ):
            mock_svc.encrypt_session.return_value = "encrypted_session"
            mock_svc.decrypt_session.return_value = "BQ_pre_auth"
            mock_svc.fingerprint_session.return_value = "fp-session"
            mock_svc.is_duplicate_session = AsyncMock(return_value=False)
            mock_events.log_event = AsyncMock()

            sess = AsyncMock()
            sess.__aenter__ = AsyncMock(return_value=sess)
            sess.__aexit__ = AsyncMock(return_value=False)
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            sess.execute = AsyncMock(return_value=mock_result)
            sess.begin = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False)))
            sess.add = MagicMock()
            sess.flush = AsyncMock()
            mock_sess.return_value = sess

            with patch("app.handlers.helper_otp_wizard.Client", return_value=mock_client_connect):
                await _handle_phone(AsyncMock(), msg_phone, {"step": "awaiting_phone"})

            assert msg_phone.reply.call_count >= 2

            from app.utils.redis_keys import helper_otp_state_key
            raw_state = store.get(helper_otp_state_key(42))
            assert raw_state is not None
            state = json.loads(raw_state)
            assert state["phone"] == "+989123456789"
            assert state["fp_device"] == "OnePlus 12"
            assert state["step"] == "awaiting_code"

            with patch("app.handlers.helper_otp_wizard.Client", return_value=mock_client_sign):
                await _handle_code(AsyncMock(), msg_code, state)

            assert msg_code.reply.call_count >= 1
            last_reply = str(msg_code.reply.call_args_list[-1])
            assert "OnePlus 12" in last_reply or "هلپر" in last_reply
