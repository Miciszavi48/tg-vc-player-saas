"""Focused tests for Developer Panel hardening (2026-06-01)."""
from __future__ import annotations

import ast
import os
import sys
from datetime import date, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.database.models import MonthlyInvoice
from app.handlers import dev_panel
from app.services import monthly_invoice_service
from app.services import texts_links_ui
from app.services.wizard_ui import (
    TOKEN_DEV_CREDIT,
    TOKEN_DEV_FORCE_JOIN,
    TOKEN_DEV_INSTALL_POLICY,
    TOKEN_DEV_MONTHLY_INVOICE,
    TOKEN_DEV_MODERATION,
    wz_back_callback,
)
from app.utils.ask_result import AskResult
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


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


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


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


def _assert_no_missing_labels(kb) -> None:
    labels = [
        btn.text
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "text", None)
    ]
    assert labels
    assert all("[missing:" not in label for label in labels)
    assert all("[invalid:" not in label for label in labels)


def _literal_t_keys(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "t":
            candidate = node.args[1] if len(node.args) >= 2 else None
        elif isinstance(func, ast.Attribute) and func.attr == "t":
            candidate = node.args[1] if len(node.args) >= 2 else None
        else:
            continue
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            keys.add(candidate.value)
    return keys


def test_developer_panel_literal_i18n_keys_resolve_in_fa_and_en():
    repo_root = Path(__file__).resolve().parents[2]
    paths = [
        "app/handlers/dev_panel.py",
        "app/handlers/dev_banall_panel.py",
        "app/utils/ui.py",
        "app/services/wizard_ui.py",
        "app/services/texts_links_ui.py",
        "app/services/monthly_invoice_service.py",
    ]
    missing: list[str] = []
    for rel_path in paths:
        for key in sorted(_literal_t_keys(repo_root / rel_path)):
            for lang in ("fa", "en"):
                rendered = t(lang, key)
                if "[missing:" in rendered or "[invalid:" in rendered:
                    missing.append(f"{rel_path}:{lang}:{key}:{rendered}")
    assert not missing


def test_visible_developer_keyboards_have_no_missing_labels():
    for lang in ("fa", "en"):
        keyboards = [
            KeyboardFactory.developer_panel(lang),
            KeyboardFactory.dev_sub_credit(lang),
            KeyboardFactory.dev_sub_monthly_invoice(lang),
            KeyboardFactory.dev_sub_rates(lang),
            KeyboardFactory.dev_sub_broadcast(lang),
            KeyboardFactory.dev_sub_lists(lang),
            KeyboardFactory.dev_sub_settings(lang),
            KeyboardFactory.dev_sub_force_join(lang, force_join_enabled=True),
            KeyboardFactory.dev_sub_moderation(lang),
            KeyboardFactory.dev_sub_users(lang),
            KeyboardFactory.dev_sub_texts(lang),
            KeyboardFactory.dev_admin_titles(lang),
            KeyboardFactory.dev_banall_home(lang),
            KeyboardFactory.dev_banall_clear_confirm(lang, actor_id=settings.DEVELOPER_ID, issued_at=1000),
            KeyboardFactory.forced_membership_panel(lang, is_enabled=True, target_count=0),
            KeyboardFactory.broadcast_history(lang, []),
            KeyboardFactory.helper_home(lang, active=0, disabled=0, quarantined=0),
            KeyboardFactory.analytics_home(lang),
        ]
        for kb in keyboards:
            _assert_no_missing_labels(kb)


def test_force_join_subflow_navigation_returns_to_force_join_section():
    assert wz_back_callback(TOKEN_DEV_FORCE_JOIN) in _kb_callbacks(dev_panel._force_join_menu_kb())
    assert wz_back_callback(TOKEN_DEV_FORCE_JOIN) in _kb_callbacks(dev_panel._force_join_list_kb([], 0, 1))
    assert wz_back_callback(TOKEN_DEV_FORCE_JOIN) in _kb_callbacks(
        dev_panel._fj_remove_confirm_kb(-100123, 0, settings.DEVELOPER_ID, 1000)
    )


def test_moderation_subflow_navigation_returns_to_moderation_section():
    assert wz_back_callback(TOKEN_DEV_MODERATION) in _kb_callbacks(KeyboardFactory.dev_banall_home("fa"))
    assert CB["DEV_BANALL_HOME"] in _kb_callbacks(KeyboardFactory.dev_sub_moderation("fa"))
    assert CB["DEV_BANALL_HOME"] not in _kb_callbacks(KeyboardFactory.dev_sub_users("fa"))


@pytest.mark.asyncio
async def test_install_policy_invalid_mode_reprompts_same_panel_then_saves_once():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_install_policy_mode")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INSTALL_POLICY_MODE"])

    ask = AsyncMock(
        side_effect=[
            AskResult(message=SimpleNamespace(text="invalid_mode")),
            AskResult(message=SimpleNamespace(text="paid")),
        ]
    )
    policy = SimpleNamespace(
        trial_days=3,
        group_install_fee_irr=100,
        chan_install_fee_irr=200,
    )
    with (
        patch("app.handlers.dev_panel._ask", ask),
        patch(
            "app.handlers.dev_panel.InstallPolicyService.get_policy",
            AsyncMock(return_value=policy),
        ),
        patch(
            "app.handlers.dev_panel.InstallPolicyService.update_policy",
            AsyncMock(),
        ) as update,
        patch(
            "app.handlers.dev_panel._deliver_install_policy_outcome",
            AsyncMock(),
        ) as deliver,
    ):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    assert ask.await_count == 2
    assert t("fa", "admin.install_policy.invalid_mode") in ask.await_args_list[1].kwargs["prompt_text"]
    update.assert_awaited_once_with("paid", 3, 100, 200)
    deliver.assert_awaited_once()
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_policy_mode_save_redraws_same_panel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_install_policy_mode")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INSTALL_POLICY_MODE"])
    policy = SimpleNamespace(
        trial_days=3,
        group_install_fee_irr=100,
        chan_install_fee_irr=200,
    )

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="paid"))),
        ),
        patch(
            "app.handlers.dev_panel.InstallPolicyService.get_policy",
            AsyncMock(return_value=policy),
        ),
        patch(
            "app.handlers.dev_panel.InstallPolicyService.update_policy",
            AsyncMock(),
        ) as update,
        patch(
            "app.handlers.dev_panel._deliver_install_policy_outcome",
            AsyncMock(),
        ) as deliver,
    ):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    update.assert_awaited_once_with("paid", 3, 100, 200)
    deliver.assert_awaited_once()
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_policy_trial_save_redraws_same_panel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_install_policy_trial")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INSTALL_POLICY_TRIAL"])
    policy = SimpleNamespace(
        policy_mode="hybrid",
        group_install_fee_irr=100,
        chan_install_fee_irr=200,
    )

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="14"))),
        ),
        patch(
            "app.handlers.dev_panel.InstallPolicyService.get_policy",
            AsyncMock(return_value=policy),
        ),
        patch(
            "app.handlers.dev_panel.InstallPolicyService.update_policy",
            AsyncMock(),
        ) as update,
        patch(
            "app.handlers.dev_panel._deliver_install_policy_outcome",
            AsyncMock(),
        ) as deliver,
    ):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    update.assert_awaited_once_with("hybrid", 14, 100, 200)
    deliver.assert_awaited_once()
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_policy_whitelist_save_redraws_whitelist_panel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(
        bot.callback_handlers,
        "dev_install_policy_whitelist_add",
    )
    query = _pm_query(
        settings.DEVELOPER_ID,
        CB["DEV_INSTALL_POLICY_WHITELIST_ADD"],
    )

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="-100123"))),
        ),
        patch(
            "app.handlers.dev_panel.InstallPolicyService.add_whitelist",
            AsyncMock(),
        ) as add,
        patch(
            "app.handlers.dev_panel._deliver_install_policy_outcome",
            AsyncMock(),
        ) as deliver,
    ):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    add.assert_awaited_once_with(-100123, created_by=settings.DEVELOPER_ID)
    assert deliver.await_args.kwargs["whitelist"] is True
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_increase_credit_invalid_chat_reprompts_until_listener_stops():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_increase_credit")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INCREASE_CREDIT"])

    ask = AsyncMock(
        side_effect=[
            AskResult(message=SimpleNamespace(text="not-a-number")),
            AskResult(message=None, abort_reason="listener_stopped", user_notified=True),
        ]
    )
    with patch("app.handlers.dev_panel._ask", ask):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    assert ask.await_count == 2
    assert t("fa", "common.errors.invalid_number") in ask.await_args_list[1].kwargs["prompt_text"]
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_sudo_manage_invalid_remove_reprompts_until_listener_stops():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_manage")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_SUDO_MANAGE"])

    ask = AsyncMock(
        side_effect=[
            AskResult(message=SimpleNamespace(text="-garbage")),
            AskResult(message=None, abort_reason="listener_stopped", user_notified=True),
        ]
    )
    with patch("app.handlers.dev_panel._ask", ask):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    assert ask.await_count == 2
    assert t("fa", "common.errors.invalid_number") in ask.await_args_list[1].kwargs["prompt_text"]
    client.send_message.assert_not_awaited()


