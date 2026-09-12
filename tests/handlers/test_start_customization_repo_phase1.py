from __future__ import annotations

import asyncio
import importlib

import pytest

from app.repositories import start_customization_repo as repo

OWNER_A = 910_001
OWNER_B = 910_002


def test_phase1_migration_metadata() -> None:
    mod = importlib.import_module(
        "app.database.migrations.versions.0029_start_customization_schema",
    )

    assert mod.revision == "0029_start_customization_schema"
    assert mod.down_revision == "0028_monthly_invoices"
    assert mod.TABLE_MESSAGES == "start_customization_messages"
    assert mod.TABLE_BUTTONS == "start_button_configs"
    assert mod.TABLE_STYLES == "start_style_configs"


def test_fixed_eight_slot_contract() -> None:
    assert repo.SLOT_KEYS == (
        "purchase",
        "test",
        "use",
        "history",
        "ability",
        "commands",
        "support",
        "note",
    )
    assert repo.normalize_slot(1) == ("purchase", 1)
    assert repo.normalize_slot(8) == ("note", 8)
    assert repo.normalize_slot("ability") == ("ability", 5)
    with pytest.raises(ValueError):
        repo.normalize_slot(9)
    with pytest.raises(ValueError):
        repo.normalize_slot("unknown")


@pytest.mark.asyncio
async def test_create_and_list_global_message_pool_item() -> None:
    row = await repo.create_message_item(
        scope_type="global",
        category="start",
        source_chat_id=-1001,
        source_message_id=44,
        source_chat_type="supergroup",
        message_type="text",
        text="global start",
        entities_json='[{"type":"custom_emoji"}]',
        created_by=123,
    )

    rows = await repo.list_active_message_items(scope_type="global", category="start")
    matched = [item for item in rows if item.id == row.id]

    assert matched
    assert matched[0].scope_owner_user_id == 0
    assert matched[0].text == "global start"
    assert matched[0].source_chat_id == -1001
    assert matched[0].entities_json == '[{"type":"custom_emoji"}]'


@pytest.mark.asyncio
async def test_owner_message_pool_items_are_scope_isolated() -> None:
    await repo.create_message_item(
        scope_type="owner",
        owner_user_id=OWNER_A,
        category="ability",
        text="owner-a ability",
    )
    await repo.create_message_item(
        scope_type="owner",
        owner_user_id=OWNER_B,
        category="ability",
        text="owner-b ability",
    )

    rows_a = await repo.list_active_message_items(
        scope_type="owner",
        owner_user_id=OWNER_A,
        category="ability",
    )
    rows_b = await repo.list_active_message_items(
        scope_type="owner",
        owner_user_id=OWNER_B,
        category="ability",
    )

    assert {row.text for row in rows_a} == {"owner-a ability"}
    assert {row.scope_owner_user_id for row in rows_a} == {OWNER_A}
    assert {row.text for row in rows_b} == {"owner-b ability"}
    assert {row.scope_owner_user_id for row in rows_b} == {OWNER_B}


@pytest.mark.asyncio
async def test_clear_message_category_marks_active_rows_inactive() -> None:
    row = await repo.create_message_item(
        scope_type="owner",
        owner_user_id=OWNER_A,
        category="history",
        text="history to clear",
    )

    cleared = await repo.clear_message_category(
        scope_type="owner",
        owner_user_id=OWNER_A,
        category="history",
        updated_by=OWNER_A,
    )
    rows = await repo.list_active_message_items(
        scope_type="owner",
        owner_user_id=OWNER_A,
        category="history",
    )

    assert cleared == 1
    assert all(item.id != row.id for item in rows)
    assert await repo.clear_message_category(
        scope_type="owner",
        owner_user_id=OWNER_A,
        category="history",
    ) == 0


