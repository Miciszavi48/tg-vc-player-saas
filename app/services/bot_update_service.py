"""Developer bot diagnostics and safe configuration-driven reload."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import shlex
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.config.settings import settings
from app.database.engine import async_session
from app.services import CallService
from app.services.analytics_service import get_report
from app.utils.cache import get_redis

from app.utils.diagnostic_logging import redact_freeform_text

logger = logging.getLogger(__name__)

_PROCESS_STARTED_AT: float | None = None
_RUNTIME_BOT = None
_RUNTIME_CALL_PY = None
_OUTPUT_LIMIT = 200


@dataclass(frozen=True)
class ReloadConfigStatus:
    """Safe description of how reload is configured."""

    state: str
    detail: str


@dataclass(frozen=True)
class BotDiagnosticsSnapshot:
    """Read-only runtime diagnostics for the developer panel."""

    python_version: str
    platform_system: str
    process_uptime_seconds: int
    db_ok: bool
    redis_ok: bool
    telegram_ok: bool | None
    scheduler_running: bool
    pytgcalls_available: bool
    pytgcalls_started: bool
    active_calls: int
    helpers_total: int
    helpers_active: int
    memory_mb: float | None
    cpu_percent: float | None
    bot_enabled: bool
    config_presence: dict[str, bool]
    reload_config: ReloadConfigStatus
    error_metrics: dict[str, int]
    errors_available: bool
    ytdlp_available: bool | None
    ffmpeg_available: bool | None


@dataclass(frozen=True)
class ReloadResult:
    """Outcome of a configured reload action."""

    ok: bool
    method: str
    detail: str


def mark_process_started() -> None:
    """Record process start time for uptime reporting."""
    global _PROCESS_STARTED_AT  # noqa: PLW0603
    _PROCESS_STARTED_AT = time.time()


def set_runtime_refs(bot=None, call_py=None) -> None:
    """Store live bot/call references for diagnostics probes."""
    global _RUNTIME_BOT, _RUNTIME_CALL_PY  # noqa: PLW0603
    if bot is not None:
        _RUNTIME_BOT = bot
    if call_py is not None:
        _RUNTIME_CALL_PY = call_py


def get_runtime_bot():
    """Return the live main-bot Pyrogram client set at process start."""
    return _RUNTIME_BOT


def get_reload_config_status() -> ReloadConfigStatus:
    """Return reload configuration state without exposing secrets."""
    if not settings.BOT_RELOAD_ENABLED:
        return ReloadConfigStatus(
            state="disabled",
            detail="BOT_RELOAD_ENABLED=false",
        )

    sentinel = (settings.BOT_RELOAD_SENTINEL_PATH or "").strip()
    command = (settings.BOT_RELOAD_COMMAND or "").strip()

    if sentinel:
        return ReloadConfigStatus(
            state="sentinel",
            detail="sentinel file",
        )
    if command:
        return ReloadConfigStatus(
            state="command",
            detail="configured command",
        )
    return ReloadConfigStatus(
        state="not_configured",
        detail="no reload strategy configured",
    )


def reload_is_available() -> bool:
    """Return True when a safe reload strategy is configured and enabled."""
    status = get_reload_config_status()
    return status.state in {"sentinel", "command"}


def _process_uptime_seconds() -> int:
    if _PROCESS_STARTED_AT is None:
        return 0
    return max(0, int(time.time() - _PROCESS_STARTED_AT))


def _config_presence() -> dict[str, bool]:
    return {
        "bot_token_set": bool(settings.BOT_TOKEN),
        "api_id_set": bool(settings.API_ID),
        "api_hash_set": bool(settings.API_HASH),
        "developer_id_set": bool(settings.DEVELOPER_IDS),
        "database_url_set": bool(settings.DATABASE_URL),
        "redis_url_set": bool(settings.REDIS_URL),
    }


def _truncate_output(text: str, limit: int = _OUTPUT_LIMIT) -> str:
    cleaned = (text or "").strip().replace("\n", " ")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _redact_output(text: str) -> str:
    return redact_freeform_text(
        _truncate_output(text),
        secrets=(settings.BOT_TOKEN, settings.API_HASH, settings.DATABASE_URL, settings.REDIS_URL),
    )


async def _ping_database() -> bool:
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _ping_redis() -> bool:
    try:
        redis = await get_redis()
        return bool(await redis.ping())
    except Exception:
        return False


async def _ping_telegram() -> bool | None:
    if _RUNTIME_BOT is None:
        return None
    try:
        me = await _RUNTIME_BOT.get_me()
        return me is not None
    except Exception:
        return False


def _scheduler_running() -> bool:
    try:
        from app.scheduler import scheduler

        return bool(getattr(scheduler, "running", False))
    except Exception:
        return False


async def _helper_counts() -> tuple[int, int]:
    try:
        from app.services.helper_pool_service import HelperPoolService

        helpers = await HelperPoolService.get_all_helpers()
        total = len(helpers)
        active = sum(1 for item in helpers if getattr(item, "status", "") == "active")
        return total, active
    except Exception:
        return 0, 0


async def _system_metrics() -> tuple[float | None, float | None]:
    try:
        import psutil

        process = psutil.Process()
        mem_mb = process.memory_info().rss / (1024 * 1024)
        cpu = psutil.cpu_percent(interval=0.0)
        return round(mem_mb, 1), round(cpu, 1)
    except Exception:
        return None, None


async def _media_tool_availability() -> tuple[bool | None, bool | None]:
    try:
        from app.services.media_health_service import get_ffmpeg_version, get_ytdlp_version

        ytdlp, ffmpeg = await asyncio.gather(
            get_ytdlp_version(),
            get_ffmpeg_version(),
        )
        return ytdlp.ok, ffmpeg.ok
    except Exception:
        return None, None


async def _error_metrics_summary() -> tuple[dict[str, int], bool]:
    try:
        report = await get_report("errors")
        errors = report.get("errors") if isinstance(report, dict) else None
        if isinstance(errors, dict):
            return {str(k): int(v) for k, v in errors.items()}, True
    except Exception:
        logger.debug("bot update error summary unavailable", exc_info=True)
    return {}, False


async def build_diagnostics_snapshot() -> BotDiagnosticsSnapshot:
    """Collect a safe diagnostics snapshot for the developer panel."""
    db_ok, redis_ok, telegram_ok, helpers, metrics, media, errors = await asyncio.gather(
        _ping_database(),
        _ping_redis(),
        _ping_telegram(),
        _helper_counts(),
        _system_metrics(),
        _media_tool_availability(),
        _error_metrics_summary(),
    )
    helpers_total, helpers_active = helpers
    memory_mb, cpu_percent = metrics
    ytdlp_ok, ffmpeg_ok = media
    error_metrics, errors_available = errors

    from app.repositories import settings_repo

    bot_enabled = await settings_repo.get_bot_setting_bool("bot_enabled", default=True)

    try:
        from app.services.helper_pytgcalls_pool import (
            HelperPyTgCallsPool,
            pytgcalls_available as helper_pytgcalls_available,
        )

        pytgcalls_available = helper_pytgcalls_available()
        pytgcalls_started = bool(HelperPyTgCallsPool.active_helper_ids())
    except ImportError:
        pytgcalls_available = False
        pytgcalls_started = False

    return BotDiagnosticsSnapshot(
        python_version=sys.version.split()[0],
        platform_system=platform.system(),
        process_uptime_seconds=_process_uptime_seconds(),
        db_ok=db_ok,
        redis_ok=redis_ok,
        telegram_ok=telegram_ok,
        scheduler_running=_scheduler_running(),
        pytgcalls_available=pytgcalls_available,
        pytgcalls_started=pytgcalls_started,
        active_calls=len(CallService.get_active_calls()),
        helpers_total=helpers_total,
        helpers_active=helpers_active,
        memory_mb=memory_mb,
        cpu_percent=cpu_percent,
        bot_enabled=bot_enabled,
        config_presence=_config_presence(),
        reload_config=get_reload_config_status(),
        error_metrics=error_metrics,
        errors_available=errors_available,
        ytdlp_available=ytdlp_ok,
        ffmpeg_available=ffmpeg_ok,
    )


def _status_label(lang: str, ok: bool | None) -> str:
    from app.utils.i18n import t

    if ok is True:
        return t(lang, "bot_update.status_ok")
    if ok is False:
        return t(lang, "bot_update.status_fail")
    return t(lang, "bot_update.status_unknown")


def format_bot_update_panel(lang: str, snapshot: BotDiagnosticsSnapshot) -> str:
    """Format diagnostics snapshot for Telegram."""
    from app.utils.i18n import t

    uptime_hours, rem = divmod(snapshot.process_uptime_seconds, 3600)
    uptime_minutes, _ = divmod(rem, 60)

    lines = [
        t(lang, "bot_update.title"),
        "",
        t(
            lang,
            "bot_update.runtime_line",
            python=snapshot.python_version,
            platform=snapshot.platform_system,
            uptime_hours=uptime_hours,
            uptime_minutes=uptime_minutes,
        ),
        t(
            lang,
            "bot_update.connectivity_line",
            db=_status_label(lang, snapshot.db_ok),
            redis=_status_label(lang, snapshot.redis_ok),
            telegram=_status_label(lang, snapshot.telegram_ok),
        ),
        t(
            lang,
            "bot_update.services_line",
            scheduler=_status_label(lang, snapshot.scheduler_running),
            pytgcalls=_status_label(lang, snapshot.pytgcalls_started if snapshot.pytgcalls_available else False),
            bot_enabled=t(lang, "common.labels.on") if snapshot.bot_enabled else t(lang, "common.labels.off"),
        ),
        t(
            lang,
            "bot_update.workload_line",
            active_calls=snapshot.active_calls,
            helpers_active=snapshot.helpers_active,
            helpers_total=snapshot.helpers_total,
        ),
    ]

    if snapshot.memory_mb is not None and snapshot.cpu_percent is not None:
        lines.append(
            t(
                lang,
                "bot_update.resources_line",
                memory_mb=snapshot.memory_mb,
                cpu_percent=snapshot.cpu_percent,
            )
        )

    if snapshot.ytdlp_available is not None or snapshot.ffmpeg_available is not None:
        lines.append(
            t(
                lang,
                "bot_update.media_tools_line",
                ytdlp=_status_label(lang, snapshot.ytdlp_available),
                ffmpeg=_status_label(lang, snapshot.ffmpeg_available),
            )
        )

    reload_state = snapshot.reload_config.state
    if reload_state == "not_configured":
        lines.append(t(lang, "bot_update.reload_not_configured"))
    elif reload_state == "disabled":
        lines.append(t(lang, "bot_update.reload_disabled"))
    elif reload_state == "sentinel":
        lines.append(t(lang, "bot_update.reload_sentinel_configured"))
    else:
        lines.append(t(lang, "bot_update.reload_command_configured"))

    if snapshot.errors_available and snapshot.error_metrics:
        lines.append("")
        lines.append(t(lang, "bot_update.errors_header"))
        for metric, count in sorted(snapshot.error_metrics.items(), key=lambda item: item[1], reverse=True)[:5]:
            lines.append(t(lang, "bot_update.error_metric_line", metric=metric, count=count))
    else:
        lines.append("")
        lines.append(t(lang, "bot_update.errors_unavailable"))

    return "\n".join(lines)


async def _write_reload_sentinel(user_id: int) -> ReloadResult:
    raw_path = (settings.BOT_RELOAD_SENTINEL_PATH or "").strip()
    if not raw_path:
        return ReloadResult(ok=False, method="sentinel", detail="sentinel path missing")

    path = Path(raw_path).expanduser().resolve()
    payload = {
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "requested_by": user_id,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return ReloadResult(ok=True, method="sentinel", detail=str(path.name))
    except Exception as exc:
        logger.exception("Failed to write reload sentinel")
        return ReloadResult(ok=False, method="sentinel", detail=type(exc).__name__)


async def _run_reload_command() -> ReloadResult:
    raw_command = (settings.BOT_RELOAD_COMMAND or "").strip()
    if not raw_command:
        return ReloadResult(ok=False, method="command", detail="command missing")

    try:
        parts = shlex.split(raw_command)
    except ValueError as exc:
        return ReloadResult(ok=False, method="command", detail=str(exc))

    if not parts:
        return ReloadResult(ok=False, method="command", detail="empty command")

    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=max(1, settings.BOT_RELOAD_TIMEOUT_SEC),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()  # type: ignore[union-attr]
        except ProcessLookupError:
            pass
        return ReloadResult(ok=False, method="command", detail="timeout")
    except Exception as exc:
        logger.exception("Reload command failed to start")
        return ReloadResult(ok=False, method="command", detail=type(exc).__name__)

    if proc.returncode == 0:
        detail = _redact_output((stdout or b"").decode("utf-8", errors="replace"))
        return ReloadResult(ok=True, method="command", detail=detail or "exit 0")

    detail = _redact_output((stderr or stdout or b"").decode("utf-8", errors="replace"))
    return ReloadResult(
        ok=False,
        method="command",
        detail=detail or f"exit {proc.returncode}",
    )


async def execute_configured_reload(user_id: int) -> ReloadResult:
    """Trigger the configured safe reload strategy. Never raises."""
    status = get_reload_config_status()
    if status.state == "disabled":
        return ReloadResult(ok=False, method="none", detail="disabled")
    if status.state == "not_configured":
        return ReloadResult(ok=False, method="none", detail="not configured")
    if status.state == "sentinel":
        return await _write_reload_sentinel(user_id)
    return await _run_reload_command()
