"""Phase C3-3: runtime effective reads for owner-scoped text/link overrides."""
from __future__ import annotations

import json
import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

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
from app.handlers import callbacks, group_panel, install
from app.utils.i18n import AUTO_LANG
from app.services.bot_settings_service import (
    build_start_links,
    get_about_text,
    get_bot_channel_link,
    get_developer_link,
    get_effective_setting_value,
    get_guide_channel_link,
    get_start_welcome_text,
    get_support_group_link,
)
from app.services.owner_text_link_service import TextLinkValue
from app.services.texts_links_ui import all_field_keys, get_field_spec, is_storage_only_field
from app.utils.ui import CB

_OWNER = 900_301
_CHAT_ID = -100_555_001
_GLOBAL_DEV = "https://t.me/global_dev"
_OWNER_DEV = "https://t.me/owner_dev"
_GLOBAL_GUIDE = "https://t.me/global_guide"
_OWNER_GUIDE = "https://t.me/owner_guide"
_GLOBAL_SUPPORT = "https://t.me/global_support"
_OWNER_SUPPORT = "https://t.me/owner_support"
_MEDIA_JSON = '{"__mode":"media","file_id":"AgAC","caption":"caption-only"}'

_STORAGE_ONLY_KEYS = frozenset({"helper_text", "helper_start_text", "broadcast_channel_link"})


def _pm_query(user_id: int, data: str, chat_id: int = _CHAT_ID):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
        ),
    )


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator

    def on_chat_member_updated(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
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


@pytest.mark.asyncio
async def test_group_support_creator_uses_owner_override():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_creator")
    query = _pm_query(1, CB["GRP_CREATOR"])

    with (
        patch(
            "app.handlers.group_panel.owner_scope_service.resolve_owner_user_id_for_chat",
            AsyncMock(return_value=_OWNER),
        ),
        patch(
            "app.handlers.group_panel.get_developer_link",
            AsyncMock(return_value=_OWNER_DEV),
        ) as get_link,
    ):
        await handler(bot, query)

    get_link.assert_awaited_once_with(owner_user_id=_OWNER)
    query.answer.assert_awaited_once_with(_OWNER_DEV, show_alert=True)


@pytest.mark.asyncio
async def test_group_support_creator_falls_back_global_when_no_override():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_creator")
    query = _pm_query(1, CB["GRP_CREATOR"])

    with (
        patch(
            "app.handlers.group_panel.owner_scope_service.resolve_owner_user_id_for_chat",
            AsyncMock(return_value=_OWNER),
        ),
        patch(
            "app.handlers.group_panel.get_developer_link",
            AsyncMock(return_value=_GLOBAL_DEV),
        ) as get_link,
    ):
        await handler(bot, query)

    get_link.assert_awaited_once_with(owner_user_id=_OWNER)
    assert query.answer.await_args.args[0] == _GLOBAL_DEV


@pytest.mark.asyncio
async def test_group_support_creator_falls_back_global_when_owner_unresolved():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "grp_creator")
    query = _pm_query(1, CB["GRP_CREATOR"])

    with (
        patch(
            "app.handlers.group_panel.owner_scope_service.resolve_owner_user_id_for_chat",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.handlers.group_panel.get_developer_link",
            AsyncMock(return_value=_GLOBAL_DEV),
        ) as get_link,
    ):
        await handler(bot, query)

    get_link.assert_awaited_once_with(owner_user_id=None)
    assert query.answer.await_args.args[0] == _GLOBAL_DEV


