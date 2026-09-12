"""Call Security panel: UI, permissions, credit gate, callbacks."""
from __future__ import annotations

import os
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
        self.callback_handlers: list = []

    def on_callback_query(self, *_args, **_kwargs):
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


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


def test_grp_callsec_callbacks_present_in_cb():
    from app.utils.ui import CB

    assert CB["GRP_CALLSEC"] == "grp:callsec"
    assert CB["GRP_CALLSEC_TOGGLE"] == "grp:callsec:toggle"
    assert CB["GRP_CALLSEC_BACK"] == "grp:callsec:back"


def test_call_security_button_in_group_settings_keyboard():
    from app.utils.ui import CB, KeyboardFactory

    sd = {
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
    kb = KeyboardFactory.group_settings("fa", sd)
    cbs = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert CB["GRP_CALLSEC"] in cbs


def test_build_panel_text_includes_mute_unsupported_note_when_needed():
    from app.database.models import CallSecuritySettings
    from app.services.call_security_service import (
        CallSecurityCapabilities,
        build_panel_text,
    )

    settings = CallSecuritySettings(
        chat_id=1,
        enabled=True,
        mute_incoming_enabled=True,
        summary_enabled=False,
        report_enabled=False,
        membership_age_days=7,
    )
    caps = CallSecurityCapabilities(mute_enforcement=False)
    text = build_panel_text("fa", settings, caps)
    assert "وضعیت میوت" in text
    assert "پشتیبانی نمی‌شود" in text


def test_build_panel_text_distinguishes_raw_api_available_no_active_call():
    from app.database.models import CallSecuritySettings
    from app.services.call_security_service import (
        CallSecurityCapabilities,
        build_panel_text,
    )

    settings = CallSecuritySettings(
        chat_id=1,
        enabled=True,
        mute_incoming_enabled=True,
        membership_age_days=7,
    )
    caps = CallSecurityCapabilities(
        mute_enforcement=False,
        raw_mute_api_available=True,
        active_call_known=False,
        helper_available=False,
        mute_status_reason="no_active_call",
    )
    text = build_panel_text("fa", settings, caps)
    assert "تماس فعال نیست" in text
    assert "پشتیبانی نمی‌شود" not in text


@pytest.mark.asyncio
async def test_toggle_enabled_denied_without_credit():
    from app.services import call_security_service

    settings = SimpleNamespace(
        enabled=False,
        owner_access_enabled=False,
        mute_incoming_enabled=False,
        summary_enabled=False,
        report_enabled=False,
        membership_age_days=7,
    )
    with (
        patch(
            "app.services.call_security_service.call_security_repo.get_or_create_call_security_settings",
            AsyncMock(return_value=settings),
        ),
        patch(
            "app.services.call_security_service.can_manage",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.call_security_service.has_active_credit",
            AsyncMock(return_value=False),
        ),
    ):
        updated, err = await call_security_service.toggle_field(1, "enabled", 99)
    assert updated is None
    assert err == "call_security.no_credit"


@pytest.mark.asyncio
async def test_open_call_security_denies_unprivileged_user():
    from app.handlers import call_security_panel

    bot = _RecorderBot()
    call_security_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "open_call_security")
    query = _group_query("grp:callsec", user_id=999)

    with (
        patch(
            "app.handlers.call_security_panel._can_open_panel",
            AsyncMock(return_value=False),
        ),
    ):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited()
    assert query.message.edit_text.await_count == 0


@pytest.mark.asyncio
async def test_installer_sees_owner_access_button():
    from app.database.models import CallSecuritySettings
    from app.handlers import call_security_panel
    from app.utils.i18n import t

    query = _group_query("grp:callsec", user_id=42)
    client = SimpleNamespace()
    settings = CallSecuritySettings(chat_id=-100123, membership_age_days=7)
    captured_markup = None

    async def _capture_edit(_client, _query, _text, reply_markup, **kwargs):
        nonlocal captured_markup
        captured_markup = reply_markup
        return True

    with (
        patch(
            "app.handlers.call_security_panel.call_security_repo.get_or_create_call_security_settings",
            AsyncMock(return_value=settings),
        ),
        patch(
            "app.handlers.call_security_panel.call_security_service.can_toggle_owner_access",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_security_panel.call_security_service.get_panel_capabilities",
            AsyncMock(return_value=SimpleNamespace(mute_enforcement=False)),
        ),
        patch(
            "app.handlers.call_security_panel.panel_callback_edit",
            side_effect=_capture_edit,
        ),
    ):
        await call_security_panel.render_call_security_panel(client, query, None)

    markup = captured_markup
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any(t("fa", "call_security.btn_owner_access") in label for label in labels)


@pytest.mark.asyncio
async def test_player_owner_can_manage_call_security_without_owner_access_toggle():
    from app.services import call_security_service

    user_id = 777
    chat_id = -100123
    disabled = SimpleNamespace(owner_access_enabled=False)
    enabled = SimpleNamespace(owner_access_enabled=True)
    client = MagicMock()
    with (
        patch("app.services.call_security_service.is_developer", return_value=False),
        patch("app.services.call_security_service.is_installer", AsyncMock(return_value=False)),
        patch(
            "app.utils.player_permissions.can_manage_call_security",
            AsyncMock(return_value=True),
        ),
    ):
        assert await call_security_service.can_manage(
            user_id, chat_id, disabled, client=client,
        ) is True
        assert await call_security_service.can_manage(
            user_id, chat_id, enabled, client=client,
        ) is True


