from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

from app.config.settings import settings

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

_THIRD_PARTY_LOGGERS = (
    "pyrogram",
    "kurigram",
    "apscheduler",
    "asyncio",
    "sqlalchemy.engine",
)


class InterceptHandler(logging.Handler):
    """Bridge stdlib logging records into Loguru so journalctl captures them."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _resolve_log_level(name: str) -> int:
    """Map settings.LOG_LEVEL string to a stdlib logging level."""
    normalized = (name or "INFO").strip().upper()
    return getattr(logging, normalized, logging.INFO)


def _error_log_path(log_file: str) -> str:
    """Derive the central error-only log path from ``LOG_FILE``."""
    path = Path(log_file)
    if path.suffix == ".log":
        return str(path.with_name(f"{path.stem}_error.log"))
    return str(path.with_name(f"{path.name}_error.log"))


def setup_logger() -> None:
    """Configure Loguru sinks, stdlib interception, and third-party log levels."""
    logger.remove()

    log_path = Path(settings.LOG_FILE)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    error_log = _error_log_path(settings.LOG_FILE)

    logger.add(
        sys.stderr,
        format=_FORMAT,
        level=settings.LOG_LEVEL,
        colorize=True,
    )

    logger.add(
        str(log_path),
        format=_FORMAT,
        level=settings.LOG_LEVEL,
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        encoding="utf-8",
    )

    logger.add(
        error_log,
        format=_FORMAT,
        level="ERROR",
        rotation="10 MB",
        retention="7 days",
        compression="gz",
        encoding="utf-8",
    )

    stdlib_level = _resolve_log_level(settings.LOG_LEVEL)
    logging.basicConfig(handlers=[InterceptHandler()], level=stdlib_level, force=True)

    root = logging.getLogger()
    root.setLevel(stdlib_level)

    for name in _THIRD_PARTY_LOGGERS:
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True
        lib_logger.setLevel(stdlib_level)

    from app.utils.diagnostic_logging import emit_startup_diagnostics

    emit_startup_diagnostics(log_level=settings.LOG_LEVEL.upper())