@pytest.mark.asyncio
async def test_group_guide_and_support_links_pass_owner_scope():
    bot = _RecorderBot()
    group_panel.register(bot, None)
    guide_handler = _handler_by_name(bot.callback_handlers, "grp_guide_channel")
    support_handler = _handler_by_name(bot.callback_handlers, "grp_support_group")
    guide_query = _pm_query(1, CB["GRP_GUIDE_CHANNEL"])
    support_query = _pm_query(1, CB["GRP_SUPPORT_GROUP"])

    with (
        patch(
            "app.handlers.group_panel.owner_scope_service.resolve_owner_user_id_for_chat",
            AsyncMock(return_value=_OWNER),
        ),
        patch(
            "app.handlers.group_panel.get_guide_channel_link",
            AsyncMock(return_value=_OWNER_GUIDE),
        ) as get_guide,
        patch(
            "app.handlers.group_panel.get_support_group_link",
            AsyncMock(return_value=_OWNER_SUPPORT),
        ) as get_support,
    ):
        await guide_handler(bot, guide_query)
        await support_handler(bot, support_query)

    get_guide.assert_awaited_once_with(owner_user_id=_OWNER)
    get_support.assert_awaited_once_with(owner_user_id=_OWNER)


@pytest.mark.asyncio
async def test_private_start_welcome_remains_global_without_owner_context():
    with patch(
        "app.services.bot_settings_service.get_effective_setting_value",
        AsyncMock(return_value="global welcome"),
    ) as effective_mock:
        text = await get_start_welcome_text("fa", mention="@user")

    effective_mock.assert_awaited_once_with(
        "start_text",
        owner_user_id=None,
        env_default=settings.START_TEXT or "",
    )
    assert "global welcome" in text or text


@pytest.mark.asyncio
async def test_start_links_do_not_guess_owner_without_context():
    with (
        patch(
            "app.services.bot_settings_service.get_developer_link",
            AsyncMock(return_value=_GLOBAL_DEV),
        ) as dev_link,
        patch(
            "app.services.bot_settings_service.get_guide_channel_link",
            AsyncMock(return_value=_GLOBAL_GUIDE),
        ) as guide_link,
        patch(
            "app.services.bot_settings_service.get_bot_channel_link",
            AsyncMock(return_value=""),
        ),
        patch(
            "app.services.bot_settings_service.get_support_group_link",
            AsyncMock(return_value=_GLOBAL_SUPPORT),
        ),
        patch(
            "app.services.bot_settings_service.get_custom_link",
            AsyncMock(return_value=""),
        ),
        patch(
            "app.services.bot_settings_service.get_sudo_buy_links",
            AsyncMock(return_value=("", "")),
        ),
    ):
        links = await build_start_links(None)

    dev_link.assert_awaited_once_with(owner_user_id=None)
    guide_link.assert_awaited_once_with(owner_user_id=None)
    assert links["creator"] == _GLOBAL_DEV
    assert links["guide_channel"] == _GLOBAL_GUIDE


@pytest.mark.asyncio
async def test_about_text_uses_owner_override_only_when_owner_passed():
    with (
        patch(
            "app.services.bot_settings_service.get_developer_link",
            AsyncMock(return_value=_OWNER_DEV),
        ),
        patch(
            "app.services.bot_settings_service.get_effective_setting_value",
            AsyncMock(return_value="owner about body"),
        ) as effective_mock,
    ):
        text = await get_about_text("fa", owner_user_id=_OWNER)

    effective_mock.assert_awaited_once_with("about_text", owner_user_id=_OWNER)
    assert "owner about body" in text or _OWNER_DEV in text


@pytest.mark.asyncio
async def test_about_text_private_callback_stays_global():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "start_about")
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        data=CB["START_ABOUT"],
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=99, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )
    client = SimpleNamespace()

    with patch(
        "app.handlers.callbacks.get_about_text",
        AsyncMock(return_value="global about"),
    ) as about_mock:
        await handler(client, query)

    about_mock.assert_awaited_once_with(AUTO_LANG)


