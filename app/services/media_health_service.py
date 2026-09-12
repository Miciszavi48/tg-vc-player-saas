"""Developer media / webservice health probes (yt-dlp, ffmpeg, cache dirs)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select

from app.config.settings import INSTANCE_MARKER_FILENAME, settings
from app.utils.diagnostic_logging import safe_exc_name, safe_subprocess_error_summary
from app.database.engine import async_session
from app.database.models import PlaybackState, Playlist
from app.services import CallService
from app.services.media_cache import get_cache_size_bytes
from app.services.media_event_service import (
    MediaRankingSummary,
    RankingEntry,
    get_media_ranking_summary,
    ranking_summary_to_json,
)
from app.services.media_service import _get_semaphore
from app.repositories.youtube_session_repo import YoutubeSessionSummary, get_summary as get_youtube_session_summary
from app.utils.cache import get_redis
from app.utils.i18n import t
from app.utils.media_source_fingerprint import build_media_source_fields
from app.utils.media_sources import (
    ensure_trusted_local_media_path,
    is_http_url,
    trusted_media_roots,
)
from app.utils.redis_keys import (
    MEDIA_HEALTH_LAST_CLEANUP,
    MEDIA_HEALTH_LAST_EVICTION,
    MEDIA_HEALTH_YTDLP_PROBE,
    TTL_MEDIA_HEALTH_PROBE,
)

logger = logging.getLogger(__name__)

YTDLP_PROBE_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
_DIR_SCAN_FILE_CAP = 50_000
_VERSION_TIMEOUT_SEC = 10
_PROBE_TIMEOUT_SEC = 15
TOP_FILES_LIMIT = 50
MAX_ACTIVE_STATES = 100
MAX_QUEUE_ROWS = 200
MAX_REPORT_JSON_BYTES = 2 * 1024 * 1024
DOWNLOAD_RETENTION_SECONDS = 7200
_CLEANUP_PREVIEW_SAMPLES = 8
_TRANSCODE_SUFFIXES = (".tc.ogg", ".tc.mp4")
_EJS_MIN_VERSION = (0, 8, 0)
_JAVASCRIPT_RUNTIME_MINIMUMS = (("deno", (2, 3, 0)), ("node", (22, 0, 0)))
_SEMVER_RE = re.compile(r"(?:v|deno\s+)?(\d+)(?:\.(\d+))?(?:\.(\d+))?", re.IGNORECASE)


@dataclass(frozen=True)
class ToolVersion:
    """Result of a CLI version probe."""

    ok: bool
    version: str
    detail: str


@dataclass(frozen=True)
class DirStats:
    """Aggregated filesystem stats under a trusted root."""

    exists: bool
    file_count: int
    total_bytes: int
    truncated: bool


@dataclass(frozen=True)
class YoutubeProbeResult:
    """Cached or fresh YouTube simulate probe via yt-dlp."""

    ok: bool
    cached: bool
    age_seconds: int
    detail: str


@dataclass(frozen=True)
class EvictionSnapshot:
    """Last scheduler media cache eviction run."""

    at: str
    stale_removed: int
    lru_removed: int


@dataclass(frozen=True)
class MediaHealthSnapshot:
    """Full read-only health snapshot for the developer panel."""

    ytdlp: ToolVersion
    ffmpeg: ToolVersion
    cache_dir: DirStats
    cache_size_bytes: int
    downloads_dir: DirStats
    active_calls: int
    download_limit: int
    download_available: int
    youtube_probe: YoutubeProbeResult
    last_eviction: EvictionSnapshot | None
    youtube_sessions: YoutubeSessionSummary | None = None
    ytdlp_ejs: ToolVersion | None = None
    javascript_runtime: ToolVersion | None = None


def _truncate_detail(text: str, limit: int = 200) -> str:
    cleaned = (text or "").strip().replace("\n", " ")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _run_version_cmd(cmd: list[str], timeout_sec: int) -> ToolVersion:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except FileNotFoundError:
        return ToolVersion(ok=False, version="", detail="not found")
    except subprocess.TimeoutExpired:
        return ToolVersion(ok=False, version="", detail="timeout")
    except Exception as exc:
        logger.debug("version probe failed for %s: %s", cmd[0], safe_exc_name(exc))
        return ToolVersion(ok=False, version="", detail=_truncate_detail(str(exc)))

    if result.returncode != 0:
        err = safe_subprocess_error_summary(
            result.returncode,
            result.stderr,
            result.stdout,
        )
        return ToolVersion(ok=False, version="", detail=_truncate_detail(err))

    first_line = (result.stdout or "").strip().splitlines()
    version = first_line[0] if first_line else ""
    return ToolVersion(ok=bool(version), version=version, detail="")


def _parse_semver(value: str) -> tuple[int, int, int] | None:
    match = _SEMVER_RE.search(value or "")
    if match is None:
        return None
    return tuple(int(part or 0) for part in match.groups())


def _get_package_version(package_name: str, minimum: tuple[int, int, int] | None = None) -> ToolVersion:
    try:
        installed = package_version(package_name)
    except PackageNotFoundError:
        return ToolVersion(ok=False, version="", detail="not installed")
    except Exception as exc:
        logger.debug("package version probe failed for %s: %s", package_name, safe_exc_name(exc))
        return ToolVersion(ok=False, version="", detail=safe_exc_name(exc))
    if minimum is not None:
        parsed = _parse_semver(installed)
        if parsed is None or parsed < minimum:
            return ToolVersion(
                ok=False,
                version=installed,
                detail=f"requires >= {'.'.join(str(part) for part in minimum)}",
            )
    return ToolVersion(ok=True, version=installed, detail="")


def _get_javascript_runtime_version() -> ToolVersion:
    """Find a JS runtime yt-dlp can use for YouTube challenge solving."""
    incompatible: list[str] = []
    for executable, minimum in _JAVASCRIPT_RUNTIME_MINIMUMS:
        result = _run_version_cmd([executable, "--version"], _VERSION_TIMEOUT_SEC)
        if result.ok and (parsed := _parse_semver(result.version)) is not None and parsed >= minimum:
            return ToolVersion(ok=True, version=f"{executable} {result.version}", detail="")
        if result.ok:
            incompatible.append(f"{executable} requires >= {'.'.join(str(part) for part in minimum)}")
    detail = "; ".join(incompatible) or "Deno >= 2.3 or Node.js >= 22 not found"
    return ToolVersion(ok=False, version="", detail=detail)


def _scan_dir_stats(root: Path) -> DirStats:
    if not root.exists():
        return DirStats(exists=False, file_count=0, total_bytes=0, truncated=False)

    file_count = 0
    total_bytes = 0
    truncated = False

    try:
        for entry in root.rglob("*"):
            if not entry.is_file() or entry.name == INSTANCE_MARKER_FILENAME:
                continue
            try:
                total_bytes += entry.stat().st_size
            except OSError:
                continue
            file_count += 1
            if file_count >= _DIR_SCAN_FILE_CAP:
                truncated = True
                break
    except OSError as exc:
        logger.debug("dir scan failed for %s: %s", root, safe_exc_name(exc))
        return DirStats(exists=True, file_count=0, total_bytes=0, truncated=False)

    return DirStats(
        exists=True,
        file_count=file_count,
        total_bytes=total_bytes,
        truncated=truncated,
    )


def _run_ytdlp_simulate(url: str) -> ToolVersion:
    cmd = [
        "yt-dlp",
        "--simulate",
        "--no-playlist",
        "--no-warnings",
        "--quiet",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        return ToolVersion(ok=False, version="", detail="yt-dlp not found")
    except subprocess.TimeoutExpired:
        return ToolVersion(ok=False, version="", detail="timeout")
    except Exception as exc:
        return ToolVersion(ok=False, version="", detail=_truncate_detail(str(exc)))

    if result.returncode != 0:
        err = safe_subprocess_error_summary(
            result.returncode,
            result.stderr,
            result.stdout,
        )
        return ToolVersion(ok=False, version="", detail=_truncate_detail(err))
    return ToolVersion(ok=True, version="", detail="")


async def get_ytdlp_version() -> ToolVersion:
    """Return yt-dlp --version result."""
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, _run_version_cmd, ["yt-dlp", "--version"], _VERSION_TIMEOUT_SEC),
        timeout=_VERSION_TIMEOUT_SEC + 2,
    )


async def get_ffmpeg_version() -> ToolVersion:
    """Return ffmpeg -version first line."""
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(
            None,
            _run_version_cmd,
            ["ffmpeg", "-version"],
            _VERSION_TIMEOUT_SEC,
        ),
        timeout=_VERSION_TIMEOUT_SEC + 2,
    )


async def get_ytdlp_ejs_version() -> ToolVersion:
    """Return installed yt-dlp-ejs package version without invoking it."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_package_version, "yt-dlp-ejs", _EJS_MIN_VERSION)


