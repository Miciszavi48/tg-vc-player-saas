from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "1234567890")


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _setup_state(**overrides):
    from app.services.manager_command_service import InstallPlayerSetupState

    data = {
        "chat_id": -1001234567890,
        "title": "Test Group",
        "managed": True,
        "settings_exists": True,
        "credit_exists": True,
        "credit_days": 9,
        "credit_status": "active",
        "credit_is_trial": True,
        "language": "fa",
        "audio_enabled": True,
        "video_enabled": True,
        "helper_id": None,
        "helper_state": None,
        "helper_account_status": None,
        "helper_failed": False,
        "music_admin_count": 1,
        "video_admin_count": 0,
        "player_owner_count": 0,
        "player_deputy_count": 0,
        "player_vip_count": 0,
    }
    data.update(overrides)
    return InstallPlayerSetupState(**data)


def _query(
    action: str,
    *,
    chat_id: int = -1001234567890,
    user_id: int = 1234567890,
    from_user_id: int | None = None,
    message_chat_id: int | None = None,
    issued_at: int | None = None,
):
    issued_at = int(time.time()) if issued_at is None else issued_at
    query = AsyncMock()
    query.data = f"Add:Fa:{action}:G{chat_id}:U{user_id}:T{issued_at}"
    query.from_user = SimpleNamespace(id=from_user_id if from_user_id is not None else user_id)
    query.message = AsyncMock()
    query.message.chat = SimpleNamespace(id=message_chat_id if message_chat_id is not None else chat_id)
    query.message.edit_text = AsyncMock()
    query.message.delete = AsyncMock()
    query.answer = AsyncMock()
    return query


def _install_setup_handler():
    from app.handlers import group_panel

    bot = _RecorderBot()
    group_panel.register(bot, None)
    return _handler_by_name(bot.callback_handlers, "install_player_setup_callback")


def test_install_setup_main_keyboard_actions_and_lengths():
    from app.utils.ui import KeyboardFactory

    chat_id = -1001234567890
    user_id = 1234567890
    issued_at = 1700000000
    kb = KeyboardFactory.install_player_setup_panel(
        "fa",
        chat_id,
        user_id,
        issued_at,
        show_charge=True,
    )
    callbacks = _callback_data_set(kb)

    for action in (
        "ConfigAdmin",
        "Lang",
        "ShowSetCharge",
        "AddCli",
        "Access",
        "Exit",
    ):
        assert f"Add:Fa:{action}:G{chat_id}:U{user_id}:T{issued_at}" in callbacks
    assert all(len(cb.encode("utf-8")) <= 64 for cb in callbacks)


def test_install_setup_main_keyboard_hides_charge_for_non_developer():
    from app.utils.ui import KeyboardFactory

    chat_id = -1001234567890
    user_id = 1234567890
    issued_at = 1700000000
    kb = KeyboardFactory.install_player_setup_panel(
        "fa",
        chat_id,
        user_id,
        issued_at,
        show_charge=False,
    )
    callbacks = _callback_data_set(kb)

    assert f"Add:Fa:ShowSetCharge:G{chat_id}:U{user_id}:T{issued_at}" not in callbacks
    assert f"Add:Fa:AddCli:G{chat_id}:U{user_id}:T{issued_at}" in callbacks


def test_install_setup_charge_menu_contains_all_durations():
    from app.utils.ui import KeyboardFactory

    chat_id = -1001234567890
    user_id = 1234567890
    issued_at = 1700000000
    kb = KeyboardFactory.install_player_charge_menu("fa", chat_id, user_id, issued_at)
    callbacks = _callback_data_set(kb)

    for days in (3, 5, 10, 15, 20, 30, 60, 90, 120, 150, 180, 360, 0, 2):
        assert f"Add:Fa:SetCharge{days}:G{chat_id}:U{user_id}:T{issued_at}" in callbacks
    assert f"Add:Fa:Home:G{chat_id}:U{user_id}:T{issued_at}" in callbacks
    assert all(len(cb.encode("utf-8")) <= 64 for cb in callbacks)


def test_install_setup_access_menu_uses_audio_and_video_actions():
    from app.utils.ui import KeyboardFactory

    kb = KeyboardFactory.install_player_access_menu(
        "fa",
        -1001234567890,
        1234567890,
        1700000000,
        audio_enabled=True,
        video_enabled=False,
    )
    callbacks = _callback_data_set(kb)
    assert "Add:Fa:Access:Music:G-1001234567890:U1234567890:T1700000000" in callbacks
    assert "Add:Fa:Access:Video:G-1001234567890:U1234567890:T1700000000" in callbacks


