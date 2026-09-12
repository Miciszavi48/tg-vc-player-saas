"""Phase 3: Enforce sudo permission matrix."""
from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.handlers import credit_commands, install, sudo_panel
from app.repositories import user_repo
from app.repositories.user_repo import SUDO_PERMISSION_FIELD_NAMES, _permission_bool
from app.utils.i18n import t
from app.utils.playback_auth import authorize_playback_action
from app.utils.sudo_permissions import (
    allow_group_chat_settings_change,
    sudo_has_permission,
)
from app.utils.ui import CB


def _all_true() -> dict[str, bool]:
    return {name: True for name in SUDO_PERMISSION_FIELD_NAMES}


def _all_false() -> dict[str, bool]:
    return {name: False for name in SUDO_PERMISSION_FIELD_NAMES}


@pytest.mark.asyncio
async def test_developer_bypasses_all_permissions():
    for field in SUDO_PERMISSION_FIELD_NAMES:
        assert await sudo_has_permission(settings.DEVELOPER_ID, field) is True


@pytest.mark.asyncio
async def test_owner_bypasses_all_permissions():
    with patch("app.utils.sudo_permissions.user_repo.is_owner", AsyncMock(return_value=True)):
        for field in SUDO_PERMISSION_FIELD_NAMES:
            assert await sudo_has_permission(910001, field) is True


@pytest.mark.asyncio
async def test_sudo_allowed_when_flag_true():
    with (
        patch("app.utils.sudo_permissions.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch(
            "app.utils.sudo_permissions.user_repo.get_sudo_permissions",
            AsyncMock(return_value=_all_true()),
        ),
    ):
        assert await sudo_has_permission(90001, "can_manage_credit") is True


@pytest.mark.asyncio
async def test_sudo_denied_when_flag_false():
    perms = _all_true()
    perms["can_manage_credit"] = False
    with (
        patch("app.utils.sudo_permissions.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch(
            "app.utils.sudo_permissions.user_repo.get_sudo_permissions",
            AsyncMock(return_value=perms),
        ),
    ):
        assert await sudo_has_permission(90001, "can_manage_credit") is False


@pytest.mark.asyncio
async def test_regular_user_denied():
    with (
        patch("app.utils.sudo_permissions.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.utils.sudo_permissions.user_repo.is_owner", AsyncMock(return_value=False)),
    ):
        assert await sudo_has_permission(77777, "can_manage_groups") is False


@pytest.mark.asyncio
async def test_unknown_field_denied_safely():
    assert await sudo_has_permission(90001, "evil_field") is False


@pytest.mark.asyncio
async def test_null_permission_defaults_to_true():
    row = Sudo(user_id=1, is_active=True)
    row.can_manage_groups = None  # type: ignore[assignment]
    perms = user_repo.sudo_permissions_from_row(row)
    assert perms["can_manage_groups"] is True
    assert _permission_bool(None) is True


@pytest.mark.asyncio
async def test_install_group_denied_before_wallet():
    client = SimpleNamespace(
        send_message=AsyncMock(),
        leave_chat=AsyncMock(),
    )
    with (
        patch("app.handlers.install.blacklist_repo.is_blacklisted", AsyncMock(return_value=False)),
        patch(
            "app.handlers.install.InstallPolicyService.determine_installer_role",
            AsyncMock(return_value="sudo"),
        ),
        patch("app.handlers.install.sudo_has_permission", AsyncMock(return_value=False)),
        patch(
            "app.handlers.install.InstallPolicyService.compute_install_cost",
            AsyncMock(return_value=100),
        ) as cost_mock,
        patch(
            "app.handlers.install.InstallPolicyService.deduct_install_fee",
            AsyncMock(return_value=True),
        ) as deduct_mock,
        patch("app.handlers.install.group_repo.upsert_group", AsyncMock()) as upsert_mock,
    ):
        await install._do_install(client, -1001, "G", "group", 90001)

    cost_mock.assert_not_awaited()
    deduct_mock.assert_not_awaited()
    upsert_mock.assert_not_awaited()
    client.leave_chat.assert_awaited()