async def get_javascript_runtime_version() -> ToolVersion:
    """Return the first supported JavaScript runtime found on PATH."""
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, _get_javascript_runtime_version),
        timeout=_VERSION_TIMEOUT_SEC + 2,
    )


async def get_dir_stats(root: Path) -> DirStats:
    """Scan a directory tree for file count and total size."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _scan_dir_stats, root)


def _download_slot_info() -> tuple[int, int]:
    limit = settings.DOWNLOAD_SEMAPHORE
    sem = _get_semaphore()
    available = getattr(sem, "_value", limit)
    try:
        available_int = int(available)
    except (TypeError, ValueError):
        available_int = limit
    return limit, max(0, min(limit, available_int))


async def get_last_eviction_snapshot() -> EvictionSnapshot | None:
    """Load last scheduler eviction stats from Redis."""
    try:
        r = await get_redis()
        raw = await r.get(MEDIA_HEALTH_LAST_EVICTION)
        if not raw:
            return None
        data = json.loads(raw)
        return EvictionSnapshot(
            at=str(data.get("at", "")),
            stale_removed=int(data.get("stale_removed", 0)),
            lru_removed=int(data.get("lru_removed", 0)),
        )
    except Exception:
        logger.debug("failed to load last eviction snapshot", exc_info=True)
        return None


async def store_last_eviction_snapshot(stale_removed: int, lru_removed: int) -> None:
    """Persist eviction counts for the developer health panel."""
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "stale_removed": stale_removed,
        "lru_removed": lru_removed,
    }
    try:
        r = await get_redis()
        await r.set(MEDIA_HEALTH_LAST_EVICTION, json.dumps(payload))
    except Exception:
        logger.debug("failed to store last eviction snapshot", exc_info=True)


async def probe_youtube_cached() -> YoutubeProbeResult:
    """Run or return cached yt-dlp simulate probe on a fixed YouTube URL."""
    now = datetime.now(timezone.utc)
    try:
        r = await get_redis()
        raw = await r.get(MEDIA_HEALTH_YTDLP_PROBE)
        if raw:
            data = json.loads(raw)
            checked_at = data.get("checked_at", "")
            try:
                checked_dt = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
                age = int((now - checked_dt).total_seconds())
            except (TypeError, ValueError):
                age = 0
            return YoutubeProbeResult(
                ok=bool(data.get("ok")),
                cached=True,
                age_seconds=max(0, age),
                detail=str(data.get("detail", "")),
            )
    except Exception:
        logger.debug("youtube probe cache read failed", exc_info=True)

    loop = asyncio.get_running_loop()
    result = await asyncio.wait_for(
        loop.run_in_executor(None, _run_ytdlp_simulate, YTDLP_PROBE_URL),
        timeout=_PROBE_TIMEOUT_SEC + 2,
    )

    payload = {
        "ok": result.ok,
        "checked_at": now.isoformat(),
        "detail": result.detail,
    }
    try:
        r = await get_redis()
        await r.set(
            MEDIA_HEALTH_YTDLP_PROBE,
            json.dumps(payload),
            ex=TTL_MEDIA_HEALTH_PROBE,
        )
    except Exception:
        logger.debug("youtube probe cache write failed", exc_info=True)

    return YoutubeProbeResult(
        ok=result.ok,
        cached=False,
        age_seconds=0,
        detail=result.detail,
    )


async def _get_youtube_session_summary_safe() -> YoutubeSessionSummary | None:
    try:
        return await get_youtube_session_summary()
    except Exception:
        logger.debug("YouTube session summary unavailable", exc_info=True)
        return None


async def build_health_snapshot() -> MediaHealthSnapshot:
    """Collect all media health metrics for the developer panel."""
    cache_path = Path(settings.MEDIA_CACHE_PATH)
    downloads_path = Path(settings.DOWNLOADS_PATH)
    download_limit, download_available = _download_slot_info()

    (
        ytdlp,
        ffmpeg,
        cache_dir,
        downloads_dir,
        youtube_probe,
        last_eviction,
        youtube_sessions,
        ytdlp_ejs,
        javascript_runtime,
    ) = await asyncio.gather(
        get_ytdlp_version(),
        get_ffmpeg_version(),
        get_dir_stats(cache_path),
        get_dir_stats(downloads_path),
        probe_youtube_cached(),
        get_last_eviction_snapshot(),
        _get_youtube_session_summary_safe(),
        get_ytdlp_ejs_version(),
        get_javascript_runtime_version(),
    )

    return MediaHealthSnapshot(
        ytdlp=ytdlp,
        ffmpeg=ffmpeg,
        cache_dir=cache_dir,
        cache_size_bytes=get_cache_size_bytes(),
        downloads_dir=downloads_dir,
        active_calls=len(CallService.get_active_calls()),
        download_limit=download_limit,
        download_available=download_available,
        youtube_probe=youtube_probe,
        last_eviction=last_eviction,
        youtube_sessions=youtube_sessions,
        ytdlp_ejs=ytdlp_ejs,
        javascript_runtime=javascript_runtime,
    )


def _format_bytes_gb(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "0"
    return f"{size_bytes / (1024 ** 3):.2f}"


def _format_dir_line(lang: str, label_key: str, stats: DirStats) -> str:
    label = t(lang, label_key)
    if not stats.exists:
        return t(lang, "media_health.dir_missing", label=label)
    suffix = ""
    if stats.truncated:
        suffix = t(lang, "media_health.dir_truncated")
    return t(
        lang,
        "media_health.dir_line",
        label=label,
        files=stats.file_count,
        size_gb=_format_bytes_gb(stats.total_bytes),
        suffix=suffix,
    )


def _format_youtube_probe(lang: str, probe: YoutubeProbeResult) -> str:
    if probe.cached:
        if probe.ok:
            return t(lang, "media_health.probe_cached_ok", age=probe.age_seconds)
        return t(
            lang,
            "media_health.probe_cached_fail",
            age=probe.age_seconds,
            detail=probe.detail or t(lang, "media_health.status_fail"),
        )
    if probe.ok:
        return t(lang, "media_health.probe_ok")
    return t(
        lang,
        "media_health.probe_fail",
        detail=probe.detail or t(lang, "media_health.status_fail"),
    )


def format_health_report(lang: str, snapshot: MediaHealthSnapshot) -> str:
    """Format a health snapshot into user-facing panel text."""
    lines = [
        t(lang, "media_health.title"),
        "",
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "media_health.ytdlp_label"),
            value=(
                snapshot.ytdlp.version
                if snapshot.ytdlp.ok
                else _truncate_detail(snapshot.ytdlp.detail or t(lang, "media_health.status_fail"))
            ),
        ),
        _format_dir_line(lang, "media_health.cache_dir_label", snapshot.cache_dir),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "media_health.cache_quota_label"),
            value=f"{_format_bytes_gb(snapshot.cache_size_bytes)} GB",
        ),
        _format_dir_line(lang, "media_health.downloads_dir_label", snapshot.downloads_dir),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "media_health.active_calls_label"),
            value=str(snapshot.active_calls),
        ),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "media_health.download_slots_label"),
            value=t(
                lang,
                "media_health.download_slots_value",
                available=snapshot.download_available,
                limit=snapshot.download_limit,
            ),
        ),
        t(
            lang,
            "status.setting_value",
            feature=t(lang, "media_health.youtube_probe_label"),
            value=_format_youtube_probe(lang, snapshot.youtube_probe),
        ),
    ]

    for label_key, tool in (
        ("media_health.ffmpeg_label", snapshot.ffmpeg),
        ("media_health.javascript_runtime_label", snapshot.javascript_runtime),
        ("media_health.ytdlp_ejs_label", snapshot.ytdlp_ejs),
    ):
        if tool is None:
            continue
        lines.insert(
            4,
            t(
                lang,
                "status.setting_value",
                feature=t(lang, label_key),
                value=(
                    tool.version
                    if tool.ok
                    else _truncate_detail(tool.detail or t(lang, "media_health.status_fail"))
                ),
            ),
        )

    if snapshot.youtube_sessions is not None:
        lines.append(
            t(
                lang,
                "status.setting_value",
                feature=t(lang, "youtube_sessions.panel_button"),
                value=t(
                    lang,
                    "youtube_sessions.health_value",
                    active=snapshot.youtube_sessions.active,
                    cooldown=snapshot.youtube_sessions.cooldown,
                    inactive=snapshot.youtube_sessions.disabled_or_invalid,
                ),
            )
        )

    if snapshot.last_eviction is not None:
        lines.append(
            t(
                lang,
                "media_health.last_eviction",
                at=snapshot.last_eviction.at,
                stale=snapshot.last_eviction.stale_removed,
                lru=snapshot.last_eviction.lru_removed,
            )
        )
    else:
        lines.append(t(lang, "media_health.last_eviction_none"))

    return "\n".join(lines)


def _safe_relative_path(path: Path) -> str | None:
    """Return a root-relative path when the file is under trusted media roots."""
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


def _trusted_file_entry(path: Path, now_ts: float) -> dict | None:
    """Build a safe inventory entry for a file under trusted roots."""
    try:
        if path.is_symlink():
            target = path.resolve(strict=False)
            if _safe_relative_path(target) is None:
                return None
            stat_path = target
        else:
            stat_path = path.resolve(strict=False)
            if _safe_relative_path(stat_path) is None:
                return None
        if not stat_path.is_file():
            return None
        st = stat_path.stat()
    except OSError:
        return None

    relative = _safe_relative_path(stat_path)
    if relative is None:
        return None
    return {
        "relative_path": relative,
        "size_bytes": st.st_size,
        "modified_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "age_seconds": max(0, int(now_ts - st.st_mtime)),
    }


def _collect_top_largest_files(root: Path) -> tuple[list[dict], int, int, bool]:
    """Scan trusted files under root and return top-N largest entries."""
    if not root.exists():
        return [], 0, 0, False

    now_ts = datetime.now(timezone.utc).timestamp()
    scanned = 0
    file_count = 0
    total_bytes = 0
    scan_truncated = False
    candidates: list[tuple[int, dict]] = []

    try:
        for entry in root.rglob("*"):
            scanned += 1
            if scanned > _DIR_SCAN_FILE_CAP:
                scan_truncated = True
                break
            if entry.name == INSTANCE_MARKER_FILENAME:
                continue
            if not entry.is_file() and not entry.is_symlink():
                continue
            item = _trusted_file_entry(entry, now_ts)
            if item is None:
                continue
            file_count += 1
            total_bytes += int(item["size_bytes"])
            candidates.append((int(item["size_bytes"]), item))
    except OSError as exc:
        logger.debug("inventory scan failed for %s: %s", root, safe_exc_name(exc))
        return [], 0, 0, False

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    top = [pair[1] for pair in candidates[:TOP_FILES_LIMIT]]
    return top, file_count, total_bytes, scan_truncated


def redact_media_source(source: str | None) -> dict:
    """Return a redacted representation of a playback or queue source."""
    if source is None or not str(source).strip():
        return {"kind": "empty"}

    fields = build_media_source_fields(source)
    if fields is None:
        return {"kind": "empty"}

    if fields.source_kind == "url":
        parsed = urlparse(str(source).strip())
        return {
            "kind": "url",
            "scheme": parsed.scheme or "",
            "host": fields.host or "",
            "path": parsed.path or "/",
            "url_fingerprint": fields.url_fingerprint[:16],
        }

    if fields.source_kind == "local":
        return {
            "kind": "local",
            "relative_path": fields.redacted_url,
            "url_fingerprint": fields.url_fingerprint[:16],
        }

    if fields.source_kind == "telegram":
        return {
            "kind": "telegram",
            "redacted_url": fields.redacted_url,
            "url_fingerprint": fields.url_fingerprint[:16],
        }

    return {"kind": "redacted", "value": fields.redacted_url}


def _probe_status_for_report(probe: YoutubeProbeResult) -> str:
    if probe.cached:
        return "cached_ok" if probe.ok else "cached_failed"
    return "ok" if probe.ok else "failed"


def _storage_section(label: str, root: Path, stats: DirStats) -> dict:
    """Build storage inventory for one trusted root (sync; run in executor)."""
    top_files, file_count, total_bytes, scan_truncated = _collect_top_largest_files(root)
    return {
        "path_label": label,
        "exists": stats.exists,
        "file_count": file_count if stats.exists else 0,
        "total_bytes": total_bytes if stats.exists else 0,
        "scan_truncated": scan_truncated or stats.truncated,
        "top_largest_files": top_files,
    }


async def _fetch_active_playback_states(limit: int) -> tuple[list[dict], bool]:
    async with async_session() as session:
        result = await session.execute(
            select(PlaybackState)
            .order_by(PlaybackState.last_update_at.desc())
            .limit(limit + 1)
        )
        rows = list(result.scalars().all())
    truncated = len(rows) > limit
    payload = []
    for row in rows[:limit]:
        payload.append(
            {
                "chat_id": row.chat_id,
                "media_type": row.media_type,
                "title": row.title,
                "source": redact_media_source(row.source),
                "is_paused": row.is_paused,
                "queue_position": row.queue_position,
                "last_update_at": row.last_update_at.isoformat() if row.last_update_at else None,
            }
        )
    return payload, truncated


async def _fetch_queue_summary(limit: int) -> tuple[list[dict], bool]:
    async with async_session() as session:
        result = await session.execute(
            select(Playlist)
            .order_by(Playlist.chat_id.asc(), Playlist.position.asc())
            .limit(limit + 1)
        )
        rows = list(result.scalars().all())
    truncated = len(rows) > limit
    payload = []
    for row in rows[:limit]:
        payload.append(
            {
                "chat_id": row.chat_id,
                "position": row.position,
                "media_type": row.media_type,
                "title": row.title,
                "stream_url": redact_media_source(row.stream_url),
                "file_path": redact_media_source(row.file_path),
            }
        )
    return payload, truncated


def _apply_json_size_limit(report: dict) -> dict:
    """Shrink report payload if serialized JSON exceeds the byte cap."""
    encoded = json.dumps(report, ensure_ascii=False).encode("utf-8")
    if len(encoded) <= MAX_REPORT_JSON_BYTES:
        limits = report.setdefault("limits", {})
        limits["json_truncated"] = False
        return report

    report["limits"]["json_truncated"] = True
    report["limits"]["json_truncated_reason"] = "report_exceeded_max_json_bytes"
    playback = report.get("playback", {})
    if isinstance(playback.get("active_states"), list):
        playback["active_states"] = playback["active_states"][:20]
    if isinstance(playback.get("queue_summary"), list):
        playback["queue_summary"] = playback["queue_summary"][:50]
    for key in ("media_cache", "downloads"):
        storage = report.get("storage", {}).get(key, {})
        if isinstance(storage.get("top_largest_files"), list):
            storage["top_largest_files"] = storage["top_largest_files"][:10]
    return report


async def build_media_report() -> dict:
    """Build a bounded JSON-serializable media health report."""
    snapshot = await build_health_snapshot()
    cache_root = Path(settings.MEDIA_CACHE_PATH)
    downloads_root = Path(settings.DOWNLOADS_PATH)

    loop = asyncio.get_running_loop()
    cache_storage, downloads_storage = await asyncio.gather(
        loop.run_in_executor(None, _storage_section, "MEDIA_CACHE_PATH", cache_root, snapshot.cache_dir),
        loop.run_in_executor(
            None,
            _storage_section,
            "DOWNLOADS_PATH",
            downloads_root,
            snapshot.downloads_dir,
        ),
    )
    active_states, states_truncated = await _fetch_active_playback_states(MAX_ACTIVE_STATES)
    queue_summary, queue_truncated = await _fetch_queue_summary(MAX_QUEUE_ROWS)
    ranking_summary = await get_media_ranking_summary()

    last_eviction: dict = {}
    if snapshot.last_eviction is not None:
        last_eviction = {
            "at": snapshot.last_eviction.at,
            "stale_removed": snapshot.last_eviction.stale_removed,
            "lru_removed": snapshot.last_eviction.lru_removed,
        }

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "health": {
            "yt_dlp": {
                "available": snapshot.ytdlp.ok,
                "version": snapshot.ytdlp.version,
                "probe_status": _probe_status_for_report(snapshot.youtube_probe),
                "probe_cached": snapshot.youtube_probe.cached,
            },
            "ffmpeg": {
                "available": snapshot.ffmpeg.ok,
                "version": snapshot.ffmpeg.version,
            },
        },
        "storage": {
            "media_cache": cache_storage,
            "downloads": downloads_storage,
        },
        "runtime": {
            "active_calls_count": snapshot.active_calls,
            "download_slots": {
                "available": snapshot.download_available,
                "limit": snapshot.download_limit,
            },
            "last_eviction": last_eviction,
        },
        "playback": {
            "active_states": active_states,
            "queue_summary": queue_summary,
        },
        "ranking": ranking_summary_to_json(ranking_summary),
        "limits": {
            "max_files_scanned": _DIR_SCAN_FILE_CAP,
            "top_files_limit": TOP_FILES_LIMIT,
            "max_active_states": MAX_ACTIVE_STATES,
            "max_queue_rows": MAX_QUEUE_ROWS,
            "truncated": bool(
                states_truncated
                or queue_truncated
                or cache_storage.get("scan_truncated")
                or downloads_storage.get("scan_truncated")
            ),
            "playback_states_truncated": states_truncated,
            "queue_rows_truncated": queue_truncated,
        },
    }
    return _apply_json_size_limit(report)


def media_report_filename() -> str:
    """Return a timestamped JSON report filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"media_report_{stamp}.json"


