"""Group play-command routing for Fast-Creat file delivery."""

from __future__ import annotations

import os
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

from app.services.fast_creat_media_service import (
    FastCreatMediaItem,
    FastCreatResolution,
)


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator


def _play_audio_handler():
    from app.handlers.playback import register

    bot = _RecorderBot()
    register(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")


def _message(text: str):
    message = AsyncMock()
    message.chat.id = -7001
    message.from_user = SimpleNamespace(id=42, first_name="Tester")
    message.text = text
    message.caption = None
    message.reply_to_message = None
    status = AsyncMock()
    message.reply = AsyncMock(return_value=status)
    message.reply_video = AsyncMock()
    message.reply_photo = AsyncMock()
    message.reply_document = AsyncMock()
    return message, status


@pytest.mark.asyncio
async def test_social_play_command_downloads_and_sends_video_without_voice_chat():
    handler = _play_audio_handler()
    url = "https://www.instagram.com/reel/abc/"
    message, status = _message(f"پخش {url}")
    resolution = FastCreatResolution(
        provider="instagram",
        source_url=url,
        title="clip",
        items=(FastCreatMediaItem("video", "https://cdn.example/clip.mp4"),),
    )
    with (
        patch("app.handlers.playback.get_download_denial_key", AsyncMock(return_value=None)),
        patch("app.handlers.playback.resolve_fast_creat_media", AsyncMock(return_value=resolution)),
        patch("app.handlers.playback.MediaService.download_direct_media", AsyncMock(return_value="/tmp/clip.mp4")),
        patch("app.handlers.playback.track_media_download", AsyncMock()) as tracked,
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=False)) as voice_auth,
        patch("app.handlers.playback.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await handler(AsyncMock(), message)

    message.reply_video.assert_awaited_once_with("/tmp/clip.mp4", caption="clip", supports_streaming=True)
    tracked.assert_awaited_once()
    status.edit_text.assert_awaited()
    voice_auth.assert_not_awaited()
    join.assert_not_awaited()


@pytest.mark.asyncio
async def test_spotify_play_command_falls_back_to_first_youtube_download():
    handler = _play_audio_handler()
    url = "https://open.spotify.com/track/abc"
    message, _status = _message(f"پخش {url}")
    resolution = FastCreatResolution(
        provider="spotify",
        source_url=url,
        items=(),
        title="Artist - Track",
        youtube_fallback_query="Artist - Track",
    )
    with (
        patch("app.handlers.playback.get_download_denial_key", AsyncMock(return_value=None)),
        patch("app.handlers.playback.resolve_fast_creat_media", AsyncMock(return_value=resolution)),
        patch("app.handlers.search.resolve_youtube_first_result", AsyncMock(return_value={"url": "https://youtube.com/watch?v=abc"})) as search,
        patch("app.handlers.playback.MediaService.download_audio", AsyncMock(return_value="/tmp/track.opus")),
        patch("app.handlers.playback.track_media_download", AsyncMock()),
        patch("app.handlers.playback.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await handler(AsyncMock(), message)

    search.assert_awaited_once_with("Artist - Track")
    message.reply_document.assert_awaited_once_with("/tmp/track.opus")
    join.assert_not_awaited()


@pytest.mark.asyncio
async def test_spotify_invalid_direct_file_still_uses_oembed_youtube_fallback():
    handler = _play_audio_handler()
    url = "https://open.spotify.com/track/def"
    message, _status = _message(f"پخش {url}")
    resolution = FastCreatResolution(
        provider="spotify",
        source_url=url,
        items=(FastCreatMediaItem("audio", "https://cdn.example/not-audio"),),
        title="Artist - Track",
    )
    with (
        patch("app.handlers.playback.get_download_denial_key", AsyncMock(return_value=None)),
        patch("app.handlers.playback.resolve_fast_creat_media", AsyncMock(return_value=resolution)),
        patch("app.handlers.playback.MediaService.download_direct_media", AsyncMock(return_value=None)),
        patch(
            "app.handlers.playback.resolve_spotify_fallback_query",
            AsyncMock(return_value="Artist - Track"),
        ) as metadata,
        patch(
            "app.handlers.search.resolve_youtube_first_result",
            AsyncMock(return_value={"url": "https://youtube.com/watch?v=def"}),
        ) as search,
        patch("app.handlers.playback.MediaService.download_audio", AsyncMock(return_value="/tmp/track.opus")),
        patch("app.handlers.playback.track_media_download", AsyncMock()),
        patch("app.handlers.playback.CallService.join_voice_chat", AsyncMock()) as join,
    ):
        await handler(AsyncMock(), message)

    metadata.assert_awaited_once_with(url)
    search.assert_awaited_once_with("Artist - Track")
    message.reply_document.assert_awaited_once_with("/tmp/track.opus")
    join.assert_not_awaited()
