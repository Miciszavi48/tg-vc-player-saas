from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from sqlalchemy import func, select

from app.config.settings import settings
from app.database.engine import async_session
from app.database.models import CreditHistory, Group, GroupCredit
from app.handlers import callbacks, dev_panel, owner_panel, sudo_panel
from app.repositories import admin_report_repo
from app.services import CreditService, InstallPolicyService, wizard_ui
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
    for handler in handlers:
        if handler.__name__ == name:
            return handler
    raise AssertionError(f"handler {name!r} not registered")


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


OWNER_ID = 910001
SUDO_ID = 910002

_OWNER_MUTATION_CALLBACKS = {
    CB["OWN_CREDIT_ADD"],
    CB["OWN_CREDIT_DEDUCT"],
    CB["OWN_TOPUP_SUDO_WALLET"],
    CB["OWN_INCREASE_BOT_CREDIT"],
}

_OWNER_FINANCIAL_CALLBACKS = {
    CB["OWN_BOT_CREDIT"],
    CB["OWN_INCREASE_BOT_CREDIT"],
    CB["OWN_SALES_REPORT"],
    CB["OWN_BOT_INVOICES"],
}

_OWNER_KEYBOARD_FACTORIES = (
    KeyboardFactory.owner_panel,
    KeyboardFactory.owner_sub_groups,
    KeyboardFactory.owner_sub_credit,
    KeyboardFactory.owner_sub_lists,
    KeyboardFactory.owner_sub_moderation,
    KeyboardFactory.owner_sub_media,
    KeyboardFactory.owner_sub_sudo_titles,
    KeyboardFactory.owner_sub_installs,
    KeyboardFactory.owner_sub_broadcast,
    KeyboardFactory.owner_sub_settings,
    KeyboardFactory.owner_sub_users,
    KeyboardFactory.owner_sub_reports,
    KeyboardFactory.owner_sub_billing,
    KeyboardFactory.owner_sub_texts,
)


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _all_owner_keyboard_callbacks() -> set[str]:
    callbacks: set[str] = set()
    for factory in _OWNER_KEYBOARD_FACTORIES:
        callbacks.update(_kb_callbacks(factory("fa")))
    return callbacks


def _sample_install_row() -> admin_report_repo.ChatInstallRow:
    return admin_report_repo.ChatInstallRow(
        chat_id=-100501,
        chat_type="group",
        title="Test Group",
        invite_link="https://t.me/+test",
        credit_days=5,
        expire_at=None,
        status="active",
        installed_by=OWNER_ID,
    )


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_owner_credit_submenu_hides_mutation_controls(lang: str):
    callbacks = _kb_callbacks(KeyboardFactory.owner_sub_credit(lang))
    assert CB["OWN_CREDIT_ADD"] not in callbacks
    assert CB["OWN_CREDIT_DEDUCT"] not in callbacks
    assert {
        CB["OWN_LIST_GROUPS_CREDIT"],
        CB["OWN_LIST_CHANNELS_CREDIT"],
        CB["OWN_LIST_NO_CREDIT"],
        CB["NAV_BACK"],
    }.issubset(callbacks)


def test_no_owner_keyboard_emits_credit_mutation_callbacks():
    emitted = _all_owner_keyboard_callbacks()
    for forbidden in _OWNER_MUTATION_CALLBACKS:
        assert forbidden not in emitted


def test_owner_group_list_and_detail_keyboards_hide_credit_mutation():
    row = _sample_install_row()
    for kind in owner_panel._OWNER_GROUP_ACTIONABLE_KINDS:
        list_kb = owner_panel._owner_group_list_kb([row], kind, page=0, total_pages=1)
        detail_kb = owner_panel._owner_group_detail_kb(row, kind, page=0)
        for kb in (list_kb, detail_kb):
            callbacks = _kb_callbacks(kb)
            assert not any(
                cb.startswith(CB["OWN_GRP_CREDIT_INC_PREFIX"])
                or cb.startswith(CB["OWN_GRP_CREDIT_DEC_PREFIX"])
                for cb in callbacks
            )


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_developer_credit_mutation_ui_remains_visible(lang: str):
    callbacks = _kb_callbacks(KeyboardFactory.dev_sub_credit(lang))
    assert CB["DEV_INCREASE_CREDIT"] in callbacks
    assert CB["DEV_DECREASE_CREDIT"] in callbacks