def write_media_report_tempfile(report: dict) -> tuple[Path, str]:
    """Write report JSON to a temporary file and return path and download filename."""
    filename = media_report_filename()
    Path(settings.TEMP_PATH).mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix="media_report_",
        suffix=".json",
        dir=settings.TEMP_PATH,
    )
    os.close(fd)
    path = Path(raw_path)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, filename


def _format_ranking_entries(lang: str, header_key: str, entries: tuple[RankingEntry, ...]) -> list[str]:
    lines = [t(lang, header_key)]
    if not entries:
        lines.append(t(lang, "media_health.ranking_section_empty"))
        return lines
    for index, entry in enumerate(entries, start=1):
        host_suffix = f" ({entry.host})" if entry.host else ""
        lines.append(
            t(
                lang,
                "media_health.ranking_item_line",
                rank=index,
                url=entry.redacted_url,
                host_suffix=host_suffix,
                count=entry.count,
            )
        )
    return lines


def format_media_ranking_report(lang: str, summary: MediaRankingSummary) -> str:
    """Format URL ranking for the developer panel."""
    if not summary.has_data:
        return t(lang, "media_health.ranking_no_data")

    lines = [
        t(lang, "media_health.ranking_title"),
        "",
        t(lang, "media_health.ranking_tracking_note"),
        "",
    ]
    lines.extend(_format_ranking_entries(lang, "media_health.ranking_top_played", summary.top_played))
    lines.append("")
    lines.extend(_format_ranking_entries(lang, "media_health.ranking_top_downloaded", summary.top_downloaded))
    if summary.recent_top_played:
        lines.append("")
        lines.extend(
            _format_ranking_entries(lang, "media_health.ranking_recent_played", summary.recent_top_played)
        )
    if summary.top_hosts:
        lines.append("")
        lines.append(t(lang, "media_health.ranking_top_hosts"))
        for index, entry in enumerate(summary.top_hosts, start=1):
            lines.append(
                t(
                    lang,
                    "media_health.ranking_host_line",
                    rank=index,
                    host=entry.host,
                    count=entry.count,
                )
            )
    return "\n".join(lines)