def test_sudo_link_list_back_returns_to_menu():
    cbs = _kb_callbacks(KeyboardFactory.sudo_link_list_back("fa"))
    assert CB["DEV_SUDO_LINK_MENU"] in cbs
    assert CB["NAV_BACK"] not in cbs


@pytest.mark.asyncio
async def test_texts_hub_includes_dev_texts_back():
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        _text, kb = await texts_links_ui.build_texts_hub_payload("fa", "dev")
    assert CB["DEV_TEXTS_BACK"] in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_dev_monthly_invoice_config_callback_is_visibly_disabled():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_removed_financial_surface")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_CONFIG"])

    await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "credit.financial_surface_removed"),
        show_alert=True,
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_monthly_invoice_auto_toggle_is_disabled_without_mutation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_removed_financial_surface")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_AUTO_TOGGLE"])

    with patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    set_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(
        t("fa", "credit.financial_surface_removed"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_dev_monthly_invoice_auto_toggle_disables_setting():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_auto_toggle")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_AUTO_TOGGLE"])

    with (
        patch(
            "app.handlers.dev_panel.monthly_invoice_service.get_monthly_invoice_auto_send_enabled",
            AsyncMock(side_effect=[True, False, False]),
        ),
        patch(
            "app.handlers.dev_panel.monthly_invoice_service.get_monthly_invoice_amount",
            AsyncMock(return_value=450_000),
        ),
        patch(
            "app.handlers.dev_panel.monthly_invoice_service.get_active_owner_recipients",
            AsyncMock(return_value=[SimpleNamespace(user_id=1)]),
        ),
        patch(
            "app.handlers.dev_panel.settings_repo.set_bot_setting",
            AsyncMock(),
        ) as set_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(send_message=AsyncMock()), query)

    set_mock.assert_awaited_once_with(
        monthly_invoice_service.MONTHLY_INVOICE_AUTO_SEND_ENABLED_KEY,
        "0",
        updated_by=settings.DEVELOPER_ID,
    )
    body = query.message.edit_text.await_args.args[0]
    assert "غیرفعال" in body


