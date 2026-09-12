"""Secure global cookie-jar selection for yt-dlp YouTube requests."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet, InvalidToken

from app.config.settings import settings
from app.database.models import YoutubeCookieSession
from app.repositories import youtube_session_repo
from app.utils.cache import get_redis
from app.utils.redis_keys import TTL_YOUTUBE_REKEY_LOCK, youtube_rekey_lock_key


YOUTUBE_SESSION_PROBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
MAX_COOKIE_FILE_BYTES = 512 * 1024
_REKEY_BATCH_SIZE = 100
_REKEY_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_AUTH_COOKIE_NAMES = frozenset({
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "__SECURE-1PSID",
    "__SECURE-3PSID",
    "__SECURE-1PAPISID",
    "__SECURE-3PAPISID",
})

logger = logging.getLogger(__name__)


class CookieValidationError(ValueError):
    """A cookie upload failed safe, non-secret validation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class YoutubeSessionsUnavailable(RuntimeError):
    """Configured YouTube sessions exist, but none can safely be used."""


class YoutubePlaylistUnsupported(ValueError):
    """A playlist URL has no explicit video target."""


@dataclass(frozen=True)
class YtdlpRunResult:
    returncode: int
    stdout: str
    error_code: str | None

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class SessionTestResult:
    ok: bool
    error_code: str | None
    status: str | None


@dataclass(frozen=True)
class ImportedSessionResult:
    session_id: int
    test: SessionTestResult


@dataclass(frozen=True)
class RekeyPoolResult:
    total: int
    succeeded: int
    failed: int
    busy: bool = False