@pytest.mark.asyncio
async def test_show_charge_opens_selector():
    handler = _install_setup_handler()
    query = _query("ShowSetCharge")

    with (
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.manager_command_service.can_manage_setup_credit", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=_setup_state()),
        ),
    ):
        await handler(AsyncMock(), query)

    callbacks = _callback_data_set(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert any(cb.startswith("Add:Fa:SetCharge360:") for cb in callbacks)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "days"),
    [
        ("SetCharge3", 3),
        ("SetCharge5", 5),
        ("SetCharge10", 10),
        ("SetCharge15", 15),
        ("SetCharge20", 20),
        ("SetCharge30", 30),
        ("SetCharge60", 60),
        ("SetCharge90", 90),
        ("SetCharge120", 120),
        ("SetCharge150", 150),
        ("SetCharge180", 180),
        ("SetCharge360", 360),
        ("SetCharge0", 0),
        ("SetCharge2", 2),
    ],
)
async def test_charge_duration_callbacks_map_to_expected_days(action: str, days: int):
    from app.services.manager_command_service import ManagerCommandResult

    handler = _install_setup_handler()
    query = _query(action)
    status = "unlimited" if days == 0 else "active"
    result = ManagerCommandResult(True, "charge_updated", {"amount": days, "status": status})

    with (
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.manager_command_service.can_manage_setup_credit", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.manager_command_service.apply_setup_charge",
            AsyncMock(return_value=result),
        ) as apply_mock,
        patch(
            "app.handlers.group_panel.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=_setup_state()),
        ),
    ):
        await handler(AsyncMock(), query)

    apply_mock.assert_awaited_once_with(-1001234567890, duration_days=days, user_id=1234567890)
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_user_rejected_before_charge_mutation():
    handler = _install_setup_handler()
    query = _query("SetCharge10", from_user_id=99)

    with patch(
        "app.handlers.group_panel.manager_command_service.apply_setup_charge",
        AsyncMock(),
    ) as apply_mock:
        await handler(AsyncMock(), query)

    apply_mock.assert_not_awaited()
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_chat_rejected_before_access_mutation():
    handler = _install_setup_handler()
    query = _query("Access:Music", message_chat_id=-1009999999999)

    with patch(
        "app.handlers.group_panel.manager_command_service.toggle_setup_access",
        AsyncMock(),
    ) as toggle_mock:
        await handler(AsyncMock(), query)

    toggle_mock.assert_not_awaited()
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_permission_denied_rejects_charge_before_mutation():
    handler = _install_setup_handler()
    query = _query("SetCharge10")

    with (
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.manager_command_service.can_manage_setup_credit", AsyncMock(return_value=False)),
        patch(
            "app.handlers.group_panel.manager_command_service.apply_setup_charge",
            AsyncMock(),
        ) as apply_mock,
    ):
        await handler(AsyncMock(), query)

    apply_mock.assert_not_awaited()
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_malformed_action_rejected_before_charge_mutation():
    handler = _install_setup_handler()
    query = _query("SetCharge10")
    query.data = "Add:Fa:SetCharge999:G-1001234567890:U1234567890:T1700000000"

    with patch(
        "app.handlers.group_panel.manager_command_service.apply_setup_charge",
        AsyncMock(),
    ) as apply_mock:
        await handler(AsyncMock(), query)

    apply_mock.assert_not_awaited()
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_callback_rejected():
    handler = _install_setup_handler()
    query = _query("Home", issued_at=1)

    await handler(AsyncMock(), query)

    query.answer.assert_awaited_once()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "access"),
    [("Access:Music", "music"), ("Access:Video", "video")],
)
async def test_access_toggles_call_service(action: str, access: str):
    from app.services.manager_command_service import ManagerCommandResult

    handler = _install_setup_handler()
    query = _query(action)
    result = ManagerCommandResult(True, "access_updated", {"access": access, "enabled": False})

    with (
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.manager_command_service.can_manage_setup_access", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.manager_command_service.toggle_setup_access",
            AsyncMock(return_value=result),
        ) as toggle_mock,
        patch(
            "app.handlers.group_panel.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=_setup_state(audio_enabled=False, video_enabled=False)),
        ),
    ):
        await handler(AsyncMock(), query)

    toggle_mock.assert_awaited_once_with(-1001234567890, access)


