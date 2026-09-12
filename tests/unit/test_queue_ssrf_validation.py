"""Queue and prefetch SSRF validation for CallService.play_next / _prefetch_next."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.media_sources import resolve_media_source_for_playback


@pytest.mark.asyncio
async def test_resolve_media_source_for_playback_uses_redirect_validator_for_http():
    url = "https://93.184.216.34/test-audio.opus"
    with patch(
        "app.utils.media_sources.validate_safe_url_with_redirects",
        AsyncMock(return_value=url),
    ) as redirect_mock:
        resolved = await resolve_media_source_for_playback(url, chat_id=-1001)
    assert resolved == url
    redirect_mock.assert_awaited_once_with(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/audio.mp3",
        "http://192.168.1.10/audio.mp3",
        "http://10.0.0.5/audio.mp3",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/audio.mp3",
        "file:///etc/passwd",
    ],
)
async def test_resolve_media_source_for_playback_blocks_unsafe_urls(url: str):
    assert await resolve_media_source_for_playback(url, chat_id=-1002) is None


@pytest.mark.asyncio
async def test_resolve_media_source_for_playback_blocks_redirect_to_private():
    with patch(
        "app.utils.media_sources.validate_safe_url_with_redirects",
        new_callable=AsyncMock,
        return_value=None,
    ) as redirect_mock:
        resolved = await resolve_media_source_for_playback(
            "https://example.com/redirect.mp3",
            chat_id=-1003,
        )

    assert resolved is None
    redirect_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_media_source_for_playback_rejects_untrusted_local(tmp_path, monkeypatch):
    from app.config.settings import settings

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))

    outside = tmp_path / "outside.opus"
    outside.write_bytes(b"x")
    assert await resolve_media_source_for_playback(str(outside), chat_id=-1004) is None


@pytest.mark.asyncio
async def test_resolve_media_source_for_playback_allows_trusted_local(tmp_path, monkeypatch):
    from app.config.settings import settings

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))

    trusted = downloads / "queued.opus"
    trusted.write_bytes(b"ok")
    resolved = await resolve_media_source_for_playback(str(trusted), chat_id=-1005)
    assert resolved == str(trusted.resolve())


def _queue_item(
    *,
    stream_url: str | None = None,
    file_path: str | None = None,
    title: str = "Track",
):
    return SimpleNamespace(
        stream_url=stream_url,
        file_path=file_path,
        media_type="audio",
        title=title,
    )


@pytest.mark.asyncio
async def test_play_next_plays_public_https_queue_item():
    from app.services.call_service import CallService

    call_py = SimpleNamespace()
    vc_change = AsyncMock()
    safe_url = "https://93.184.216.34/queue.opus"
    safe_item = _queue_item(stream_url=safe_url)
    tc_path = "/tmp/queue.tc.ogg"

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=call_py)),
        patch("app.services.call_service.playlist_repo.advance_queue", AsyncMock(return_value=safe_item)),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value=safe_url),
        ),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=tc_path)),
        patch("app.services.call_service.normalize_media_source", side_effect=lambda s: s),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.utils.voice_stack.vc_change_stream", vc_change),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.media_event_service.track_media_play", AsyncMock()),
    ):
        ok = await CallService.play_next(call_py, -2001)

    assert ok is True
    vc_change.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_next_blocks_localhost_without_streaming():
    from app.services.call_service import CallService

    call_py = SimpleNamespace(change_stream=AsyncMock())
    bad_item = _queue_item(stream_url="http://127.0.0.1:8080/a.mp3", title="Bad")

    with (
        patch(
            "app.services.call_service.playlist_repo.advance_queue",
            AsyncMock(side_effect=[bad_item, None]),
        ),
        patch("app.services.call_service.CallService.leave_voice_chat", AsyncMock(return_value=True)) as leave_mock,
        patch("app.services.transcode_pool.pre_transcode", AsyncMock()) as pre_tc,
    ):
        ok = await CallService.play_next(call_py, -2002)

    assert ok is False
    call_py.change_stream.assert_not_awaited()
    pre_tc.assert_not_awaited()
    leave_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_next_skips_unsafe_then_plays_safe_item():
    from app.services.call_service import CallService

    call_py = SimpleNamespace()
    vc_change = AsyncMock()
    bad_item = _queue_item(stream_url="http://169.254.169.254/meta", title="Bad")
    good_url = "https://93.184.216.34/good.opus"
    good_item = _queue_item(stream_url=good_url, title="Good")
    tc_path = "/tmp/good.tc.ogg"

    async def _resolve_source(url, **kwargs):
        if "169.254" in str(url) or "127.0.0.1" in str(url):
            return None
        return url

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=call_py)),
        patch(
            "app.services.call_service.playlist_repo.advance_queue",
            AsyncMock(side_effect=[bad_item, good_item]),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(side_effect=_resolve_source),
        ),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=tc_path)),
        patch("app.services.call_service.normalize_media_source", side_effect=lambda s: s),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.utils.voice_stack.vc_change_stream", vc_change),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.media_event_service.track_media_play", AsyncMock()),
    ):
        ok = await CallService.play_next(call_py, -2003)

    assert ok is True
    vc_change.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_next_trusted_local_file_still_works(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.call_service import CallService

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
    local_file = downloads / "q.opus"
    local_file.write_bytes(b"audio")
    tc_path = str(downloads / "q.tc.ogg")
    Path(tc_path).write_bytes(b"tc")

    call_py = SimpleNamespace()
    vc_change = AsyncMock()
    item = _queue_item(file_path=str(local_file))

    with (
        patch("app.services.call_service._resolve_call_py", AsyncMock(return_value=call_py)),
        patch("app.services.call_service.playlist_repo.advance_queue", AsyncMock(return_value=item)),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value=str(local_file)),
        ),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock(return_value=tc_path)),
        patch("app.services.call_service.normalize_media_source", side_effect=lambda s: s),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.utils.voice_stack.vc_change_stream", vc_change),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.media_event_service.track_media_play", AsyncMock()),
    ):
        ok = await CallService.play_next(call_py, -2004)

    assert ok is True
    vc_change.assert_awaited_once()


@pytest.mark.asyncio
async def test_play_next_unsafe_local_path_not_streamed(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.call_service import CallService

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))
    outside = tmp_path / "evil.opus"
    outside.write_bytes(b"no")

    call_py = SimpleNamespace(change_stream=AsyncMock())
    item = _queue_item(file_path=str(outside))

    with (
        patch(
            "app.services.call_service.playlist_repo.advance_queue",
            AsyncMock(side_effect=[item, None]),
        ),
        patch("app.services.call_service.CallService.leave_voice_chat", AsyncMock(return_value=True)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock()) as pre_tc,
    ):
        ok = await CallService.play_next(call_py, -2005)

    assert ok is False
    call_py.change_stream.assert_not_awaited()
    pre_tc.assert_not_awaited()


@pytest.mark.asyncio
async def test_prefetch_calls_redirect_validation_for_http_queue_url():
    from app.services.call_service import _prefetch_cache, _prefetch_next

    item0 = _queue_item(file_path=None, stream_url=None, title="Current")
    item1 = _queue_item(stream_url="https://93.184.216.34/next.opus", title="Next")

    with (
        patch("app.services.call_service.playlist_repo") as mock_pr,
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="https://93.184.216.34/next.opus"),
        ) as resolve_mock,
        patch("app.services.media_service.MediaService.download_audio", AsyncMock(return_value=None)),
        patch("app.services.transcode_pool.pre_transcode", AsyncMock()) as pre_tc,
    ):
        mock_pr.get_queue = AsyncMock(return_value=[item0, item1])
        await _prefetch_next(-2006)

    resolve_mock.assert_awaited_once()
    pre_tc.assert_not_awaited()
    assert -2006 not in _prefetch_cache


@pytest.mark.asyncio
async def test_prefetch_blocks_private_url_before_download():
    from app.services.call_service import _prefetch_cache, _prefetch_next

    item0 = _queue_item(title="Current")
    item1 = _queue_item(stream_url="http://127.0.0.1/stream.mp3", title="Next")

    with (
        patch("app.services.call_service.playlist_repo") as mock_pr,
        patch("app.services.media_service.MediaService.download_audio", new_callable=AsyncMock) as download_audio,
        patch("app.services.transcode_pool.pre_transcode", new_callable=AsyncMock) as pre_transcode,
    ):
        mock_pr.get_queue = AsyncMock(return_value=[item0, item1])
        await _prefetch_next(-2007)

    assert -2007 not in _prefetch_cache
    download_audio.assert_not_awaited()
    pre_transcode.assert_not_awaited()
