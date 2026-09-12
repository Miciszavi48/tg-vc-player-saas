"""PyTgCalls / voice-stack probing and stream-class discovery.

Read-only infrastructure helpers used by ``scripts/check_voice_stack.py`` and
``app.services.call_service``. Does not start the bot, connect to Telegram, or
touch DB/Redis.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.config.settings import settings
from app.utils.diagnostic_logging import safe_exc_name

logger = logging.getLogger(__name__)

_PROBE_SOURCE = str(Path(settings.TEMP_PATH) / "voice_stack_probe_nonexistent.mp3")


@dataclass
class ImportProbe:
    """Result of probing a single import path."""

    name: str
    ok: bool
    error: str | None = None


@dataclass
class StreamCandidateProbe:
    """Result of attempting one stream builder."""

    name: str
    ok: bool
    error: str | None = None


@dataclass
class VoiceStackReport:
    """Aggregated voice-stack diagnostic report."""

    python_version: str
    platform_info: str
    pytgcalls_importable: bool
    pytgcalls_version: str | None
    pytgcalls_dist_name: str | None
    tgcalls_importable: bool
    ntgcalls_importable: bool
    import_probes: list[ImportProbe] = field(default_factory=list)
    stream_candidates: list[StreamCandidateProbe] = field(default_factory=list)
    audio_stream_buildable: bool = False
    video_stream_buildable: bool = False
    vc_methods: dict[str, bool] = field(default_factory=dict)
    ffmpeg_in_path: bool = False
    ffmpeg_version: str | None = None
    usable: bool = False
    notes: list[str] = field(default_factory=list)


def get_pytgcalls_version() -> tuple[bool, str | None, str | None]:
    """Return whether pytgcalls imports, its version string, and pip dist name hint.

    Returns:
        Tuple of (importable, version or None, distribution name hint or None).
    """
    try:
        import pytgcalls  # type: ignore[import-untyped]

        version = getattr(pytgcalls, "__version__", None)
        if version is not None:
            version = str(version)
        dist = _pip_distribution_name("py-tgcalls", "pytgcalls")
        return True, version, dist
    except ImportError:
        return False, None, None


def _pip_distribution_name(*candidates: str) -> str | None:
    """Best-effort installed distribution name without importing pip at runtime."""
    for name in candidates:
        try:
            from importlib.metadata import version as pkg_version

            pkg_version(name)
            return name
        except Exception:
            continue
    return None


def probe_import_symbols() -> list[ImportProbe]:
    """Probe import availability for symbols used by call_service and main."""
    specs = (
        ("PyTgCalls", "pytgcalls", "PyTgCalls"),
        ("AudioPiped", "pytgcalls.types", "AudioPiped"),
        ("input_stream.AudioPiped", "pytgcalls.types.input_stream", "AudioPiped"),
        ("types.MediaStream", "pytgcalls.types", "MediaStream"),
        ("AudioQuality", "pytgcalls.types", "AudioQuality"),
        ("VideoQuality", "pytgcalls.types", "VideoQuality"),
        ("AudioVideoPiped", "pytgcalls.types", "AudioVideoPiped"),
        ("input_stream.AudioVideoPiped", "pytgcalls.types.input_stream", "AudioVideoPiped"),
        ("types.Update", "pytgcalls.types", "Update"),
    )
    results: list[ImportProbe] = []
    for label, module_name, attr in specs:
        try:
            mod = importlib.import_module(module_name)
            getattr(mod, attr)
            results.append(ImportProbe(name=label, ok=True))
        except Exception as exc:
            results.append(ImportProbe(name=label, ok=False, error=f"{type(exc).__name__}: {exc}"))
    return results


def probe_native_backends() -> tuple[bool, bool]:
    """Return (tgcalls_importable, ntgcalls_importable)."""
    tgcalls_ok = False
    ntgcalls_ok = False
    try:
        importlib.import_module("tgcalls")
        tgcalls_ok = True
    except ImportError:
        pass
    try:
        importlib.import_module("ntgcalls")
        ntgcalls_ok = True
    except ImportError:
        pass
    return tgcalls_ok, ntgcalls_ok


def probe_vc_methods(call_py: Any | None = None) -> dict[str, bool]:
    """Probe VC control methods on a PyTgCalls class/instance or by name only."""
    method_names = (
        "join_group_call",
        "leave_group_call",
        "change_stream",
        "pause_stream",
        "resume_stream",
        "play",
        "leave_call",
        "pause",
        "resume",
        "change_volume_call",
    )
    target: Any | None = call_py
    if target is None:
        try:
            from pytgcalls import PyTgCalls  # type: ignore[import-untyped]

            target = PyTgCalls
        except ImportError:
            return {name: False for name in method_names}
    return {name: callable(getattr(target, name, None)) for name in method_names}


def _try_builder(name: str, builder: Callable[[str], Any]) -> StreamCandidateProbe:
    """Run one stream builder against the probe path."""
    try:
        builder(_PROBE_SOURCE)
        return StreamCandidateProbe(name=name, ok=True)
    except Exception as exc:
        return StreamCandidateProbe(
            name=name,
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )


def _audio_builders() -> list[tuple[str, Callable[[str], Any]]]:
    """Ordered audio stream builder candidates (v2 first, then v1)."""
    builders: list[tuple[str, Callable[[str], Any]]] = []

    def _b6(source: str) -> Any:
        from pytgcalls.types import AudioQuality, MediaStream  # type: ignore[import-untyped]

        return MediaStream(source, audio_parameters=AudioQuality.HIGH)

    builders.append(("MediaStream(v2 AudioQuality)", _b6))

    def _b5(source: str) -> Any:
        from pytgcalls.types import MediaStream  # type: ignore[import-untyped]

        return MediaStream(source, video_flags=MediaStream.Flags.IGNORE)

    builders.append(("MediaStream(v2 IGNORE video)", _b5))

    def _b1(source: str) -> Any:
        from pytgcalls.types import AudioPiped  # type: ignore[import-untyped]

        return AudioPiped(source)

    builders.append(("AudioPiped", _b1))

    def _b2(source: str) -> Any:
        from pytgcalls.types.input_stream import AudioPiped as AP2  # type: ignore[import-untyped]

        return AP2(source)

    builders.append(("input_stream.AudioPiped", _b2))

    def _b3(source: str) -> Any:
        from pytgcalls.types import MediaStream  # type: ignore[import-untyped]

        return MediaStream(source, audio_parameters=True, video_parameters=False)

    builders.append(("MediaStream(bool)", _b3))

    def _b4(source: str) -> Any:
        from pytgcalls import MediaStream as MS  # type: ignore[import-untyped]

        return MS(source)

    builders.append(("root.MediaStream", _b4))

    return builders


def _video_builders() -> list[tuple[str, Callable[[str], Any]]]:
    """Ordered video stream builder candidates (v1 then v2)."""
    builders: list[tuple[str, Callable[[str], Any]]] = []

    def _b1(source: str) -> Any:
        from pytgcalls.types import AudioVideoPiped  # type: ignore[import-untyped]

        return AudioVideoPiped(source)

    builders.append(("AudioVideoPiped", _b1))

    def _b2(source: str) -> Any:
        from pytgcalls.types.input_stream import AudioVideoPiped as AVP2  # type: ignore[import-untyped]

        return AVP2(source)

    builders.append(("input_stream.AudioVideoPiped", _b2))

    def _b3(source: str) -> Any:
        from pytgcalls.types import MediaStream  # type: ignore[import-untyped]

        return MediaStream(source, audio_parameters=True, video_parameters=True)

    builders.append(("MediaStream(bool video)", _b3))

    def _b4(source: str) -> Any:
        from pytgcalls import MediaStream as MS  # type: ignore[import-untyped]

        return MS(source, video=True)

    builders.append(("root.MediaStream(video=True)", _b4))

    def _b5(source: str) -> Any:
        from pytgcalls.types import AudioQuality, MediaStream, VideoQuality  # type: ignore[import-untyped]

        return MediaStream(
            source,
            audio_parameters=AudioQuality.HIGH,
            video_parameters=VideoQuality.HD_720p,
        )

    builders.append(("MediaStream(v2 quality)", _b5))

    return builders


def probe_stream_candidates(
    *,
    media_type: str = "audio",
    log_failures: bool = False,
) -> list[StreamCandidateProbe]:
    """Enumerate stream builder candidates for audio or video."""
    builders = _audio_builders() if media_type == "audio" else _video_builders()
    results: list[StreamCandidateProbe] = []
    for name, builder in builders:
        probe = _try_builder(name, builder)
        results.append(probe)
        if log_failures and not probe.ok and probe.error:
            logger.debug("stream candidate %s failed: %s", name, probe.error)
    return results


def build_audio_stream(
    source: str,
    *,
    log_failures: bool = False,
) -> Any | None:
    """Build an audio stream object using the first compatible pytgcalls API."""
    for name, builder in _audio_builders():
        try:
            return builder(source)
        except Exception as exc:
            if log_failures:
                logger.debug("stream candidate %s failed: %s", name, safe_exc_name(exc))
            continue
    version = get_pytgcalls_version()[1]
    logger.error(
        "No compatible pytgcalls stream class found (pytgcalls version=%s)",
        version or "unknown",
    )
    return None


def build_video_stream(
    source: str,
    *,
    log_failures: bool = False,
) -> Any | None:
    """Build a video stream object using the first compatible pytgcalls API."""
    for name, builder in _video_builders():
        try:
            return builder(source)
        except Exception as exc:
            if log_failures:
                logger.debug("stream candidate %s failed: %s", name, safe_exc_name(exc))
            continue
    version = get_pytgcalls_version()[1]
    logger.error(
        "No compatible pytgcalls video stream class found (pytgcalls version=%s)",
        version or "unknown",
    )
    return None


def discover_stream_class_available(media_type: str = "audio") -> bool:
    """Return True if any stream builder succeeds without starting Telegram."""
    builders = _audio_builders() if media_type == "audio" else _video_builders()
    for _, builder in builders:
        try:
            builder(_PROBE_SOURCE)
            return True
        except Exception:
            continue
    return False


def probe_ffmpeg() -> tuple[bool, str | None]:
    """Return whether ffmpeg is on PATH and its version line."""
    path = shutil.which("ffmpeg")
    if not path:
        return False, None
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        first_line = (proc.stdout or proc.stderr or "").splitlines()
        return True, first_line[0] if first_line else None
    except Exception:
        return True, None


def probe_voice_stack() -> VoiceStackReport:
    """Build a full voice-stack diagnostic report (no bot, DB, or Telegram)."""
    importable, version, dist_name = get_pytgcalls_version()
    tgcalls_ok, ntgcalls_ok = probe_native_backends()
    import_probes = probe_import_symbols() if importable else []
    audio_candidates = probe_stream_candidates(media_type="audio") if importable else []
    video_candidates = probe_stream_candidates(media_type="video") if importable else []
    audio_ok = any(c.ok for c in audio_candidates)
    video_ok = any(c.ok for c in video_candidates)
    vc_methods = probe_vc_methods()
    ffmpeg_ok, ffmpeg_ver = probe_ffmpeg()

    has_join_api = vc_methods.get("join_group_call", False) or vc_methods.get("play", False)
    has_leave_api = vc_methods.get("leave_group_call", False) or vc_methods.get("leave_call", False)
    usable = importable and audio_ok and has_join_api and has_leave_api and ffmpeg_ok

    notes: list[str] = []
    if importable and not ntgcalls_ok and not tgcalls_ok:
        notes.append("pytgcalls imports but neither tgcalls nor ntgcalls native module found")
    if importable and audio_ok and not has_join_api:
        notes.append("stream classes work but no join_group_call/play method on PyTgCalls")
    if importable and dist_name == "pytgcalls" and not tgcalls_ok:
        notes.append("legacy pytgcalls dist without tgcalls — install tgcalls or migrate to py-tgcalls")
    if not ffmpeg_ok:
        notes.append("ffmpeg not found in PATH")

    return VoiceStackReport(
        python_version=sys.version.split()[0],
        platform_info=platform.platform(),
        pytgcalls_importable=importable,
        pytgcalls_version=version,
        pytgcalls_dist_name=dist_name,
        tgcalls_importable=tgcalls_ok,
        ntgcalls_importable=ntgcalls_ok,
        import_probes=import_probes,
        stream_candidates=audio_candidates,
        audio_stream_buildable=audio_ok,
        video_stream_buildable=video_ok,
        vc_methods=vc_methods,
        ffmpeg_in_path=ffmpeg_ok,
        ffmpeg_version=ffmpeg_ver,
        usable=usable,
        notes=notes,
    )


def _is_retryable_vc_join_error(exc: BaseException) -> bool:
    """Return True when a voice-chat connect error may succeed on retry."""
    name = type(exc).__name__
    if name in {"TelegramServerError", "ConnectionError", "TimeoutError", "OSError"}:
        return True
    err_str = f"{name} {exc}".upper()
    return "TELEGRAMSERVERERROR" in err_str or "CONNECTION" in err_str


async def _vc_leave_best_effort(call_py: Any, chat_id: int) -> None:
    """Leave a voice chat without raising when the helper is not connected."""
    for method_name in ("leave_call", "leave_group_call"):
        method = getattr(call_py, method_name, None)
        if not callable(method):
            continue
        try:
            await method(chat_id)
            return
        except Exception as exc:
            logger.debug(
                "vc_leave_best_effort ignored chat_id=%s method=%s exc=%s",
                chat_id,
                method_name,
                type(exc).__name__,
            )


async def _refresh_input_call_cache(call_py: Any, chat_id: int) -> None:
    """Refresh PyTgCalls full-chat cache after leave or create."""
    app = getattr(call_py, "_app", None)
    if app is None:
        return
    get_call = getattr(app, "get_call", None)
    if not callable(get_call):
        return
    try:
        await get_call(chat_id)
    except Exception:
        logger.debug(
            "refresh_input_call_cache failed chat_id=%s",
            chat_id,
            exc_info=True,
        )


async def _vc_in_binding_calls(call_py: Any, chat_id: int) -> bool:
    """Return True when ntgcalls already tracks an active call for *chat_id*."""
    binding = getattr(call_py, "_binding", None)
    calls = getattr(binding, "calls", None)
    if not callable(calls):
        return False
    try:
        active_calls = await calls()
    except Exception:
        logger.debug(
            "binding.calls failed chat_id=%s",
            chat_id,
            exc_info=True,
        )
        return False
    return chat_id in active_calls


async def _vc_play(call_py: Any, chat_id: int, stream: Any) -> None:
    """Start playback via v1 or v2 PyTgCalls API."""
    if hasattr(call_py, "join_group_call"):
        await call_py.join_group_call(chat_id, stream)
        return
    if hasattr(call_py, "play"):
        try:
            from pytgcalls.types import GroupCallConfig
        except ImportError:
            await call_py.play(chat_id, stream)
            return
        try:
            play_signature = inspect.signature(call_py.play)
        except (TypeError, ValueError):
            play_signature = None
        if play_signature is not None and "config" not in play_signature.parameters:
            await call_py.play(chat_id, stream)
            return
        await call_py.play(
            chat_id,
            stream,
            config=GroupCallConfig(auto_start=False),
        )
        return
    raise AttributeError("PyTgCalls has no join_group_call or play method")


async def vc_join(
    call_py: Any,
    chat_id: int,
    stream: Any,
    *,
    max_attempts: int = 2,
    retry_delay_seconds: float = 2.5,
) -> None:
    """Join or start playback using v1 or v2 PyTgCalls API with limited retry."""
    import asyncio

    last_exc: BaseException | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            in_call = await _vc_in_binding_calls(call_py, chat_id)
            if attempt > 1 or in_call:
                await _vc_leave_best_effort(call_py, chat_id)
                if attempt > 1:
                    await _refresh_input_call_cache(call_py, chat_id)

            await _vc_play(call_py, chat_id, stream)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts and _is_retryable_vc_join_error(exc):
                logger.warning(
                    "vc_join retry chat_id=%s attempt=%s/%s exc=%s",
                    chat_id,
                    attempt,
                    max_attempts,
                    type(exc).__name__,
                )
                await asyncio.sleep(retry_delay_seconds)
                continue
            raise

    if last_exc is not None:
        raise last_exc


async def vc_leave(call_py: Any, chat_id: int) -> None:
    """Leave a voice chat using v1 or v2 PyTgCalls API."""
    if hasattr(call_py, "leave_group_call"):
        await call_py.leave_group_call(chat_id)
        return
    if hasattr(call_py, "leave_call"):
        await call_py.leave_call(chat_id)
        return
    raise AttributeError("PyTgCalls has no leave_group_call or leave_call method")


async def vc_change_stream(call_py: Any, chat_id: int, stream: Any) -> None:
    """Replace the active stream using v1 or v2 PyTgCalls API."""
    if hasattr(call_py, "change_stream"):
        await call_py.change_stream(chat_id, stream)
        return
    if hasattr(call_py, "play"):
        await call_py.play(chat_id, stream)
        return
    raise AttributeError("PyTgCalls has no change_stream or play method")


async def vc_pause(call_py: Any, chat_id: int) -> None:
    """Pause playback using v1 or v2 PyTgCalls API."""
    if hasattr(call_py, "pause_stream"):
        await call_py.pause_stream(chat_id)
        return
    if hasattr(call_py, "pause"):
        await call_py.pause(chat_id)
        return
    raise AttributeError("PyTgCalls has no pause_stream or pause method")


async def vc_resume(call_py: Any, chat_id: int) -> None:
    """Resume playback using v1 or v2 PyTgCalls API."""
    if hasattr(call_py, "resume_stream"):
        await call_py.resume_stream(chat_id)
        return
    if hasattr(call_py, "resume"):
        await call_py.resume(chat_id)
        return
    raise AttributeError("PyTgCalls has no resume_stream or resume method")
