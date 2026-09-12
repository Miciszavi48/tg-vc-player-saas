from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pyrogram

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

from app.config.settings import settings
from app.handlers import callbacks, group_panel, call_security_panel
from app.services import call_security_service
from unittest.mock import MagicMock
from app.services import wizard_ui
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
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


class _FakeScalarResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    async def execute(self, stmt):  # noqa: ANN001
        return _FakeScalarResult()


def _fake_async_session():
    return _FakeSession()


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _kb_callbacks(kb) -> set[str]:
    if kb is None:
        return set()
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
            text="menu",
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


def _group_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="GroupAdmin"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
            text="menu",
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_normal_user_cannot_forge_wz_back_dev_settings():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_back")
    query = _pm_query(900003, f"{CB['WZ_BACK_PREFIX']}{wizard_ui.TOKEN_DEV_SETTINGS}")
    root_kb = KeyboardFactory.start_menu("fa", {})

    with (
        patch("app.services.wizard_ui.build_private_root_payload", AsyncMock(return_value=("regular root", root_kb))),
        patch(
            "app.services.wizard_ui.AdminDashboardService.get_general_summary",
            AsyncMock(side_effect=AssertionError("developer summary rendered")),
        ),
    ):
        await handler(SimpleNamespace(), query)

    assert query.message.edit_text.await_count == 1
    text = query.message.edit_text.call_args.args[0]
    assert t("fa", "common.errors.no_access") in text
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    assert CB["DEV_FORCE_JOIN_TOGGLE"] not in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_normal_user_cannot_forge_wz_cancel_dev_settings():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_cancel")
    client = SimpleNamespace(stop_listening=AsyncMock())
    query = _pm_query(900003, f"{CB['WZ_CANCEL_PREFIX']}{wizard_ui.TOKEN_DEV_SETTINGS}")
    fake_redis = SimpleNamespace(delete=AsyncMock())
    root_kb = KeyboardFactory.start_menu("fa", {})

    with (
        patch("app.services.wizard_ui.get_redis", AsyncMock(return_value=fake_redis)),
        patch("app.services.wizard_ui.build_private_root_payload", AsyncMock(return_value=("regular root", root_kb))),
        patch(
            "app.services.wizard_ui.AdminDashboardService.get_general_summary",
            AsyncMock(side_effect=AssertionError("developer summary rendered")),
        ),
    ):
        await handler(client, query)

    assert fake_redis.delete.await_count == 1
    text = query.message.edit_text.call_args.args[0]
    assert t("fa", "common.errors.no_access") in text
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    assert CB["DEV_FORCE_JOIN_TOGGLE"] not in _kb_callbacks(kb)


@pytest.mark.asyncio
async def test_developer_can_use_privileged_wizard_navigation():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_back")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['WZ_BACK_PREFIX']}{wizard_ui.TOKEN_DEV_SETTINGS}")

    with patch(
        "app.services.wizard_ui.AdminDashboardService.get_general_summary",
        AsyncMock(
            return_value={
                "force_join_enabled": False,
                "auto_leave_enabled": False,
                "trial_enabled": False,
                "required_channels": 0,
            }
        ),
    ):
        await handler(SimpleNamespace(), query)

    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["DEV_BOT_ENABLED_TOGGLE"] in cbs
    assert CB["DEV_INSTALL_LIMITS"] in cbs


@pytest.mark.asyncio
async def test_owner_can_use_owner_text_wizard_navigation():
    with (
        patch("app.services.wizard_ui.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.services.wizard_ui.build_texts_hub_payload", AsyncMock(return_value=("owner texts", None))) as hub_mock,
    ):
        text, kb = await wizard_ui.resolve_navigation_payload(
            SimpleNamespace(),
            900001,
            "private",
            wizard_ui.TOKEN_OWNER_TEXTS,
            lang="fa",
        )

    assert text == "owner texts"
    assert kb is None
    hub_mock.assert_awaited_once_with("fa", "owner", owner_user_id=900001)


@pytest.mark.asyncio
async def test_sudo_role_root_wizard_navigation_still_works():
    root_kb = KeyboardFactory.start_menu("fa", {})

    with patch("app.services.wizard_ui.build_private_root_payload", AsyncMock(return_value=("sudo root", root_kb))):
        text, kb = await wizard_ui.resolve_navigation_payload(
            SimpleNamespace(),
            900002,
            "private",
            wizard_ui.TOKEN_ROLE_ROOT,
            lang="fa",
        )

    assert text == "sudo root"
    assert kb is root_kb


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [f"{CB['WZ_BACK_PREFIX']}unknown", CB["WZ_BACK_PREFIX"]])
async def test_unknown_or_empty_wizard_tokens_do_not_crash(data: str):
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_back")
    query = _pm_query(900003, data)
    root_kb = KeyboardFactory.start_menu("fa", {})

    with patch("app.services.wizard_ui.build_private_root_payload", AsyncMock(return_value=("regular root", root_kb))):
        await handler(SimpleNamespace(), query)

    assert query.message.edit_text.await_count == 1
    assert query.message.edit_text.call_args.args[0] == "regular root"