@pytest.mark.asyncio
async def test_install_channel_denied():
    client = SimpleNamespace(send_message=AsyncMock(), leave_chat=AsyncMock())
    with (
        patch("app.handlers.install.blacklist_repo.is_blacklisted", AsyncMock(return_value=False)),
        patch(
            "app.handlers.install.InstallPolicyService.determine_installer_role",
            AsyncMock(return_value="sudo"),
        ),
        patch("app.handlers.install.sudo_has_permission", AsyncMock(return_value=False)),
        patch("app.handlers.install.channel_repo.upsert_channel", AsyncMock()) as upsert_mock,
    ):
        await install._do_install(client, -1002, "C", "channel", 90001)
    upsert_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_group_allowed_when_permission_true():
    client = SimpleNamespace(send_message=AsyncMock(), leave_chat=AsyncMock())
    with (
        patch("app.handlers.install.blacklist_repo.is_blacklisted", AsyncMock(return_value=False)),
        patch(
            "app.handlers.install.InstallPolicyService.determine_installer_role",
            AsyncMock(return_value="sudo"),
        ),
        patch("app.handlers.install.sudo_has_permission", AsyncMock(return_value=True)),
        patch(
            "app.handlers.install.InstallPolicyService.compute_install_cost",
            AsyncMock(return_value=0),
        ),
        patch("app.handlers.install.group_repo.upsert_group", AsyncMock()) as upsert_mock,
        patch("app.handlers.install.settings_repo.create_defaults", AsyncMock()),
        patch("app.handlers.install.CreditService.activate_trial", AsyncMock(side_effect=ValueError)),
        patch("app.handlers.install.log_repo.log_install", AsyncMock()),
        patch("app.handlers.install.NotificationService.notify_install", AsyncMock()),
        patch("app.handlers.install.InstallPolicyService.get_policy", AsyncMock(return_value=None)),
        patch("app.handlers.install.track_event", AsyncMock()),
    ):
        await install._do_install(client, -1003, "G", "group", 90001)
    upsert_mock.assert_awaited()


@pytest.mark.asyncio
async def test_credit_command_denied_for_non_developer():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=90001),
        chat=SimpleNamespace(id=-100, type=SimpleNamespace(value="group")),
        text="آپدیت شارژ 5",
        reply_text=AsyncMock(),
    )
    client = SimpleNamespace()
    with (
        patch("app.utils.bot_guards.is_developer", return_value=False),
        patch("app.handlers.credit_commands.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
    ):
        await credit_commands._handle_charge(client, message, is_video=False)
    charge_mock.assert_not_awaited()
    message.reply_text.assert_awaited_once_with(t("fa", "common.errors.no_access"))


@pytest.mark.asyncio
async def test_credit_command_allowed_for_developer():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=int(settings.DEVELOPER_ID)),
        chat=SimpleNamespace(id=-100, type=SimpleNamespace(value="group")),
        text="آپدیت شارژ 5",
        reply_text=AsyncMock(),
    )
    client = SimpleNamespace()
    with (
        patch("app.utils.bot_guards.is_developer", return_value=True),
        patch("app.handlers.credit_commands.CreditService.charge_managed_chat", AsyncMock()),
        patch("app.handlers.credit_commands.NotificationService.notify_credit_charge", AsyncMock()),
    ):
        await credit_commands._handle_charge(client, message, is_video=False)
    message.reply_text.assert_awaited()


