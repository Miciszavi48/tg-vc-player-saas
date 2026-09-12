from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import settings
from app.database.models import HelperAppCredential, HelperDeviceProfile
from app.services import helper_app_identity_service as identity_service
from app.services.helper_app_identity_service import HelperAppIdentity, HelperAppIdentityError
from app.utils.device_spoof import DeviceFingerprint, load_device_fingerprints
from app.utils.redis_keys import helper_otp_state_key
from app.utils.ui import CB


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


def _state(**extra) -> dict:
    state = {
        "step": "awaiting_code",
        "phone": "+989123456789",
        "phone_code_hash": "hash_abc",
        "pre_auth_session_enc": "enc_pre_auth",
        "pending_session_enc": "enc_pending",
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
    state.update(extra)
    return state


def _load_migration_module():
    path = Path("app/database/migrations/versions/0023_helper_app_identity_profiles.py")
    spec = importlib.util.spec_from_file_location("migration_0023", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_metadata_correct():
    migration = _load_migration_module()

    assert migration.revision == "0023_helper_app_identity"
    assert migration.down_revision == "0022_owner_text_links"


def test_app_credential_model_stores_encrypted_hash_field_not_raw_hash():
    columns = HelperAppCredential.__table__.columns

    assert "api_hash_enc" in columns
    assert "api_hash" not in columns


@pytest.mark.asyncio
async def test_creating_credential_encrypts_api_hash():
    raw_hash = "abcd1234efgh5678"
    created = SimpleNamespace(id=1, api_id=12345, api_hash_enc="enc_hash")

    with (
        patch.object(identity_service.HelperPoolService, "encrypt_session", return_value="enc_hash") as encrypt,
        patch.object(
            identity_service.helper_app_credential_repo,
            "create_app_credential",
            AsyncMock(return_value=created),
        ) as create_repo,
    ):
        credential = await identity_service.create_app_credential(
            api_id=12345,
            api_hash=raw_hash,
            label="primary",
            created_by=settings.DEVELOPER_ID,
        )

    encrypt.assert_called_once_with(raw_hash)
    assert credential.api_hash_enc == "enc_hash"
    assert raw_hash not in str(create_repo.call_args)


def test_listing_credentials_model_never_exposes_raw_api_hash_column():
    credential = HelperAppCredential(api_id=12345, api_hash_enc="enc_hash")

    assert hasattr(credential, "api_hash_enc")
    assert not hasattr(credential, "api_hash")


@pytest.mark.asyncio
async def test_decrypt_happens_only_in_service_selection(monkeypatch):
    credential = SimpleNamespace(id=7, api_id=77777, api_hash_enc="enc_saved", is_active=True)
    monkeypatch.setattr(identity_service.helper_app_credential_repo, "list_active_app_credentials", AsyncMock(return_value=[credential]))
    monkeypatch.setattr(identity_service.helper_device_profile_repo, "get_random_active_device_profile", AsyncMock(return_value=None))

    with (
        patch.object(identity_service.HelperPoolService, "decrypt_session", return_value="saved_hash_value") as decrypt,
        patch.object(identity_service, "load_device_fingerprints", return_value=[]),
        patch.object(
            identity_service,
            "pick_random_fingerprint",
            return_value=DeviceFingerprint("Pixel 8", "Android 14", "10.14.5", "en"),
        ),
    ):
        selected = await identity_service.select_identity()

    decrypt.assert_called_once_with("enc_saved")
    assert selected.api_id == 77777
    assert selected.api_hash == "saved_hash_value"
    assert selected.credential_id == 7


def test_device_profile_model_stores_device_app_fields():
    columns = HelperDeviceProfile.__table__.columns

    assert "device_model" in columns
    assert "system_version" in columns
    assert "app_version" in columns
    assert "api_hash" not in columns


def test_device_profile_loader_accepts_valid_json_list(tmp_path: Path):
    path = tmp_path / "app_information.json"
    path.write_text(
        json.dumps(
            [
                {
                    "device_model": "Samsung Galaxy A52",
                    "system_version": "12 S (31)",
                    "app_version": "10.0.4.0",
                }
            ]
        ),
        encoding="utf-8",
    )

    profiles = load_device_fingerprints(path)

    assert profiles == [
        DeviceFingerprint(
            "Samsung Galaxy A52",
            "12 S (31)",
            "10.0.4.0",
            "en",
            "en-US",
        )
    ]


def test_device_profile_loader_ignores_invalid_entries_and_deduplicates(tmp_path: Path):
    path = tmp_path / "app_information.json"
    path.write_text(
        json.dumps(
            [
                {"device_model": "", "system_version": "Android 14", "app_version": "10.0.4.0"},
                {"device_model": "Pixel 8", "system_version": "Android 14", "app_version": "10.14.5"},
                {"device_model": "Pixel 8", "system_version": "Android 14", "app_version": "10.14.5"},
            ]
        ),
        encoding="utf-8",
    )

    profiles = load_device_fingerprints(path)

    assert len(profiles) == 1
    assert profiles[0].device_model == "Pixel 8"


def test_missing_profile_file_falls_back_safely(tmp_path: Path):
    assert load_device_fingerprints(tmp_path / "missing.json") == []


@pytest.mark.asyncio
async def test_identity_service_picks_db_device_profile(monkeypatch):
    profile = SimpleNamespace(
        id=11,
        device_model="DB Pixel",
        system_version="Android 15",
        app_version="11.4.2",
        lang_code="en",
        system_lang_code="en-US",
    )
    monkeypatch.setattr(identity_service.helper_app_credential_repo, "list_active_app_credentials", AsyncMock(return_value=[]))
    monkeypatch.setattr(identity_service.helper_device_profile_repo, "get_random_active_device_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(identity_service.settings, "API_ID", 12345)
    monkeypatch.setattr(identity_service.settings, "API_HASH", "globalhash")

    selected = await identity_service.select_identity()

    assert selected.device_profile_id == 11
    assert selected.device_model == "DB Pixel"


@pytest.mark.asyncio
async def test_identity_service_falls_back_to_json_profile(monkeypatch):
    monkeypatch.setattr(identity_service.helper_app_credential_repo, "list_active_app_credentials", AsyncMock(return_value=[]))
    monkeypatch.setattr(identity_service.helper_device_profile_repo, "get_random_active_device_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(identity_service.settings, "API_ID", 12345)
    monkeypatch.setattr(identity_service.settings, "API_HASH", "globalhash")

    with (
        patch.object(identity_service, "load_device_fingerprints", return_value=[
            DeviceFingerprint("JSON Pixel", "Android 14", "10.14.5", "en")
        ]),
        patch.object(identity_service, "pick_random_fingerprint") as pick_builtin,
    ):
        selected = await identity_service.select_identity()

    assert selected.device_model == "JSON Pixel"
    pick_builtin.assert_not_called()


@pytest.mark.asyncio
async def test_identity_service_falls_back_to_builtin_profile(monkeypatch):
    monkeypatch.setattr(identity_service.helper_app_credential_repo, "list_active_app_credentials", AsyncMock(return_value=[]))
    monkeypatch.setattr(identity_service.helper_device_profile_repo, "get_random_active_device_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(identity_service.settings, "API_ID", 12345)
    monkeypatch.setattr(identity_service.settings, "API_HASH", "globalhash")

    with (
        patch.object(identity_service, "load_device_fingerprints", return_value=[]),
        patch.object(
            identity_service,
            "pick_random_fingerprint",
            return_value=DeviceFingerprint("Built-in Pixel", "Android 14", "10.14.5", "en"),
        ),
    ):
        selected = await identity_service.select_identity()

    assert selected.device_model == "Built-in Pixel"


@pytest.mark.asyncio
async def test_identity_service_picks_saved_api_credential(monkeypatch):
    credential = SimpleNamespace(id=5, api_id=54321, api_hash_enc="enc_saved", is_active=True)
    monkeypatch.setattr(identity_service.helper_app_credential_repo, "list_active_app_credentials", AsyncMock(return_value=[credential]))
    monkeypatch.setattr(identity_service.helper_device_profile_repo, "get_random_active_device_profile", AsyncMock(return_value=None))

    with (
        patch.object(identity_service.HelperPoolService, "decrypt_session", return_value="savedhash"),
        patch.object(identity_service, "load_device_fingerprints", return_value=[]),
        patch.object(
            identity_service,
            "pick_random_fingerprint",
            return_value=DeviceFingerprint("Pixel 8", "Android 14", "10.14.5", "en"),
        ),
    ):
        selected = await identity_service.select_identity()

    assert selected.api_id == 54321
    assert selected.api_hash == "savedhash"
    assert selected.credential_id == 5


@pytest.mark.asyncio
async def test_identity_service_falls_back_to_global_config(monkeypatch):
    monkeypatch.setattr(identity_service.helper_app_credential_repo, "list_active_app_credentials", AsyncMock(return_value=[]))
    monkeypatch.setattr(identity_service.helper_device_profile_repo, "get_random_active_device_profile", AsyncMock(return_value=None))
    monkeypatch.setattr(identity_service.settings, "API_ID", 24680)
    monkeypatch.setattr(identity_service.settings, "API_HASH", "globalhash")

    with (
        patch.object(identity_service, "load_device_fingerprints", return_value=[]),
        patch.object(
            identity_service,
            "pick_random_fingerprint",
            return_value=DeviceFingerprint("Pixel 8", "Android 14", "10.14.5", "en"),
        ),
    ):
        selected = await identity_service.select_identity()

    assert selected.api_id == 24680
    assert selected.api_hash == "globalhash"
    assert selected.credential_id is None


@pytest.mark.asyncio
async def test_missing_global_credentials_raises_visible_identity_error(monkeypatch):
    monkeypatch.setattr(identity_service.helper_app_credential_repo, "list_active_app_credentials", AsyncMock(return_value=[]))
    monkeypatch.setattr(identity_service.settings, "API_ID", 0)
    monkeypatch.setattr(identity_service.settings, "API_HASH", "")

    with pytest.raises(HelperAppIdentityError, match="missing_app_credentials"):
        await identity_service.select_identity()


@pytest.mark.asyncio
async def test_helper_otp_send_code_uses_selected_api_credential_and_device_profile():
    from app.handlers.helper_otp_wizard import _handle_phone

    redis = _FakeRedis()
    msg = _msg(settings.DEVELOPER_ID, "+989123456789")
    identity = HelperAppIdentity(
        api_id=67890,
        api_hash="selectedhash",
        credential_id=7,
        device_model="DB Pixel",
        system_version="Android 15",
        app_version="11.4.2",
        lang_code="en",
        system_lang_code="en-US",
        device_profile_id=11,
    )
    identity_state = {
        "app_api_id": 67890,
        "app_api_hash_enc": "enc_api_hash",
        "app_credential_id": 7,
        "device_profile_id": 11,
        "fp_device": "DB Pixel",
        "fp_system": "Android 15",
        "fp_app": "11.4.2",
        "fp_lang": "en",
        "fp_system_lang": "en-US",
    }
    pyrogram_client = AsyncMock()
    pyrogram_client.connect = AsyncMock()
    pyrogram_client.send_code = AsyncMock(return_value=SimpleNamespace(phone_code_hash="hash_saved"))
    pyrogram_client.disconnect = AsyncMock()

    with (
        patch("app.handlers.helper_otp_wizard.async_session", return_value=_phone_check_session()),
        patch("app.handlers.helper_otp_wizard.get_redis", AsyncMock(return_value=redis)),
        patch("app.handlers.helper_otp_wizard.Client", return_value=pyrogram_client) as client_cls,
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.select_identity", AsyncMock(return_value=identity)),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.state_fields_for_identity", return_value=identity_state),
        patch("app.handlers.helper_otp_wizard.helper_app_identity_service.mark_identity_used", AsyncMock()) as mark_used,
        patch("app.handlers.helper_otp_wizard.HelperPoolService.decrypt_session", return_value="selectedhash"),
    ):
        await _handle_phone(AsyncMock(), msg, {"step": "awaiting_phone"})

    kwargs = client_cls.call_args.kwargs
    assert kwargs["api_id"] == 67890
    assert kwargs["api_hash"] == "selectedhash"
    assert kwargs["device_model"] == "DB Pixel"
    pyrogram_client.send_code.assert_awaited_once_with("+989123456789")
    mark_used.assert_awaited_once_with(identity)
    saved = json.loads(await redis.get(helper_otp_state_key(settings.DEVELOPER_ID)) or "{}")
    assert saved["app_credential_id"] == 7
    assert saved["device_profile_id"] == 11
    assert "pre_auth_session_enc" not in saved


@pytest.mark.asyncio
async def test_helper_otp_sign_in_resumes_same_selected_identity_and_pre_auth_session():
    from app.handlers.helper_otp_wizard import _handle_code
    from app.services import helper_otp_pre_auth_registry

    helper_otp_pre_auth_registry.clear_all()
    msg = _msg(settings.DEVELOPER_ID, "12345")
    pyrogram_client = AsyncMock()
    pyrogram_client.sign_in = AsyncMock()
    await helper_otp_pre_auth_registry.put(
        settings.DEVELOPER_ID,
        client=pyrogram_client,
        phone="+989123456789",
        phone_code_hash="hash_abc",
    )

    with patch("app.handlers.helper_otp_wizard._finalize_helper", AsyncMock()) as finalize:
        await _handle_code(AsyncMock(), msg, _state())

    pyrogram_client.sign_in.assert_awaited_once_with("+989123456789", "hash_abc", "12345")
    finalize.assert_awaited_once()
    helper_otp_pre_auth_registry.clear_all()


@pytest.mark.asyncio
async def test_helper_otp_2fa_resumes_same_selected_identity_and_pending_session():
    from app.handlers.helper_otp_wizard import _handle_2fa
    from app.services import helper_otp_pre_auth_registry

    helper_otp_pre_auth_registry.clear_all()
    msg = _msg(settings.DEVELOPER_ID, "2fa-password")
    pyrogram_client = AsyncMock()
    pyrogram_client.check_password = AsyncMock()
    await helper_otp_pre_auth_registry.put(
        settings.DEVELOPER_ID,
        client=pyrogram_client,
        phone="+989123456789",
        phone_code_hash="hash_abc",
    )

    with patch("app.handlers.helper_otp_wizard._finalize_helper", AsyncMock()) as finalize:
        await _handle_2fa(AsyncMock(), msg, _state(step="awaiting_2fa"))

    pyrogram_client.check_password.assert_awaited_once_with("2fa-password")
    finalize.assert_awaited_once()
    helper_otp_pre_auth_registry.clear_all()


def test_no_full_secrets_or_user_inputs_appear_in_helper_otp_logs():
    from io import StringIO

    from loguru import logger
    from app.handlers.helper_otp_wizard import _log_auth_event

    buffer = StringIO()
    handler_id = logger.add(buffer, format="{message}", level="DEBUG")
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
    assert "RuntimeError" in logs
    assert "***789" in logs


def test_callback_data_unchanged():
    assert CB["HLP_ADD_OTP"] == "hlp:add:otp"
