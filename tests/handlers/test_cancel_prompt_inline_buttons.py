from __future__ import annotations

import json
import os
import sys
from pathlib import Path
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

    class ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


ROOT = Path(__file__).resolve().parents[2]


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *_args, **_kwargs):
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _callbacks(markup) -> set[str]:
    return {
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _labels(markup) -> list[str]:
    return [
        btn.text
        for row in markup.inline_keyboard
        for btn in row
    ]


def _group_query(data: str, user_id: int = 42, chat_id: int = -100123):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def _iter_i18n_strings(value, path: str = ""):
    if isinstance(value, str):
        yield path, value
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield from _iter_i18n_strings(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_i18n_strings(child, f"{path}[{index}]")


def test_split_i18n_user_prompts_do_not_advertise_slash_cancel():
    offenders: list[str] = []
    for file_path in sorted((ROOT / "app" / "resources" / "i18n").rglob("*.json")):
        if file_path.name == "manifest.json":
            continue
        data = json.loads(file_path.read_text(encoding="utf-8"))
        for key_path, text in _iter_i18n_strings(data):
            if "/cancel" in text:
                offenders.append(f"{file_path.relative_to(ROOT)}::{key_path}")

    assert offenders == []


def test_hidden_cancel_fallback_aliases_still_match():
    from app.utils.text_commands import is_escape_command

    assert is_escape_command("/cancel") is True
    assert is_escape_command("cancel") is True
    assert is_escape_command("لغو") is True


def test_generic_cancel_keyboard_has_only_inline_cancel_button():
    from app.services.wizard_ui import TOKEN_DEV_USERS, build_cancel_kb
    from app.utils.i18n import t
    from app.utils.ui import CB

    markup = build_cancel_kb("fa", TOKEN_DEV_USERS)
    callbacks = _callbacks(markup)
    labels = _labels(markup)

    assert f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_DEV_USERS}" in callbacks
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_USERS}" not in callbacks
    assert CB["WZ_HOME"] not in callbacks
    assert t("fa", "common.buttons.cancel_inline") in labels
    assert t("fa", "common.buttons.back") not in labels


def test_dev_admin_titles_uses_back_without_home():
    from app.utils.ui import CB, KeyboardFactory

    markup = KeyboardFactory.dev_admin_titles("fa")
    callbacks = _callbacks(markup)

    assert f"{CB['WZ_BACK_PREFIX']}dev_users" in callbacks
    assert CB["WZ_HOME"] not in callbacks


@pytest.mark.asyncio
async def test_safe_ask_passes_inline_keyboard_and_keeps_cancel_exclusion():
    from app.services.wizard_ui import TOKEN_DEV_USERS, build_cancel_kb
    from app.utils import safe_ask

    markup = build_cancel_kb("fa", TOKEN_DEV_USERS)
    response = SimpleNamespace(text="123")
    client = SimpleNamespace(ask=AsyncMock(return_value=response))

    with patch("app.utils.safe_ask.safe_stop_listening", AsyncMock(return_value=False)):
        result = await safe_ask.safe_ask(
            client,
            100,
            "ask.user_id",
            reply_markup=markup,
            user_id=123456789,
        )

    assert result is response
    assert client.ask.await_args.kwargs["reply_markup"] is markup

    source = (ROOT / "app" / "utils" / "safe_ask.py").read_text(encoding="utf-8")
    assert '~filters.command(["start", "cancel"])' in source


