"""Phase B5-1: centralized now-playing renderer and per-chat language."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest.fixture
def media_roots(tmp_path, monkeypatch):
    from app.config.settings import settings

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    cache = tmp_path / "media_cache"
    cache.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache))
    return downloads, cache


@pytest.mark.asyncio
async def test_renderer_returns_persian_by_default():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    with patch(
        "app.services.now_playing_renderer.resolve_lang",
        AsyncMock(return_value="fa"),
    ):
        text = await render_now_playing_text(
            -1001,
            NowPlayingContext(title=None, media_type="audio"),
        )

    assert "موزیک در حال پخش" in text
    assert "در حال پخش" in text


@pytest.mark.asyncio
async def test_renderer_returns_english_when_language_en():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Track", media_type="audio"),
        lang="en",
    )

    assert "Music Playing" in text
    assert "Title: Track" in text


@pytest.mark.asyncio
async def test_invalid_language_falls_back_to_persian():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title=None, media_type="audio"),
        lang="de",
    )

    assert "موزیک در حال پخش" in text


@pytest.mark.asyncio
async def test_renderer_includes_title():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="My Song", media_type="audio"),
        lang="fa",
    )

    assert "عنوان: My Song" in text


@pytest.mark.asyncio
async def test_renderer_maps_media_type_labels_fa_en():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    fa_text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Channel 1", media_type="tv"),
        lang="fa",
    )
    en_text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Channel 1", media_type="tv"),
        lang="en",
    )

    assert "نوع: تلویزیون" in fa_text
    assert "Type: TV" in en_text


@pytest.mark.asyncio
async def test_renderer_does_not_include_source_url():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(
            title="https://youtube.com/watch?v=abc123def45",
            media_type="audio",
        ),
        lang="en",
    )

    assert "https://" not in text
    assert "youtube.com" not in text


@pytest.mark.asyncio
async def test_renderer_does_not_include_local_file_path(media_roots):
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    downloads, _ = media_roots
    local_path = downloads / "1001" / "track.ogg"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"x")

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title=str(local_path), media_type="audio"),
        lang="en",
    )

    assert ".ogg" not in text
    assert str(downloads) not in text


@pytest.mark.asyncio
async def test_renderer_does_not_include_telegram_file_id():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    file_id = "BQACAgQAAxkBAAISampleTelegramFileIdValue1234567890"
    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title=file_id, media_type="audio"),
        lang="en",
    )

    assert file_id not in text


@pytest.mark.asyncio
async def test_renderer_does_not_include_requester_id():
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(
            title="Song",
            media_type="audio",
            requester_name="Ali",
            requester_id=987654321,
        ),
        lang="en",
    )

    assert "987654321" not in text
    assert "Ali" in text


def test_requester_display_name_uses_username_not_id():
    from app.services.now_playing_renderer import requester_display_name

    user = SimpleNamespace(
        id=12345,
        username="meliodas",
        first_name="Test",
        last_name=None,
    )

    assert requester_display_name(user) == "@meliodas"


def test_playback_handler_source_uses_renderer():
    source = Path("app/handlers/playback.py").read_text(encoding="utf-8")

    assert "render_now_playing_text" in source
    assert "resolve_lang" in source
    assert 't(_LANG, "playback_cmd.playing_audio")' not in source
    assert 't(_LANG, "playback_cmd.playing_video")' not in source


def test_search_handler_source_uses_renderer_and_stays_audio_only():
    source = Path("app/handlers/search.py").read_text(encoding="utf-8")

    assert "render_now_playing_text" in source
    assert 'media_type = "audio"' in source
    assert "display_media_type" in source
    assert 't(_LANG, "playback_cmd.playing_audio")' not in source


def test_tv_radio_handler_source_uses_renderer():
    source = Path("app/handlers/tv_radio.py").read_text(encoding="utf-8")

    assert "_render_playing_message" in source
    assert 'media_type="radio"' in source
    assert 'media_type="tv"' in source
    assert 'media_type="satellite"' in source
    assert 't(_LANG, "tv_radio.playing"' not in source


def test_queue_on_busy_message_keys_unchanged():
    from app.services.playback_dispatch_service import (
        BusyPlaybackAction,
        _MESSAGE_KEYS,
    )

    assert _MESSAGE_KEYS[BusyPlaybackAction.QUEUED] == "playback_cmd.queued"
    assert _MESSAGE_KEYS[BusyPlaybackAction.ALREADY_PLAYING] == "playback_cmd.already_playing"
    assert _MESSAGE_KEYS[BusyPlaybackAction.QUEUE_UNAVAILABLE_TEMP] == (
        "playback_cmd.queue_unavailable_for_temp_file"
    )


@pytest.mark.asyncio
async def test_buttons_enabled_does_not_affect_renderer_text():
    from app.services.media_capability_service import build_now_playing_controls
    from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text
    from app.utils.ui import CB

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Song", media_type="audio"),
        lang="fa",
    )

    cs_on = SimpleNamespace(buttons_enabled=True)
    cs_off = SimpleNamespace(buttons_enabled=False)

    with patch(
        "app.repositories.settings_repo.get_chat_settings",
        AsyncMock(side_effect=[cs_on, cs_off]),
    ):
        kb_on = await build_now_playing_controls("fa", -1001)
        kb_off = await build_now_playing_controls("fa", -1001)

    on_cbs = {b.callback_data for row in kb_on.inline_keyboard for b in row}
    off_cbs = {b.callback_data for row in kb_off.inline_keyboard for b in row}
    assert CB["PB_VOL_DOWN"] in on_cbs
    assert CB["PB_VOL_DOWN"] not in off_cbs
    assert "Song" in text


@pytest.mark.asyncio
async def test_renderer_accepts_explicit_display_flags():
    from app.services.now_playing_renderer import (
        NowPlayingContext,
        NowPlayingDisplayFlags,
        render_now_playing_text,
    )

    text = await render_now_playing_text(
        -1001,
        NowPlayingContext(title="Song", media_type="audio"),
        lang="fa",
        flags=NowPlayingDisplayFlags(show_now_playing_text=True),
    )
    assert "Song" in text
