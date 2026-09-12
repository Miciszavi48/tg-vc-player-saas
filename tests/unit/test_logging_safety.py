"""Secret masking and unsafe logging pattern regression tests."""
from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-logging-safety.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

ROOT = Path(__file__).resolve().parents[2]

from app.utils.diagnostic_logging import (  # noqa: E402
    LOGURU_DIRECT_MODULE_SUFFIXES,
    callback_data_prefix,
    find_broken_loguru_percent_formatting,
    mask_authorization_header,
    mask_connection_url,
    mask_phone,
    mask_proxy_url,
    mask_secret,
    mask_session,
    mask_token,
    redact_freeform_text,
    safe_subprocess_error_summary,
)


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


def test_mask_token_redacts_bot_token():
    token = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
    masked = mask_token(token)
    assert token not in masked
    assert masked == "1234567890:***"


def test_mask_session_redacts_opaque_blob():
    session = "1BQANOTEz" + ("A" * 120)
    masked = mask_session(session)
    assert session not in masked
    assert masked.startswith("***1BQA")


def test_mask_proxy_url_redacts_credentials():
    url = "socks5://proxyuser:proxypass@127.0.0.1:1080"
    masked = mask_proxy_url(url)
    assert "proxypass" not in masked
    assert "proxyuser:***@" in masked


def test_mask_connection_url_redacts_postgresql_password():
    url = "postgresql+asyncpg://botuser:StrongPassword123@localhost:5432/musicbot_db"
    masked = mask_connection_url(url)
    assert "StrongPassword123" not in masked
    assert "botuser:***@" in masked
    assert "musicbot_db" in masked


def test_mask_connection_url_redacts_redis_password():
    url = "redis://:SecretRedisPass@localhost:6379/0"
    masked = mask_connection_url(url)
    assert "SecretRedisPass" not in masked
    assert "localhost:6379" in masked


def test_mask_authorization_header_redacts_bearer():
    assert mask_authorization_header("Bearer abc.def.ghi") == "Bearer ***"


def test_safe_subprocess_error_summary_hides_raw_stderr():
    summary = safe_subprocess_error_summary(
        1,
        b"FATAL: password authentication failed for user botuser",
        b"",
    )
    assert "password authentication failed" not in summary
    assert "exit_code=1" in summary
    assert "stderr_bytes=" in summary


def test_redact_freeform_text_masks_known_secret_and_token():
    token = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
    text = f"reload failed token={token} db=postgresql://u:pass@localhost/db"
    redacted = redact_freeform_text(text, secrets=(token,))
    assert token not in redacted
    assert "pass@" not in redacted or "u:***@" in redacted


def test_loguru_direct_modules_have_no_broken_percent_formatting():
    app_dir = ROOT / "app"
    violations: list[str] = []
    for path in sorted(app_dir.rglob("*.py")):
        if path.name not in LOGURU_DIRECT_MODULE_SUFFIXES:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for line in find_broken_loguru_percent_formatting(path.read_text(encoding="utf-8-sig")):
            violations.append(f"{rel}: {line}")
    assert not violations, "Broken loguru % formatting:\n" + "\n".join(violations)


def test_helper_otp_source_never_logs_raw_secrets():
    path = ROOT / "app" / "handlers" / "helper_otp_wizard.py"
    text = path.read_text(encoding="utf-8-sig")
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
    text = path.read_text(encoding="utf-8-sig")
    assert "mask_phone(phone)" in text
    assert "phone_masked" in text


def test_helper_otp_auth_failure_logs_phase_and_exc_class():
    from app.handlers import helper_otp_wizard as mod

    source = inspect.getsource(mod._log_auth_event)
    assert "exc_name" in source
    assert "step=" in source
    assert "phone=" in source


def test_scheduler_pg_dump_failure_does_not_log_stderr():
    path = ROOT / "app" / "scheduler.py"
    text = path.read_text(encoding="utf-8-sig")
    assert "stderr.decode()" not in text
    assert "pg_dump failed exit_code=" in text


def test_main_never_logs_token_or_hash_values():
    path = ROOT / "app" / "main.py"
    text = path.read_text(encoding="utf-8-sig")
    forbidden = [
        r"logger\.\w+\([^)]*settings\.BOT_TOKEN",
        r"logger\.\w+\([^)]*settings\.API_HASH",
        r"logger\.\w+\([^)]*settings\.DATABASE_URL",
        r"logger\.\w+\([^)]*settings\.REDIS_URL",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, text), f"Unsafe main.py log: {pattern}"


def test_main_exception_handler_uses_safe_exc_name():
    path = ROOT / "app" / "main.py"
    text = path.read_text(encoding="utf-8-sig")
    assert "safe_exc_name(exc)" in text
    assert 'logger.error("Unhandled async exception: {} — {}", msg, exc)' not in text


def test_callback_data_prefix_truncates_long_values():
    long_data = "dev:list:detail:" + ("9" * 80)
    prefix = callback_data_prefix(long_data, max_len=24)
    assert len(prefix) <= 27
    assert "..." in prefix
