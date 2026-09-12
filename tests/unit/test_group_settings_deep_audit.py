"""Deep audit tests for group settings panel flows and toggle wiring."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


HIDDEN_KEYBOARD_SETTING_KEYS = frozenset({
    "repeat",
    "record_call",
})

VISIBLE_KEYBOARD_SETTING_KEYS = frozenset({
    "security_call",
    "download_users",
    "auto_clean",
    "call_message",
    "auto_ready_call",
    "music_video",
    "language",
    "default_media_type",
    "call_report",
    "queue",
    "show_id",
    "show_photo",
    "show_text",
    "call_stats",
    "id_call_stats",
})

STORAGE_ONLY_CHAT_SETTINGS = frozenset({
    "vote_skip_enabled",
    "lyrics_enabled",
})

LEGACY_MODEL_COLUMNS_NO_TOGGLE = frozenset({
    "inline_enabled",
    "soundcloud_enabled",
    "spotify_enabled",
})

RUNTIME_ACTIVE_CHAT_SETTINGS = frozenset({
    "security_call_enabled",
    "download_enabled",
    "filter_enabled",
    "auto_leave_enabled",
    "announce_enabled",
    "video_enabled",
    "buttons_enabled",
    "default_media_type",
    "smart_radio_enabled",
    "show_track_id",
    "show_cover",
    "show_now_playing_text",
})


def _chat_settings_columns() -> set[str]:
    from app.database.models import ChatSettings

    return {c.name for c in ChatSettings.__table__.columns}


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def test_settings_aliases_share_filter_and_handler():
    from app.handlers import group_panel
    from app.handlers.group_panel import (
        _GROUP_SETTINGS_TEXT_CMDS,
        _group_settings_message_filter,
    )

    bot = _RecorderBot()
    group_panel.register(bot, None)
    settings_handler = _handler_by_name(bot.message_handlers, "settings_command")

    assert "تنظیمات" in _GROUP_SETTINGS_TEXT_CMDS
    assert "settings" in _GROUP_SETTINGS_TEXT_CMDS
    assert settings_handler.__name__ == "settings_command"
    assert _group_settings_message_filter() is not None


def test_setting_toggle_map_targets_valid_chat_settings_columns():
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP

    cols = _chat_settings_columns()
    for cb_data, field in _SETTING_TOGGLE_MAP.items():
        assert cb_data.startswith("grp:set:")
        assert field in cols, f"{field} missing on ChatSettings"


def test_default_media_type_on_chat_settings_model():
    cols = _chat_settings_columns()
    assert "default_media_type" in cols


def test_group_settings_keyboard_shows_only_runtime_active_toggles():
    from app.utils.ui import CB, KeyboardFactory

    sd = {k: False for k in [
        "music_video", "security_call", "repeat", "download_users",
        "call_message", "auto_clean", "queue", "auto_ready_call",
        "call_report", "record_call", "show_id", "show_photo", "show_text",
        "call_stats", "id_call_stats",
    ]}
    sd["default_media_type"] = "audio"
    sd["language"] = "fa"
    kb = KeyboardFactory.group_settings("fa", sd)
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    assert toggle_cbs == {
        CB["GRP_SECURITY_CALL"],
        CB["GRP_DOWNLOAD_USERS"],
        CB["GRP_AUTO_CLEAN"],
        CB["GRP_CALL_MESSAGE"],
        CB["GRP_AUTO_READY_CALL"],
        CB["GRP_MUSIC_VIDEO"],
        CB["GRP_LANGUAGE"],
        CB["GRP_DEFAULT_MEDIA_TYPE"],
        CB["GRP_CALL_REPORT"],
        CB["GRP_QUEUE"],
        CB["GRP_SHOW_ID"],
        CB["GRP_SHOW_PHOTO"],
        CB["GRP_SHOW_TEXT"],
        CB["GRP_CALL_STATS"],
        CB["GRP_ID_CALL_STATS"],
        # PANEL-04: per-chat service-message cleanup (migration 0035 column
        # chat_settings.service_clean_enabled, handled by group_panel's
        # _SETTING_TOGGLE_MAP).
        CB["GRP_SERVICE_CLEAN"],
    }
    assert CB["GRP_REPEAT"] not in toggle_cbs


def test_group_settings_keyboard_callbacks_have_handlers():
    from pathlib import Path

    from app.handlers.group_panel import _GROUP_SETTING_TOGGLE_REGEX, _SETTING_TOGGLE_MAP
    from app.utils.ui import CB, KeyboardFactory

    source = Path("app/handlers/group_panel.py").read_text(encoding="utf-8")
    assert "_GROUP_SETTING_TOGGLE_REGEX" in source

    sd = {k: False for k in [
        "music_video", "security_call", "repeat", "download_users",
        "call_message", "auto_clean", "queue", "auto_ready_call",
        "call_report", "record_call", "show_id", "show_photo", "show_text",
        "call_stats", "id_call_stats",
    ]}
    sd["default_media_type"] = "audio"
    sd["language"] = "fa"
    kb = KeyboardFactory.group_settings("fa", sd)
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    for cb in toggle_cbs:
        if cb == CB["GRP_DEFAULT_MEDIA_TYPE"]:
            assert "grp_toggle_default_media" in source
        elif cb == CB["GRP_LANGUAGE"]:
            assert "grp_toggle_language" in source
        elif cb == CB["GRP_CALL_STATS"]:
            assert "grp_toggle_call_stats" in source
        elif cb == CB["GRP_ID_CALL_STATS"]:
            assert "grp_toggle_id_call_stats" in source
        else:
            assert cb in _SETTING_TOGGLE_MAP or cb in source


def test_root_group_panel_callbacks_unchanged():
    from app.utils.ui import CB, KeyboardFactory

    panel_cbs = [
        btn.callback_data
        for row in KeyboardFactory.group_panel("fa").inline_keyboard
        for btn in row
        if btn.callback_data
    ]
    assert panel_cbs == [
        CB["GRP_SETTINGS"],
        CB["GRP_MANAGEMENT"],
        CB["GRP_HELP"],
        CB["GRP_SUPPORT"],
        CB["NAV_CLOSE"],
    ]


@pytest.mark.asyncio
async def test_toggle_setting_refreshes_settings_panel():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = AsyncMock()
    query.data = CB["GRP_DOWNLOAD_USERS"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1001)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(download_enabled=True)
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch("app.handlers.group_panel._get_settings_dict", AsyncMock(return_value={
            "music_video": True,
            "security_call": False,
            "repeat": False,
            "download_users": False,
            "call_message": True,
            "auto_clean": False,
            "queue": False,
            "auto_ready_call": False,
            "call_report": True,
            "record_call": True,
            "show_id": True,
            "show_photo": True,
            "show_text": True,
            "default_media_type": "audio",
            "call_stats": True,
            "id_call_stats": False,
        })),
    ):
        await toggle_handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-1001, "download_enabled", False)
    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_sudo_without_chat_settings_permission_blocked_on_toggle():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = AsyncMock()
    query.data = CB["GRP_SECURITY_CALL"]
    query.from_user = SimpleNamespace(id=77)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1002)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=True)),
        patch("app.utils.decorators.admin_repo.is_music_admin_or_above", AsyncMock(return_value=True)),
        patch("app.utils.player_permissions.can_open_group_settings", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.notify_sudo_permission_denied", AsyncMock()) as deny_mock,
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
    ):
        await toggle_handler(AsyncMock(), query)

    deny_mock.assert_awaited_once()
    update_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_all_requires_owner_and_shows_confirm():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    clear_handler = _handler_by_name(bot.callback_handlers, "grp_clear_all")

    query = AsyncMock()
    query.data = CB["GRP_CLEAR_ALL"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1003)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.time.time", return_value=1_700_000_000),
    ):
        await clear_handler(AsyncMock(), query)

    markup = query.message.edit_text.await_args.kwargs.get("reply_markup")
    callbacks = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    ]
    assert any(cb.startswith(CB["GRP_CLEAR_CONFIRM_PREFIX"]) for cb in callbacks)


@pytest.mark.asyncio
async def test_nav_back_in_group_returns_group_panel():
    from app.handlers import callbacks
    from app.utils.i18n import t
    from app.utils.ui import CB, KeyboardFactory

    bot = _RecorderBot()
    callbacks.register(bot, None)
    nav_back = _handler_by_name(bot.callback_handlers, "nav_back")

    query = AsyncMock()
    query.data = CB["NAV_BACK"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup"))
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with (
        patch("app.utils.player_permissions.can_open_group_panel", AsyncMock(return_value=True)),
        patch("app.handlers.callbacks.panel_callback_edit", AsyncMock(return_value=True)) as edit_mock,
    ):
        await nav_back(AsyncMock(), query)

    edit_mock.assert_awaited_once()
    args = edit_mock.await_args.args
    kwargs = edit_mock.await_args.kwargs
    assert args[2] == t("fa", "panels.group.title")
    assert args[3].inline_keyboard == KeyboardFactory.group_panel("fa").inline_keyboard
    assert kwargs.get("answer") is False


@pytest.mark.asyncio
async def test_wz_home_in_group_resolves_to_group_panel():
    from app.services.wizard_ui import resolve_navigation_payload
    from app.utils.i18n import t
    from app.utils.ui import KeyboardFactory

    client = AsyncMock()
    with patch(
        "app.utils.player_permissions.can_open_group_panel",
        AsyncMock(return_value=True),
    ) as can_open:
        text, kb = await resolve_navigation_payload(
            client,
            42,
            "supergroup",
            None,
            lang="fa",
            chat_id=-100123,
        )
    assert "پنل گروه" in text
    assert text == t("fa", "panels.group.title")
    assert kb.inline_keyboard == KeyboardFactory.group_panel("fa").inline_keyboard
    can_open.assert_awaited_once_with(client, -100123, 42)


@pytest.mark.asyncio
async def test_wz_home_in_group_denies_without_group_panel_permission():
    from app.services.wizard_ui import resolve_navigation_payload
    from app.utils.i18n import t
    from app.utils.ui import KeyboardFactory

    client = AsyncMock()
    with (
        patch(
            "app.utils.player_permissions.can_open_group_panel",
            AsyncMock(return_value=False),
        ) as can_open,
        patch(
            "app.services.wizard_ui.KeyboardFactory.group_panel",
            MagicMock(),
        ) as group_panel,
    ):
        text, kb = await resolve_navigation_payload(
            client,
            42,
            "supergroup",
            None,
            lang="fa",
            chat_id=-100123,
        )

    assert text == t("fa", "common.errors.no_access")
    assert kb.inline_keyboard == KeyboardFactory.back_button("fa").inline_keyboard
    can_open.assert_awaited_once_with(client, -100123, 42)
    group_panel.assert_not_called()


@pytest.mark.asyncio
async def test_filter_words_respects_filter_enabled_off():
    from app.handlers.filter_words import register as register_filter_words

    bot = MagicMock()
    handlers = []
    bot.on_message = lambda *a, **k: (lambda fn: (handlers.append(fn), fn)[1])
    register_filter_words(bot, None)
    watcher = handlers[0]

    message = AsyncMock()
    message.from_user = SimpleNamespace(id=99)
    message.chat = SimpleNamespace(id=-1004)
    message.text = "badword here"
    message.delete = AsyncMock()
    message.continue_propagation = MagicMock()

    cs = SimpleNamespace(filter_enabled=False)
    with (
        patch("app.utils.bot_guards.is_developer", return_value=False),
        patch("app.handlers.filter_words.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.filter_words._load_filter_words", AsyncMock(return_value=["badword"])),
    ):
        await watcher(AsyncMock(), message)

    message.delete.assert_not_called()
    message.continue_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_stale_hidden_toggle_callback_still_updates_db():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    toggle_handler = _handler_by_name(bot.callback_handlers, "grp_toggle_setting")

    query = AsyncMock()
    query.data = CB["GRP_REPEAT"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-1005)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(vote_skip_enabled=False)
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch("app.handlers.group_panel._get_settings_dict", AsyncMock(return_value={
            "music_video": False,
            "security_call": False,
            "repeat": True,
            "download_users": False,
            "call_message": False,
            "auto_clean": False,
            "queue": False,
            "auto_ready_call": False,
            "call_report": False,
            "record_call": False,
            "show_id": False,
            "show_photo": False,
            "show_text": False,
            "default_media_type": "audio",
            "call_stats": False,
            "id_call_stats": False,
        })),
    ):
        await toggle_handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-1005, "vote_skip_enabled", True)


def test_storage_only_settings_documented():
    from app.handlers.group_panel import _SETTING_TOGGLE_MAP
    from app.database.models import ChatSettings

    mapped = set(_SETTING_TOGGLE_MAP.values())
    model_cols = {c.name for c in ChatSettings.__table__.columns}
    assert STORAGE_ONLY_CHAT_SETTINGS.issubset(mapped)
    assert LEGACY_MODEL_COLUMNS_NO_TOGGLE.issubset(model_cols)
    assert LEGACY_MODEL_COLUMNS_NO_TOGGLE.isdisjoint(mapped)
    runtime_in_toggle_map = RUNTIME_ACTIVE_CHAT_SETTINGS - {"default_media_type"}
    assert runtime_in_toggle_map.issubset(mapped)


def test_group_settings_fa_title_persian():
    from app.utils.i18n import t

    assert "پنل گروه" in t("fa", "panels.group.title")
    assert "[missing:" not in t("fa", "panels.group.settings_title")


def test_help_support_accessible_without_music_admin_filter():
    from app.handlers.group_panel import _grp_any

    assert _grp_any is not None
