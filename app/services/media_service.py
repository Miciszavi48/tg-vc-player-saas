from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import aiohttp

from app.config.settings import INSTANCE_MARKER_FILENAME, settings
from app.utils.media_sources import _SafeAiohttpResolver, validate_safe_url_with_redirects

logger = logging.getLogger(__name__)

_semaphore: asyncio.Semaphore | None = None
_AUDIO_STREAM_FORMAT = "bestaudio/best"


def _direct_media_suffix(url: str, media_type: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    allowed = {
        "audio": {".mp3", ".m4a", ".ogg", ".opus", ".wav"},
        "video": {".mp4", ".mkv", ".webm", ".mov"},
        "photo": {".jpg", ".jpeg", ".png", ".webp"},
    }
    if suffix in allowed[media_type]:
        return suffix
    return {"audio": ".mp3", "video": ".mp4", "photo": ".jpg"}[media_type]


def _direct_content_type_matches(content_type: str | None, media_type: str) -> bool:
    """Reject explicit API/error documents while tolerating generic CDN binaries."""
    normalized = str(content_type or "").partition(";")[0].strip().casefold()
    if not normalized or normalized in {"application/octet-stream", "binary/octet-stream"}:
        return True
    expected_prefix = {"audio": "audio/", "video": "video/", "photo": "image/"}[media_type]
    return normalized.startswith(expected_prefix)


def _video_stream_format() -> str:
    quality = int(settings.VIDEO_QUALITY)
    return (
        f"best[height<={quality}][vcodec!=none][acodec!=none]/"
        "best[vcodec!=none][acodec!=none]"
    )


def _stream_format_for_media_type(media_type: str) -> str:
    return _video_stream_format() if media_type == "video" else _AUDIO_STREAM_FORMAT


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(settings.DOWNLOAD_SEMAPHORE)
    return _semaphore


async def _youtube_pool_is_configured() -> bool:
    """Avoid DB-backed cookie routing unless at least one jar exists."""
    from app.repositories.youtube_session_repo import get_summary

    return (await get_summary()).total > 0


class MediaService:

    @staticmethod
    async def download_audio(url: str, chat_id: int) -> str | None:
        return await MediaService._download(url, chat_id, media_type="audio")

    @staticmethod
    async def download_video(url: str, chat_id: int) -> str | None:
        return await MediaService._download(url, chat_id, media_type="video")

    @staticmethod
    async def download_direct_media(
        url: str,
        chat_id: int,
        *,
        media_type: str,
    ) -> str | None:
        """Download a provider-returned public media URL with cache and size limits."""
        from app.services.media_cache import cache_lookup, cache_store, evict_lru

        if media_type not in {"audio", "video", "photo"}:
            return None
        safe_url = await validate_safe_url_with_redirects(url)
        if safe_url is None:
            logger.warning("Blocked unsafe direct media URL for chat %s", chat_id)
            return None
        cached = cache_lookup(safe_url, media_type)
        if cached:
            return cached

        suffix = _direct_media_suffix(safe_url, media_type)
        max_bytes = max(1, settings.MAX_DOWNLOAD_SIZE_MB) * 1024 * 1024
        download_dir = Path(settings.DOWNLOADS_PATH) / str(chat_id)
        download_dir.mkdir(parents=True, exist_ok=True)
        temp_path = download_dir / f"fastcreat-{uuid.uuid4().hex}{suffix}"

        sem = _get_semaphore()
        async with sem:
            connector = aiohttp.TCPConnector(resolver=_SafeAiohttpResolver(), ttl_dns_cache=0)
            timeout = aiohttp.ClientTimeout(total=120)
            try:
                async with aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    trust_env=False,
                ) as session:
                    async with session.get(
                        safe_url,
                        allow_redirects=False,
                        headers={"User-Agent": "tg-vc-player-saas/fast-creat"},
                    ) as response:
                        if response.status != 200:
                            return None
                        if not _direct_content_type_matches(
                            response.headers.get("Content-Type"), media_type
                        ):
                            return None
                        try:
                            declared_size = int(response.headers.get("Content-Length", "0"))
                        except ValueError:
                            declared_size = 0
                        if declared_size > max_bytes:
                            return None
                        written = 0
                        with temp_path.open("wb") as target:
                            async for chunk in response.content.iter_chunked(64 * 1024):
                                written += len(chunk)
                                if written > max_bytes:
                                    return None
                                target.write(chunk)
                if not temp_path.is_file() or temp_path.stat().st_size == 0:
                    return None
                cached_path = cache_store(safe_url, media_type, str(temp_path))
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, evict_lru)
                return cached_path
            except Exception:
                logger.debug("Direct provider media download failed", exc_info=True)
                return None
            finally:
                temp_path.unlink(missing_ok=True)

    @staticmethod
    async def _download(url: str, chat_id: int, media_type: str) -> str | None:
        from app.services.media_cache import cache_lookup, cache_store, evict_lru
        from app.services.youtube_session_service import (
            YoutubePlaylistUnsupported,
            YoutubeSessionsUnavailable,
            is_playlist_without_video,
            is_youtube_url,
            run_ytdlp_for_media,
        )

        safe_url = await validate_safe_url_with_redirects(url)
        if safe_url is None:
            logger.warning("Blocked unsafe media download URL for chat %s", chat_id)
            return None
        url = safe_url

        cached = cache_lookup(url, media_type)
        if cached:
            return cached
        if is_playlist_without_video(url):
            raise YoutubePlaylistUnsupported("youtube_playlist_requires_video")

        sem = _get_semaphore()
        async with sem:
            download_dir = Path(settings.DOWNLOADS_PATH) / str(chat_id)
            download_dir.mkdir(parents=True, exist_ok=True)

            output_template = str(download_dir / "%(title)s.%(ext)s")

            cmd: list[str] = ["yt-dlp", "--no-playlist", "-o", output_template]

            if media_type == "audio":
                cmd.extend([
                    "-x",
                    "--audio-format", "opus",
                    "--audio-quality", "5",
                    "--postprocessor-args",
                    "ffmpeg:-vn -c:a libopus -b:a 48k -threads 1",
                ])
            else:
                cmd.extend([
                    "-f", f"bestvideo[height<={settings.VIDEO_QUALITY}]+bestaudio/best",
                    "--merge-output-format", "mp4",
                    "--postprocessor-args",
                    "ffmpeg:-c:v libx264 -preset ultrafast"
                    " -maxrate 1500k -bufsize 3000k"
                    f" -vf scale=-2:{min(settings.VIDEO_QUALITY, 480)}"
                    " -c:a aac -b:a 128k -threads 2",
                ])

            cmd.extend([
                "--max-filesize", f"{settings.MAX_DOWNLOAD_SIZE_MB}M",
                "--no-warnings",
                "--quiet",
                url,
            ])

            loop = asyncio.get_running_loop()
            try:
                if is_youtube_url(url) and await _youtube_pool_is_configured():
                    result = await run_ytdlp_for_media(url, cmd, timeout=300)
                    process = result.returncode
                else:
                    process = await loop.run_in_executor(
                        None, lambda: _run_subprocess(cmd)
                    )
                if process != 0:
                    logger.error("yt-dlp exited with code %d for %s", process, url)
                    return None

                downloaded = _find_latest_file(download_dir)
                if downloaded is None:
                    return None

                cached_path = cache_store(url, media_type, str(downloaded))

                loop.run_in_executor(None, evict_lru)

                return cached_path
            except (YoutubeSessionsUnavailable, YoutubePlaylistUnsupported):
                raise
            except Exception:
                logger.exception("Download failed for %s", url)
                return None

    @staticmethod
    async def get_stream_url(url: str, *, media_type: str = "audio") -> str | None:
        from app.services.youtube_session_service import (
            YoutubePlaylistUnsupported,
            YoutubeSessionsUnavailable,
            is_playlist_without_video,
            is_youtube_url,
            run_ytdlp_for_media,
        )

        safe_url = await validate_safe_url_with_redirects(url)
        if safe_url is None:
            logger.warning("Blocked unsafe stream URL")
            return None
        url = safe_url
        if is_playlist_without_video(url):
            raise YoutubePlaylistUnsupported("youtube_playlist_requires_video")
        cmd = [
            "yt-dlp",
            "-g",
            "-f",
            _stream_format_for_media_type(media_type),
            "--no-warnings",
            url,
        ]
        loop = asyncio.get_running_loop()
        try:
            if is_youtube_url(url) and await _youtube_pool_is_configured():
                result = await run_ytdlp_for_media(url, cmd, timeout=60)
                stdout = result.stdout if result.ok else None
            else:
                stdout = await loop.run_in_executor(None, lambda: _get_output(cmd))
            if stdout:
                stream_url = stdout.strip().split("\n")[0]
                return await validate_safe_url_with_redirects(stream_url)
            return None
        except (YoutubeSessionsUnavailable, YoutubePlaylistUnsupported):
            raise
        except Exception:
            logger.exception("Failed to get stream URL for %s", url)
            return None

    @staticmethod
    async def cleanup_old_downloads(max_age_minutes: int = 30) -> int:
        downloads_path = Path(settings.DOWNLOADS_PATH)
        if not downloads_path.exists():
            return 0

        cutoff = time.time() - (max_age_minutes * 60)
        removed = 0
        loop = asyncio.get_running_loop()

        def _cleanup() -> int:
            count = 0
            for f in downloads_path.rglob("*"):
                if (
                    f.is_file()
                    and f.name != INSTANCE_MARKER_FILENAME
                    and f.stat().st_mtime < cutoff
                ):
                    try:
                        f.unlink()
                        count += 1
                    except OSError:
                        pass
            for d in sorted(downloads_path.rglob("*"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass
            return count

        removed = await loop.run_in_executor(None, _cleanup)
        return removed


def _run_subprocess(cmd: list[str]) -> int:
    import subprocess
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    return result.returncode


def _get_output(cmd: list[str]) -> str | None:
    import subprocess
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode == 0:
        return result.stdout
    return None


def _find_latest_file(directory: Path) -> Path | None:
    files = [f for f in directory.iterdir() if f.is_file()]
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)
