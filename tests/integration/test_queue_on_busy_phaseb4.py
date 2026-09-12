"""Phase B4: queue-on-busy via smart_radio_enabled."""
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
    "security_call",
    "download_users",
    "auto_clean",
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
})


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


def _settings_dict(**overrides) -> dict:
    base = {
        "music_video": True,
        "security_call": False,
        "repeat": False,
        "download_users": False,
        "call_message": False,
        "auto_clean": False,
        "queue": False,
        "auto_ready_call": False,
        "call_report": True,
        "record_call": False,
        "show_id": False,
        "show_photo": False,
        "show_text": False,
        "default_media_type": "audio",
        "language": "fa",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_join_failure_key():
    from app.services.call_service import CallService

    CallService.pop_join_failure_key()
    yield
    CallService.pop_join_failure_key()


@pytest.mark.asyncio
async def test_idle_chat_joins_normally():
    from app.handlers.playback import register as register_playback
    from app.services.call_service import CallService

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    play_audio = next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -7101
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/track.mp3"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/track.mp3"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch("app.handlers.playback.track_event", AsyncMock()),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value="audio")),
        patch("app.handlers.playback.decide_busy_playback", AsyncMock()) as decide_mock,
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision

        decide_mock.return_value = BusyPlaybackDecision(action=BusyPlaybackAction.PLAY_NOW)
        await play_audio(AsyncMock(), message)

    join_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_busy_queue_disabled_returns_already_playing():
    from app.handlers.playback import register as register_playback
    from app.services.call_service import CallService
    from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision
    from app.utils.i18n import t

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    play_audio = next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -7102
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/track.mp3"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/track.mp3"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value="audio")),
        patch(
            "app.handlers.playback.decide_busy_playback",
            AsyncMock(
                return_value=BusyPlaybackDecision(
                    action=BusyPlaybackAction.ALREADY_PLAYING,
                    message_key="playback_cmd.already_playing",
                )
            ),
        ),
        patch("app.handlers.playback.apply_busy_playback_decision", AsyncMock(return_value=True)),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await play_audio(AsyncMock(), message)

    join_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_queue_enabled_enqueues_url():
    from app.handlers.playback import register as register_playback
    from app.services.call_service import CallService
    from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    play_audio = next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -7103
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/track.mp3"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/track.mp3"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value="audio")),
        patch(
            "app.handlers.playback.decide_busy_playback",
            AsyncMock(
                return_value=BusyPlaybackDecision(
                    action=BusyPlaybackAction.QUEUED,
                    message_key="playback_cmd.queued",
                )
            ),
        ),
        patch("app.handlers.playback.apply_busy_playback_decision", AsyncMock(return_value=True)) as apply_mock,
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await play_audio(AsyncMock(), message)

    join_mock.assert_not_awaited()
    apply_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_queues_when_busy_and_enabled():
    from app.handlers.search import register as register_search
    from app.services.call_service import CallService
    from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision

    bot = _RecorderBot()
    register_search(bot, MagicMock())
    handler = next(fn for fn in bot.callback_handlers if fn.__name__ == "on_search_select")

    query = AsyncMock()
    query.data = "search:play:dQw4w9WgXcQ"
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat.id = -7104
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    stream = "https://cdn.example.com/audio.m4a"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.search.validate_safe_url_with_redirects", AsyncMock(return_value="https://youtube.com/watch?v=dQw4w9WgXcQ")),
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch(
            "app.handlers.search.decide_busy_playback",
            AsyncMock(
                return_value=BusyPlaybackDecision(
                    action=BusyPlaybackAction.QUEUED,
                    message_key="playback_cmd.queued",
                )
            ),
        ),
        patch("app.handlers.search.apply_busy_playback_decision", AsyncMock(return_value=True)),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await handler(AsyncMock(), query)

    join_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited()


@pytest.mark.asyncio
async def test_search_already_playing_when_queue_disabled():
    from app.handlers.search import register as register_search
    from app.services.call_service import CallService
    from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision
    from app.utils.i18n import t

    bot = _RecorderBot()
    register_search(bot, MagicMock())
    handler = next(fn for fn in bot.callback_handlers if fn.__name__ == "on_search_select")

    query = AsyncMock()
    query.data = "search:play:dQw4w9WgXcQ"
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat.id = -7105
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    stream = "https://cdn.example.com/audio.m4a"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.search.validate_safe_url_with_redirects", AsyncMock(return_value="https://youtube.com/watch?v=dQw4w9WgXcQ")),
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch(
            "app.handlers.search.decide_busy_playback",
            AsyncMock(
                return_value=BusyPlaybackDecision(
                    action=BusyPlaybackAction.ALREADY_PLAYING,
                    message_key="playback_cmd.already_playing",
                )
            ),
        ),
        patch("app.handlers.search.apply_busy_playback_decision", AsyncMock(return_value=True)),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await handler(AsyncMock(), query)

    join_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_with(
        t("fa", "playback_cmd.already_playing"),
        reply_markup=None,
        disable_web_page_preview=True,
    )


