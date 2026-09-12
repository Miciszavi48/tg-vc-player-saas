"""P2: Pre-transcode worker pool for RAM reduction.

Pre-transcodes media to pytgcalls-optimal format before streaming.
When pytgcalls receives an already-transcoded Opus/AAC file, its
internal FFmpeg does cheap packet passthrough instead of full decode →
encode, cutting per-stream RSS from ~47 MB to ~12 MB.

A bounded asyncio.Semaphore limits concurrent transcodes to
``TRANSCODE_POOL_SIZE`` (default 4) workers.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

from app.config.settings import settings
from app.utils.media_sources import (
    ensure_trusted_local_media_path,
    is_http_url,
    validate_safe_url,
)

logger = logging.getLogger(__name__)

_pool_sem: asyncio.Semaphore | None = None


def _get_pool() -> asyncio.Semaphore:
    global _pool_sem  # noqa: PLW0603
    if _pool_sem is None:
        _pool_sem = asyncio.Semaphore(settings.TRANSCODE_POOL_SIZE)
    return _pool_sem


_AUDIO_ARGS = [
    "-vn", "-c:a", "libopus", "-b:a", "48k",
    "-ar", "48000", "-ac", "2",
    "-threads", "1",
]

_VIDEO_ARGS = [
    "-c:v", "libx264", "-preset", "ultrafast",
    "-maxrate", "1500k", "-bufsize", "3000k",
    "-vf", "scale=-2:480",
    "-c:a", "aac", "-b:a", "128k",
    "-threads", "2",
]

_SPEED_AUDIO_ARGS = [
    "-vn", "-c:a", "libopus", "-b:a", "48k",
    "-ar", "48000", "-ac", "2",
    "-threads", "1",
]

_SPEED_VIDEO_ARGS = [
    "-c:v", "libx264", "-preset", "ultrafast",
    "-maxrate", "1500k", "-bufsize", "3000k",
    "-c:a", "aac", "-b:a", "128k",
    "-threads", "2",
]


def _speed_cache_dir() -> Path:
    path = Path(settings.MEDIA_CACHE_PATH).expanduser().resolve(strict=False) / "speed_derivatives"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _speed_source_key(src: Path) -> str:
    return hashlib.sha256(str(src).encode("utf-8")).hexdigest()[:16]


# MISC-07: fixed, non-user-supplied FFmpeg audio filter chains. Values are never
# interpolated from user input - only these literals can reach the command line.
EQUALIZER_FILTERS: dict[str, str] = {
    "normal": "",
    "bassboost": "bass=g=10",
    "amplifier": "volume=2.0",
    "soft": "treble=g=-4,bass=g=-2",
    "treble": "treble=g=8",
}


def normalize_equalizer(preset: str | None) -> str:
    """Return a known preset name, defaulting to the unfiltered 'normal'."""
    name = str(preset or "normal").strip().lower()
    return name if name in EQUALIZER_FILTERS else "normal"


def _speed_output_path(
    src: Path,
    speed_percent: int,
    *,
    media_type: str,
    start_at_seconds: int,
    equalizer: str = "normal",
) -> Path:
    stat = src.stat()
    source_key = _speed_source_key(src)
    ext = ".mp4" if media_type == "video" else ".ogg"
    digest = hashlib.sha256(
        (
            f"{src}|{stat.st_mtime_ns}|{stat.st_size}|"
            f"{media_type}|{speed_percent}|{start_at_seconds}|{equalizer}"
        ).encode("utf-8")
    ).hexdigest()[:20]
    return (
        _speed_cache_dir()
        / f"{source_key}.{digest}.{media_type}.speed{speed_percent}.pos{start_at_seconds}{ext}"
    )


def _speed_decimal(speed_percent: int) -> str:
    return f"{speed_percent / 100:.2f}".rstrip("0").rstrip(".")


def _ffmpeg_decimal(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


async def pre_transcode(source: str, media_type: str = "audio") -> str:
    """Transcode ``source`` to an optimized format. Returns path to
    the transcoded file.  If transcoding fails or the source is a URL,
    returns the original path unchanged (graceful fallback).
    """
    if is_http_url(source):
        safe_source = validate_safe_url(source)
        if safe_source is None:
            logger.warning("Rejected unsafe URL transcode source")
            return ""
        return safe_source

    src = ensure_trusted_local_media_path(source)
    if src is None:
        logger.warning("Rejected untrusted local transcode source")
        return source

    if media_type == "video":
        out_ext = ".tc.mp4"
        ff_args = _VIDEO_ARGS
    else:
        out_ext = ".tc.ogg"
        ff_args = _AUDIO_ARGS

    out_path = src.with_suffix(out_ext)
    if out_path.exists() or out_path.is_symlink():
        trusted_out_path = ensure_trusted_local_media_path(out_path)
        if trusted_out_path is None:
            logger.warning("Rejected untrusted transcode output path")
            return source
        if trusted_out_path.stat().st_size > 0:
            return str(trusted_out_path)

    sem = _get_pool()
    async with sem:
        cmd = ["ffmpeg", "-y", "-i", str(src), *ff_args, str(out_path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            returncode = await asyncio.wait_for(proc.wait(), timeout=120)
            if returncode != 0:
                logger.warning("Transcode failed (rc=%d) for %s, using original", returncode, src.name)
                _safe_unlink(out_path)
                return source
            logger.debug("Transcoded %s → %s (%.1f MB)",
                         src.name, out_path.name,
                         out_path.stat().st_size / 1024 / 1024)
            return str(out_path)
        except asyncio.TimeoutError:
            logger.warning("Transcode timed out for %s", src.name)
            try:
                proc.kill()  # type: ignore[union-attr]
            except ProcessLookupError:
                pass
            _safe_unlink(out_path)
            return source


async def pre_transcode_speed(
    source: str,
    speed_percent: int,
    *,
    media_type: str = "audio",
    start_at_seconds: int = 0,
    equalizer: str | None = None,
) -> str | None:
    """Return a trusted local path adjusted to ``speed_percent``.

    Only finite trusted local files are accepted.  Speed 100 with no offset and
    the unfiltered equalizer preset uses the normal pre-transcode path.  Other
    supported cases are produced via bounded FFmpeg filters and cached under
    MEDIA_CACHE_PATH.
    """
    if speed_percent < 50 or speed_percent > 200:
        return None
    media_type = "video" if media_type == "video" else "audio"
    start_at_seconds = max(0, int(start_at_seconds or 0))
    eq_name = normalize_equalizer(equalizer)
    eq_filter = EQUALIZER_FILTERS[eq_name]
    if is_http_url(source):
        return None
    src = ensure_trusted_local_media_path(source)
    if src is None:
        logger.warning("Rejected untrusted local speed source")
        return None
    if speed_percent == 100 and start_at_seconds == 0 and not eq_filter:
        return await pre_transcode(str(src), media_type)

    out_path = _speed_output_path(
        src,
        speed_percent,
        media_type=media_type,
        start_at_seconds=start_at_seconds,
        equalizer=eq_name,
    )
    if out_path.exists() or out_path.is_symlink():
        trusted_out_path = ensure_trusted_local_media_path(out_path)
        if trusted_out_path is None:
            logger.warning("Rejected untrusted speed output path")
            return None
        if trusted_out_path.stat().st_size > 0:
            return str(trusted_out_path)

    sem = _get_pool()
    async with sem:
        speed = _speed_decimal(speed_percent)
        input_args = ["-ss", str(start_at_seconds)] if start_at_seconds > 0 else []
        audio_chain = ",".join(
            part
            for part in (f"atempo={speed}" if speed_percent != 100 else "", eq_filter)
            if part
        )
        if media_type == "video":
            if speed_percent == 100 and not eq_filter:
                cmd = [
                    "ffmpeg",
                    "-y",
                    *input_args,
                    "-i",
                    str(src),
                    *_VIDEO_ARGS,
                    str(out_path),
                ]
            else:
                pts_factor = _ffmpeg_decimal(100 / speed_percent)
                cmd = [
                    "ffmpeg",
                    "-y",
                    *input_args,
                    "-i",
                    str(src),
                    "-filter_complex",
                    f"[0:v]setpts={pts_factor}*PTS[v];[0:a]{audio_chain or 'anull'}[a]",
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                    *_SPEED_VIDEO_ARGS,
                    str(out_path),
                ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                *input_args,
                "-i",
                str(src),
            ]
            if audio_chain:
                cmd.extend(["-filter:a", audio_chain])
            cmd.extend([*_SPEED_AUDIO_ARGS, str(out_path)])
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            timeout = 300 if media_type == "video" else 120
            returncode = await asyncio.wait_for(proc.wait(), timeout=timeout)
            if returncode != 0:
                logger.warning("Speed transcode failed (rc=%d) for %s", returncode, src.name)
                _safe_unlink(out_path)
                return None
            trusted_out_path = ensure_trusted_local_media_path(out_path)
            if trusted_out_path is None or trusted_out_path.stat().st_size <= 0:
                _safe_unlink(out_path)
                return None
            return str(trusted_out_path)
        except asyncio.TimeoutError:
            logger.warning("Speed transcode timed out for %s", src.name)
            try:
                proc.kill()  # type: ignore[union-attr]
            except ProcessLookupError:
                pass
            _safe_unlink(out_path)
            return None
        except Exception:
            logger.debug("Speed transcode error for %s", src.name, exc_info=True)
            _safe_unlink(out_path)
            return None


def _safe_unlink(path: Path) -> None:
    try:
        trusted_path = ensure_trusted_local_media_path(path)
        if trusted_path is not None:
            trusted_path.unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_transcoded(source: str) -> None:
    """Remove transcoded derivatives for a source file."""
    src = ensure_trusted_local_media_path(source)
    if src is None:
        return
    for suffix in (".tc.ogg", ".tc.mp4"):
        tc = src.with_suffix(suffix)
        _safe_unlink(tc)


def cleanup_speed_derivatives(source: str) -> None:
    """Remove cached speed derivatives for a trusted source file."""
    src = ensure_trusted_local_media_path(source)
    if src is None:
        return
    source_key = _speed_source_key(src)
    cache_dir = _speed_cache_dir()
    for path in cache_dir.glob(f"{source_key}.*.speed*.*"):
        _safe_unlink(path)
