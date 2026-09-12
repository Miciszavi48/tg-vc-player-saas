from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_daily_deduct_batching.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_daily_deduct_batching", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["validate_daily_deduct_batching"] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def test_sanitize_database_url_redacts_password():
    url = "postgresql+asyncpg://musicbot:secret@127.0.0.1:5432/musicbot_disposable"
    safe = mod.sanitize_database_url(url)
    assert "secret" not in safe
    assert "****" in safe
    assert "musicbot_disposable" in safe


def test_safety_rejects_production_like_db_without_override():
    ok, reason = mod.validate_database_safety("musicbot_db", allow_safe_non_disposable=False)
    assert ok is False
    assert "musicbot_disposable" in reason


def test_safety_accepts_musicbot_disposable():
    ok, reason = mod.validate_database_safety("musicbot_disposable", allow_safe_non_disposable=False)
    assert ok is True
    assert "musicbot_disposable" in reason


def test_safety_accepts_dev_marker_with_override():
    ok, _ = mod.validate_database_safety("musicbot_dev", allow_safe_non_disposable=True)
    assert ok is True


def test_is_test_chat_id_only_targets_namespace():
    assert mod.is_test_chat_id(-990000000001) is True
    assert mod.is_test_chat_id(-990000000050) is True
    assert mod.is_test_chat_id(-100) is False
    assert mod.is_test_chat_id(-990000000200) is False


def test_assert_after_first_deduct_expected_shape():
    from datetime import date

    today = date(2026, 6, 5)
    snapshots = {
        -990000000001: mod.RowSnapshot(-990000000001, 2, "active", today),
        -990000000002: mod.RowSnapshot(-990000000002, 0, "expired", today),
        -990000000005: mod.RowSnapshot(-990000000005, 1, "active", today),
        -990000000003: mod.RowSnapshot(-990000000003, 0, "active", None),
        -990000000004: mod.RowSnapshot(-990000000004, 5, "expired", None),
    }
    assert mod.assert_after_first_deduct(snapshots, today) == []


def test_build_json_report_shape():
    report = mod.ValidationReport(
        status="PASS",
        exit_code=0,
        database_name="musicbot_disposable",
        sanitized_url="postgresql+asyncpg://musicbot:****@localhost/musicbot_disposable",
        alembic_current=mod.EXPECTED_ALEMBIC_HEAD,
        legacy_first_count=3,
        legacy_second_count=0,
        batched_first_count=3,
        batched_second_count=0,
        batch_size=2,
    )
    payload = mod.build_json_report(report)
    assert payload["status"] == "PASS"
    assert payload["alembic_current"] == mod.EXPECTED_ALEMBIC_HEAD
    assert payload["batch_size"] == 2
    json.dumps(payload)


@pytest.mark.asyncio
async def test_run_validation_refuses_non_head_revision(monkeypatch):
    async def _fake_fetch(_session):
        return "0018_credit_hist_orphan"

    monkeypatch.setattr(mod, "fetch_alembic_current", _fake_fetch)
    monkeypatch.setattr(mod, "discover_alembic_heads", lambda _p: [mod.EXPECTED_ALEMBIC_HEAD])

    report = await mod.run_validation(
        "postgresql+asyncpg://musicbot:devpassword@127.0.0.1:5432/musicbot_disposable",
        batch_size=2,
        keep_test_rows=True,
    )
    assert report.status == "FAIL"
    assert any(mod.EXPECTED_ALEMBIC_HEAD in err for err in report.errors)


@pytest.mark.asyncio
async def test_run_daily_deduct_sets_batching_flags(monkeypatch):
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    captured: dict[str, object] = {}

    async def _fake_daily_deduct_all():
        from app.config.settings import settings

        captured["enabled"] = settings.DAILY_DEDUCT_BATCHING_ENABLED
        captured["batch_size"] = settings.DAILY_DEDUCT_BATCH_SIZE
        return 1

    monkeypatch.setattr(
        "app.services.credit_service.CreditService.daily_deduct_all",
        _fake_daily_deduct_all,
    )

    await mod.run_daily_deduct(batching_enabled=True, batch_size=2)
    assert captured["enabled"] is True
    assert captured["batch_size"] == 2

    await mod.run_daily_deduct(batching_enabled=False, batch_size=200)
    assert captured["enabled"] is False
    assert captured["batch_size"] == 200
