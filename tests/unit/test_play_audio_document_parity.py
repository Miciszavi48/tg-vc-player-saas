"""پخش command supports audio document replies like پخش ریپلای."""
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


@pytest.mark.asyncio
async def test_play_audio_downloads_audio_document_reply(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.handlers.playback import register as register_playback

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))

    chat_id = -3001
    local_path = downloads / str(chat_id) / "doc.mp3"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"mp3")

    message = AsyncMock()
    message.chat.id = chat_id
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش"
    message.reply_to_message = SimpleNamespace(
        audio=None,
        voice=None,
        document=SimpleNamespace(mime_type="audio/mpeg"),
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

    download_mock = AsyncMock(return_value=str(local_path.resolve()))

    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.settings_repo.get_chat_settings",
            AsyncMock(
                return_value=SimpleNamespace(
                    audio_enabled=True,
                    file_enabled=True,
                    video_enabled=True,
                )
            ),
        ),
        patch("app.handlers.playback.download_trusted_telegram_media", download_mock),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch("app.handlers.playback.resolve_lang", AsyncMock(return_value="fa")),
        patch("app.handlers.playback.render_now_playing_text", AsyncMock(return_value="np")),
        patch("app.handlers.playback.build_now_playing_controls", AsyncMock(return_value=None)),
        patch(
            "app.handlers.playback.reply_now_playing_with_optional_cover",
            AsyncMock(),
        ) as cover_mock,
        patch(
            "app.services.CallService.join_voice_chat",
            AsyncMock(return_value=True),
        ) as join_mock,
    ):
        await play_handler(client, message)

    download_mock.assert_awaited_once()
    assert download_mock.await_args.args[1] is message.reply_to_message.document
    join_mock.assert_awaited_once()
    cover_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_audio_ignores_non_audio_document_reply():
    from app.handlers.playback import register as register_playback

    message = AsyncMock()
    message.chat.id = -3002
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش"
    message.reply_to_message = SimpleNamespace(
        audio=None,
        voice=None,
        document=SimpleNamespace(mime_type="application/pdf"),
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
        patch("app.handlers.playback.download_trusted_telegram_media", AsyncMock()) as download_mock,
    ):
        await play_handler(client, message)

    from app.utils.i18n import t

    download_mock.assert_not_awaited()
    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.replay_no_media"))
