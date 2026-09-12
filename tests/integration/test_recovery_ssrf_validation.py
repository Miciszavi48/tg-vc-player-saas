"""Recovery playback SSRF validation for persisted PlaybackState sources."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _playback_state(
    chat_id: int,
    source: str,
    *,
    media_type: str = "audio",
):
    return SimpleNamespace(
        chat_id=chat_id,
        source=source,
        media_type=media_type,
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


@pytest.mark.asyncio
async def test_recovery_accepts_safe_public_https():
    from app.services.recovery_service import _run_recovery

    state = _playback_state(-3001, "https://93.184.216.34/recover.opus")
    safe_url = "https://93.184.216.34/recover.opus"

    with (
        patch("app.services.recovery_service.async_session", return_value=_mock_recovery_session([state])),
        patch("app.services.recovery_service.settings.RECOVERY_MAX_CONCURRENT", 10),
        patch("app.services.recovery_service.settings.RECOVERY_GLOBAL_PER_SECOND", 1000),
        patch("app.services.recovery_service.settings.RECOVERY_JITTER_MS", 0),
        patch("app.services.recovery_service.asyncio.sleep", AsyncMock()),
        patch(
            "app.utils.media_sources.validate_safe_url_with_redirects",
            AsyncMock(return_value=safe_url),
        ),
        patch("app.services.CallService.join_voice_chat", AsyncMock(return_value=True)) as join_mock,
        patch("app.services.CallService.clear_persisted_playback_state", AsyncMock()) as clear_mock,
    ):
        await _run_recovery(MagicMock())

    join_mock.assert_awaited_once()
    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/a.mp3",
        "http://10.0.0.5/a.mp3",
        "http://192.168.1.10/a.mp3",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/a.mp3",
        "file:///etc/passwd",
    ],
)
async def test_recovery_blocks_unsafe_urls_without_join(url: str):
    from app.services.recovery_service import _run_recovery

    state = _playback_state(-3002, url)

    with (
        patch("app.services.recovery_service.async_session", return_value=_mock_recovery_session([state])),
        patch("app.services.recovery_service.settings.RECOVERY_MAX_CONCURRENT", 10),
        patch("app.services.recovery_service.settings.RECOVERY_GLOBAL_PER_SECOND", 1000),
        patch("app.services.recovery_service.settings.RECOVERY_JITTER_MS", 0),
        patch("app.services.recovery_service.asyncio.sleep", AsyncMock()),
        patch("app.services.CallService.join_voice_chat", AsyncMock(return_value=False)) as join_mock,
        patch("app.services.CallService.clear_persisted_playback_state", AsyncMock()) as clear_mock,
    ):
        await _run_recovery(MagicMock())

    join_mock.assert_awaited_once()
    clear_mock.assert_awaited_once_with(-3002)


@pytest.mark.asyncio
async def test_recovery_blocks_redirect_to_private_target():
    from app.services.recovery_service import _run_recovery

    state = _playback_state(-3003, "https://example.com/redirect.mp3")

    with (
        patch("app.services.recovery_service.async_session", return_value=_mock_recovery_session([state])),
        patch("app.services.recovery_service.settings.RECOVERY_MAX_CONCURRENT", 10),
        patch("app.services.recovery_service.settings.RECOVERY_GLOBAL_PER_SECOND", 1000),
        patch("app.services.recovery_service.settings.RECOVERY_JITTER_MS", 0),
        patch("app.services.recovery_service.asyncio.sleep", AsyncMock()),
        patch(
            "app.utils.media_sources.validate_safe_url_with_redirects",
            AsyncMock(return_value=None),
        ),
        patch("app.services.CallService.join_voice_chat", AsyncMock(return_value=False)) as join_mock,
        patch("app.services.CallService.clear_persisted_playback_state", AsyncMock()) as clear_mock,
    ):
        await _run_recovery(MagicMock())

    join_mock.assert_awaited_once()
    clear_mock.assert_awaited_once_with(-3003)


@pytest.mark.asyncio
async def test_recovery_rejects_untrusted_local_path(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.recovery_service import _run_recovery

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
    outside = tmp_path / "outside.opus"
    outside.write_bytes(b"x")

    state = _playback_state(-3004, str(outside))

    with (
        patch("app.services.recovery_service.async_session", return_value=_mock_recovery_session([state])),
        patch("app.services.recovery_service.settings.RECOVERY_MAX_CONCURRENT", 10),
        patch("app.services.recovery_service.settings.RECOVERY_GLOBAL_PER_SECOND", 1000),
        patch("app.services.recovery_service.settings.RECOVERY_JITTER_MS", 0),
        patch("app.services.recovery_service.asyncio.sleep", AsyncMock()),
        patch("app.services.CallService.join_voice_chat", AsyncMock(return_value=False)) as join_mock,
        patch("app.services.CallService.clear_persisted_playback_state", AsyncMock()) as clear_mock,
        patch("app.services.transcode_pool.pre_transcode", AsyncMock()) as pre_tc,
    ):
        await _run_recovery(MagicMock())

    join_mock.assert_awaited_once()
    clear_mock.assert_awaited_once_with(-3004)
    pre_tc.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_accepts_trusted_local_file(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.recovery_service import _run_recovery

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
    trusted = downloads / "recover.opus"
    trusted.write_bytes(b"ok")
    tc_path = str(downloads / "recover.tc.ogg")
    Path(tc_path).write_bytes(b"tc")

    state = _playback_state(-3005, str(trusted))

    with (
        patch("app.services.recovery_service.async_session", return_value=_mock_recovery_session([state])),
        patch("app.services.recovery_service.settings.RECOVERY_MAX_CONCURRENT", 10),
        patch("app.services.recovery_service.settings.RECOVERY_GLOBAL_PER_SECOND", 1000),
        patch("app.services.recovery_service.settings.RECOVERY_JITTER_MS", 0),
        patch("app.services.recovery_service.asyncio.sleep", AsyncMock()),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=tc_path)),
        patch("app.services.call_service.normalize_media_source", side_effect=lambda s: s),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.helper_pool_service.HelperPoolService.increment_active_calls", AsyncMock()),
        patch("app.services.CallService.clear_persisted_playback_state", AsyncMock()) as clear_mock,
    ):
        call_py = MagicMock()
        call_py.join_group_call = AsyncMock()
        await _run_recovery(call_py)

    call_py.join_group_call.assert_awaited_once()
    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_bad_row_does_not_block_good_row():
    from app.services.recovery_service import _run_recovery

    bad = _playback_state(-3010, "http://127.0.0.1/a.mp3")
    good = _playback_state(-3011, "https://93.184.216.34/good.opus")
    good_url = "https://93.184.216.34/good.opus"

    async def _redirect_validate(url, **kwargs):
        if "127.0.0.1" in url:
            return None
        return url

    with (
        patch(
            "app.services.recovery_service.async_session",
            return_value=_mock_recovery_session([bad, good]),
        ),
        patch("app.services.recovery_service.settings.RECOVERY_MAX_CONCURRENT", 10),
        patch("app.services.recovery_service.settings.RECOVERY_GLOBAL_PER_SECOND", 1000),
        patch("app.services.recovery_service.settings.RECOVERY_JITTER_MS", 0),
        patch("app.services.recovery_service.asyncio.sleep", AsyncMock()),
        patch(
            "app.utils.media_sources.validate_safe_url_with_redirects",
            AsyncMock(side_effect=_redirect_validate),
        ),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value="/tmp/x.ogg")),
        patch("app.services.call_service.normalize_media_source", side_effect=lambda s: s),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.helper_pool_service.HelperPoolService.increment_active_calls", AsyncMock()),
        patch("app.services.CallService.clear_persisted_playback_state", AsyncMock()) as clear_mock,
        patch("app.services.CallService.join_voice_chat", AsyncMock(side_effect=[False, True])) as join_mock,
    ):
        await _run_recovery(MagicMock())

    assert join_mock.await_count == 2
    clear_mock.assert_awaited_once_with(-3010)


@pytest.mark.asyncio
async def test_join_voice_chat_uses_redirect_validator_for_http():
    from app.services.call_service import CallService

    call_py = SimpleNamespace(join_group_call=AsyncMock())
    url = "https://93.184.216.34/join.opus"

    with (
        patch(
            "app.utils.media_sources.validate_safe_url_with_redirects",
            AsyncMock(return_value=url),
        ) as redirect_mock,
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value="/tmp/x.ogg")),
        patch("app.services.call_service.normalize_media_source", side_effect=lambda s: s),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.helper_pool_service.HelperPoolService.increment_active_calls", AsyncMock()),
    ):
        ok = await CallService.join_voice_chat(call_py, -3020, url, "audio")

    assert ok is True
    redirect_mock.assert_awaited()
