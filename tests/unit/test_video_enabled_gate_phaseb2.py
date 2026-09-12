"""Phase B2: per-chat video_enabled gate and music_video toggle visibility."""
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

HIDDEN_KEYBOARD_SETTING_KEYS = frozenset({
    "repeat",
    "record_call",
})

VISIBLE_KEYBOARD_SETTING_KEYS = frozenset({
    "call_security",
    "security_call",
    "download_users",
    "auto_clean",
    "service_clean",
    "call_message",
    "auto_ready_call",
    "music_video",
    "language",
    "default_media_type",
    "call_report",
    "queue",
    "show_id",
    "show_photo",
    "show_text",
    "call_stats",
    "id_call_stats",
})


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


@pytest.fixture(autouse=True)
def _clear_join_failure_key():
    from app.services.call_service import CallService

    CallService.pop_join_failure_key()
    yield
    CallService.pop_join_failure_key()


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


def _local_track(downloads: Path, chat_id: int = -1001, name: str = "clip.mp4") -> str:
    chat_dir = downloads / str(chat_id)
    chat_dir.mkdir(parents=True, exist_ok=True)
    path = chat_dir / name
    path.write_bytes(b"video-bytes")
    return str(path.resolve())


def _settings_dict(**overrides) -> dict:
    base = {
        "music_video": True,
        "security_call": False,
        "repeat": False,
        "download_users": True,
        "call_message": True,
        "auto_clean": False,
        "queue": False,
        "auto_ready_call": False,
        "call_report": True,
        "record_call": True,
        "show_id": True,
        "show_photo": True,
        "show_text": True,
        "default_media_type": "audio",
        "language": "fa",
    }
    base.update(overrides)
    return base


def _register_play_video_handler():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == "play_video")


def _register_replay_handler():
    from app.handlers.playback import register as register_playback

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    return next(fn for fn in bot.message_handlers if fn.__name__ == "replay_on_reply")


@pytest.mark.asyncio
async def test_video_disabled_blocks_play_video(media_roots):
    from app.utils.i18n import t

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-3001)
    play_video = _register_play_video_handler()

    message = AsyncMock()
    message.chat.id = -3001
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش ویدیو"
    message.reply_to_message = SimpleNamespace(
        video=SimpleNamespace(),
        text=None,
    )
    message.reply = AsyncMock()

    cs = SimpleNamespace(video_enabled=False)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value=local_path),
        ),
        patch(
            "app.repositories.settings_repo.get_chat_settings",
            AsyncMock(return_value=cs),
        ),
    ):
        await play_video(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "playback_cmd.video_disabled_in_group")


@pytest.mark.asyncio
async def test_video_disabled_blocks_replay_on_reply_for_video(media_roots):
    from app.utils.i18n import t

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-3002)
    replay_on_reply = _register_replay_handler()

    message = AsyncMock()
    message.chat.id = -3002
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

    cs = SimpleNamespace(video_enabled=False)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value=local_path),
        ),
        patch(
            "app.repositories.settings_repo.get_chat_settings",
            AsyncMock(return_value=cs),
        ),
    ):
        await replay_on_reply(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "playback_cmd.video_disabled_in_group")


@pytest.mark.asyncio
async def test_video_disabled_does_not_block_audio_playback(media_roots):
    from app.handlers.playback import register as register_playback

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-3003, name="track.ogg")

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    play_audio = next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -3003
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش"
    message.reply_to_message = SimpleNamespace(
        audio=SimpleNamespace(),
        voice=None,
        document=None,
        text=None,
    )
    message.reply = AsyncMock()

    cs = SimpleNamespace(video_enabled=False, buttons_enabled=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value=local_path),
        ),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch(
            "app.repositories.settings_repo.get_chat_settings",
            AsyncMock(return_value=cs),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=local_path)),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.utils.voice_stack.vc_join", AsyncMock()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.seek_tracker.start_seek_tracker", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.media_event_service.track_media_play", AsyncMock()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
    ):
        await play_audio(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert "موزیک" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_video_enabled_still_respects_free_mode_denial():
    from app.utils.i18n import t

    play_video = _register_play_video_handler()

    message = AsyncMock()
    message.chat.id = -3004
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش ویدیو https://example.com/v.mp4"
    message.reply_to_message = None
    message.reply = AsyncMock()

    cs = SimpleNamespace(video_enabled=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.repositories.settings_repo.get_chat_settings",
            AsyncMock(return_value=cs),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=False),
        ),
    ):
        await play_video(AsyncMock(), message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "free_mode.blocked_video")


@pytest.mark.asyncio
async def test_video_enabled_and_allowed_calls_join_voice_chat(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-3005)
    play_video = _register_play_video_handler()

    message = AsyncMock()
    message.chat.id = -3005
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش ویدیو"
    message.reply_to_message = SimpleNamespace(
        video=SimpleNamespace(),
        text=None,
    )
    message.reply = AsyncMock()

    cs = SimpleNamespace(video_enabled=True)
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch(
            "app.handlers.playback.download_trusted_telegram_media",
            AsyncMock(return_value=local_path),
        ),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch(
            "app.repositories.settings_repo.get_chat_settings",
            AsyncMock(return_value=cs),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await play_video(AsyncMock(), message)

    join_mock.assert_awaited_once()
    assert join_mock.await_args.args[3] == "video"


@pytest.mark.asyncio
async def test_join_failure_uses_video_disabled_message_when_group_off(media_roots):
    from app.services.call_service import CallService

    downloads, _ = media_roots
    local_path = _local_track(downloads, chat_id=-3006)

    with (
        patch(
            "app.services.media_capability_service.is_chat_video_enabled",
            AsyncMock(return_value=False),
        ),
    ):
        ok = await CallService.join_voice_chat(
            MagicMock(), -3006, local_path, "video"
        )

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.video_disabled_in_group"


def test_error_message_key_exists_in_strings():
    from app.utils.i18n import t

    assert t("fa", "playback_cmd.video_disabled_in_group") == "◂ پخش ویدیو در این گروه غیرفعال است."
    assert t("en", "playback_cmd.video_disabled_in_group") == "Video playback is disabled in this group."


def test_music_video_toggle_visible_in_group_settings_keyboard():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict())
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    assert CB["GRP_MUSIC_VIDEO"] in toggle_cbs
    assert CB["GRP_MUSIC_VIDEO"] == "grp:set:music_video"


def test_music_video_stale_callback_unchanged():
    from app.utils.ui import CB

    assert CB["GRP_MUSIC_VIDEO"] == "grp:set:music_video"


def test_b1_visible_toggles_remain_visible():
    from app.utils.ui import CB, _GRP_VISIBLE_SETTING_TOGGLES, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict())
    visible_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data not in {CB["NAV_BACK"], CB["WZ_HOME"]}
    }
    assert set(_GRP_VISIBLE_SETTING_TOGGLES.values()) == visible_cbs
    assert VISIBLE_KEYBOARD_SETTING_KEYS == set(_GRP_VISIBLE_SETTING_TOGGLES.keys())


def test_phase_a_hidden_toggles_remain_hidden():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict())
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    hidden_callbacks = {
        CB["GRP_REPEAT"],
        CB["GRP_RECORD_CALL"],
    }
    assert toggle_cbs.isdisjoint(hidden_callbacks)
    assert CB["GRP_SHOW_ID"] in toggle_cbs
