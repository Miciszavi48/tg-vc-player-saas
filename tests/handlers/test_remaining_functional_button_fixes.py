from __future__ import annotations

import os
import sys
import time
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

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import callbacks, group_panel, helper_panel
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
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False

    async def execute(self, stmt):  # noqa: ANN001
        return _FakeScalarResult(self._rows)


def _fake_async_session(rows: list[SimpleNamespace]):
    def _factory():
        return _FakeSession(rows)

    return _factory


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _kb_callbacks(kb) -> list[str]:
    return [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


def _group_query(user_id: int, data: str, chat_id: int = -1001):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="GroupAdmin"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
            text="Now playing",
            edit_text=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Developer"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            text="menu",
            edit_text=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


def _developer_client():
    return SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="testbot")),
        send_message=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_group_clear_all_first_click_only_shows_confirmation():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_clear_all")
    query = _group_query(600002, CB["GRP_CLEAR_ALL"])

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_music_admins", AsyncMock()) as clear_music_mock,
        patch("app.handlers.group_panel.admin_repo.clear_video_admins", AsyncMock()) as clear_video_mock,
    ):
        await handler(SimpleNamespace(), query)

    clear_music_mock.assert_not_awaited()
    clear_video_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    callbacks_in_kb = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["GRP_CLEAR_CONFIRM_PREFIX"]) for cb in callbacks_in_kb)
    assert any(cb.startswith(CB["GRP_CLEAR_CANCEL_PREFIX"]) for cb in callbacks_in_kb)


@pytest.mark.asyncio
async def test_group_clear_confirm_by_same_authorized_user_clears_admin_lists():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_clear_confirm")
    user_id = 600002
    chat_id = -1001
    data = f"{CB['GRP_CLEAR_CONFIRM_PREFIX']}{chat_id}:{user_id}:{int(time.time())}"
    query = _group_query(user_id, data, chat_id)

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_music_admins", AsyncMock()) as clear_music_mock,
        patch("app.handlers.group_panel.admin_repo.clear_video_admins", AsyncMock()) as clear_video_mock,
        patch("app.handlers.group_panel._build_group_management_summary", AsyncMock(return_value="summary")),
    ):
        await handler(SimpleNamespace(), query)

    clear_music_mock.assert_awaited_once_with(chat_id)
    clear_video_mock.assert_awaited_once_with(chat_id)
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_clear_confirm_by_different_user_is_rejected():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_clear_confirm")
    data = f"{CB['GRP_CLEAR_CONFIRM_PREFIX']}-1001:600002:{int(time.time())}"
    query = _group_query(600003, data)

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_music_admins", AsyncMock()) as clear_music_mock,
        patch("app.handlers.group_panel.admin_repo.clear_video_admins", AsyncMock()) as clear_video_mock,
    ):
        await handler(SimpleNamespace(), query)

    clear_music_mock.assert_not_awaited()
    clear_video_mock.assert_not_awaited()
    query.answer.assert_awaited_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_group_clear_cancel_does_not_clear_admin_lists():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_clear_cancel")
    user_id = 600002
    chat_id = -1001
    data = f"{CB['GRP_CLEAR_CANCEL_PREFIX']}{chat_id}:{user_id}:{int(time.time())}"
    query = _group_query(user_id, data, chat_id)

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_admin", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_music_admins", AsyncMock()) as clear_music_mock,
        patch("app.handlers.group_panel.admin_repo.clear_video_admins", AsyncMock()) as clear_video_mock,
        patch("app.handlers.group_panel._build_group_management_summary", AsyncMock(return_value="summary")),
    ):
        await handler(SimpleNamespace(), query)

    clear_music_mock.assert_not_awaited()
    clear_video_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["grp:mgmt:clear_confirm:", "grp:mgmt:clear_confirm:abc", "grp:mgmt:clear_cancel:abc"])
async def test_group_clear_malformed_callbacks_do_not_crash(data: str):
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_clear_malformed")
    query = _group_query(600002, data)

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)


