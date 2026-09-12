"""Playback join failures map to specific Persian i18n keys."""
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


def _local_track(downloads: Path, chat_id: int = -1001, name: str = "track.ogg") -> str:
    chat_dir = downloads / str(chat_id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    path = chat_dir / name
    path.write_bytes(b"audio-bytes")
    return str(path.resolve())


@pytest.fixture(autouse=True)
def _clear_join_failure_key():
    from app.services.call_service import CallService

    CallService.pop_join_failure_key()
    yield
    CallService.pop_join_failure_key()


@pytest.mark.asyncio
async def test_join_failure_helper_unavailable(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads)

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=None)),
    ):
        ok = await CallService.join_voice_chat(MagicMock(), -1001, local_path, "audio")

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.helper_unavailable"


@pytest.mark.asyncio
async def test_join_failure_voice_chat_unavailable_when_call_py_none(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-1002)

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=None)),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
    ):
        ok = await CallService.join_voice_chat(None, -1002, local_path, "audio")

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.voice_chat_unavailable"


@pytest.mark.asyncio
async def test_join_failure_stream_unavailable(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-1003)

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=local_path)),
        patch("app.utils.voice_stack.build_audio_stream", return_value=None),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
    ):
        ok = await CallService.join_voice_chat(MagicMock(), -1003, local_path, "audio")

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.stream_unavailable"


@pytest.mark.asyncio
async def test_join_failure_no_voice_chat_on_exception(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-1004)
    call_py = SimpleNamespace(
        join_group_call=AsyncMock(side_effect=RuntimeError("NO_ACTIVE_GROUP_CALL")),
    )

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=local_path)),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
        patch("app.services.call_service._maybe_quarantine_helper", AsyncMock()),
    ):
        ok = await CallService.join_voice_chat(call_py, -1004, local_path, "audio")

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.no_voice_chat"


@pytest.mark.asyncio
async def test_join_failure_invalid_source():
    from app.services.call_service import CallService

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value=None),
        ),
    ):
        ok = await CallService.join_voice_chat(MagicMock(), -1005, "/outside/evil.mp3", "audio")

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.invalid_source"


@pytest.mark.asyncio
async def test_join_failure_free_mode_video_denied():
    from app.services.call_service import CallService

    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=False),
        ),
    ):
        ok = await CallService.join_voice_chat(
            MagicMock(), -1006, "https://example.com/v.mp4", "video"
        )

    assert ok is False
    assert CallService.pop_join_failure_key() == "free_mode.blocked_video"


@pytest.mark.asyncio
async def test_play_audio_replies_with_helper_unavailable_message(media_roots):
    from app.handlers.playback import register as register_playback
    from app.utils.i18n import t

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-2003, name="track.mp3")

    message = AsyncMock()
    message.chat.id = -2003
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش"
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        document=None,
        text=None,
    )
    message.reply = AsyncMock()

    client = AsyncMock()
    handlers = []
    bot = MagicMock()

    def capture_on_message(*args, **kwargs):
        def decorator(fn):
            handlers.append(fn)
            return fn

        return decorator

    bot.on_message = capture_on_message
    register_playback(bot, MagicMock())
    play_handler = next(fn for fn in handlers if fn.__name__ == "play_audio")

    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value=local_path),
        ),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=None)),
    ):
        await play_handler(client, message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "playback_cmd.helper_unavailable")
