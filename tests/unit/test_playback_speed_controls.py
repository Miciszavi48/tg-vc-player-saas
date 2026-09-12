from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest.fixture(autouse=True)
def _clear_speed_state():
    from app.services import call_service as cs

    cs._active_calls.clear()
    cs._playback_speed_states.clear()
    cs._playback_speed_locks.clear()
    from app.services.call_service import CallService
    CallService.pop_leave_failure_key()
    yield
    cs._active_calls.clear()
    cs._playback_speed_states.clear()
    cs._playback_speed_locks.clear()
    CallService.pop_leave_failure_key()


def _local_audio(tmp_path, monkeypatch):
    from app.config.settings import settings

    downloads = tmp_path / "downloads"
    cache = tmp_path / "media_cache"
    downloads.mkdir()
    cache.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache))
    track = downloads / "track.ogg"
    track.write_bytes(b"audio")
    return track


def _active_audio(chat_id: int, source: str) -> dict:
    from app.services.call_service import PLAYBACK_SPEED_DEFAULT

    return {
        "source": source,
        "speed_original_source": source,
        "speed_derivative_source": None,
        "speed_percent": PLAYBACK_SPEED_DEFAULT,
        "source_url": source if source.startswith(("http://", "https://")) else None,
        "stream_source": source if source.startswith(("http://", "https://")) else None,
        "media_type": "audio",
        "helper_id": 7,
        "playback_feature": None,
        "position_base_seconds": 0,
        "position_started_at": datetime.now(timezone.utc),
        "is_paused": False,
    }


def _active_video(chat_id: int, source: str) -> dict:
    return {
        **_active_audio(chat_id, source),
        "media_type": "video",
    }


@pytest.mark.asyncio
async def test_speed_change_success_updates_state_only_after_stream_replacement(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1001
    source = _local_audio(tmp_path, monkeypatch)
    derivative = tmp_path / "media_cache" / "speed.ogg"
    derivative.write_bytes(b"speed")
    cs._active_calls[chat_id] = _active_audio(chat_id, str(source))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))) as transcode,
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream") as build_stream,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "changed"
    assert result.speed_percent == 125
    assert CallService.get_playback_speed(chat_id) == 125
    assert cs._active_calls[chat_id]["speed_original_source"] == str(source.resolve())
    assert cs._active_calls[chat_id]["speed_derivative_source"] == str(derivative.resolve())
    transcode.assert_awaited_once_with(
        str(source.resolve()),
        125,
        media_type="audio",
        start_at_seconds=0,
    )
    build_stream.assert_called_once_with(str(derivative.resolve()), log_failures=True)
    change_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_speed_change_failure_keeps_old_state_and_stream(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1002
    source = _local_audio(tmp_path, monkeypatch)
    derivative = tmp_path / "media_cache" / "speed.ogg"
    derivative.write_bytes(b"speed")
    cs._active_calls[chat_id] = _active_audio(chat_id, str(source))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))),
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream"),
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "failed"
    assert CallService.get_playback_speed(chat_id) == 100
    assert cs._active_calls[chat_id]["speed_percent"] == 100
    assert cs._active_calls[chat_id]["speed_derivative_source"] is None


@pytest.mark.asyncio
async def test_speed_transcode_failure_keeps_old_state_without_stream_replacement(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1014
    source = _local_audio(tmp_path, monkeypatch)
    cs._active_calls[chat_id] = _active_audio(chat_id, str(source))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=None)) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "failed"
    assert CallService.get_playback_speed(chat_id) == 100
    assert cs._active_calls[chat_id]["speed_percent"] == 100
    transcode.assert_awaited_once()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_speed_changes_use_original_source_not_derivative(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1003
    source = _local_audio(tmp_path, monkeypatch)
    derivative_125 = tmp_path / "media_cache" / "speed125.ogg"
    derivative_150 = tmp_path / "media_cache" / "speed150.ogg"
    derivative_125.write_bytes(b"speed")
    derivative_150.write_bytes(b"speed")
    cs._active_calls[chat_id] = _active_audio(chat_id, str(source))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch(
            "app.services.transcode_pool.pre_transcode_speed",
            AsyncMock(side_effect=[str(derivative_125), str(derivative_150)]),
        ) as transcode,
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream"),
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()),
    ):
        await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)
        await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert [call.args for call in transcode.await_args_list] == [
        (str(source.resolve()), 125),
        (str(source.resolve()), 150),
    ]
    assert [call.kwargs for call in transcode.await_args_list] == [
        {"media_type": "audio", "start_at_seconds": 0},
        {"media_type": "audio", "start_at_seconds": 0},
    ]
    assert CallService.get_playback_speed(chat_id) == 150


