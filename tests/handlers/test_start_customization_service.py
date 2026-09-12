from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import start_customization_service as svc


def _row(**kwargs):
    defaults = {
        "id": 1,
        "category": "start",
        "source_chat_id": None,
        "source_message_id": None,
        "source_chat_type": None,
        "message_type": "text",
        "text": None,
        "caption": None,
        "media_file_id": None,
        "media_type": None,
        "entities_json": None,
        "extra_json": None,
        "weight": 1,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _slot_row(slot_key: str, **kwargs):
    defaults = {
        "slot_key": slot_key,
        "slot_index": svc.SLOT_INDEX_BY_KEY[slot_key],
        "custom_text": None,
        "color_token": None,
        "emoji_text": None,
        "emoji_entities_json": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_color_parser_accepts_case_insensitive_tokens() -> None:
    assert svc.parse_color_sequence("rgbnRGBN") == (
        "R",
        "G",
        "B",
        "N",
        "R",
        "G",
        "B",
        "N",
    )


def test_color_parser_accepts_automatic_and_all_neutral_palettes() -> None:
    assert svc.parse_color_sequence("RBGNNNNR") == svc.DEFAULT_ADVANCED_COLOR_SEQUENCE
    assert svc.parse_color_sequence("NNNNNNNN") == ("N",) * len(svc.SLOT_KEYS)


@pytest.mark.parametrize("value", ["RBGNNNN", "RBGNNNNRR"])
def test_color_parser_requires_all_eight_semantic_slots(value: str) -> None:
    with pytest.raises(ValueError):
        svc.parse_color_sequence(value)


def test_color_parser_rejects_unknown_token() -> None:
    with pytest.raises(ValueError):
        svc.parse_color_sequence("RGBNRGBT")


def test_color_parser_accepts_persian_and_indexed_formats() -> None:
    persian_input = (
        "1- آبی\n"
        "2- قرمز\n"
        "3- سبز\n"
        "4- قرمز\n"
        "5- سبز\n"
        "6- قرمز\n"
        "7- آبی\n"
        "8- بدون رنگ\n"
    )
    assert svc.parse_color_sequence(persian_input) == (
        "B", "R", "G", "R", "G", "R", "B", "N"
    )


def test_color_parser_honors_explicit_slot_numbers_when_lines_are_reordered() -> None:
    reordered_input = (
        "2- قرمز\n"
        "1- آبی\n"
        "3- سبز\n"
        "4- قرمز\n"
        "5- سبز\n"
        "6- قرمز\n"
        "7- آبی\n"
        "8- بدون رنگ\n"
    )
    assert svc.parse_color_sequence(reordered_input) == (
        "B", "R", "G", "R", "G", "R", "B", "N"
    )


def test_color_parser_rejects_non_spec_aliases() -> None:
    with pytest.raises(ValueError):
        svc.parse_color_sequence("psdpsdsn")


def test_split_emoji_entities_per_slot_offsets_entities_by_line() -> None:
    value = "aa\nbb\ncc\ndd\nee\nff\ngg\nhh"
    entities_json = (
        '[{"type":"custom_emoji","offset":0,"length":2,"custom_emoji_id":"e1"},'
        '{"type":"custom_emoji","offset":3,"length":2,"custom_emoji_id":"e2"}]'
    )
    per_slot = svc.split_emoji_entities_per_slot(value, entities_json)
    assert per_slot[0] is not None
    assert '"offset": 0' in per_slot[0]
    assert '"custom_emoji_id": "e1"' in per_slot[0]
    assert per_slot[1] is not None
    assert '"offset": 0' in per_slot[1]
    assert '"custom_emoji_id": "e2"' in per_slot[1]
    assert per_slot[2] is None


def test_adjacent_premium_emoji_use_utf16_entity_offsets() -> None:
    value = "😀😃😄😁😆😅😂🙂"
    entities_json = json.dumps(
        [
            {
                "type": "custom_emoji",
                "offset": index * 2,
                "length": 2,
                "custom_emoji_id": str(10_000 + index),
            }
            for index in range(8)
        ]
    )

    assert svc.parse_emoji_sequence(value, entities_json) == tuple(value)
    per_slot = svc.split_emoji_entities_per_slot(value, entities_json)
    second = json.loads(per_slot[1])
    assert second == [
        {
            "type": "custom_emoji",
            "offset": 0,
            "length": 2,
            "custom_emoji_id": "10001",
        }
    ]


def test_adjacent_ordinary_emoji_are_split_as_grapheme_like_items() -> None:
    value = "⚫️🔴🔵🟢🟢🔵🔴⚫️"
    assert svc.parse_emoji_sequence(value) == (
        "⚫️",
        "🔴",
        "🔵",
        "🟢",
        "🟢",
        "🔵",
        "🔴",
        "⚫️",
    )


@pytest.mark.asyncio
async def test_emoji_update_rejects_composed_button_label_over_limit(
    monkeypatch,
) -> None:
    settings = tuple(
        svc.ButtonSlotSetting(
            slot_index=index,
            slot_key=slot_key,
            custom_text="x" * 64 if index == 1 else None,
        )
        for index, slot_key in enumerate(svc.SLOT_KEYS, start=1)
    )
    upsert = AsyncMock()
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=settings),
    )
    monkeypatch.setattr(svc.repo, "upsert_button_slot_configs", upsert)

    with pytest.raises(ValueError, match="button label is too long"):
        await svc.set_button_emoji_sequence(
            scope_type="global",
            value="😀 😃 😄 😁 😆 😅 😂 🙂",
            actor_user_id=1,
        )

    upsert.assert_not_awaited()



