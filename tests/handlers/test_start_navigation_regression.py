from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.handlers import callbacks, register_all, start
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []
        self.other_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    self.other_handlers.append(fn)
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
    if kb is None:
        return set()
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _pm_message(user_id: int, first_name: str = "Tester"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name=first_name, username="tester"),
        chat=SimpleNamespace(id=100),
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
    )


def _pm_query(user_id: int, first_name: str = "Tester"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name=first_name, username="tester"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        data=CB["NAV_BACK"],
    )


_LEGACY_DEV_ROOT_CBS = {
    CB["DEV_INCREASE_CREDIT"],
    CB["DEV_DECREASE_CREDIT"],
    CB["DEV_SET_BASE_RATE"],
    CB["DEV_SET_MUSIC_RATE"],
    CB["DEV_SET_VIDEO_RATE"],
    CB["DEV_BROADCAST_GROUP"],
    CB["DEV_FORWARD_GROUP"],
    CB["DEV_BROADCAST_PRIVATE"],
    CB["DEV_FORWARD_PRIVATE"],
    CB["DEV_BROADCAST_CHANNEL"],
    CB["DEV_FORWARD_CHANNEL"],
}

_PUBLIC_LINKS = {
    "creator": "https://t.me/creator",
    "sudo_1": "https://t.me/sudo1",
    "sudo_2": "https://t.me/sudo2",
    "guide_channel": "https://t.me/guide",
    "bot_channel": "https://t.me/channel",
    "support_group": "https://t.me/support",
    "custom_link": "https://t.me/custom",
    "add_to_group": "https://t.me/test_bot?startgroup=true",
    "add_to_channel": "https://t.me/test_bot?startchannel=true",
}


def _kb_urls(kb) -> set[str]:
    if kb is None:
        return set()
    return {
        btn.url
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "url", None)
    }


@pytest.mark.asyncio
async def test_start_single_message_developer_pm():
    bot = _RecorderBot()
    start.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "start_handler")

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )
    msg = _pm_message(settings.DEVELOPER_ID, first_name="Dev")

    with (
        patch("app.handlers.start.check_force_join", AsyncMock(return_value=True)),
        patch("app.handlers.start.track_event", AsyncMock()),
        patch("app.handlers.start.user_repo.upsert_user", AsyncMock()),
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
    ):
        await handler(client, msg)

    assert msg.reply.await_count == 1
    kb = msg.reply.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert cbs == {CB["WZ_HOME"]}
    assert CB["DEV_CAT_CREDIT"] not in cbs
    assert CB["DEV_CAT_BROADCAST"] not in cbs
    assert cbs.isdisjoint(_LEGACY_DEV_ROOT_CBS)
    msg.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_start_single_message_owner_pm():
    bot = _RecorderBot()
    start.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "start_handler")

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )
    msg = _pm_message(900001, first_name="Owner")

    with (
        patch("app.handlers.start.check_force_join", AsyncMock(return_value=True)),
        patch("app.handlers.start.track_event", AsyncMock()),
        patch("app.handlers.start.user_repo.upsert_user", AsyncMock()),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
    ):
        await handler(client, msg)

    assert msg.reply.await_count == 1
    kb = msg.reply.call_args.kwargs["reply_markup"]
    callbacks = _kb_callbacks(kb)
    assert callbacks == {CB["WZ_HOME"]}
    assert CB["OWN_STATS"] not in callbacks
    msg.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_start_single_message_sudo_pm():
    bot = _RecorderBot()
    start.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "start_handler")

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )
    msg = _pm_message(900002, first_name="Sudo")

    with (
        patch("app.handlers.start.check_force_join", AsyncMock(return_value=True)),
        patch("app.handlers.start.track_event", AsyncMock()),
        patch("app.handlers.start.user_repo.upsert_user", AsyncMock()),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
    ):
        await handler(client, msg)

    assert msg.reply.await_count == 1
    kb = msg.reply.call_args.kwargs["reply_markup"]
    callbacks = _kb_callbacks(kb)
    assert callbacks == {CB["WZ_HOME"]}
    assert CB["SUDO_STATUS"] not in callbacks
    msg.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_start_single_message_regular_pm():
    bot = _RecorderBot()
    start.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "start_handler")

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )
    msg = _pm_message(900003, first_name="User")

    with (
        patch("app.handlers.start.check_force_join", AsyncMock(return_value=True)),
        patch("app.handlers.start.track_event", AsyncMock()),
        patch("app.handlers.start.user_repo.upsert_user", AsyncMock()),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.services.start_customization_runtime.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.start_customization_runtime.build_start_links", AsyncMock(return_value=_PUBLIC_LINKS)),
    ):
        await handler(client, msg)

    assert msg.reply.await_count == 1
    kb = msg.reply.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    urls = _kb_urls(kb)
    assert "https://t.me/creator" in urls
    assert "https://t.me/support" in urls
    assert CB["START_PRICING"] not in cbs
    assert CB["START_ABOUT"] not in cbs
    assert CB["DEV_CAT_CREDIT"] not in cbs
    msg.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_back_returns_to_new_dev_root():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_back")

    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
    )
    query = _pm_query(settings.DEVELOPER_ID, first_name="Dev")

    with (
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
    ):
        await handler(client, query)

    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["DEV_CAT_CREDIT"] in cbs
    assert cbs.isdisjoint(_LEGACY_DEV_ROOT_CBS)
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_back_returns_to_owner_root():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_back")

    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
    )
    query = _pm_query(910001, first_name="Owner")

    with (
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
    ):
        await handler(client, query)

    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    assert CB["OWN_STATS"] in _kb_callbacks(kb)
    client.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_back_returns_to_sudo_root():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_back")

    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
    )
    query = _pm_query(910002, first_name="Sudo")

    with (
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
    ):
        await handler(client, query)

    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    assert CB["SUDO_STATUS"] in _kb_callbacks(kb)
    client.send_message.assert_not_called()