@pytest.mark.asyncio
@pytest.mark.parametrize(("action", "lang"), [("SetLangFa", "fa"), ("SetLangEn", "en")])
async def test_language_callbacks_persist_choice(action: str, lang: str):
    from app.services.manager_command_service import ManagerCommandResult

    handler = _install_setup_handler()
    query = _query(action)
    result = ManagerCommandResult(True, "language_updated", {"language": lang})

    with (
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.manager_command_service.can_manage_setup_access", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.manager_command_service.set_setup_language",
            AsyncMock(return_value=result),
        ) as lang_mock,
        patch(
            "app.handlers.group_panel.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=_setup_state(language=lang)),
        ),
    ):
        await handler(AsyncMock(), query)

    lang_mock.assert_awaited_once_with(-1001234567890, lang)


@pytest.mark.asyncio
async def test_config_callback_uses_existing_import_service():
    from app.services.manager_command_service import ManagerCommandResult

    handler = _install_setup_handler()
    query = _query("ConfigAdmin")
    result = ManagerCommandResult(
        True,
        "config_imported",
        {
            "imported": 2,
            "skipped": 1,
            "failures": 0,
            "owners_section": "-",
            "deputies_section": "-",
            "admins_section": "-",
            "vips_section": "-",
        },
    )

    with (
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.manager_command_service.can_manage_setup_config", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.manager_command_service.config_group_admins",
            AsyncMock(return_value=result),
        ) as config_mock,
        patch(
            "app.handlers.group_panel.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=_setup_state(music_admin_count=2)),
        ),
    ):
        await handler(AsyncMock(), query)

    config_mock.assert_awaited_once()
    assert config_mock.await_args.args[1:] == (-1001234567890, 1234567890)


@pytest.mark.asyncio
async def test_helper_callback_uses_existing_binding_service():
    from app.services.manager_command_service import ManagerCommandResult

    handler = _install_setup_handler()
    query = _query("AddCli")
    result = ManagerCommandResult(True, "helper_already_present", {"helper_id": 7})

    with (
        patch("app.handlers.group_panel.group_runtime_state_service.require_active_group", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.manager_command_service.can_manage_setup_helper", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.manager_command_service.add_helper",
            AsyncMock(return_value=result),
        ) as helper_mock,
        patch(
            "app.handlers.group_panel.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=_setup_state(helper_id=7)),
        ),
    ):
        client = AsyncMock()
        await handler(client, query)

    helper_mock.assert_awaited_once_with(-1001234567890, bot_client=client)


@pytest.mark.asyncio
async def test_malformed_setup_callback_answers_visibly():
    from app.handlers import group_panel

    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "install_player_setup_malformed")
    query = AsyncMock()
    query.data = "Add:Fa:Bad"
    query.answer = AsyncMock()

    await handler(AsyncMock(), query)

    query.answer.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("access", "field"),
    [("music", "audio_enabled"), ("video", "video_enabled")],
)
async def test_toggle_setup_access_persists_correct_field(access: str, field: str):
    from app.services import manager_command_service

    cs = SimpleNamespace(audio_enabled=True, video_enabled=True)
    with (
        patch("app.services.manager_command_service.runtime_state.require_active_group", AsyncMock(return_value=True)),
        patch("app.services.manager_command_service.settings_repo.get_chat_settings", AsyncMock(return_value=cs)),
        patch("app.services.manager_command_service.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.services.manager_command_service.invalidate_chat_settings", AsyncMock()) as invalidate_mock,
    ):
        result = await manager_command_service.toggle_setup_access(-1001, access)

    assert result.ok is True
    update_mock.assert_awaited_once_with(-1001, field, False)
    invalidate_mock.assert_awaited_once_with(-1001, "group")


@pytest.mark.asyncio
async def test_set_setup_language_persists_language():
    from app.services import manager_command_service

    with (
        patch("app.services.manager_command_service.runtime_state.require_active_group", AsyncMock(return_value=True)),
        patch("app.services.manager_command_service.settings_repo.get_chat_settings", AsyncMock(return_value=SimpleNamespace(language="fa"))),
        patch("app.services.manager_command_service.settings_repo.update_setting", AsyncMock()) as update_mock,
        patch("app.services.manager_command_service.invalidate_chat_settings", AsyncMock()) as invalidate_mock,
    ):
        result = await manager_command_service.set_setup_language(-1001, "en")

    assert result.ok is True
    update_mock.assert_awaited_once_with(-1001, "language", "en")
    invalidate_mock.assert_awaited_once_with(-1001, "group")