@pytest.mark.asyncio
async def test_dev_monthly_invoice_auto_toggle_denies_non_developer():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_auto_toggle")
    query = _pm_query(987_654, CB["DEV_MONTHLY_INVOICE_AUTO_TOGGLE"])

    with patch(
        "app.handlers.dev_panel.settings_repo.set_bot_setting",
        AsyncMock(),
    ) as set_mock:
        await handler(SimpleNamespace(send_message=AsyncMock()), query)

    set_mock.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_dev_monthly_invoice_set_amount_validates_and_saves_setting():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_set_amount")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_SET_AMOUNT"])

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="450,000"))),
        ) as ask_mock,
        patch(
            "app.handlers.dev_panel.settings_repo.set_bot_setting",
            AsyncMock(),
        ) as set_mock,
    ):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    ask_mock.assert_awaited_once()
    assert ask_mock.await_args.kwargs["return_to"] == TOKEN_DEV_MONTHLY_INVOICE
    set_mock.assert_awaited_once_with(
        monthly_invoice_service.MONTHLY_INVOICE_AMOUNT_KEY,
        "450000",
        updated_by=settings.DEVELOPER_ID,
    )
    body = client.send_message.await_args.args[1]
    assert "450000" in body
    done_cbs = _kb_callbacks(client.send_message.await_args.kwargs["reply_markup"])
    assert wz_back_callback(TOKEN_DEV_MONTHLY_INVOICE) in done_cbs


