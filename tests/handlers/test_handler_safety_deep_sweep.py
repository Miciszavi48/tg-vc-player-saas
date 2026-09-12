"""Deep handler safety regression tests for no-response callback/message paths."""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-handler-safety.db")
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
from app.handlers import (
    analytics_panel,
    credit_commands,
    force_join_panel,
    helper_panel,
    owner_panel,
)
from app.services import wizard_ui
from app.services.wizard_ui import TOKEN_HELPER_HOME
from app.utils.decorators import developer_only
from app.utils.i18n import t
from app.utils.redis_keys import helper_proxy_state_key, wizard_return_key
from app.utils.ui import CB

ROOT = Path(__file__).resolve().parents[2]


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _noop(*args, **kwargs):  # noqa: ANN002, ANN003
                def _decorator(fn):
                    return fn

                return _decorator

            return _noop
        raise AttributeError(name)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.store.pop(key, None)
        return len(keys)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _pm_query(data: str, *, user_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id or settings.DEVELOPER_ID, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            text="panel",
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


def _dev_message(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        text=text,
        caption=None,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        id=42,
        reply=AsyncMock(),
        reply_text=AsyncMock(),
        stop_propagation=MagicMock(),
    )


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_slow_analytics_callback_answers_before_report_work():
    bot = _RecorderBot()
    analytics_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "an_yesterday")
    query = _pm_query(CB["AN_YESTERDAY"])
    order: list[str] = []

    async def answer(*args, **kwargs):  # noqa: ANN002, ANN003
        order.append("answer")

    async def report(*args, **kwargs):  # noqa: ANN002, ANN003
        order.append("report")
        return {"range": "yesterday"}

    query.answer = AsyncMock(side_effect=answer)
    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="bot")))

    with patch("app.handlers.analytics_panel.get_report", AsyncMock(side_effect=report)):
        await handler(client, query)

    assert order[:2] == ["answer", "report"]
    query.message.edit_text.assert_awaited_once()


def test_ask_start_callbacks_have_early_acknowledgement_static_guard():
    checks = {
        "app/handlers/dev_panel.py": [
            "dev_increase_credit",
            "dev_decrease_credit",
            "dev_remove_owner",
            "dev_remove_sudo",
            "dev_sudo_link_set",
            "dev_sudo_link_rm",
        ],
        "app/handlers/owner_panel.py": [
            "own_increase_bot_credit",
            "own_topup_sudo_wallet",
            "_handle_broadcast",
        ],
        "app/handlers/force_join_panel.py": ["fm_add"],
    }
    for rel_path, names in checks.items():
        src = _source(rel_path)
        tree = ast.parse(src)
        functions = {
            node.name: ast.get_source_segment(src, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        }
        for name in names:
            body = functions[name]
            assert "await query.answer(" in body
            assert body.index("await query.answer(") < body.index("_ask(") if "_ask(" in body else True


@pytest.mark.asyncio
async def test_invalid_callback_data_gets_visible_feedback():
    bot = _RecorderBot()
    force_join_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "fm_remove_invalid")
    query = _pm_query("fm:rm:not-a-channel")

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)


@pytest.mark.asyncio
async def test_stale_callback_data_gets_visible_feedback():
    bot = _RecorderBot()
    force_join_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "fm_remove_confirm")
    query = _pm_query(f"fm:rm:do:-100123:{settings.DEVELOPER_ID}:1")

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)


@pytest.mark.asyncio
async def test_missing_object_callback_gets_visible_feedback():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_detail")
    query = _pm_query("hlp:d:404")
    client = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(username="bot")))

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch("app.handlers.helper_panel._fetch_helper", AsyncMock(return_value=None)),
    ):
        await handler(client, query)

    query.message.edit_text.assert_awaited_once()
    assert t("fa", "common.errors.try_later") in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_owner_forged_callback_gets_not_in_scope_feedback():
    query = _pm_query("own:sp:t:g:999:0", user_id=222)
    sudo = SimpleNamespace(user_id=999, added_by=111)

    await owner_panel._deny_owner_sudo_not_in_scope(query, sudo)

    query.answer.assert_awaited_once_with(t("fa", "owner_mgmt.sudo_not_in_scope"), show_alert=True)


@pytest.mark.asyncio
async def test_developer_only_callback_denial_is_visible():
    calls: list[str] = []

    @developer_only
    async def protected(client, query):  # noqa: ANN001
        calls.append("called")

    query = _pm_query("dev:status", user_id=555)
    await protected(SimpleNamespace(), query)

    assert calls == []
    query.answer.assert_awaited_once_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_helper_proxy_invalid_input_replies_in_active_wizard():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "hlp_proxy_input")
    redis = _FakeRedis()
    await redis.set(
        helper_proxy_state_key(settings.DEVELOPER_ID),
        '{"step": "awaiting_proxy", "helper_id": 7}',
    )
    message = _dev_message("not a proxy")

    with patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)):
        await handler(SimpleNamespace(), message)

    message.reply.assert_awaited_once()
    assert t("fa", "admin.helpers.proxy_invalid") in message.reply.await_args.args[0]
    message.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_missing_state_reply_for_wizard_looking_input():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "hlp_proxy_input")
    message = _dev_message("socks5://proxy.example:1080")

    redis = _FakeRedis()
    await redis.set(
        wizard_return_key(settings.DEVELOPER_ID),
        TOKEN_HELPER_HOME,
    )
    with patch("app.handlers.helper_panel.get_redis", AsyncMock(return_value=redis)):
        await handler(SimpleNamespace(), message)

    assert t("fa", "admin.helpers.proxy_session_expired") in message.reply.await_args.args[0]
    message.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_explicit_credit_command_non_sudo_gets_denial():
    message = _dev_message("update charge 5")
    message.from_user.id = 777
    client = SimpleNamespace()

    with patch("app.handlers.credit_commands.user_repo.is_sudo_or_above", AsyncMock(return_value=False)):
        await credit_commands._handle_charge(client, message, is_video=False)

    message.reply_text.assert_awaited_once_with(t("fa", "common.errors.no_access"))