@pytest.mark.asyncio
async def test_dev_owner_and_broadcast_asks_send_only_inline_cancel_buttons():
    from app.handlers import broadcast, dev_panel, owner_panel
    from app.services.wizard_ui import TOKEN_DEV_BROADCAST, TOKEN_DEV_USERS, TOKEN_OWNER_ROOT
    from app.utils.ui import CB

    async def _exercise_dev_ask():
        client = SimpleNamespace(ask=AsyncMock(return_value=SimpleNamespace(text="123")))
        with (
            patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
            patch("app.handlers.dev_panel.safe_stop_listening", AsyncMock(return_value=False)),
        ):
            await dev_panel._ask(
                client,
                100,
                "ask.user_id",
                user_id=123456789,
                return_to=TOKEN_DEV_USERS,
            )
        return client.ask.await_args.kwargs["reply_markup"]

    async def _exercise_owner_ask():
        client = SimpleNamespace(ask=AsyncMock(return_value=SimpleNamespace(text="123")))
        with (
            patch("app.handlers.owner_panel.remember_return_token", AsyncMock()),
            patch("app.handlers.owner_panel.safe_stop_listening", AsyncMock(return_value=False)),
        ):
            await owner_panel._ask(
                client,
                100,
                "ask.user_id",
                user_id=123456789,
                return_to=TOKEN_OWNER_ROOT,
            )
        return client.ask.await_args.kwargs["reply_markup"]

    async def _exercise_broadcast_ask():
        client = SimpleNamespace(ask=AsyncMock(return_value=SimpleNamespace(text="payload")))
        with (
            patch("app.handlers.broadcast.remember_return_token", AsyncMock()),
            patch("app.handlers.broadcast.safe_stop_listening", AsyncMock(return_value=False)),
        ):
            await broadcast._ask(
                client,
                100,
                "ask.broadcast_msg",
                user_id=123456789,
                return_to=TOKEN_DEV_BROADCAST,
            )
        return client.ask.await_args.kwargs["reply_markup"]

    dev_callbacks = _callbacks(await _exercise_dev_ask())
    owner_callbacks = _callbacks(await _exercise_owner_ask())
    broadcast_callbacks = _callbacks(await _exercise_broadcast_ask())

    assert f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_DEV_USERS}" in dev_callbacks
    assert f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_OWNER_ROOT}" in owner_callbacks
    assert f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_DEV_BROADCAST}" in broadcast_callbacks
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_USERS}" not in dev_callbacks
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_OWNER_ROOT}" not in owner_callbacks
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_BROADCAST}" not in broadcast_callbacks
    assert CB["WZ_HOME"] not in dev_callbacks | owner_callbacks | broadcast_callbacks


@pytest.mark.asyncio
async def test_call_security_membership_age_ask_has_inline_back_button():
    from app.handlers import call_security_panel
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    call_security_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "ask_membership_age_days")
    query = _group_query(CB["GRP_CALLSEC_AGE"])
    settings = SimpleNamespace(membership_age_days=7)
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.call_security_panel.call_security_repo.get_or_create_call_security_settings",
            AsyncMock(return_value=settings),
        ),
        patch("app.handlers.call_security_panel.call_security_service.can_manage", AsyncMock(return_value=True)),
        patch(
            "app.handlers.call_security_panel.call_security_service.get_membership_age_threshold_days",
            return_value=7,
        ),
        patch("app.handlers.call_security_panel.safe_ask", AsyncMock(return_value=None)) as ask_mock,
        patch("app.handlers.call_security_panel.remember_panel_from_query", AsyncMock()),
    ):
        await handler(client, query)

    markup = ask_mock.await_args.kwargs["reply_markup"]
    assert CB["GRP_CALLSEC_AGE_CANCEL"] in _callbacks(markup)
    assert t("fa", "common.buttons.back") in _labels(markup)
    assert "/cancel" not in t("fa", "call_security.ask_membership_age", days=7)


@pytest.mark.asyncio
async def test_call_security_membership_age_cancel_returns_to_panel():
    from app.handlers import call_security_panel
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    call_security_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "cancel_membership_age_ask")
    query = _group_query(CB["GRP_CALLSEC_AGE_CANCEL"])
    client = SimpleNamespace(stop_listening=AsyncMock())
    settings = SimpleNamespace(membership_age_days=7)

    with (
        patch(
            "app.handlers.call_security_panel.call_security_repo.get_or_create_call_security_settings",
            AsyncMock(return_value=settings),
        ),
        patch("app.handlers.call_security_panel.call_security_service.can_manage", AsyncMock(return_value=True)),
        patch("app.handlers.call_security_panel.safe_stop_listening", AsyncMock()) as stop_mock,
        patch("app.handlers.call_security_panel.render_call_security_panel", AsyncMock()) as render_mock,
    ):
        await handler(client, query)

    stop_mock.assert_awaited_once_with(client, query.message.chat.id, user_id=query.from_user.id)
    query.answer.assert_awaited_once_with(t("fa", "common.cancelled"), show_alert=False)
    render_mock.assert_awaited_once()
