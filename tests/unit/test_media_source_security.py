from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_normalize_media_source_allows_only_http_or_trusted_local(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.utils.media_sources import normalize_media_source

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    media_cache = tmp_path / "media_cache"
    media_cache.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(media_cache))

    trusted = downloads / "track.opus"
    trusted.write_bytes(b"trusted")
    outside = tmp_path / "outside.opus"
    outside.write_bytes(b"outside")

    assert normalize_media_source("https://93.184.216.34/a.opus") == "https://93.184.216.34/a.opus"
    assert normalize_media_source(str(trusted)) == str(trusted.resolve())
    assert normalize_media_source(str(outside)) is None
    assert normalize_media_source("file:///etc/passwd") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/stream.mp3",
        "http://127.0.0.1/stream.mp3",
        "http://0.0.0.0/stream.mp3",
        "http://10.0.0.1/stream.mp3",
        "http://172.16.0.1/stream.mp3",
        "http://192.168.1.5/stream.mp3",
        "http://169.254.1.1/stream.mp3",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://93.184.216.34/stream.mp3",
        "file:///etc/passwd",
        "http://internal/stream.mp3",
        "http://music.local/stream.mp3",
    ],
)
def test_safe_url_validation_blocks_non_public_targets(url):
    from app.utils.media_sources import validate_safe_url

    assert validate_safe_url(url) is None


def test_safe_url_validation_allows_public_https_literal():
    from app.utils.media_sources import validate_safe_url

    assert validate_safe_url("https://93.184.216.34/stream.mp3") == "https://93.184.216.34/stream.mp3"


def test_safe_url_validation_rejects_embedded_credentials():
    from app.utils.media_sources import validate_safe_url

    assert validate_safe_url("https://user:secret@93.184.216.34/stream.mp3") is None


def test_safe_url_validation_checks_dns_targets():
    from app.utils.media_sources import validate_safe_url

    assert validate_safe_url(
        "https://media.example.com/stream.mp3",
        resolver=lambda host, port: ["93.184.216.34"],
    ) == "https://media.example.com/stream.mp3"
    assert validate_safe_url(
        "https://media.example.com/stream.mp3",
        resolver=lambda host, port: ["10.0.0.5"],
    ) is None


@pytest.mark.asyncio
async def test_safe_url_validation_blocks_redirect_to_private_target():
    from app.utils.media_sources import validate_safe_url_with_redirects

    async def redirect_to_private(url: str) -> str | None:
        return "http://127.0.0.1/internal.mp3"

    assert await validate_safe_url_with_redirects(
        "https://93.184.216.34/redirect",
        redirect_fetcher=redirect_to_private,
    ) is None


@pytest.mark.asyncio
async def test_safe_url_validation_allows_public_redirect_chain():
    from app.utils.media_sources import validate_safe_url_with_redirects

    redirects = iter(["https://93.184.216.34/final.mp3", None])

    async def public_redirect(url: str) -> str | None:
        return next(redirects)

    assert await validate_safe_url_with_redirects(
        "https://93.184.216.34/redirect",
        redirect_fetcher=public_redirect,
    ) == "https://93.184.216.34/final.mp3"


@pytest.mark.asyncio
async def test_redirect_fetch_resolver_blocks_private_dns_target(monkeypatch):
    from app.utils import media_sources

    monkeypatch.setattr(media_sources, "_resolve_hostname", lambda host, port: ["10.0.0.5"])
    resolver = media_sources._SafeAiohttpResolver()

    with pytest.raises(OSError):
        await resolver.resolve("media.example.com", 443)


def test_trusted_local_media_path_rejects_symlink_escape(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.utils.media_sources import is_trusted_local_media_path

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))

    outside = tmp_path / "outside.opus"
    outside.write_bytes(b"outside")
    link = downloads / "link.opus"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is not available: {exc}")

    assert not is_trusted_local_media_path(link)


@pytest.mark.asyncio
async def test_download_trusted_telegram_media_saves_inside_download_root(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.utils.media_sources import download_trusted_telegram_media

    downloads = tmp_path / "downloads"
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))

    async def fake_download(media, *, file_name):
        path = Path(file_name) / "voice.ogg"
        path.write_bytes(b"voice")
        return str(path)

    client = SimpleNamespace(download_media=AsyncMock(side_effect=fake_download))

    path = await download_trusted_telegram_media(client, object(), 12345)

    expected = downloads / "12345" / "voice.ogg"
    assert path == str(expected.resolve())
    client.download_media.assert_awaited_once()
    assert client.download_media.await_args.kwargs["file_name"].endswith(os.sep)


@pytest.mark.asyncio
async def test_join_voice_chat_rejects_untrusted_local_source(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.call_service import CallService

    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(downloads))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "media_cache"))

    outside = tmp_path / "outside.opus"
    outside.write_bytes(b"outside")
    call_py = SimpleNamespace(join_group_call=AsyncMock())

    with patch("app.services.call_service._ensure_helper_in_chat", new_callable=AsyncMock) as ensure_helper:
        ok = await CallService.join_voice_chat(call_py, -100123, str(outside), "audio")

    assert ok is False
    ensure_helper.assert_not_awaited()
    call_py.join_group_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_join_voice_chat_rejects_private_url_source():
    from app.services.call_service import CallService

    call_py = SimpleNamespace(join_group_call=AsyncMock())

    with patch("app.services.call_service._ensure_helper_in_chat", new_callable=AsyncMock) as ensure_helper:
        ok = await CallService.join_voice_chat(call_py, -100123, "http://127.0.0.1/stream.mp3", "audio")

    assert ok is False
    ensure_helper.assert_not_awaited()
    call_py.join_group_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_service_blocks_private_download_before_ytdlp():
    from app.services import MediaService

    with patch("app.services.media_service._run_subprocess") as run:
        path = await MediaService.download_audio("http://127.0.0.1/stream.mp3", -100123)

    assert path is None
    run.assert_not_called()


@pytest.mark.asyncio
async def test_media_service_blocks_private_stream_resolution_before_ytdlp():
    from app.services import MediaService

    with patch("app.services.media_service._get_output") as get_output:
        stream_url = await MediaService.get_stream_url("http://127.0.0.1/stream.mp3")

    assert stream_url is None
    get_output.assert_not_called()


@pytest.mark.asyncio
async def test_pre_transcode_blocks_private_url_before_ffmpeg():
    from app.services.transcode_pool import pre_transcode

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as create_proc:
        result = await pre_transcode("http://127.0.0.1/stream.mp3", "audio")

    assert result == ""
    create_proc.assert_not_called()


@pytest.mark.asyncio
async def test_playlist_prefetch_blocks_private_url_before_download():
    from app.services.call_service import _prefetch_cache, _prefetch_next

    item0 = SimpleNamespace(stream_url=None, file_path=None, media_type="audio", title="Current")
    item1 = SimpleNamespace(stream_url="http://127.0.0.1/stream.mp3", file_path=None, media_type="audio", title="Next")

    with (
        patch("app.services.call_service.playlist_repo") as mock_pr,
        patch("app.services.media_service.MediaService.download_audio", new_callable=AsyncMock) as download_audio,
        patch("app.services.transcode_pool.pre_transcode", new_callable=AsyncMock) as pre_transcode,
    ):
        mock_pr.get_queue = AsyncMock(return_value=[item0, item1])
        await _prefetch_next(-100124)

    assert -100124 not in _prefetch_cache
    download_audio.assert_not_awaited()
    pre_transcode.assert_not_awaited()
