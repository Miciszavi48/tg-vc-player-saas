"""Object-level coverage for centralized inline button semantic styles."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pyrogram.enums import ButtonStyle
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "999888777")

from app.handlers.broadcast_wizard import _targets_keyboard
from app.services import start_customization_service as start_custom
from app.services.call_security_service import build_panel_keyboard
from app.utils.button_style import (
    STYLE_ADVANCED,
    STYLE_SIMPLE,
    apply_button_style_policy,
    bind_button_style_scope_from_update,
    install_inline_keyboard_style_hook,
    reset_button_style_scope,
)
from app.utils.ui import CB, KeyboardFactory
from app.utils.pagination import paginate_keyboard


def _button(kb: InlineKeyboardMarkup, callback_data: str) -> InlineKeyboardButton:
    return next(
        button
        for row in kb.inline_keyboard
        for button in row
        if button.callback_data == callback_data
    )


def _shape_and_targets(
    kb: InlineKeyboardMarkup,
) -> tuple[tuple[tuple[str, str | None, str | None], ...], ...]:
    return tuple(
        tuple((button.text, button.callback_data, button.url) for button in row)
        for row in kb.inline_keyboard
    )


def _all_styles(kb: InlineKeyboardMarkup) -> list[ButtonStyle]:
    return [button.style for row in kb.inline_keyboard for button in row]


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["fa", "en"])
async def test_advanced_policy_covers_representative_keyboard_families(
    lang: str,
) -> None:
    developer = KeyboardFactory.developer_panel(lang)
    credit = KeyboardFactory.dev_sub_credit(lang)
    owner_media = KeyboardFactory.owner_sub_media(
        lang,
        {
            "audio": True,
            "video": False,
            "file": True,
            "download": False,
            "buttons": True,
        },
    )
    helper_active = KeyboardFactory.helper_detail(lang, 7, "active")
    helper_disabled = KeyboardFactory.helper_detail(lang, 8, "disabled")
    playback = KeyboardFactory.now_playing_controls(lang, repeat_on=True)

    for keyboard in (
        developer,
        credit,
        owner_media,
        helper_active,
        helper_disabled,
        playback,
    ):
        await apply_button_style_policy(keyboard, style_mode=STYLE_ADVANCED)

    assert _button(developer, CB["DEV_STATUS"]).style == ButtonStyle.DEFAULT
    assert _button(developer, CB["NAV_START"]).style == ButtonStyle.PRIMARY
    assert _button(credit, CB["DEV_INCREASE_CREDIT"]).style == ButtonStyle.SUCCESS
    assert _button(credit, CB["DEV_DECREASE_CREDIT"]).style == ButtonStyle.DANGER
    assert _button(credit, CB["NAV_BACK"]).style == ButtonStyle.PRIMARY

    assert (
        _button(owner_media, CB["OWN_MEDIA_AUDIO_TOGGLE"]).style == ButtonStyle.DANGER
    )
    assert (
        _button(owner_media, CB["OWN_MEDIA_VIDEO_TOGGLE"]).style == ButtonStyle.SUCCESS
    )
    assert _button(helper_active, f"{CB['HLP_DISABLE']}7").style == ButtonStyle.DANGER
    assert _button(helper_disabled, f"{CB['HLP_ENABLE']}8").style == ButtonStyle.SUCCESS
    assert _button(playback, CB["PB_PREV"]).style == ButtonStyle.PRIMARY
    assert _button(playback, CB["PB_NEXT"]).style == ButtonStyle.PRIMARY
    assert _button(playback, CB["PB_STOP"]).style == ButtonStyle.DANGER
    assert _button(playback, CB["PB_RESUME"]).style == ButtonStyle.SUCCESS
    assert _button(playback, CB["PB_REPEAT_TOGGLE"]).style == ButtonStyle.DANGER
    assert _button(playback, CB["PB_VOL_UP"]).style == ButtonStyle.DEFAULT


@pytest.mark.asyncio
async def test_simple_mode_removes_colors_without_changing_layout_or_targets() -> None:
    keyboard = KeyboardFactory.post_install_panel(
        "en",
        14,
        "Owner",
        "https://example.com/guide",
        show_credit_controls=True,
    )
    before = _shape_and_targets(keyboard)

    await apply_button_style_policy(keyboard, style_mode=STYLE_ADVANCED)
    assert _button(keyboard, CB["POST_INSTALL_INC_CREDIT"]).style == ButtonStyle.SUCCESS
    assert _button(keyboard, CB["POST_INSTALL_DEC_CREDIT"]).style == ButtonStyle.DANGER
    assert _button(keyboard, CB["NAV_CLOSE"]).style == ButtonStyle.DANGER
    url_button = next(
        button for row in keyboard.inline_keyboard for button in row if button.url
    )
    assert url_button.style == ButtonStyle.DEFAULT

    await apply_button_style_policy(keyboard, style_mode=STYLE_SIMPLE)

    assert _shape_and_targets(keyboard) == before
    assert all(style == ButtonStyle.DEFAULT for style in _all_styles(keyboard))


@pytest.mark.asyncio
async def test_destructive_confirm_is_danger_and_abort_back_are_primary() -> None:
    keyboard = KeyboardFactory.group_clear_admins_confirm(
        "en",
        chat_id=-1001,
        user_id=77,
        issued_at=123,
    )
    await apply_button_style_policy(keyboard, style_mode=STYLE_ADVANCED)

    assert (
        _button(
            keyboard,
            f"{CB['GRP_CLEAR_CONFIRM_PREFIX']}-1001:77:123",
        ).style
        == ButtonStyle.DANGER
    )
    assert (
        _button(
            keyboard,
            f"{CB['GRP_CLEAR_CANCEL_PREFIX']}-1001:77:123",
        ).style
        == ButtonStyle.PRIMARY
    )
    assert _button(keyboard, CB["GRP_MANAGEMENT"]).style == ButtonStyle.PRIMARY


@pytest.mark.asyncio
async def test_compact_clear_and_permission_disable_confirmations_are_safe() -> None:
    text_clear = KeyboardFactory.dev_text_clear_confirm(
        "en",
        field="start_text",
        user_id=77,
        issued_at=123,
    )
    permission = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Confirm",
                    callback_data="dev:sp:y:g:55:0:77:123",
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="dev:sp:n:g:55:0:77:123",
                ),
            ]
        ]
    )

    await apply_button_style_policy(text_clear, style_mode=STYLE_ADVANCED)
    await apply_button_style_policy(permission, style_mode=STYLE_ADVANCED)

    assert (
        _button(
            text_clear,
            f"{CB['DEV_TEXT_CLEAR_EXEC_PREFIX']}start_text:77:123",
        ).style
        == ButtonStyle.DANGER
    )
    assert (
        _button(
            text_clear,
            f"{CB['DEV_TEXT_CLEAR_ABORT_PREFIX']}start_text:77:123",
        ).style
        == ButtonStyle.PRIMARY
    )
    assert _button(permission, "dev:sp:y:g:55:0:77:123").style == ButtonStyle.DANGER
    assert _button(permission, "dev:sp:n:g:55:0:77:123").style == ButtonStyle.PRIMARY


@pytest.mark.asyncio
async def test_positive_confirm_and_regular_cancel_are_success_and_danger() -> None:
    from app.handlers.owner_panel import _owner_confirm_kb

    keyboard = _owner_confirm_kb("save:confirm", "save:no", CB["OWN_SUDOS"])
    await apply_button_style_policy(keyboard, style_mode=STYLE_ADVANCED)

    assert _button(keyboard, "save:confirm").style == ButtonStyle.SUCCESS
    assert _button(keyboard, "save:no").style == ButtonStyle.DANGER
    assert _button(keyboard, CB["OWN_SUDOS"]).style == ButtonStyle.PRIMARY


@pytest.mark.asyncio
async def test_categories_stay_neutral_while_compact_actions_are_semantic() -> None:
    developer = KeyboardFactory.developer_panel("en")
    moderation = KeyboardFactory.dev_sub_moderation("en")
    download = KeyboardFactory.youtube_download_format("en", "token")
    refresh = KeyboardFactory.youtube_sessions_home(
        "en",
        back_callback=CB["NAV_BACK"],
    )
    compact = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Leave",
                    callback_data="own:grp:lv:group:1",
                ),
                InlineKeyboardButton(
                    "Add credit",
                    callback_data="own:grp:ci:group:1",
                ),
                InlineKeyboardButton(
                    "Deduct credit",
                    callback_data="own:grp:cd:group:1",
                ),
            ]
        ]
    )

    for keyboard in (developer, moderation, download, refresh, compact):
        await apply_button_style_policy(keyboard, style_mode=STYLE_ADVANCED)

    assert _button(developer, CB["HLP_HOME"]).style == ButtonStyle.DEFAULT
    assert _button(developer, CB["AN_HOME"]).style == ButtonStyle.DEFAULT
    assert _button(moderation, CB["DEV_BANALL_HOME"]).style == ButtonStyle.DEFAULT
    assert (
        _button(download, f"{CB['DOWNLOAD_FORMAT_PREFIX']}x:token").style
        == ButtonStyle.DANGER
    )
    assert _button(refresh, CB["YT_SESSION_HOME"]).style == ButtonStyle.SUCCESS
    assert _button(compact, "own:grp:lv:group:1").style == ButtonStyle.DANGER
    assert _button(compact, "own:grp:ci:group:1").style == ButtonStyle.SUCCESS
    assert _button(compact, "own:grp:cd:group:1").style == ButtonStyle.DANGER


@pytest.mark.asyncio
async def test_alternate_navigation_labels_and_page_numbers_are_consistent() -> None:
    pagination = paginate_keyboard("en", page=1, total_pages=3, cb_prefix="pg:test:")
    alternate_labels = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔙 Back to List",
                    callback_data="own:grp:l:active:0",
                ),
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="dev:sudo:detail:55:0",
                ),
            ]
        ]
    )

    await apply_button_style_policy(pagination, style_mode=STYLE_ADVANCED)
    await apply_button_style_policy(alternate_labels, style_mode=STYLE_ADVANCED)

    assert _button(pagination, "pg:test:0").style == ButtonStyle.PRIMARY
    assert _button(pagination, CB["NOOP"]).style == ButtonStyle.DEFAULT
    assert _button(pagination, "pg:test:2").style == ButtonStyle.PRIMARY
    assert _button(pagination, CB["NAV_BACK"]).style == ButtonStyle.PRIMARY
    assert _all_styles(alternate_labels) == [
        ButtonStyle.PRIMARY,
        ButtonStyle.PRIMARY,
    ]


@pytest.mark.asyncio
async def test_dynamic_toggle_styles_describe_the_action_that_will_happen() -> None:
    targets = _targets_keyboard("en", {"users"})

    settings = SimpleNamespace(
        enabled=True,
        owner_access_enabled=False,
        mute_incoming_enabled=False,
        summary_enabled=True,
        report_enabled=False,
        membership_age_days=3,
    )
    call_security = build_panel_keyboard(
        "en",
        settings,
        show_owner_access=True,
    )

    for keyboard in (targets, call_security):
        await apply_button_style_policy(keyboard, style_mode=STYLE_ADVANCED)

    assert _button(targets, CB["BCW_TGT_USERS"]).style == ButtonStyle.DANGER
    assert _button(targets, CB["BCW_TGT_GROUPS"]).style == ButtonStyle.SUCCESS
    assert _button(targets, CB["BCW_TGT_NEXT"]).style == ButtonStyle.PRIMARY
    assert _button(targets, CB["BCW_CANCEL"]).style == ButtonStyle.DANGER
    assert _button(targets, CB["BCW_BACK_MODE"]).style == ButtonStyle.PRIMARY

    assert _button(call_security, CB["GRP_CALLSEC_TOGGLE"]).style == ButtonStyle.DANGER
    assert (
        _button(call_security, CB["GRP_CALLSEC_MUTE_IN"]).style == ButtonStyle.SUCCESS
    )
    assert _button(call_security, CB["GRP_CALLSEC_SUMMARY"]).style == ButtonStyle.DANGER


@pytest.mark.asyncio
async def test_effective_mode_uses_owner_scope_only_for_private_owner(
    monkeypatch,
) -> None:
    mode = AsyncMock(
        side_effect=lambda *, owner_user_id=None: start_custom.EffectiveStyle(
            STYLE_ADVANCED if owner_user_id == 42 else STYLE_SIMPLE,
            "owner" if owner_user_id == 42 else "global",
        )
    )
    monkeypatch.setattr(start_custom, "get_effective_style_mode", mode)
    monkeypatch.setattr("app.utils.bot_guards.is_developer", lambda user_id: False)
    monkeypatch.setattr(
        "app.repositories.user_repo.is_owner",
        AsyncMock(return_value=True),
    )

    private_owner_update = SimpleNamespace(
        chat=SimpleNamespace(type="private"),
        from_user=SimpleNamespace(id=42),
    )
    token = await bind_button_style_scope_from_update(private_owner_update)
    try:
        owner_keyboard = KeyboardFactory.back_button("en")
        await apply_button_style_policy(owner_keyboard)
    finally:
        reset_button_style_scope(token)

    global_keyboard = KeyboardFactory.back_button("en")
    await apply_button_style_policy(global_keyboard)

    assert _button(owner_keyboard, CB["NAV_BACK"]).style == ButtonStyle.PRIMARY
    assert _button(global_keyboard, CB["NAV_BACK"]).style == ButtonStyle.DEFAULT
    assert mode.await_args_list[0].kwargs == {"owner_user_id": 42}
    assert mode.await_args_list[1].kwargs == {"owner_user_id": None}


@pytest.mark.asyncio
async def test_custom_start_slot_colors_override_policy_but_simple_strips_them() -> (
    None
):
    rendered = start_custom.RenderedStartMenu(
        style_mode=STYLE_ADVANCED,
        source_scope="global",
        rows=(
            (
                start_custom.StartButtonDescriptor(
                    slot_index=1,
                    slot_key="purchase",
                    label="Purchase",
                    base_label="Purchase",
                    behavior="url",
                    url="https://example.com/purchase",
                    color_token="R",
                ),
            ),
            (
                start_custom.StartButtonDescriptor(
                    slot_index=2,
                    slot_key="test",
                    label="Test",
                    base_label="Test",
                    behavior="callback",
                    callback_data="start:cat:test",
                    color_token="B",
                ),
            ),
        ),
        uses_legacy_fallback=False,
    )
    keyboard = KeyboardFactory.start_custom_menu(rendered)
    assert keyboard is not None
    before = _shape_and_targets(keyboard)

    await apply_button_style_policy(keyboard, style_mode=STYLE_ADVANCED)
    assert keyboard.inline_keyboard[0][0].style == ButtonStyle.DANGER
    assert keyboard.inline_keyboard[1][0].style == ButtonStyle.PRIMARY

    await apply_button_style_policy(keyboard, style_mode=STYLE_SIMPLE)
    assert _shape_and_targets(keyboard) == before
    assert _all_styles(keyboard) == [ButtonStyle.DEFAULT, ButtonStyle.DEFAULT]


@pytest.mark.asyncio
async def test_native_serialization_contains_styles_and_falls_back_uncolored(
    monkeypatch,
) -> None:
    native_keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Back", callback_data=CB["NAV_BACK"])]]
    )
    await apply_button_style_policy(native_keyboard, style_mode=STYLE_ADVANCED)
    raw = await native_keyboard.inline_keyboard[0][0].write(None)
    assert raw.style.bg_primary is True

    calls: list[list[ButtonStyle]] = []

    async def unsupported_write(self, client):  # noqa: ARG001
        styles = _all_styles(self)
        calls.append(styles)
        if any(style != ButtonStyle.DEFAULT for style in styles):
            raise TypeError("unexpected keyword argument 'style'")
        return "serialized-without-styles"

    monkeypatch.setattr(InlineKeyboardMarkup, "write", unsupported_write)
    monkeypatch.setattr(
        start_custom,
        "get_effective_style_mode",
        AsyncMock(return_value=start_custom.EffectiveStyle(STYLE_ADVANCED, "global")),
    )
    install_inline_keyboard_style_hook()

    fallback_keyboard = KeyboardFactory.back_button("en")
    result = await fallback_keyboard.write(None)

    assert result == "serialized-without-styles"
    assert calls == [[ButtonStyle.PRIMARY], [ButtonStyle.DEFAULT]]
