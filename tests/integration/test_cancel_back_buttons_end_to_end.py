from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-cancel-back-buttons.db")
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


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *_args, **_kwargs):
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *_args, **_kwargs):
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _callbacks(markup) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(markup, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _labels(markup) -> list[str]:
    return [
        btn.text
        for row in getattr(markup, "inline_keyboard", [])
        for btn in row
    ]


def _query(data: str, *, user_id: int = 123456789, chat_id: int = 100, chat_type: str = "private"):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type)),
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
    )


def _message(*, user_id: int = 123456789, chat_id: int = 100, text: str = ""):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="private")),
        text=text,
        reply=AsyncMock(),
        delete=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_generic_wz_back_clears_runtime_state_and_returns_to_token():
    from app.handlers import callbacks
    from app.services.wizard_ui import TOKEN_DEV_USERS
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_back")
    query = _query(f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_USERS}")
    client = SimpleNamespace()
    kb = SimpleNamespace(inline_keyboard=[])

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()) as clear_mock,
        patch(
            "app.handlers.callbacks.resolve_navigation_payload",
            AsyncMock(return_value=("users panel", kb)),
        ) as resolve_mock,
        patch(
            "app.handlers.callbacks.panel_callback_edit",
            AsyncMock(return_value=True),
        ) as edit_mock,
    ):
        await handler(client, query)

    clear_mock.assert_awaited_once_with(client, query.from_user.id, query.message.chat.id)
    resolve_mock.assert_awaited_once_with(
        client,
        query.from_user.id,
        query.message.chat.type.value,
        TOKEN_DEV_USERS,
        lang=callbacks._LANG,
        chat_id=query.message.chat.id,
    )
    edit_mock.assert_awaited_once_with(
        client,
        query,
        "users panel",
        kb,
        answer=False,
    )
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_generic_wz_back_edit_failure_is_visible():
    from app.handlers import callbacks
    from app.services.wizard_ui import TOKEN_DEV_USERS
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_back")
    query = _query(f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_USERS}")

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()),
        patch(
            "app.handlers.callbacks.resolve_navigation_payload",
            AsyncMock(return_value=("users panel", SimpleNamespace(inline_keyboard=[]))),
        ),
        patch(
            "app.handlers.callbacks.panel_callback_edit",
            AsyncMock(return_value=False),
        ),
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "common.errors.navigation_failed"), show_alert=True)


@pytest.mark.asyncio
async def test_broadcast_wizard_cancel_clears_state_answers_and_falls_back_on_edit_failure():
    from app.handlers import broadcast_wizard
    from app.services.wizard_ui import TOKEN_DEV_BROADCAST
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bcw_cancel")
    query = _query(CB["BCW_CANCEL"])
    query.message.edit_text.side_effect = RuntimeError("edit failed")
    query.message.edit_caption.side_effect = RuntimeError("caption failed")
    query.message.edit_reply_markup.side_effect = RuntimeError("markup failed")
    client = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace()))

    with (
        patch("app.handlers.broadcast_wizard._clear_state", AsyncMock()) as clear_mock,
        patch(
            "app.handlers.broadcast_wizard.resolve_navigation_payload",
            AsyncMock(return_value=("broadcast panel", SimpleNamespace(inline_keyboard=[]))),
        ) as resolve_mock,
    ):
        await handler.__wrapped__(client, query)

    clear_mock.assert_awaited_once_with(query.from_user.id)
    resolve_mock.assert_awaited_once_with(
        client,
        query.from_user.id,
        query.message.chat.type.value,
        TOKEN_DEV_BROADCAST,
        lang=broadcast_wizard._LANG,
    )
    client.send_message.assert_awaited_once()
    query.answer.assert_awaited_once_with(t("fa", "common.cancelled"), show_alert=False)


@pytest.mark.asyncio
async def test_broadcast_wizard_back_to_targets_preserves_payload_and_sets_step():
    from app.handlers import broadcast_wizard
    from app.utils.ui import CB

    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bcw_back_tgt")
    query = _query(CB["BCW_BACK_TGT"])
    state = {"step": "filter_selection", "payload": {"text": "hello"}, "targets": ["users"]}

    with (
        patch("app.handlers.broadcast_wizard._get_state", AsyncMock(return_value=state)),
        patch("app.handlers.broadcast_wizard._set_state", AsyncMock()) as set_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_awaited_once()
    saved_state = set_mock.await_args.args[1]
    assert saved_state["step"] == "target_selection"
    assert saved_state["payload"] == {"text": "hello"}
    query.answer.assert_awaited_once_with()
    assert CB["BCW_BACK_MODE"] in _callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])


@pytest.mark.asyncio
async def test_call_security_age_cancel_denies_without_silence():
    from app.handlers import call_security_panel
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    call_security_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "cancel_membership_age_ask")
    query = _query(CB["GRP_CALLSEC_AGE_CANCEL"], chat_id=-1001, chat_type="supergroup")

    with (
        patch(
            "app.handlers.call_security_panel.call_security_repo.get_or_create_call_security_settings",
            AsyncMock(return_value=SimpleNamespace()),
        ),
        patch("app.handlers.call_security_panel.call_security_service.can_manage", AsyncMock(return_value=False)),
        patch("app.handlers.call_security_panel.safe_stop_listening", AsyncMock()) as stop_mock,
    ):
        await handler(SimpleNamespace(), query)

    stop_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "call_security.no_permission"), show_alert=True)