def test_favorites_button_label_no_longer_implies_direct_playback():
    labels = [
        btn.text
        for row in KeyboardFactory.now_playing_controls("en", repeat_on=False).inline_keyboard
        for btn in row
        if btn.callback_data == CB["PB_FAV_PLAY"]
    ]
    assert labels == [t("en", "playback.controls.fav_play")]
    assert "Play" not in labels[0]


@pytest.mark.asyncio
async def test_favorites_list_item_buttons_use_info_callback_not_refresh():
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_fav_list")
    query = _group_query(600002, CB["PB_FAV_LIST"])
    rows = [
        SimpleNamespace(id=1, title="Track A"),
        SimpleNamespace(id=2, title="Track B"),
    ]

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.repositories.favorite_repo.get_favorites_page", AsyncMock(return_value=(rows, 1, 0))),
    ):
        await handler(SimpleNamespace(), query)

    keyboard_callbacks = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['FAV_INFO_PREFIX']}1" in keyboard_callbacks
    assert CB["PB_FAV_LIST"] not in keyboard_callbacks
    assert f"{CB['FAV_RM_PREFIX']}1" in keyboard_callbacks


@pytest.mark.asyncio
async def test_favorites_list_stale_edit_fallback_is_bounded():
    callbacks._CALLBACK_EDIT_FALLBACK_SENT.clear()
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_fav_list")
    query = _group_query(600002, CB["PB_FAV_LIST"])
    query.message.id = 93
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("message deleted"))
    rows = [SimpleNamespace(id=1, title="Track A")]

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.repositories.favorite_repo.get_favorites_page", AsyncMock(return_value=(rows, 1, 0))),
    ):
        await handler(SimpleNamespace(), query)
        await handler(SimpleNamespace(), query)

    query.message.reply.assert_awaited_once()
    assert query.message.reply.await_args.args[0] == t("fa", "favorites.list_header")


@pytest.mark.asyncio
async def test_favorites_list_message_not_modified_is_success_no_fallback():
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_fav_list")
    query = _group_query(600002, CB["PB_FAV_LIST"])
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("MESSAGE_NOT_MODIFIED"))
    rows = [SimpleNamespace(id=1, title="Track A")]

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.repositories.favorite_repo.get_favorites_page", AsyncMock(return_value=(rows, 1, 0))),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    query.message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_favorite_remove_stale_refresh_fallback_is_bounded():
    callbacks._CALLBACK_EDIT_FALLBACK_SENT.clear()
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_fav_rm")
    query = _group_query(600002, f"{CB['FAV_RM_PREFIX']}1")
    query.message.id = 94
    query.message.reply = AsyncMock()
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_caption = AsyncMock(side_effect=RuntimeError("message deleted"))
    query.message.edit_reply_markup = AsyncMock(side_effect=RuntimeError("message deleted"))

    class _DeleteSession(_FakeSession):
        def begin(self):
            return self

    def _delete_async_session():
        return _DeleteSession([])

    with (
        patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)),
        patch("app.database.engine.async_session", _delete_async_session),
        patch("app.repositories.favorite_repo.get_favorites_page", AsyncMock(return_value=([], 1, 0))),
    ):
        await handler(SimpleNamespace(), query)
        await handler(SimpleNamespace(), query)

    query.message.reply.assert_awaited_once()
    assert t("fa", "favorites.no_track") in query.message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_favorite_info_callback_returns_clear_message():
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "pb_fav_info")
    query = _group_query(600002, f"{CB['FAV_INFO_PREFIX']}1")

    with patch("app.handlers.callbacks.authorize_playback_action", AsyncMock(return_value=True)):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "favorites.item_unavailable"), show_alert=True)


def test_post_install_status_rows_do_not_close_panel():
    kb = KeyboardFactory.post_install_panel("en", 7, "admin", "https://t.me/guide")
    callbacks_in_order = _kb_callbacks(kb)
    assert callbacks_in_order[0] == CB["NOOP"]
    assert callbacks_in_order[1] == CB["NOOP"]
    assert CB["NAV_CLOSE"] in callbacks_in_order