@pytest.mark.asyncio
async def test_radio_url_queues_when_busy_enabled():
    from app.handlers.tv_radio import register as register_tv_radio
    from app.services.call_service import CallService
    from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision

    bot = _RecorderBot()
    register_tv_radio(bot, MagicMock())
    handler = next(fn for fn in bot.callback_handlers if fn.__name__ == "on_radio_select")

    query = AsyncMock()
    query.data = "pb:radio:station1"
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat.id = -7106
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    stations = [{"id": "station1", "name": "Test FM", "url": "https://stream.example.com/radio.mp3"}]
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.tv_radio.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.tv_radio._load_json", return_value=stations),
        patch("app.handlers.tv_radio.validate_safe_url_with_redirects", AsyncMock(return_value=stations[0]["url"])),
        patch(
            "app.handlers.tv_radio.decide_busy_playback",
            AsyncMock(
                return_value=BusyPlaybackDecision(
                    action=BusyPlaybackAction.QUEUED,
                    message_key="playback_cmd.queued",
                )
            ),
        ),
        patch("app.handlers.tv_radio.apply_busy_playback_decision", AsyncMock(return_value=True)),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await handler(AsyncMock(), query)

    join_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_temp_local_not_queued_when_busy(tmp_path, monkeypatch):
    from app.handlers.playback import register as register_playback
    from app.config.settings import settings
    from app.services.call_service import CallService
    from app.services.playback_dispatch_service import (
        BusyPlaybackAction,
        BusyPlaybackDecision,
        decide_busy_playback,
    )
    from app.utils.i18n import t

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "cache"))

    chat_dir = downloads / "-7107"
    chat_dir.mkdir()
    local_path = str((chat_dir / "voice.ogg").resolve())
    (chat_dir / "voice.ogg").write_bytes(b"audio")

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    play_audio = next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -7107
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش"
    message.reply_to_message = SimpleNamespace(
        audio=None,
        voice=SimpleNamespace(),
        document=None,
        text=None,
    )
    message.reply = AsyncMock()

    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.services.playback_dispatch_service.is_chat_playing", return_value=True),
        patch(
            "app.services.playback_dispatch_service.is_chat_queue_on_busy_enabled",
            AsyncMock(return_value=True),
        ),
    ):
        decision = await decide_busy_playback(
            -7107,
            local_path,
            "audio",
            is_temp_local=True,
        )
    assert decision.action == BusyPlaybackAction.QUEUE_UNAVAILABLE_TEMP

    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.download_trusted_telegram_media", AsyncMock(return_value=local_path)),
        patch("app.handlers.playback.is_http_url", return_value=False),
        patch("app.handlers.playback.normalize_media_source", return_value=local_path),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value="audio")),
        patch("app.handlers.playback.decide_busy_playback", AsyncMock(return_value=decision)),
        patch.object(CallService, "join_voice_chat", join_mock),
        patch("app.handlers.playback.cleanup_temp_playback_local_source") as cleanup_mock,
    ):
        await play_audio(AsyncMock(), message)

    join_mock.assert_not_awaited()
    cleanup_mock.assert_called()


def test_queue_toggle_visible_in_keyboard():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict(queue=True))
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    assert CB["GRP_QUEUE"] in toggle_cbs
    assert CB["GRP_QUEUE"] == "grp:set:queue"


@pytest.mark.asyncio
async def test_group_settings_summary_shows_queue_state():
    from app.handlers.group_panel import _build_group_settings_summary
    from app.utils.i18n import t

    with patch(
        "app.handlers.group_panel.call_security_repo.get_call_security_settings",
        AsyncMock(return_value=SimpleNamespace(enabled=False)),
    ):
        text_on = await _build_group_settings_summary(_settings_dict(queue=True), -1001)
        text_off = await _build_group_settings_summary(_settings_dict(queue=False), -1001)
    assert t("fa", "panels.group.settings.queue") in text_on
    assert t("fa", "common.labels.on") in text_on
    assert t("fa", "common.labels.off") in text_off


def test_hidden_toggles_remain_hidden():
    from app.utils.ui import CB, KeyboardFactory, _GRP_VISIBLE_SETTING_TOGGLES

    sd = _settings_dict()
    kb = KeyboardFactory.group_settings("fa", sd)
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    expected_visible_setting_toggles = {
        cb for cb in _GRP_VISIBLE_SETTING_TOGGLES.values() if cb.startswith("grp:set:")
    }
    assert toggle_cbs == expected_visible_setting_toggles
    for hidden_key in HIDDEN_KEYBOARD_SETTING_KEYS:
        hidden_cb = {
            "repeat": CB["GRP_REPEAT"],
            "record_call": CB["GRP_RECORD_CALL"],
            "show_id": CB["GRP_SHOW_ID"],
            "show_photo": CB["GRP_SHOW_PHOTO"],
            "show_text": CB["GRP_SHOW_TEXT"],
        }[hidden_key]
        assert hidden_cb not in toggle_cbs


