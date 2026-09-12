"""P0: Content-addressed media cache with LRU disk guard.

Deduplicates downloads across groups by hashing the canonical URL.
A hard quota (default 10 GB) with LRU eviction to 8 GB prevents disk
exhaustion.  An hourly TTL sweep deletes files untouched for 24 h.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from pathlib import Path

from app.config.settings import INSTANCE_MARKER_FILENAME, settings

logger = logging.getLogger(__name__)

_TRACKING_PARAMS = re.compile(r"[&?](si|list|index|t|feature|pp|ab_channel)=[^&]*")


def _canonical_url(url: str) -> str:
    """Strip tracking / playlist params so the same video always hashes identically."""
    return _TRACKING_PARAMS.sub("", url).strip().rstrip("&?")


def _url_hash(url: str) -> str:
    return hashlib.sha256(_canonical_url(url).encode()).hexdigest()


def _cache_dir() -> Path:
    p = Path(settings.MEDIA_CACHE_PATH)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _shard_path(h: str) -> Path:
    """Two-level sharding: /cache/ab/abcdef...ext"""
    shard = _cache_dir() / h[:2]
    shard.mkdir(parents=True, exist_ok=True)
    return shard


def cache_lookup(url: str, media_type: str) -> str | None:
    """Return cached file path if it exists and is fresh, else None."""
    h = _url_hash(url)
    shard = _shard_path(h)
    for ext in _extensions(media_type):
        candidate = shard / f"{h}{ext}"
        if candidate.is_file():
            _touch(candidate)
            logger.debug("Cache HIT: %s → %s", url[:60], candidate.name)
            return str(candidate)
    return None


def cache_store(url: str, media_type: str, source_path: str) -> str:
    """Move a downloaded file into the cache. Returns the cache path."""
    h = _url_hash(url)
    shard = _shard_path(h)
    src = Path(source_path)
    dest = shard / f"{h}{src.suffix}"
    if dest.exists():
        _touch(dest)
        return str(dest)
    try:
        os.link(str(src), str(dest))
    except OSError:
        import shutil
        shutil.copy2(str(src), str(dest))
    logger.debug("Cache STORE: %s → %s", url[:60], dest.name)
    return str(dest)


def _extensions(media_type: str) -> list[str]:
    if media_type == "video":
        return [".mp4", ".mkv", ".webm"]
    if media_type == "photo":
        return [".jpg", ".jpeg", ".png", ".webp"]
    return [".opus", ".ogg", ".mp3", ".m4a", ".wav"]


def _touch(path: Path) -> None:
    """Update atime so LRU eviction sees it as recently used."""
    try:
        os.utime(str(path), None)
    except OSError:
        pass


# ── Disk guard: LRU eviction ────────────────────────────────────────────

def get_cache_size_bytes() -> int:
    total = 0
    for f in _cache_dir().rglob("*"):
        if f.is_file() and f.name != INSTANCE_MARKER_FILENAME:
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def evict_lru(max_bytes: int | None = None, target_bytes: int | None = None) -> int:
    """Delete least-recently-accessed files until cache is under target_bytes.

    Returns number of files removed.
    """
    if max_bytes is None:
        max_bytes = settings.MEDIA_CACHE_MAX_GB * 1024 * 1024 * 1024
    if target_bytes is None:
        target_bytes = settings.MEDIA_CACHE_TARGET_GB * 1024 * 1024 * 1024

    current = get_cache_size_bytes()
    if current <= max_bytes:
        return 0

    files: list[tuple[float, int, Path]] = []
    for f in _cache_dir().rglob("*"):
        if f.is_file() and f.name != INSTANCE_MARKER_FILENAME:
            try:
                st = f.stat()
                files.append((st.st_atime, st.st_size, f))
            except OSError:
                pass

    files.sort(key=lambda x: x[0])

    removed = 0
    for atime, size, path in files:
        if current <= target_bytes:
            break
        try:
            path.unlink()
            current -= size
            removed += 1
        except OSError:
            pass

    _cleanup_empty_shards()
    logger.info("LRU eviction: removed %d files, cache now %.1f GB",
                removed, current / 1024 / 1024 / 1024)
    return removed


def evict_stale(max_age_hours: int | None = None) -> int:
    """Delete files not accessed within max_age_hours. Returns count removed."""
    if max_age_hours is None:
        max_age_hours = settings.MEDIA_CACHE_MAX_AGE_HOURS
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for f in _cache_dir().rglob("*"):
        if f.is_file() and f.name != INSTANCE_MARKER_FILENAME:
            try:
                if f.stat().st_atime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    _cleanup_empty_shards()
    if removed:
        logger.info("TTL eviction: removed %d stale files (>%dh)", removed, max_age_hours)
    return removed


def _cleanup_empty_shards() -> None:
    for d in sorted(_cache_dir().rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass
