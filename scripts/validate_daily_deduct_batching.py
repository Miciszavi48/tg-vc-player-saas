#!/usr/bin/env python3
"""Disposable PostgreSQL validation for daily credit deduction batching (Phase 2D-4B-2).

Mutates only test ``group_credits`` rows in a safe database (default: musicbot_disposable).
Never starts the bot or calls Telegram.

Usage (PowerShell)::

    python scripts/validate_daily_deduct_batching.py

Auto-loads a safe URL from ``TEST_DATABASE_URL``, safe ``DATABASE_URL``, or
``app/config.env.disposable`` (see ``scripts/safe_db_url.py``).
Optional: ``. .\\scripts\\load_disposable_env.ps1`` to export env vars for Alembic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DISPOSABLE_ENV_FILE = REPO_ROOT / "app" / "config.env.disposable"
MIGRATIONS_VERSIONS_DIR = REPO_ROOT / "app" / "database" / "migrations" / "versions"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import safe_db_url  # noqa: E402

EXPECTED_ALEMBIC_HEAD = "0039_hot_seat"
REQUIRED_DISPOSABLE_DB = "musicbot_disposable"
SAFE_DB_MARKERS = ("disposable", "test", "dev", "local", "staging")

TEST_CHAT_ID_MIN = -990000000100
TEST_CHAT_ID_MAX = -990000000000


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    """Disposable ``group_credits`` row inserted for validation."""

    chat_id: int
    chat_type: str
    credit_days: int
    status: str


FIXTURE_SPECS: tuple[FixtureSpec, ...] = (
    FixtureSpec(-990000000001, "group", 3, "active"),
    FixtureSpec(-990000000002, "channel", 1, "active"),
    FixtureSpec(-990000000005, "group", 2, "active"),
    FixtureSpec(-990000000003, "group", 0, "active"),
    FixtureSpec(-990000000004, "group", 5, "expired"),
)

DEDUCTIBLE_CHAT_IDS = frozenset(
    spec.chat_id for spec in FIXTURE_SPECS if spec.status == "active" and spec.credit_days > 0
)


@dataclass(slots=True)
class RowSnapshot:
    """Lightweight row state for assertions."""

    chat_id: int
    credit_days: int
    status: str
    last_daily_deducted_on: date | None


@dataclass(slots=True)
class ValidationReport:
    """Aggregated PASS/FAIL outcome."""

    status: str = "FAIL"
    exit_code: int = 1
    database_name: str | None = None
    sanitized_url: str | None = None
    alembic_current: str | None = None
    legacy_first_count: int | None = None
    legacy_second_count: int | None = None
    batched_first_count: int | None = None
    batched_second_count: int | None = None
    batch_size: int = 2
    errors: list[str] = field(default_factory=list)
    cleanup_ok: bool = True


def load_disposable_env_defaults() -> None:
    """Deprecated: URL resolution uses ``scripts.safe_db_url.load_safe_database_url``."""
    return None


def sanitize_database_url(url: str) -> str:
    """Return a display-safe database URL with password redacted."""
    return safe_db_url.redact_database_url(url)


def database_name_from_url(url: str) -> str | None:
    """Extract database name from a SQLAlchemy URL."""
    return safe_db_url.database_name_from_url(url)


def is_test_chat_id(chat_id: int) -> bool:
    """Return True when ``chat_id`` is in the disposable validation namespace."""
    return TEST_CHAT_ID_MIN <= chat_id <= TEST_CHAT_ID_MAX


def validate_database_safety(
    db_name: str | None,
    *,
    allow_safe_non_disposable: bool,
) -> tuple[bool, str]:
    """Enforce safe DB name for mutating validation."""
    if not db_name:
        return False, "could not determine database name from URL"

    lowered = db_name.lower()
    if lowered == REQUIRED_DISPOSABLE_DB:
        return True, f"database is {REQUIRED_DISPOSABLE_DB}"

    if allow_safe_non_disposable:
        for marker in SAFE_DB_MARKERS:
            if marker in lowered:
                return True, f"database name contains '{marker}' (--allow-safe-non-disposable)"
        return (
            False,
            "database name is not musicbot_disposable and lacks safe marker "
            "(use --allow-safe-non-disposable only for non-production test DBs)",
        )

    return (
        False,
        f"database must be exactly {REQUIRED_DISPOSABLE_DB!r} "
        f"(got {db_name!r}); pass --allow-safe-non-disposable for other safe test DBs",
    )


def discover_alembic_heads(versions_dir: Path) -> list[str]:
    """Return migration revision ids that are heads (copied from drift checker pattern)."""
    import re

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
        if re.search(r"^down_revision\s*=\s*None", content, re.MULTILINE):
            down_revision = None
        else:
            for pattern in down_patterns:
                match = pattern.search(content)
                if match:
                    down_revision = match.group(1)
                    break
        revisions[revision_id] = down_revision

    referenced = {down for down in revisions.values() if down}
    return sorted(rev for rev in revisions if rev not in referenced)


async def fetch_alembic_current(session: AsyncSession) -> str | None:
    """Read single ``alembic_version.version_num`` or None if missing/empty."""
    exists = (
        await session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'alembic_version'
                )
                """
            )
        )
    ).scalar()
    if not exists:
        return None

    rows = (
        await session.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num"))
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        versions = [r[0] for r in rows]
        raise RuntimeError(f"multiple alembic_version rows: {versions}")
    return str(rows[0][0])


async def cleanup_test_rows(session: AsyncSession) -> int:
    """Delete only validation-namespace ``group_credits`` rows."""
    from app.database.models import GroupCredit

    if not is_test_chat_id(TEST_CHAT_ID_MIN) or not is_test_chat_id(TEST_CHAT_ID_MAX):
        raise RuntimeError("test chat ID bounds are misconfigured")

    result = await session.execute(
        delete(GroupCredit).where(
            GroupCredit.chat_id >= TEST_CHAT_ID_MIN,
            GroupCredit.chat_id <= TEST_CHAT_ID_MAX,
        )
    )
    await session.commit()
    return int(result.rowcount or 0)


async def insert_fixtures(session: AsyncSession) -> None:
    """Insert fresh disposable fixture rows."""
    from app.database.models import GroupCredit

    for spec in FIXTURE_SPECS:
        session.add(
            GroupCredit(
                chat_id=spec.chat_id,
                chat_type=spec.chat_type,
                credit_days=spec.credit_days,
                status=spec.status,
                last_daily_deducted_on=None,
            )
        )
    await session.commit()


async def fetch_test_snapshots(session: AsyncSession) -> dict[int, RowSnapshot]:
    """Load current state for all test-namespace rows."""
    from app.database.models import GroupCredit

    result = await session.execute(
        select(GroupCredit).where(
            GroupCredit.chat_id >= TEST_CHAT_ID_MIN,
            GroupCredit.chat_id <= TEST_CHAT_ID_MAX,
        )
    )
    rows = result.scalars().all()
    return {
        row.chat_id: RowSnapshot(
            chat_id=row.chat_id,
            credit_days=int(row.credit_days),
            status=str(row.status),
            last_daily_deducted_on=row.last_daily_deducted_on,
        )
        for row in rows
    }


def assert_after_first_deduct(snapshots: dict[int, RowSnapshot], today: date) -> list[str]:
    """Validate test rows after the first deduction of a run."""
    errors: list[str] = []
    expected = {
        -990000000001: (2, "active", today),
        -990000000002: (0, "expired", today),
        -990000000005: (1, "active", today),
        -990000000003: (0, "active", None),
        -990000000004: (5, "expired", None),
    }
    for chat_id, (days, status, deducted_on) in expected.items():
        row = snapshots.get(chat_id)
        if row is None:
            errors.append(f"missing test row chat_id={chat_id}")
            continue
        if row.credit_days != days:
            errors.append(f"chat_id={chat_id}: expected credit_days={days}, got {row.credit_days}")
        if row.status != status:
            errors.append(f"chat_id={chat_id}: expected status={status!r}, got {row.status!r}")
        if row.last_daily_deducted_on != deducted_on:
            errors.append(
                f"chat_id={chat_id}: expected last_daily_deducted_on={deducted_on}, "
                f"got {row.last_daily_deducted_on}"
            )
    return errors


def assert_unchanged(
    before: dict[int, RowSnapshot],
    after: dict[int, RowSnapshot],
) -> list[str]:
    """Ensure second same-day run did not mutate test rows."""
    errors: list[str] = []
    for chat_id, prev in before.items():
        curr = after.get(chat_id)
        if curr is None:
            errors.append(f"missing test row after second run chat_id={chat_id}")
            continue
        if curr != prev:
            errors.append(
                f"chat_id={chat_id} changed on second run: before={prev!r} after={curr!r}"
            )
    return errors


def count_test_rows_deducted_today(
    before: dict[int, RowSnapshot],
    after: dict[int, RowSnapshot],
    today: date,
) -> int:
    """Count test rows that received ``last_daily_deducted_on=today`` this run."""
    count = 0
    for chat_id in DEDUCTIBLE_CHAT_IDS:
        prev = before.get(chat_id)
        curr = after.get(chat_id)
        if curr is None:
            continue
        if curr.last_daily_deducted_on == today and (
            prev is None or prev.last_daily_deducted_on != today
        ):
            count += 1
    return count


async def run_daily_deduct(*, batching_enabled: bool, batch_size: int) -> int:
    """Invoke ``CreditService.daily_deduct_all`` with explicit batching settings."""
    from app.config.settings import settings
    from app.services.credit_service import CreditService

    settings.DAILY_DEDUCT_BATCHING_ENABLED = batching_enabled
    settings.DAILY_DEDUCT_BATCH_SIZE = batch_size
    return await CreditService.daily_deduct_all()


async def run_validation(
    database_url: str,
    *,
    batch_size: int,
    keep_test_rows: bool,
) -> ValidationReport:
    """Execute legacy and batched validation against a safe disposable database."""
    from app.database.models import GroupCredit  # noqa: F401 — ensure model registry

    report = ValidationReport(batch_size=batch_size)
    report.database_name = database_name_from_url(database_url)
    report.sanitized_url = sanitize_database_url(database_url)

    engine = create_async_engine(database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            current = await fetch_alembic_current(session)
            report.alembic_current = current
            heads = discover_alembic_heads(MIGRATIONS_VERSIONS_DIR)
            if current != EXPECTED_ALEMBIC_HEAD:
                report.errors.append(
                    f"alembic current {current!r} != expected {EXPECTED_ALEMBIC_HEAD!r}; "
                    "run: cd app && alembic upgrade head"
                )
                return report
            if heads != [EXPECTED_ALEMBIC_HEAD]:
                report.errors.append(f"local migration heads {heads!r} unexpected")

        deleted = 0
        async with session_factory() as session:
            deleted = await cleanup_test_rows(session)
        print(f"Pre-clean removed {deleted} prior test row(s).")

        today = date.today()

        # --- Legacy path ---
        async with session_factory() as session:
            await insert_fixtures(session)
        before_legacy = await _snapshots(session_factory)
        report.legacy_first_count = await run_daily_deduct(
            batching_enabled=False, batch_size=batch_size
        )
        after_legacy = await _snapshots(session_factory)
        report.errors.extend(assert_after_first_deduct(after_legacy, today))
        if count_test_rows_deducted_today(before_legacy, after_legacy, today) != len(
            DEDUCTIBLE_CHAT_IDS
        ):
            report.errors.append(
                "legacy run: not all deductible test rows were stamped today "
                f"(expected {len(DEDUCTIBLE_CHAT_IDS)})"
            )

        report.legacy_second_count = await run_daily_deduct(
            batching_enabled=False, batch_size=batch_size
        )
        after_legacy_2 = await _snapshots(session_factory)
        report.errors.extend(assert_unchanged(after_legacy, after_legacy_2))
        if count_test_rows_deducted_today(after_legacy, after_legacy_2, today) > 0:
            report.errors.append("legacy second run deducted test-namespace rows again")

        async with session_factory() as session:
            await cleanup_test_rows(session)

        # --- Batched path ---
        async with session_factory() as session:
            await insert_fixtures(session)
        before_batched = await _snapshots(session_factory)
        report.batched_first_count = await run_daily_deduct(
            batching_enabled=True, batch_size=batch_size
        )
        after_batched = await _snapshots(session_factory)
        report.errors.extend(assert_after_first_deduct(after_batched, today))
        if count_test_rows_deducted_today(before_batched, after_batched, today) != len(
            DEDUCTIBLE_CHAT_IDS
        ):
            report.errors.append(
                "batched run: not all deductible test rows were stamped today "
                f"(expected {len(DEDUCTIBLE_CHAT_IDS)})"
            )

        report.batched_second_count = await run_daily_deduct(
            batching_enabled=True, batch_size=batch_size
        )
        after_batched_2 = await _snapshots(session_factory)
        report.errors.extend(assert_unchanged(after_batched, after_batched_2))
        if count_test_rows_deducted_today(after_batched, after_batched_2, today) > 0:
            report.errors.append("batched second run deducted test-namespace rows again")

        if not report.errors:
            report.status = "PASS"
            report.exit_code = 0
    finally:
        if not keep_test_rows:
            try:
                async with session_factory() as session:
                    removed = await cleanup_test_rows(session)
                print(f"Cleanup removed {removed} test row(s).")
            except Exception as exc:
                report.cleanup_ok = False
                report.errors.append(f"CLEANUP FAILED: {exc}")
                report.status = "FAIL"
                report.exit_code = 1
        await engine.dispose()

    return report


async def _snapshots(session_factory) -> dict[int, RowSnapshot]:
    async with session_factory() as session:
        return await fetch_test_snapshots(session)


def print_report(report: ValidationReport) -> None:
    """Print human-readable PASS/FAIL summary."""
    print("=== Daily deduct batching validation (disposable) ===")
    print(f"URL: {report.sanitized_url}")
    print(f"Database: {report.database_name}")
    print(f"Alembic current: {report.alembic_current}")
    print(f"Batch size (batched phase): {report.batch_size}")
    print(f"Legacy first/second count: {report.legacy_first_count} / {report.legacy_second_count}")
    print(
        f"Batched first/second count: {report.batched_first_count} / {report.batched_second_count}"
    )
    print(f"Cleanup OK: {report.cleanup_ok}")
    if report.errors:
        print("Errors:")
        for err in report.errors:
            print(f"  - {err}")
    print(f"Status: {report.status}")


def build_json_report(report: ValidationReport) -> dict[str, Any]:
    """Serialize report for ``--json``."""
    return asdict(report)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Validate legacy vs batched daily deduction on a disposable PostgreSQL DB.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            "Override safe DB URL (default: TEST_DATABASE_URL, safe DATABASE_URL, "
            "app/config.env.disposable, app/config.env.test)"
        ),
    )
    parser.add_argument("--batch-size", type=int, default=2, help="Batched path batch size")
    parser.add_argument(
        "--keep-test-rows",
        action="store_true",
        help="Do not delete test rows after validation (debug only)",
    )
    parser.add_argument(
        "--allow-safe-non-disposable",
        action="store_true",
        help="Allow safe non-disposable DB names (test/dev/staging/local/disposable substring)",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    args = parse_args(argv)

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    try:
        resolved = safe_db_url.load_safe_database_url(cli_url=args.database_url)
    except safe_db_url.SafeDbUrlError as exc:
        print(f"FAIL: {exc}")
        return 1

    database_url = resolved.url
    db_name = resolved.db_name
    os.environ["DATABASE_URL"] = database_url
    os.environ["TEST_DATABASE_URL"] = database_url
    try:
        from app.config.settings import settings

        settings.DATABASE_URL = database_url
    except Exception:
        pass
    print(f"DB source: {resolved.source}")
    safe, reason = validate_database_safety(
        db_name,
        allow_safe_non_disposable=args.allow_safe_non_disposable,
    )
    if not safe:
        print(f"FAIL: unsafe database: {reason}")
        return 1

    print(f"DB safety: {reason}")
    print(f"URL: {resolved.redacted_url}")

    try:
        report = asyncio.run(
            run_validation(
                database_url,
                batch_size=max(1, args.batch_size),
                keep_test_rows=args.keep_test_rows,
            )
        )
    except Exception as exc:
        print(f"FAIL: validation aborted: {exc}")
        return 1

    if args.json_output:
        print(json.dumps(build_json_report(report), indent=2))
    else:
        print_report(report)

    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
