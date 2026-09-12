"""Logging visibility, formatting, and no-secrets regression tests."""
from __future__ import annotations

import asyncio
import inspect
import os
import re
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loguru import logger

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-logging-visibility.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

ROOT = Path(__file__).resolve().parents[2]

from app.utils.diagnostic_logging import (  # noqa: E402
    LOGURU_DIRECT_MODULE_SUFFIXES,
    callback_data_prefix,
    create_logged_task,
    find_broken_loguru_percent_formatting,
    log_callback_failure,
    mask_phone,
    mask_secret,
)
from app.utils.ui import CB  # noqa: E402


def _capture_logs():
    buffer = StringIO()
    handler_id = logger.add(buffer, format="{message}", level="DEBUG")
    return buffer, handler_id


@pytest.fixture(autouse=True)
def _no_loguru_handlers_leak():
    yield
    logger.remove()


def test_loguru_direct_modules_have_no_broken_percent_formatting():
    app_dir = ROOT / "app"
    violations: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        if path.name not in LOGURU_DIRECT_MODULE_SUFFIXES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for line in find_broken_loguru_percent_formatting(path.read_text(encoding="utf-8")):
            violations.append(f"{rel}: {line}")
    assert not violations, "Broken loguru % formatting:\n" + "\n".join(violations)


def test_mask_phone_never_returns_full_number():
    phone = "+989123456789"
    masked = mask_phone(phone)
    assert phone not in masked
    assert masked.startswith("***")
    assert masked.endswith("789")
    assert "6789" not in masked


def test_mask_secret_redacts_values():
    assert mask_secret("supersecretapihash") == "***"
    assert mask_secret("") == "<empty>"


def test_helper_otp_auth_failure_logs_phase_and_exc_class():
    from app.handlers import helper_otp_wizard as mod

    source = inspect.getsource(mod._log_auth_event)
    assert "exc_name" in source
    assert "step=" in source
    assert "phone=" in source


def test_helper_otp_source_never_logs_raw_secrets():
    path = ROOT / "app" / "handlers" / "helper_otp_wizard.py"
    text = path.read_text(encoding="utf-8")
    forbidden_patterns = [
        r"logger\.\w+\([^)]*session_string",
        r"logger\.\w+\([^)]*api_hash[^_enc]",
        r"logger\.\w+\([^)]*export_session_string",
        r"logger\.\w+\([^)]*sign_in\([^)]*digits",
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text, re.I), f"Forbidden log pattern: {pattern}"


def test_registry_masks_phone_not_full():
    path = ROOT / "app" / "services" / "helper_otp_pre_auth_registry.py"
    text = path.read_text(encoding="utf-8")
    assert "mask_phone(phone)" in text
    assert "phone_masked" in text


def test_generic_otp_failure_has_journal_log():
    path = ROOT / "app" / "handlers" / "helper_otp_wizard.py"
    text = path.read_text(encoding="utf-8")
    assert "otp_fail_generic" in text
    assert "import_finalize_failed" in text
    assert "log_handler_phase" in text


def test_callback_safety_wrapper_logs_exception_class():
    import app.handlers as handlers_pkg

    source = inspect.getsource(handlers_pkg._wrap_callback_handlers_with_auto_answer)
    assert "log_callback_failure" in source
    assert "exc=" in inspect.getsource(log_callback_failure)


@pytest.mark.asyncio
async def test_background_task_wrapper_logs_task_exceptions():
    buffer, handler_id = _capture_logs()

    async def _boom():
        raise RuntimeError("task_failed_test")

    task = create_logged_task(_boom(), name="test_boom")
    with pytest.raises(RuntimeError, match="task_failed_test"):
        await task
    logger.remove(handler_id)
    output = buffer.getvalue()
    assert "background_task_failed" in output
    assert "RuntimeError" in output


def test_startup_logging_diagnostics_exists():
    from app.utils.diagnostic_logging import emit_startup_diagnostics

    buffer, handler_id = _capture_logs()
    emit_startup_diagnostics(log_level="INFO")
    logger.remove(handler_id)
    output = buffer.getvalue()
    assert "Logging initialized:" in output
    assert "level=INFO" in output
    assert "sinks=" in output


def test_callback_data_constants_unchanged():
    assert CB["HLP_ADD"] == "hlp:add"
    assert CB["BCW_START"] == "bcw:start"
    assert CB["NAV_BACK"] == "nav:back"


def test_callback_data_prefix_truncates_long_values():
    long_data = "dev:list:detail:" + ("9" * 80)
    prefix = callback_data_prefix(long_data, max_len=24)
    assert len(prefix) <= 27
    assert "..." in prefix


@pytest.mark.asyncio
async def test_log_callback_failure_includes_safe_fields():
    buffer, handler_id = _capture_logs()
    try:
        raise ValueError("safe_test")
    except ValueError as exc:
        log_callback_failure(
            exc,
            handler="test_handler",
            user_id=42,
            chat_id=99,
            callback_data="pb:stop",
        )
    logger.remove(handler_id)
    output = buffer.getvalue()
    assert "callback_handler_failed" in output
    assert "ValueError" in output
    assert "user_id=42" in output
    assert "pb:stop" in output
