from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from app.config.settings import settings
from app.repositories import helper_app_credential_repo, helper_device_profile_repo
from app.services.helper_pool_service import HelperPoolService
from app.utils.device_spoof import DeviceFingerprint, load_device_fingerprints, pick_random_fingerprint

logger = logging.getLogger(__name__)

STATE_API_ID_KEY = "app_api_id"
STATE_API_HASH_ENC_KEY = "app_api_hash_enc"
STATE_CREDENTIAL_ID_KEY = "app_credential_id"
STATE_DEVICE_PROFILE_ID_KEY = "device_profile_id"


class HelperAppIdentityError(Exception):
    """Raised when Helper OTP cannot construct a safe app identity."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class HelperAppIdentity:
    api_id: int
    api_hash: str
    credential_id: int | None
    device_model: str
    system_version: str
    app_version: str
    lang_code: str
    system_lang_code: str
    device_profile_id: int | None


def validate_api_id(api_id: object) -> int:
    try:
        value = int(api_id)
    except (TypeError, ValueError) as exc:
        raise HelperAppIdentityError("invalid_api_id") from exc
    if value <= 0:
        raise HelperAppIdentityError("invalid_api_id")
    return value


def validate_api_hash(api_hash: object) -> str:
    value = api_hash.strip() if isinstance(api_hash, str) else ""
    if not 8 <= len(value) <= 128:
        raise HelperAppIdentityError("invalid_api_hash")
    return value


def mask_api_hash(api_hash: str | None) -> str:
    value = api_hash or ""
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}...{value[-4:]}"


async def create_app_credential(
    *,
    api_id: int,
    api_hash: str,
    label: str | None = None,
    created_by: int | None = None,
    notes: str | None = None,
    is_active: bool = True,
):
    validated_api_id = validate_api_id(api_id)
    validated_api_hash = validate_api_hash(api_hash)
    try:
        api_hash_enc = HelperPoolService.encrypt_session(validated_api_hash)
    except Exception as exc:
        raise HelperAppIdentityError("api_hash_encrypt_failed") from exc
    return await helper_app_credential_repo.create_app_credential(
        api_id=validated_api_id,
        api_hash_enc=api_hash_enc,
        label=label,
        created_by=created_by,
        notes=notes,
        is_active=is_active,
    )


async def select_identity(preferred_credential_id: int | None = None) -> HelperAppIdentity:
    credential = None
    if preferred_credential_id is not None:
        candidate = await helper_app_credential_repo.get_app_credential(preferred_credential_id)
        if candidate is not None and candidate.is_active:
            credential = candidate

    if credential is None:
        credentials = await helper_app_credential_repo.list_active_app_credentials()
        if credentials:
            credential = random.choice(credentials)

    if credential is not None:
        api_id = validate_api_id(credential.api_id)
        api_hash = HelperPoolService.decrypt_session(credential.api_hash_enc)
        if not api_hash:
            raise HelperAppIdentityError("api_hash_decrypt_failed")
        api_hash = validate_api_hash(api_hash)
        credential_id = credential.id
    else:
        try:
            api_id = validate_api_id(settings.API_ID)
            api_hash = validate_api_hash(settings.API_HASH)
        except HelperAppIdentityError as exc:
            raise HelperAppIdentityError("missing_app_credentials") from exc
        credential_id = None

    fingerprint, device_profile_id = await _select_device_profile()
    return HelperAppIdentity(
        api_id=api_id,
        api_hash=api_hash,
        credential_id=credential_id,
        device_model=fingerprint.device_model,
        system_version=fingerprint.system_version,
        app_version=fingerprint.app_version,
        lang_code=fingerprint.lang_code,
        system_lang_code=fingerprint.system_lang_code,
        device_profile_id=device_profile_id,
    )


async def _select_device_profile() -> tuple[DeviceFingerprint, int | None]:
    profile = await helper_device_profile_repo.get_random_active_device_profile()
    if profile is not None:
        return (
            DeviceFingerprint(
                device_model=profile.device_model,
                system_version=profile.system_version,
                app_version=profile.app_version,
                lang_code=profile.lang_code,
                system_lang_code=profile.system_lang_code,
            ),
            profile.id,
        )

    local_profiles = load_device_fingerprints()
    if local_profiles:
        return random.choice(local_profiles), None

    return pick_random_fingerprint(), None


def state_fields_for_identity(identity: HelperAppIdentity) -> dict:
    try:
        api_hash_enc = HelperPoolService.encrypt_session(identity.api_hash)
    except Exception as exc:
        raise HelperAppIdentityError("api_hash_encrypt_failed") from exc
    return {
        STATE_API_ID_KEY: identity.api_id,
        STATE_API_HASH_ENC_KEY: api_hash_enc,
        STATE_CREDENTIAL_ID_KEY: identity.credential_id,
        STATE_DEVICE_PROFILE_ID_KEY: identity.device_profile_id,
        "fp_device": identity.device_model,
        "fp_system": identity.system_version,
        "fp_app": identity.app_version,
        "fp_lang": identity.lang_code,
        "fp_system_lang": identity.system_lang_code,
    }


def api_id_from_state(state: dict) -> int:
    raw = state.get(STATE_API_ID_KEY, settings.API_ID)
    return validate_api_id(raw)


def api_hash_from_state(state: dict) -> str:
    enc = state.get(STATE_API_HASH_ENC_KEY)
    if enc:
        api_hash = HelperPoolService.decrypt_session(str(enc))
        if not api_hash:
            raise HelperAppIdentityError("api_hash_decrypt_failed")
        return validate_api_hash(api_hash)
    return validate_api_hash(settings.API_HASH)


async def mark_identity_used(identity: HelperAppIdentity) -> None:
    await mark_identity_ids_used(
        credential_id=identity.credential_id,
        device_profile_id=identity.device_profile_id,
    )


async def mark_identity_ids_used(
    *,
    credential_id: int | None,
    device_profile_id: int | None,
) -> None:
    try:
        if credential_id is not None:
            await helper_app_credential_repo.mark_app_credential_used(credential_id)
        if device_profile_id is not None:
            await helper_device_profile_repo.mark_device_profile_used(device_profile_id)
    except Exception as exc:
        logger.warning(
            "helper_app_identity mark_used_failed credential_id=%s device_profile_id=%s exc=%s",
            credential_id,
            device_profile_id,
            type(exc).__name__,
        )
