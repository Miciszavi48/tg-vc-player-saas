"""Group settings panel command aliases and callback stability."""
from __future__ import annotations

import os
import re
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


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):

            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _group_message(text: str, *, chat_id: int = -1001, user_id: int = 42):
    message = AsyncMock()
    message.chat = SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup"))
    message.from_user = SimpleNamespace(id=user_id)
    message.text = text
    message.reply = AsyncMock()
    return message


def _get_settings_handler():
    from app.handlers import group_panel

    bot = _RecorderBot()
    group_panel.register(bot, None)
    return next(fn for fn in bot.message_handlers if fn.__name__ == "settings_command")


@pytest.fixture
def settings_handler():
    return _get_settings_handler()


@pytest.fixture
def open_panel_mocks():
    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch(
            "app.handlers.group_panel.allow_group_chat_settings_change",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.group_runtime_state_service.require_active_group",
            AsyncMock(return_value=True),
        ),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/settings", "تنظیمات", "settings"])
async def test_group_settings_aliases_open_same_panel(text, settings_handler, open_panel_mocks):
    from app.utils.i18n import t
    from app.utils.ui import CB, KeyboardFactory

    message = _group_message(text)
    client = AsyncMock()

    await settings_handler(client, message)

    message.reply.assert_awaited_once()
    args, kwargs = message.reply.await_args
    assert "پنل گروه" in args[0]
    assert args[0] == t("fa", "panels.group.title")
    markup = kwargs.get("reply_markup")
    expected = KeyboardFactory.group_panel("fa")
    assert [
        [btn.callback_data for btn in row]
        for row in markup.inline_keyboard
    ] == [
        [btn.callback_data for btn in row]
        for row in expected.inline_keyboard
    ]


def test_settings_text_alias_regex_matches_only_at_start():
    from app.handlers.group_panel import _GROUP_SETTINGS_TEXT_CMDS

    pattern = "|".join(re.escape(c) for c in _GROUP_SETTINGS_TEXT_CMDS)
    rx = re.compile(rf"^(?:{pattern})(?:\s|$)", re.IGNORECASE)
    assert rx.match("تنظیمات")
    assert rx.match("settings")
    assert rx.match("SETTINGS ")
    assert rx.match("تنظیمات ") is not None
    assert rx.match("لطفاً تنظیمات را ببینید") is None


@pytest.mark.asyncio
async def test_group_chat_filter_rejects_private_chat():
    from pyrogram.types import Message

    from app.utils.filters import group_chat_filter

    msg = MagicMock(spec=Message)
    msg.chat = SimpleNamespace(type=SimpleNamespace(value="private"))
    flt = group_chat_filter()
    assert await flt(AsyncMock(), msg) is False


@pytest.mark.asyncio
async def test_non_admin_denied_by_group_music_admin(settings_handler):
    message = _group_message("/settings", user_id=999)
    client = AsyncMock()

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch(
            "app.utils.decorators.can_use_sudo_admin_bypass",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.decorators.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.utils.decorators._deny_access", AsyncMock()) as deny_mock,
    ):
        result = await settings_handler(client, message)

    assert result is None
    message.reply.assert_not_awaited()
    deny_mock.assert_awaited_once()


def test_group_panel_callback_data_unchanged():
    from app.utils.ui import CB

    assert CB["GRP_SETTINGS"] == "grp:settings"
    assert CB["GRP_MANAGEMENT"] == "grp:management"
    assert CB["GRP_HELP"] == "grp:help"
    assert CB["GRP_SUPPORT"] == "grp:support"
    assert CB["GRP_DEFAULT_MEDIA_TYPE"] == "grp:set:default_media"


def test_group_panel_keyboard_callbacks_have_handlers():
    from pathlib import Path

    from app.utils.ui import CB, KeyboardFactory

    source = Path("app/handlers/group_panel.py").read_text(encoding="utf-8")
    cb_name_by_value = {value: name for name, value in CB.items()}

    panel_cbs = {
        btn.callback_data
        for row in KeyboardFactory.group_panel("fa").inline_keyboard
        for btn in row
        if btn.callback_data
    }
    for cb in panel_cbs:
        if cb == CB["NAV_CLOSE"]:
            continue
        cb_name = cb_name_by_value.get(cb, "")
        assert cb in source or f"CB['{cb_name}']" in source, (
            f"missing handler reference for {cb}"
        )


def test_group_settings_title_is_persian():
    from app.utils.i18n import t

    title = t("fa", "panels.group.title")
    assert "پنل گروه" in title


def test_play_commands_alert_only_callbacks_documented():
    """PB_AUDIO/VIDEO/DOWNLOAD in playback menu are hint-only by design."""
    from pathlib import Path

    callbacks_src = Path("app/handlers/callbacks.py").read_text(encoding="utf-8")
    for cb_key in ("PB_AUDIO", "PB_VIDEO", "PB_DOWNLOAD"):
        assert f"CB['{cb_key}']" in callbacks_src
    assert "show_alert=True" in callbacks_src
