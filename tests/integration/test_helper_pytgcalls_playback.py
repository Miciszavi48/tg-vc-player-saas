"""Tests for helper-bound PyTgCalls playback routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_call_py_is_bot_session_detects_bot_token():
    from app.services.helper_pytgcalls_pool import call_py_is_bot_session

    bot_app = SimpleNamespace(bot_token="123:abc", me=SimpleNamespace(is_bot=True))
    bot_call_py = SimpleNamespace(_app=bot_app)
    assert call_py_is_bot_session(bot_call_py) is True


def test_call_py_is_bot_session_allows_test_mocks():
    from app.services.helper_pytgcalls_pool import call_py_is_bot_session

    mock_call_py = SimpleNamespace(join_group_call=AsyncMock())
    assert call_py_is_bot_session(mock_call_py) is False


@pytest.mark.asyncio
async def test_resolve_call_py_prefers_helper_pool():
    from app.services.call_service import _resolve_call_py

    helper_call_py = SimpleNamespace(play=AsyncMock())
    with patch(
        "app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_chat",
        AsyncMock(return_value=helper_call_py),
    ):
        resolved = await _resolve_call_py(-1001, helper_id=7, fallback=SimpleNamespace())

    assert resolved is helper_call_py


@pytest.mark.asyncio
async def test_resolve_call_py_rejects_bot_fallback():
    from app.services.call_service import _resolve_call_py

    bot_app = SimpleNamespace(bot_token="1:token", me=SimpleNamespace(is_bot=True))
    bot_call_py = SimpleNamespace(_app=bot_app)
    with patch(
        "app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_chat",
        AsyncMock(return_value=None),
    ):
        resolved = await _resolve_call_py(-1002, helper_id=8, fallback=bot_call_py)

    assert resolved is None


@pytest.mark.asyncio
async def test_join_voice_chat_uses_helper_pool_not_bot_call_py():
    from app.services.call_service import CallService

    helper_call_py = SimpleNamespace(join_group_call=AsyncMock())
    bot_app = SimpleNamespace(bot_token="1:token", me=SimpleNamespace(is_bot=True))
    bot_call_py = SimpleNamespace(_app=bot_app, play=AsyncMock())

    with (
        patch(
            "app.services.call_service._ensure_helper_in_chat",
            AsyncMock(return_value=3),
        ),
        patch(
            "app.services.call_service._ensure_group_call_before_play",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.helper_pytgcalls_pool.pytgcalls_available",
            return_value=True,
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="/tmp/safe.ogg"),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.transcode_pool.pre_transcode",
            AsyncMock(return_value="/tmp/safe.tc.ogg"),
        ),
        patch(
            "app.services.call_service.normalize_media_source",
            side_effect=lambda value: value,
        ),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.seek_tracker.start_seek_tracker", AsyncMock()),
        patch(
            "app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_chat",
            AsyncMock(return_value=helper_call_py),
        ) as pool_mock,
    ):
        ok = await CallService.join_voice_chat(
            bot_call_py,
            -1003,
            "/tmp/safe.ogg",
            "audio",
        )

    assert ok is True
    pool_mock.assert_awaited_once_with(-1003, helper_id=3)
    helper_call_py.join_group_call.assert_awaited_once()
    bot_call_py.play.assert_not_called()


def test_join_failure_key_maps_bot_method_invalid():
    from app.services.call_service import _join_failure_key_for_exception

    class BotMethodInvalid(Exception):
        pass

    key = _join_failure_key_for_exception(
        BotMethodInvalid("Telegram says: [400 BOT_METHOD_INVALID]")
    )
    assert key == "playback_cmd.voice_chat_unavailable"


def test_join_failure_key_maps_telegram_server_error():
    from app.services.call_service import _join_failure_key_for_exception

    class TelegramServerError(Exception):
        pass

    assert (
        _join_failure_key_for_exception(TelegramServerError("connect timeout"))
        == "playback_cmd.voice_chat_connect_failed"
    )


def test_join_failure_key_maps_file_not_found():
    from app.services.call_service import _join_failure_key_for_exception

    assert _join_failure_key_for_exception(FileNotFoundError()) == "playback_cmd.source_missing"


def test_register_stream_end_uses_fl_stream_end_in_source():
    import inspect

    from app.services import helper_pytgcalls_pool as pool_mod

    source = inspect.getsource(pool_mod.register_stream_end_handler)
    assert "fl.stream_end()" in source
    assert "Update as _PtgUpdate" not in source


def test_register_stream_end_handler_registers_only_once():
    from app.services.helper_pytgcalls_pool import register_stream_end_handler

    calls: list[object] = []
    mock_call_py = MagicMock()
    mock_call_py._stream_end_registered = False

    def fake_on_update(filter_obj):
        calls.append(filter_obj)

        def decorator(fn):
            return fn

        return decorator

    mock_call_py.on_update = fake_on_update
    register_stream_end_handler(mock_call_py)
    register_stream_end_handler(mock_call_py)
    assert len(calls) == 1
    assert mock_call_py._stream_end_registered is True


@pytest.mark.asyncio
async def test_join_voice_chat_rejects_concurrent_join():
    from app.services.call_service import CallService, _join_in_progress

    _join_in_progress.add(-9001)
    try:
        with patch(
            "app.services.helper_pytgcalls_pool.pytgcalls_available",
            return_value=True,
        ):
            ok = await CallService.join_voice_chat(None, -9001, "/tmp/x.ogg", "audio")
    finally:
        _join_in_progress.discard(-9001)

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.already_playing"


@pytest.mark.asyncio
async def test_ensure_group_call_skips_create_when_active():
    from app.services.call_service import _ensure_group_call_before_play

    app = SimpleNamespace(
        get_input_call=AsyncMock(return_value=object()),
        get_call=AsyncMock(),
        create_group_call=AsyncMock(),
    )
    call_py = SimpleNamespace(_app=app)

    ok = await _ensure_group_call_before_play(-100, 5, call_py)

    assert ok is True
    app.create_group_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_group_call_creates_on_pooled_client():
    from app.services.call_service import _ensure_group_call_before_play

    app = SimpleNamespace(
        get_input_call=AsyncMock(side_effect=[None, object()]),
        create_group_call=AsyncMock(),
    )
    call_py = SimpleNamespace(_app=app)

    with patch("asyncio.sleep", AsyncMock()) as sleep_mock:
        ok = await _ensure_group_call_before_play(-100, 5, call_py)

    assert ok is True
    app.create_group_call.assert_awaited_once_with(-100)
    assert app.get_input_call.await_count == 2
    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_ensure_group_call_returns_false_when_cache_empty_after_create():
    from app.services.call_service import _ensure_group_call_before_play

    app = SimpleNamespace(
        get_input_call=AsyncMock(return_value=None),
        create_group_call=AsyncMock(),
    )
    call_py = SimpleNamespace(_app=app)

    with patch("asyncio.sleep", AsyncMock()):
        ok = await _ensure_group_call_before_play(-100, 5, call_py)

    assert ok is False


@pytest.mark.asyncio
async def test_ensure_group_call_returns_false_when_create_fails():
    from app.services.call_service import _ensure_group_call_before_play

    app = SimpleNamespace(
        get_input_call=AsyncMock(return_value=None),
        get_call=AsyncMock(return_value=None),
        create_group_call=AsyncMock(side_effect=RuntimeError("fail")),
    )
    call_py = SimpleNamespace(_app=app)

    ok = await _ensure_group_call_before_play(-100, 5, call_py)

    assert ok is False


@pytest.mark.asyncio
async def test_ensure_group_call_never_calls_try_create():
    from app.services.call_service import _ensure_group_call_before_play

    app = SimpleNamespace(
        get_input_call=AsyncMock(side_effect=[None, object()]),
        create_group_call=AsyncMock(),
    )
    call_py = SimpleNamespace(_app=app)

    with (
        patch("asyncio.sleep", AsyncMock()),
        patch(
            "app.services.group_call_moderation_service.try_create_group_call",
            AsyncMock(),
        ) as try_create_mock,
    ):
        await _ensure_group_call_before_play(-100, 5, call_py)

    try_create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_voice_chat_leaves_on_play_failure():
    from app.services.call_service import CallService

    helper_call_py = SimpleNamespace(play=AsyncMock(side_effect=RuntimeError("connect")))

    with (
        patch(
            "app.services.call_service._ensure_helper_in_chat",
            AsyncMock(return_value=3),
        ),
        patch(
            "app.services.call_service._ensure_group_call_before_play",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.helper_pytgcalls_pool.pytgcalls_available",
            return_value=True,
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="/tmp/safe.ogg"),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.transcode_pool.pre_transcode",
            AsyncMock(return_value="/tmp/safe.tc.ogg"),
        ),
        patch(
            "app.services.call_service.normalize_media_source",
            side_effect=lambda value: value,
        ),
        patch(
            "app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_chat",
            AsyncMock(return_value=helper_call_py),
        ),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.utils.voice_stack.vc_join", AsyncMock(side_effect=RuntimeError("connect"))),
        patch("app.utils.voice_stack.vc_leave", AsyncMock()) as vc_leave_mock,
    ):
        ok = await CallService.join_voice_chat(
            None,
            -1005,
            "/tmp/safe.ogg",
            "audio",
        )

    assert ok is False
    vc_leave_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_join_voice_chat_fails_when_group_call_preflight_fails():
    from app.services.call_service import CallService

    helper_call_py = SimpleNamespace(play=AsyncMock())

    with (
        patch(
            "app.services.call_service._ensure_helper_in_chat",
            AsyncMock(return_value=3),
        ),
        patch(
            "app.services.call_service._ensure_group_call_before_play",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.helper_pytgcalls_pool.pytgcalls_available",
            return_value=True,
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="/tmp/safe.ogg"),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.transcode_pool.pre_transcode",
            AsyncMock(return_value="/tmp/safe.tc.ogg"),
        ),
        patch(
            "app.services.call_service.normalize_media_source",
            side_effect=lambda value: value,
        ),
        patch(
            "app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_chat",
            AsyncMock(return_value=helper_call_py),
        ),
        patch("app.utils.voice_stack.vc_join", AsyncMock()) as vc_join_mock,
    ):
        ok = await CallService.join_voice_chat(
            None,
            -1004,
            "/tmp/safe.ogg",
            "audio",
        )

    assert ok is False
    assert CallService.pop_join_failure_key() == "playback_cmd.voice_chat_connect_failed"
    vc_join_mock.assert_not_awaited()
