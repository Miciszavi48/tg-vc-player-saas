"""Phase B3: default_media_type group settings toggle and panel."""
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
        "call_stats": True,
        "id_call_stats": False,
        "default_media_type": "audio",
        "language": "fa",
    }
    base.update(overrides)
    return base


def test_default_media_type_button_visible_in_keyboard():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict())
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    assert CB["GRP_DEFAULT_MEDIA_TYPE"] in toggle_cbs
    assert CB["GRP_DEFAULT_MEDIA_TYPE"] == "grp:set:default_media"


def test_default_media_button_shows_audio_label():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict(default_media_type="audio"))
    btn = next(
        b
        for row in kb.inline_keyboard
        for b in row
        if b.callback_data == CB["GRP_DEFAULT_MEDIA_TYPE"]
    )
    assert "صوتی" in btn.text


@pytest.mark.asyncio
async def test_toggle_audio_to_video():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_default_media")

    query = AsyncMock()
    query.data = CB["GRP_DEFAULT_MEDIA_TYPE"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-5001)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(default_media_type="audio")
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(default_media_type="video")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-5001, "default_media_type", "video")


@pytest.mark.asyncio
async def test_toggle_video_to_audio():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_default_media")

    query = AsyncMock()
    query.data = CB["GRP_DEFAULT_MEDIA_TYPE"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-5002)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(default_media_type="video")
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(default_media_type="audio")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-5002, "default_media_type", "audio")


@pytest.mark.asyncio
async def test_invalid_default_normalizes_to_audio_on_toggle():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_default_media")

    query = AsyncMock()
    query.data = CB["GRP_DEFAULT_MEDIA_TYPE"]
    query.from_user = SimpleNamespace(id=42)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-5003)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(default_media_type="bogus")
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.allow_group_chat_settings_change", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.handlers.group_panel.invalidate_chat_settings", AsyncMock()),
        patch(
            "app.handlers.group_panel._get_settings_dict",
            AsyncMock(return_value=_settings_dict(default_media_type="video")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once_with(-5003, "default_media_type", "video")


@pytest.mark.asyncio
async def test_summary_shows_default_media_type():
    from app.handlers.group_panel import _build_group_settings_summary

    with patch(
        "app.handlers.group_panel.call_security_repo.get_call_security_settings",
        AsyncMock(return_value=None),
    ):
        text = await _build_group_settings_summary(
            _settings_dict(default_media_type="video"),
            -5004,
        )
    assert "ویدیویی" in text


@pytest.mark.asyncio
async def test_non_admin_cannot_toggle_default_media():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_default_media")

    query = AsyncMock()
    query.data = CB["GRP_DEFAULT_MEDIA_TYPE"]
    query.from_user = SimpleNamespace(id=99)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-5004)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.settings_repo.update_setting", AsyncMock()) as update_mock,
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_not_awaited()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_sudo_without_permission_blocked():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_default_media")

    query = AsyncMock()
    query.data = CB["GRP_DEFAULT_MEDIA_TYPE"]
    query.from_user = SimpleNamespace(id=77)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-5005)
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
async def test_sudo_with_permission_can_toggle():
    from app.handlers import group_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_toggle_default_media")

    query = AsyncMock()
    query.data = CB["GRP_DEFAULT_MEDIA_TYPE"]
    query.from_user = SimpleNamespace(id=77)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=-5006)
    query.message.edit_text = AsyncMock()
    query.answer = AsyncMock()

    cs = SimpleNamespace(default_media_type="audio")
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
            AsyncMock(return_value=_settings_dict(default_media_type="video")),
        ),
    ):
        await handler(AsyncMock(), query)

    update_mock.assert_awaited_once()


def test_hidden_toggles_remain_hidden():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.group_settings("fa", _settings_dict())
    toggle_cbs = {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data and btn.callback_data.startswith("grp:set:")
    }
    hidden = {
        CB["GRP_REPEAT"],
        CB["GRP_RECORD_CALL"],
    }
    assert toggle_cbs.isdisjoint(hidden)
    assert CB["GRP_SHOW_ID"] in toggle_cbs
