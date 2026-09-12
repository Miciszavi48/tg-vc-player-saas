"""Focused safety tests for the user-bound YouTube audio/video choice."""

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

from app.handlers.download import _claim_download_choice, _store_download_choice
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


def _query(user_id: int, chat_id: int = -100123):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
    )


@pytest.mark.asyncio
async def test_download_choice_is_user_bound_and_consumed_once():
    token = await _store_download_choice(
        user_id=10,
        chat_id=-100123,
        url="https://www.youtube.com/shorts/dQw4w9WgXcQ",
    )

    assert await _claim_download_choice(_query(11), token) is None
    claimed = await _claim_download_choice(_query(10), token)
    assert claimed == {
        "v": 1,
        "chat_id": -100123,
        "url": "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    }
    assert await _claim_download_choice(_query(10), token) is None


def test_download_format_keyboard_uses_short_nonsecret_callbacks():
    callbacks = {
        button.callback_data
        for row in KeyboardFactory.youtube_download_format("en", "token123").inline_keyboard
        for button in row
    }
    assert f"{CB['DOWNLOAD_FORMAT_PREFIX']}a:token123" in callbacks
    assert f"{CB['DOWNLOAD_FORMAT_PREFIX']}v:token123" in callbacks
    assert f"{CB['DOWNLOAD_FORMAT_PREFIX']}x:token123" in callbacks


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        return lambda handler: handler

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(handler):
            self.callback_handlers.append(handler)
            return handler

        return _decorator


def _youtube_download_choice_handler():
    from app.handlers import download

    bot = _RecorderBot()
    download.register(bot, MagicMock())
    return next(
        handler
        for handler in bot.callback_handlers
        if handler.__name__ == "youtube_download_format_choice"
    )


@pytest.mark.asyncio
async def test_audio_choice_sends_the_downloaded_file_to_the_group():
    message = SimpleNamespace(
        chat=SimpleNamespace(id=-100123),
        edit_text=AsyncMock(),
        reply_document=AsyncMock(),
        reply_video=AsyncMock(),
        reply=AsyncMock(),
    )
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=10),
        message=message,
        data=f"{CB['DOWNLOAD_FORMAT_PREFIX']}a:token123",
        answer=AsyncMock(),
    )
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    with (
        patch(
            "app.handlers.download._claim_download_choice",
            AsyncMock(return_value={"v": 1, "chat_id": -100123, "url": url}),
        ),
        patch("app.handlers.download._download_allowed", AsyncMock(return_value=None)),
        patch(
            "app.handlers.download.MediaService.download_audio",
            AsyncMock(return_value="/tmp/video.opus"),
        ) as download_audio,
        patch("app.handlers.download.track_media_download", AsyncMock()) as track_download,
    ):
        await _youtube_download_choice_handler()(AsyncMock(), query)

    query.answer.assert_awaited_once_with(t("fa", "youtube_sessions.download_started"))
    message.edit_text.assert_awaited_once_with(t("fa", "youtube_sessions.download_started"))
    download_audio.assert_awaited_once_with(url, -100123)
    message.reply_document.assert_awaited_once_with("/tmp/video.opus")
    track_download.assert_awaited_once_with(
        url,
        media_type="audio",
        chat_id=-100123,
        user_id=10,
    )
    message.reply.assert_awaited_once_with(t("fa", "download_cmd.completed"))
