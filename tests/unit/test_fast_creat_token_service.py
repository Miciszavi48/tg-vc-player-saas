"""Focused security and fair-selection tests for Fast-Creat token storage."""

from __future__ import annotations

import importlib
import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.config.settings import settings
from app.repositories import fast_creat_token_repo
from app.services.fast_creat_token_service import (
    FastCreatTokenError,
    add_token,
    decrypt_token,
    encrypt_token,
    fingerprint_token,
    has_required_keys,
    reserve_token,
)


def _configure_keys(monkeypatch):
    monkeypatch.setattr(settings, "FAST_CREAT_TOKEN_KEY_CURRENT", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "FAST_CREAT_TOKEN_KEY_OLD", "")
    monkeypatch.setattr(
        settings,
        "FAST_CREAT_TOKEN_FINGERPRINT_KEY",
        "fast-creat-test-fingerprint-key-32-bytes",
    )


def test_token_encryption_and_fingerprint_never_return_plaintext(monkeypatch):
    _configure_keys(monkeypatch)
    raw = "token:with@characters"
    encrypted = encrypt_token(raw)

    assert encrypted != raw
    assert decrypt_token(encrypted) == raw
    assert fingerprint_token(raw) == fingerprint_token(raw)


def test_fast_creat_migration_is_current_model_contract():
    from app.database.models import FastCreatApiToken, FastCreatApiTokenEvent

    migration = importlib.import_module(
        "app.database.migrations.versions.0032_fast_creat_token_pool"
    )
    assert migration.revision == "0032_fast_creat_token_pool"
    assert migration.down_revision == "0031_youtube_cookie_hardening"
    assert str(FastCreatApiToken.__table__.c.status.server_default.arg) == "'active'"
    assert str(FastCreatApiToken.__table__.c.use_count.server_default.arg) == "0"
    assert str(FastCreatApiToken.__table__.c.failure_count.server_default.arg) == "0"
    assert {index.name for index in FastCreatApiTokenEvent.__table__.indexes} == {
        "idx_fast_creat_api_token_events_token_at"
    }


def test_malformed_fernet_key_is_treated_as_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "FAST_CREAT_TOKEN_KEY_CURRENT", "not-a-fernet-key")
    monkeypatch.setattr(settings, "FAST_CREAT_TOKEN_FINGERPRINT_KEY", "test-hmac-key")

    assert has_required_keys() is False


def test_encryption_and_fingerprint_keys_must_be_independent_and_strong(monkeypatch):
    encryption_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "FAST_CREAT_TOKEN_KEY_CURRENT", encryption_key)
    monkeypatch.setattr(settings, "FAST_CREAT_TOKEN_FINGERPRINT_KEY", "too-short")
    assert has_required_keys() is False

    monkeypatch.setattr(settings, "FAST_CREAT_TOKEN_FINGERPRINT_KEY", encryption_key)
    assert has_required_keys() is False


@pytest.mark.asyncio
async def test_concurrent_duplicate_constraint_is_mapped_to_safe_error(monkeypatch):
    _configure_keys(monkeypatch)
    error = IntegrityError("insert", {}, RuntimeError("unique constraint"))
    with patch(
        "app.services.fast_creat_token_service.fast_creat_token_repo.create_token",
        AsyncMock(side_effect=error),
    ):
        with pytest.raises(FastCreatTokenError, match="duplicate_token"):
            await add_token(provider="instagram", value="duplicate-race", actor_id=1)


@pytest.mark.asyncio
async def test_provider_tokens_are_separate_unlimited_and_selected_round_robin(monkeypatch):
    _configure_keys(monkeypatch)
    suffix = uuid.uuid4().hex
    token_ids: list[int] = []
    try:
        for value in (f"ig-one-{suffix}", f"ig-two-{suffix}", f"ig-three-{suffix}"):
            token_ids.append(await add_token(provider="instagram", value=value, actor_id=1))
        spotify_id = await add_token(provider="spotify", value=f"sp-{suffix}", actor_id=1)
        token_ids.append(spotify_id)

        first = await reserve_token("instagram")
        second = await reserve_token("instagram")
        spotify = await reserve_token("spotify")

        assert first is not None and second is not None and spotify is not None
        assert first.id != second.id
        assert spotify.id == spotify_id
        assert first.provider == second.provider == "instagram"
        assert spotify.provider == "spotify"
        assert await fast_creat_token_repo.get_by_fingerprint("tiktok", fingerprint_token(first.value)) is None
    finally:
        for token_id in token_ids:
            await fast_creat_token_repo.delete_token(token_id, actor_id=1)


@pytest.mark.asyncio
async def test_duplicate_tokens_are_rejected_per_provider_and_rate_limit_moves_to_next(monkeypatch):
    _configure_keys(monkeypatch)
    suffix = uuid.uuid4().hex
    token_ids: list[int] = []
    try:
        first_id = await add_token(provider="tiktok", value=f"one-{suffix}", actor_id=1)
        second_id = await add_token(provider="tiktok", value=f"two-{suffix}", actor_id=1)
        token_ids.extend((first_id, second_id))
        with pytest.raises(FastCreatTokenError, match="duplicate_token"):
            await add_token(provider="tiktok", value=f"one-{suffix}", actor_id=1)

        first = await reserve_token("tiktok")
        assert first is not None
        await fast_creat_token_repo.mark_rate_limited(first.id)
        second = await reserve_token("tiktok")

        assert second is not None
        assert second.id != first.id
        cooled = await fast_creat_token_repo.get_token(first.id)
        assert cooled.cooldown_until is not None
    finally:
        for token_id in token_ids:
            await fast_creat_token_repo.delete_token(token_id, actor_id=1)


@pytest.mark.asyncio
async def test_undecryptable_token_is_invalidated_without_exhausting_other_tokens(monkeypatch):
    _configure_keys(monkeypatch)
    corrupt = SimpleNamespace(id=1, provider="spotify", token_enc="not-a-fernet-token")
    usable = SimpleNamespace(
        id=2,
        provider="spotify",
        token_enc=encrypt_token("usable-token"),
    )
    with (
        patch(
            "app.services.fast_creat_token_service.fast_creat_token_repo.reserve_next_token",
            AsyncMock(side_effect=[corrupt, usable]),
        ) as reserve,
        patch(
            "app.services.fast_creat_token_service.fast_creat_token_repo.mark_invalid",
            AsyncMock(),
        ) as invalid,
    ):
        selected = await reserve_token("spotify")

    assert selected is not None
    assert selected.id == usable.id
    assert selected.value == "usable-token"
    invalid.assert_awaited_once_with(corrupt.id, code="decrypt_failed")
    assert reserve.await_args_list[1].kwargs["exclude_ids"] == {corrupt.id}
