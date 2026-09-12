"""Safe diagnostic logging helpers for handler phases and secret masking."""
from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from loguru import logger as _loguru_logger

# Modules that import loguru directly; must use ``{}`` formatting, not ``%s`` / ``%d``.
LOGURU_DIRECT_MODULE_SUFFIXES: frozenset[str] = frozenset({
    "main.py",
    "scheduler.py",
    "helper_otp_wizard.py",
    "helper_otp_pre_auth_registry.py",
    "schema_readiness.py",
    "i18n.py",
    "media_sources.py",
})

_BROKEN_LOGURU_FORMAT_RE = re.compile(
    r'logger\.(?:debug|info|warning|error|critical|exception|opt)\([^)]*%[sd]'
)
_BOT_TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{20,}\b")
_URL_WITH_CREDS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://"
    r"(?:(?P<user>[^:@/]*)(?::(?P<password>[^@/]*))?@)?"
    r"(?P<host>[^/?#]+)"
    r"(?P<rest>[^ \t\r\n]*)"
)
_AUTHORIZATION_RE = re.compile(r"(?i)(authorization|bearer)\s*[:=]?\s*\S+")


def safe_exc_name(exc: BaseException | None) -> str:
    """Return the exception class name without message content."""
    if exc is None:
        return "None"
    return type(exc).__name__


def mask_phone(value: str | None) -> str:
    """Mask a phone number for safe logs, exposing at most the last 3 digits."""
    clean = re.sub(r"\D+", "", (value or "").strip())
    if not clean:
        return "***"
    return f"***{clean[-3:]}"


def mask_secret(value: str | None, *, visible_tail: int = 0) -> str:
    """Replace a secret with a redacted placeholder."""
    if not value:
        return "<empty>"
    text = str(value)
    if len(text) <= 4:
        return "***"
    if visible_tail > 0:
        return f"***{text[-visible_tail:]}"
    return "***"


def mask_token(value: str | None) -> str:
    """Mask a Telegram bot token or similar ``digits:secret`` credential."""
    text = (value or "").strip()
    if not text:
        return "<empty>"
    if _BOT_TOKEN_RE.fullmatch(text):
        prefix = text.split(":", 1)[0]
        return f"{prefix}:***"
    return mask_secret(text)


def mask_session(value: str | None) -> str:
    """Mask a Pyrogram/Telegram session string for safe logs."""
    text = (value or "").strip()
    if not text:
        return "<empty>"
    if len(text) <= 12:
        return "***"
    return f"***{text[:4]}...{text[-4:]}"


def mask_proxy_url(url: str | None) -> str:
    """Mask credentials in a proxy URL (``socks5://user:pass@host:port``)."""
    return mask_connection_url(url)


def mask_authorization_header(value: str | None) -> str:
    """Mask bearer tokens and authorization headers."""
    text = (value or "").strip()
    if not text:
        return "<empty>"
    if text.lower().startswith("bearer "):
        return "Bearer ***"
    return _AUTHORIZATION_RE.sub("authorization=***", text)


def _mask_url_via_urllib(url: str) -> str:
    """Fallback URL redaction for Redis and other schemes SQLAlchemy may not parse."""
    parts = urlsplit(url)
    username = parts.username or ""
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    path = parts.path or ""
    query = f"?{parts.query}" if parts.query else ""
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    userinfo = f"{username}:***@" if parts.username or parts.password else ""
    return urlunsplit((parts.scheme, f"{userinfo}{host}{port}", path, query, fragment))


def mask_connection_url(url: str | None) -> str:
    """Return a database/Redis URL safe for logs (credentials redacted)."""
    if not url:
        return "<empty>"
    try:
        from sqlalchemy.engine import make_url

        parsed = make_url(url)
        username = parsed.username or ""
        host = parsed.host or ""
        port = f":{parsed.port}" if parsed.port else ""
        database = parsed.database or ""
        return f"{parsed.drivername}://{username}:***@{host}{port}/{database}"
    except Exception:
        try:
            return _mask_url_via_urllib(url)
        except Exception:
            return "<redacted-url>"


def safe_subprocess_error_summary(
    returncode: int,
    stderr: str | bytes | None = None,
    stdout: str | bytes | None = None,
    *,
    max_detail: int = 0,
) -> str:
    """Build a log-safe subprocess failure summary without raw credential-bearing output."""
    stderr_bytes = len(stderr or b"") if isinstance(stderr, (bytes, bytearray)) else len((stderr or "").encode())
    stdout_bytes = len(stdout or b"") if isinstance(stdout, (bytes, bytearray)) else len((stdout or "").encode())
    summary = f"exit_code={returncode} stderr_bytes={stderr_bytes} stdout_bytes={stdout_bytes}"
    if max_detail <= 0:
        return summary
    raw = stderr if isinstance(stderr, str) else (stderr or b"").decode("utf-8", errors="replace")
    if not raw and stdout:
        raw = stdout if isinstance(stdout, str) else (stdout or b"").decode("utf-8", errors="replace")
    detail = redact_freeform_text((raw or "").strip().replace("\n", " ")[:max_detail])
    if detail:
        return f"{summary} detail={detail}"
    return summary