# ── Safe developer cleanup (Phase 3) ────────────────────────────────────────


@dataclass(frozen=True)
class CleanupCandidate:
    """A stale file eligible for safe deletion under trusted media roots."""

    relative_path: str
    size_bytes: int
    category: str


@dataclass(frozen=True)
class CleanupPreview:
    """Dry-run summary of files that may be removed on confirmation."""

    cache_count: int
    downloads_count: int
    total_bytes: int
    samples: tuple[CleanupCandidate, ...]
    scan_truncated: bool


@dataclass(frozen=True)
class CleanupResult:
    """Outcome of a confirmed safe cleanup run."""

    deleted_count: int
    skipped_count: int
    failed_count: int
    freed_bytes: int
    cache_deleted: int
    downloads_deleted: int


@dataclass(frozen=True)
class CleanupSnapshot:
    """Last developer or scheduler cleanup stats stored in Redis."""

    at: str
    deleted_count: int
    freed_bytes: int
    cache_deleted: int
    downloads_deleted: int


def _transcode_sibling_paths(path: Path) -> list[Path]:
    siblings: list[Path] = []
    for suffix in _TRANSCODE_SUFFIXES:
        candidate = path.with_suffix(suffix)
        trusted = ensure_trusted_local_media_path(candidate)
        if trusted is not None:
            siblings.append(trusted)
    return siblings