def is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def is_playlist_without_video(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not (host == "youtube.com" or host.endswith(".youtube.com")):
        return False
    query = parse_qs(parsed.query)
    return parsed.path.rstrip("/") == "/playlist" and bool(query.get("list")) and not bool(query.get("v"))


def has_required_keys() -> bool:
    return bool(
        settings.YOUTUBE_COOKIE_KEY_CURRENT
        and settings.YOUTUBE_COOKIE_FINGERPRINT_KEY
    )


def _allowed_cookie_domain(value: str) -> bool:
    domain = value.lower().lstrip(".")
    return domain in {"youtube.com", "google.com", "youtu.be"} or domain.endswith(
        (".youtube.com", ".google.com")
    )


def normalize_cookie_jar(raw: bytes) -> str:
    if len(raw) > MAX_COOKIE_FILE_BYTES:
        raise CookieValidationError("file_too_large")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CookieValidationError("invalid_encoding") from exc

    lines = text.splitlines()
    first = next((line.strip() for line in lines if line.strip()), "")
    if first != "# Netscape HTTP Cookie File":
        raise CookieValidationError("invalid_netscape_header")

    accepted: list[str] = []
    for original in lines:
        if not original.strip() or original.strip() == "# Netscape HTTP Cookie File":
            continue
        row = original
        if original.startswith("#HttpOnly_"):
            fields = original[len("#HttpOnly_"):].split("\t")
        elif original.startswith("#"):
            continue
        else:
            fields = original.split("\t")
        if len(fields) != 7:
            raise CookieValidationError("invalid_cookie_row")
        if _allowed_cookie_domain(fields[0]):
            accepted.append(row)

    if not accepted:
        raise CookieValidationError("no_youtube_cookie_rows")
    return "# Netscape HTTP Cookie File\n" + "\n".join(accepted) + "\n"


def extract_cookie_expiry(normalized_cookie_jar: str) -> datetime | None:
    """Return the nearest known authentication-cookie expiry without retaining its name."""
    expiries: list[datetime] = []
    for original in normalized_cookie_jar.splitlines():
        if original.startswith("#HttpOnly_"):
            fields = original[len("#HttpOnly_"):].split("\t")
        elif original.startswith("#"):
            continue
        else:
            fields = original.split("\t")
        if len(fields) != 7 or fields[5].upper() not in _AUTH_COOKIE_NAMES:
            continue
        try:
            unix_expiry = int(fields[4])
        except ValueError:
            continue
        if unix_expiry <= 0:
            continue
        try:
            expiries.append(datetime.fromtimestamp(unix_expiry, tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            continue
    return min(expiries, default=None)


def fingerprint_cookie_jar(normalized_cookie_jar: str) -> str:
    key = settings.YOUTUBE_COOKIE_FINGERPRINT_KEY
    if not key:
        raise CookieValidationError("fingerprint_key_missing")
    return hmac.new(
        key.encode("utf-8"),
        normalized_cookie_jar.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def encrypt_cookie_jar(normalized_cookie_jar: str) -> str:
    key = settings.YOUTUBE_COOKIE_KEY_CURRENT
    if not key:
        raise CookieValidationError("encryption_key_missing")
    try:
        return Fernet(key.encode("utf-8")).encrypt(normalized_cookie_jar.encode("utf-8")).decode("utf-8")
    except (ValueError, TypeError) as exc:
        raise CookieValidationError("invalid_encryption_key") from exc


def _valid_fernet_key(value: str) -> bool:
    try:
        Fernet(value.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    return True


def has_rotation_keys() -> bool:
    """Require two distinct, valid keys before mutating every encrypted session."""
    current = settings.YOUTUBE_COOKIE_KEY_CURRENT
    old = settings.YOUTUBE_COOKIE_KEY_OLD
    return bool(current and old and current != old and _valid_fernet_key(current) and _valid_fernet_key(old))


def decrypt_cookie_jar(encrypted_text: str) -> str | None:
    token = encrypted_text.encode("utf-8")
    for key in (settings.YOUTUBE_COOKIE_KEY_CURRENT, settings.YOUTUBE_COOKIE_KEY_OLD):
        if not key:
            continue
        try:
            return Fernet(key.encode("utf-8")).decrypt(token).decode("utf-8")
        except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
            continue
    return None


def _session_temp_dir() -> Path:
    root = (
        Path(settings.TEMP_PATH).expanduser().resolve(strict=False)
        / "youtube_cookies"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def materialized_cookie_file(encrypted_text: str):
    plain = decrypt_cookie_jar(encrypted_text)
    if plain is None:
        raise YoutubeSessionsUnavailable("youtube cookie decryption failed")
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=_session_temp_dir(),
            prefix="yt_",
            suffix=".txt",
            delete=False,
        ) as handle:
            handle.write(plain)
            path = Path(handle.name)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        yield path
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _run_process(command: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "yt-dlp-not-found"


def _error_code(stderr: str) -> str:
    message = (stderr or "").lower()
    if any(marker in message for marker in (
        "http error 429",
        "too many requests",
        "sign in to confirm you're not a bot",
        "sign in to confirm that you're not a bot",
    )):
        return "youtube_cooldown"
    if any(marker in message for marker in (
        "cookies are no longer valid",
        "invalid cookie",
        "cookie is invalid",
    )):
        return "invalid_cookie"
    if "yt-dlp-not-found" in message:
        return "ytdlp_missing"
    if "timeout" in message:
        return "ytdlp_timeout"
    return "media_error"


def _with_cookie(command: list[str], cookie_path: Path) -> list[str]:
    if not command:
        raise ValueError("yt-dlp command cannot be empty")
    return [*command[:-1], "--cookies", str(cookie_path), command[-1]]


async def _run_for_reserved_session(
    row: YoutubeCookieSession,
    command: list[str],
    *,
    timeout: int,
) -> YtdlpRunResult:
    try:
        with materialized_cookie_file(row.cookie_blob_enc) as cookie_path:
            returncode, stdout, stderr = await asyncio.to_thread(
                _run_process,
                _with_cookie(command, cookie_path),
                timeout,
            )
    except YoutubeSessionsUnavailable:
        await youtube_session_repo.mark_invalid(row.id, error_code="decrypt_failed")
        return YtdlpRunResult(returncode=1, stdout="", error_code="invalid_cookie")

    if returncode == 0:
        await youtube_session_repo.mark_success(row.id)
        return YtdlpRunResult(returncode=returncode, stdout=stdout, error_code=None)

    code = _error_code(stderr)
    if code == "youtube_cooldown":
        await youtube_session_repo.mark_cooldown(row.id)
    elif code == "invalid_cookie":
        await youtube_session_repo.mark_invalid(row.id, error_code=code)
    return YtdlpRunResult(returncode=returncode, stdout=stdout, error_code=code)


async def run_ytdlp_for_media(
    url: str,
    command: list[str],
    *,
    timeout: int,
) -> YtdlpRunResult:
    """Run a yt-dlp media command with the global pool only for YouTube URLs."""
    if is_playlist_without_video(url):
        raise YoutubePlaylistUnsupported("youtube_playlist_requires_video")
    if not is_youtube_url(url):
        code, stdout, stderr = await asyncio.to_thread(_run_process, command, timeout)
        return YtdlpRunResult(code, stdout, None if code == 0 else _error_code(stderr))

    summary = await youtube_session_repo.get_summary()
    if summary.total == 0:
        code, stdout, stderr = await asyncio.to_thread(_run_process, command, timeout)
        return YtdlpRunResult(code, stdout, None if code == 0 else _error_code(stderr))
    if not has_required_keys():
        raise YoutubeSessionsUnavailable("youtube cookie keys are not configured")

    first = await youtube_session_repo.reserve_next_session()
    if first is None:
        raise YoutubeSessionsUnavailable("no eligible YouTube cookie session")
    first_result = await _run_for_reserved_session(first, command, timeout=timeout)
    if first_result.ok or first_result.error_code not in {"youtube_cooldown", "invalid_cookie"}:
        return first_result

    second = await youtube_session_repo.reserve_next_session(exclude_id=first.id)
    if second is None:
        return first_result
    return await _run_for_reserved_session(second, command, timeout=timeout)


async def test_session(session_id: int, *, actor_id: int | None = None) -> SessionTestResult:
    row = await youtube_session_repo.get_session(session_id)
    if row is None:
        return SessionTestResult(False, "not_found", None)
    if not has_required_keys():
        return SessionTestResult(False, "key_missing", row.status)

    plain = decrypt_cookie_jar(row.cookie_blob_enc)
    if plain is None:
        await youtube_session_repo.mark_test_failure(
            session_id,
            error_code="decrypt_failed",
            invalid=True,
            actor_id=actor_id,
        )
        return SessionTestResult(False, "invalid_cookie", "invalid")
    cookie_expires_at = extract_cookie_expiry(plain)

    command = [
        "yt-dlp",
        "--simulate",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        YOUTUBE_SESSION_PROBE_URL,
    ]
    try:
        with materialized_cookie_file(row.cookie_blob_enc) as cookie_path:
            returncode, _stdout, stderr = await asyncio.to_thread(
                _run_process,
                _with_cookie(command, cookie_path),
                30,
            )
    except YoutubeSessionsUnavailable:
        await youtube_session_repo.mark_test_failure(
            session_id,
            error_code="decrypt_failed",
            invalid=True,
            actor_id=actor_id,
        )
        return SessionTestResult(False, "invalid_cookie", "invalid")

    await youtube_session_repo.update_cookie_expiry(
        session_id,
        cookie_expires_at=cookie_expires_at,
    )

    if returncode == 0:
        await youtube_session_repo.mark_success(session_id, checked=True, actor_id=actor_id)
        updated = await youtube_session_repo.get_session(session_id)
        return SessionTestResult(True, None, updated.status if updated else None)

    code = _error_code(stderr)
    await youtube_session_repo.mark_test_failure(
        session_id,
        error_code=code,
        invalid=code == "invalid_cookie",
        actor_id=actor_id,
    )
    updated = await youtube_session_repo.get_session(session_id)
    return SessionTestResult(False, code, updated.status if updated else None)


async def import_cookie_session(raw: bytes, *, actor_id: int) -> ImportedSessionResult:
    if not has_required_keys():
        raise CookieValidationError("key_missing")
    normalized = normalize_cookie_jar(raw)
    fingerprint = fingerprint_cookie_jar(normalized)
    if await youtube_session_repo.get_by_fingerprint(fingerprint):
        raise CookieValidationError("duplicate_cookie")
    row = await youtube_session_repo.create_session(
        cookie_blob_enc=encrypt_cookie_jar(normalized),
        fingerprint=fingerprint,
        created_by=actor_id,
        cookie_expires_at=extract_cookie_expiry(normalized),
    )
    test = await test_session(row.id, actor_id=actor_id)
    if test.ok:
        await youtube_session_repo.set_status(row.id, status="active", actor_id=actor_id)
        test = SessionTestResult(True, None, "active")
    return ImportedSessionResult(session_id=row.id, test=test)


async def rekey_session_pool(*, actor_id: int) -> RekeyPoolResult:
    """Re-encrypt every stored jar with the current key under one Redis-owned lease."""
    if not has_rotation_keys():
        raise CookieValidationError("rotation_key_missing")

    token = secrets.token_urlsafe(24)
    try:
        redis = await get_redis()
        acquired = await redis.set(
            youtube_rekey_lock_key(),
            token,
            nx=True,
            ex=TTL_YOUTUBE_REKEY_LOCK,
        )
    except Exception as exc:
        logger.warning("youtube_rekey_lock_unavailable error=%s", type(exc).__name__)
        return RekeyPoolResult(total=0, succeeded=0, failed=0, busy=True)
    if not acquired:
        return RekeyPoolResult(total=0, succeeded=0, failed=0, busy=True)

    total = succeeded = failed = 0
    after_id = 0
    try:
        while True:
            rows = await youtube_session_repo.list_session_batch(
                after_id=after_id,
                batch_size=_REKEY_BATCH_SIZE,
            )
            if not rows:
                break
            for row in rows:
                total += 1
                plain = decrypt_cookie_jar(row.cookie_blob_enc)
                if plain is None:
                    await youtube_session_repo.record_rekey_failure(
                        row.id,
                        actor_id=actor_id,
                        result_code="decrypt_failed",
                    )
                    failed += 1
                    continue
                replaced = await youtube_session_repo.replace_encrypted_blob_after_rekey(
                    row.id,
                    expected_blob=row.cookie_blob_enc,
                    replacement_blob=encrypt_cookie_jar(plain),
                    cookie_expires_at=extract_cookie_expiry(plain),
                    actor_id=actor_id,
                )
                if replaced:
                    succeeded += 1
                else:
                    await youtube_session_repo.record_rekey_failure(
                        row.id,
                        actor_id=actor_id,
                        result_code="stale_record",
                    )
                    failed += 1
            after_id = rows[-1].id
    finally:
        try:
            await redis.eval(_REKEY_RELEASE_LUA, 1, youtube_rekey_lock_key(), token)
        except Exception as exc:
            logger.warning("youtube_rekey_lock_release_failed error=%s", type(exc).__name__)

    return RekeyPoolResult(total=total, succeeded=succeeded, failed=failed)


def new_download_choice_token() -> str:
    return secrets.token_urlsafe(8).rstrip("=")