@pytest.mark.asyncio
async def test_music_admin_cannot_trigger_group_vip_demote_callback():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_vip_demote")
    query = _group_query(600001, f"{CB['GRP_VIP_DEMOTE_PREFIX']}42")

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.admin_repo.demote_vip", AsyncMock()) as demote_mock,
    ):
        await handler(SimpleNamespace(), query)

    demote_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_music_admin_cannot_trigger_group_clear_all_callback():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_clear_all")
    query = _group_query(600001, CB["GRP_CLEAR_ALL"])

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.admin_repo.clear_music_admins", AsyncMock()) as clear_music_mock,
        patch("app.handlers.group_panel.admin_repo.clear_video_admins", AsyncMock()) as clear_video_mock,
    ):
        await handler(SimpleNamespace(), query)

    clear_music_mock.assert_not_awaited()
    clear_video_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_player_owner_can_trigger_group_vip_demote_callback():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_vip_demote")
    query = _group_query(600002, f"{CB['GRP_VIP_DEMOTE_PREFIX']}42")

    with (
        patch("app.utils.decorators.user_repo.is_sudo_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=True)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.utils.bot_guards.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.admin_repo.demote_vip", AsyncMock()) as demote_mock,
        patch("app.handlers.group_panel.admin_repo.count_player_vips", AsyncMock(return_value=1)),
        patch(
            "app.handlers.group_panel.admin_repo.get_player_vips_page",
            AsyncMock(return_value=([], 1, 0)),
        ),
    ):
        await handler(SimpleNamespace(), query)

    demote_mock.assert_awaited_once_with(-1001, 42)
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_player_owner_cannot_demote_zero_vip_id_callback():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_vip_demote")
    query = _group_query(600002, f"{CB['GRP_VIP_DEMOTE_PREFIX']}0")

    with (
        patch("app.utils.decorators.user_repo.is_sudo_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=True)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.utils.bot_guards.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.handlers.group_panel.admin_repo.demote_vip", AsyncMock()) as demote_mock,
    ):
        await handler(SimpleNamespace(), query)

    demote_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.invalid_callback"),
        show_alert=True,
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_player_owner_can_trigger_group_clear_all_callback():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_clear_all")
    query = _group_query(600002, CB["GRP_CLEAR_ALL"])

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.can_use_sudo_admin_bypass", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo_or_above", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=True)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_music_admins", AsyncMock()) as clear_music_mock,
        patch("app.handlers.group_panel.admin_repo.clear_video_admins", AsyncMock()) as clear_video_mock,
    ):
        await handler(SimpleNamespace(), query)

    clear_music_mock.assert_not_awaited()
    clear_video_mock.assert_not_awaited()
    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()
    keyboard = query.message.edit_text.call_args.kwargs["reply_markup"]
    callbacks = _kb_callbacks(keyboard)
    assert any(cb.startswith(CB["GRP_CLEAR_CONFIRM_PREFIX"]) for cb in callbacks)
    assert any(cb.startswith(CB["GRP_CLEAR_CANCEL_PREFIX"]) for cb in callbacks)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["grp:vip:rm:", "grp:vip:rm:abc"])
async def test_malformed_group_vip_callbacks_do_not_crash(data: str):
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_vip_demote_invalid")
    query = _group_query(600001, data)

    with patch("app.handlers.group_panel.admin_repo.demote_vip", AsyncMock()) as demote_mock:
        await handler(SimpleNamespace(), query)

    demote_mock.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_number"), show_alert=True)


@pytest.fixture
def mock_client_new():
    client = MagicMock()
    return client

@pytest.fixture
def mock_query_new():
    query = MagicMock()
    query.message.chat.id = -100123
    query.from_user.id = 42
    query.answer = AsyncMock()
    query.stop_propagation = MagicMock()
    return query