@pytest.mark.asyncio
async def test_dev_monthly_invoice_set_amount_rejects_invalid_value():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_set_amount")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_SET_AMOUNT"])

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="not-number"))),
        ),
        patch(
            "app.handlers.dev_panel.settings_repo.set_bot_setting",
            AsyncMock(),
        ) as set_mock,
    ):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    set_mock.assert_not_awaited()
    body = client.send_message.await_args.args[1]
    assert "مبلغ" in body
    done_cbs = _kb_callbacks(client.send_message.await_args.kwargs["reply_markup"])
    assert wz_back_callback(TOKEN_DEV_MONTHLY_INVOICE) in done_cbs


@pytest.mark.asyncio
async def test_dev_monthly_invoice_prepare_creates_preview_records_without_sending():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_prepare")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_PREPARE"])
    invoice = MonthlyInvoice(
        id=7001,
        owner_user_id=930_501,
        bot_identifier="ربات",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        due_at=datetime(2026, 7, 31, 23, 59, 59, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
        amount=450_000,
        status="pending",
        install_count=4,
        private_count=20,
        group_count=3,
        channel_count=1,
        developer_id=settings.DEVELOPER_ID,
    )
    result = monthly_invoice_service.MonthlyInvoiceBuildResult(
        amount_configured=True,
        amount=450_000,
        period=monthly_invoice_service.MonthlyInvoicePeriod(
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            due_at=invoice.due_at,
        ),
        previews=(
            monthly_invoice_service.MonthlyInvoicePreview(
                owner_user_id=930_501,
                invoice=invoice,
                message="متن پیش‌نمایش",
                stats=monthly_invoice_service.MonthlyInvoiceStats(
                    install_count=4,
                    private_count=20,
                    group_count=3,
                    channel_count=1,
                ),
            ),
        ),
    )

    with patch(
        "app.handlers.dev_panel.monthly_invoice_service.create_current_month_invoice_previews",
        AsyncMock(return_value=result),
    ) as preview_mock:
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    preview_mock.assert_awaited_once()
    body = query.message.edit_text.await_args.args[0]
    assert "930501" in body
    assert "هیچ پیامی ارسال نمی‌کند" in body
    assert "متن پیش‌نمایش" in body
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_monthly_invoice_send_empty_state_without_sending():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_send")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_SEND"])
    period = monthly_invoice_service.MonthlyInvoicePeriod(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        due_at=datetime(2026, 7, 31, 23, 59, 59, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
    )
    result = monthly_invoice_service.MonthlyInvoiceDeliveryResult(
        amount_configured=True,
        period=period,
        total=0,
        sent=0,
        failed=0,
        skipped=0,
        items=(),
    )

    with patch(
        "app.handlers.dev_panel.monthly_invoice_service.send_prepared_monthly_invoices",
        AsyncMock(return_value=result),
    ) as send_mock:
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    send_mock.assert_awaited_once()
    body = query.message.edit_text.await_args.args[0]
    assert "هیچ فاکتور آماده‌ای" in body
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_monthly_invoice_send_summary_hides_raw_errors():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_send")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_SEND"])
    period = monthly_invoice_service.MonthlyInvoicePeriod(
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        due_at=datetime(2026, 7, 31, 23, 59, 59, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
    )
    result = monthly_invoice_service.MonthlyInvoiceDeliveryResult(
        amount_configured=True,
        period=period,
        total=3,
        sent=1,
        failed=1,
        skipped=1,
        items=(
            monthly_invoice_service.MonthlyInvoiceDeliveryItem(1, 930_511, "sent"),
            monthly_invoice_service.MonthlyInvoiceDeliveryItem(
                2,
                930_512,
                "failed",
                "Traceback: secret stack",
            ),
            monthly_invoice_service.MonthlyInvoiceDeliveryItem(3, 930_513, "skipped", "inactive_owner"),
        ),
    )

    with patch(
        "app.handlers.dev_panel.monthly_invoice_service.send_prepared_monthly_invoices",
        AsyncMock(return_value=result),
    ):
        client = SimpleNamespace(send_message=AsyncMock())
        await handler.__wrapped__(client, query)

    body = query.message.edit_text.await_args.args[0]
    assert "ارسال‌شده: 1" in body
    assert "ناموفق: 1" in body
    assert "ردشده: 1" in body
    assert "930512" in body
    assert "930513" in body
    assert "Traceback" not in body
    assert "secret" not in body
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_monthly_invoice_send_callback_denies_non_developer():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_send")
    query = _pm_query(987_654, CB["DEV_MONTHLY_INVOICE_SEND"])

    with patch(
        "app.handlers.dev_panel.monthly_invoice_service.send_prepared_monthly_invoices",
        AsyncMock(),
    ) as send_mock:
        await handler(SimpleNamespace(send_message=AsyncMock()), query)

    send_mock.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_dev_monthly_invoice_list_and_detail_are_read_only():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    list_handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_list")
    detail_handler = _handler_by_name(bot.callback_handlers, "dev_monthly_invoice_detail")
    invoice = MonthlyInvoice(
        id=7002,
        owner_user_id=930_502,
        bot_identifier="ربات",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        due_at=datetime(2026, 7, 31, 23, 59, 59, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
        amount=450_000,
        status="pending",
        install_count=4,
        private_count=20,
        group_count=3,
        channel_count=1,
        developer_id=settings.DEVELOPER_ID,
        created_at=datetime(2026, 7, 2, 12, 0, tzinfo=monthly_invoice_service.PROJECT_TIMEZONE),
    )

    with patch(
        "app.handlers.dev_panel.monthly_invoice_repo.list_recent_invoices",
        AsyncMock(return_value=[invoice]),
    ):
        query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_LIST"])
        client = SimpleNamespace(send_message=AsyncMock())
        await list_handler.__wrapped__(client, query)

    list_body = query.message.edit_text.await_args.args[0]
    list_cbs = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert "930502" in list_body
    assert f"{CB['DEV_MONTHLY_INVOICE_DETAIL_PREFIX']}7002" in list_cbs
    client.send_message.assert_not_awaited()

    with patch(
        "app.handlers.dev_panel.monthly_invoice_repo.get_invoice_by_id",
        AsyncMock(return_value=invoice),
    ):
        detail_query = _pm_query(
            settings.DEVELOPER_ID,
            f"{CB['DEV_MONTHLY_INVOICE_DETAIL_PREFIX']}7002",
        )
        await detail_handler.__wrapped__(client, detail_query)

    detail_body = detail_query.message.edit_text.await_args.args[0]
    assert "جزئیات فاکتور ماهیانه" in detail_body
    assert "930502" in detail_body
    assert "فاکتور ماهیانه ربات شما" in detail_body
