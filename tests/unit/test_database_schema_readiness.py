"""Unit tests for Alembic head detection and startup schema readiness."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.schema_readiness import (
    ALEMBIC_HEAD_ERROR,
    discover_alembic_heads,
    ensure_database_schema_ready,
    fetch_alembic_current_revision,
    should_use_create_all,
    test_mode_enabled,
    validate_alembic_revision_at_head,
)


def test_discover_alembic_heads_includes_current_head():
    heads = discover_alembic_heads()
    assert "0039_hot_seat" in heads


def test_every_revision_id_fits_alembic_version_column():
    """Alembic's ``alembic_version.version_num`` is VARCHAR(32) on PostgreSQL.

    SQLite ignores the declared length, so an over-long revision id only fails
    on a real PostgreSQL upgrade (StringDataRightTruncationError while stamping
    the revision). Keep every id inside the column width.
    """
    versions_dir = (
        Path(__file__).resolve().parents[2]
        / "app" / "database" / "migrations" / "versions"
    )
    pattern = re.compile(r"^revision(?::\s*str)?\s*=\s*[\"']([^\"']+)[\"']", re.M)
    too_long: list[tuple[str, int]] = []
    seen = 0
    for path in sorted(versions_dir.glob("*.py")):
        match = pattern.search(path.read_text(encoding="utf-8"))
        if not match:
            continue
        seen += 1
        revision_id = match.group(1)
        if len(revision_id) > 32:
            too_long.append((revision_id, len(revision_id)))
    assert seen > 0, f"no revision ids found under {versions_dir}"
    assert not too_long, f"revision ids exceed VARCHAR(32): {too_long}"


def test_should_use_create_all_sqlite():
    assert should_use_create_all("sqlite+aiosqlite:///./x.db", db_allow_create_all=False)


def test_should_use_create_all_test_mode_postgres(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "true")
    assert should_use_create_all(
        "postgresql+asyncpg://u:p@localhost/db",
        db_allow_create_all=False,
    )


def test_should_use_create_all_explicit_dev_flag():
    assert should_use_create_all(
        "postgresql+asyncpg://u:p@localhost/db",
        db_allow_create_all=True,
    )


def test_should_not_use_create_all_production_postgres(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)
    assert not should_use_create_all(
        "postgresql+asyncpg://u:p@localhost/db",
        db_allow_create_all=False,
    )


def test_validate_missing_alembic_version():
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        validate_alembic_revision_at_head(None, ["0032_fast_creat_token_pool"])


def test_validate_outdated_revision():
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        validate_alembic_revision_at_head(
            "0017_safe_hot_path_indexes",
            ["0032_fast_creat_token_pool"],
        )


def test_validate_matching_revision_passes():
    validate_alembic_revision_at_head(
        "0032_fast_creat_token_pool",
        ["0032_fast_creat_token_pool"],
    )


@pytest.mark.asyncio
async def test_ensure_schema_ready_fails_when_alembic_missing(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)
    engine = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar=lambda: False),
        ]
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None
    engine.connect.return_value = cm

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        await ensure_database_schema_ready(
            engine,
            "postgresql+asyncpg://u:p@localhost/prod",
            db_allow_create_all=False,
        )


@pytest.mark.asyncio
async def test_ensure_schema_ready_passes_when_at_head(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)
    engine = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar=lambda: True),
            MagicMock(fetchall=lambda: [("0039_hot_seat",)]),
        ]
    )
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None
    engine.connect.return_value = cm

    await ensure_database_schema_ready(
        engine,
        "postgresql+asyncpg://u:p@localhost/prod",
        db_allow_create_all=False,
    )


@pytest.mark.asyncio
async def test_ensure_schema_ready_skips_check_in_test_mode(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "true")
    engine = MagicMock()
    await ensure_database_schema_ready(
        engine,
        "postgresql+asyncpg://u:p@localhost/test",
        db_allow_create_all=False,
    )
    engine.connect.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_alembic_current_revision_none_when_table_missing():
    engine = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=MagicMock(scalar=lambda: False))
    cm = AsyncMock()
    cm.__aenter__.return_value = conn
    cm.__aexit__.return_value = None
    engine.connect.return_value = cm

    assert await fetch_alembic_current_revision(engine) is None


def test_is_test_mode_enabled(monkeypatch):
    monkeypatch.setenv("TEST_MODE", "1")
    assert test_mode_enabled()
    monkeypatch.setenv("TEST_MODE", "false")
    assert not test_mode_enabled()


def test_error_message_constant():
    assert "alembic upgrade head" in ALEMBIC_HEAD_ERROR