def test_multiline_text_maps_line_to_semantic_slot() -> None:
    parsed = svc.parse_button_text_lines(
        "\n".join(
            [
                "purchase label",
                "test label",
                "use label",
                "history label",
                "ability label",
                "commands label",
                "support label",
                "note label",
            ]
        )
    )

    assert parsed[svc.SLOT_INDEX_BY_KEY["purchase"] - 1] == "purchase label"
    assert parsed[svc.SLOT_INDEX_BY_KEY["note"] - 1] == "note label"


@pytest.mark.asyncio
async def test_effective_button_slots_owner_fallback_to_global(monkeypatch) -> None:
    async def _list_configs(*, scope_type, owner_user_id=None):
        if scope_type == "global":
            return [
                _slot_row(
                    "purchase",
                    custom_text="global purchase",
                    color_token="R",
                ),
                _slot_row("test", color_token="R"),
            ]
        return [
            _slot_row("purchase", color_token="N"),
            _slot_row("test", custom_text="owner test"),
        ]

    monkeypatch.setattr(svc.repo, "list_button_slot_configs", _list_configs)

    settings = await svc.get_effective_button_slot_settings(owner_user_id=9001)
    by_key = {setting.slot_key: setting for setting in settings}

    assert by_key["purchase"].custom_text == "global purchase"
    assert by_key["purchase"].color_token == "N"
    assert by_key["purchase"].source_scope == "owner"
    assert by_key["test"].custom_text == "owner test"
    assert by_key["test"].color_token == "R"


@pytest.mark.asyncio
async def test_effective_style_owner_override_precedes_global(monkeypatch) -> None:
    async def _get_style(*, scope_type, owner_user_id=None):
        if scope_type == "owner":
            assert owner_user_id == 9001
            return SimpleNamespace(style_mode="simple")
        return SimpleNamespace(style_mode="advanced")

    monkeypatch.setattr(svc.repo, "get_style_config", _get_style)

    style = await svc.get_effective_style_mode(owner_user_id=9001)

    assert style == svc.EffectiveStyle("simple", "owner")


@pytest.mark.asyncio
async def test_random_selector_is_deterministic_under_injected_rng(monkeypatch) -> None:
    rows = [
        _row(id=10, category="ability", text="first", weight=1),
        _row(id=11, category="ability", text="second", weight=5),
    ]

    async def _list_items(**kwargs):
        assert kwargs["scope_type"] == "global"
        assert kwargs["category"] == "ability"
        return rows

    class _Rng:
        def choices(self, population, weights, k):  # noqa: ANN001
            assert population == [0, 1]
            assert weights == [1, 5]
            assert k == 1
            return [1]

        def choice(self, seq):  # noqa: ANN001
            raise AssertionError("choices should be used")

    monkeypatch.setattr(svc.repo, "list_active_message_items", _list_items)

    selected = await svc.get_random_message_item(category="ability", rng=_Rng())

    assert selected is not None
    assert selected.id == 11
    assert selected.text == "second"
    assert selected.send_strategy == "send_text"
    assert selected.source_scope == "global"


@pytest.mark.asyncio
async def test_is_test_category_exposed_rejects_free_policy_mode(monkeypatch) -> None:
    policy = SimpleNamespace(policy_mode="free", charge_on_install=True)

    async def _get_policy():
        return policy

    monkeypatch.setattr(svc.InstallPolicyService, "get_policy", _get_policy)

    assert await svc.is_test_category_exposed(chat_id=None) is False


@pytest.mark.asyncio
async def test_test_category_selection_is_gated_before_repo_read(monkeypatch) -> None:
    monkeypatch.setattr(svc, "is_test_category_exposed", AsyncMock(return_value=False))
    list_mock = AsyncMock(return_value=[_row(category="test", text="paid content")])
    monkeypatch.setattr(svc.repo, "list_active_message_items", list_mock)

    selected = await svc.get_random_message_item(category="test", chat_id=-100)

    assert selected is None
    list_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_category_pool_passes_explicit_scope_and_actor(monkeypatch) -> None:
    clear_mock = AsyncMock(return_value=2)
    monkeypatch.setattr(svc.repo, "clear_message_category", clear_mock)

    cleared = await svc.clear_category_pool(
        scope_type="owner",
        owner_user_id=9001,
        category="history",
        actor_user_id=9001,
    )

    assert cleared == 2
    clear_mock.assert_awaited_once_with(
        scope_type="owner",
        owner_user_id=9001,
        category="history",
        updated_by=9001,
    )


@pytest.mark.asyncio
async def test_add_message_pool_item_rejects_empty_payload(monkeypatch) -> None:
    create_mock = AsyncMock()
    monkeypatch.setattr(svc.repo, "create_message_item", create_mock)

    with pytest.raises(ValueError):
        await svc.add_message_pool_item(
            scope_type="global",
            category="note",
            actor_user_id=123,
        )

    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_button_color_sequence_uses_atomic_batch_repo(monkeypatch) -> None:
    batch_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(svc.repo, "upsert_button_slot_configs", batch_mock)
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=()),
    )

    await svc.set_button_color_sequence(
        scope_type="owner",
        owner_user_id=9001,
        value="RGBNRGBN",
        actor_user_id=777,
    )

    kwargs = batch_mock.await_args.kwargs
    assert kwargs["scope_type"] == "owner"
    assert kwargs["owner_user_id"] == 9001
    assert kwargs["updated_by"] == 777
    assert kwargs["slot_values"][1] == {"color_token": "R"}
    assert kwargs["slot_values"][8] == {"color_token": "N"}
