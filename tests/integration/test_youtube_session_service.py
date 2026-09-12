"""Focused tests for the encrypted YouTube cookie-session pool."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.config.settings import settings
from app.repositories import youtube_session_repo
from app.services.youtube_session_service import (
    CookieValidationError,
    YtdlpRunResult,
    decrypt_cookie_jar,
    encrypt_cookie_jar,
    extract_cookie_expiry,
    fingerprint_cookie_jar,
    has_rotation_keys,
    is_playlist_without_video,
    is_youtube_url,
    normalize_cookie_jar,
    rekey_session_pool,
    run_ytdlp_for_media,
)
from app.utils.redis_keys import youtube_rekey_lock_key


def _configure_keys(monkeypatch):
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_CURRENT", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_OLD", "")
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_FINGERPRINT_KEY", "fingerprint-test-key")


def _jar(*, extra_domain: bool = False, expires_at: int = 2147483647) -> bytes:
    rows = [
        "# Netscape HTTP Cookie File",
        f".youtube.com\tTRUE\t/\tTRUE\t{expires_at}\tSID\tredacted",
        f"#HttpOnly_.google.com\tTRUE\t/\tTRUE\t{expires_at}\tHSID\tredacted",
    ]
    if extra_domain:
        rows.append(".example.com\tTRUE\t/\tTRUE\t2147483647\tX\tredacted")
    return ("\n".join(rows) + "\n").encode()


def test_cookie_normalization_filters_unrelated_domains_and_preserves_http_only():
    normalized = normalize_cookie_jar(_jar(extra_domain=True))
    assert "#HttpOnly_.google.com" in normalized
    assert "example.com" not in normalized
    assert normalized.startswith("# Netscape HTTP Cookie File\n")


def test_cookie_normalization_rejects_invalid_rows():
    with pytest.raises(CookieValidationError, match="invalid_cookie_row"):
        normalize_cookie_jar(b"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\n")


def test_cookie_encryption_round_trip_and_hmac_fingerprint(monkeypatch):
    _configure_keys(monkeypatch)
    normalized = normalize_cookie_jar(_jar())
    encrypted = encrypt_cookie_jar(normalized)

    assert encrypted != normalized
    assert decrypt_cookie_jar(encrypted) == normalized
    assert fingerprint_cookie_jar(normalized) == fingerprint_cookie_jar(normalized)


def test_cookie_expiry_tracks_only_authentication_cookies():
    normalized = normalize_cookie_jar(
        b"# Netscape HTTP Cookie File\n"
        b".youtube.com\tTRUE\t/\tTRUE\t200\tSID\tredacted\n"
        b".youtube.com\tTRUE\t/\tTRUE\t1\tCONSENT\tredacted\n"
        b".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tredacted\n"
    )
    assert extract_cookie_expiry(normalized) == datetime.fromtimestamp(200, tz=timezone.utc)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, *, nx=False, ex=None):  # noqa: ANN001, ANN201
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, numkeys, key, token):  # noqa: ANN001, ANN201
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_rekey_uses_old_key_then_allows_old_key_removal(monkeypatch):
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_CURRENT", old_key)
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_OLD", "")
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_FINGERPRINT_KEY", "fingerprint-test-key")
    normalized = normalize_cookie_jar(_jar())
    row = await youtube_session_repo.create_session(
        cookie_blob_enc=encrypt_cookie_jar(normalized),
        fingerprint="c" * 64,
        created_by=1,
    )
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_CURRENT", new_key)
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_OLD", old_key)
    assert has_rotation_keys() is True

    with patch(
        "app.services.youtube_session_service.get_redis",
        AsyncMock(return_value=_FakeRedis()),
    ):
        result = await rekey_session_pool(actor_id=1)

    assert result.succeeded == 1
    assert result.failed == 0
    updated = await youtube_session_repo.get_session(row.id)
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_OLD", "")
    assert decrypt_cookie_jar(updated.cookie_blob_enc) == normalized
    await youtube_session_repo.delete_session(row.id, actor_id=1)


@pytest.mark.asyncio
async def test_rekey_preserves_blob_when_decryption_fails(monkeypatch):
    _configure_keys(monkeypatch)
    old_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_OLD", old_key)
    row = await youtube_session_repo.create_session(
        cookie_blob_enc="not-a-fernet-token",
        fingerprint="d" * 64,
        created_by=1,
    )
    with patch(
        "app.services.youtube_session_service.get_redis",
        AsyncMock(return_value=_FakeRedis()),
    ):
        result = await rekey_session_pool(actor_id=1)

    assert result.succeeded == 0
    assert result.failed == 1
    assert (await youtube_session_repo.get_session(row.id)).cookie_blob_enc == "not-a-fernet-token"
    await youtube_session_repo.delete_session(row.id, actor_id=1)


@pytest.mark.asyncio
async def test_rekey_refuses_to_run_while_another_actor_holds_the_lease(monkeypatch):
    _configure_keys(monkeypatch)
    monkeypatch.setattr(settings, "YOUTUBE_COOKIE_KEY_OLD", Fernet.generate_key().decode())
    redis = _FakeRedis()
    redis.values[youtube_rekey_lock_key()] = "another-owner"
    with patch(
        "app.services.youtube_session_service.get_redis",
        AsyncMock(return_value=redis),
    ):
        result = await rekey_session_pool(actor_id=1)

    assert result.busy is True
    assert result.succeeded == 0


@pytest.mark.asyncio
async def test_rotation_uses_least_recently_selected_active_session(monkeypatch):
    _configure_keys(monkeypatch)
    first = await youtube_session_repo.create_session(
        cookie_blob_enc=encrypt_cookie_jar(normalize_cookie_jar(_jar())),
        fingerprint="a" * 64,
        created_by=1,
    )
    second = await youtube_session_repo.create_session(
        cookie_blob_enc=encrypt_cookie_jar(normalize_cookie_jar(_jar())),
        fingerprint="b" * 64,
        created_by=1,
    )
    await youtube_session_repo.set_status(first.id, status="active", actor_id=1)
    await youtube_session_repo.set_status(second.id, status="active", actor_id=1)

    selected_one = await youtube_session_repo.reserve_next_session()
    selected_two = await youtube_session_repo.reserve_next_session()

    assert {selected_one.id, selected_two.id} == {first.id, second.id}
    await youtube_session_repo.delete_session(first.id, actor_id=1)
    await youtube_session_repo.delete_session(second.id, actor_id=1)


@pytest.mark.asyncio
async def test_cookie_failure_retries_exactly_one_different_session(monkeypatch):
    first = type("Row", (), {"id": 10, "cookie_blob_enc": "x"})()
    second = type("Row", (), {"id": 11, "cookie_blob_enc": "x"})()
    summary = type("Summary", (), {"total": 2})()

    with (
        patch("app.services.youtube_session_service.youtube_session_repo.get_summary", AsyncMock(return_value=summary)),
        patch("app.services.youtube_session_service.has_required_keys", return_value=True),
        patch(
            "app.services.youtube_session_service.youtube_session_repo.reserve_next_session",
            AsyncMock(side_effect=[first, second]),
        ) as reserve,
        patch(
            "app.services.youtube_session_service._run_for_reserved_session",
            AsyncMock(side_effect=[
                YtdlpRunResult(1, "", "youtube_cooldown"),
                YtdlpRunResult(0, "stream-url\n", None),
            ]),
        ) as run,
    ):
        result = await run_ytdlp_for_media(
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
            ["yt-dlp", "https://www.youtube.com/shorts/dQw4w9WgXcQ"],
            timeout=60,
        )

    assert result.ok is True
    assert run.await_count == 2
    assert reserve.await_args_list[1].kwargs == {"exclude_id": 10}


def test_youtube_url_and_single_video_playlist_rules():
    assert is_youtube_url("https://youtu.be/dQw4w9WgXcQ")
    assert is_youtube_url("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
    assert is_youtube_url("https://www.youtube.com/shorts/dQw4w9WgXcQ")
    assert not is_youtube_url("https://notyoutube.com/watch?v=dQw4w9WgXcQ")
    assert is_playlist_without_video("https://www.youtube.com/playlist?list=PL123")
    assert not is_playlist_without_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123")