@pytest.mark.asyncio
async def test_speed_bounds_do_not_transcode_or_replace_stream(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1004
    source = _local_audio(tmp_path, monkeypatch)
    cs._active_calls[chat_id] = _active_audio(chat_id, str(source))
    cs._playback_speed_states[chat_id] = 200
    cs._active_calls[chat_id]["speed_percent"] = 200

    with (
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        max_result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)
        cs._playback_speed_states[chat_id] = 50
        cs._active_calls[chat_id]["speed_percent"] = 50
        min_result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, -25)

    assert max_result.message_key == "playback.controls.speed_max"
    assert max_result.speed_label == "2.0"
    assert min_result.message_key == "playback.controls.speed_min"
    assert min_result.speed_label == "0.5"
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active",
    [
        {"source": "https://example.com/a.mp3", "media_type": "audio"},
        {"source": "", "media_type": "unknown"},
        {"source": "", "media_type": "audio", "playback_feature": "radio"},
        {"source": "", "media_type": "audio", "playback_feature": "satellite"},
        {"source": "", "media_type": "audio", "playback_feature": "tv"},
        {"source": "https://live.example.com/radio.m3u8", "media_type": "audio"},
    ],
)
async def test_speed_rejects_unsupported_current_sources(
    active,
    tmp_path,
    monkeypatch,
):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1005
    if active["source"] == "":
        active["source"] = str(_local_audio(tmp_path, monkeypatch))
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, active["source"]),
        **active,
    }

    with (
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "unsupported"
    assert result.show_alert is True
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_url_audio_uses_trusted_cached_original_without_network(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1015
    source_url = "https://youtube.com/watch?v=abc123"
    cached = _local_audio(tmp_path, monkeypatch)
    derivative = tmp_path / "media_cache" / "speed.ogg"
    derivative.write_bytes(b"speed")
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, source_url),
        "source_url": source_url,
        "speed_original_source": source_url,
    }

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.media_cache.cache_lookup", return_value=str(cached)) as cache_lookup,
        patch("app.services.media_service.MediaService.download_audio", AsyncMock()) as download_audio,
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))) as transcode,
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream"),
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()),
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "changed"
    cache_lookup.assert_called_once_with(source_url, "audio")
    download_audio.assert_not_awaited()
    transcode.assert_awaited_once_with(
        str(cached.resolve()),
        125,
        media_type="audio",
        start_at_seconds=0,
    )
    assert cs._active_calls[chat_id]["speed_original_source"] == str(cached.resolve())


@pytest.mark.asyncio
async def test_uncached_url_audio_returns_safe_feedback_without_transcode_or_download():
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1016
    source_url = "https://youtube.com/watch?v=missing"
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, source_url),
        "source_url": source_url,
        "speed_original_source": source_url,
    }

    with (
        patch("app.services.media_cache.cache_lookup", return_value=None) as cache_lookup,
        patch("app.services.media_service.MediaService.download_audio", AsyncMock()) as download_audio,
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "unsupported"
    assert result.message_key == "playback.controls.speed_source_unavailable"
    cache_lookup.assert_called_once_with(source_url, "audio")
    download_audio.assert_not_awaited()
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_url_returns_live_unsupported_without_cache_lookup():
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1019
    source_url = "https://stream.example.com/channel.m3u8"
    cs._active_calls[chat_id] = _active_audio(chat_id, source_url)

    with (
        patch("app.services.media_cache.cache_lookup") as cache_lookup,
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "unsupported"
    assert result.message_key == "playback.controls.speed_live_unsupported"
    cache_lookup.assert_not_called()
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_finite_local_video_speed_uses_video_derivative_and_video_stream(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services import call_service as cs
    from app.services.call_service import CallService

    downloads = tmp_path / "downloads"
    cache = tmp_path / "media_cache"
    downloads.mkdir()
    cache.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache))
    source = downloads / "clip.mp4"
    source.write_bytes(b"video")
    derivative = cache / "speed.mp4"
    derivative.write_bytes(b"speed")
    chat_id = -1017
    cs._active_calls[chat_id] = _active_video(chat_id, str(source))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))) as transcode,
        patch("app.utils.voice_stack.build_video_stream", return_value="video-stream") as build_video,
        patch("app.utils.voice_stack.build_audio_stream", return_value="audio-stream") as build_audio,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.status == "changed"
    transcode.assert_awaited_once_with(
        str(source.resolve()),
        125,
        media_type="video",
        start_at_seconds=0,
    )
    build_video.assert_called_once_with(str(derivative.resolve()), log_failures=True)
    build_audio.assert_not_called()
    change_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_speed_change_uses_estimated_current_position_for_derivative(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1018
    source = _local_audio(tmp_path, monkeypatch)
    derivative = tmp_path / "media_cache" / "speed.ogg"
    derivative.write_bytes(b"speed")
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, str(source)),
        "position_base_seconds": 30,
        "position_started_at": datetime.now(timezone.utc) - timedelta(seconds=10),
    }

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))) as transcode,
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream"),
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()),
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), chat_id, 25)

    assert result.message_key == "playback.controls.speed_set_position"
    start_at = transcode.await_args.kwargs["start_at_seconds"]
    assert 39 <= start_at <= 41
    assert cs._active_calls[chat_id]["position_base_seconds"] == start_at


