"""Tests for playback commands, dedication parsing, replay, and callbacks."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


@pytest.mark.asyncio
class TestPlaybackCommandParsing:
    async def test_play_audio_cmds_defined(self):
        from app.handlers.playback import _PLAY_AUDIO_CMDS
        assert "پخش" in _PLAY_AUDIO_CMDS
        assert "play" in _PLAY_AUDIO_CMDS

    async def test_play_video_cmds_defined(self):
        from app.handlers.playback import _PLAY_VIDEO_CMDS
        assert "پخش ویدیو" in _PLAY_VIDEO_CMDS
        assert "playvideo" in _PLAY_VIDEO_CMDS

    async def test_stop_cmds_defined(self):
        from app.handlers.playback import _STOP_AUDIO_CMDS, _STOP_VIDEO_CMDS
        assert "توقف پخش" in _STOP_AUDIO_CMDS
        assert "توقف ویدیو" in _STOP_VIDEO_CMDS

    async def test_pause_resume_cmds(self):
        from app.handlers.playback import _PAUSE_CMDS, _RESUME_CMDS
        assert "مکث" in _PAUSE_CMDS
        assert "ازسرگیری" in _RESUME_CMDS

    async def test_mute_unmute_cmds(self):
        from app.handlers.playback import _MUTE_CMDS, _UNMUTE_CMDS
        assert "بیصدا" in _MUTE_CMDS
        assert "باصدا" in _UNMUTE_CMDS

    async def test_volume_cmds(self):
        from app.handlers.playback import (
            _MUSIC_VOL_CMDS,
            _SEEK_BACK_CMDS,
            _SEEK_FRONT_CMDS,
            _VIDEO_VOL_CMDS,
            _VOLUME_DOWN_CMDS,
            _VOLUME_UP_CMDS,
        )
        assert "صدای موزیک" in _MUSIC_VOL_CMDS
        assert "صدای ویدیو" in _VIDEO_VOL_CMDS
        assert "Volume-" in _VOLUME_DOWN_CMDS
        assert "Volume+" in _VOLUME_UP_CMDS
        assert "Front" in _SEEK_FRONT_CMDS
        assert "Back" in _SEEK_BACK_CMDS

    async def test_tv_cmds(self):
        from app.handlers.playback import _PLAY_TV_CMDS, _STOP_TV_CMDS
        assert "پخش تیوی" in _PLAY_TV_CMDS
        assert "توقف تیوی" in _STOP_TV_CMDS

    async def test_ping_bot_cmds(self):
        from app.handlers.playback import _BOT_CMDS, _BOT_EXACT_FA_REPLIES, _PING_CMDS
        assert "پینگ" in _PING_CMDS
        assert "ربات" in _BOT_CMDS
        assert _BOT_EXACT_FA_REPLIES == ("جونم؟", "بفرمایید", "من فعالم", "در خدمتم", "آنلاینم")

    async def test_replay_cmds(self):
        from app.handlers.playback import _REPLAY_CMDS
        assert "پخش ریپلای" in _REPLAY_CMDS

    async def test_creators_list_cmds(self):
        from app.handlers.playback import _CREATORS_LIST_CMDS
        assert "لیست مالکان" in _CREATORS_LIST_CMDS
        assert "creatorslist" in _CREATORS_LIST_CMDS


@pytest.mark.asyncio
class TestDedicationParsing:
    async def test_at_target(self):
        from app.handlers.playback import _parse_dedication
        source, target = _parse_dedication("پخش @user song", "پخش")
        assert target == "@user"
        assert source == "song"

    async def test_no_target(self):
        from app.handlers.playback import _parse_dedication
        source, target = _parse_dedication("پخش http://x.mp3", "پخش")
        assert target is None

    async def test_baray_syntax(self):
        from app.handlers.playback import _parse_dedication
        source, target = _parse_dedication("پخش song برای @friend", "پخش")
        assert target == "@friend"
        assert source == "song"


@pytest.mark.asyncio
class TestPlaybackI18nKeys:
    async def test_all_playback_message_keys(self):
        from app.utils.i18n import t
        keys = [
            "playback_cmd.playing_audio", "playback_cmd.playing_video",
            "playback_cmd.stopped_audio", "playback_cmd.stopped_video",
            "playback_cmd.paused", "playback_cmd.resumed",
            "playback_cmd.muted", "playback_cmd.unmuted",
            "playback_cmd.volume_set", "playback_cmd.playing_tv",
            "playback_cmd.stopped_tv", "playback_cmd.ping_response",
            "playback_cmd.bot_info_response", "playback_cmd.not_admin",
            "playback_cmd.no_credit", "playback_cmd.no_permission",
            "playback_cmd.provide_source", "playback_cmd.failed",
            "playback_cmd.dedicated_to", "playback_cmd.replay_no_reply",
            "playback_cmd.replay_no_media",
        ]
        for k in keys:
            val = t("fa", k, target="x", ms=0, volume=0)
            assert "[missing:" not in val, f"Missing key: {k}"


@pytest.mark.asyncio
async def test_exact_fa_bot_message_uses_random_approved_reply():
    from app.handlers import playback

    bot = _RecorderBot()
    playback.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "bot_info")
    message = SimpleNamespace(text="ربات", reply=AsyncMock())

    with patch("app.handlers.playback.random.choice", return_value="جونم؟") as choice:
        await handler(SimpleNamespace(), message)

    choice.assert_called_once_with(playback._BOT_EXACT_FA_REPLIES)
    message.reply.assert_awaited_once_with("جونم؟")


@pytest.mark.asyncio
async def test_exact_latin_bot_message_keeps_existing_fixed_reply():
    from app.handlers import playback
    from app.utils.i18n import t

    bot = _RecorderBot()
    playback.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "bot_info")
    message = SimpleNamespace(text="bot", reply=AsyncMock())

    await handler(SimpleNamespace(), message)

    message.reply.assert_awaited_once_with(t("fa", "playback_cmd.bot_info_response"))


@pytest.mark.asyncio
async def test_ping_reports_active_state_and_latency_without_sensitive_details():
    from app.handlers import playback

    bot = _RecorderBot()
    playback.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "ping")
    sent = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(reply=AsyncMock(return_value=sent))

    with patch("app.handlers.playback.time.perf_counter", side_effect=[10.0, 10.05]):
        await handler(SimpleNamespace(), message)

    message.reply.assert_awaited_once_with("⏳")
    sent.edit_text.assert_awaited_once()
    body = sent.edit_text.await_args.args[0]
    assert "پینگ ربات" in body
    assert "وضعیت: فعال" in body
    assert "زمان پاسخ‌دهی: 50 ms" in body
    lowered = body.lower()
    assert "token" not in lowered
    assert "redis" not in lowered
    assert "database" not in lowered
    assert "postgres" not in lowered


@pytest.mark.asyncio
class TestCallbackConstants:
    async def test_playback_callbacks_exist(self):
        from app.utils.ui import CB
        playback_cbs = [
            "PB_AUDIO", "PB_VIDEO", "PB_TV", "PB_SAT", "PB_RADIO", "PB_DOWNLOAD",
            "PB_VOL_UP", "PB_VOL_DOWN", "PB_SPEED_UP", "PB_SPEED_DOWN",
            "PB_NEXT", "PB_PREV", "PB_REPEAT_TOGGLE",
            "PB_FAV_ADD", "PB_FAV_PLAY", "PB_STOP", "PB_PAUSE", "PB_RESUME",
        ]
        for key in playback_cbs:
            assert key in CB, f"Missing CB: {key}"

    async def test_now_playing_keyboard_renders(self):
        from app.utils.ui import KeyboardFactory
        kb = KeyboardFactory.now_playing_controls("fa", repeat_on=True)
        assert len(kb.inline_keyboard) > 0

        kb2 = KeyboardFactory.now_playing_controls("fa", repeat_on=False)
        assert len(kb2.inline_keyboard) > 0
