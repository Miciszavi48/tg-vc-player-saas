from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from pyrogram.enums import ButtonStyle

from app.services import start_customization_service as svc
from app.utils.ui import KeyboardFactory


_LINKS = {
    "creator": "https://t.me/creator",
    "guide_channel": "https://t.me/commands",
    "support_group": "https://t.me/support",
    "bot_channel": "https://t.me/bot",
    "custom_link": "https://example.com/custom",
    "sudo_1": "https://t.me/sudo1",
    "sudo_2": "https://t.me/sudo2",
}


def _default_settings() -> tuple[svc.ButtonSlotSetting, ...]:
    return tuple(
        svc.ButtonSlotSetting(
            slot_index=index,
            slot_key=slot_key,
        )
        for index, slot_key in enumerate(svc.SLOT_KEYS, start=1)
    )


def _labels(menu: svc.RenderedStartMenu) -> list[str]:
    return [button.label for row in menu.rows for button in row]


def _by_slot(menu: svc.RenderedStartMenu) -> dict[str, svc.StartButtonDescriptor]:
    return {
        button.slot_key: button
        for row in menu.rows
        for button in row
    }


def _styles_by_slot(menu: svc.RenderedStartMenu) -> dict[str, ButtonStyle]:
    markup = KeyboardFactory.start_custom_menu(menu)
    assert markup is not None
    return {
        descriptor.slot_key: button.style
        for descriptor_row, button_row in zip(
            menu.rows,
            markup.inline_keyboard,
            strict=True,
        )
        for descriptor, button in zip(descriptor_row, button_row, strict=True)
    }


@pytest.mark.asyncio
async def test_renderer_falls_back_to_current_start_labels_without_customization(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("simple", "default")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=_default_settings()),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=False))

    menu = await svc.render_start_menu("fa", _LINKS)

    assert menu.uses_legacy_fallback is True
    assert [[button.url for button in row] for row in menu.rows] == [
        ["https://t.me/creator"],
        ["https://t.me/bot", "https://t.me/support"],
        ["https://t.me/commands", "https://example.com/custom"],
        ["https://t.me/sudo1", "https://t.me/sudo2"],
    ]
    assert _labels(menu)[0] == "🛒 خرید از سازنده"


