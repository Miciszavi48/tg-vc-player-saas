"""Phase B1: in-memory repeat state on now-playing keyboard and stream-end."""
from __future__ import annotations

import inspect
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


def test_playback_uses_get_repeat_state_for_now_playing_keyboard():
    from app.handlers import playback

    source = inspect.getsource(playback.register)
    assert "build_now_playing_controls" in source
    assert "repeat_on=False" not in source

    helper_source = inspect.getsource(
        __import__(
            "app.services.media_capability_service",
            fromlist=["build_now_playing_controls"],
        ).build_now_playing_controls,
    )
    assert "CallService.get_repeat_state" in helper_source


def test_stream_end_uses_in_memory_repeat_not_repeat_mode_column():
    from app.services import helper_pytgcalls_pool as pool_mod

    source = inspect.getsource(pool_mod.register_stream_end_handler)
    assert "repeat_mode" not in source
    assert "CallService.get_repeat_state" in source


@pytest.mark.asyncio
async def test_now_playing_keyboard_reflects_repeat_state():
    from app.services.call_service import CallService
    from app.utils.ui import CB, KeyboardFactory

    chat_id = -900001
    CallService.set_repeat_state(chat_id, True)
    try:
        kb_on = KeyboardFactory.now_playing_controls("fa", repeat_on=CallService.get_repeat_state(chat_id))
        labels_on = [btn.text for row in kb_on.inline_keyboard for btn in row]
        assert any("فعال" in text for text in labels_on)
    finally:
        CallService.set_repeat_state(chat_id, False)

    kb_off = KeyboardFactory.now_playing_controls("fa", repeat_on=CallService.get_repeat_state(chat_id))
    labels_off = [btn.text for row in kb_off.inline_keyboard for btn in row]
    assert any(btn.callback_data == CB["PB_REPEAT_TOGGLE"] for row in kb_off.inline_keyboard for btn in row)
    assert any("خاموش" in text for text in labels_off)


def _register_stream_end_handler():
    """Return the registered stream-end coroutine from helper pool registration."""
    from app.services.helper_pytgcalls_pool import register_stream_end_handler

    handlers: list = []
    mock_call_py = MagicMock()
    mock_call_py._stream_end_registered = False

    def fake_on_update(_filter_obj):
        def _wrap(fn):
            handlers.append(fn)
            return fn

        return _wrap

    mock_call_py.on_update = fake_on_update

    with patch("pytgcalls.filters.stream_end", return_value=object(), create=True):
        register_stream_end_handler(mock_call_py)

    assert len(handlers) == 1
    return handlers[0]


@pytest.mark.asyncio
async def test_stream_end_repeat_replays_when_in_memory_state_enabled():
    from app.services.call_service import CallService

    chat_id = -900002
    handler = _register_stream_end_handler()
    CallService.set_repeat_state(chat_id, True)
    try:
        join_mock = AsyncMock()
        play_next_mock = AsyncMock()
        update = SimpleNamespace(chat_id=chat_id)
        active = {"source": "/tmp/track.mp3", "media_type": "audio"}

        with (
            patch.object(CallService, "get_active_calls", return_value={chat_id: active}),
            patch.object(CallService, "join_voice_chat", join_mock),
            patch.object(CallService, "play_next", play_next_mock),
        ):
            await handler(None, update)

        join_mock.assert_awaited_once()
        play_next_mock.assert_not_awaited()
    finally:
        CallService.set_repeat_state(chat_id, False)


@pytest.mark.asyncio
async def test_stream_end_advances_queue_when_repeat_disabled():
    from app.services.call_service import CallService

    chat_id = -900003
    handler = _register_stream_end_handler()
    CallService.set_repeat_state(chat_id, False)
    play_next_mock = AsyncMock()
    join_mock = AsyncMock()
    update = SimpleNamespace(chat_id=chat_id)

    with (
        patch.object(CallService, "get_active_calls", return_value={chat_id: {"source": "x", "media_type": "audio"}}),
        patch.object(CallService, "join_voice_chat", join_mock),
        patch.object(CallService, "play_next", play_next_mock),
    ):
        await handler(None, update)

    play_next_mock.assert_awaited_once()
    join_mock.assert_not_awaited()
