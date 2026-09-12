"""Safe URL/source fingerprinting and redaction for media event tracking."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.services.media_cache import _canonical_url
from app.utils.media_sources import ensure_trusted_local_media_path, is_http_url, trusted_media_roots


@dataclass(frozen=True)
class MediaSourceFields:
    """Safe storage fields derived from a media source."""

    url_fingerprint: str
    redacted_url: str
    host: str | None
    source_kind: str


def _trusted_relative_path(path: Path) -> str | None:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return None
    for root in trusted_media_roots():
        try:
            return str(resolved.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
    return None


def _sha256_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_media_source_fields(
    source: str | None,
    *,
    source_kind_hint: str | None = None,
) -> MediaSourceFields | None:
    """Build deterministic fingerprint and redacted display fields for a source.

    Args:
        source: Raw media source (URL or local path).
        source_kind_hint: Optional hint such as ``telegram`` when no URL exists.

    Returns:
        MediaSourceFields or None when the source is empty and cannot be classified.
    """
    if source is None or not str(source).strip():
        if source_kind_hint == "telegram":
            canonical = "telegram:media"
            return MediaSourceFields(
                url_fingerprint=_sha256_fingerprint(canonical),
                redacted_url="[telegram media]",
                host=None,
                source_kind="telegram",
            )
        return None

    raw = str(source).strip()
    if is_http_url(raw):
        canonical = _canonical_url(raw)
        parsed = urlparse(canonical)
        scheme = parsed.scheme or "https"
        host = parsed.netloc or None
        path = parsed.path or "/"
        redacted = f"{scheme}://{host}{path}" if host else path
        return MediaSourceFields(
            url_fingerprint=_sha256_fingerprint(canonical),
            redacted_url=redacted[:512],
            host=host[:255] if host else None,
            source_kind="url",
        )

    if source_kind_hint == "telegram":
        canonical = f"telegram:{raw[:120]}"
        return MediaSourceFields(
            url_fingerprint=_sha256_fingerprint(canonical),
            redacted_url="[telegram media]",
            host=None,
            source_kind="telegram",
        )

    trusted = ensure_trusted_local_media_path(raw)
    if trusted is not None:
        relative = _trusted_relative_path(trusted) or trusted.name
        canonical = f"local:{relative}"
        return MediaSourceFields(
            url_fingerprint=_sha256_fingerprint(canonical),
            redacted_url=relative[:512],
            host=None,
            source_kind="local",
        )

    return MediaSourceFields(
        url_fingerprint=_sha256_fingerprint(f"unknown:{raw[:120]}"),
        redacted_url="[redacted]",
        host=None,
        source_kind="unknown",
    )


def build_telegram_media_fields(file_unique_id: str, media_type: str) -> MediaSourceFields:
    """Build safe fields for a Telegram-hosted media file."""
    canonical = f"telegram:{media_type}:{file_unique_id}"
    label = f"[telegram {media_type}]"
    return MediaSourceFields(
        url_fingerprint=_sha256_fingerprint(canonical),
        redacted_url=label,
        host=None,
        source_kind="telegram",
    )
