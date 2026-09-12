"""Focused SoundCloud download coverage for the existing group command."""

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

from app.handlers import download
from app.utils.i18n import t


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _download_handler():
    bot = _RecorderBot()
    download.register(bot, None)
    return next(handler for handler in bot.message_handlers if handler.__name__ == "download_media")


def _message(url: str):
    status = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        from_user=SimpleNamespace(id=42),
        text=f"دانلود {url}",
        reply_to_message=None,
        reply=AsyncMock(return_value=status),
        reply_document=AsyncMock(),
    )
    return message, status


@pytest.mark.asyncio
async def test_soundcloud_track_uses_existing_audio_download_pipeline():
    url = "https://soundcloud.com/artist-name/track-name"
    message, status = _message(url)

    with (
        patch("app.handlers.download._download_allowed", AsyncMock(return_value=None)),
        patch(
            "app.handlers.download.validate_safe_url_with_redirects",
            AsyncMock(return_value=url),
        ),
        patch(
            "app.handlers.download.MediaService.download_audio",
            AsyncMock(return_value="/tmp/track.opus"),
        ) as download_audio,
        patch("app.handlers.download.track_media_download", AsyncMock()) as track_download,
    ):
        await _download_handler()(SimpleNamespace(), message)

    message.reply.assert_awaited_once_with(t("fa", "download_cmd.soundcloud_downloading"))
    download_audio.assert_awaited_once_with(url, -100123)
    message.reply_document.assert_awaited_once_with("/tmp/track.opus")
    track_download.assert_awaited_once_with(
        url,
        media_type="audio",
        chat_id=-100123,
        user_id=42,
    )
    status.edit_text.assert_awaited_once_with(t("fa", "download_cmd.completed"))


@pytest.mark.asyncio
async def test_soundcloud_short_link_uses_validated_canonical_track_url():
    short_url = "https://on.soundcloud.com/share-token"
    canonical_url = "https://soundcloud.com/artist-name/track-name"
    message, _status = _message(short_url)

    with (
        patch("app.handlers.download._download_allowed", AsyncMock(return_value=None)),
        patch(
            "app.handlers.download.validate_safe_url_with_redirects",
            AsyncMock(return_value=canonical_url),
        ),
        patch(
            "app.handlers.download.MediaService.download_audio",
            AsyncMock(return_value="/tmp/track.opus"),
        ) as download_audio,
        patch("app.handlers.download.track_media_download", AsyncMock()),
    ):
        await _download_handler()(SimpleNamespace(), message)

    download_audio.assert_awaited_once_with(canonical_url, -100123)
    message.reply.assert_awaited_once_with(t("fa", "download_cmd.soundcloud_downloading"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://soundcloud.com/artist-name",
        "https://soundcloud.com/artist-name/sets/release-name",
        "https://soundcloud.com/artist-name%2Fsets/release-name",
        "https://soundcloud.com/artist-name/tracks",
        "https://soundcloud.com/discover",
    ],
)
async def test_soundcloud_profile_or_collection_is_rejected_before_ytdlp(url: str):
    message, _status = _message(url)

    with (
        patch("app.handlers.download._download_allowed", AsyncMock(return_value=None)),
        patch(
            "app.handlers.download.validate_safe_url_with_redirects",
            AsyncMock(return_value=url),
        ),
        patch(
            "app.handlers.download.MediaService.download_audio",
            AsyncMock(),
        ) as download_audio,
    ):
        await _download_handler()(SimpleNamespace(), message)

    message.reply.assert_awaited_once_with(t("fa", "download_cmd.soundcloud_track_only"))
    download_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsafe_url_is_rejected_before_soundcloud_or_generic_download():
    message, _status = _message("http://127.0.0.1/private.mp3")

    with (
        patch("app.handlers.download._download_allowed", AsyncMock(return_value=None)),
        patch(
            "app.handlers.download.validate_safe_url_with_redirects",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.handlers.download.MediaService.download_audio",
            AsyncMock(),
        ) as download_audio,
    ):
        await _download_handler()(SimpleNamespace(), message)

    message.reply.assert_awaited_once_with(t("fa", "download_cmd.blocked_url"))
    download_audio.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_non_soundcloud_url_keeps_generic_audio_download_behavior():
    url = "https://example.org/track.mp3"
    message, status = _message(url)

    with (
        patch("app.handlers.download._download_allowed", AsyncMock(return_value=None)),
        patch(
            "app.handlers.download.validate_safe_url_with_redirects",
            AsyncMock(return_value=url),
        ),
        patch(
            "app.handlers.download.MediaService.download_audio",
            AsyncMock(return_value="/tmp/track.opus"),
        ) as download_audio,
        patch("app.handlers.download.track_media_download", AsyncMock()),
    ):
        await _download_handler()(SimpleNamespace(), message)

    message.reply.assert_awaited_once_with(t("fa", "download_cmd.downloading"))
    download_audio.assert_awaited_once_with(url, -100123)
    status.edit_text.assert_awaited_once_with(t("fa", "download_cmd.completed"))


@pytest.mark.asyncio
async def test_youtube_url_opens_existing_audio_video_choice_before_download():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    message, _status = _message(url)
    markup = MagicMock()

    with (
        patch("app.handlers.download._download_allowed", AsyncMock(return_value=None)),
        patch(
            "app.handlers.download.validate_safe_url_with_redirects",
            AsyncMock(return_value=url),
        ),
        patch("app.handlers.download.is_playlist_without_video", return_value=False),
        patch("app.handlers.download.is_youtube_url", return_value=True),
        patch(
            "app.handlers.download._store_download_choice",
            AsyncMock(return_value="token123"),
        ) as store_choice,
        patch(
            "app.handlers.download.KeyboardFactory.youtube_download_format",
            return_value=markup,
        ) as format_keyboard,
        patch("app.handlers.download.MediaService.download_audio", AsyncMock()) as download_audio,
    ):
        await _download_handler()(SimpleNamespace(), message)

    store_choice.assert_awaited_once_with(
        user_id=42,
        chat_id=-100123,
        url=url,
    )
    format_keyboard.assert_called_once_with("fa", "token123")
    message.reply.assert_awaited_once_with(
        t("fa", "youtube_sessions.choose_format"),
        reply_markup=markup,
    )
    download_audio.assert_not_awaited()
