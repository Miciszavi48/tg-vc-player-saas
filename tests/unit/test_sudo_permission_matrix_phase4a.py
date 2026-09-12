"""Phase 4A: Clarify auto_admin_bypass as internal-only in Developer sudo detail UI."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.database.models import Sudo
from app.handlers import dev_panel
from app.utils.i18n import t
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
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


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _sample_sudo() -> Sudo:
    return Sudo(
        user_id=90001,
        username="sudo_user",
        display_name="Sudo One",
        added_by=settings.DEVELOPER_ID,
        added_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_active=True,
        can_manage_groups=True,
        can_manage_channels=True,
        can_manage_credit=True,
        can_remove_bot=True,
        can_manage_chat_settings=True,
        auto_admin_bypass=True,
    )


@pytest.mark.asyncio
async def test_sudo_detail_shows_internal_admin_bypass_label_fa():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert t("fa", "sudo_permissions.perm_auto_admin") == "بای‌پس داخلی ادمین"
    assert t("fa", "sudo_permissions.perm_auto_admin") in text
    assert "دسترسی ادمین خودکار" not in text


@pytest.mark.asyncio
async def test_sudo_detail_shows_internal_only_note_fa():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")

    with patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    note = t("fa", "sudo_permissions.perm_auto_admin_note")
    assert note in text
    assert "تلگرام ادمین نمی‌کند" in note
    assert "لقب ادمین تلگرام تنظیم نمی‌کند" in note


@pytest.mark.asyncio
async def test_sudo_detail_internal_only_note_en():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_detail")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_SUDO_DETAIL_PREFIX']}90001:0")

    with (
        patch("app.handlers.dev_panel.user_repo.get_sudo_record", AsyncMock(return_value=_sample_sudo())),
        patch("app.handlers.dev_panel._LANG", "en"),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    note = t("en", "sudo_permissions.perm_auto_admin_note")
    assert note in text
    assert "does not promote the user as a Telegram administrator" in note
    assert "does not set a Telegram admin title" in note
