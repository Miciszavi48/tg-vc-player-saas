"""Phase B6-1: lock session-only repeat runtime; group repeat toggle stays hidden."""

from __future__ import annotations

import inspect
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

@pytest.fixture(autouse=True)
def _clear_repeat_states():
    from app.services import call_service

    call_service._repeat_states.clear()
    yield
    call_service._repeat_states.clear()


def test_get_repeat_state_defaults_false():
    from app.services.call_service import CallService

    assert CallService.get_repeat_state(-710001) is False


def test_set_repeat_state_toggles_on_and_off():
    from app.services.call_service import CallService

    chat_id = -710002
    CallService.set_repeat_state(chat_id, True)
    assert CallService.get_repeat_state(chat_id) is True
    CallService.set_repeat_state(chat_id, False)
    assert CallService.get_repeat_state(chat_id) is False


def test_repeat_state_isolated_per_chat():
    from app.services.call_service import CallService

    CallService.set_repeat_state(-710003, True)
    CallService.set_repeat_state(-710004, False)
    assert CallService.get_repeat_state(-710003) is True
    assert CallService.get_repeat_state(-710004) is False


@pytest.mark.asyncio
async def test_pb_repeat_toggles_in_memory_state():
    from app.handlers import callbacks
    from app.services.call_service import CallService
    from app.utils.ui import CB

    handlers: list = []

    def capture_callback(*args, **kwargs):
        def decorator(fn):
            handlers.append(fn)
            return fn

        return decorator

    bot = MagicMock()
    bot._core_callbacks_registered = False
    bot.on_callback_query = capture_callback
    bot.on_message = lambda *args, **kwargs: (lambda fn: fn)
    callbacks.register(bot, MagicMock())
    repeat_handler = next(fn for fn in handlers if fn.__name__ == "pb_playback_controls")

    chat_id = -710005
    query = AsyncMock()
    query.data = CB["PB_REPEAT_TOGGLE"]
    query.message.chat.id = chat_id
    query.answer = AsyncMock()

    with patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)):
        await repeat_handler(AsyncMock(), query)
    assert CallService.get_repeat_state(chat_id) is True

    with patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)):
        await repeat_handler(AsyncMock(), query)
    assert CallService.get_repeat_state(chat_id) is False


@pytest.mark.asyncio
async def test_now_playing_keyboard_uses_get_repeat_state():
    from app.services.media_capability_service import build_now_playing_controls
    from app.services.call_service import CallService

    chat_id = -710006
    CallService.set_repeat_state(chat_id, True)
    with patch(
        "app.services.media_capability_service.is_chat_buttons_enabled",
        AsyncMock(return_value=True),
    ):
        kb = await build_now_playing_controls("fa", chat_id)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("فعال" in text for text in labels)


def test_stream_end_handler_reads_get_repeat_state():
    from app.services import helper_pytgcalls_pool as pool_mod

    source = inspect.getsource(pool_mod.register_stream_end_handler)
    assert "CallService.get_repeat_state" in source
    assert "repeat_mode" not in source


@pytest.mark.asyncio
async def test_stream_end_replays_when_repeat_on():
    from tests.test_playback_repeat_state_phaseb1 import _register_stream_end_handler
    from app.services.call_service import CallService

    chat_id = -710007
    handler = _register_stream_end_handler()
    CallService.set_repeat_state(chat_id, True)
    join_mock = AsyncMock()
    play_next_mock = AsyncMock()
    update = SimpleNamespace(chat_id=chat_id)

    with (
        patch.object(CallService, "get_active_calls", return_value={chat_id: {"source": "https://example.com/a.mp3", "media_type": "audio"}}),
        patch.object(CallService, "join_voice_chat", join_mock),
        patch.object(CallService, "play_next", play_next_mock),
    ):
        await handler(None, update)

    join_mock.assert_awaited_once()
    play_next_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_end_play_next_when_repeat_off():
    from tests.test_playback_repeat_state_phaseb1 import _register_stream_end_handler
    from app.services.call_service import CallService

    chat_id = -710008
    handler = _register_stream_end_handler()
    CallService.set_repeat_state(chat_id, False)
    play_next_mock = AsyncMock()

    with (
        patch.object(CallService, "get_active_calls", return_value={chat_id: {"source": "x", "media_type": "audio"}}),
        patch.object(CallService, "join_voice_chat", AsyncMock()),
        patch.object(CallService, "play_next", play_next_mock),
    ):
        await handler(None, update := SimpleNamespace(chat_id=chat_id))

    play_next_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_leave_voice_chat_does_not_clear_repeat_state():
    from app.services.call_service import CallService

    chat_id = -710009
    CallService.set_repeat_state(chat_id, True)
    call_py = SimpleNamespace()

    with (
        patch("app.utils.voice_stack.vc_leave", AsyncMock()),
        patch("app.services.seek_tracker.stop_seek_tracker"),
        patch("app.services.helper_pool_service.HelperPoolService.decrement_active_calls", AsyncMock()),
        patch("app.services.call_service._remove_playback_state", AsyncMock()),
    ):
        await CallService.leave_voice_chat(call_py, chat_id)

    assert CallService.get_repeat_state(chat_id) is True