def redact_freeform_text(text: str, *, secrets: tuple[str, ...] = ()) -> str:
    """Redact known secrets and common credential patterns from free-form log text."""
    redacted = (text or "").strip()
    if not redacted:
        return ""
    for secret in secrets:
        if secret and len(secret) > 4 and secret in redacted:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = _BOT_TOKEN_RE.sub(lambda m: mask_token(m.group(0)), redacted)

    def _replace_url(match: re.Match[str]) -> str:
        scheme = match.group("scheme")
        user = match.group("user") or ""
        host = match.group("host") or ""
        rest = match.group("rest") or ""
        userinfo = f"{user}:***@" if user or match.group("password") else ""
        return f"{scheme}://{userinfo}{host}{rest}"

    redacted = _URL_WITH_CREDS_RE.sub(_replace_url, redacted)
    redacted = _AUTHORIZATION_RE.sub("authorization=***", redacted)
    return redacted


def callback_data_prefix(data: str | None, *, max_len: int = 48) -> str:
    """Return a safe callback-data prefix without logging full dynamic payloads."""
    text = (data or "").strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}..."


def log_handler_phase(
    log: Any,
    *,
    handler: str,
    phase: str,
    user_id: int | None = None,
    chat_id: int | None = None,
    error: BaseException | None = None,
    extra: dict[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Emit a structured handler phase log without secret payloads."""
    parts = [
        f"handler={handler}",
        f"phase={phase}",
    ]
    if user_id is not None:
        parts.append(f"user_id={user_id}")
    if chat_id is not None:
        parts.append(f"chat_id={chat_id}")
    if error is not None:
        parts.append(f"exc={safe_exc_name(error)}")
    if extra:
        for key, value in extra.items():
            if value is None:
                continue
            parts.append(f"{key}={value}")
    message = " ".join(parts)
    if level == "warning" and error is not None:
        log.opt(exception=True).warning(message)
    elif level == "warning":
        log.warning(message)
    elif level == "error" and error is not None:
        log.opt(exception=True).error(message)
    elif level == "error":
        log.error(message)
    elif level == "debug":
        log.debug(message)
    else:
        log.info(message)


def log_guard_decision(
    scope: str,
    decision: str,
    *,
    reason: str | None = None,
    user_id: int | None = None,
    chat_id: int | None = None,
) -> None:
    """Emit a concise guard pass/block/skip decision at debug level."""
    parts = [f"guard={scope}", f"decision={decision}"]
    if reason:
        parts.append(f"reason={reason}")
    if user_id is not None:
        parts.append(f"user_id={user_id}")
    if chat_id is not None:
        parts.append(f"chat_id={chat_id}")
    _loguru_logger.debug(" ".join(parts))


def log_callback_failure(
    exc: BaseException,
    *,
    handler: str | None = None,
    user_id: int | None = None,
    chat_id: int | None = None,
    callback_data: str | None = None,
) -> None:
    """Log a callback handler failure with safe context for journalctl."""
    prefix = callback_data_prefix(callback_data)
    _loguru_logger.opt(exception=True).warning(
        "callback_handler_failed handler={} user_id={} chat_id={} cb_prefix={} exc={}",
        handler or "unknown",
        user_id,
        chat_id,
        prefix,
        safe_exc_name(exc),
    )


def _task_done_callback(task: asyncio.Task, *, name: str) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _loguru_logger.opt(exception=exc).warning(
            "background_task_failed name={} exc={}",
            name,
            safe_exc_name(exc),
        )


def create_logged_task(coro: Any, *, name: str) -> asyncio.Task:
    """Create an asyncio task that logs unhandled exceptions on completion."""
    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(lambda t: _task_done_callback(t, name=name))
    return task


def find_broken_loguru_percent_formatting(source: str) -> list[str]:
    """Return lines that use printf-style formatting on a loguru logger call."""
    return [
        line.strip()
        for line in source.splitlines()
        if _BROKEN_LOGURU_FORMAT_RE.search(line)
    ]


def emit_startup_diagnostics(*, log_level: str) -> None:
    """Log one-time pipeline diagnostics (no secrets)."""
    unbuffered = os.environ.get("PYTHONUNBUFFERED", "")
    stderr_active = sys.stderr is not None
    stdout_active = sys.stdout is not None
    service_mode = "systemd" if os.environ.get("INVOCATION_ID") else "interactive"
    sinks = "stderr+file"
    _loguru_logger.info(
        "Logging initialized: level={} sinks={} unbuffered={} stderr_active={} "
        "stdout_active={} service_mode={}",
        log_level,
        sinks,
        unbuffered == "1",
        stderr_active,
        stdout_active,
        service_mode,
    )
