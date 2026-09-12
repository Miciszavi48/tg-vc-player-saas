"""Phase B3: default_media_type migration and model column."""
from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def test_migration_0020_module_metadata():
    mod = importlib.import_module(
        "app.database.migrations.versions.0020_chat_default_media_type",
    )
    assert mod.revision == "0020_chat_default_media_type"
    assert mod.down_revision == "0019_daily_deduct_idem"


def test_chat_settings_model_has_default_media_type_column():
    from app.database.models import ChatSettings

    cols = {c.name for c in ChatSettings.__table__.columns}
    assert "default_media_type" in cols


def test_discover_alembic_head_is_current():
    from app.database.schema_readiness import discover_alembic_heads

    heads = discover_alembic_heads()
    assert heads == ["0039_hot_seat"]


def test_migration_upgrade_adds_column_with_audio_default():
    source = Path(
        "app/database/migrations/versions/0020_chat_default_media_type.py"
    ).read_text(encoding="utf-8")
    assert "op.add_column" in source
    assert '"default_media_type"' in source or "'default_media_type'" in source
    assert 'server_default="audio"' in source


def test_migration_downgrade_drops_column():
    source = Path(
        "app/database/migrations/versions/0020_chat_default_media_type.py"
    ).read_text(encoding="utf-8")
    assert 'op.drop_column("chat_settings", "default_media_type")' in source


def test_drift_checker_requires_default_media_type_column():
    import importlib.util
    import sys
    from pathlib import Path

    script_path = Path("scripts/db_schema_drift_check.py")
    spec = importlib.util.spec_from_file_location("db_schema_drift_check", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_schema_drift_check"] = mod
    spec.loader.exec_module(mod)
    assert ("chat_settings", "default_media_type") in mod.REQUIRED_COLUMNS


def test_normalize_default_media_type_invalid_falls_back_to_audio():
    from app.services.media_capability_service import normalize_default_media_type

    assert normalize_default_media_type(None) == "audio"
    assert normalize_default_media_type("") == "audio"
    assert normalize_default_media_type("bogus") == "audio"
    assert normalize_default_media_type("video") == "video"
    assert normalize_default_media_type("VIDEO") == "video"