def test_no_repeat_enabled_column_on_model():
    from app.database.models import ChatSettings

    cols = {c.name for c in ChatSettings.__table__.columns}
    assert "repeat_enabled" not in cols
    assert "vote_skip_enabled" in cols


def test_no_migration_0022_repeat_enabled():
    migrations = Path("app/database/migrations/versions")
    assert not any("0022" in p.name and "repeat" in p.name for p in migrations.glob("*.py"))


def test_repeat_not_in_visible_group_settings():
    from app.utils.ui import _GRP_VISIBLE_SETTING_TOGGLES

    assert "repeat" not in _GRP_VISIBLE_SETTING_TOGGLES


@pytest.mark.asyncio
async def test_group_settings_summary_omits_repeat_toggle():
    from unittest.mock import AsyncMock, patch

    from app.handlers.group_panel import _build_group_settings_summary

    sd = {
        "security_call": False,
        "download_users": False,
        "auto_clean": False,
        "call_message": False,
        "auto_ready_call": False,
        "music_video": False,
        "language": "fa",
        "default_media_type": "audio",
        "call_report": False,
        "queue": False,
        "show_id": False,
        "show_photo": False,
        "show_text": False,
        "repeat": True,
    }
    with patch(
        "app.handlers.group_panel.call_security_repo.get_call_security_settings",
        new=AsyncMock(return_value=None),
    ):
        summary = await _build_group_settings_summary(sd, chat_id=-100123)
    assert "panels.group.settings.repeat" not in summary
    assert "تکرار فعال/غیرفعال" not in summary
    assert "تکرار" not in summary


@pytest.mark.asyncio
async def test_stale_grp_repeat_does_not_change_repeat_state():
    from app.handlers import group_panel
    from app.services.call_service import CallService
    from app.utils.ui import CB

    handlers: list = []

    def capture(*args, **kwargs):
        def decorator(fn):
            handlers.append(fn)
            return fn

        return decorator

    bot2 = MagicMock()
    bot2.on_callback_query = capture
    group_panel.register(bot2, None)
    toggle_handler = next(fn for fn in handlers if fn.__name__ == "grp_toggle_setting")

    chat_id = -710010
    CallService.set_repeat_state(chat_id, False)
    query = AsyncMock()
    query.data = CB["GRP_REPEAT"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=chat_id)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(vote_skip_enabled=False)
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch("app.handlers.group_panel._get_settings_dict", AsyncMock(return_value={**{
            "music_video": False, "security_call": False, "repeat": True,
            "download_users": False, "call_message": False, "auto_clean": False,
            "queue": False, "auto_ready_call": False, "call_report": False,
            "record_call": False, "show_id": False, "show_photo": False,
            "show_text": False, "default_media_type": "audio", "language": "fa",
        }})),
    ):
        await toggle_handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(chat_id, "vote_skip_enabled", True)
    assert CallService.get_repeat_state(chat_id) is False


def test_grp_repeat_callback_data_unchanged():
    from app.utils.ui import CB

    assert CB["GRP_REPEAT"] == "grp:set:repeat"


def test_vote_skip_enabled_not_in_playback_runtime():
    for rel in (
        "app/handlers/callbacks.py",
        "app/services/call_service.py",
        "app/handlers/playback.py",
        "app/main.py",
    ):
        assert "vote_skip_enabled" not in Path(rel).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_queue_on_busy_unaffected_by_repeat_state():
    from app.services.playback_dispatch_service import BusyPlaybackAction, decide_busy_playback

    with patch(
        "app.services.playback_dispatch_service.is_chat_playing",
        return_value=True,
    ), patch(
        "app.services.playback_dispatch_service.is_chat_queue_on_busy_enabled",
        AsyncMock(return_value=True),
    ):
        decision = await decide_busy_playback(
            -710011,
            "https://example.com/track.mp3",
            "audio",
        )
    assert decision.action == BusyPlaybackAction.QUEUED


def test_help_text_no_persistent_group_repeat_claim():
    from app.utils.i18n import t

    fa_help = t("fa", "help.group_panel")
    en_help = t("en", "help.group_panel")
    assert "تکرار" in fa_help
    assert "نشست" in fa_help or "در حال پخش" in fa_help
    assert "تنظیمات شامل: موزیک/ویدیو، دانلود، کال، تکرار" not in fa_help
    assert "Toggles: music/video, download, call, repeat" not in en_help
    assert "current bot session" in en_help or "now-playing" in en_help


def test_play_commands_references_now_playing_buttons_not_slash_repeat():
    from app.utils.i18n import t

    fa_cmds = t("fa", "help_content.play_commands")
    en_cmds = t("en", "help_content.play_commands")
    assert "/repeat" not in fa_cmds.lower()
    assert "/repeat" not in en_cmds.lower()
    assert "در حال پخش" in fa_cmds or "now-playing" in en_cmds
