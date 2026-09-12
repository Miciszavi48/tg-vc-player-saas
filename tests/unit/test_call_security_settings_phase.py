"""Call Security settings: migration, model, repository, drift head."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def test_migration_0024_metadata():
    import importlib

    mig = importlib.import_module(
        "app.database.migrations.versions.0024_call_security_settings",
    )
    assert mig.revision == "0024_call_security_settings"
    assert mig.down_revision == "0023_helper_app_identity"


def test_migration_0025_metadata():
    import importlib

    mig = importlib.import_module(
        "app.database.migrations.versions.0025_group_member_membership_tracking",
    )
    assert mig.revision == "0025_group_member_membership"
    assert mig.down_revision == "0024_call_security_settings"


def test_call_security_model_table_name():
    from app.database.models import CallSecuritySettings

    assert CallSecuritySettings.__tablename__ == "call_security_settings"


def test_clamp_membership_age_days_bounds():
    from app.repositories.call_security_repo import clamp_membership_age_days

    assert clamp_membership_age_days(-5) == 0
    assert clamp_membership_age_days(7) == 7
    assert clamp_membership_age_days(9999) == 3650


def test_call_security_model_columns_and_defaults():
    from app.database.models import CallSecuritySettings

    cols = {c.name for c in CallSecuritySettings.__table__.columns}
    assert {
        "chat_id",
        "enabled",
        "owner_access_enabled",
        "mute_incoming_enabled",
        "summary_enabled",
        "report_enabled",
        "membership_age_days",
    }.issubset(cols)
    assert CallSecuritySettings.__table__.c.membership_age_days.default.arg == 7


def test_group_member_membership_model_columns():
    from app.database.models import GroupMemberMembership

    cols = {c.name for c in GroupMemberMembership.__table__.columns}
    assert {
        "chat_id",
        "user_id",
        "joined_at",
        "first_seen_at",
        "last_seen_at",
        "left_at",
        "source",
    }.issubset(cols)


@pytest.mark.asyncio
async def test_update_toggle_field_persists_value():
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.database.models import CallSecuritySettings
    from app.repositories import call_security_repo

    existing = CallSecuritySettings(chat_id=-990024002, enabled=False)
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)),
    )
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    async def _refresh(_row):
        _row.enabled = True
        _row.updated_by = 1

    session.refresh.side_effect = _refresh

    cm = AsyncMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = None

    with patch("app.repositories.call_security_repo.async_session", return_value=cm):
        updated = await call_security_repo.update_call_security_setting(
            -990024002,
            "enabled",
            True,
            updated_by=1,
        )
    assert updated.enabled is True
    assert updated.updated_by == 1


def test_drift_required_tables_includes_call_security_settings():
    from scripts.db_schema_drift_check import REQUIRED_COLUMNS, REQUIRED_TABLES

    assert "call_security_settings" in REQUIRED_TABLES
    assert "group_member_memberships" in REQUIRED_TABLES
    assert ("call_security_settings", "membership_age_days") in REQUIRED_COLUMNS
    assert ("group_member_memberships", "joined_at") in REQUIRED_COLUMNS
