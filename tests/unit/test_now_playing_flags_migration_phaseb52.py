"""Phase B5-2: now-playing metadata flag columns (schema only)."""
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

_EXPECTED_HEAD = "0021_now_playing_flags"
_FLAG_COLUMNS = ("show_track_id", "show_cover", "show_now_playing_text")
_LEGACY_COLUMNS = ("inline_enabled", "soundcloud_enabled", "spotify_enabled")


def _chat_settings_columns() -> set[str]:
    from app.database.models import ChatSettings

    return {c.name for c in ChatSettings.__table__.columns}


def _load_drift_module():
    import importlib.util
    import sys

    script_path = Path("scripts/db_schema_drift_check.py")
    spec = importlib.util.spec_from_file_location("db_schema_drift_check", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_schema_drift_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_migration_0021_module_metadata():
    mod = importlib.import_module(
        "app.database.migrations.versions.0021_now_playing_flags",
    )
    assert mod.revision == _EXPECTED_HEAD
    assert mod.down_revision == "0020_chat_default_media_type"


def test_discover_alembic_head_is_current():
    from app.database.schema_readiness import discover_alembic_heads

    heads = discover_alembic_heads()
    assert heads == ["0039_hot_seat"]


def test_migration_upgrade_adds_three_boolean_columns():
    source = Path(
        "app/database/migrations/versions/0021_now_playing_flags.py"
    ).read_text(encoding="utf-8")
    assert source.count("op.add_column") == 3
    for column in _FLAG_COLUMNS:
        assert column in source
    assert "server_default=sa.false()" in source
    assert source.count("server_default=sa.true()") == 2


def test_migration_defaults_for_existing_rows():
    source = Path(
        "app/database/migrations/versions/0021_now_playing_flags.py"
    ).read_text(encoding="utf-8")
    assert '"show_track_id"' in source or "'show_track_id'" in source
    assert "server_default=sa.false()" in source
    assert '"show_cover"' in source or "'show_cover'" in source
    assert '"show_now_playing_text"' in source or "'show_now_playing_text'" in source
    assert source.count("server_default=sa.true()") == 2


def test_migration_downgrade_drops_three_columns():
    source = Path(
        "app/database/migrations/versions/0021_now_playing_flags.py"
    ).read_text(encoding="utf-8")
    assert 'op.drop_column("chat_settings", "show_now_playing_text")' in source
    assert 'op.drop_column("chat_settings", "show_cover")' in source
    assert 'op.drop_column("chat_settings", "show_track_id")' in source


def test_chat_settings_model_has_metadata_flag_columns():
    cols = _chat_settings_columns()
    for column in _FLAG_COLUMNS:
        assert column in cols


def test_chat_settings_model_defaults_match_migration():
    from app.database.models import ChatSettings

    table = ChatSettings.__table__
    assert table.c.show_track_id.default.arg is False
    assert table.c.show_cover.default.arg is True
    assert table.c.show_now_playing_text.default.arg is True


@pytest.mark.asyncio
async def test_create_all_path_has_metadata_flag_defaults(db_session):
    from sqlalchemy import select

    from app.database.models import ChatSettings

    cs = ChatSettings(chat_id=-100501, chat_type="group")
    db_session.add(cs)
    await db_session.flush()

    result = await db_session.execute(
        select(ChatSettings).where(ChatSettings.chat_id == -100501)
    )
    fetched = result.scalar_one()
    assert fetched.show_track_id is False
    assert fetched.show_cover is True
    assert fetched.show_now_playing_text is True


def test_drift_checker_requires_metadata_flag_columns():
    mod = _load_drift_module()
    for column in _FLAG_COLUMNS:
        assert ("chat_settings", column) in mod.REQUIRED_COLUMNS


def test_legacy_columns_still_exist_and_are_not_repurposed():
    cols = _chat_settings_columns()
    for column in _LEGACY_COLUMNS:
        assert column in cols

    toggle_source = Path("app/handlers/group_panel.py").read_text(encoding="utf-8")
    assert 'CB["GRP_SHOW_ID"]: "show_track_id"' in toggle_source
    assert 'CB["GRP_SHOW_PHOTO"]: "show_cover"' in toggle_source
    assert 'CB["GRP_SHOW_TEXT"]: "show_now_playing_text"' in toggle_source
    assert 'CB["GRP_SHOW_ID"]: "inline_enabled"' not in toggle_source


def test_b52_schema_columns_exist_before_b53_runtime_wiring():
    """B5-2 added columns; B5-3 wires them (this file documents the split)."""
    cols = _chat_settings_columns()
    for column in _FLAG_COLUMNS:
        assert column in cols
