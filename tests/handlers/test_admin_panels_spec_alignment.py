from __future__ import annotations

import os
import sys
from types import ModuleType
from types import SimpleNamespace
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

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions


def _callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _labels(kb) -> list[str]:
    return [btn.text for row in kb.inline_keyboard for btn in row if getattr(btn, "text", None)]


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers = []

    def on_callback_query(self, _filter=None, group=0):  # noqa: ARG002
        def decorator(func):
            self.callback_handlers.append(func)
            return func

        return decorator


def _handler_by_name(handlers, name: str):
    for handler in handlers:
        if getattr(handler, "__name__", "") == name:
            return handler
    raise AssertionError(f"handler {name} not registered")


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id, first_name="Tester", username="tester"),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def test_panel_titles_match_requested_persian_names():
    from app.utils.i18n import t

    assert "پنل برنامه نویس" in t("fa", "panels.developer.title")
    assert "سلام برنامه نویس عزیز به پنل مدیریتی خود خوش آمدید" in t("fa", "panels.developer.title")
    assert "پنل سازنده" in t("fa", "panels.owner.title")
    assert "سلام سازنده عزیز به پنل مدیریتی خود خوش آمدید" in t("fa", "panels.owner.title")
    assert "پنل سودو" in t("fa", "panels.sudo.title")
    assert "سلام سودو عزیز به پنل مدیریتی خود خوش آمدید" in t("fa", "panels.sudo.title")


def test_developer_panel_includes_implemented_developer_buttons():
    from app.utils.ui import CB, KeyboardFactory

    root = _callbacks(KeyboardFactory.developer_panel("fa"))
    settings_all = [
        btn.callback_data
        for row in KeyboardFactory.dev_sub_settings("fa").inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]
    settings = _callbacks(KeyboardFactory.dev_sub_settings("fa"))
    users = _callbacks(KeyboardFactory.dev_sub_users("fa"))
    force_join = _callbacks(KeyboardFactory.dev_sub_force_join("fa"))
    moderation = _callbacks(KeyboardFactory.dev_sub_moderation("fa"))

    assert {
        CB["DEV_STATUS"],
        CB["DEV_CAT_CREDIT"],
        CB["DEV_CAT_BROADCAST"],
        CB["DEV_CAT_LISTS"],
        CB["DEV_CAT_SETTINGS"],
        CB["DEV_CAT_USERS"],
        CB["DEV_CAT_FORCE_JOIN"],
        CB["DEV_CAT_MODERATION"],
        CB["DEV_CAT_TEXTS"],
        CB["DEV_INSTALL_POLICY"],
        CB["HLP_HOME"],
        CB["AN_HOME"],
        CB["DEV_ABOUT"],
    }.issubset(root)
    assert CB["HELP_HOME"] not in root
    assert CB["DEV_CAT_MONTHLY_INVOICE"] not in root
    assert CB["DEV_CAT_RATES"] not in root
    monthly_invoice = _callbacks(KeyboardFactory.dev_sub_monthly_invoice("fa"))
    assert monthly_invoice == {CB["NAV_BACK"]}
    assert {
        CB["DEV_BOT_ENABLED_TOGGLE"],
        CB["DEV_SUDO_PANEL_ENABLED_TOGGLE"],
        CB["DEV_MEDIA_HEALTH"],
        CB["DEV_BOT_UPDATE"],
        CB["DEV_SET_LOG_CHANNEL"],
    }.issubset(settings)
    assert CB["DEV_FORCE_JOIN_TOGGLE"] not in settings
    assert CB["DEV_FORCE_JOIN_MANAGE"] not in settings
    assert settings_all.count(CB["DEV_MEDIA_HEALTH"]) == 1
    assert {CB["DEV_ADMIN_TITLES"], CB["DEV_LIST_SUDOS"]}.issubset(users)
    assert {
        CB["DEV_FORCE_JOIN_TOGGLE"],
        CB["DEV_FORCE_JOIN_MANAGE"],
    }.issubset(force_join)
    assert {
        CB["DEV_FILTERS"],
        CB["DEV_BLACKLIST"],
        CB["DEV_BANALL_HOME"],
    }.issubset(moderation)
    assert {
        CB["DEV_FILTERS"],
        CB["DEV_BLACKLIST"],
        CB["DEV_BANALL_HOME"],
        CB["DEV_FORCE_JOIN_TOGGLE"],
        CB["DEV_FORCE_JOIN_MANAGE"],
    }.isdisjoint(users)
    assert CB["DEV_SUDO_LINK_MENU"] not in users