@pytest.mark.asyncio
async def test_helper_proxy_cancel_clears_state_answers_and_returns_to_helper_home():
    from app.handlers import helper_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_proxy_cancel")
    query = _query("hlp:proxy:cancel")

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()) as clear_mock,
        patch("app.handlers.helper_panel._safe_helper_edit", AsyncMock(return_value="edited")) as edit_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    clear_mock.assert_awaited_once_with(query.from_user.id)
    query.answer.assert_awaited_once_with()
    edit_mock.assert_awaited_once()
    assert "helper_home" in str(edit_mock.await_args.kwargs["reply_markup"].inline_keyboard)


@pytest.mark.asyncio
async def test_helper_otp_cancel_clears_state_answers_and_returns_to_helper_home():
    from app.handlers import helper_otp_wizard
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "otp_cancel")
    query = _query("hlp:otp:cancel")

    with patch("app.handlers.helper_otp_wizard._clear_state", AsyncMock()) as clear_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    clear_mock.assert_awaited_once_with(query.from_user.id)
    query.answer.assert_awaited_once_with(t("fa", "common.cancelled"), show_alert=False)
    callbacks = _callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert f"{CB['WZ_BACK_PREFIX']}helper_home" in callbacks
    assert CB["WZ_HOME"] in callbacks


@pytest.mark.asyncio
async def test_helper_otp_back_code_preserves_phone_and_restores_code_prompt():
    from app.handlers import helper_otp_wizard

    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "otp_back_code")
    query = _query("hlp:otp:back:code")
    state = {"step": "awaiting_2fa", "phone": "+989123456789", "phone_code_hash": "hash"}

    with (
        patch("app.handlers.helper_otp_wizard._get_state", AsyncMock(return_value=state)),
        patch("app.handlers.helper_otp_wizard._set_state", AsyncMock()) as set_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    saved_state = set_mock.await_args.args[1]
    assert saved_state["step"] == "awaiting_code"
    assert saved_state["phone"] == "+989123456789"
    query.answer.assert_awaited_once_with()
    callbacks = _callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert "hlp:otp:back:phone" in callbacks
    assert "hlp:otp:cancel" in callbacks


@pytest.mark.asyncio
async def test_helper_import_back_max_calls_preserves_phone_and_restores_prompt():
    from app.handlers import helper_otp_wizard

    bot = _RecorderBot()
    helper_otp_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "import_back_max_calls")
    query = _query("hlp:imp:back:max_calls")
    state = {"step": "awaiting_import_max_joins", "phone": "+989123456789"}

    with (
        patch("app.handlers.helper_otp_wizard._get_state", AsyncMock(return_value=state)),
        patch("app.handlers.helper_otp_wizard._set_state", AsyncMock()) as set_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    saved_state = set_mock.await_args.args[1]
    assert saved_state["step"] == "awaiting_import_max_calls"
    assert saved_state["phone"] == "+989123456789"
    query.answer.assert_awaited_once_with()
    callbacks = _callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert "hlp:imp:back:phone" in callbacks
    assert "hlp:otp:cancel" in callbacks


@pytest.mark.asyncio
async def test_import_max_joins_prompt_has_working_back_callback():
    from app.handlers import helper_otp_wizard
    from app.utils.i18n import t

    msg = _message(text="20")
    state = {"step": "awaiting_import_max_calls", "phone": "+989123456789"}

    with patch("app.handlers.helper_otp_wizard._set_state", AsyncMock()):
        await helper_otp_wizard._handle_import_max_calls(msg, state)

    msg.reply.assert_awaited_once()
    assert "/cancel" not in msg.reply.await_args.args[0]
    markup = msg.reply.await_args.kwargs["reply_markup"]
    assert "hlp:imp:back:max_calls" in _callbacks(markup)
    assert "hlp:otp:cancel" in _callbacks(markup)
    assert t("fa", "common.buttons.back") in _labels(markup)


@pytest.mark.asyncio
async def test_text_editor_back_callbacks_answer_and_return_to_correct_panels():
    from app.handlers import dev_panel, owner_panel
    from app.utils.ui import CB

    dev_bot = _RecorderBot()
    dev_panel.register(dev_bot, None)
    dev_handler = _handler_by_name(dev_bot.callback_handlers, "dev_texts_back")
    dev_query = _query(CB["DEV_TEXTS_BACK"])

    with patch("app.handlers.dev_panel._build_dev_texts_text", AsyncMock(return_value="dev texts")):
        await dev_handler.__wrapped__(SimpleNamespace(), dev_query)

    dev_query.answer.assert_awaited_once_with()
    assert dev_query.message.edit_text.await_args.args[0] == "dev texts"

    own_bot = _RecorderBot()
    owner_panel.register(own_bot, None)
    own_handler = _handler_by_name(own_bot.callback_handlers, "own_texts_back")
    own_query = _query(CB["OWN_TEXTS_BACK"])

    with patch("app.handlers.owner_panel._show_owner_root", AsyncMock()) as root_mock:
        await own_handler.__wrapped__(SimpleNamespace(), own_query)

    own_query.answer.assert_awaited_once_with()
    root_mock.assert_awaited_once()


def test_no_legacy_i18n_monoliths_recreated_and_no_visible_slash_cancel():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert not (root / "app" / "resources" / "strings" / "fa.json").exists()
    assert not (root / "app" / "resources" / "strings" / "en.json").exists()
    for path in (root / "app" / "resources" / "i18n").rglob("*.json"):
        if path.name == "manifest.json":
            continue
        assert "/cancel" not in path.read_text(encoding="utf-8"), path