@pytest.mark.asyncio
async def test_trial_charge_does_not_overwrite_active_paid_credit():
    from app.config.settings import settings
    from app.services import manager_command_service

    state = _setup_state(credit_days=30, credit_status="active", credit_is_trial=False)
    with (
        patch("app.services.manager_command_service.runtime_state.require_active_group", AsyncMock(return_value=True)),
        patch(
            "app.services.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=state),
        ),
    ):
        result = await manager_command_service.apply_setup_charge(
            -1001,
            duration_days=2,
            user_id=settings.DEVELOPER_ID,
        )

    assert result.ok is False
    assert result.reason == "trial_active_credit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (_setup_state(credit_days=2, credit_status="active", credit_is_trial=True), "trial_already_used"),
        (_setup_state(credit_days=0, credit_status="unlimited", credit_is_trial=False), "trial_active_credit"),
    ],
)
async def test_trial_charge_rejects_existing_trial_or_unlimited_credit(state, reason: str):
    from app.config.settings import settings
    from app.services import manager_command_service

    with (
        patch("app.services.manager_command_service.runtime_state.require_active_group", AsyncMock(return_value=True)),
        patch(
            "app.services.manager_command_service.build_install_player_setup_state",
            AsyncMock(return_value=state),
        ),
    ):
        result = await manager_command_service.apply_setup_charge(
            -1001,
            duration_days=2,
            user_id=settings.DEVELOPER_ID,
        )

    assert result.ok is False
    assert result.reason == reason


@pytest.mark.asyncio
async def test_unlimited_charge_uses_existing_unlimited_credit_path():
    from app.config.settings import settings
    from app.services import manager_command_service
    from app.services.manager_command_service import ManagerCommandResult

    result = ManagerCommandResult(True, "charge_updated", {"status": "unlimited"})
    with patch(
        "app.services.manager_command_service.charge_group",
        AsyncMock(return_value=result),
    ) as charge_mock:
        actual = await manager_command_service.apply_setup_charge(
            -1001,
            duration_days=0,
            user_id=settings.DEVELOPER_ID,
        )

    assert actual is result
    charge_mock.assert_awaited_once_with(
        -1001,
        mode="unlimited",
        amount=None,
        user_id=settings.DEVELOPER_ID,
    )


@pytest.mark.asyncio
async def test_setup_panel_reflects_real_persisted_state():
    from app.database.engine import async_session
    from app.database.models import (
        ChatSettings,
        Group,
        GroupCredit,
        HelperAccount,
        HelperChatBinding,
        MusicAdmin,
    )
    from app.handlers import group_panel
    from app.utils.i18n import reset_current_lang, set_current_lang

    chat_id = -1001999001
    token = set_current_lang("fa")
    try:
        async with async_session() as session:
            async with session.begin():
                helper = HelperAccount(phone="+1999000001", status="active")
                session.add_all(
                    [
                        Group(chat_id=chat_id, chat_title="Persisted Setup Group", status="active"),
                        ChatSettings(
                            chat_id=chat_id,
                            chat_type="group",
                            language="en",
                            audio_enabled=False,
                            video_enabled=True,
                        ),
                        GroupCredit(
                            chat_id=chat_id,
                            chat_type="group",
                            credit_days=45,
                            status="active",
                            is_trial=False,
                        ),
                        MusicAdmin(chat_id=chat_id, user_id=88001, username="admin"),
                        helper,
                    ]
                )
                await session.flush()
                session.add(HelperChatBinding(chat_id=chat_id, helper_account_id=helper.id))

        text, reply_markup = await group_panel.build_install_player_setup_payload(
            chat_id,
            1234567890,
            1700000000,
        )
    finally:
        reset_current_lang(token)

    assert "Persisted Setup Group" in text
    assert "45 روز" in text
    assert "English" in text
    assert "موزیک ❌ | ویدئو ✅" in text
    assert "متصل" in text
    assert "انجام‌شده" in text
    callbacks = _callback_data_set(reply_markup)
    assert f"Add:Fa:ConfigAdmin:G{chat_id}:U1234567890:T1700000000" in callbacks