@pytest.mark.asyncio
async def test_speed_no_active_playback_returns_safe_feedback_without_work():
    from app.services.call_service import CallService

    with (
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.change_playback_speed(SimpleNamespace(), -9001, 25)

    assert result.status == "no_active"
    assert result.message_key == "playback_cmd.no_voice_chat"
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_seek_forward_replaces_stream_from_current_position_and_preserves_speed(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1020
    source = _local_audio(tmp_path, monkeypatch)
    derivative = tmp_path / "media_cache" / "seek.ogg"
    derivative.write_bytes(b"seek")
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, str(source)),
        "position_base_seconds": 30,
        "is_paused": True,
        "speed_percent": 125,
    }
    cs._playback_speed_states[chat_id] = 125

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))) as transcode,
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream") as build_stream,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.seek_playback(SimpleNamespace(), chat_id, 10)

    assert result.status == "changed"
    assert result.position_seconds == 40
    assert CallService.get_playback_speed(chat_id) == 125
    assert cs._active_calls[chat_id]["position_base_seconds"] == 40
    transcode.assert_awaited_once_with(
        str(source.resolve()),
        125,
        media_type="audio",
        start_at_seconds=40,
    )
    build_stream.assert_called_once_with(str(derivative.resolve()), log_failures=True)
    change_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_seek_back_clamps_to_start_without_queue_previous(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1021
    source = _local_audio(tmp_path, monkeypatch)
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, str(source)),
        "position_base_seconds": 0,
        "is_paused": True,
    }

    with (
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.seek_playback(SimpleNamespace(), chat_id, -10)

    assert result.status == "boundary"
    assert result.message_key == "playback.controls.seek_start"
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_seek_failure_keeps_old_position_and_derivative(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1022
    source = _local_audio(tmp_path, monkeypatch)
    derivative = tmp_path / "media_cache" / "seek.ogg"
    derivative.write_bytes(b"seek")
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, str(source)),
        "position_base_seconds": 20,
        "is_paused": True,
        "speed_derivative_source": None,
    }

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))),
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream"),
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        result = await CallService.seek_playback(SimpleNamespace(), chat_id, 10)

    assert result.status == "failed"
    assert cs._active_calls[chat_id]["position_base_seconds"] == 20
    assert cs._active_calls[chat_id]["speed_derivative_source"] is None


@pytest.mark.asyncio
async def test_seek_rejects_live_and_uncached_url_without_transcode(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1023
    cs._active_calls[chat_id] = _active_audio(chat_id, "https://stream.example.com/live.m3u8")

    with (
        patch("app.services.media_cache.cache_lookup") as cache_lookup,
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.seek_playback(SimpleNamespace(), chat_id, 10)

    assert result.status == "unsupported"
    assert result.message_key == "playback.controls.seek_live_unsupported"
    cache_lookup.assert_not_called()
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()

    uncached_chat = -1024
    source_url = "https://youtube.com/watch?v=missing"
    cs._active_calls[uncached_chat] = {
        **_active_audio(uncached_chat, source_url),
        "source_url": source_url,
        "speed_original_source": source_url,
    }
    with (
        patch("app.services.media_cache.cache_lookup", return_value=None) as cache_lookup,
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock()) as transcode,
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()) as change_stream,
    ):
        result = await CallService.seek_playback(SimpleNamespace(), uncached_chat, 10)

    assert result.status == "unsupported"
    assert result.message_key == "playback.controls.seek_source_unavailable"
    cache_lookup.assert_called_once_with(source_url, "audio")
    transcode.assert_not_awaited()
    change_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_speed_state_is_per_chat(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    source = _local_audio(tmp_path, monkeypatch)
    derivative = tmp_path / "media_cache" / "speed.ogg"
    derivative.write_bytes(b"speed")
    cs._active_calls[-1] = _active_audio(-1, str(source))
    cs._active_calls[-2] = _active_audio(-2, str(source))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.services.transcode_pool.pre_transcode_speed", AsyncMock(return_value=str(derivative))),
        patch("app.utils.voice_stack.build_audio_stream", return_value="stream"),
        patch("app.utils.voice_stack.vc_change_stream", AsyncMock()),
    ):
        await CallService.change_playback_speed(SimpleNamespace(), -1, 25)

    assert CallService.get_playback_speed(-1) == 125
    assert CallService.get_playback_speed(-2) == 100


