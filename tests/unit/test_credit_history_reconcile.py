"""PostgreSQL integration checks for credit_history Phase 2C-1 (optional)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _disposable_database_url() -> str | None:
    """Return DATABASE_URL only when it targets musicbot_disposable."""
    for key in ("DATABASE_URL", "TEST_DATABASE_URL"):
        url = os.getenv(key, "")
        if url.startswith("postgresql") and "musicbot_disposable" in url:
            return url
    config_path = os.path.join("app", "config.env.disposable")
    if os.path.isfile(config_path):
        for line in Path(config_path).read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
                if url.startswith("postgresql") and "musicbot_disposable" in url:
                    return url
    return None


@pytest.fixture
async def disposable_engine():
    url = _disposable_database_url()
    if url is None:
        pytest.skip("musicbot_disposable DATABASE_URL not configured")
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


async def test_orphan_credit_history_partitioned_absent(disposable_engine):
    async with disposable_engine.connect() as conn:
        exists = (
            await conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'credit_history_partitioned'
                    )
                    """
                )
            )
        ).scalar()
        assert exists is False


async def test_canonical_credit_history_still_exists(disposable_engine):
    async with disposable_engine.connect() as conn:
        relkind = (
            await conn.execute(
                text(
                    "SELECT relkind::text FROM pg_class WHERE relname = 'credit_history'"
                )
            )
        ).scalar()
        assert relkind == "p"


async def test_credit_history_default_attached_to_canonical(disposable_engine):
    async with disposable_engine.connect() as conn:
        parent = (
            await conn.execute(
                text(
                    """
                    SELECT p.relname
                    FROM pg_inherits i
                    JOIN pg_class c ON c.oid = i.inhrelid
                    JOIN pg_class p ON p.oid = i.inhparent
                    WHERE c.relname = 'credit_history_default'
                    """
                )
            )
        ).scalar()
        assert parent == "credit_history"


async def test_no_orphan_monthly_partitions(disposable_engine):
    async with disposable_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public' AND tablename LIKE 'credit_history_y%'
                    """
                )
            )
        ).fetchall()
        assert rows == []
