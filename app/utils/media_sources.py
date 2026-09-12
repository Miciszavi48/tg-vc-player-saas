from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import os
import socket
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import unquote, urljoin, urlparse

import aiohttp
from loguru import logger

from app.config.settings import settings

_media_cleanup_logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = {"localhost"}
_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".test",
    ".invalid",
)
_METADATA_IPS = {ipaddress.ip_address("169.254.169.254")}
_SOUNDCLOUD_COLLECTION_SEGMENTS = frozenset(
    {
        "albums",
        "discover",
        "likes",
        "playlists",
        "popular-tracks",
        "reposts",
        "search",
        "sets",
        "stations",
        "stream",
        "tracks",
        "you",
    }
)
_Resolver = Callable[[str, int | None], list[str]]
_RedirectFetcher = Callable[[str], Awaitable[str | None]]


class _RedirectCheckFailed(Exception):
    pass


class _SafeAiohttpResolver(aiohttp.abc.AbstractResolver):
    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_INET,
    ) -> list[dict[str, object]]:
        addresses = await asyncio.to_thread(_resolve_hostname, host, port)
        results: list[dict[str, object]] = []
        for address in addresses:
            ip = ipaddress.ip_address(str(address).strip("[]"))
            if not _is_public_ip(ip):
                raise OSError("blocked non-public DNS target")
            results.append({
                "hostname": host,
                "host": str(ip),
                "port": port,
                "family": socket.AF_INET6 if ip.version == 6 else socket.AF_INET,
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            })
        if not results:
            raise OSError("empty DNS result")
        return results

    async def close(self) -> None:
        return None


