from __future__ import annotations

import importlib.util
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database.models import (
    CallSecuritySettings,
    CallReport,
    ChatSettings,
    GroupMemberMembership,
    HelperAccount,
    PlaybackState,
    PlayerDeputy,
    PlayerVip,
    StartButtonConfig,
    StartCustomizationMessage,
    StartStyleConfig,
)
from app.utils.redis_keys import (
    credit_expired_pending_member,
    credit_key,
    credit_lock_key,
    credit_warning_sent_key,
    settings_key,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _load_helper_check_script():
    path = REPO_ROOT / "scripts" / "check_helper_otp_config.py"
    spec = importlib.util.spec_from_file_location("check_helper_otp_config", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_conftest_module():
    path = REPO_ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location("_runtime_sync_conftest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_helper_session_fingerprint_metadata_matches_migration_index() -> None:
    indexes = {idx.name: idx for idx in HelperAccount.__table__.indexes}
    index = indexes["uq_helper_accounts_session_fingerprint"]

    assert index.unique is True
    assert [col.name for col in index.columns] == ["session_fingerprint"]
    assert "ix_helper_accounts_session_fingerprint" not in indexes


def test_playback_state_helper_index_matches_spec_migration() -> None:
    indexes = {
        idx.name: tuple(col.name for col in idx.columns)
        for idx in PlaybackState.__table__.indexes
    }

    assert indexes["idx_playback_states_helper"] == ("helper_account_id",)
    assert indexes["idx_playback_states_last_update"] == ("last_update_at",)


def test_call_report_helper_index_matches_migration() -> None:
    indexes = {
        idx.name: tuple(col.name for col in idx.columns)
        for idx in CallReport.__table__.indexes
    }

    assert indexes["idx_call_reports_helper_account_id"] == ("helper_account_id",)


def test_call_report_helper_index_migration_metadata() -> None:
    migration = importlib.import_module(
        "app.database.migrations.versions.0026_call_reports_helper_idx"
    )

    assert migration.revision == "0026_call_reports_helper_idx"
    assert migration.down_revision == "0025_group_member_membership"
    assert migration.INDEX_NAME == "idx_call_reports_helper_account_id"


def test_player_deputy_and_vip_expiry_migration_metadata() -> None:
    migration = importlib.import_module(
        "app.database.migrations.versions.0027_player_deputies_vip_expiry"
    )

    assert migration.revision == "0027_player_deputies_vip_expiry"
    assert migration.down_revision == "0026_call_reports_helper_idx"
    assert migration.TABLE_DEPUTIES == "player_deputies"
    assert migration.VIP_EXPIRES_AT == "expires_at"


def test_monthly_invoice_migration_metadata() -> None:
    migration = importlib.import_module(
        "app.database.migrations.versions.0028_monthly_invoices"
    )

    assert migration.revision == "0028_monthly_invoices"
    assert migration.down_revision == "0027_player_deputies_vip_expiry"
    assert migration.TABLE_NAME == "monthly_invoices"


def test_start_customization_migration_metadata() -> None:
    migration = importlib.import_module(
        "app.database.migrations.versions.0029_start_customization_schema"
    )

    assert migration.revision == "0029_start_customization_schema"
    assert migration.down_revision == "0028_monthly_invoices"
    assert migration.TABLE_MESSAGES == "start_customization_messages"
    assert migration.TABLE_BUTTONS == "start_button_configs"
    assert migration.TABLE_STYLES == "start_style_configs"


def test_start_customization_models_match_migration_contract() -> None:
    message_cols = {column.name for column in StartCustomizationMessage.__table__.columns}
    button_cols = {column.name for column in StartButtonConfig.__table__.columns}
    style_cols = {column.name for column in StartStyleConfig.__table__.columns}
    message_indexes = {
        idx.name: tuple(col.name for col in idx.columns)
        for idx in StartCustomizationMessage.__table__.indexes
    }
    button_indexes = {
        idx.name: tuple(col.name for col in idx.columns)
        for idx in StartButtonConfig.__table__.indexes
    }
    button_constraints = {constraint.name for constraint in StartButtonConfig.__table__.constraints}
    style_constraints = {constraint.name for constraint in StartStyleConfig.__table__.constraints}

    assert {
        "scope_type",
        "scope_owner_user_id",
        "category",
        "is_active",
        "source_chat_id",
        "source_message_id",
        "text",
        "caption",
        "media_file_id",
        "entities_json",
    }.issubset(message_cols)
    assert {
        "scope_type",
        "scope_owner_user_id",
        "slot_key",
        "slot_index",
        "custom_text",
        "color_token",
        "emoji_text",
        "emoji_entities_json",
    }.issubset(button_cols)
    assert {"scope_type", "scope_owner_user_id", "style_mode"}.issubset(style_cols)
    assert message_indexes["idx_start_custom_messages_scope_category_active"] == (
        "scope_type",
        "scope_owner_user_id",
        "category",
        "is_active",
    )
    assert message_indexes["idx_start_custom_messages_source"] == (
        "source_chat_id",
        "source_message_id",
    )
    assert button_indexes["idx_start_button_configs_scope"] == (
        "scope_type",
        "scope_owner_user_id",
    )
    assert "uq_start_button_configs_scope_slot" in button_constraints
    assert "uq_start_style_configs_scope" in style_constraints


def test_player_deputy_model_and_vip_expiry_match_migration() -> None:
    deputy = PlayerDeputy.__table__.c
    vip = PlayerVip.__table__.c
    deputy_constraints = {constraint.name for constraint in PlayerDeputy.__table__.constraints}
    deputy_indexes = {
        idx.name: tuple(col.name for col in idx.columns)
        for idx in PlayerDeputy.__table__.indexes
    }

    assert "expires_at" in vip
    assert "uq_player_deputy_chat_user" in deputy_constraints
    assert deputy.chat_id.index is True
    assert deputy.user_id.index is True
    assert deputy_indexes["idx_player_deputies_chat"] == ("chat_id",)
    assert deputy_indexes["idx_player_deputies_user_chat"] == ("user_id", "chat_id")


def test_server_defaults_match_recent_migrations() -> None:
    chat = ChatSettings.__table__.c
    callsec = CallSecuritySettings.__table__.c
    membership = GroupMemberMembership.__table__.c

    assert str(chat.default_media_type.server_default.arg) == "audio"
    assert str(chat.show_track_id.server_default.arg) == "false"
    assert str(chat.show_cover.server_default.arg) == "true"
    assert str(chat.show_now_playing_text.server_default.arg) == "true"
    assert str(callsec.enabled.server_default.arg) == "false"
    assert str(callsec.membership_age_days.server_default.arg) == "7"
    assert str(callsec.account_age_days.server_default.arg) == "7"
    assert str(membership.source.server_default.arg) == "'manual_unknown'"


def test_group_credit_import_conflict_target_matches_model_uniqueness() -> None:
    source = _source("app/database/migrate_sqlite_to_pg.py")
    assert "ON CONFLICT (chat_id, chat_type) DO NOTHING" in source
    assert "group_credits" in source


def test_chat_and_credit_cache_keys_include_chat_type() -> None:
    from app.utils.redis_keys import instance_key

    assert settings_key(-100, "group") == instance_key("settings:group:-100")
    assert settings_key(-100, "channel") == instance_key("settings:channel:-100")
    assert credit_key(-100, "group") == instance_key("credit:group:-100")
    assert credit_key(-100, "channel") == instance_key("credit:channel:-100")
    assert credit_lock_key(-100, "channel") == instance_key("credit:channel:-100")
    assert credit_warning_sent_key(-100, 2, "2026-06-08", "channel") == (
        instance_key("credit:warn:channel:-100:2:2026-06-08")
    )
    assert credit_expired_pending_member(-100, "channel") == "channel:-100"


def test_credit_and_settings_queries_filter_by_chat_type() -> None:
    credit_source = _source("app/services/credit_service.py")
    repo_source = _source("app/repositories/settings_repo.py")
    scheduler_source = _source("app/scheduler.py")
    notification_source = _source("app/services/notification_service.py")

    assert "GroupCredit.chat_id == chat_id, GroupCredit.chat_type == chat_type" in credit_source
    assert "ChatSettings.chat_id == chat_id" in repo_source
    assert "ChatSettings.chat_type == chat_type" in repo_source
    assert "CreditService.auto_leave_check(" in scheduler_source
    assert "chat_type=chat_type" in notification_source


def test_group_cleanup_filters_type_aware_tables_by_chat_type() -> None:
    source = _source("app/repositories/manager_command_repo.py")

    assert "ChatSettings.chat_type == \"group\"" in source
    assert "CallSecuritySettings.chat_type" not in source
    assert "await invalidate_chat_settings(chat_id, \"group\")" in source
    assert "await invalidate_credit(chat_id, \"group\")" in source


def test_expected_alembic_head_constants_match_the_real_head() -> None:
    """Every pinned head constant must track the migration tree.

    Adding a migration without updating these silently makes deploy/validation
    scripts report a correct database as stale, so pin them all in one place.
    """
    import re

    from app.database.schema_readiness import discover_alembic_heads

    heads = discover_alembic_heads()
    assert len(heads) == 1, f"expected a single alembic head, got {heads}"
    real_head = heads[0]

    pattern = re.compile(
        r"^EXPECTED_(?:ALEMBIC|MIGRATION)_HEAD\s*=\s*[\"']([^\"']+)[\"']",
        re.MULTILINE,
    )
    checked: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "scripts").glob("*.py")):
        for pinned in pattern.findall(path.read_text(encoding="utf-8")):
            checked[str(path.relative_to(REPO_ROOT))] = pinned

    assert checked, "no EXPECTED_*_HEAD constants found; update this test's search"
    stale = {path: head for path, head in checked.items() if head != real_head}
    assert not stale, f"stale head constants (real head is {real_head!r}): {stale}"


def test_helper_otp_config_uses_actual_alembic_heads(monkeypatch) -> None:
    mod = _load_helper_check_script()

    result = mod._check_migration_head()
    assert result["local_heads"] == ["0039_hot_seat"]
    assert result["migration_head_ok"] is True

    monkeypatch.setattr(mod, "EXPECTED_ALEMBIC_HEAD", "0022_owner_text_links")
    stale = mod._check_migration_head()
    assert stale["expected_head_stale"] is True
    assert stale["migration_head_ok"] is False


def test_test_config_rejects_musicbot_dev() -> None:
    conftest = _load_conftest_module()

    with pytest.raises(RuntimeError, match="blocked PostgreSQL database"):
        conftest._assert_safe_test_database_url(
            "postgresql+asyncpg://u:p@localhost/musicbot_dev",
            allow_external=True,
        )


@pytest.mark.asyncio
async def test_advisory_lock_fallback_skips_non_postgresql(monkeypatch) -> None:
    import app.utils.cache as cache_mod
    import app.database.engine  # noqa: F401

    engine_mod = sys.modules["app.database.engine"]
    monkeypatch.setattr(
        engine_mod,
        "engine",
        SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
    )
    assert await cache_mod._pg_advisory_acquire("sync-test") is None
