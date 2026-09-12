"""Phase B3: default_media_type playback resolution (conservative)."""
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


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator


def _register_playback():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return bot.message_handlers


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


@pytest.fixture(autouse=True)
def _clear_join_failure_key():
    from app.services.call_service import CallService

    CallService.pop_join_failure_key()
    yield
    CallService.pop_join_failure_key()


@pytest.mark.asyncio
async def test_play_url_with_default_audio_joins_audio():
    from app.services.call_service import CallService

    handlers = _register_playback()
    play_audio = next(fn for fn in handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -6001
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/track.mp3"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/track.mp3"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value="https://example.com/track.mp3")),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch(
            "app.handlers.playback.get_chat_default_media_type",
            AsyncMock(return_value="audio"),
        ),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await play_audio(AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_play_url_with_default_video_joins_video():
    from app.services.call_service import CallService

    handlers = _register_playback()
    play_audio = next(fn for fn in handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -6002
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/track.mp4"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/track.mp4"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value="https://example.com/track.mp4")),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch(
            "app.handlers.playback.get_chat_default_media_type",
            AsyncMock(return_value="video"),
        ),
        patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=False)),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await play_audio(AsyncMock(), message)

    assert join_mock.await_args.args[3] == "video"


@pytest.mark.asyncio
async def test_reply_audio_ignores_default_video(media_roots):
    from app.handlers.playback import register as register_playback
    from app.services.call_service import CallService

    downloads, _ = media_roots
    chat_dir = downloads / "-6003"
    chat_dir.mkdir(parents=True)
    local_path = str((chat_dir / "track.ogg").resolve())
    (chat_dir / "track.ogg").write_bytes(b"audio")

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    play_audio = next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -6003
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش"
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        document=None,
        text=None,
    )
    message.reply = AsyncMock()

    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.download_trusted_telegram_media", AsyncMock(return_value=local_path)),
        patch("app.handlers.playback.normalize_media_source", return_value=local_path),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch(
            "app.handlers.playback.get_chat_default_media_type",
            AsyncMock(return_value="video"),
        ),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await play_audio(AsyncMock(), message)

    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_replay_video_ignores_default_audio(media_roots):
    from app.handlers.playback import register as register_playback
    from app.services.call_service import CallService

    downloads, _ = media_roots
    chat_dir = downloads / "-6004"
    chat_dir.mkdir(parents=True)
    local_path = str((chat_dir / "clip.mp4").resolve())
    (chat_dir / "clip.mp4").write_bytes(b"video")

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    replay = next(fn for fn in bot.message_handlers if fn.__name__ == "replay_on_reply")

    message = AsyncMock()
    message.chat.id = -6004
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش ریپلای"
    message.reply_to_message = SimpleNamespace(
        audio=None,
        video=SimpleNamespace(),
        voice=None,
        document=None,
        text=None,
    )
    message.reply = AsyncMock()

    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.download_trusted_telegram_media", AsyncMock(return_value=local_path)),
        patch("app.handlers.playback.normalize_media_source", return_value=local_path),
        patch(
            "app.handlers.playback.get_chat_default_media_type",
            AsyncMock(return_value="audio"),
        ),
        patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=False)),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await replay(AsyncMock(), message)

    assert join_mock.await_args.args[3] == "video"


@pytest.mark.asyncio
async def test_default_video_blocked_when_video_disabled_in_group():
    from app.utils.i18n import t

    handlers = _register_playback()
    play_audio = next(fn for fn in handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -6005
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/v.mp4"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/v.mp4"
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value="https://example.com/v.mp4")),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch(
            "app.handlers.playback.get_chat_default_media_type",
            AsyncMock(return_value="video"),
        ),
        patch(
            "app.repositories.settings_repo.get_chat_settings",
            AsyncMock(return_value=SimpleNamespace(video_enabled=False)),
        ),
    ):
        await play_audio(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "playback_cmd.video_disabled_in_group")


@pytest.mark.asyncio
async def test_default_video_blocked_by_free_mode():
    from app.utils.i18n import t

    handlers = _register_playback()
    play_audio = next(fn for fn in handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -6006
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/v.mp4"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/v.mp4"
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value="https://example.com/v.mp4")),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch(
            "app.handlers.playback.get_chat_default_media_type",
            AsyncMock(return_value="video"),
        ),
        patch(
            "app.repositories.settings_repo.get_chat_settings",
            AsyncMock(return_value=SimpleNamespace(video_enabled=True)),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=False),
        ),
    ):
        await play_audio(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "free_mode.blocked_video")


def test_search_playback_stays_audio_only():
    source = Path("app/handlers/search.py").read_text(encoding="utf-8")
    assert '"audio"' in source
    assert "get_chat_default_media_type" not in source


def test_tv_radio_unchanged_by_default_media_type():
    tv_source = Path("app/handlers/tv_radio.py").read_text(encoding="utf-8")
    assert "get_chat_default_media_type" not in tv_source
