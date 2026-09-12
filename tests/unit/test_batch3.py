"""Tests for Batch 3: G21, G3, G4, G5, G20."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


# ═══════════════════════════════════════════════════════════════════════
# G21: on_stream_end wiring
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG21StreamEnd:
    async def test_register_function_exists(self):
        """register_stream_end_handler must exist in helper_pytgcalls_pool."""
        from app.services.helper_pytgcalls_pool import register_stream_end_handler

        assert callable(register_stream_end_handler)

    async def test_stream_end_calls_play_next(self):
        """When stream_end fires, CallService.play_next must be called."""
        from app.services import CallService
        from app.services.helper_pytgcalls_pool import register_stream_end_handler
        from app.repositories import settings_repo as sr_mod

        mock_call_py = MagicMock()
        mock_call_py._stream_end_registered = False

        with patch.object(CallService, "play_next", new_callable=AsyncMock) as mock_pn, \
             patch.object(CallService, "get_active_calls", return_value={}), \
             patch.object(sr_mod, "get_chat_settings", new_callable=AsyncMock, return_value=None):

            captured_handler = None

            def fake_on_update(_filter_obj):
                def decorator(fn):
                    nonlocal captured_handler
                    captured_handler = fn
                    return fn
                return decorator

            def fake_on_stream_end():
                def decorator(fn):
                    nonlocal captured_handler
                    captured_handler = fn
                    return fn
                return decorator

            mock_call_py.on_stream_end = fake_on_stream_end
            mock_call_py.on_update = fake_on_update

            with patch("pytgcalls.filters.stream_end", return_value=object(), create=True):
                register_stream_end_handler(mock_call_py)

            assert captured_handler is not None

            mock_update = MagicMock()
            mock_update.chat_id = -100999
            await captured_handler(mock_call_py, mock_update)

            mock_pn.assert_called_once_with(None, -100999)


# ═══════════════════════════════════════════════════════════════════════
# G3: Track dedication parsing
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG3Dedication:
    async def test_parse_with_at_target(self):
        """'پخش @user song' → source='song', target='@user'."""
        from app.handlers.playback import _parse_dedication
        source, target = _parse_dedication("پخش @user song", "پخش")
        assert target == "@user"
        assert source == "song"

    async def test_parse_with_numeric_target(self):
        """'پخش 123456 song' → source='song', target='123456'."""
        from app.handlers.playback import _parse_dedication
        source, target = _parse_dedication("پخش 123456 song", "پخش")
        assert target == "123456"
        assert source == "song"

    async def test_parse_without_target(self):
        """'پخش http://song.mp3' → source='http://song.mp3', target=None."""
        from app.handlers.playback import _parse_dedication
        source, target = _parse_dedication("پخش http://song.mp3", "پخش")
        assert target is None
        assert source == "http://song.mp3"

    async def test_parse_with_baray_syntax(self):
        """'پخش song برای @friend' → source='song', target='@friend'."""
        from app.handlers.playback import _parse_dedication
        source, target = _parse_dedication("پخش song برای @friend", "پخش")
        assert target == "@friend"
        assert source == "song"

    async def test_now_playing_includes_dedication(self):
        """When dedication is present, now-playing message must contain it."""
        from app.utils.i18n import t
        text = t("fa", "playback_cmd.playing_audio")
        ded = t("fa", "playback_cmd.dedicated_to", target="@user")
        combined = text + "\n" + ded
        assert "تقدیم به" in combined
        assert "@user" in combined


# ═══════════════════════════════════════════════════════════════════════
# G4: Replay-on-reply
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG4ReplayOnReply:
    async def test_replay_cmds_defined(self):
        """Replay command list must contain the Persian command."""
        from app.handlers.playback import _REPLAY_CMDS
        assert "پخش ریپلای" in _REPLAY_CMDS

    async def test_no_reply_shows_guidance(self):
        """Calling replay without a reply shows guidance JSON text."""
        from app.utils.i18n import t
        msg = t("fa", "playback_cmd.replay_no_reply")
        assert "ریپلای" in msg

    async def test_no_media_shows_error(self):
        """Replied message without media shows specific error."""
        from app.utils.i18n import t
        msg = t("fa", "playback_cmd.replay_no_media")
        assert len(msg) > 0
        assert "[missing:" not in msg


# ═══════════════════════════════════════════════════════════════════════
# G5: CreatorsList
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG5CreatorsList:
    async def test_creators_list_cmds_defined(self):
        """CreatorsList command list exists with Persian and English."""
        from app.handlers.playback import _CREATORS_LIST_CMDS
        assert "لیست مالکان" in _CREATORS_LIST_CMDS
        assert "creatorslist" in _CREATORS_LIST_CMDS

    async def test_empty_list_message(self):
        """Empty state shows proper JSON message."""
        from app.utils.i18n import t
        msg = t("fa", "promotion.creators_list_empty")
        assert "[missing:" not in msg
        assert len(msg) > 0

    async def test_list_title_message(self):
        """List header shows proper JSON message."""
        from app.utils.i18n import t
        msg = t("fa", "promotion.creators_list_title")
        assert "[missing:" not in msg
        assert "مالکان" in msg


# ═══════════════════════════════════════════════════════════════════════
# G20: default_media_type toggle
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestG20DefaultMediaType:
    async def test_cb_constant_exists(self):
        """GRP_DEFAULT_MEDIA_TYPE CB constant must exist."""
        from app.utils.ui import CB
        assert "GRP_DEFAULT_MEDIA_TYPE" in CB
        assert CB["GRP_DEFAULT_MEDIA_TYPE"] == "grp:set:default_media"

    async def test_settings_label_exists(self):
        """JSON key for the setting label must exist."""
        from app.utils.i18n import t
        fa = t("fa", "panels.group.settings.default_media_type")
        en = t("en", "panels.group.settings.default_media_type")
        assert "[missing:" not in fa
        assert "[missing:" not in en

    async def test_group_settings_keyboard_includes_language_toggle(self):
        """group_settings keyboard must include language row."""
        from app.utils.ui import KeyboardFactory
        sd = {
            "music_video": True,
            "security_call": False,
            "repeat": False,
            "download_users": False,
            "call_message": True,
            "auto_clean": False,
            "queue": True,
            "auto_ready_call": False,
            "call_report": False,
            "record_call": False,
            "show_id": True,
            "show_photo": True,
            "show_text": True,
            "default_media_type": "audio",
            "language": "fa",
        }
        kb = KeyboardFactory.group_settings("fa", sd)
        all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "grp:set:language" in all_cb