def test_developer_resource_report_line_shows_status():
    from app.handlers import dev_panel
    from app.repositories.admin_report_repo import ChatInstallRow

    row = ChatInstallRow(
        chat_id=-100123,
        chat_type="group",
        title="Test Group",
        invite_link=None,
        credit_days=4,
        expire_at=None,
        status="active",
        installed_by=123456789,
    )

    with patch.object(dev_panel, "_LANG", "fa"):
        text = dev_panel._format_report_line(row, "groups", 1)

    assert "وضعیت" in text
    assert "فعال" in text
    assert "اعتبار: 4 روز" in text
    assert "-100123" in text


def test_developer_statistics_labels_make_scope_clear():
    from app.utils.i18n import t

    assert "فعال" in t("fa", "panels.developer.summary_groups")
    assert "فعال" in t("fa", "panels.developer.summary_channels")
    assert "Active" in t("en", "panels.developer.summary_groups")
    assert "Active" in t("en", "panels.developer.summary_channels")

    fa_status = t(
        "fa",
        "status.bot_info",
        groups=1,
        channels=2,
        users=3,
        sudos=4,
        helpers=5,
        active_calls=6,
        uptime="1 ساعت",
        cpu=1,
        ram_used=2,
        ram_total=4,
        ram_pct=50,
        disk_used=5,
        disk_total=10,
        disk_pct=50,
    )
    assert "کاربران خصوصی ثبت‌شده (سراسری): 3" in fa_status


def test_owner_panel_exposes_only_owner_safe_root_buttons():
    from app.utils.i18n import t
    from app.utils.ui import CB, KeyboardFactory

    root = _callbacks(KeyboardFactory.owner_panel("fa"))
    labels = _labels(KeyboardFactory.owner_panel("fa"))

    assert {
        CB["OWN_STATS"],
        CB["OWN_GROUPS"],
        CB["OWN_CREDIT"],
        CB["OWN_SUDOS"],
        CB["OWN_SUDO_TITLES"],
        CB["OWN_START_TEXT"],
        CB["OWN_FORCE_JOIN_TOGGLE"],
        CB["OWN_BROADCAST"],
        CB["OWN_LISTS"],
        CB["OWN_MODERATION"],
        CB["OWN_MEDIA"],
        CB["OWN_REPORTS"],
    }.issubset(root)
    assert {
        CB["OWN_CAT_INSTALLS"],
        CB["OWN_CAT_BROADCAST"],
        CB["OWN_CAT_SETTINGS"],
        CB["OWN_CAT_USERS"],
        CB["OWN_CAT_REPORTS"],
        CB["OWN_CAT_BILLING"],
        CB["OWN_CAT_TEXTS"],
        CB["DEV_CAT_USERS"],
        CB["DEV_STATUS"],
    }.isdisjoint(root)
    assert t("fa", "panels.owner.root_sudos") in labels
    assert "سودو" in t("fa", "panels.owner.root_sudos")


def test_owner_submenus_hide_global_only_actions():
    from app.utils.i18n import t
    from app.utils.ui import CB, KeyboardFactory

    users = _callbacks(KeyboardFactory.owner_sub_users("fa"))
    user_labels = _labels(KeyboardFactory.owner_sub_users("fa"))
    reports = _callbacks(KeyboardFactory.owner_sub_reports("fa"))
    billing = _callbacks(KeyboardFactory.owner_sub_billing("fa"))

    assert CB["OWN_LIST_SUDOS"] in users
    assert CB["OWN_SUDO_MANAGE"] in users
    assert CB["OWN_REMOVE_SUDO"] in users
    assert {CB["OWN_USERS"], CB["OWN_LIST_OWNERS"], CB["OWN_REMOVE_OWNER"]}.isdisjoint(users)
    assert CB["OWN_INSTALL_REPORTS"] not in reports
    assert CB["OWN_NO_CREDIT_REPORTS"] in reports
    assert CB["OWN_SALES_REPORT"] not in reports
    assert CB["OWN_TOPUP_SUDO_WALLET"] not in billing
    credit = _callbacks(KeyboardFactory.owner_sub_credit("fa"))
    assert CB["OWN_CREDIT_ADD"] not in credit
    assert CB["OWN_CREDIT_DEDUCT"] not in credit
    assert {
        CB["OWN_LIST_GROUPS_CREDIT"],
        CB["OWN_LIST_CHANNELS_CREDIT"],
        CB["OWN_LIST_NO_CREDIT"],
    }.issubset(credit)
    assert {
        CB["OWN_CREDIT_LINKS"],
        CB["OWN_BOT_CREDIT"],
        CB["OWN_INCREASE_BOT_CREDIT"],
        CB["OWN_BOT_INVOICES"],
    }.isdisjoint(billing)
    assert t("fa", "panels.owner.sudo_manage") in user_labels
    assert all("دعوت" not in label and "شارژ کیف" not in label for label in user_labels)