def _register_protected_path(paths: set[Path], raw: str | None) -> None:
    if not raw or is_http_url(str(raw).strip()):
        return
    trusted = ensure_trusted_local_media_path(raw)
    if trusted is None:
        return
    try:
        resolved = trusted.resolve(strict=False)
    except OSError:
        return
    paths.add(resolved)
    for sibling in _transcode_sibling_paths(resolved):
        paths.add(sibling)


def _protected_path_set(protected: frozenset[str]) -> set[Path]:
    resolved: set[Path] = set()
    for item in protected:
        _register_protected_path(resolved, item)
    return resolved


def _resolved_trusted_file(entry: Path) -> Path | None:
    """Resolve a directory entry to a trusted regular file, rejecting symlink escapes."""
    if entry.name == INSTANCE_MARKER_FILENAME:
        return None
    try:
        if entry.is_symlink():
            target = entry.resolve(strict=False)
            if _safe_relative_path(target) is None:
                return None
            if not target.is_file():
                return None
            return target
        if not entry.is_file():
            return None
        resolved = entry.resolve(strict=False)
        if _safe_relative_path(resolved) is None:
            return None
        return resolved
    except OSError:
        return None


def _is_stale_for_cleanup(path: Path, *, category: str, now_ts: float) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    if category == "cache":
        cache_cutoff = now_ts - (settings.MEDIA_CACHE_MAX_AGE_HOURS * 3600)
        return st.st_atime < cache_cutoff
    download_cutoff = now_ts - DOWNLOAD_RETENTION_SECONDS
    return st.st_mtime < download_cutoff


