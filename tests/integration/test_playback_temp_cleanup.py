"""Temporary playback download cleanup on voice-chat failure."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


def _local_track(downloads: Path, chat_id: int = -1001, name: str = "track.mp3") -> Path:
    chat_dir = downloads / str(chat_id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    path = chat_dir / name
    path.write_bytes(b"audio-bytes")
    return path.resolve()


@pytest.mark.asyncio
async def test_join_voice_chat_false_deletes_temp_local_file(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads)
    call_py = SimpleNamespace(join_group_call=AsyncMock())

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=str(local_path))),
        patch("app.utils.voice_stack.build_audio_stream", return_value=None),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
    ):
        ok = await CallService.join_voice_chat(
            call_py,
            -1001,
            str(local_path),
            "audio",
            cleanup_local_source_on_failure=True,
        )

    assert ok is False
    assert not local_path.exists()


@pytest.mark.asyncio
async def test_join_voice_chat_exception_deletes_temp_local_file(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, name="voice.ogg")
    call_py = SimpleNamespace(join_group_call=AsyncMock(side_effect=RuntimeError("pytgcalls down")))

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=str(local_path))),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
        patch("app.services.call_service._maybe_quarantine_helper", AsyncMock()),
    ):
        ok = await CallService.join_voice_chat(
            call_py,
            -1002,
            str(local_path),
            "audio",
            cleanup_local_source_on_failure=True,
        )

    assert ok is False
    assert not local_path.exists()


@pytest.mark.asyncio
async def test_join_voice_chat_success_keeps_temp_local_file(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, name="keep.mp3")
    call_py = SimpleNamespace(join_group_call=AsyncMock())

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=str(local_path))),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.utils.voice_stack.vc_join", AsyncMock()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.seek_tracker.start_seek_tracker", AsyncMock()),
        patch("app.services.media_event_service.track_media_play", AsyncMock()),
    ):
        ok = await CallService.join_voice_chat(
            call_py,
            -1003,
            str(local_path),
            "audio",
            cleanup_local_source_on_failure=True,
        )

    assert ok is True
    assert local_path.exists()


@pytest.mark.asyncio
async def test_url_playback_failure_does_not_delete_unrelated_local_file(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    unrelated = _local_track(downloads, name="playlist_item.mp3")
    url = "https://93.184.216.34/stream.mp3"
    call_py = SimpleNamespace(join_group_call=AsyncMock())

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.utils.media_sources.validate_safe_url_with_redirects",
            AsyncMock(return_value=url),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=url)),
        patch("app.utils.voice_stack.build_audio_stream", return_value=None),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
    ):
        ok = await CallService.join_voice_chat(
            call_py,
            -1004,
            url,
            "audio",
            cleanup_local_source_on_failure=True,
        )

    assert ok is False
    assert unrelated.exists()


@pytest.mark.asyncio
async def test_download_command_keeps_file_until_sent(media_roots):
    from app.handlers import download as download_handler

    downloads, _ = media_roots
    local_path = _local_track(downloads, name="user_download.mp3")
    message = AsyncMock()
    message.chat.id = -1005
    message.from_user = SimpleNamespace(id=99)
    message.text = "download"
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(file_unique_id="uniq-audio"),
        video=None,
        text=None,
    )
    message.reply = AsyncMock()
    message.reply_document = AsyncMock()

    client = AsyncMock()
    client.download_media = AsyncMock(return_value=str(local_path))

    with (
        patch(
            "app.handlers.download.get_download_denial_key",
            AsyncMock(return_value=None),
        ),
        patch("app.services.media_event_service.track_media_download", AsyncMock()),
    ):
        handlers = []
        bot = MagicMock()

        def capture_on_message(*args, **kwargs):
            def decorator(fn):
                handlers.append(fn)
                return fn

            return decorator

        bot.on_message = capture_on_message
        download_handler.register(bot, None)
        # Select by name: the name-based download commands (SEARCH-06/07) must
        # register before the generic URL handler, so index 0 is not it.
        url_handler = next(h for h in handlers if h.__name__ == "download_media")
        await url_handler(client, message)

    assert local_path.exists()
    message.reply_document.assert_awaited()


@pytest.mark.asyncio
async def test_safe_unlink_logs_warning_without_crashing(media_roots, caplog):
    from app.utils.media_sources import safe_unlink_temp_media

    downloads, _ = media_roots
    local_path = _local_track(downloads, name="warn.mp3")

    with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
        with caplog.at_level("WARNING"):
            safe_unlink_temp_media(local_path, reason="test_warning")

    assert any("Failed to remove temporary playback file" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_stream_build_failure_triggers_cleanup_with_flag(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, name="no_stream.mp3")
    call_py = SimpleNamespace(join_group_call=AsyncMock())

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=str(local_path))),
        patch("app.utils.voice_stack.build_audio_stream", return_value=None),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
    ):
        ok = await CallService.join_voice_chat(
            call_py,
            -1006,
            str(local_path),
            "audio",
            cleanup_local_source_on_failure=True,
        )

    assert ok is False
    assert not local_path.exists()


@pytest.mark.asyncio
async def test_play_voice_reply_cleanup_on_join_failure(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-2002, name="reply_voice.ogg")
    call_py = SimpleNamespace(join_group_call=AsyncMock(side_effect=RuntimeError("pytgcalls down")))

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=str(local_path))),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
        patch("app.services.call_service._maybe_quarantine_helper", AsyncMock()),
    ):
        ok = await CallService.join_voice_chat(
            call_py,
            -2002,
            str(local_path),
            "audio",
            cleanup_local_source_on_failure=True,
        )

    assert ok is False
    assert not local_path.exists()


@pytest.mark.asyncio
async def test_helper_unavailable_deletes_temp_local_file(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-1007, name="no_helper.mp3")

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value=str(local_path)),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=None)),
    ):
        ok = await CallService.join_voice_chat(
            SimpleNamespace(join_group_call=AsyncMock()),
            -1007,
            str(local_path),
            "audio",
            cleanup_local_source_on_failure=True,
        )

    assert ok is False
    assert not local_path.exists()
    assert CallService.pop_join_failure_key() == "playback_cmd.helper_unavailable"


@pytest.mark.asyncio
async def test_play_audio_reply_cleanup_on_join_false(media_roots):
    from app.handlers.playback import register as register_playback

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-2001, name="reply_audio.mp3")

    message = AsyncMock()
    message.chat.id = -2001
    message.from_user = SimpleNamespace(id=42)
    message.text = "play"
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        text=None,
    )
    message.reply = AsyncMock()

    client = AsyncMock()
    call_py = MagicMock()
    handlers = []
    bot = MagicMock()

    def capture_on_message(*args, **kwargs):
        def decorator(fn):
            handlers.append(fn)
            return fn

        return decorator

    bot.on_message = capture_on_message
    register_playback(bot, call_py)
    play_handler = next(fn for fn in handlers if fn.__name__ == "play_audio")

    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value=str(local_path)),
        ),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=str(local_path))),
        patch("app.utils.voice_stack.build_audio_stream", return_value=None),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
    ):
        await play_handler(client, message)

    assert not local_path.exists()
    message.reply.assert_awaited()
