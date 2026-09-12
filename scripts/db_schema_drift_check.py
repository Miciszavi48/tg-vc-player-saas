#!/usr/bin/env python3
"""Read-only PostgreSQL schema drift checker vs local Alembic migrations.

Never mutates schema or data. Uses SELECT-only introspection queries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
MIGRATIONS_VERSIONS_DIR = REPO_ROOT / "app" / "database" / "migrations" / "versions"
DISPOSABLE_ENV_FILE = REPO_ROOT / "app" / "config.env.disposable"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import safe_db_url  # noqa: E402

SAFE_DB_NAME_MARKERS = safe_db_url.SAFE_DB_NAME_MARKERS

REQUIRED_TABLES: tuple[str, ...] = (
    "users",
    "groups",
    "channels",
    "group_credits",
    "credit_history",
    "bot_settings",
    "chat_settings",
    "broadcasts",
    "force_join_channels",
    "helper_accounts",
    "helper_chat_bindings",
    "helper_events",
    "analytics_hourly",
    "media_events",
    "global_bans",
    "blacklist",
    "install_logs",
    "owner_sales",
    "owner_text_links",
    "monthly_invoices",
    "start_customization_messages",
    "start_button_configs",
    "start_style_configs",
    "call_security_settings",
    "group_member_memberships",
    "player_deputies",
    "player_vips",
)

# (table, column) — proxy_host is the real column (0009); not proxy_url.
REQUIRED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("broadcasts", "run_at"),
    ("broadcasts", "interval_hours"),
    ("broadcasts", "target_types_json"),
    ("force_join_channels", "is_active"),
    ("force_join_channels", "position"),
    ("helper_accounts", "session_fingerprint"),
    ("helper_accounts", "proxy_host"),
    ("playback_states", "source"),
    ("sudos", "can_manage_groups"),
    ("sudos", "can_manage_channels"),
    ("sudos", "can_manage_credit"),
    ("sudos", "can_remove_bot"),
    ("sudos", "can_manage_chat_settings"),
    ("sudos", "auto_admin_bypass"),
    ("sudos", "admin_title"),
    ("owners", "admin_title"),
    ("global_bans", "user_id"),
    ("media_events", "url_fingerprint"),
    ("media_events", "event_type"),
    ("media_events", "created_at"),
    ("group_credits", "last_daily_deducted_on"),
    ("chat_settings", "default_media_type"),
    ("chat_settings", "show_track_id"),
    ("chat_settings", "show_cover"),
    ("chat_settings", "show_now_playing_text"),
    ("owner_text_links", "owner_user_id"),
    ("owner_text_links", "key"),
    ("owner_text_links", "kind"),
    ("owner_text_links", "value"),
    ("monthly_invoices", "owner_user_id"),
    ("monthly_invoices", "period_start"),
    ("monthly_invoices", "period_end"),
    ("monthly_invoices", "due_at"),
    ("monthly_invoices", "amount"),
    ("monthly_invoices", "status"),
    ("monthly_invoices", "install_count"),
    ("monthly_invoices", "private_count"),
    ("monthly_invoices", "group_count"),
    ("monthly_invoices", "channel_count"),
    ("start_customization_messages", "scope_type"),
    ("start_customization_messages", "scope_owner_user_id"),
    ("start_customization_messages", "category"),
    ("start_customization_messages", "is_active"),
    ("start_customization_messages", "source_chat_id"),
    ("start_customization_messages", "source_message_id"),
    ("start_customization_messages", "text"),
    ("start_customization_messages", "caption"),
    ("start_customization_messages", "media_file_id"),
    ("start_customization_messages", "entities_json"),
    ("start_button_configs", "scope_type"),
    ("start_button_configs", "scope_owner_user_id"),
    ("start_button_configs", "slot_key"),
    ("start_button_configs", "slot_index"),
    ("start_button_configs", "custom_text"),
    ("start_button_configs", "color_token"),
    ("start_button_configs", "emoji_text"),
    ("start_button_configs", "emoji_entities_json"),
    ("start_style_configs", "scope_type"),
    ("start_style_configs", "scope_owner_user_id"),
    ("start_style_configs", "style_mode"),
    ("call_security_settings", "membership_age_days"),
    ("group_member_memberships", "chat_id"),
    ("group_member_memberships", "user_id"),
    ("group_member_memberships", "joined_at"),
    ("group_member_memberships", "first_seen_at"),
    ("group_member_memberships", "last_seen_at"),
    ("group_member_memberships", "left_at"),
    ("group_member_memberships", "source"),
    ("player_deputies", "chat_id"),
    ("player_deputies", "user_id"),
    ("player_deputies", "promoted_at"),
    ("player_vips", "expires_at"),
)

REQUIRED_INDEXES: tuple[str, ...] = (
    "uq_helper_accounts_session_fingerprint",
    "idx_broadcasts_pending_run_at",
    "idx_blacklist_active_entity",
    "idx_group_credits_active_trial_expire",
    "idx_fjc_active_position",
    "idx_global_bans_active_created",
    "idx_install_logs_chat_action_time",
    "idx_call_reports_helper_account_id",
    "idx_playback_states_helper",
    "idx_playback_states_last_update",
    "idx_owner_sales_owner_created",
    "idx_users_not_banned_last_seen",
    "idx_group_credits_status_type_days_id",
    "idx_group_credits_type_days_id",
    "idx_group_credits_daily_deduct_due",
    "ix_media_events_type_created",
    "ix_media_events_fingerprint_type",
    "ix_owner_text_links_owner_user_id",
    "ix_monthly_invoices_owner_user_id",
    "ix_monthly_invoices_due_at",
    "ix_monthly_invoices_status",
    "idx_monthly_invoices_status_due",
    "idx_monthly_invoices_owner_created",
    "idx_start_custom_messages_scope_category_active",
    "idx_start_custom_messages_source",
    "idx_start_button_configs_scope",
    "ix_group_member_memberships_chat_id",
    "ix_group_member_memberships_user_id",
    "ix_group_member_memberships_joined_at",
    "ix_player_deputies_chat_id",
    "ix_player_deputies_user_id",
    "idx_player_deputies_chat",
    "idx_player_deputies_user_chat",
)

ORM_CREDIT_HISTORY_PK_COLUMNS = ("id",)


@dataclass(slots=True)
class CheckResult:
    """Aggregated drift check outcome."""

    status: str = "PASS"
    exit_code: int = 0
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    missing_indexes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    credit_history: dict[str, Any] = field(default_factory=dict)
    alembic_current: str | None = None
    alembic_expected_heads: list[str] = field(default_factory=list)
    alembic_ok: bool | None = None
    dialect: str | None = None
    database_name: str | None = None
    sanitized_url: str | None = None
    postgres_version: str | None = None
    deep_checks_skipped: bool = False
    introspection_completed: bool = False
    tables_present: dict[str, bool] = field(default_factory=dict)
    columns_present: dict[str, bool] = field(default_factory=dict)
    indexes_present: dict[str, bool] = field(default_factory=dict)


def load_disposable_env_defaults() -> None:
    """Deprecated: URL resolution uses ``scripts.safe_db_url.load_safe_database_url``."""
    return None


def sanitize_database_url(url: str) -> str:
    """Return a display-safe database URL with credentials redacted."""
    return safe_db_url.redact_database_url(url)


def database_name_from_url(url: str) -> str | None:
    """Extract database name from a SQLAlchemy URL."""
    return safe_db_url.database_name_from_url(url)


def is_safe_database_name(db_name: str | None, *, allow_non_test_db: bool) -> tuple[bool, str]:
    """Return whether the database name is allowed for unattended checks."""
    return safe_db_url.is_safe_database_name(
        db_name,
        allow_non_test_db=allow_non_test_db,
    )


def discover_migration_revisions(versions_dir: Path) -> dict[str, str | None]:
    """Parse Alembic revision ids and down_revision from migration files."""
    revisions: dict[str, str | None] = {}
    rev_patterns = (
        re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE),
        re.compile(r'^revision:\s*str\s*=\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE),
    )
    down_patterns = (
        re.compile(r"^down_revision\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE),
        re.compile(
            r'^down_revision:\s*Union\[str,\s*None\]\s*=\s*[\'"]([^\'"]+)[\'"]',
            re.MULTILINE,
        ),
        re.compile(r"^down_revision\s*=\s*None", re.MULTILINE),
    )

    for path in sorted(versions_dir.glob("*.py")):
        content = path.read_text(encoding="utf-8")
        revision_id: str | None = None
        for pattern in rev_patterns:
            match = pattern.search(content)
            if match:
                revision_id = match.group(1)
                break
        if revision_id is None:
            continue

        down_revision: str | None = None
        if re.search(r"^down_revision\s*=\s*None", content, re.MULTILINE) or re.search(
            r"^down_revision:\s*Union\[str,\s*None\]\s*=\s*None",
            content,
            re.MULTILINE,
        ):
            down_revision = None
        else:
            for pattern in down_patterns[:2]:
                match = pattern.search(content)
                if match:
                    down_revision = match.group(1)
                    break

        revisions[revision_id] = down_revision

    return revisions


def discover_alembic_heads(versions_dir: Path) -> list[str]:
    """Return migration revision ids that are heads (no child revision)."""
    revisions = discover_migration_revisions(versions_dir)
    if not revisions:
        return []

    referenced_as_parent = {down for down in revisions.values() if down}
    heads = sorted(rev for rev in revisions if rev not in referenced_as_parent)
    return heads


def evaluate_credit_history_drift(
    *,
    credit_history_exists: bool,
    credit_history_relkind: str | None,
    credit_history_pk_columns: list[str],
    partitioned_table_exists: bool,
    partitioned_table_row_count: int | None,
    monthly_partitions: list[str],
    orm_pk_columns: tuple[str, ...] = ORM_CREDIT_HISTORY_PK_COLUMNS,
) -> tuple[str, list[str]]:
    """Classify credit_history partition drift without mutating anything."""
    notes: list[str] = []
    if not credit_history_exists:
        return "CRITICAL", ["table credit_history is missing"]

    if partitioned_table_exists:
        notes.append(
            "credit_history_partitioned exists alongside credit_history "
            "(0007 orphan; run migration 0018_credit_hist_orphan)"
        )
        rows = int(partitioned_table_row_count or 0)
        if rows > 0:
            notes.append(
                f"credit_history_partitioned has {rows} row(s); manual merge required before 0018"
            )
            return "CRITICAL", notes
        return "CRITICAL", notes

    if monthly_partitions:
        notes.append(
            f"unexpected credit_history_y% partitions ({len(monthly_partitions)}): "
            f"{', '.join(monthly_partitions[:5])}"
            + (" ..." if len(monthly_partitions) > 5 else "")
        )
        return "WARNING", notes

    pk_set = tuple(credit_history_pk_columns)
    orm_mismatch = pk_set != orm_pk_columns
    if orm_mismatch:
        notes.append(
            f"ORM expects PK {list(orm_pk_columns)} but credit_history PK is {list(pk_set)} "
            "(deferred Phase 2C-2)"
        )

    if credit_history_relkind == "p":
        notes.append("credit_history is partitioned (relkind=p); canonical writes use credit_history")
        if notes:
            return "WARNING", notes
        return "OK", []

    if credit_history_relkind == "r":
        notes.append(
            "credit_history is a flat table (legacy 0003 no-op path); "
            "partitioning not applied"
        )
        return "WARNING", notes

    if orm_mismatch:
        return "WARNING", notes

    return "OK", notes


def aggregate_status(result: CheckResult) -> tuple[str, int]:
    """Compute final PASS/WARN/FAIL label and process exit code."""
    if result.failures:
        result.status = "FAIL"
        result.exit_code = 1
        return result.status, result.exit_code

    credit_level = result.credit_history.get("drift_level", "OK")
    if credit_level == "CRITICAL":
        result.status = "FAIL"
        result.exit_code = 1
        return result.status, result.exit_code

    warn = bool(
        result.warnings
        or result.missing_indexes
        or credit_level == "WARNING"
        or result.alembic_ok is False
    )
    if warn:
        result.status = "WARN"
        result.exit_code = 0
        return result.status, result.exit_code

    result.status = "PASS"
    result.exit_code = 0
    return result.status, result.exit_code


def build_json_report(result: CheckResult) -> dict[str, Any]:
    """Serialize check outcome for --json output."""
    return {
        "safe_database_url": result.sanitized_url,
        "dialect": result.dialect,
        "database_name": result.database_name,
        "postgres_version": result.postgres_version,
        "alembic_current": result.alembic_current,
        "alembic_expected_head": result.alembic_expected_heads,
        "alembic_ok": result.alembic_ok,
        "tables": result.tables_present,
        "columns": result.columns_present,
        "indexes": result.indexes_present,
        "missing_tables": result.missing_tables,
        "missing_columns": result.missing_columns,
        "missing_indexes": result.missing_indexes,
        "credit_history": result.credit_history,
        "warnings": result.warnings,
        "failures": result.failures,
        "deep_checks_skipped": result.deep_checks_skipped,
        "introspection_completed": result.introspection_completed,
        "status": result.status,
        "exit_code": result.exit_code,
    }


async def _fetch_scalar(session: AsyncSession, sql: str, params: dict | None = None) -> Any:
    """Run a single-value SELECT."""
    row = (await session.execute(text(sql), params or {})).first()
    return row[0] if row else None


async def run_postgres_checks(session: AsyncSession, result: CheckResult) -> None:
    """Execute read-only PostgreSQL introspection."""
    result.postgres_version = await _fetch_scalar(session, "SELECT version()")

    # Alembic
    alembic_table_exists = await _fetch_scalar(
        session,
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'alembic_version'
        )
        """,
    )
    if not alembic_table_exists:
        result.alembic_ok = False
        result.failures.append("alembic_version table is missing (migrations not applied?)")
    else:
        rows = (
            await session.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))
        ).fetchall()
        versions = [r[0] for r in rows]
        if not versions:
            result.alembic_current = None
            result.alembic_ok = False
            result.failures.append("alembic_version is empty")
        elif len(versions) > 1:
            result.alembic_current = ",".join(versions)
            result.alembic_ok = False
            result.failures.append(f"multiple alembic_version rows: {versions}")
        else:
            result.alembic_current = versions[0]
            expected = result.alembic_expected_heads
            if len(expected) != 1:
                result.alembic_ok = False
                result.failures.append(f"expected exactly one local head, found {expected}")
            else:
                result.alembic_ok = result.alembic_current == expected[0]
                if not result.alembic_ok:
                    result.failures.append(
                        f"alembic revision {result.alembic_current!r} != head {expected[0]!r}"
                    )

    # Tables
    existing_tables = {
        r[0]
        for r in (
            await session.execute(
                text(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                    """
                )
            )
        ).fetchall()
    }
    for table in REQUIRED_TABLES:
        present = table in existing_tables
        result.tables_present[table] = present
        if not present:
            result.missing_tables.append(table)
            result.failures.append(f"missing table: {table}")

    # Columns
    for table, column in REQUIRED_COLUMNS:
        key = f"{table}.{column}"
        if table not in existing_tables:
            result.columns_present[key] = False
            result.missing_columns.append(key)
            continue

        row = (
            await session.execute(
                text(
                    """
                    SELECT data_type, udt_name, character_maximum_length
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = :table
                      AND column_name = :column
                    """
                ),
                {"table": table, "column": column},
            )
        ).first()
        present = row is not None
        result.columns_present[key] = present
        if not present:
            result.missing_columns.append(key)
            result.failures.append(f"missing column: {key}")
            continue

        if table == "playback_states" and column == "source":
            data_type, udt_name, _ = row
            if data_type not in {"text"} and udt_name not in {"text"}:
                result.failures.append(
                    f"playback_states.source type is {data_type}/{udt_name}, expected text (0011)"
                )
                result.columns_present[key] = False
                result.missing_columns.append(key)

    # Indexes
    existing_indexes = {
        r[0]
        for r in (
            await session.execute(
                text(
                    """
                    SELECT indexname FROM pg_indexes
                    WHERE schemaname = 'public'
                    """
                )
            )
        ).fetchall()
    }
    for index_name in REQUIRED_INDEXES:
        present = index_name in existing_indexes
        result.indexes_present[index_name] = present
        if not present:
            result.missing_indexes.append(index_name)
            result.warnings.append(f"missing index: {index_name}")

    # credit_history partition state
    ch_exists = "credit_history" in existing_tables
    ch_relkind = None
    ch_pk_cols: list[str] = []
    if ch_exists:
        ch_relkind = await _fetch_scalar(
            session,
            "SELECT relkind::text FROM pg_class WHERE relname = 'credit_history'",
        )
        ch_pk_cols = [
            r[0]
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_attribute a
                          ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                        WHERE i.indrelid = 'credit_history'::regclass
                          AND i.indisprimary
                        ORDER BY array_position(i.indkey, a.attnum)
                        """
                    )
                )
            ).fetchall()
        ]

    ch_part_exists = "credit_history_partitioned" in existing_tables
    ch_part_rows: int | None = None
    if ch_part_exists:
        ch_part_rows = int(
            (
                await session.execute(
                    text("SELECT COUNT(*) FROM credit_history_partitioned")
                )
            ).scalar()
            or 0
        )
    monthly_parts = [
        r[0]
        for r in (
            await session.execute(
                text(
                    """
                    SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public' AND tablename LIKE 'credit_history_y%'
                    ORDER BY tablename
                    """
                )
            )
        ).fetchall()
    ]

    drift_level, drift_notes = evaluate_credit_history_drift(
        credit_history_exists=ch_exists,
        credit_history_relkind=ch_relkind,
        credit_history_pk_columns=ch_pk_cols,
        partitioned_table_exists=ch_part_exists,
        partitioned_table_row_count=ch_part_rows,
        monthly_partitions=monthly_parts,
    )
    result.credit_history = {
        "credit_history_exists": ch_exists,
        "credit_history_relkind": ch_relkind,
        "credit_history_pk_columns": ch_pk_cols,
        "credit_history_partitioned_exists": ch_part_exists,
        "credit_history_partitioned_row_count": ch_part_rows,
        "monthly_partitions": monthly_parts,
        "both_tables_present": ch_exists and ch_part_exists,
        "orm_expected_pk_columns": list(ORM_CREDIT_HISTORY_PK_COLUMNS),
        "pk_mismatch": tuple(ch_pk_cols) != ORM_CREDIT_HISTORY_PK_COLUMNS,
        "drift_level": drift_level,
        "notes": drift_notes,
    }
    if drift_level == "CRITICAL":
        result.failures.extend([f"credit_history: {n}" for n in drift_notes])
    elif drift_level == "WARNING":
        result.warnings.extend([f"credit_history: {n}" for n in drift_notes])

    # create_all() risk (static guidance)
    if result.missing_indexes or result.missing_columns:
        result.warnings.append(
            "Schema has Alembic-only gaps; app startup create_all() will NOT add missing "
            "indexes/columns — run alembic upgrade head from app/ with PYTHONPATH set."
        )

    result.introspection_completed = True