@pytest.mark.asyncio
async def test_video_gate_blocks_before_queue():
    from app.handlers.playback import register as register_playback
    from app.services.call_service import CallService

    bot = _RecorderBot()
    register_playback(bot, MagicMock())
    play_audio = next(fn for fn in bot.message_handlers if fn.__name__ == "play_audio")

    message = AsyncMock()
    message.chat.id = -7108
    message.from_user = SimpleNamespace(id=42)
    message.text = "پخش https://example.com/video.mp4"
    message.reply_to_message = None
    message.reply = AsyncMock()

    stream = "https://cdn.example.com/video.mp4"
    join_mock = AsyncMock(return_value=True)
    decide_mock = AsyncMock()
    with (
        patch("app.handlers.playback.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.playback.is_http_url", return_value=True),
        patch("app.handlers.playback.validate_safe_url_with_redirects", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch("app.handlers.playback.normalize_media_source", return_value=stream),
        patch("app.handlers.playback.get_chat_default_media_type", AsyncMock(return_value="video")),
        patch("app.handlers.playback.deny_video_playback", AsyncMock(return_value=True)),
        patch("app.handlers.playback.decide_busy_playback", decide_mock),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await play_audio(AsyncMock(), message)

    decide_mock.assert_not_awaited()
    join_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_stays_audio_only():
    from app.handlers.search import register as register_search
    from app.services.call_service import CallService
    from app.services.playback_dispatch_service import BusyPlaybackAction, BusyPlaybackDecision

    bot = _RecorderBot()
    register_search(bot, MagicMock())
    handler = next(fn for fn in bot.callback_handlers if fn.__name__ == "on_search_select")

    query = AsyncMock()
    query.data = "search:play:dQw4w9WgXcQ"
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat.id = -7109
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    stream = "https://cdn.example.com/audio.m4a"
    join_mock = AsyncMock(return_value=True)
    with (
        patch("app.handlers.search.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.handlers.search.validate_safe_url_with_redirects", AsyncMock(return_value="https://youtube.com/watch?v=dQw4w9WgXcQ")),
        patch("app.handlers.search.MediaService.get_stream_url", AsyncMock(return_value=stream)),
        patch(
            "app.handlers.search.decide_busy_playback",
            AsyncMock(return_value=BusyPlaybackDecision(action=BusyPlaybackAction.PLAY_NOW)),
        ),
        patch("app.handlers.search.apply_busy_playback_decision", AsyncMock(return_value=False)),
        patch.object(CallService, "join_voice_chat", join_mock),
    ):
        await handler(AsyncMock(), query)

    assert join_mock.await_args.args[3] == "audio"


@pytest.mark.asyncio
async def test_add_to_playlist_ignores_busy_state():
    from app.handlers.playlist import register as register_playlist

    bot = _RecorderBot()
    register_playlist(bot, MagicMock())
    handler = next(fn for fn in bot.message_handlers if fn.__name__ == "add_to_playlist")

    message = AsyncMock()
    message.chat.id = -7110
    message.from_user = SimpleNamespace(id=42)
    message.text = "افزودن به لیست https://example.com/track.mp3"
    message.reply_to_message = None
    message.reply = AsyncMock()

    add_mock = AsyncMock()
    with (
        patch("app.handlers.playlist._has_permission", AsyncMock(return_value=True)),
        patch("app.handlers.playlist.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.handlers.playlist.release_lock", AsyncMock()),
        patch("app.handlers.playlist.is_http_url", return_value=True),
        patch("app.handlers.playlist.validate_safe_url_with_redirects", AsyncMock(return_value="https://example.com/track.mp3")),
        patch("app.handlers.playlist.normalize_media_source", return_value="https://example.com/track.mp3"),
        patch("app.handlers.playlist.is_chat_playing", return_value=True),
        patch("app.handlers.playlist.playlist_repo.add_to_queue", add_mock),
    ):
        await handler(AsyncMock(), message)

    add_mock.assert_awaited_once()


def test_decide_busy_playback_unit_rules():
    from app.services.playback_dispatch_service import (
        BusyPlaybackAction,
        decide_busy_playback,
        is_queue_safe_source,
    )

    assert is_queue_safe_source("https://example.com/a.mp3", is_temp_local=False) is True
    assert is_queue_safe_source("/tmp/x.ogg", is_temp_local=True) is False
    assert is_queue_safe_source("/downloads/x.ogg", is_temp_local=False) is False


@pytest.mark.asyncio
async def test_dispatch_service_not_busy_returns_play_now():
    from app.services.playback_dispatch_service import BusyPlaybackAction, decide_busy_playback

    with patch("app.services.playback_dispatch_service.is_chat_playing", return_value=False):
        decision = await decide_busy_playback(-1, "https://example.com/a.mp3", "audio")
    assert decision.action == BusyPlaybackAction.PLAY_NOW


@pytest.mark.asyncio
async def test_call_service_is_chat_playing_wrapper():
    from app.services.call_service import CallService

    with patch.dict("app.services.call_service._active_calls", {-999: {"source": "x"}}, clear=False):
        assert CallService.is_chat_playing(-999) is True
        assert CallService.is_chat_playing(-998) is False