def _category_for_root(path: Path, cache_root: Path, downloads_root: Path) -> str | None:
    try:
        path.relative_to(cache_root)
        return "cache"
    except ValueError:
        pass
    try:
        path.relative_to(downloads_root)
        return "downloads"
    except ValueError:
        return None


def _scan_cleanup_file_paths(
    protected_resolved: set[Path],
) -> tuple[list[Path], bool]:
    """List stale trusted files that are not actively referenced."""
    cache_root = Path(settings.MEDIA_CACHE_PATH).expanduser().resolve(strict=False)
    downloads_root = Path(settings.DOWNLOADS_PATH).expanduser().resolve(strict=False)
    now_ts = datetime.now(timezone.utc).timestamp()
    candidates: list[Path] = []
    scan_truncated = False
    scanned = 0

    for root in (cache_root, downloads_root):
        if not root.exists():
            continue
        try:
            for entry in root.rglob("*"):
                scanned += 1
                if scanned > _DIR_SCAN_FILE_CAP:
                    scan_truncated = True
                    break
                resolved = _resolved_trusted_file(entry)
                if resolved is None:
                    continue
                category = _category_for_root(resolved, cache_root, downloads_root)
                if category is None:
                    continue
                if resolved in protected_resolved:
                    continue
                if not _is_stale_for_cleanup(resolved, category=category, now_ts=now_ts):
                    continue
                candidates.append(resolved)
        except OSError as exc:
            logger.debug("cleanup scan failed for %s: %s", root, safe_exc_name(exc))
        if scan_truncated:
            break

    return candidates, scan_truncated


