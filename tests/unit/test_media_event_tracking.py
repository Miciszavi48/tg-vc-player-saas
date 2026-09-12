"""Tests for media event fingerprinting and tracking."""
from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.database.models import MediaEvent
from app.services.media_event_service import track_media_download, track_media_play
from app.utils.media_source_fingerprint import build_media_source_fields, build_telegram_media_fields


def test_media_event_model_exists():
    assert MediaEvent.__tablename__ == "media_events"
    assert hasattr(MediaEvent, "url_fingerprint")
    assert hasattr(MediaEvent, "redacted_url")


def test_url_fingerprint_is_deterministic():
    url = "https://youtube.com/watch?v=abc&token=secret"
    first = build_media_source_fields(url)
    second = build_media_source_fields(url)
    assert first is not None
    assert second is not None
    assert first.url_fingerprint == second.url_fingerprint


def test_url_fingerprint_is_not_raw_url():
    url = "https://example.com/watch?v=abc&token=secret"
    fields = build_media_source_fields(url)
    assert fields is not None
    assert fields.url_fingerprint != url
    assert "secret" not in fields.url_fingerprint


def test_redacted_url_strips_query_and_fragment():
    url = "https://youtube.com/watch?v=abc&token=secret#section"
    fields = build_media_source_fields(url)
    assert fields is not None
    assert "?" not in fields.redacted_url
    assert "#" not in fields.redacted_url
    assert "secret" not in fields.redacted_url
    assert fields.redacted_url == "https://youtube.com/watch"


def test_local_path_stored_safely(tmp_path, monkeypatch):
    root = tmp_path / "downloads"
    root.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(root))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache))
    file_path = root / "123" / "track.mp3"
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"audio")

    fields = build_media_source_fields(str(file_path))
    assert fields is not None
    assert fields.source_kind == "local"
    assert str(root) not in fields.redacted_url
    assert fields.redacted_url.replace("\\", "/") == "123/track.mp3"


@pytest.mark.asyncio
async def test_track_media_play_inserts_event():
    with patch(
        "app.services.media_event_service._insert_media_event",
        AsyncMock(),
    ) as insert_mock:
        await track_media_play(
            "https://example.com/watch?v=abc&token=secret",
            media_type="audio",
            chat_id=1,
            user_id=2,
        )
    insert_mock.assert_awaited_once()
    args = insert_mock.await_args.args
    assert args[0] == "play"
    assert "secret" not in args[1].redacted_url


@pytest.mark.asyncio
async def test_track_media_download_inserts_event():
    with patch(
        "app.services.media_event_service._insert_media_event",
        AsyncMock(),
    ) as insert_mock:
        await track_media_download(
            "https://example.com/song?sig=abc",
            media_type="audio",
            chat_id=10,
            user_id=20,
        )
    insert_mock.assert_awaited_once()
    assert insert_mock.await_args.args[0] == "download"


@pytest.mark.asyncio
async def test_tracking_failure_does_not_break_playback():
    with patch(
        "app.services.media_event_service._insert_media_event",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        await track_media_play("https://example.com/watch?v=abc&token=secret")


def test_telegram_media_fields_do_not_store_tokens():
    fields = build_telegram_media_fields("unique123", "audio")
    assert fields.redacted_url == "[telegram audio]"
    assert "unique123" not in fields.redacted_url
    assert len(fields.url_fingerprint) == 64


def test_sensitive_query_params_not_in_storage_fields():
    fields = build_media_source_fields("https://cdn.example.com/file.m3u8?token=abc&expires=999")
    assert fields is not None
    assert "token=" not in fields.redacted_url
    assert "expires=" not in fields.redacted_url