@pytest.mark.asyncio
@pytest.mark.parametrize("callback_data, handler_name", [
    (CB["NAV_BACK"], "grp_nav_back"),
    (CB["WZ_HOME"], "grp_wz_home"),
])
async def test_group_panel_navigation_denies_unauthorized_users(mock_client_new, mock_query_new, callback_data, handler_name):
    mock_query_new.data = callback_data
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)

    with patch("app.utils.player_permissions.can_open_group_panel", AsyncMock(return_value=False)) as mock_can_open,          patch("app.handlers.group_panel._navigate_to_group_root", AsyncMock()) as mock_navigate,          patch("app.handlers.group_panel.KeyboardFactory.group_panel", MagicMock()) as mock_kb:
        with pytest.raises(pyrogram.StopPropagation):
            await handler(mock_client_new, mock_query_new)
        mock_can_open.assert_awaited_once_with(mock_client_new, mock_query_new.message.chat.id, mock_query_new.from_user.id)
        mock_query_new.answer.assert_awaited_once()
        assert mock_query_new.answer.call_args[1].get("show_alert") is True
        mock_query_new.stop_propagation.assert_called_once()
        mock_navigate.assert_not_awaited()
        mock_kb.assert_not_called()

@pytest.mark.asyncio
@pytest.mark.parametrize("callback_data, handler_name", [
    (CB["NAV_BACK"], "grp_nav_back"),
    (CB["WZ_HOME"], "grp_wz_home"),
])
async def test_group_panel_navigation_allows_authorized_users(mock_client_new, mock_query_new, callback_data, handler_name):
    mock_query_new.data = callback_data
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)

    with patch("app.utils.player_permissions.can_open_group_panel", AsyncMock(return_value=True)) as mock_can_open,          patch("app.handlers.group_panel._navigate_to_group_root", AsyncMock(return_value=True)) as mock_navigate,          patch("app.handlers.group_panel._answer_navigation_result", AsyncMock()),          patch("app.handlers.group_panel.clear_runtime_state", AsyncMock()):
        await handler(mock_client_new, mock_query_new)
        mock_can_open.assert_awaited_once_with(mock_client_new, mock_query_new.message.chat.id, mock_query_new.from_user.id)
        mock_navigate.assert_awaited_once_with(mock_client_new, mock_query_new)


@pytest.mark.asyncio
async def test_global_wz_home_group_context_denies_before_group_panel_render(mock_client_new):
    query = MagicMock()
    query.message.chat.id = -100123
    query.message.chat.type.value = "supergroup"
    query.from_user.id = 42
    query.answer = AsyncMock()

    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "wz_home")

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()),
        patch("app.utils.player_permissions.can_open_group_panel", AsyncMock(return_value=False)) as can_open,
        patch("app.handlers.callbacks.panel_callback_edit", AsyncMock(return_value=True)) as edit_mock,
        patch("app.services.wizard_ui.KeyboardFactory.group_panel", MagicMock()) as group_panel_mock,
    ):
        await handler(mock_client_new, query)

    can_open.assert_awaited_once_with(mock_client_new, -100123, 42)
    edit_mock.assert_awaited_once()
    assert edit_mock.await_args.args[2] == t("fa", "common.errors.no_access")
    group_panel_mock.assert_not_called()

@pytest.mark.asyncio
async def test_call_security_installer_bypass_removed(mock_client_new):
    user_id = 42
    chat_id = -100123
    with patch("app.utils.player_permissions.can_manage_call_security", AsyncMock(return_value=False)),          patch("app.handlers.call_security_panel.is_developer", return_value=False),          patch("app.handlers.call_security_panel.admin_repo.is_player_deputy_or_above", AsyncMock(return_value=False)):
        assert await call_security_panel._can_open_panel(user_id, chat_id, mock_client_new) is False

    with patch("app.utils.player_permissions.can_manage_call_security", AsyncMock(return_value=False)),          patch("app.services.call_security_service.is_developer", return_value=False),          patch("app.services.call_security_service.admin_repo.is_player_deputy_or_above", AsyncMock(return_value=False)),          patch("app.services.call_security_service.user_repo.is_owner", AsyncMock(return_value=False)),          patch("app.services.call_security_service.user_repo.is_sudo", AsyncMock(return_value=False)):
        assert await call_security_service.can_toggle_owner_access(user_id, chat_id, client=mock_client_new) is False
        assert await call_security_service.can_manage(user_id, chat_id, MagicMock(), client=mock_client_new) is False

@pytest.mark.asyncio
async def test_call_security_allows_authorized_users(mock_client_new):
    user_id = 99
    chat_id = -100123
    with patch("app.utils.player_permissions.can_manage_call_security", AsyncMock(return_value=True)):
        assert await call_security_panel._can_open_panel(user_id, chat_id, mock_client_new) is True
        assert await call_security_service.can_toggle_owner_access(user_id, chat_id, client=mock_client_new) is True
        assert await call_security_service.can_manage(user_id, chat_id, MagicMock(), client=mock_client_new) is True