@pytest.mark.asyncio
async def test_navigation_message_not_modified_is_harmless():
    class MessageNotModified(Exception):
        pass

    message = SimpleNamespace(
        text="same",
        caption=None,
        chat=SimpleNamespace(id=100),
        edit_text=AsyncMock(side_effect=MessageNotModified("MESSAGE_NOT_MODIFIED")),
        edit_caption=AsyncMock(),
        delete=AsyncMock(),
    )
    client = SimpleNamespace(send_message=AsyncMock())

    await wizard_ui.safe_edit_navigation_message(client, message, "same")

    client.send_message.assert_not_awaited()
    message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_or_inaccessible_message_fallback_sends_new_message():
    message = SimpleNamespace(
        text="old",
        caption=None,
        chat=SimpleNamespace(id=100),
        edit_text=AsyncMock(side_effect=RuntimeError("MESSAGE_ID_INVALID")),
        edit_caption=AsyncMock(side_effect=RuntimeError("MESSAGE_ID_INVALID")),
        delete=AsyncMock(),
    )
    client = SimpleNamespace(send_message=AsyncMock())

    await wizard_ui.safe_edit_navigation_message(client, message, "new")

    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.args[:2] == (100, "new")


def test_no_broad_callback_handler_silently_returns_on_parse_failure():
    checks = {
        "app/handlers/broadcast_panel.py": ["bc_detail_invalid", "bc_cancel_invalid"],
        "app/handlers/force_join_panel.py": ["fm_remove_malformed", "fm_remove_invalid"],
        "app/handlers/helper_panel.py": ["hlp_state_action_malformed", "hlp_rotate_key_malformed"],
        "app/handlers/dev_banall_panel.py": ["dev_banall_clear_malformed"],
        "app/handlers/group_panel.py": ["grp_clear_malformed"],
        "app/handlers/owner_panel.py": ["own_sudo_perm_malformed"],
    }
    for rel_path, names in checks.items():
        src = _source(rel_path)
        tree = ast.parse(src)
        functions = {
            node.name: ast.get_source_segment(src, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        }
        for name in names:
            body = functions[name]
            assert "query.answer" in body
            assert (
                "invalid_callback" in body
                or "invalid_permission" in body
                or "try_later" in body
            )


def test_no_critical_handler_uses_except_pass():
    critical_files = [
        "app/handlers/analytics_panel.py",
        "app/handlers/broadcast_wizard.py",
        "app/handlers/force_join_panel.py",
        "app/handlers/helper_panel.py",
        "app/handlers/helper_otp_wizard.py",
        "app/handlers/dev_panel.py",
        "app/handlers/owner_panel.py",
        "app/services/wizard_ui.py",
        "app/utils/playback_auth.py",
    ]
    for rel_path in critical_files:
        src = _source(rel_path)
        assert "except Exception:\n        pass" not in src
        assert "except:\n" not in src


def test_callback_data_snapshot_unchanged_for_deep_sweep_surfaces():
    assert CB["FM_ADD"] == "fm:add"
    assert CB["FM_REMOVE"] == "fm:rm"
    assert CB["DEV_INCREASE_CREDIT"] == "dev:credit:inc"
    assert CB["DEV_DECREASE_CREDIT"] == "dev:credit:dec"
    assert CB["OWN_TOPUP_SUDO_WALLET"] == "own:topup_sudo"
    assert CB["OWN_SALES_REPORT"] == "own:sales_report"
    assert CB["HLP_DETAIL_PREFIX"] == "hlp:d:"
    assert CB["NAV_CLOSE"] == "nav:close"
    assert CB["NAV_START"] == "nav:start"


def test_existing_helper_otp_live_silence_regression_static_guard():
    src = _source("app/handlers/helper_otp_wizard.py")
    assert "group=HELPER_OTP_INPUT_GROUP" in src
    assert "OTP text handler entered" in src
    assert "otp_sending_code" in src
    assert "safe_stop_listening(client, message.chat.id, user_id=user_id)" in src


def test_group_guard_blacklist_replies_before_stop():
    src = _source("app/handlers/group_guard.py")
    assert "common.errors.blacklisted" in src
    assert "stop_propagation" in src


def test_unknown_callback_fallback_registered():
    src = _source("app/handlers/callbacks.py")
    assert "unknown_callback_fallback" in src
    assert "common.errors.unknown_callback" in src
    assert "FALLBACK_CALLBACK_GROUP" in src


def test_priority_command_group_used_for_core_text_commands():
    for rel_path in (
        "app/handlers/help_center.py",
        "app/handlers/credit_commands.py",
        "app/handlers/group_panel.py",
        "app/handlers/playback.py",
    ):
        src = _source(rel_path)
        assert "PRIORITY_COMMAND_GROUP" in src


def test_fsm_escape_uses_clear_wizard_without_stop_on_escape():
    for rel_path in (
        "app/handlers/broadcast_wizard.py",
        "app/handlers/helper_otp_wizard.py",
        "app/handlers/helper_panel.py",
    ):
        src = _source(rel_path)
        assert "clear_wizard_and_allow_command" in src
