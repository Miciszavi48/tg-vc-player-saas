"""Phase B1: per-chat language toggle in group settings panel."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    "call_security",
    "security_call",
    "download_users",
    "auto_clean",
    "service_clean",
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


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _settings_dict(**overrides) -> dict:
    base = {
        "music_video": True,
        "security_call": False,
        "repeat": False,
        "download_users": True,
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
        "language": "fa",
    }
    base.update(overrides)
    return base


def test_language_button_appears_in_group_settings_keyboard():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict(language="fa"))
    toggle_cbs = [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    ]
    assert CB["GRP_LANGUAGE"] in toggle_cbs
    lang_btn = next(
        btn
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data == CB["GRP_LANGUAGE"]
    )
    assert "فارسی" in lang_btn.text or "Persian" in lang_btn.text


@pytest.mark.asyncio
async def test_language_toggle_fa_to_en():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_language")

    query = AsyncMock()
    query.data = CB["GRP_LANGUAGE"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-2001)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(language="fa")
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(language="en")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-2001, "language", "en")
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_language_toggle_en_to_fa():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_language")

    query = AsyncMock()
    query.data = CB["GRP_LANGUAGE"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-2002)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(language="en")
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(language="fa")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-2002, "language", "fa")


@pytest.mark.asyncio
async def test_language_toggle_refreshes_settings_panel():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_language")

    query = AsyncMock()
    query.data = CB["GRP_LANGUAGE"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-2003)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(language="fa")
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()),
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(language="en")),
        ),
    ):
        await handler(AsyncMock(), query)

    assert query.message.edit_text.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_non_admin_cannot_toggle_language():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_language")

    query = AsyncMock()
    query.data = CB["GRP_LANGUAGE"]
    query.from_user = SimpleNamespace(id=99)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-2004)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.utils.decorators.admin_repo.is_music_admin_or_above", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_sudo_without_chat_settings_permission_blocked():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_language")

    query = AsyncMock()
    query.data = CB["GRP_LANGUAGE"]
    query.from_user = SimpleNamespace(id=77)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-2005)
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
        await handler(AsyncMock(), query)

    deny_mock.assert_awaited_once()
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sudo_with_chat_settings_permission_can_toggle():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_language")

    query = AsyncMock()
    query.data = CB["GRP_LANGUAGE"]
    query.from_user = SimpleNamespace(id=88)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-2006)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(language="fa")
    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=True)),
        patch("app.utils.decorators.admin_repo.is_music_admin_or_above", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(language="en")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-2006, "language", "en")


@pytest.mark.asyncio
async def test_developer_can_toggle_language():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_language")

    query = AsyncMock()
    query.data = CB["GRP_LANGUAGE"]
    query.from_user = SimpleNamespace(id=123456789)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-2007)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(language="en")
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(language="fa")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-2007, "language", "fa")


def test_invalid_language_normalizes_to_fa():
    from app.utils.i18n import normalize_lang

    assert normalize_lang(None) == "fa"
    assert normalize_lang("") == "fa"
    assert normalize_lang("de") == "fa"
    assert normalize_lang("EN") == "en"


def test_visible_toggles_remain_visible():
    from app.utils.ui import _GRP_VISIBLE_SETTING_TOGGLES

    assert set(_GRP_VISIBLE_SETTING_TOGGLES.keys()) == VISIBLE_KEYBOARD_SETTING_KEYS


def test_hidden_toggles_remain_hidden_from_keyboard():
    from app.utils.ui import CB, KeyboardFactory, _GRP_VISIBLE_SETTING_TOGGLES

    sd = _settings_dict()
    kb = KeyboardFactory.group_settings("fa", sd)
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data not in {CB["NAV_BACK"], CB["WZ_HOME"]}
    }
    visible_callbacks = set(_GRP_VISIBLE_SETTING_TOGGLES.values())
    assert toggle_cbs == visible_callbacks
    for hidden_key in HIDDEN_KEYBOARD_SETTING_KEYS:
        hidden_cb = {
            "repeat": CB["GRP_REPEAT"],
            "record_call": CB["GRP_RECORD_CALL"],
            "show_id": CB["GRP_SHOW_ID"],
            "show_photo": CB["GRP_SHOW_PHOTO"],
            "show_text": CB["GRP_SHOW_TEXT"],
        }[hidden_key]
        assert hidden_cb not in toggle_cbs