@pytest.mark.parametrize("lang", ["fa", "en"])
def test_monetary_financial_controls_are_not_rendered(lang: str):
    root = _kb_callbacks(KeyboardFactory.developer_panel(lang))
    credit = _kb_callbacks(KeyboardFactory.dev_sub_credit(lang))
    install_policy = _kb_callbacks(KeyboardFactory.install_policy_panel(lang))

    assert CB["DEV_CAT_RATES"] not in root
    assert CB["DEV_CAT_MONTHLY_INVOICE"] not in root
    assert CB["DEV_SEND_INVOICE"] not in credit
    assert CB["DEV_INVOICE_HISTORY"] not in credit
    assert CB["DEV_INSTALL_POLICY_FEE_GROUP"] not in install_policy
    assert CB["DEV_INSTALL_POLICY_FEE_CHAN"] not in install_policy
    for owner_menu in (
        KeyboardFactory.owner_sub_lists(lang),
        KeyboardFactory.owner_sub_reports(lang),
    ):
        assert not (_kb_callbacks(owner_menu) & _OWNER_FINANCIAL_CALLBACKS)


@pytest.mark.asyncio
async def test_stale_public_pricing_callback_is_visibly_denied():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "start_pricing")
    query = _pm_query(920001, CB["START_PRICING"])

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "credit.pricing_removed"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_stale_developer_financial_callback_is_visibly_denied():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_removed_financial_surface")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_SET_BASE_RATE"])

    await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "credit.financial_surface_removed"),
        show_alert=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [wizard_ui.TOKEN_DEV_RATES, wizard_ui.TOKEN_DEV_MONTHLY_INVOICE],
)
async def test_stale_financial_wizard_return_shows_removed_notice(token: str):
    text, keyboard = await wizard_ui.resolve_navigation_payload(
        SimpleNamespace(),
        settings.DEVELOPER_ID,
        "private",
        token,
        lang="fa",
    )

    assert text == t("fa", "credit.financial_surface_removed")
    assert CB["DEV_CAT_CREDIT"] in _kb_callbacks(keyboard)
    assert CB["DEV_CAT_RATES"] not in _kb_callbacks(keyboard)
    assert CB["DEV_CAT_MONTHLY_INVOICE"] not in _kb_callbacks(keyboard)


@pytest.mark.asyncio
async def test_stale_developer_financial_callback_stops_legacy_handlers():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_removed_financial_surface")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_MONTHLY_INVOICE_SEND"])
    query.stop_propagation = Mock()

    await handler.__wrapped__(SimpleNamespace(), query)

    query.stop_propagation.assert_called_once_with()


@pytest.mark.asyncio
async def test_stale_owner_sales_callback_is_visibly_denied():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_sales_report")
    query = _pm_query(OWNER_ID, CB["OWN_SALES_REPORT"])

    await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "credit.financial_surface_removed"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_wallet_credit_and_install_fee_paths_are_inert():
    with patch("app.services.credit_service.acquire_lock", AsyncMock()) as lock_mock:
        with pytest.raises(ValueError, match="wallet_credit_disabled"):
            await CreditService.charge_with_wallet(
                -10092001,
                "group",
                30,
                sudo_user_id=settings.DEVELOPER_ID,
            )
        with pytest.raises(ValueError, match="wallet_credit_disabled"):
            await CreditService.charge_managed_chat_with_wallet(
                -10092001,
                "group",
                30,
                sudo_user_id=settings.DEVELOPER_ID,
            )
    lock_mock.assert_not_awaited()
    assert await InstallPolicyService.compute_install_cost(
        "group",
        "other",
        -10092001,
    ) == 0
    assert await InstallPolicyService.deduct_install_fee(
        -10092001,
        920001,
        50_000,
    ) is False


@pytest.mark.asyncio
async def test_post_commit_cache_failure_does_not_turn_charge_into_failure():
    chat_id = -10092002
    async with async_session() as session:
        session.add(Group(chat_id=chat_id, chat_title="Cache outage", status="active"))
        await session.commit()

    with (
        patch(
            "app.services.credit_service.acquire_lock",
            AsyncMock(return_value="lock-token"),
        ),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch(
            "app.services.credit_service.invalidate_credit",
            AsyncMock(side_effect=RuntimeError("redis down")),
        ),
        patch(
            "app.services.credit_service._clear_expired_pending_marker",
            AsyncMock(),
        ),
        patch("app.services.credit_service.track_event", AsyncMock()),
    ):
        result = await CreditService.adjust_managed_credit(
            chat_id,
            "group",
            mode="increase",
            amount=5,
            operated_by=settings.DEVELOPER_ID,
            note="cache-outage-test",
        )

    assert result.after == 5
    async with async_session() as session:
        history_count = (
            await session.execute(
                select(func.count())
                .select_from(CreditHistory)
                .where(CreditHistory.chat_id == chat_id)
            )
        ).scalar_one()
    assert history_count == 1


