"""Fast-Creat request, normalization, and Spotify fallback tests."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.services.fast_creat_media_service import (
    FastCreatMediaError,
    FastCreatMediaItem,
    _request_provider,
    _provider_rejection_code,
    classify_fast_creat_source,
    resolve_fast_creat_media,
)
from app.services.fast_creat_token_service import ReservedFastCreatToken


def test_classification_accepts_individual_media_only():
    assert classify_fast_creat_source("https://www.instagram.com/reel/abc/").provider == "instagram"
    assert classify_fast_creat_source("https://www.tiktok.com/@user/video/123").provider == "tiktok"
    assert classify_fast_creat_source("https://open.spotify.com/track/abc").provider == "spotify"
    assert classify_fast_creat_source("https://open.spotify.com/album/abc") is None
    assert classify_fast_creat_source("https://www.instagram.com/example/") is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": "error", "code": "401", "message": "bad key"}, "invalid_token"),
        ({"success": False, "code": "429"}, "rate_limited"),
        ({"ok": False, "message": "rejected"}, "provider_rejected"),
        ({"status": "success", "result": {}}, None),
    ],
)
def test_provider_application_errors_are_classified(payload, expected):
    assert _provider_rejection_code(payload) == expected


@pytest.mark.asyncio
async def test_instagram_post_uses_post2_then_normalizes_media():
    source = "https://www.instagram.com/reel/abc/"
    payload = {"ok": True, "result": {"caption": "clip", "media": [{"type": "video", "url": "https://cdn.example/clip.mp4"}]}}
    with (
        patch("app.services.fast_creat_media_service.validate_safe_url_with_redirects", AsyncMock(return_value=source)),
        patch("app.services.fast_creat_media_service._request_provider", AsyncMock(return_value=payload)) as request,
    ):
        resolution = await resolve_fast_creat_media(source)

    assert resolution.provider == "instagram"
    assert resolution.title == "clip"
    assert resolution.items == (FastCreatMediaItem("video", "https://cdn.example/clip.mp4"),)
    assert request.await_args.args == ("instagram", {"type": "post2", "url": source})


@pytest.mark.asyncio
async def test_spotify_empty_provider_response_uses_oembed_title_for_youtube_fallback():
    source = "https://open.spotify.com/track/123"
    with (
        patch("app.services.fast_creat_media_service.validate_safe_url_with_redirects", AsyncMock(return_value=source)),
        patch("app.services.fast_creat_media_service._request_provider", AsyncMock(return_value={"ok": True, "result": None})) as request,
        patch("app.services.fast_creat_media_service._spotify_oembed_title", AsyncMock(return_value="Artist - Track")),
    ):
        resolution = await resolve_fast_creat_media(source)

    assert request.await_args.args == ("spotify", {"action": "dl", "url": source})
    assert resolution.items == ()
    assert resolution.youtube_fallback_query == "Artist - Track"


@pytest.mark.asyncio
async def test_spotify_rejected_provider_response_uses_oembed_youtube_fallback():
    source = "https://open.spotify.com/track/rejected"
    with (
        patch(
            "app.services.fast_creat_media_service.validate_safe_url_with_redirects",
            AsyncMock(return_value=source),
        ),
        patch(
            "app.services.fast_creat_media_service._request_provider",
            AsyncMock(side_effect=FastCreatMediaError("provider_rejected")),
        ),
        patch(
            "app.services.fast_creat_media_service._spotify_oembed_title",
            AsyncMock(return_value="Artist - Track"),
        ),
    ):
        resolution = await resolve_fast_creat_media(source)

    assert resolution.items == ()
    assert resolution.youtube_fallback_query == "Artist - Track"


@pytest.mark.asyncio
async def test_rate_limited_token_is_cooled_then_next_token_is_used():
    first = ReservedFastCreatToken(id=1, provider="tiktok", value="first")
    second = ReservedFastCreatToken(id=2, provider="tiktok", value="second")
    with (
        patch(
            "app.services.fast_creat_media_service.fast_creat_token_service.reserve_token",
            AsyncMock(side_effect=[first, second]),
        ),
        patch(
            "app.services.fast_creat_media_service._call_provider",
            AsyncMock(side_effect=[FastCreatMediaError("rate_limited"), {"ok": True, "result": {}}]),
        ),
        patch("app.services.fast_creat_media_service.fast_creat_token_repo.mark_rate_limited", AsyncMock()) as rate,
        patch("app.services.fast_creat_media_service.fast_creat_token_repo.mark_success", AsyncMock()) as success,
    ):
        payload = await _request_provider("tiktok", {"url": "https://www.tiktok.com/@user/video/1"})

    assert payload == {"ok": True, "result": {}}
    rate.assert_awaited_once_with(1)
    success.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_exhausted_rate_limited_pool_reports_cooldown_not_missing_tokens():
    token = ReservedFastCreatToken(id=1, provider="tiktok", value="only")
    with (
        patch(
            "app.services.fast_creat_media_service.fast_creat_token_service.reserve_token",
            AsyncMock(side_effect=[token, None]),
        ),
        patch(
            "app.services.fast_creat_media_service._call_provider",
            AsyncMock(side_effect=FastCreatMediaError("rate_limited")),
        ),
        patch(
            "app.services.fast_creat_media_service.fast_creat_token_repo.mark_rate_limited",
            AsyncMock(),
        ),
    ):
        with pytest.raises(FastCreatMediaError, match="rate_limited"):
            await _request_provider("tiktok", {"url": "https://www.tiktok.com/@user/video/1"})


@pytest.mark.asyncio
async def test_invalid_token_is_disabled_then_next_token_is_used():
    first = ReservedFastCreatToken(id=1, provider="spotify", value="first")
    second = ReservedFastCreatToken(id=2, provider="spotify", value="second")
    with (
        patch(
            "app.services.fast_creat_media_service.fast_creat_token_service.reserve_token",
            AsyncMock(side_effect=[first, second]),
        ),
        patch(
            "app.services.fast_creat_media_service._call_provider",
            AsyncMock(side_effect=[FastCreatMediaError("invalid_token"), {"ok": True, "result": {}}]),
        ),
        patch("app.services.fast_creat_media_service.fast_creat_token_repo.mark_invalid", AsyncMock()) as invalid,
        patch("app.services.fast_creat_media_service.fast_creat_token_repo.mark_success", AsyncMock()) as success,
    ):
        payload = await _request_provider("spotify", {"action": "dl", "url": "https://open.spotify.com/track/1"})

    assert payload == {"ok": True, "result": {}}
    invalid.assert_awaited_once_with(1, code="invalid_token")
    success.assert_awaited_once_with(2)


@pytest.mark.asyncio
async def test_media_normalization_ignores_source_url_and_caps_result_at_ten():
    source = "https://www.tiktok.com/@user/photo/1"
    payload = {
        "ok": True,
        "url": source,
        "result": {
            "items": [
                {"type": "photo", "image_url": f"https://cdn.example/{number}.jpg"}
                for number in range(12)
            ]
        },
    }
    with (
        patch("app.services.fast_creat_media_service.validate_safe_url_with_redirects", AsyncMock(return_value=source)),
        patch("app.services.fast_creat_media_service._request_provider", AsyncMock(return_value=payload)),
    ):
        resolution = await resolve_fast_creat_media(source)

    assert len(resolution.items) == 10
    assert all(item.url != source for item in resolution.items)


@pytest.mark.asyncio
async def test_tiktok_documented_video_and_photo_shapes_are_normalized():
    video_source = "https://www.tiktok.com/@user/video/1"
    photo_source = "https://www.tiktok.com/@user/photo/2"
    video_payload = {"ok": True, "result": {"play": "https://cdn.example/video.mp4"}}
    photo_payload = {
        "ok": True,
        "result": {"image_post_info": {"images": ["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"]}},
    }
    with (
        patch(
            "app.services.fast_creat_media_service.validate_safe_url_with_redirects",
            AsyncMock(side_effect=[video_source, photo_source]),
        ),
        patch(
            "app.services.fast_creat_media_service._request_provider",
            AsyncMock(side_effect=[video_payload, photo_payload]),
        ),
    ):
        video = await resolve_fast_creat_media(video_source)
        photo = await resolve_fast_creat_media(photo_source)

    assert video.items == (FastCreatMediaItem("video", "https://cdn.example/video.mp4"),)
    assert photo.items == (
        FastCreatMediaItem("photo", "https://cdn.example/one.jpg"),
        FastCreatMediaItem("photo", "https://cdn.example/two.jpg"),
    )


@pytest.mark.asyncio
async def test_tiktok_mixed_video_and_music_response_sends_only_video_media():
    source = "https://www.tiktok.com/@user/video/3"
    payload = {
        "ok": True,
        "result": {
            "play": "https://cdn.example/video.mp4",
            "music": "https://cdn.example/soundtrack.mp3",
        },
    }
    with (
        patch(
            "app.services.fast_creat_media_service.validate_safe_url_with_redirects",
            AsyncMock(return_value=source),
        ),
        patch(
            "app.services.fast_creat_media_service._request_provider",
            AsyncMock(return_value=payload),
        ),
    ):
        resolution = await resolve_fast_creat_media(source)

    assert resolution.items == (
        FastCreatMediaItem("video", "https://cdn.example/video.mp4"),
    )


def test_direct_media_rejects_explicit_html_or_json_content_types():
    from app.services.media_service import _direct_content_type_matches

    assert _direct_content_type_matches("audio/mpeg", "audio") is True
    assert _direct_content_type_matches("application/octet-stream", "video") is True
    assert _direct_content_type_matches("text/html; charset=utf-8", "audio") is False
    assert _direct_content_type_matches("application/json", "video") is False


class _DirectResponse:
    status = 200
    headers = {"Content-Type": "video/mp4"}

    def __init__(self, content) -> None:
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _DirectSession:
    def __init__(self, response) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def get(self, *_args, **_kwargs):
        return self.response


class _EmptyContent:
    async def iter_chunked(self, _size):
        if False:
            yield b""


class _CancelledContent:
    async def iter_chunked(self, _size):
        yield b"partial"
        raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_direct_media_cleans_empty_and_cancelled_temporary_files(tmp_path, monkeypatch):
    from app.config.settings import settings
    from app.services.media_service import MediaService

    monkeypatch.setattr(settings, "DOWNLOADS_PATH", str(tmp_path / "downloads"))
    monkeypatch.setattr(settings, "MEDIA_CACHE_PATH", str(tmp_path / "cache"))
    url = "https://cdn.example/video.mp4"

    async def run_with(content):
        session = _DirectSession(_DirectResponse(content))
        with (
            patch(
                "app.services.media_service.validate_safe_url_with_redirects",
                AsyncMock(return_value=url),
            ),
            patch("app.services.media_service.aiohttp.TCPConnector", MagicMock()),
            patch("app.services.media_service.aiohttp.ClientSession", return_value=session),
            patch("app.services.media_cache.cache_lookup", return_value=None),
        ):
            return await MediaService.download_direct_media(
                url,
                -1001,
                media_type="video",
            )

    assert await run_with(_EmptyContent()) is None
    assert not list((tmp_path / "downloads").rglob("fastcreat-*"))

    with pytest.raises(asyncio.CancelledError):
        await run_with(_CancelledContent())
    assert not list((tmp_path / "downloads").rglob("fastcreat-*"))


@pytest.mark.asyncio
async def test_direct_provider_media_blocks_private_or_unsafe_redirect_before_http():
    from app.services.media_service import MediaService

    with patch(
        "app.services.media_service.validate_safe_url_with_redirects",
        AsyncMock(return_value=None),
    ) as validate:
        path = await MediaService.download_direct_media(
            "https://cdn.example/redirect",
            -100123,
            media_type="video",
        )

    assert path is None
    validate.assert_awaited_once_with("https://cdn.example/redirect")
