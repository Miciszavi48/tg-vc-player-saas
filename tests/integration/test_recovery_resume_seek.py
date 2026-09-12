"""Regression tests for playback recovery resume position.

Covers the fix where restart recovery must resume a persisted session at its
stored ``playback_states.seek_sec`` instead of restarting near zero and then
overwriting the persisted position with early values.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.recovery_service import (
    ResumeDecision,
    _is_seekable_source,
    compute_resume_position,
)


# ── Pure decision logic ──────────────────────────────────────────────────────

def test_playing_seekable_resumes_at_persisted_seek():
    decision = compute_resume_position(
        seek_sec=7360, duration_sec=10800, is_paused=False, is_seekable=True,
    )
    assert decision == ResumeDecision(
        start_at=7360, resume_paused=False, seekable=True, completed=False,
    )


def test_paused_session_remains_paused_at_persisted_seek():
    decision = compute_resume_position(
        seek_sec=1234, duration_sec=10800, is_paused=True, is_seekable=True,
    )
    assert decision.start_at == 1234
    assert decision.resume_paused is True
    assert decision.seekable is True


def test_elapsed_downtime_is_not_added_to_resume_position():
    # The resume position depends only on the persisted seek, never on how long
    # the service was down — audio does not advance while the process is dead.
    decision = compute_resume_position(
        seek_sec=600, duration_sec=10800, is_paused=False, is_seekable=True,
    )
    assert decision.start_at == 600  # not 600 + downtime


def test_duration_clamping_keeps_position_inside_track():
    decision = compute_resume_position(
        seek_sec=10800, duration_sec=10800, is_paused=False, is_seekable=True,
    )
    # seek == duration → treated as completed, restart at 0 (no out-of-range seek)
    assert decision.completed is True
    assert decision.start_at == 0


def test_position_just_below_duration_is_clamped_not_completed():
    decision = compute_resume_position(
        seek_sec=10799, duration_sec=10800, is_paused=False, is_seekable=True,
    )
    assert decision.completed is False
    assert decision.start_at == 10799


def test_position_beyond_duration_is_completed():
    decision = compute_resume_position(
        seek_sec=99999, duration_sec=200, is_paused=False, is_seekable=True,
    )
    assert decision.completed is True
    assert decision.start_at == 0


def test_unknown_duration_uses_raw_seek():
    decision = compute_resume_position(
        seek_sec=500, duration_sec=0, is_paused=False, is_seekable=True,
    )
    assert decision.start_at == 500
    assert decision.completed is False


def test_non_seekable_source_falls_back_to_zero():
    decision = compute_resume_position(
        seek_sec=7360, duration_sec=0, is_paused=False, is_seekable=False,
    )
    assert decision.start_at == 0
    assert decision.seekable is False


def test_negative_seek_is_clamped_to_zero():
    decision = compute_resume_position(
        seek_sec=-50, duration_sec=100, is_paused=False, is_seekable=True,
    )
    assert decision.start_at == 0


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("/opt/musicbot/downloads/song.opus", True),
        ("https://example.com/track.mp3", True),
        ("https://radio.example.com/stream.m3u8", False),
        ("http://icecast.example.com:8000/live", False),
        ("http://host/shoutcast", False),
        (None, False),
        ("", False),
    ],
)
def test_is_seekable_source_classification(source, expected):
    assert _is_seekable_source(source) is expected


# ── Recovery loop forwards the resume position ───────────────────────────────

def _playback_state(
    chat_id: int,
    source: str,
    *,
    media_type: str = "audio",
    seek_sec: int = 0,
    duration_sec: int = 0,
    is_paused: bool = False,
):
    return SimpleNamespace(
        chat_id=chat_id,
        source=source,
        media_type=media_type,
        seek_sec=seek_sec,
        duration_sec=duration_sec,
        is_paused=is_paused,
        last_update_at=None,
    )


def _mock_recovery_session(states: list):
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = states
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _recovery_patches(states: list, join_mock: AsyncMock):
    return (
        patch(
            "app.services.recovery_service.async_session",
            return_value=_mock_recovery_session(states),
        ),
        patch("app.services.recovery_service.settings.RECOVERY_MAX_CONCURRENT", 10),
        patch("app.services.recovery_service.settings.RECOVERY_GLOBAL_PER_SECOND", 1000),
        patch("app.services.recovery_service.settings.RECOVERY_JITTER_MS", 0),
        patch("app.services.recovery_service.asyncio.sleep", AsyncMock()),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.CallService.join_voice_chat", join_mock),
        patch("app.services.CallService.clear_persisted_playback_state", AsyncMock()),
    )


@pytest.mark.asyncio
async def test_recovery_forwards_persisted_seek_to_join():
    from app.services.recovery_service import _run_recovery

    state = _playback_state(
        -5001, "/opt/musicbot/downloads/song.opus",
        seek_sec=7360, duration_sec=10800, is_paused=False,
    )
    join_mock = AsyncMock(return_value=True)

    from contextlib import ExitStack

    with ExitStack() as stack:
        for cm in _recovery_patches([state], join_mock):
            stack.enter_context(cm)
        await _run_recovery(MagicMock())

    join_mock.assert_awaited_once()
    kwargs = join_mock.await_args.kwargs
    assert kwargs["resume_from_seconds"] == 7360
    assert kwargs["resume_paused"] is False
    assert kwargs["duration_seconds"] == 10800


@pytest.mark.asyncio
async def test_recovery_forwards_paused_flag():
    from app.services.recovery_service import _run_recovery

    state = _playback_state(
        -5002, "/opt/musicbot/downloads/song.opus",
        seek_sec=42, duration_sec=300, is_paused=True,
    )
    join_mock = AsyncMock(return_value=True)

    from contextlib import ExitStack

    with ExitStack() as stack:
        for cm in _recovery_patches([state], join_mock):
            stack.enter_context(cm)
        await _run_recovery(MagicMock())

    kwargs = join_mock.await_args.kwargs
    assert kwargs["resume_from_seconds"] == 42
    assert kwargs["resume_paused"] is True


@pytest.mark.asyncio
async def test_recovery_multi_instance_chat_isolation():
    """Each persisted chat resumes at its own seek — no cross-contamination."""
    from app.services.recovery_service import _run_recovery

    states = [
        _playback_state(-6001, "/opt/musicbot/downloads/a.opus", seek_sec=100, duration_sec=500),
        _playback_state(-6002, "/opt/musicbot/downloads/b.opus", seek_sec=250, duration_sec=500),
    ]
    join_mock = AsyncMock(return_value=True)

    from contextlib import ExitStack

    with ExitStack() as stack:
        for cm in _recovery_patches(states, join_mock):
            stack.enter_context(cm)
        await _run_recovery(MagicMock())

    seen = {
        call.args[1]: call.kwargs["resume_from_seconds"]
        for call in join_mock.await_args_list
    }
    assert seen == {-6001: 100, -6002: 250}


# ── Idempotency / duplicate recovery ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_schedule_recovery_does_not_spawn_second_task():
    from app.services import recovery_service

    running = MagicMock()
    running.done.return_value = False

    with (
        patch.object(recovery_service, "_recovery_task", running),
        patch.object(recovery_service, "create_logged_task") as spawn,
    ):
        await recovery_service.schedule_recovery(MagicMock())

    spawn.assert_not_called()


# ── join_voice_chat applies the offset to the stream ─────────────────────────

def _join_patches(stack, *, seekable=True, helper=1):
    """Patch the I/O boundary of ``join_voice_chat`` for offset tests."""
    pre_transcode_speed = AsyncMock(
        return_value="/opt/musicbot/downloads/song.pos.ogg" if seekable else None,
    )
    pre_transcode = AsyncMock(return_value="/opt/musicbot/downloads/song.tc.ogg")
    start_tracker = AsyncMock()
    vc_join = AsyncMock()

    cms = [
        patch("app.services.helper_pytgcalls_pool.pytgcalls_available", return_value=True),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="/opt/musicbot/downloads/song.opus"),
        ),
        patch(
            "app.services.call_service._ensure_helper_in_chat",
            AsyncMock(return_value=helper),
        ),
        patch("app.services.transcode_pool.pre_transcode_speed", pre_transcode_speed),
        patch("app.services.transcode_pool.pre_transcode", pre_transcode),
        patch("app.services.call_service.normalize_media_source", side_effect=lambda s: s),
        patch(
            "app.services.call_service._resolve_call_py",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.call_service._ensure_group_call_before_play",
            AsyncMock(return_value=True),
        ),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.utils.voice_stack.vc_join", vc_join),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.seek_tracker.start_seek_tracker", start_tracker),
        patch("app.services.call_service._trigger_prefetch"),
    ]
    for cm in cms:
        stack.enter_context(cm)
    return SimpleNamespace(
        pre_transcode_speed=pre_transcode_speed,
        pre_transcode=pre_transcode,
        start_tracker=start_tracker,
        vc_join=vc_join,
    )


@pytest.mark.asyncio
async def test_join_applies_seek_offset_and_starts_tracker_at_offset():
    from contextlib import ExitStack

    from app.services.call_service import CallService, _active_calls

    chat_id = -7001
    try:
        with ExitStack() as stack:
            mocks = _join_patches(stack, seekable=True)
            ok = await CallService.join_voice_chat(
                MagicMock(), chat_id, "/opt/musicbot/downloads/song.opus", "audio",
                skip_event_tracking=True, resume_from_seconds=7360, duration_seconds=10800,
            )
        assert ok is True
        # ffmpeg -ss offset applied via pre_transcode_speed
        assert mocks.pre_transcode_speed.await_args.kwargs["start_at_seconds"] == 7360
        # seek tracker resumes from the recovered offset, NOT 0
        assert mocks.start_tracker.await_args.kwargs["initial_seek"] == 7360
        assert _active_calls[chat_id]["position_base_seconds"] == 7360
    finally:
        _active_calls.pop(chat_id, None)


@pytest.mark.asyncio
async def test_join_non_seekable_source_falls_back_to_zero():
    from contextlib import ExitStack

    from app.services.call_service import CallService, _active_calls

    chat_id = -7002
    try:
        with ExitStack() as stack:
            mocks = _join_patches(stack, seekable=False)
            ok = await CallService.join_voice_chat(
                MagicMock(), chat_id, "/opt/musicbot/downloads/song.opus", "audio",
                skip_event_tracking=True, resume_from_seconds=7360, duration_seconds=10800,
            )
        assert ok is True
        # pre_transcode_speed returned None → fall back to full pre_transcode from 0
        mocks.pre_transcode.assert_awaited()
        assert mocks.start_tracker.await_args.kwargs["initial_seek"] == 0
        assert _active_calls[chat_id]["position_base_seconds"] == 0
    finally:
        _active_calls.pop(chat_id, None)


@pytest.mark.asyncio
async def test_join_resume_paused_pauses_after_start():
    from contextlib import ExitStack

    from app.services.call_service import CallService, _active_calls

    chat_id = -7003
    pause_mock = AsyncMock(return_value=True)
    try:
        with ExitStack() as stack:
            mocks = _join_patches(stack, seekable=True)
            stack.enter_context(
                patch("app.services.call_service.CallService.pause", pause_mock),
            )
            ok = await CallService.join_voice_chat(
                MagicMock(), chat_id, "/opt/musicbot/downloads/song.opus", "audio",
                skip_event_tracking=True, resume_from_seconds=1234,
                resume_paused=True, duration_seconds=300,
            )
        assert ok is True
        assert mocks.start_tracker.await_args.kwargs["initial_seek"] == 1234
        pause_mock.assert_awaited_once()
    finally:
        _active_calls.pop(chat_id, None)


@pytest.mark.asyncio
async def test_join_missing_helper_fails_without_starting_tracker():
    from contextlib import ExitStack

    from app.services.call_service import CallService, _active_calls

    chat_id = -7004
    try:
        with ExitStack() as stack:
            mocks = _join_patches(stack, seekable=True, helper=None)
            ok = await CallService.join_voice_chat(
                MagicMock(), chat_id, "/opt/musicbot/downloads/song.opus", "audio",
                skip_event_tracking=True, resume_from_seconds=7360, duration_seconds=10800,
            )
        assert ok is False
        mocks.start_tracker.assert_not_awaited()
        assert chat_id not in _active_calls
    finally:
        _active_calls.pop(chat_id, None)