def test_dev_root_keyboard_has_no_legacy_flat_actions():
    cbs = _kb_callbacks(KeyboardFactory.developer_panel("fa"))
    assert cbs.isdisjoint(_LEGACY_DEV_ROOT_CBS)


def test_register_all_has_single_start_handler():
    bot = _RecorderBot()
    register_all(bot, None)
    count = sum(1 for fn in bot.message_handlers if fn.__name__ == "start_handler")
    assert count == 1


def _assert_panel_root_has_back_not_close(kb, lang: str) -> None:
    from app.utils.i18n import t

    cbs = _kb_callbacks(kb)
    assert CB["NAV_START"] in cbs
    assert CB["NAV_CLOSE"] not in cbs
    back_btn = next(
        btn
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data == CB["NAV_START"]
    )
    assert back_btn.text == t(lang, "common.buttons.back")


def test_developer_owner_sudo_panel_roots_show_back_not_close():
    for lang in ("fa", "en"):
        _assert_panel_root_has_back_not_close(KeyboardFactory.developer_panel(lang), lang)
        _assert_panel_root_has_back_not_close(KeyboardFactory.owner_panel(lang), lang)
        _assert_panel_root_has_back_not_close(KeyboardFactory.sudo_panel(lang), lang)


def _pm_nav_query(user_id: int, data: str, first_name: str = "Tester"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name=first_name, username="tester"),
        answer=AsyncMock(),
        data=data,
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
            delete=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "role", "panel_marker"),
    [
        (settings.DEVELOPER_ID, "developer", CB["DEV_CAT_CREDIT"]),
        (910001, "owner", CB["OWN_STATS"]),
        (910002, "sudo", CB["SUDO_STATUS"]),
    ],
)
async def test_panel_root_back_returns_start_home_with_management_entry(
    user_id: int,
    role: str,
    panel_marker: str,
):
    from app.utils.i18n import t

    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_start")
    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
        stop_listening=AsyncMock(),
    )
    query = _pm_nav_query(user_id, CB["NAV_START"], first_name=role.title())
    home_kb = KeyboardFactory.with_management_entry("fa", role, None)

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()) as clear_state,
        patch(
            "app.handlers.callbacks.build_private_start_home_payload",
            AsyncMock(return_value=("start home", home_kb)),
        ) as build_home,
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(client, query)

    clear_state.assert_awaited_once()
    build_home.assert_awaited_once()
    query.message.delete.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    client.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()

    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert cbs == {CB["WZ_HOME"]}
    assert panel_marker not in cbs
    assert kb.inline_keyboard[0][0].text == t(
        "fa",
        "start.menu.developer_panel" if role == "developer" else "start.menu.management_panel",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", [settings.DEVELOPER_ID, 910001, 910002])
async def test_stale_private_close_returns_start_home_safely(user_id: int):
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_close")
    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
        stop_listening=AsyncMock(),
    )
    query = _pm_nav_query(user_id, CB["NAV_CLOSE"])
    role = (
        "developer"
        if user_id == settings.DEVELOPER_ID
        else "owner"
        if user_id == 910001
        else "sudo"
    )
    home_kb = KeyboardFactory.with_management_entry("fa", role, None)

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()) as clear_state,
        patch(
            "app.handlers.callbacks.build_private_start_home_payload",
            AsyncMock(return_value=("start home", home_kb)),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(client, query)

    clear_state.assert_awaited_once()
    query.message.delete.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    client.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()
    assert _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"]) == {
        CB["WZ_HOME"]
    }


@pytest.mark.asyncio
async def test_panel_root_back_edit_failure_uses_single_message_fallback():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_start")
    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(
            return_value=SimpleNamespace(id=999, message_id=999),
        ),
        stop_listening=AsyncMock(),
    )
    query = _pm_nav_query(settings.DEVELOPER_ID, CB["NAV_START"], first_name="Dev")
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("cant edit"))
    home_kb = KeyboardFactory.with_management_entry("fa", "developer", None)

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()),
        patch(
            "app.handlers.callbacks.build_private_start_home_payload",
            AsyncMock(return_value=("start home", home_kb)),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler(client, query)

    query.message.edit_text.assert_awaited()
    client.send_message.assert_awaited_once()
    query.answer.assert_awaited_once_with()
    assert _kb_callbacks(client.send_message.call_args.kwargs["reply_markup"]) == {
        CB["WZ_HOME"]
    }


@pytest.mark.asyncio
async def test_build_private_start_home_payload_keeps_single_management_entry():
    from app.services.panel_router import build_private_start_home_payload

    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )
    base_kb = KeyboardFactory.start_menu(
        "fa",
        {
            "creator": "https://t.me/creator",
            "support_group": "https://t.me/support",
        },
    )

    with (
        patch(
            "app.services.panel_router.detect_private_role",
            AsyncMock(return_value="developer"),
        ),
        patch(
            "app.services.start_customization_runtime.build_regular_start_payload",
            AsyncMock(return_value=(None, "welcome", base_kb)),
        ),
    ):
        text, kb = await build_private_start_home_payload(
            client,
            settings.DEVELOPER_ID,
            "Dev",
            lang="fa",
        )

    assert text == "welcome"
    cbs = _kb_callbacks(kb)
    assert CB["WZ_HOME"] in cbs
    assert list(cbs).count(CB["WZ_HOME"]) == 1
    assert CB["DEV_CAT_CREDIT"] not in cbs