def _candidate_from_path(path: Path) -> CleanupCandidate | None:
    cache_root = Path(settings.MEDIA_CACHE_PATH).expanduser().resolve(strict=False)
    downloads_root = Path(settings.DOWNLOADS_PATH).expanduser().resolve(strict=False)
    category = _category_for_root(path, cache_root, downloads_root)
    if category is None:
        return None
    relative = _safe_relative_path(path)
    if relative is None:
        return None
    try:
        size_bytes = path.stat().st_size
    except OSError:
        return None
    return CleanupCandidate(relative_path=relative, size_bytes=size_bytes, category=category)


def _build_preview_from_paths(
    paths: list[Path],
    scan_truncated: bool,
) -> CleanupPreview:
    cache_count = 0
    downloads_count = 0
    total_bytes = 0
    samples: list[CleanupCandidate] = []

    cache_root = Path(settings.MEDIA_CACHE_PATH).expanduser().resolve(strict=False)
    downloads_root = Path(settings.DOWNLOADS_PATH).expanduser().resolve(strict=False)

    for path in paths:
        category = _category_for_root(path, cache_root, downloads_root)
        if category == "cache":
            cache_count += 1
        elif category == "downloads":
            downloads_count += 1
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue

    def _path_size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    sorted_paths = sorted(paths, key=_path_size, reverse=True)
    for path in sorted_paths:
        if len(samples) >= _CLEANUP_PREVIEW_SAMPLES:
            break
        item = _candidate_from_path(path)
        if item is not None:
            samples.append(item)

    return CleanupPreview(
        cache_count=cache_count,
        downloads_count=downloads_count,
        total_bytes=total_bytes,
        samples=tuple(samples),
        scan_truncated=scan_truncated,
    )


async def collect_protected_local_paths() -> frozenset[str]:
    """Collect local paths that must not be deleted (playback, queue, active calls)."""
    protected: set[Path] = set()
    protected.update(_protected_path_set(CallService.collect_active_local_paths()))

    try:
        async with async_session() as session:
            playback_rows = await session.execute(select(PlaybackState))
            for row in playback_rows.scalars():
                _register_protected_path(protected, row.source)

            playlist_rows = await session.execute(select(Playlist))
            for row in playlist_rows.scalars():
                _register_protected_path(protected, row.file_path)
                _register_protected_path(protected, row.stream_url)
    except Exception:
        logger.debug("failed to load protected paths from database", exc_info=True)

    return frozenset(str(p) for p in protected)


async def build_cleanup_preview() -> CleanupPreview:
    """Compute a dry-run cleanup preview without deleting anything."""
    protected = await collect_protected_local_paths()
    loop = asyncio.get_running_loop()
    paths, scan_truncated = await loop.run_in_executor(
        None,
        _scan_cleanup_file_paths,
        _protected_path_set(protected),
    )
    return _build_preview_from_paths(paths, scan_truncated)