async def run_checks(database_url: str, *, allow_non_test_db: bool) -> CheckResult:
    """Run all drift checks for the given database URL."""
    result = CheckResult()
    result.sanitized_url = sanitize_database_url(database_url)
    result.alembic_expected_heads = discover_alembic_heads(MIGRATIONS_VERSIONS_DIR)

    if not MIGRATIONS_VERSIONS_DIR.is_dir():
        result.failures.append(f"migrations directory not found: {MIGRATIONS_VERSIONS_DIR}")
        result.exit_code = 2
        result.status = "FAIL"
        return result

    try:
        url = make_url(database_url)
    except Exception as exc:
        result.failures.append(f"invalid DATABASE_URL: {exc}")
        result.exit_code = 2
        result.status = "FAIL"
        return result

    result.dialect = url.drivername
    result.database_name = url.database

    safe, safe_reason = is_safe_database_name(result.database_name, allow_non_test_db=allow_non_test_db)
    if not safe:
        result.failures.append(safe_reason)
        result.exit_code = 2
        result.status = "FAIL"
        return result

    if not url.drivername.startswith("postgresql"):
        result.deep_checks_skipped = True
        result.warnings.append(
            f"dialect {url.drivername!r}: deep drift checks are PostgreSQL-only; skipped."
        )
        aggregate_status(result)
        return result

    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await run_postgres_checks(session, result)
    except Exception as exc:
        result.failures.append(f"database connection or query failed: {exc}")
        result.exit_code = 2
        result.status = "FAIL"
        return result
    finally:
        await engine.dispose()

    aggregate_status(result)
    return result