@pytest.mark.asyncio
async def test_sudo_leave_blocked_when_remove_bot_false():
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=90001),
        data=CB["SUDO_LEAVE_INSTALLS"],
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), chat=SimpleNamespace(id=100)),
    )
    with patch(
        "app.handlers.sudo_panel.deny_unless_sudo_permission",
        AsyncMock(return_value=False),
    ) as deny_mock:
        allowed = await sudo_panel._require_remove_bot_permission(query)
    assert allowed is False
    deny_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_sudo_leave_confirm_rechecks_permission():
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=90001),
        answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock(), chat=SimpleNamespace(id=100)),
    )
    with (
        patch("app.handlers.sudo_panel._require_remove_bot_permission", AsyncMock(return_value=False)),
        patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock(return_value=[])),
    ):
        await sudo_panel._execute_sudo_leave_installs(SimpleNamespace(), None, query)
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_settings_sudo_bypass_requires_permission():
    with (
        patch("app.utils.sudo_permissions.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch("app.utils.sudo_permissions.user_repo.is_owner", AsyncMock(return_value=False)),
        patch(
            "app.utils.sudo_permissions.user_repo.get_sudo_permissions",
            AsyncMock(return_value={**_all_true(), "can_manage_chat_settings": False}),
        ),
        patch(
            "app.utils.sudo_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.sudo_permissions.admin_repo.is_player_owner",
            AsyncMock(return_value=False),
        ),
    ):
        allowed = await allow_group_chat_settings_change(90001, -100)
    assert allowed is False


@pytest.mark.asyncio
async def test_chat_settings_real_music_admin_still_works():
    with (
        patch("app.utils.sudo_permissions.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch(
            "app.utils.sudo_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=True),
        ),
    ):
        assert await allow_group_chat_settings_change(90001, -100) is True


@pytest.mark.asyncio
async def test_auto_admin_bypass_disabled_blocks_playback():
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=90001),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100)),
        answer=AsyncMock(),
    )
    client = MagicMock()
    client.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
    )
    cs = SimpleNamespace(security_call_enabled=True, download_enabled=True)
    with (
        patch(
            "app.utils.playback_auth.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=SimpleNamespace(has_runtime_credit=True, settings=cs)),
        ),
        patch("app.utils.playback_auth.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.utils.player_permissions.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch(
            "app.utils.player_permissions.admin_repo.is_music_admin_or_above",
            AsyncMock(return_value=False),
        ),
        patch("app.utils.player_permissions.admin_repo.is_vip", AsyncMock(return_value=False)),
    ):
        ok = await authorize_playback_action(client, query, lang="fa")
    assert ok is False


@pytest.mark.asyncio
async def test_auto_admin_bypass_enabled_allows_playback():
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=90001),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100)),
        answer=AsyncMock(),
    )
    client = MagicMock()
    client.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
    )
    cs = SimpleNamespace(security_call_enabled=True, download_enabled=True)
    with (
        patch(
            "app.utils.playback_auth.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=SimpleNamespace(has_runtime_credit=True, settings=cs)),
        ),
        patch("app.utils.playback_auth.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.utils.player_permissions.can_use_sudo_admin_bypass", AsyncMock(return_value=True)),
    ):
        ok = await authorize_playback_action(client, query, lang="fa")
    assert ok is True


@pytest.mark.asyncio
async def test_developer_still_bypasses_playback():
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100)),
        answer=AsyncMock(),
    )
    client = MagicMock()
    client.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
    )
    cs = SimpleNamespace(security_call_enabled=True, download_enabled=False)
    with (
        patch(
            "app.utils.playback_auth.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=SimpleNamespace(has_runtime_credit=True, settings=cs)),
        ),
        patch("app.utils.playback_auth.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.utils.player_permissions.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
    ):
        ok = await authorize_playback_action(client, query, lang="fa")
    assert ok is True


@pytest.mark.asyncio
async def test_sudo_panel_hides_leave_when_remove_bot_false():
    with patch(
        "app.handlers.sudo_panel.user_repo.get_sudo_permissions",
        AsyncMock(return_value={**_all_true(), "can_remove_bot": False}),
    ):
        kb = await sudo_panel._sudo_panel_kb(90001)
    cbs = {btn.callback_data for row in kb.inline_keyboard for btn in row}
    assert CB["SUDO_LEAVE_INSTALLS"] not in cbs
