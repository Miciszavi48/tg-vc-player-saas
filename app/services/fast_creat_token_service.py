"""Secret handling and fair reservation for Fast-Creat API tokens."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import IntegrityError

from app.config.settings import settings
from app.repositories import fast_creat_token_repo


class FastCreatTokenError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FastCreatTokensUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ReservedFastCreatToken:
    id: int
    provider: str
    value: str


def has_required_keys() -> bool:
    encryption_key = settings.FAST_CREAT_TOKEN_KEY_CURRENT
    fingerprint_key = settings.FAST_CREAT_TOKEN_FINGERPRINT_KEY
    if not encryption_key or not fingerprint_key:
        return False
    if len(fingerprint_key.encode("utf-8")) < 32:
        return False
    if hmac.compare_digest(encryption_key, fingerprint_key):
        return False
    try:
        Fernet(encryption_key.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    return True


def normalize_token(value: str) -> str:
    token = str(value or "").strip()
    if not token or len(token) > 4096 or any(ch.isspace() for ch in token):
        raise FastCreatTokenError("invalid_token")
    return token


def fingerprint_token(value: str) -> str:
    key = settings.FAST_CREAT_TOKEN_FINGERPRINT_KEY
    if not key:
        raise FastCreatTokenError("fingerprint_key_missing")
    return hmac.new(
        key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def encrypt_token(value: str) -> str:
    key = settings.FAST_CREAT_TOKEN_KEY_CURRENT
    if not key:
        raise FastCreatTokenError("encryption_key_missing")
    try:
        return Fernet(key.encode("utf-8")).encrypt(value.encode("utf-8")).decode("utf-8")
    except (ValueError, TypeError) as exc:
        raise FastCreatTokenError("invalid_encryption_key") from exc


def decrypt_token(value: str) -> str | None:
    encrypted = value.encode("utf-8")
    for key in (settings.FAST_CREAT_TOKEN_KEY_CURRENT, settings.FAST_CREAT_TOKEN_KEY_OLD):
        if not key:
            continue
        try:
            return Fernet(key.encode("utf-8")).decrypt(encrypted).decode("utf-8")
        except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
            continue
    return None


async def add_token(*, provider: str, value: str, actor_id: int) -> int:
    if not has_required_keys():
        raise FastCreatTokenError("storage_not_configured")
    token = normalize_token(value)
    fingerprint = fingerprint_token(token)
    try:
        row = await fast_creat_token_repo.create_token(
            provider=provider,
            token_enc=encrypt_token(token),
            fingerprint=fingerprint,
            created_by=actor_id,
        )
    except IntegrityError as exc:
        raise FastCreatTokenError("duplicate_token") from exc
    except ValueError as exc:
        raise FastCreatTokenError(str(exc)) from exc
    return row.id


async def reserve_token(
    provider: str,
    *,
    exclude_ids: set[int] | None = None,
) -> ReservedFastCreatToken | None:
    if not has_required_keys():
        raise FastCreatTokensUnavailable("storage_not_configured")
    excluded = set(exclude_ids or ())
    while True:
        row = await fast_creat_token_repo.reserve_next_token(provider, exclude_ids=excluded)
        if row is None:
            return None
        value = decrypt_token(row.token_enc)
        if value is not None:
            return ReservedFastCreatToken(id=row.id, provider=row.provider, value=value)

        # A rotated or corrupted ciphertext must not consume the whole pool.
        # Mark only that row invalid and continue with the next eligible token.
        await fast_creat_token_repo.mark_invalid(row.id, code="decrypt_failed")
        excluded.add(row.id)