def is_http_url(source: str | None) -> bool:
    if not source:
        return False
    parsed = urlparse(str(source).strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def is_soundcloud_url(url: str) -> bool:
    """Return whether *url* targets a SoundCloud-owned host."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "soundcloud.com" or host.endswith(".soundcloud.com")


def is_soundcloud_track_url(url: str) -> bool:
    """Accept only canonical SoundCloud artist/track URLs after redirects."""
    if not is_soundcloud_url(url):
        return False
    segments = [
        segment.casefold()
        for segment in unquote(urlparse(url).path).split("/")
        if segment
    ]
    return (
        len(segments) == 2
        and not any(segment in _SOUNDCLOUD_COLLECTION_SEGMENTS for segment in segments)
    )


def validate_safe_url(
    source: str | None,
    *,
    resolver: _Resolver | None = None,
) -> str | None:
    """Return a safe HTTP(S) URL or None when the URL could reach internal hosts."""
    if not source:
        return None
    url = str(source).strip()
    if not url or any(ch.isspace() for ch in url):
        return None

    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None

    hostname = parsed.hostname
    if not hostname or not _is_safe_hostname(hostname):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None

    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        if not _hostname_resolves_publicly(hostname, port, resolver):
            return None
    else:
        if not _is_public_ip(ip):
            return None

    return url


def is_safe_url(source: str | None) -> bool:
    return validate_safe_url(source) is not None


async def validate_safe_url_with_redirects(
    source: str | None,
    *,
    max_redirects: int = 5,
    resolver: _Resolver | None = None,
    redirect_fetcher: _RedirectFetcher | None = None,
) -> str | None:
    """Validate a URL and each observed HTTP redirect target."""
    current = await asyncio.to_thread(validate_safe_url, source, resolver=resolver)
    if current is None:
        return None

    for _ in range(max_redirects):
        try:
            location = (
                await redirect_fetcher(current)
                if redirect_fetcher is not None
                else await _fetch_redirect_location(current)
            )
        except _RedirectCheckFailed:
            return None
        if not location:
            return current

        next_url = urljoin(current, location)
        current = await asyncio.to_thread(
            validate_safe_url,
            next_url,
            resolver=resolver,
        )
        if current is None:
            return None

    return None


def _is_safe_hostname(hostname: str) -> bool:
    host = hostname.strip().strip("[]").rstrip(".").lower()
    if not host or host in _BLOCKED_HOSTNAMES:
        return False
    if any(host.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        return False
    if "." not in host:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
    return True


def _hostname_resolves_publicly(
    hostname: str,
    port: int | None,
    resolver: _Resolver | None,
) -> bool:
    try:
        addresses = (
            resolver(hostname, port)
            if resolver is not None
            else _resolve_hostname(hostname, port)
        )
    except (OSError, socket.gaierror, ValueError):
        return False
    if not addresses:
        return False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(str(address).strip("[]"))
        except ValueError:
            return False
        if not _is_public_ip(ip):
            return False
    return True


def _resolve_hostname(hostname: str, port: int | None) -> list[str]:
    service_port = port or 443
    infos = socket.getaddrinfo(hostname, service_port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip in _METADATA_IPS:
        return False
    if not ip.is_global:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


async def _fetch_redirect_location(url: str) -> str | None:
    timeout = aiohttp.ClientTimeout(total=5)
    headers = {"User-Agent": "tg-vc-player-saas/ssrf-check"}
    connector = aiohttp.TCPConnector(resolver=_SafeAiohttpResolver(), ttl_dns_cache=0)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=False,
        ) as session:
            try:
                async with session.head(url, allow_redirects=False, headers=headers) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        return response.headers.get("Location")
                    if response.status != 405:
                        return None
            except Exception:
                pass
            async with session.get(url, allow_redirects=False, headers=headers) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    return response.headers.get("Location")
    except Exception as exc:
        raise _RedirectCheckFailed from exc
    return None


def trusted_media_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    for raw in (settings.DOWNLOADS_PATH, settings.MEDIA_CACHE_PATH):
        if raw:
            roots.append(Path(raw).expanduser().resolve(strict=False))
    return tuple(roots)


def trusted_download_dir(chat_id: int) -> Path:
    download_dir = (
        Path(settings.DOWNLOADS_PATH)
        .expanduser()
        .resolve(strict=False)
        / str(chat_id)
    )
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


async def download_trusted_telegram_media(client, media, chat_id: int) -> str | None:
    download_dir = trusted_download_dir(chat_id)
    path = await client.download_media(media, file_name=str(download_dir) + os.sep)
    return normalize_media_source(path)


def safe_unlink_temp_media(path: str | Path, *, reason: str) -> None:
    """Delete a trusted local media file used only for temporary playback.

    Args:
        path: Local filesystem path under DOWNLOADS_PATH or MEDIA_CACHE_PATH.
        reason: Short cleanup reason for logs (not shown to users).
    """
    trusted = ensure_trusted_local_media_path(path)
    if trusted is None:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            trusted.unlink(missing_ok=True)
        _media_cleanup_logger.info(
            "Removed temporary playback file (%s): %s",
            reason,
            trusted.name,
        )
    except OSError as exc:
        _media_cleanup_logger.warning(
            "Failed to remove temporary playback file (%s): %s — %s",
            reason,
            trusted.name,
            type(exc).__name__,
        )


def cleanup_temp_playback_local_source(
    source: str | Path | None,
    *,
    reason: str,
) -> None:
    """Remove a one-shot playback download and any transcode siblings."""
    normalized = normalize_media_source(str(source) if source else None)
    if not normalized or is_http_url(normalized):
        return
    from app.services.transcode_pool import cleanup_transcoded

    cleanup_transcoded(normalized)
    safe_unlink_temp_media(normalized, reason=reason)


def normalize_media_source(source: str | None) -> str | None:
    if not source:
        return None
    source = str(source).strip()
    if is_http_url(source):
        return validate_safe_url(source)
    trusted_path = ensure_trusted_local_media_path(source)
    return str(trusted_path) if trusted_path is not None else None


async def resolve_media_source_for_playback(
    source: str | None,
    *,
    chat_id: int | None = None,
) -> str | None:
    """Validate a playback source (queue, prefetch, repeat) with redirect-aware SSRF checks."""
    if not source:
        return None
    raw = str(source).strip()
    if not raw:
        return None
    if is_http_url(raw):
        safe = await validate_safe_url_with_redirects(raw)
        if safe is None:
            if chat_id is not None:
                logger.warning("Rejected unsafe HTTP media source for chat {}", chat_id)
            else:
                logger.warning("Rejected unsafe HTTP media source")
        return safe
    trusted = normalize_media_source(raw)
    if trusted is None and chat_id is not None:
        logger.warning("Rejected unsafe local media source for chat {}", chat_id)
    return trusted


def is_trusted_local_media_path(source: str | Path | None) -> bool:
    return ensure_trusted_local_media_path(source) is not None


def ensure_trusted_local_media_path(source: str | Path | None) -> Path | None:
    if not source:
        return None
    try:
        path = Path(source).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not path.is_file():
        return None
    return path if _is_under_trusted_root(path) else None


def _is_under_trusted_root(path: Path) -> bool:
    for root in trusted_media_roots():
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    return False