def test_broadcast_history_label_is_configured():
    from app.utils.ui import KeyboardFactory

    for lang in ("fa", "en"):
        labels = _labels(KeyboardFactory.dev_sub_broadcast(lang))
        assert all("[missing:" not in label for label in labels)
        assert any("History" in label or "تاریخچه" in label for label in labels)


def test_sudo_panel_has_no_invite_referral_or_topup_ui():
    from app.utils.ui import CB, KeyboardFactory

    for lang in ("fa", "en"):
        kb = KeyboardFactory.sudo_panel(lang)
        labels = _labels(kb)
        callbacks = _callbacks(kb)
        joined = "\n".join(labels).lower()

        assert CB["DEV_SUDO_LINK_MENU"] not in callbacks
        assert CB["OWN_TOPUP_SUDO_WALLET"] not in callbacks
        assert "invite" not in joined
        assert "referral" not in joined
        assert "top-up" not in joined
        assert "دعوت" not in joined
        assert "زیرمجموعه" not in joined
        assert "شارژ کیف" not in joined


@pytest.mark.asyncio
async def test_developer_about_uses_about_text_not_help_panel():
    from app.handlers import dev_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_about")
    query = _pm_query(123456789, CB["DEV_ABOUT"])

    with (
        patch("app.handlers.dev_panel.get_about_text", AsyncMock(return_value="about text")),
        patch("app.handlers.dev_panel.render_help_home_panel", AsyncMock()) as help_home,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    help_home.assert_not_called()
    query.message.edit_text.assert_awaited_once()
    assert query.message.edit_text.await_args.args[0] == "about text"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "callback"),
    [
        ("dev_sudo_link_menu", "DEV_SUDO_LINK_MENU"),
        ("dev_sudo_link_set", "DEV_SUDO_LINK_SET"),
        ("dev_sudo_link_rm", "DEV_SUDO_LINK_RM"),
        ("dev_sudo_link_list", "DEV_SUDO_LINK_LIST"),
    ],
)
async def test_stale_sudo_link_callbacks_show_removed_notice(handler_name: str, callback: str):
    from app.handlers import dev_panel
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _pm_query(123456789, CB[callback])

    with patch("app.handlers.dev_panel._ask", AsyncMock()) as ask:
        await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "sudo_mgmt.link_removed_notice"), show_alert=True)
    query.message.edit_text.assert_not_awaited()
    ask.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "callback"),
    [
        ("own_cat_billing", "OWN_CAT_BILLING"),
        ("own_topup_sudo_wallet", "OWN_TOPUP_SUDO_WALLET"),
    ],
)
async def test_stale_owner_billing_callbacks_show_removed_notice(handler_name: str, callback: str):
    from app.handlers import owner_panel
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _pm_query(900001, CB[callback])

    with patch("app.handlers.owner_panel._ask", AsyncMock()) as ask:
        await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "wallet.topup_removed_notice"), show_alert=True)
    query.message.edit_text.assert_not_awaited()
    ask.assert_not_called()


@pytest.mark.asyncio
async def test_developer_only_decorator_rejects_non_developer():
    from app.utils.decorators import developer_only

    called = False

    @developer_only
    async def handler(_client, _query):
        nonlocal called
        called = True

    query = _pm_query(999999, "dev:status")

    with patch("app.utils.decorators.is_developer", return_value=False):
        await handler(SimpleNamespace(), query)

    assert called is False
    assert query.answer.await_count == 1