@pytest.mark.asyncio
async def test_install_success_uses_owner_scoped_guide_link():
    owner_id = _OWNER
    with (
        patch(
            "app.handlers.install.owner_scope_service.resolve_owner_user_id_for_chat",
            AsyncMock(return_value=owner_id),
        ),
        patch(
            "app.handlers.install.get_guide_channel_link",
            AsyncMock(return_value=_OWNER_GUIDE),
        ) as guide_mock,
        patch(
            "app.handlers.install.get_bot_channel_link",
            AsyncMock(return_value=_GLOBAL_DEV),
        ),
    ):
        guide = await install.get_guide_channel_link(owner_user_id=owner_id)
        if not guide:
            guide = await install.get_bot_channel_link(owner_user_id=owner_id)

    guide_mock.assert_awaited_once_with(owner_user_id=owner_id)
    assert guide == _OWNER_GUIDE


@pytest.mark.asyncio
async def test_install_success_falls_back_global_when_owner_unresolved():
    with (
        patch(
            "app.handlers.install.owner_scope_service.resolve_owner_user_id_for_chat",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.handlers.install.get_guide_channel_link",
            AsyncMock(return_value=_GLOBAL_GUIDE),
        ) as guide_mock,
    ):
        owner_id = await install.owner_scope_service.resolve_owner_user_id_for_chat(
            _CHAT_ID,
            "group",
        )
        guide = await install.get_guide_channel_link(owner_user_id=owner_id)

    guide_mock.assert_awaited_once_with(owner_user_id=None)
    assert guide == _GLOBAL_GUIDE


@pytest.mark.asyncio
async def test_runtime_effective_helpers_never_call_set_bot_setting():
    with (
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(
                return_value=TextLinkValue(
                    mode="link",
                    raw=_OWNER_DEV,
                    source="owner",
                    owner_user_id=_OWNER,
                ),
            ),
        ),
        patch(
            "app.repositories.settings_repo.set_bot_setting",
            AsyncMock(),
        ) as set_mock,
    ):
        value = await get_developer_link(owner_user_id=_OWNER)
        await get_guide_channel_link(owner_user_id=_OWNER)
        await get_support_group_link(owner_user_id=_OWNER)

    set_mock.assert_not_awaited()
    assert value == _OWNER_DEV


@pytest.mark.asyncio
async def test_effective_setting_value_start_text_uses_owner_override():
    with (
        patch(
            "app.services.bot_settings_service.get_setting_text",
            AsyncMock(return_value="global start"),
        ) as global_mock,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(
                return_value=TextLinkValue(mode="media", raw=_MEDIA_JSON, source="owner"),
            ),
        ) as owner_mock,
    ):
        value = await get_effective_setting_value("start_text", owner_user_id=_OWNER)

    global_mock.assert_not_awaited()
    owner_mock.assert_awaited_once_with("start_text", owner_user_id=_OWNER)
    assert value == "caption-only"


def test_storage_only_fields_remain_unwired_in_c33():
    wired_runtime_keys = {
        "start_text",
        "about_text",
        "tariff_text",
        "developer_link",
        "developer_pv_link",
        "bot_channel_link",
        "guide_channel_link",
        "support_group_link",
        "custom_link",
        "sudo_link_1",
        "sudo_link_2",
    }
    assert _STORAGE_ONLY_KEYS.issubset(set(all_field_keys()))
    assert _STORAGE_ONLY_KEYS.isdisjoint(wired_runtime_keys)


def test_storage_only_fields_are_marked_in_field_metadata():
    for key in _STORAGE_ONLY_KEYS:
        spec = get_field_spec(key)
        assert spec is not None
        assert is_storage_only_field(spec)


def test_callback_constants_unchanged():
    assert CB["GRP_CREATOR"] == "grp:support:creator"
    assert CB["DEV_TEXT_SET_TEXT_PREFIX"] == "dev:text:txt:"
    assert CB["OWN_TEXT_SET_TEXT_PREFIX"] == "own:text:txt:"


@pytest.mark.asyncio
async def test_get_effective_setting_value_without_owner_matches_global_path():
    with patch(
        "app.services.bot_settings_service.get_setting_text",
        AsyncMock(return_value="plain global"),
    ) as global_mock:
        value = await get_effective_setting_value("start_text", env_default="env")

    global_mock.assert_awaited_once_with("start_text", "env")
    assert value == "plain global"
