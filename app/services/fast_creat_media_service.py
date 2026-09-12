"""Fast-Creat media resolution without exposing provider tokens to handlers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp

from app.repositories import fast_creat_token_repo
from app.services import fast_creat_token_service
from app.utils.media_sources import _SafeAiohttpResolver, validate_safe_url_with_redirects


logger = logging.getLogger(__name__)

MAX_FAST_CREAT_MEDIA_ITEMS = 10
_FAST_CREAT_ENDPOINTS = {
    "instagram": "https://api.fast-creat.ir/instagram",
    "tiktok": "https://api.fast-creat.ir/tiktok",
    "spotify": "https://api.fast-creat.ir/spotify",
}
_MEDIA_URL_KEYS = (
    "download_url", "video_url", "image_url", "audio_url", "music_url",
    "play", "wmplay", "url", "link", "src", "download", "file", "video", "image", "audio", "music",
)
_MEDIA_CONTAINER_KEYS = frozenset({
    "media", "medias", "items", "files", "downloads", "download", "images",
    "image_post_info", "result", "data", "list",
})


class FastCreatMediaError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FastCreatSource:
    provider: str
    url: str
    instagram_type: str | None = None


@dataclass(frozen=True)
class FastCreatMediaItem:
    media_type: str
    url: str


@dataclass(frozen=True)
class FastCreatResolution:
    provider: str
    source_url: str
    items: tuple[FastCreatMediaItem, ...]
    title: str | None = None
    youtube_fallback_query: str | None = None


def is_fast_creat_candidate(url: str) -> bool:
    host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
    return (
        host == "instagram.com" or host.endswith(".instagram.com")
        or host == "tiktok.com" or host.endswith(".tiktok.com")
        or host == "open.spotify.com" or host == "spotify.link"
    )


def classify_fast_creat_source(url: str) -> FastCreatSource | None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    segments = [segment.casefold() for segment in unquote(parsed.path).split("/") if segment]
    if host == "instagram.com" or host.endswith(".instagram.com"):
        if not segments:
            return None
        first = segments[0]
        if first in {"p", "reel", "reels", "tv"} and len(segments) >= 2:
            return FastCreatSource("instagram", url, instagram_type="post")
        if first == "stories" and len(segments) >= 3:
            kind = "highlight" if "highlights" in segments else "story"
            return FastCreatSource("instagram", url, instagram_type=kind)
        return None
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        if any(segment in {"video", "photo"} for segment in segments):
            return FastCreatSource("tiktok", url)
        return None
    if host == "open.spotify.com" and len(segments) >= 2 and segments[0] == "track":
        return FastCreatSource("spotify", url)
    return None


async def resolve_fast_creat_media(source_url: str) -> FastCreatResolution:
    safe_url = await validate_safe_url_with_redirects(source_url)
    if safe_url is None:
        raise FastCreatMediaError("blocked_url")
    source = classify_fast_creat_source(safe_url)
    if source is None:
        raise FastCreatMediaError("unsupported_link")

    if source.provider == "instagram":
        request_type = "post2" if source.instagram_type == "post" else (source.instagram_type or "post2")
        payload = await _request_provider(
            "instagram",
            {"type": request_type, "url": source.url},
        )
        items = _normalize_media(payload, provider="instagram", source_url=source.url)
        if not items and source.instagram_type == "post":
            payload = await _request_provider("instagram", {"type": "post", "url": source.url})
            items = _normalize_media(payload, provider="instagram", source_url=source.url)
        if not items:
            raise FastCreatMediaError("no_media")
        return FastCreatResolution(
            provider="instagram",
            source_url=source.url,
            items=items,
            title=_extract_title(payload),
        )

    if source.provider == "tiktok":
        payload = await _request_provider("tiktok", {"url": source.url})
        items = _normalize_media(payload, provider="tiktok", source_url=source.url)
        if not items:
            raise FastCreatMediaError("no_media")
        return FastCreatResolution(
            provider="tiktok",
            source_url=source.url,
            items=items,
            title=_extract_title(payload),
        )

    try:
        payload = await _request_provider("spotify", {"action": "dl", "url": source.url})
    except FastCreatMediaError as exc:
        if exc.code not in {
            "provider_rejected",
            "provider_http_error",
            "provider_invalid_response",
            "provider_unavailable",
        }:
            raise
        payload = {}
    items = _normalize_media(payload, provider="spotify", source_url=source.url)
    if items:
        return FastCreatResolution(
            provider="spotify",
            source_url=source.url,
            items=items,
            title=_extract_title(payload),
        )
    title = await _spotify_oembed_title(source.url)
    if not title:
        raise FastCreatMediaError("spotify_metadata_unavailable")
    return FastCreatResolution(
        provider="spotify",
        source_url=source.url,
        items=(),
        title=title,
        youtube_fallback_query=title,
    )


async def resolve_spotify_fallback_query(source_url: str) -> str | None:
    """Resolve a validated single-track Spotify URL to a YouTube search query."""
    safe_url = await validate_safe_url_with_redirects(source_url)
    if safe_url is None:
        return None
    source = classify_fast_creat_source(safe_url)
    if source is None or source.provider != "spotify":
        return None
    return await _spotify_oembed_title(source.url)


async def _request_provider(provider: str, params: dict[str, str]) -> dict[str, Any]:
    excluded: set[int] = set()
    saw_rate_limit = False
    while True:
        try:
            token = await fast_creat_token_service.reserve_token(provider, exclude_ids=excluded)
        except fast_creat_token_service.FastCreatTokensUnavailable as exc:
            raise FastCreatMediaError("tokens_unavailable") from exc
        if token is None:
            raise FastCreatMediaError("rate_limited" if saw_rate_limit else "no_active_token")
        excluded.add(token.id)
        try:
            payload = await _call_provider(provider, token.value, params)
        except FastCreatMediaError as exc:
            if exc.code == "invalid_token":
                await fast_creat_token_repo.mark_invalid(token.id, code="invalid_token")
                continue
            if exc.code == "rate_limited":
                saw_rate_limit = True
                await fast_creat_token_repo.mark_rate_limited(token.id)
                continue
            await fast_creat_token_repo.mark_failure(token.id, code=exc.code)
            raise
        await fast_creat_token_repo.mark_success(token.id)
        return payload


async def _call_provider(provider: str, token: str, params: dict[str, str]) -> dict[str, Any]:
    request_params = {"apikey": token, **params}
    connector = aiohttp.TCPConnector(resolver=_SafeAiohttpResolver(), ttl_dns_cache=0)
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
        ) as session:
            async with session.get(
                _FAST_CREAT_ENDPOINTS[provider],
                params=request_params,
                allow_redirects=False,
                headers={"User-Agent": "tg-vc-player-saas/fast-creat"},
            ) as response:
                if response.status == 401:
                    raise FastCreatMediaError("invalid_token")
                if response.status == 429:
                    raise FastCreatMediaError("rate_limited")
                if response.status != 200:
                    raise FastCreatMediaError("provider_http_error")
                try:
                    payload = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise FastCreatMediaError("provider_invalid_response") from exc
    except FastCreatMediaError:
        raise
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise FastCreatMediaError("provider_unavailable") from exc

    if not isinstance(payload, dict):
        raise FastCreatMediaError("provider_invalid_response")
    rejection_code = _provider_rejection_code(payload)
    if rejection_code is not None:
        raise FastCreatMediaError(rejection_code)
    return payload


def _provider_payload_failed(payload: dict[str, Any]) -> bool:
    if payload.get("ok") is False or payload.get("success") is False:
        return True
    status = payload.get("status")
    if status is False:
        return True
    return isinstance(status, str) and status.strip().casefold() in {"error", "failed", "failure"}


def _provider_rejection_code(payload: dict[str, Any]) -> str | None:
    if not _provider_payload_failed(payload):
        return None
    code = str(payload.get("code") or payload.get("status_code") or "").strip()
    message = str(
        payload.get("message") or payload.get("error") or payload.get("result") or ""
    ).casefold()
    if code == "401" or "invalid" in message or "نامعتبر" in message:
        return "invalid_token"
    if code == "429" or "limit" in message or "rate" in message or "محدود" in message:
        return "rate_limited"
    return "provider_rejected"


async def _spotify_oembed_title(url: str) -> str | None:
    connector = aiohttp.TCPConnector(resolver=_SafeAiohttpResolver(), ttl_dns_cache=0)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15),
            trust_env=False,
        ) as session:
            async with session.get(
                "https://open.spotify.com/oembed",
                params={"url": url},
                allow_redirects=False,
                headers={"User-Agent": "tg-vc-player-saas/spotify-metadata"},
            ) as response:
                if response.status != 200:
                    return None
                payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    title = payload.get("title") if isinstance(payload, dict) else None
    return str(title).strip()[:512] if isinstance(title, str) and title.strip() else None


def _normalize_media(
    payload: dict[str, Any],
    *,
    provider: str,
    source_url: str,
) -> tuple[FastCreatMediaItem, ...]:
    candidates: list[FastCreatMediaItem] = []
    seen_urls: set[str] = set()
    stack: list[tuple[Any, str]] = [
        (payload.get("result"), "result"),
        (payload.get("data"), "data"),
        (payload.get("media"), "media"),
        (payload, "payload"),
    ]
    visited: set[int] = set()
    while stack and len(candidates) < MAX_FAST_CREAT_MEDIA_ITEMS:
        value, context_key = stack.pop()
        if isinstance(value, (dict, list)):
            marker = id(value)
            if marker in visited:
                continue
            visited.add(marker)
        if isinstance(value, list):
            nested_values: list[tuple[Any, str]] = []
            for nested in value:
                if (
                    provider == "tiktok"
                    and isinstance(nested, str)
                    and context_key in {"images", "image_post_info"}
                    and nested != source_url
                    and nested.startswith(("https://", "http://"))
                    and nested not in seen_urls
                ):
                    # TikTok photo posts may expose ``images`` as a plain
                    # string list instead of objects with a URL field.
                    seen_urls.add(nested)
                    candidates.append(FastCreatMediaItem(media_type="photo", url=nested))
                    if len(candidates) >= MAX_FAST_CREAT_MEDIA_ITEMS:
                        break
                else:
                    nested_values.append((nested, context_key))
            stack.extend(reversed(nested_values))
            continue
        if not isinstance(value, dict):
            continue

        for key in _MEDIA_URL_KEYS:
            raw_url = value.get(key)
            if (
                isinstance(raw_url, str)
                and raw_url != source_url
                and raw_url.startswith(("https://", "http://"))
            ):
                if raw_url not in seen_urls:
                    media_type = _media_type_for_url(
                        value,
                        key=key,
                        url=raw_url,
                        provider=provider,
                    )
                    if provider != "spotify" and media_type == "audio":
                        continue
                    seen_urls.add(raw_url)
                    candidates.append(FastCreatMediaItem(media_type=media_type, url=raw_url))
                    if len(candidates) >= MAX_FAST_CREAT_MEDIA_ITEMS:
                        break
        for key, nested in value.items():
            if key.casefold() in _MEDIA_CONTAINER_KEYS and isinstance(nested, (dict, list)):
                stack.append((nested, key.casefold()))
    return tuple(candidates)


def _media_type_for_url(
    value: dict[str, Any],
    *,
    key: str,
    url: str,
    provider: str,
) -> str:
    hint = str(value.get("media_type") or value.get("type") or value.get("kind") or "").lower()
    key = key.casefold()
    url_path = urlparse(url).path.casefold()
    if provider == "spotify":
        return "audio"
    if (
        key in {"audio_url", "music_url", "audio", "music"}
        or hint in {"audio", "music", "song"}
        or any(ext in url_path for ext in (".mp3", ".m4a", ".opus", ".ogg", ".wav"))
    ):
        return "audio"
    if (
        key in {"image_url", "image"}
        or hint in {"photo", "image", "images"}
        or any(ext in url_path for ext in (".jpg", ".jpeg", ".png", ".webp"))
    ):
        return "photo"
    return "video"


def _extract_title(payload: dict[str, Any]) -> str | None:
    for container in (payload, payload.get("result"), payload.get("data")):
        if not isinstance(container, dict):
            continue
        for key in ("title", "caption", "name", "track_name"):
            value = container.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:512]
    return None