def format_cleanup_preview(lang: str, preview: CleanupPreview) -> str:
    """Format cleanup preview text for the developer panel."""
    if preview.cache_count == 0 and preview.downloads_count == 0:
        return t(lang, "media_health.cleanup_preview_empty")

    lines = [
        t(lang, "media_health.cleanup_preview_title"),
        "",
        t(
            lang,
            "media_health.cleanup_preview_counts",
            cache=preview.cache_count,
            downloads=preview.downloads_count,
            size_gb=_format_bytes_gb(preview.total_bytes),
        ),
    ]
    if preview.scan_truncated:
        lines.append(t(lang, "media_health.cleanup_preview_scan_capped"))

    if preview.samples:
        lines.append("")
        lines.append(t(lang, "media_health.cleanup_preview_samples_header"))
        for item in preview.samples:
            lines.append(
                t(
                    lang,
                    "media_health.cleanup_preview_sample_line",
                    category=item.category,
                    path=item.relative_path,
                    size_kb=max(1, item.size_bytes // 1024),
                )
            )

    lines.append("")
    lines.append(t(lang, "media_health.cleanup_confirm_prompt"))
    return "\n".join(lines)


def _cleanup_empty_dirs_under_roots() -> int:
    removed = 0
    for root in trusted_media_roots():
        if not root.exists():
            continue
        try:
            for directory in sorted(root.rglob("*"), reverse=True):
                if not directory.is_dir():
                    continue
                try:
                    directory.rmdir()
                    removed += 1
                except OSError:
                    pass
        except OSError:
            pass
    return removed


def _delete_cleanup_paths(paths: list[Path], protected_resolved: set[Path]) -> CleanupResult:
    deleted_count = 0
    skipped_count = 0
    failed_count = 0
    freed_bytes = 0
    cache_deleted = 0
    downloads_deleted = 0

    cache_root = Path(settings.MEDIA_CACHE_PATH).expanduser().resolve(strict=False)
    downloads_root = Path(settings.DOWNLOADS_PATH).expanduser().resolve(strict=False)

    for path in paths:
        if path in protected_resolved:
            skipped_count += 1
            continue
        if _safe_relative_path(path) is None:
            skipped_count += 1
            continue
        try:
            size_bytes = path.stat().st_size
        except OSError:
            skipped_count += 1
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failed_count += 1
            continue
        deleted_count += 1
        freed_bytes += size_bytes
        category = _category_for_root(path, cache_root, downloads_root)
        if category == "cache":
            cache_deleted += 1
        elif category == "downloads":
            downloads_deleted += 1

    _cleanup_empty_dirs_under_roots()

    return CleanupResult(
        deleted_count=deleted_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        freed_bytes=freed_bytes,
        cache_deleted=cache_deleted,
        downloads_deleted=downloads_deleted,
    )


async def execute_safe_cleanup() -> CleanupResult:
    """Recompute candidates and delete only stale trusted files not in active use."""
    protected = await collect_protected_local_paths()
    protected_resolved = _protected_path_set(protected)
    loop = asyncio.get_running_loop()
    paths, _ = await loop.run_in_executor(
        None,
        _scan_cleanup_file_paths,
        protected_resolved,
    )
    result = await loop.run_in_executor(
        None,
        _delete_cleanup_paths,
        paths,
        protected_resolved,
    )
    await store_last_cleanup_snapshot(result)
    return result


async def store_last_cleanup_snapshot(result: CleanupResult) -> None:
    """Persist last safe cleanup stats for the developer health panel."""
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "deleted_count": result.deleted_count,
        "freed_bytes": result.freed_bytes,
        "cache_deleted": result.cache_deleted,
        "downloads_deleted": result.downloads_deleted,
    }
    try:
        r = await get_redis()
        await r.set(MEDIA_HEALTH_LAST_CLEANUP, json.dumps(payload))
    except Exception:
        logger.debug("failed to store last cleanup snapshot", exc_info=True)


def format_cleanup_result(lang: str, result: CleanupResult) -> str:
    """Format cleanup completion message."""
    return t(
        lang,
        "media_health.cleanup_completed",
        deleted=result.deleted_count,
        skipped=result.skipped_count,
        failed=result.failed_count,
        size_gb=_format_bytes_gb(result.freed_bytes),
    )


def cleanup_stale_downloads_sync(
    protected: frozenset[str],
    retention_seconds: int = DOWNLOAD_RETENTION_SECONDS,
) -> int:
    """Remove stale files under DOWNLOADS_PATH (including nested chat dirs).

    Args:
        protected: Resolved local paths that must be kept.
        retention_seconds: Delete files with mtime older than this many seconds.

    Returns:
        Number of files removed.
    """
    downloads_root = Path(settings.DOWNLOADS_PATH).expanduser().resolve(strict=False)
    if not downloads_root.exists():
        return 0

    protected_resolved = _protected_path_set(protected)
    cutoff = datetime.now(timezone.utc).timestamp() - retention_seconds
    removed = 0

    try:
        for entry in downloads_root.rglob("*"):
            resolved = _resolved_trusted_file(entry)
            if resolved is None:
                continue
            try:
                resolved.relative_to(downloads_root)
            except ValueError:
                continue
            if resolved in protected_resolved:
                continue
            try:
                if resolved.stat().st_mtime >= cutoff:
                    continue
                size_before = resolved.stat().st_size
                resolved.unlink(missing_ok=True)
                removed += 1
                logger.debug(
                    "Scheduler removed stale download %s (%.1f KB)",
                    resolved.name,
                    size_before / 1024,
                )
            except OSError:
                pass
    except OSError as exc:
        logger.debug("scheduler download cleanup scan failed: %s", safe_exc_name(exc))

    if removed:
        _cleanup_empty_dirs_under_roots()
    return removed
