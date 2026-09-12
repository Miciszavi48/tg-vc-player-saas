"""Database schema readiness checks for bot startup (read-only; no Alembic upgrades).

Environment matrix (create_all vs Alembic head check):

| Environment                               | DB type               | create_all? | Alembic check? | Behavior                          |
| ----------------------------------------- | --------------------- | ----------- | -------------- | --------------------------------- |
| TEST_MODE=true                            | SQLite                | yes         | no             | Fast test setup                   |
| TEST_MODE=true                            | PostgreSQL            | yes         | no             | Test bootstrap (not production)   |
| DB_ALLOW_CREATE_ALL=true                  | any (incl. PostgreSQL)| yes         | warning only   | Legacy/local dev only             |
| default production-like startup           | PostgreSQL            | no          | yes            | Fail fast if not at Alembic head  |
| SQLite without TEST_MODE (unusual)        | SQLite                | yes         | no             | Implicit via driver               |
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

ALEMBIC_HEAD_ERROR = (
    "Database schema is not at Alembic head. "
    "Run `alembic upgrade head` from app/ with the correct DATABASE_URL."
)

_VERSIONS_DIR = Path(__file__).resolve().parent / "migrations" / "versions"


def test_mode_enabled() -> bool:
    """Return True when TEST_MODE is enabled for pytest or local test runs."""
    return os.getenv("TEST_MODE", "").strip().lower() in {"1", "true", "yes"}


def should_use_create_all(database_url: str, *, db_allow_create_all: bool) -> bool:
    """Return True when startup may call Base.metadata.create_all()."""
    driver = make_url(database_url).drivername
    if driver.startswith("sqlite"):
        return True
    if test_mode_enabled():
        return True
    return db_allow_create_all


def discover_alembic_heads(versions_dir: Path | None = None) -> list[str]:
    """Return migration revision ids that are heads (no child revision)."""
    versions_dir = versions_dir or _VERSIONS_DIR
    revisions: dict[str, str | None] = {}
    rev_patterns = (
        re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE),
        re.compile(r'^revision:\s*str\s*=\s*["\']([^"\']+)["\']', re.MULTILINE),
    )
    down_patterns = (
        re.compile(r'^down_revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE),
        re.compile(r'^down_revision:\s*Union\[str,\s*None\]\s*=\s*["\']([^"\']+)["\']', re.MULTILINE),
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

    if not revisions:
        return []

    referenced_as_parent = {down for down in revisions.values() if down}
    return sorted(rev for rev in revisions if rev not in referenced_as_parent)


async def fetch_alembic_current_revision(engine: AsyncEngine) -> str | None:
    """Read the current Alembic revision from alembic_version (None if missing/empty)."""
    async with engine.connect() as conn:
        table_exists = (
            await conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'alembic_version'
                    )
                    """
                )
            )
        ).scalar()
        if not table_exists:
            return None

        rows = (
            await conn.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            )
        ).fetchall()

    if not rows:
        return None
    if len(rows) > 1:
        versions = [row[0] for row in rows]
        raise RuntimeError(
            f"{ALEMBIC_HEAD_ERROR} Multiple alembic_version rows: {versions}."
        )
    return str(rows[0][0])


def validate_alembic_revision_at_head(current: str | None, expected_heads: list[str]) -> None:
    """Raise RuntimeError when current revision is missing or not at expected head(s)."""
    if not expected_heads:
        raise RuntimeError(
            f"{ALEMBIC_HEAD_ERROR} No Alembic migration heads found under {_VERSIONS_DIR}."
        )

    if current is None:
        raise RuntimeError(
            f"{ALEMBIC_HEAD_ERROR} The alembic_version table is missing or empty."
        )

    if len(expected_heads) == 1:
        expected = expected_heads[0]
        if current != expected:
            raise RuntimeError(
                f"{ALEMBIC_HEAD_ERROR} Current revision is {current!r}; "
                f"expected head {expected!r}."
            )
        return

    if current not in expected_heads:
        raise RuntimeError(
            f"{ALEMBIC_HEAD_ERROR} Current revision is {current!r}; "
            f"expected one of {expected_heads!r}."
        )


async def ensure_database_schema_ready(
    engine: AsyncEngine,
    database_url: str,
    *,
    db_allow_create_all: bool,
) -> None:
    """Verify schema readiness before seeding settings (PostgreSQL production path)."""
    if should_use_create_all(database_url, db_allow_create_all=db_allow_create_all):
        driver = make_url(database_url).drivername
        if (
            db_allow_create_all
            and not test_mode_enabled()
            and driver.startswith("postgresql")
        ):
            logger.warning(
                "DB_ALLOW_CREATE_ALL=true: Base.metadata.create_all() is enabled on "
                "PostgreSQL. Use only for local development; run Alembic in production."
            )
        return

    expected_heads = discover_alembic_heads()
    current = await fetch_alembic_current_revision(engine)
    validate_alembic_revision_at_head(current, expected_heads)