@pytest.mark.asyncio
async def test_renderer_builds_all_eight_semantic_slots(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("simple", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=_default_settings()),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu("fa", _LINKS, expose_test_slot=True)
    by_slot = _by_slot(menu)

    assert tuple(by_slot) == svc.SLOT_KEYS
    assert by_slot["purchase"].behavior == "url"
    assert by_slot["purchase"].url == "https://t.me/creator"
    assert by_slot["commands"].behavior == "url"
    assert by_slot["commands"].url == "https://t.me/commands"
    assert by_slot["support"].behavior == "url"
    assert by_slot["support"].url == "https://t.me/support"
    assert by_slot["purchase"].callback_data is None
    assert by_slot["commands"].callback_data is None
    assert by_slot["support"].callback_data is None
    assert by_slot["test"].callback_data == "start:cat:test"
    assert by_slot["use"].callback_data == "start:cat:use"
    assert by_slot["history"].callback_data == "start:cat:history"
    assert by_slot["ability"].callback_data == "start:cat:ability"
    assert by_slot["note"].callback_data == "start:cat:note"


@pytest.mark.asyncio
async def test_renderer_preserves_required_rtl_visual_row_order(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("simple", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=_default_settings()),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu("fa", _LINKS, expose_test_slot=True)

    assert [[button.slot_key for button in row] for row in menu.rows] == [
        ["purchase"],
        ["test"],
        ["use"],
        ["history", "ability"],
        ["commands", "support"],
        ["note"],
    ]


@pytest.mark.asyncio
async def test_renderer_hides_test_slot_when_install_policy_blocks_it(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("advanced", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=_default_settings()),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu("fa", _LINKS, expose_test_slot=False)
    by_slot = _by_slot(menu)

    assert "test" not in by_slot
    assert by_slot["use"].color_token == "G"
    assert by_slot["note"].color_token == "R"
    assert ["use"] in [[button.slot_key for button in row] for row in menu.rows]


@pytest.mark.asyncio
async def test_renderer_simple_mode_suppresses_markers_but_keeps_custom_text(
    monkeypatch,
) -> None:
    settings = list(_default_settings())
    settings[0] = svc.ButtonSlotSetting(
        slot_index=1,
        slot_key="purchase",
        custom_text="Custom Purchase",
        color_token="R",
        emoji_text="⭐",
        source_scope="global",
    )
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("simple", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=tuple(settings)),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu("en", _LINKS, expose_test_slot=True)

    purchase = _by_slot(menu)["purchase"]
    assert purchase.label == "Custom Purchase"
    assert purchase.color_token is None
    assert purchase.emoji_text is None
    assert purchase.url == "https://t.me/creator"


@pytest.mark.asyncio
async def test_renderer_advanced_mode_uses_automatic_palette_without_saved_colors(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("advanced", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=_default_settings()),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu("fa", _LINKS, expose_test_slot=True)
    by_slot = _by_slot(menu)
    styles = _styles_by_slot(menu)

    assert tuple(by_slot[key].color_token for key in svc.SLOT_KEYS) == (
        "R",
        "B",
        "G",
        "N",
        "N",
        "N",
        "N",
        "R",
    )
    assert tuple(styles[key] for key in svc.SLOT_KEYS) == (
        ButtonStyle.DANGER,
        ButtonStyle.PRIMARY,
        ButtonStyle.SUCCESS,
        ButtonStyle.DEFAULT,
        ButtonStyle.DEFAULT,
        ButtonStyle.DEFAULT,
        ButtonStyle.DEFAULT,
        ButtonStyle.DANGER,
    )

    markup = KeyboardFactory.start_custom_menu(menu)
    assert markup is not None
    raw_purchase = await markup.inline_keyboard[0][0].write(None)
    raw_test = await markup.inline_keyboard[1][0].write(None)
    raw_history = await markup.inline_keyboard[3][0].write(None)
    assert raw_purchase.style.bg_danger is True
    assert raw_test.style.bg_primary is True
    assert raw_history.style is None


@pytest.mark.asyncio
async def test_renderer_advanced_mode_uses_native_style_without_color_marker(
    monkeypatch,
) -> None:
    settings = list(_default_settings())
    settings[0] = svc.ButtonSlotSetting(
        slot_index=1,
        slot_key="purchase",
        custom_text="Custom Purchase",
        color_token="R",
        emoji_text="⭐",
        source_scope="global",
    )
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("advanced", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=tuple(settings)),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu("en", _LINKS, expose_test_slot=True)

    purchase = _by_slot(menu)["purchase"]
    assert purchase.label == "⭐ Custom Purchase"
    assert purchase.color_token == "R"
    assert purchase.emoji_text == "⭐"
    assert purchase.url == "https://t.me/creator"
    assert purchase.callback_data is None

    button = KeyboardFactory.start_custom_menu(menu).inline_keyboard[0][0]
    assert button.style == ButtonStyle.DANGER
    assert button.icon_custom_emoji_id is None


@pytest.mark.asyncio
async def test_renderer_advanced_mode_uses_native_premium_icon(monkeypatch) -> None:
    settings = list(_default_settings())
    settings[0] = svc.ButtonSlotSetting(
        slot_index=1,
        slot_key="purchase",
        custom_text="Custom Purchase",
        color_token="N",
        emoji_text="😀",
        emoji_entities_json=json.dumps(
            [
                {
                    "type": "custom_emoji",
                    "offset": 0,
                    "length": 2,
                    "custom_emoji_id": "5765013706681360005",
                }
            ]
        ),
        source_scope="global",
    )
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("advanced", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=tuple(settings)),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu("en", _LINKS, expose_test_slot=True)
    purchase = _by_slot(menu)["purchase"]

    assert purchase.label == "Custom Purchase"
    assert purchase.color_token == "N"
    assert purchase.icon_custom_emoji_id == 5765013706681360005
    button = KeyboardFactory.start_custom_menu(menu).inline_keyboard[0][0]
    assert button.style == ButtonStyle.DEFAULT
    assert button.icon_custom_emoji_id == 5765013706681360005


@pytest.mark.asyncio
async def test_renderer_hides_missing_url_slots_with_existing_fallback_rule(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        svc,
        "get_effective_style_mode",
        AsyncMock(return_value=svc.EffectiveStyle("simple", "global")),
    )
    monkeypatch.setattr(
        svc,
        "get_effective_button_slot_settings",
        AsyncMock(return_value=_default_settings()),
    )
    monkeypatch.setattr(svc, "_has_any_customization", AsyncMock(return_value=True))

    menu = await svc.render_start_menu(
        "fa",
        {"creator": "", "guide_channel": "", "support_group": ""},
        expose_test_slot=True,
    )

    by_slot = _by_slot(menu)
    assert "purchase" not in by_slot
    assert "commands" not in by_slot
    assert "support" not in by_slot
    assert by_slot["test"].callback_data == "start:cat:test"