def print_human_report(result: CheckResult) -> None:
    """Print human-readable report to stdout."""
    print("=== DB schema drift check (read-only) ===")
    print(f"URL: {result.sanitized_url}")
    print(f"Dialect: {result.dialect}")
    print(f"Database: {result.database_name}")
    if result.postgres_version:
        print(f"PostgreSQL: {result.postgres_version}")

    if result.deep_checks_skipped:
        print("\n[SKIP] Deep checks skipped (non-PostgreSQL).")
        print(f"Status: {result.status}")
        if result.warnings:
            print("Warnings:")
            for w in result.warnings:
                print(f"  - {w}")
        return

    if not result.introspection_completed:
        print("\n[SKIP] Database introspection did not complete.")
        if result.failures:
            print("Failures:")
            for failure in result.failures:
                print(f"  - {failure}")
        print("\n=== Summary ===")
        print(f"Status: {result.status}")
        print("\nRecommended next action:")
        print("  Fix connection/safety issues, then re-run.")
        return

    print("\n--- Alembic ---")
    print(f"Current:  {result.alembic_current}")
    print(f"Expected: {', '.join(result.alembic_expected_heads) or '(none detected)'}")
    print(f"OK: {result.alembic_ok}")

    print("\n--- Tables ---")
    missing_tables = result.missing_tables
    print(f"Missing: {len(missing_tables)}")
    for t in missing_tables:
        print(f"  - {t}")

    print("\n--- Columns ---")
    print(f"Missing: {len(result.missing_columns)}")
    for c in result.missing_columns:
        print(f"  - {c}")
    print("Note: helper proxy column is proxy_host (migration 0009), not proxy_url.")

    print("\n--- Indexes ---")
    print(f"Missing: {len(result.missing_indexes)}")
    for idx in result.missing_indexes:
        print(f"  - {idx}")

    print("\n--- credit_history ---")
    ch = result.credit_history
    for key in (
        "credit_history_exists",
        "credit_history_relkind",
        "credit_history_pk_columns",
        "credit_history_partitioned_exists",
        "monthly_partitions",
        "both_tables_present",
        "orm_expected_pk_columns",
        "pk_mismatch",
        "drift_level",
    ):
        print(f"  {key}: {ch.get(key)}")
    for note in ch.get("notes", []):
        print(f"  note: {note}")

    print("\n--- create_all() risk ---")
    print(
        "  Production startup checks Alembic head and does not use create_all(); "
        "run alembic upgrade head before start. TEST_MODE / DB_ALLOW_CREATE_ALL enable create_all."
    )

    print("\n=== Summary ===")
    print(f"Status: {result.status}")
    print(f"Missing tables:  {len(result.missing_tables)}")
    print(f"Missing columns: {len(result.missing_columns)}")
    print(f"Missing indexes: {len(result.missing_indexes)}")
    print(f"credit_history drift: {ch.get('drift_level', 'n/a')}")

    if result.failures:
        print("Failures:")
        for f in result.failures:
            print(f"  - {f}")
    if result.warnings:
        print("Warnings:")
        for w in result.warnings:
            print(f"  - {w}")

    print("\nRecommended next action:")
    if result.exit_code == 2:
        print("  Fix connection/safety issues, then re-run.")
    elif result.status == "FAIL":
        if result.alembic_ok is False:
            print("  Run: cd app && alembic upgrade head (with correct DATABASE_URL/PYTHONPATH).")
        if ch.get("drift_level") == "CRITICAL":
            print("  Review credit_history partition drift before production scale (do not ignore).")
        print("  Re-run this script after migrations.")
    elif result.status == "WARN":
        print("  Apply missing migrations/indexes; investigate credit_history warnings.")
    else:
        print("  Schema matches expected head; proceed with normal deployment checks.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL schema drift check vs local Alembic migrations.",
    )
    parser.add_argument(
        "--database-url",
        dest="database_url",
        default=None,
        help=(
            "SQLAlchemy database URL (default: safe loader — TEST_DATABASE_URL, "
            "safe DATABASE_URL, app/config.env.disposable, app/config.env.test)"
        ),
    )
    parser.add_argument(
        "--allow-non-test-db",
        action="store_true",
        help="Allow read-only checks against DB names without test/dev/staging/local/disposable",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON report on stdout (human summary still printed unless quiet)",
    )
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)
    try:
        resolved = safe_db_url.load_safe_database_url(
            cli_url=args.database_url,
            allow_non_test_db=args.allow_non_test_db,
        )
    except safe_db_url.SafeDbUrlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    database_url = resolved.url
    print(f"DB source: {resolved.source}")
    print(f"DB name: {resolved.db_name}")

    result = await run_checks(database_url, allow_non_test_db=args.allow_non_test_db)

    if args.json:
        print(json.dumps(build_json_report(result), indent=2, sort_keys=True))

    print_human_report(result)
    return result.exit_code


def main() -> int:
    """Synchronous wrapper for asyncio CLI."""
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