@pytest.mark.asyncio
async def test_post_install_decrease_credit_returns_useful_message():
    bot = _RecorderBot()
    callbacks.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "postinst_dec_credit")
    query = _group_query(600002, CB["POST_INSTALL_DEC_CREDIT"])

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "install.decrease_credit_hint"), show_alert=True)


def test_group_support_sudo_label_matches_player_owner_data_source():
    kb = KeyboardFactory.group_support_menu("en")
    labels = {
        btn.callback_data: btn.text
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }
    assert labels[CB["GRP_SUDO"]] == t("en", "panels.group.support.sudo")
    assert "Player Owners" in labels[CB["GRP_SUDO"]]


@pytest.mark.asyncio
async def test_helper_rotate_key_first_click_only_shows_confirmation():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_rotate_key")
    user_id = settings.DEVELOPER_ID
    query = _pm_query(user_id, CB["HLP_ROTATE_KEY"])

    with patch("app.handlers.helper_panel._rotate_helper_session_keys", AsyncMock()) as rotate_mock:
        await handler(_developer_client(), query)

    rotate_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    callbacks_in_kb = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert any(cb.startswith(CB["HLP_ROTATE_CONFIRM_PREFIX"]) for cb in callbacks_in_kb)
    assert any(cb.startswith(CB["HLP_ROTATE_CANCEL_PREFIX"]) for cb in callbacks_in_kb)


@pytest.mark.asyncio
async def test_helper_rotate_confirm_by_same_developer_executes_rotation():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_rotate_key_confirm")
    user_id = settings.DEVELOPER_ID
    data = f"{CB['HLP_ROTATE_CONFIRM_PREFIX']}{user_id}:{int(time.time())}"
    query = _pm_query(user_id, data)
    client = _developer_client()

    with (
        patch("app.handlers.helper_panel._rotate_helper_session_keys", AsyncMock(return_value=(2, 0))) as rotate_mock,
        patch("app.handlers.helper_panel.helper_event_repo.log_event", AsyncMock()) as log_mock,
    ):
        await handler(client, query)

    rotate_mock.assert_awaited_once()
    log_mock.assert_awaited_once()
    client.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_helper_rotate_confirm_with_wrong_payload_user_is_rejected():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_rotate_key_confirm")
    user_id = settings.DEVELOPER_ID
    data = f"{CB['HLP_ROTATE_CONFIRM_PREFIX']}{user_id + 1}:{int(time.time())}"
    query = _pm_query(user_id, data)

    with patch("app.handlers.helper_panel._rotate_helper_session_keys", AsyncMock()) as rotate_mock:
        await handler(_developer_client(), query)

    rotate_mock.assert_not_awaited()
    query.answer.assert_awaited_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_helper_rotate_cancel_does_not_rotate():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_rotate_key_cancel")
    user_id = settings.DEVELOPER_ID
    data = f"{CB['HLP_ROTATE_CANCEL_PREFIX']}{user_id}:{int(time.time())}"
    query = _pm_query(user_id, data)

    with patch("app.handlers.helper_panel._rotate_helper_session_keys", AsyncMock()) as rotate_mock:
        await handler(_developer_client(), query)

    rotate_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ["hlp:rotkey:confirm:", "hlp:rotkey:confirm:abc", "hlp:rotkey:cancel:abc"])
async def test_helper_rotate_malformed_callbacks_do_not_crash(data: str):
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_rotate_key_malformed")
    query = _pm_query(settings.DEVELOPER_ID, data)

    await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(t("fa", "common.errors.invalid_callback"), show_alert=True)


def test_helper_health_text_discloses_quarantine_behavior():
    assert "Quarantine" in t("en", "admin.helpers.health_btn")
    assert "quarantined" in t("en", "admin.helpers.health_done", ok=1, fail=1)
