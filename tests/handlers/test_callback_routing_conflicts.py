"""Callback prefix separation and unknown-callback fallback tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path
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
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import callbacks, help_center, helper_panel
from app.handlers.helper_panel import HelperPanelSummary
from app.handlers.priority import FALLBACK_CALLBACK_GROUP
from app.utils.bot_guards import is_developer
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self._cb_meta: list[dict] = []

    def on_callback_query(self, *args, **kwargs):
        self._cb_meta.append({"args": args, "kwargs": kwargs})

        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):
        def _decorator(fn):
            return fn

        return _decorator


def test_help_and_helper_home_prefixes_differ():
    assert CB["HELP_HOME"] == "h:home"
    assert CB["HLP_HOME"] == "hlp:home"
    assert CB["HELP_HOME"] != CB["HLP_HOME"]


def test_configured_developer_id_matches_guard_with_numeric_types():
    assert settings.DEVELOPER_ID in settings.DEVELOPER_IDS
    assert is_developer(settings.DEVELOPER_ID) is True
    assert is_developer(str(settings.DEVELOPER_ID)) is True


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _query(
    data: str,
    *,
    user_id: int = 1,
    chat_id: int = 100,
    chat_type: str = "private",
    edit_side_effect=None,
):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type)),
            text="menu",
            edit_text=AsyncMock(side_effect=edit_side_effect),
            reply=AsyncMock(),
        ),
    )


def _developer_client():
    return SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
    )


def _developer_client_get_me_fails():
    return SimpleNamespace(
        get_me=AsyncMock(side_effect=RuntimeError("telegram unavailable")),
        send_message=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_unknown_callback_fallback_answers():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "unknown_callback_fallback")
    meta = next(
        m for m in bot._cb_meta if m["kwargs"].get("group") == FALLBACK_CALLBACK_GROUP
    )
    assert meta is not None

    query = _query("totally:unknown:button")
    await handler(MagicMock(), query)
    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.unknown_callback"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_known_prefix_unknown_callback_fallback_answers_when_unrouted():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(
        bot.callback_handlers, "known_prefix_unknown_callback_fallback"
    )
    query = _query("grp:not-real")

    await handler(MagicMock(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.unknown_callback"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_known_prefix_unknown_callback_fallback_skips_handled_routes():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(
        bot.callback_handlers, "known_prefix_unknown_callback_fallback"
    )
    query = _query(CB["HELP_HOME"])
    query._musicbot_callback_route_seen = True

    await handler(MagicMock(), query)

    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_h_home_routes_to_help_center_home():
    bot = _RecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "help_route_home")
    user_id = 1
    query = _query(
        f"{CB['HELP_HOME']}:U{user_id}",
        user_id=user_id,
        chat_id=-100123,
        chat_type="supergroup",
    )

    with patch(
        "app.handlers.help_center.panel_callback_edit",
        AsyncMock(return_value=True),
    ) as edit:
        await handler(MagicMock(), query)

    edit.assert_awaited_once()
    assert edit.await_args.args[2] == t("fa", "help.title")
    cbs = _kb_callbacks(edit.await_args.args[3])
    assert f"{CB['HELP_PROMOTE']}:U{user_id}" in cbs
    assert CB["HLP_HOME"] not in cbs
    query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_help_home_edit_fallback_replies_on_edit_failure():
    bot = _RecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "help_route_home")
    user_id = 1
    query = _query(
        f"{CB['HELP_HOME']}:U{user_id}",
        user_id=user_id,
        chat_id=-100123,
        chat_type="supergroup",
    )
    with patch(
        "app.handlers.help_center.panel_callback_edit",
        AsyncMock(return_value=False),
    ) as edit:
        await handler(MagicMock(), query)
    edit.assert_awaited_once()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_hlp_home_routes_to_helper_panel_home_for_developer():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _query(CB["HLP_HOME"], user_id=settings.DEVELOPER_ID)

    with (
        patch(
            "app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()
        ) as clear_mock,
        patch(
            "app.handlers.helper_panel._get_helper_panel_summary",
            AsyncMock(
                return_value=HelperPanelSummary(
                    total=3, active=2, disabled=1, quarantined=0, available=2
                )
            ),
        ),
    ):
        await handler(_developer_client(), query)

    clear_mock.assert_awaited_once_with(settings.DEVELOPER_ID)
    query.message.edit_text.assert_awaited_once()
    assert t("fa", "admin.helpers.title") in query.message.edit_text.await_args.args[0]
    assert "در دسترس" in query.message.edit_text.await_args.args[0]
    cbs = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert CB["HLP_LIST"] in cbs
    assert CB["HELP_HOME"] not in cbs
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_developer_hlp_home_answers_before_helper_db_reads():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _query(CB["HLP_HOME"], user_id=settings.DEVELOPER_ID)

    async def _clear_after_answer(user_id: int):
        assert user_id == settings.DEVELOPER_ID
        assert query.answer.await_count == 1

    with (
        patch(
            "app.handlers.helper_panel._clear_helper_wizard_states",
            AsyncMock(side_effect=_clear_after_answer),
        ),
        patch(
            "app.handlers.helper_panel._get_helper_panel_summary",
            AsyncMock(
                return_value=HelperPanelSummary(
                    total=3, active=2, disabled=1, quarantined=0, available=2
                )
            ),
        ),
    ):
        await handler(_developer_client(), query)

    query.answer.assert_awaited_once_with()
    query.message.edit_text.assert_awaited_once()


def test_help_center_buttons_do_not_use_hlp_home():
    for role in ("regular", "developer", "owner", "sudo", "player_owner"):
        cbs = _kb_callbacks(KeyboardFactory.help_home("fa", role, is_group=True))
        assert CB["HLP_HOME"] not in cbs
        assert all(not cb.startswith("hlp:") for cb in cbs)
    assert CB["HLP_HOME"] not in _kb_callbacks(KeyboardFactory.help_section_back("fa"))


def test_helper_panel_buttons_do_not_use_h_home():
    keyboards = [
        KeyboardFactory.helper_home("fa", 1, 0, 0),
        KeyboardFactory.helper_add_menu("fa"),
        KeyboardFactory.helper_detail("fa", 7, "active"),
        KeyboardFactory.helper_detail("fa", 7, "disabled"),
        KeyboardFactory.helper_rotate_key_confirm("fa", settings.DEVELOPER_ID, 123),
    ]
    for kb in keyboards:
        cbs = _kb_callbacks(kb)
        assert CB["HELP_HOME"] not in cbs
        assert all(not cb.startswith("h:") for cb in cbs)


@pytest.mark.asyncio
async def test_hlp_home_denied_for_non_developer_is_visible():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_unknown_or_denied")
    query = _query(CB["HLP_HOME"], user_id=settings.DEVELOPER_ID + 99)

    await handler(MagicMock(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.no_access"), show_alert=True
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_hlp_home_wrong_chat_type_is_visible():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _query(
        CB["HLP_HOME"],
        user_id=settings.DEVELOPER_ID,
        chat_id=-100123,
        chat_type="supergroup",
    )

    await handler(_developer_client(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "admin.helpers.private_only"), show_alert=True
    )
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_hlp_home_wrong_chat_type_answers_even_if_get_me_fails():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _query(
        CB["HLP_HOME"],
        user_id=settings.DEVELOPER_ID,
        chat_id=-100123,
        chat_type="supergroup",
    )

    await handler(_developer_client_get_me_fails(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "admin.helpers.private_only"), show_alert=True
    )
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_hlp_list_denied_for_non_developer_is_visible():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_list")
    query = _query(CB["HLP_LIST"], user_id=settings.DEVELOPER_ID + 99)

    await handler(MagicMock(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.no_access"), show_alert=True
    )
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_hlp_health_denied_for_non_developer_is_visible():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_health")
    query = _query(CB["HLP_HEALTH_CHECK"], user_id=settings.DEVELOPER_ID + 99)
    client = _developer_client()

    await handler(client, query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.no_access"), show_alert=True
    )
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_hlp_home_edit_failure_replies_instead_of_silence():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _query(
        CB["HLP_HOME"],
        user_id=settings.DEVELOPER_ID,
        edit_side_effect=RuntimeError("cant edit"),
    )

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch(
            "app.handlers.helper_panel._get_helper_panel_summary",
            AsyncMock(
                return_value=HelperPanelSummary(
                    total=3, active=2, disabled=1, quarantined=0, available=2
                )
            ),
        ),
    ):
        client = _developer_client()
        await handler(client, query)

    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.args[0] == query.message.chat.id
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_unknown_hlp_callback_answers_for_developer():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_unknown_or_denied")
    query = _query("hlp:not-real", user_id=settings.DEVELOPER_ID)

    await handler(MagicMock(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.unknown_callback"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_unknown_h_callback_answers():
    bot = _RecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "help_route_play")
    query = _query("h:play:Ubad", user_id=1)

    await handler(MagicMock(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "help.errors.invalid_callback"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_user_bound_help_promote_routes_through_router():
    bot = _RecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "help_route_promote")
    user_id = 42
    query = _query(
        f"{CB['HELP_PROMOTE']}:U{user_id}",
        user_id=user_id,
        chat_type="supergroup",
    )

    with patch(
        "app.handlers.help_center.panel_callback_edit",
        AsyncMock(return_value=True),
    ) as edit:
        await handler(MagicMock(), query)

    edit.assert_awaited_once()
    assert edit.await_args.args[2] == t("fa", "help.promote_menu")


@pytest.mark.asyncio
async def test_user_bound_help_play_rejects_wrong_user():
    bot = _RecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "help_route_play")
    opener_id = 42
    other_id = 111
    query = _query(
        f"{CB['HELP_PLAYBACK']}:U{opener_id}",
        user_id=other_id,
        chat_type="supergroup",
    )

    with patch(
        "app.handlers.help_center.panel_callback_edit",
        AsyncMock(return_value=True),
    ) as edit:
        await handler(MagicMock(), query)

    edit.assert_not_awaited()
    query.answer.assert_awaited_once_with(
        t("fa", "help.errors.not_for_you"),
        show_alert=True,
    )


def test_generic_unknown_fallback_excludes_known_h_and_hlp_prefixes():
    source = Path("app/handlers/callbacks.py").read_text(encoding="utf-8")
    assert "h:|hlp:" in source
    assert "nav:" in source
    assert "an:" in source
    assert "bc:" in source
    assert "pg:" in source
    assert "postinst:" in source
    assert "noop" in source
    assert "search:" in source
    assert "@bot.on_callback_query(~filters.regex(_KNOWN_CB_PREFIX)" in source.replace("\n", " ") or "~filters.regex(_KNOWN_CB_PREFIX)" in source

    bot = _RecorderBot()
    help_center.register(bot, None)
    helper_panel.register(bot, None)
    registered = {fn.__name__ for fn in bot.callback_handlers}
    assert "help_route_home" in registered
    assert "help_route_play" in registered
    assert "help_route_promote" in registered
    assert "help_route_legacy" in registered
    assert "hlp_home" in registered
    assert "hlp_unknown_or_denied" in registered


def test_major_callback_families_have_registered_handler_or_declared_callback_surface():
    handler_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("app/handlers").glob("*.py")
    )
    ui_source = Path("app/utils/ui.py").read_text(encoding="utf-8")
    surface = handler_sources + ui_source
    families = {
        "h:": ("h:",),
        "hlp:": ("hlp:",),
        "grp:": ("grp:",),
        "dev:": ("dev:",),
        "own:": ("own:",),
        "sudo:": ("sudo:",),
        "pb:": ("pb:",),
        "wz:": ("wz:",),
        "fm:": ("fm:",),
        "txt:": ("txt:",),
        "helper:": ("hlp:",),
        "call_security:": ("cs:", "GRP_CALLSEC"),
        "force:": ("fj:", "fm:"),
        "media:": ("media:",),
        "bot_update:": ("bot_update:",),
    }
    for family, tokens in families.items():
        assert any(token in surface for token in tokens), family


def test_no_legacy_i18n_monoliths_recreated():
    assert not Path("app/resources/strings/fa.json").exists()
    assert not Path("app/resources/strings/en.json").exists()