@pytest.mark.asyncio
async def test_button_slot_upsert_and_independent_clear() -> None:
    row = await repo.upsert_button_slot_config(
        scope_type="owner",
        owner_user_id=OWNER_A,
        slot=2,
        custom_text="Test tutorial",
        color_token="r",
        emoji_text="🔴",
        emoji_entities_json='[{"type":"custom_emoji"}]',
        updated_by=OWNER_A,
    )

    assert row.slot_key == "test"
    assert row.slot_index == 2
    assert row.color_token == "R"
    assert row.custom_text == "Test tutorial"
    assert row.emoji_text == "🔴"

    assert await repo.clear_button_slot_part(
        scope_type="owner",
        owner_user_id=OWNER_A,
        slot="test",
        part="color",
        updated_by=OWNER_A,
    ) is True

    fetched = await repo.get_button_slot_config(
        scope_type="owner",
        owner_user_id=OWNER_A,
        slot="test",
    )
    assert fetched is not None
    assert fetched.color_token is None
    assert fetched.custom_text == "Test tutorial"
    assert fetched.emoji_text == "🔴"

    assert await repo.clear_button_slot_part(
        scope_type="owner",
        owner_user_id=OWNER_A,
        slot="test",
        part="emoji",
    ) is True
    fetched = await repo.get_button_slot_config(
        scope_type="owner",
        owner_user_id=OWNER_A,
        slot="test",
    )
    assert fetched is not None
    assert fetched.emoji_text is None
    assert fetched.emoji_entities_json is None


@pytest.mark.asyncio
async def test_style_mode_upserts_per_scope() -> None:
    global_row = await repo.set_style_mode(scope_type="global", style_mode="advanced")
    owner_row = await repo.set_style_mode(
        scope_type="owner",
        owner_user_id=OWNER_A,
        style_mode="simple",
    )

    assert global_row.scope_owner_user_id == 0
    assert global_row.style_mode == "advanced"
    assert owner_row.scope_owner_user_id == OWNER_A
    assert owner_row.style_mode == "simple"

    updated = await repo.set_style_mode(
        scope_type="owner",
        owner_user_id=OWNER_A,
        style_mode="advanced",
    )
    assert updated.id == owner_row.id
    assert updated.style_mode == "advanced"


@pytest.mark.asyncio
async def test_concurrent_first_slot_upserts_keep_one_row() -> None:
    owner_id = 919_991
    await asyncio.gather(
        repo.upsert_button_slot_config(
            scope_type="owner",
            owner_user_id=owner_id,
            slot="purchase",
            custom_text="first",
        ),
        repo.upsert_button_slot_config(
            scope_type="owner",
            owner_user_id=owner_id,
            slot="purchase",
            custom_text="second",
        ),
    )

    rows = await repo.list_button_slot_configs(
        scope_type="owner",
        owner_user_id=owner_id,
    )
    purchase_rows = [row for row in rows if row.slot_key == "purchase"]
    assert len(purchase_rows) == 1
    assert purchase_rows[0].custom_text in {"first", "second"}


@pytest.mark.asyncio
async def test_two_atomic_first_style_toggles_cancel_each_other() -> None:
    owner_id = 919_992
    await asyncio.gather(
        repo.toggle_style_mode(
            scope_type="owner",
            owner_user_id=owner_id,
            initial_mode="advanced",
        ),
        repo.toggle_style_mode(
            scope_type="owner",
            owner_user_id=owner_id,
            initial_mode="advanced",
        ),
    )

    row = await repo.get_style_config(
        scope_type="owner",
        owner_user_id=owner_id,
    )
    assert row is not None
    assert row.style_mode == "simple"


@pytest.mark.asyncio
async def test_invalid_scope_category_color_and_style_are_rejected() -> None:
    with pytest.raises(ValueError):
        await repo.create_message_item(scope_type="owner", category="start", text="x")
    with pytest.raises(ValueError):
        await repo.create_message_item(scope_type="global", category="bogus", text="x")
    with pytest.raises(ValueError):
        await repo.upsert_button_slot_config(
            scope_type="global",
            slot=1,
            color_token="T",
        )
    with pytest.raises(ValueError):
        await repo.set_style_mode(scope_type="global", style_mode="windows11")
