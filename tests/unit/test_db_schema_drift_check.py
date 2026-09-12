"""Unit tests for scripts/db_schema_drift_check.py (no live PostgreSQL required)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "db_schema_drift_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("db_schema_drift_check", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["db_schema_drift_check"] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def test_sanitize_database_url_redacts_password():
    url = "postgresql+asyncpg://musicbot:secret@localhost:5432/musicbot_dev"
    safe = mod.sanitize_database_url(url)
    assert "secret" not in safe
    assert "****" in safe
    assert "musicbot_dev" in safe


def test_is_safe_database_name_accepts_dev():
    ok, _ = mod.is_safe_database_name("musicbot_dev", allow_non_test_db=False)
    assert ok is True


def test_is_safe_database_name_rejects_production_like():
    ok, reason = mod.is_safe_database_name("musicbot_db", allow_non_test_db=False)
    assert ok is False
    assert "allow-non-test-db" in reason


def test_is_safe_database_name_override():
    ok, _ = mod.is_safe_database_name("musicbot_db", allow_non_test_db=True)
    assert ok is True


def test_discover_alembic_heads_single_head():
    heads = mod.discover_alembic_heads(mod.MIGRATIONS_VERSIONS_DIR)
    assert heads == ["0039_hot_seat"]


def test_required_indexes_include_call_reports_helper_fk_index():
    assert "idx_call_reports_helper_account_id" in mod.REQUIRED_INDEXES
    assert "idx_playback_states_helper" in mod.REQUIRED_INDEXES
    assert "idx_playback_states_last_update" in mod.REQUIRED_INDEXES
    assert "idx_start_custom_messages_scope_category_active" in mod.REQUIRED_INDEXES
    assert "idx_start_custom_messages_source" in mod.REQUIRED_INDEXES
    assert "idx_start_button_configs_scope" in mod.REQUIRED_INDEXES


def test_required_tables_include_start_customization_tables():
    assert "start_customization_messages" in mod.REQUIRED_TABLES
    assert "start_button_configs" in mod.REQUIRED_TABLES
    assert "start_style_configs" in mod.REQUIRED_TABLES


def test_required_columns_include_start_customization_contract():
    required = set(mod.REQUIRED_COLUMNS)
    assert ("start_customization_messages", "scope_owner_user_id") in required
    assert ("start_customization_messages", "category") in required
    assert ("start_button_configs", "slot_key") in required
    assert ("start_button_configs", "color_token") in required
    assert ("start_style_configs", "style_mode") in required


def test_evaluate_credit_history_drift_ok_flat():
    level, notes = mod.evaluate_credit_history_drift(
        credit_history_exists=True,
        credit_history_relkind="r",
        credit_history_pk_columns=["id"],
        partitioned_table_exists=False,
        partitioned_table_row_count=None,
        monthly_partitions=[],
    )
    assert level == "WARNING"
    assert any("flat table" in n for n in notes)


def test_evaluate_credit_history_drift_ok_canonical_partitioned_only():
    level, notes = mod.evaluate_credit_history_drift(
        credit_history_exists=True,
        credit_history_relkind="p",
        credit_history_pk_columns=["id", "operated_at"],
        partitioned_table_exists=False,
        partitioned_table_row_count=None,
        monthly_partitions=[],
    )
    assert level == "WARNING"
    assert any("ORM expects PK" in n for n in notes)


def test_evaluate_credit_history_drift_critical_missing_table():
    level, notes = mod.evaluate_credit_history_drift(
        credit_history_exists=False,
        credit_history_relkind=None,
        credit_history_pk_columns=[],
        partitioned_table_exists=False,
        partitioned_table_row_count=None,
        monthly_partitions=[],
    )
    assert level == "CRITICAL"
    assert "missing" in notes[0]


def test_evaluate_credit_history_drift_critical_dual_tables():
    level, notes = mod.evaluate_credit_history_drift(
        credit_history_exists=True,
        credit_history_relkind="p",
        credit_history_pk_columns=["id", "operated_at"],
        partitioned_table_exists=True,
        partitioned_table_row_count=0,
        monthly_partitions=["credit_history_y2026m01"],
    )
    assert level == "CRITICAL"
    assert notes


def test_evaluate_credit_history_drift_critical_orphan_has_rows():
    level, notes = mod.evaluate_credit_history_drift(
        credit_history_exists=True,
        credit_history_relkind="p",
        credit_history_pk_columns=["id", "operated_at"],
        partitioned_table_exists=True,
        partitioned_table_row_count=3,
        monthly_partitions=[],
    )
    assert level == "CRITICAL"
    assert any("manual merge" in n for n in notes)


def test_evaluate_credit_history_drift_warning_unexpected_monthly_partitions():
    level, notes = mod.evaluate_credit_history_drift(
        credit_history_exists=True,
        credit_history_relkind="p",
        credit_history_pk_columns=["id", "operated_at"],
        partitioned_table_exists=False,
        partitioned_table_row_count=None,
        monthly_partitions=["credit_history_y2026m01"],
    )
    assert level == "WARNING"
    assert any("unexpected" in n for n in notes)


def test_aggregate_status_fail_on_missing_table():
    result = mod.CheckResult()
    result.missing_tables.append("global_bans")
    result.failures.append("missing table: global_bans")
    status, code = mod.aggregate_status(result)
    assert status == "FAIL"
    assert code == 1


def test_aggregate_status_warn_on_missing_index_only():
    result = mod.CheckResult()
    result.missing_indexes.append("idx_broadcasts_pending_run_at")
    result.warnings.append("missing index: idx_broadcasts_pending_run_at")
    status, code = mod.aggregate_status(result)
    assert status == "WARN"
    assert code == 0


def test_aggregate_status_pass_on_credit_history_warning_only():
    result = mod.CheckResult()
    result.credit_history = {"drift_level": "WARNING"}
    result.warnings.append("credit_history: ORM PK deferred")
    status, code = mod.aggregate_status(result)
    assert status == "WARN"
    assert code == 0


def test_build_json_report_shape():
    result = mod.CheckResult(
        sanitized_url="postgresql+asyncpg://u:****@localhost/db",
        alembic_current="0018_credit_hist_orphan",
        alembic_expected_heads=["0018_credit_hist_orphan"],
        alembic_ok=True,
        credit_history={"drift_level": "WARNING"},
        status="WARN",
        exit_code=0,
    )
    payload = mod.build_json_report(result)
    assert payload["safe_database_url"] == result.sanitized_url
    assert payload["alembic_current"] == "0018_credit_hist_orphan"
    assert payload["alembic_expected_head"] == ["0018_credit_hist_orphan"]
    assert payload["credit_history"]["drift_level"] == "WARNING"
    assert payload["status"] == "WARN"
    json.dumps(payload)


@pytest.mark.asyncio
async def test_run_checks_sqlite_skips_deep(monkeypatch):
    url = "sqlite+aiosqlite:///./test_schema_drift_check.db"
    result = await mod.run_checks(url, allow_non_test_db=False)
    assert result.deep_checks_skipped is True
    assert result.dialect.startswith("sqlite")
    assert result.exit_code == 0
    assert "PostgreSQL-only" in " ".join(result.warnings)


@pytest.mark.asyncio
async def test_run_checks_refuses_unsafe_db_name():
    url = "postgresql+asyncpg://u:p@localhost/production_musicbot"
    result = await mod.run_checks(url, allow_non_test_db=False)
    assert result.exit_code == 2
    assert any("allow-non-test-db" in f for f in result.failures)