@pytest.mark.asyncio
async def test_leave_clears_speed_state_and_derivative_reference(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1006
    source = _local_audio(tmp_path, monkeypatch)
    cs._active_calls[chat_id] = {
        **_active_audio(chat_id, str(source)),
        "speed_derivative_source": str(tmp_path / "media_cache" / "speed.ogg"),
        "speed_percent": 125,
    }
    cs._playback_speed_states[chat_id] = 125

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.utils.voice_stack.vc_leave", AsyncMock()),
        patch("app.services.transcode_pool.cleanup_transcoded") as cleanup_transcoded,
        patch("app.services.transcode_pool.cleanup_speed_derivatives") as cleanup_speed,
        patch("app.services.helper_pool_service.HelperPoolService.decrement_active_calls", AsyncMock()),
        patch("app.services.call_service._remove_playback_state", AsyncMock()),
    ):
        ok = await CallService.leave_voice_chat(SimpleNamespace(), chat_id)

    assert ok is True
    assert CallService.get_playback_speed(chat_id) == 100
    assert chat_id not in cs._active_calls
    cleanup_transcoded.assert_called_once_with(str(source.resolve()))
    cleanup_speed.assert_called_once_with(str(source.resolve()))


@pytest.mark.asyncio
async def test_leave_voice_chat_returns_false_when_no_active_call_object():
    from app.services.call_service import CallService

    with patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=None)):
        ok = await CallService.leave_voice_chat(SimpleNamespace(), -1099)

    assert ok is False
    assert CallService.pop_leave_failure_key() == "playback_cmd.no_voice_chat"


@pytest.mark.asyncio
async def test_leave_voice_chat_failure_preserves_active_state(tmp_path, monkeypatch):
    from app.services import call_service as cs
    from app.services.call_service import CallService

    chat_id = -1007
    source = _local_audio(tmp_path, monkeypatch)
    cs._active_calls[chat_id] = _active_audio(chat_id, str(source))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=SimpleNamespace())),
        patch("app.utils.voice_stack.vc_leave", AsyncMock(side_effect=RuntimeError("leave failed"))),
        patch("app.services.call_service._remove_playback_state", AsyncMock()) as remove_state,
    ):
        ok = await CallService.leave_voice_chat(SimpleNamespace(), chat_id)

    assert ok is False
    assert CallService.pop_leave_failure_key() == "playback_cmd.failed"
    assert chat_id in cs._active_calls
    remove_state.assert_not_awaited()
    cs._active_calls.pop(chat_id, None)


@pytest.mark.asyncio
async def test_speed_transcode_audio_uses_atempo_and_offset(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.transcode_pool import pre_transcode_speed

    downloads = tmp_path / "downloads"
    cache = tmp_path / "media_cache"
    downloads.mkdir()
    cache.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache))
    source = downloads / "track.ogg"
    source.write_bytes(b"audio")
    seen = {}

    class _Proc:
        async def wait(self):
            output = seen["cmd"][-1]
            with open(output, "wb") as f:
                f.write(b"speed")
            return 0

    async def _exec(*cmd, **kwargs):  # noqa: ANN001
        seen["cmd"] = list(cmd)
        return _Proc()

    with patch("asyncio.create_subprocess_exec", _exec):
        result = await pre_transcode_speed(
            str(source),
            125,
            media_type="audio",
            start_at_seconds=42,
        )

    assert result is not None
    cmd = seen["cmd"]
    assert cmd[:4] == ["ffmpeg", "-y", "-ss", "42"]
    assert "-filter:a" in cmd
    assert "atempo=1.25" in cmd
    assert "-vn" in cmd


@pytest.mark.asyncio
async def test_speed_transcode_video_uses_synchronized_video_and_audio_filters(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.transcode_pool import pre_transcode_speed

    downloads = tmp_path / "downloads"
    cache = tmp_path / "media_cache"
    downloads.mkdir()
    cache.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(cache))
    source = downloads / "clip.mp4"
    source.write_bytes(b"video")
    seen = {}

    class _Proc:
        async def wait(self):
            output = seen["cmd"][-1]
            with open(output, "wb") as f:
                f.write(b"speed-video")
            return 0

    async def _exec(*cmd, **kwargs):  # noqa: ANN001
        seen["cmd"] = list(cmd)
        return _Proc()

    with patch("asyncio.create_subprocess_exec", _exec):
        result = await pre_transcode_speed(
            str(source),
            150,
            media_type="video",
            start_at_seconds=12,
        )

    assert result is not None
    cmd = seen["cmd"]
    assert cmd[:4] == ["ffmpeg", "-y", "-ss", "12"]
    assert "-filter_complex" in cmd
    filter_arg = cmd[cmd.index("-filter_complex") + 1]
    assert "setpts=0.6667*PTS" in filter_arg
    assert "atempo=1.5" in filter_arg
    assert "-map" in cmd