@pytest.mark.asyncio
async def test_owner_only_callback_rejects_unrelated_user():
    from app.utils.decorators import owner_or_above

    called = False

    @owner_or_above
    async def handler(_client, _query):
        nonlocal called
        called = True

    query = _pm_query(777777, "own:stats")

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=False)),
    ):
        await handler(SimpleNamespace(), query)

    assert called is False
    assert query.answer.await_count == 1


@pytest.mark.asyncio
async def test_sudo_permissions_hide_remove_install_action():
    from app.handlers import sudo_panel
    from app.utils.ui import CB

    with patch(
        "app.handlers.sudo_panel.user_repo.get_sudo_permissions",
        AsyncMock(return_value={"can_remove_bot": False}),
    ):
        kb = await sudo_panel._sudo_panel_kb(900001)

    assert CB["SUDO_LEAVE_INSTALLS"] not in _callbacks(kb)


@pytest.mark.asyncio
async def test_owner_global_broadcast_route_allows_pure_owner():
    from app.handlers import owner_panel
    from app.utils.ui import CB

    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_bc_private")
    invoke = getattr(handler, "__wrapped__", handler)
    query = _pm_query(900001, CB["OWN_BROADCAST_PRIVATE"])
    message = SimpleNamespace(text="hi", entities=None, chat=SimpleNamespace(id=900001), id=5)
    client = SimpleNamespace(ask=AsyncMock(return_value=message), send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.is_developer", return_value=False),
        patch("app.handlers.owner_panel.BroadcastServiceV2.create_broadcast", AsyncMock(return_value=SimpleNamespace(id=77))),
        patch("app.handlers.owner_panel.BroadcastServiceV2._count_recipients", AsyncMock(return_value=3)),
        patch("app.handlers.owner_panel.BroadcastServiceV2.execute", AsyncMock()),
        patch("app.handlers.owner_panel.create_logged_task"),
    ):
        await invoke(client, query)

    assert client.ask.await_count == 1
    client.send_message.assert_awaited()


def test_existing_callback_data_stayed_stable():
    from app.utils.ui import CB

    expected = {
        "DEV_STATUS": "dev:status",
        "DEV_CAT_CREDIT": "dev:cat:credit",
        "DEV_CAT_SETTINGS": "dev:cat:settings",
        "DEV_BOT_UPDATE": "dev:bot_update",
        "DEV_MEDIA_HEALTH": "dev:media_health",
        "DEV_BANALL_HOME": "dev:banall:home",
        "OWN_STATS": "own:stats",
        "OWN_CAT_INSTALLS": "own:cat:installs",
        "OWN_CAT_USERS": "own:cat:users",
        "OWN_TOPUP_SUDO_WALLET": "own:topup_sudo",
        "SUDO_INSTALLS_REPORT": "sudo:installs_report",
    }

    for key, value in expected.items():
        assert CB[key] == value


def test_owner_text_link_labels_are_creator_truthful():
    from app.services.texts_links_ui import field_label_key, get_field_spec, is_owner_editable_field
    from app.utils.i18n import t

    spec = get_field_spec("developer_link")
    assert spec is not None
    assert t("fa", field_label_key(spec, "dev")) == "لینک برنامه‌نویس"
    assert t("fa", field_label_key(spec, "owner")) == "لینک سازنده / خرید از سازنده"

    start_spec = get_field_spec("start_text")
    assert start_spec is not None
    assert is_owner_editable_field(start_spec) is True
    assert "/start خصوصی" in t("fa", field_label_key(start_spec, "owner"))


def test_fa_panel_labels_have_no_obvious_english_placeholders():
    from app.utils.i18n import t
    from app.utils.ui import KeyboardFactory

    values = [
        t("fa", "panels.developer.title"),
        t("fa", "panels.owner.title"),
        t("fa", "panels.sudo.title"),
        *_labels(KeyboardFactory.developer_panel("fa")),
        *_labels(KeyboardFactory.owner_panel("fa")),
        *_labels(KeyboardFactory.sudo_panel("fa")),
    ]

    english_placeholders = ("Developer Panel", "Owner Panel", "Sudo Panel", "Welcome")
    for value in values:
        assert "[missing:" not in value
        assert not any(term in value for term in english_placeholders)
