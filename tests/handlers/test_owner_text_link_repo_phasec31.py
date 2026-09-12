"""Phase C3-1: owner_text_links migration, repo, and service (schema + fallback only)."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

_EXPECTED_HEAD = "0022_owner_text_links"
_CURRENT_HEAD = "0039_hot_seat"
_DOWN_REVISION = "0021_now_playing_flags"
_REQUIRED_COLUMNS = frozenset(
    {"id", "owner_user_id", "key", "kind", "value", "created_at", "updated_at", "updated_by"},
)

_OWNER_A = 900_001
_OWNER_B = 900_002
_FIELD_KEY = "start_text"
_LINK_KEY = "developer_link"

_EXPECTED_CALLBACK_SNAPSHOT = {
    "DEV_TEXT_FIELD_PREFIX": "dev:text:f:",
    "DEV_TEXT_SET_TEXT_PREFIX": "dev:text:txt:",
    "DEV_TEXT_SET_MEDIA_PREFIX": "dev:text:med:",
    "DEV_TEXT_CLEAR_PREFIX": "dev:text:clr:",
    "OWN_TEXT_FIELD_PREFIX": "own:text:f:",
    "OWN_TEXT_SET_TEXT_PREFIX": "own:text:txt:",
    "OWN_TEXT_SET_MEDIA_PREFIX": "own:text:med:",
    "OWN_TEXT_CLEAR_PREFIX": "own:text:clr:",
    "OWN_TEXT_PREVIEW_PREFIX": "own:text:prv:",
}


def test_migration_0022_module_metadata():
    mod = importlib.import_module(
        "app.database.migrations.versions.0022_owner_text_links",
    )
    assert mod.revision == _EXPECTED_HEAD
    assert mod.down_revision == _DOWN_REVISION


def test_migration_0022_down_revision_in_file():
    source = Path(
        "app/database/migrations/versions/0022_owner_text_links.py",
    ).read_text(encoding="utf-8")
    assert f'down_revision = "{_DOWN_REVISION}"' in source
    assert 'revision = "0022_owner_text_links"' in source


def test_owner_text_link_model_table_name():
    from app.database.models import OwnerTextLink

    assert OwnerTextLink.__tablename__ == "owner_text_links"


def test_owner_text_link_model_required_columns():
    from app.database.models import OwnerTextLink

    cols = {c.name for c in OwnerTextLink.__table__.columns}
    assert _REQUIRED_COLUMNS.issubset(cols)


@pytest.mark.asyncio
async def test_set_owner_text_link_creates_override():
    from app.repositories import owner_text_link_repo

    row = await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        "owner hello",
    )
    assert row.owner_user_id == _OWNER_A
    assert row.key == _FIELD_KEY
    assert row.kind == "text"
    assert row.value == "owner hello"


@pytest.mark.asyncio
async def test_set_owner_text_link_upserts_same_owner_key():
    from app.repositories import owner_text_link_repo

    await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        "first",
    )
    row = await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        "second",
    )
    fetched = await owner_text_link_repo.get_owner_text_link(_OWNER_A, _FIELD_KEY)
    assert fetched is not None
    assert fetched.id == row.id
    assert fetched.value == "second"


@pytest.mark.asyncio
async def test_two_owners_same_key_are_isolated():
    from app.repositories import owner_text_link_repo

    await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        "owner-a",
    )
    await owner_text_link_repo.set_owner_text_link(
        _OWNER_B,
        _FIELD_KEY,
        "text",
        "owner-b",
    )
    row_a = await owner_text_link_repo.get_owner_text_link(_OWNER_A, _FIELD_KEY)
    row_b = await owner_text_link_repo.get_owner_text_link(_OWNER_B, _FIELD_KEY)
    assert row_a is not None and row_a.value == "owner-a"
    assert row_b is not None and row_b.value == "owner-b"


@pytest.mark.asyncio
async def test_clear_owner_text_link_deletes_row():
    from app.repositories import owner_text_link_repo

    await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        "to-delete",
    )
    deleted = await owner_text_link_repo.clear_owner_text_link(_OWNER_A, _FIELD_KEY)
    assert deleted is True
    assert await owner_text_link_repo.get_owner_text_link(_OWNER_A, _FIELD_KEY) is None


@pytest.mark.asyncio
async def test_clear_owner_text_link_missing_returns_false():
    from app.repositories import owner_text_link_repo

    deleted = await owner_text_link_repo.clear_owner_text_link(_OWNER_A, "missing_key_xyz")
    assert deleted is False


@pytest.mark.asyncio
async def test_list_owner_text_links_scoped_to_owner():
    from app.repositories import owner_text_link_repo

    await owner_text_link_repo.set_owner_text_link(_OWNER_A, _FIELD_KEY, "text", "a1")
    await owner_text_link_repo.set_owner_text_link(_OWNER_A, "about_text", "text", "a2")
    await owner_text_link_repo.set_owner_text_link(_OWNER_B, _FIELD_KEY, "text", "b1")

    rows = await owner_text_link_repo.list_owner_text_links(_OWNER_A)
    keys = {row.key for row in rows}
    owner_ids = {row.owner_user_id for row in rows}
    assert keys == {_FIELD_KEY, "about_text"}
    assert owner_ids == {_OWNER_A}


@pytest.mark.asyncio
async def test_effective_read_returns_owner_override():
    from app.repositories import owner_text_link_repo
    from app.services import owner_text_link_service

    await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        "owner-only",
    )
    value = await owner_text_link_service.get_effective_text_link(
        _FIELD_KEY,
        owner_user_id=_OWNER_A,
    )
    assert value.source == "owner"
    assert value.mode == "text"
    assert value.raw == "owner-only"
    assert value.owner_user_id == _OWNER_A


@pytest.mark.asyncio
async def test_effective_read_falls_back_to_global_bot_setting():
    from app.repositories import settings_repo
    from app.services import owner_text_link_service

    global_key = "helper_start_text"
    await settings_repo.set_bot_setting(global_key, "global fallback")
    value = await owner_text_link_service.get_effective_text_link(
        global_key,
        owner_user_id=900_099,
    )
    assert value.source == "global"
    assert value.mode == "text"
    assert value.raw == "global fallback"


@pytest.mark.asyncio
async def test_effective_read_returns_empty_when_missing():
    from app.services import owner_text_link_service

    value = await owner_text_link_service.get_effective_text_link(
        "nonexistent_field_key_xyz",
        owner_user_id=_OWNER_A,
    )
    assert value.source == "empty"
    assert value.mode == "empty"
    assert value.raw is None


@pytest.mark.asyncio
async def test_media_json_value_does_not_crash():
    from app.repositories import owner_text_link_repo
    from app.services import owner_text_link_service
    from app.services.texts_links_ui import encode_media_value

    media_raw = encode_media_value("AgACAgIAAxkBAAI", "caption")
    await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        media_raw,
    )
    value = await owner_text_link_service.get_effective_text_link(
        _FIELD_KEY,
        owner_user_id=_OWNER_A,
    )
    assert value.mode == "media"
    assert value.raw == media_raw


@pytest.mark.asyncio
async def test_invalid_json_treated_as_text_without_crash():
    from app.repositories import owner_text_link_repo
    from app.services import owner_text_link_service

    broken = "{not-json"
    await owner_text_link_repo.set_owner_text_link(
        _OWNER_A,
        _FIELD_KEY,
        "text",
        broken,
    )
    value = await owner_text_link_service.get_effective_text_link(
        _FIELD_KEY,
        owner_user_id=_OWNER_A,
    )
    assert value.mode == "text"
    assert value.raw == broken


@pytest.mark.asyncio
async def test_repo_and_service_never_call_set_bot_setting_for_owner_override():
    from app.repositories import owner_text_link_repo
    from app.services import owner_text_link_service

    with patch(
        "app.repositories.settings_repo.set_bot_setting",
        new_callable=AsyncMock,
    ) as mock_set:
        await owner_text_link_repo.set_owner_text_link(
            _OWNER_A,
            _FIELD_KEY,
            "text",
            "owner value",
        )
        await owner_text_link_service.set_owner_override(
            _OWNER_A,
            _LINK_KEY,
            "link",
            "https://t.me/example",
        )
        await owner_text_link_service.clear_owner_override(_OWNER_A, _FIELD_KEY)
        mock_set.assert_not_called()


def test_callback_data_unchanged():
    from app.utils.ui import CB

    for key, expected in _EXPECTED_CALLBACK_SNAPSHOT.items():
        assert CB[key] == expected, f"callback {key} changed"


def test_drift_checker_tracks_owner_text_links_table():
    import importlib.util
    import sys

    script_path = Path("scripts/db_schema_drift_check.py")
    spec = importlib.util.spec_from_file_location("db_schema_drift_check", script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_schema_drift_check"] = mod
    spec.loader.exec_module(mod)
    assert "owner_text_links" in mod.REQUIRED_TABLES
    required = {(t, c) for t, c in mod.REQUIRED_COLUMNS if t == "owner_text_links"}
    assert ("owner_text_links", "owner_user_id") in required
    assert ("owner_text_links", "key") in required
    assert ("owner_text_links", "kind") in required
    assert ("owner_text_links", "value") in required


def test_discover_alembic_head_is_current():
    from app.database.schema_readiness import discover_alembic_heads

    heads = discover_alembic_heads()
    assert heads == [_CURRENT_HEAD]