@pytest.mark.asyncio
async def test_owner_access_enabled_still_gates_privileged_member_not_panel_manage():
    from app.services import call_security_service

    user_id = 777
    chat_id = -100123
    disabled = SimpleNamespace(owner_access_enabled=False)
    enabled = SimpleNamespace(owner_access_enabled=True)
    with (
        patch("app.services.call_security_service.is_developer", return_value=False),
        patch("app.services.call_security_service.is_installer", AsyncMock(return_value=False)),
        patch(
            "app.services.call_security_service.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.call_security_service.admin_repo.is_vip",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.call_security_service.admin_repo.is_player_owner",
            AsyncMock(return_value=True),
        ),
    ):
        assert await call_security_service.is_privileged_member(user_id, chat_id, disabled) is False
        assert await call_security_service.is_privileged_member(user_id, chat_id, enabled) is True


@pytest.mark.asyncio
async def test_sudo_can_open_call_security_without_local_role_or_flags():
    from app.handlers import call_security_panel

    client = MagicMock()
    user_id = 555
    chat_id = -100123
    with (
        patch(
            "app.handlers.call_security_panel.call_security_service.is_installer",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.can_manage_call_security",
            AsyncMock(return_value=True),
        ),
    ):
        assert await call_security_panel._can_open_panel(user_id, chat_id, client) is True


@pytest.mark.asyncio
async def test_music_admin_denied_call_security_open():
    from app.handlers import call_security_panel

    client = MagicMock()
    user_id = 888
    chat_id = -100123
    with (
        patch(
            "app.handlers.call_security_panel.call_security_service.is_installer",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.can_manage_call_security",
            AsyncMock(return_value=False),
        ),
        patch("app.handlers.call_security_panel.is_developer", return_value=False),
        patch(
            "app.handlers.call_security_panel.admin_repo.is_player_deputy_or_above",
            AsyncMock(return_value=False),
        ),
    ):
        assert await call_security_panel._can_open_panel(user_id, chat_id, client) is False


@pytest.mark.asyncio
async def test_deputy_can_open_call_security_via_permissions():
    from app.handlers import call_security_panel

    client = MagicMock()
    user_id = 999
    chat_id = -100123
    with (
        patch(
            "app.handlers.call_security_panel.call_security_service.is_installer",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.player_permissions.can_manage_call_security",
            AsyncMock(return_value=True),
        ),
    ):
        assert await call_security_panel._can_open_panel(user_id, chat_id, client) is True


@pytest.mark.asyncio
async def test_open_call_security_callback_uses_can_open_panel():
    from app.handlers import call_security_panel

    bot = _RecorderBot()
    call_security_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "open_call_security")
    query = _group_query("grp:callsec", user_id=42)
    client = MagicMock()

    with (
        patch(
            "app.handlers.call_security_panel._can_open_panel",
            AsyncMock(return_value=True),
        ) as open_mock,
        patch(
            "app.handlers.call_security_panel.render_call_security_panel",
            AsyncMock(),
        ),
    ):
        await handler(client, query)

    open_mock.assert_awaited_once_with(42, -100123, client)
    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_age_button_asks_for_membership_age_days_and_invalid_replies():
    from app.handlers import call_security_panel
    from app.utils.i18n import t

    bot = _RecorderBot()
    call_security_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "ask_membership_age_days")
    query = _group_query("grp:callsec:age", user_id=42)
    client = SimpleNamespace(send_message=AsyncMock())
    settings = SimpleNamespace(
        enabled=True,
        owner_access_enabled=False,
        mute_incoming_enabled=False,
        summary_enabled=False,
        report_enabled=False,
        membership_age_days=7,
    )
    response = SimpleNamespace(text="bad")
    with (
        patch(
            "app.handlers.call_security_panel.call_security_repo.get_or_create_call_security_settings",
            AsyncMock(return_value=settings),
        ),
        patch(
            "app.handlers.call_security_panel.call_security_service.can_manage",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.call_security_panel.safe_ask",
            AsyncMock(return_value=response),
        ) as ask_mock,
        patch(
            "app.handlers.call_security_panel.deliver_ask_outcome",
            AsyncMock(),
        ) as deliver_mock,
    ):
        await handler(client, query)

    ask_mock.assert_awaited_once()
    assert ask_mock.await_args.args[2] == "call_security.ask_membership_age"
    deliver_mock.assert_awaited_once()
    assert deliver_mock.await_args.args[3] == t("fa", "call_security.invalid_membership_age")
    client.send_message.assert_not_called()


def test_call_security_ui_label_uses_membership_age_not_account_age():
    from app.database.models import CallSecuritySettings
    from app.services.call_security_service import CallSecurityCapabilities, build_panel_text

    text = build_panel_text(
        "fa",
        CallSecuritySettings(chat_id=1, membership_age_days=7),
        CallSecurityCapabilities(mute_enforcement=False),
    )
    assert "قدمت عضویت" in text
    assert "قدمت اکانت" not in text