@pytest.mark.asyncio
async def test_owner_credit_add_callback_denied_for_non_developer():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_credit_add")
    query = _pm_query(OWNER_ID, CB["OWN_CREDIT_ADD"])

    with (
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
    ):
        await handler(SimpleNamespace(), query)

    charge_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "credit.developer_only"), show_alert=True)


@pytest.mark.asyncio
async def test_owner_credit_deduct_callback_denied_for_non_developer():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_credit_deduct")
    query = _pm_query(OWNER_ID, CB["OWN_CREDIT_DEDUCT"])

    with (
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel.CreditService.adjust_managed_credit", AsyncMock()) as adjust_mock,
    ):
        await handler(SimpleNamespace(), query)

    adjust_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "credit.developer_only"), show_alert=True)


@pytest.mark.asyncio
async def test_owner_credit_deduct_stale_callback_writes_no_history():
    chat_id = -10091001
    async with async_session() as session:
        session.add(
            GroupCredit(
                chat_id=chat_id,
                chat_type="group",
                credit_days=12,
                status="active",
            )
        )
        await session.commit()
        before_history = (
            await session.execute(
                select(func.count()).select_from(CreditHistory).where(CreditHistory.chat_id == chat_id)
            )
        ).scalar_one()

    recorder = _RecorderBot()
    owner_panel.register(recorder, None)
    handler = _handler_by_name(recorder.callback_handlers, "own_credit_deduct")
    query = _pm_query(OWNER_ID, CB["OWN_CREDIT_DEDUCT"])

    with (
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel.CreditService.adjust_managed_credit", AsyncMock()) as adjust_mock,
    ):
        await handler(SimpleNamespace(), query)

    adjust_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "credit.developer_only"), show_alert=True)

    async with async_session() as session:
        after_history = (
            await session.execute(
                select(func.count()).select_from(CreditHistory).where(CreditHistory.chat_id == chat_id)
            )
        ).scalar_one()
    assert after_history == before_history


@pytest.mark.asyncio
async def test_sudo_credit_add_callback_denied_for_non_developer():
    bot = _RecorderBot()
    sudo_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "sudo_credit_add")
    query = _pm_query(SUDO_ID, CB["SUDO_CREDIT_ADD"])

    with (
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch("app.handlers.sudo_panel._require_credit_permission", AsyncMock(return_value=True)),
        patch("app.handlers.sudo_panel._handle_credit_prompt", AsyncMock()) as prompt_mock,
    ):
        await handler(SimpleNamespace(), query)

    prompt_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "credit.developer_only"), show_alert=True)


@pytest.mark.asyncio
async def test_owner_wallet_topup_callback_returns_removed_notice():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_topup_sudo_wallet")
    query = _pm_query(OWNER_ID, CB["OWN_TOPUP_SUDO_WALLET"])

    with patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=True)):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "wallet.topup_removed_notice"), show_alert=True)


@pytest.mark.asyncio
async def test_apply_setup_charge_denies_owner_trial_mutation():
    from app.services import manager_command_service

    result = await manager_command_service.apply_setup_charge(
        -10091002,
        duration_days=2,
        user_id=OWNER_ID,
    )

    assert result.ok is False
    assert result.reason == "no_access"


@pytest.mark.asyncio
async def test_apply_setup_charge_denies_sudo_paid_mutation():
    from app.services import manager_command_service

    result = await manager_command_service.apply_setup_charge(
        -10091003,
        duration_days=10,
        user_id=SUDO_ID,
    )

    assert result.ok is False
    assert result.reason == "no_access"


def test_post_install_panel_hides_credit_controls_by_default():
    callbacks = _kb_callbacks(
        KeyboardFactory.post_install_panel("fa", 5, "owner", "https://t.me/guide")
    )
    assert CB["POST_INSTALL_INC_CREDIT"] not in callbacks
    assert CB["POST_INSTALL_DEC_CREDIT"] not in callbacks


def test_install_setup_panel_hides_charge_button_by_default():
    callbacks = _kb_callbacks(
        KeyboardFactory.install_player_setup_panel("fa", -100501, OWNER_ID, 1700000000)
    )
    assert not any(cb.startswith("Add:Fa:ShowSetCharge:") for cb in callbacks)


@pytest.mark.asyncio
async def test_owner_group_credit_inc_stale_callback_denied_for_non_developer():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_group_credit_inc")
    payload = f"{owner_panel._OWNER_KIND_ACTIVE_GROUPS}:-100501:0"
    query = _pm_query(OWNER_ID, f"{CB['OWN_GRP_CREDIT_INC_PREFIX']}{payload}")

    with (
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel._handle_owner_group_row_credit", AsyncMock()) as credit_mock,
    ):
        await handler(SimpleNamespace(), query)

    credit_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "credit.developer_only"), show_alert=True)
