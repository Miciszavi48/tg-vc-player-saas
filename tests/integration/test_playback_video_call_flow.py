"""Video playback call join path and failure messaging."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrogram.enums import ChatType

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.playback_errors import reply_playback_join_failure


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):
        def deco(fn):
            self.message_handlers.append(fn)
            return fn

        return deco

    def on_callback_query(self, *args, **kwargs):
        def deco(fn):
            return fn

        return deco


def _video_message():
    message = MagicMock()
    message.chat = SimpleNamespace(id=-9001, type=ChatType.SUPERGROUP)
    message.from_user = SimpleNamespace(id=42, first_name="Tester")
    message.text = "پخش ویدیو https://youtu.be/abc"
    message.reply_to_message = None
    message.reply = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_video_play_join_uses_video_media_type():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    call_py = MagicMock()
    register_playback(bot, call_py)
    play_video = next(fn for fn in bot.message_handlers if fn.__name__ == "play_video")
    message = _video_message()
    client = MagicMock()
    join_mock = AsyncMock(return_value=True)
    reply_now_playing = AsyncMock()
    controls = object()

    with (
        patch("app.handlers.playback._check_prerequisites", AsyncMock(return_value=True)),
        patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=False)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value="https://safe/url")),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value="https://stream/url")),
        patch("app.handlers.playback.normalize_media_source", return_value="https://stream/url"),
        patch("app.handlers.playback.decide_busy_playback", AsyncMock(return_value=MagicMock(action="play"))),
        patch("app.handlers.playback.apply_busy_playback_decision", AsyncMock(return_value=False)),
        patch("app.handlers.playback.CallService.join_voice_chat", join_mock),
        patch("app.handlers.playback.resolve_lang", AsyncMock(return_value="fa")),
        patch("app.handlers.playback.render_now_playing_text", AsyncMock(return_value="now playing")),
        patch("app.handlers.playback.build_now_playing_controls", AsyncMock(return_value=controls)),
        patch("app.handlers.playback.reply_now_playing_with_optional_cover", reply_now_playing),
        patch("app.handlers.playback.track_event", AsyncMock()),
    ):
        await play_video(client, message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "video"
    reply_now_playing.assert_awaited_once()
    assert reply_now_playing.await_args.kwargs["reply_markup"] is controls


@pytest.mark.asyncio
async def test_join_failure_replies_visible_message():
    message = MagicMock()
    message.reply = AsyncMock()
    with patch(
        "app.services.call_service.CallService.pop_join_failure_key",
        return_value="playback_cmd.failed",
    ):
        await reply_playback_join_failure(message, lang="fa", track=False)
    message.reply.assert_awaited_once()
