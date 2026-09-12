"""Start text ownership and dynamic /start button layout alignment tests."""
from __future__ import annotations

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
from app.handlers import dev_panel, owner_panel
from app.services.bot_settings_service import build_start_links, get_start_welcome_text
from app.services.texts_links_ui import (
    AskResult,
    build_text_field_payload,
    get_field_spec,
    is_owner_editable_field,
)
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory

_OWNER_ID = 900_401


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


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _kb_callbacks(kb) -> list[str]:
    if kb is None:
        return []
    return [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]


def _kb_urls(kb) -> list[str]:
    if kb is None:
        return []
    return [
        btn.url
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "url", None)
    ]


def _kb_labels(kb) -> list[str]:
    if kb is None:
        return []
    return [btn.text for row in kb.inline_keyboard for btn in row]


def test_start_text_is_developer_editable_metadata():
    spec = get_field_spec("start_text")
    assert spec is not None
    assert spec.kind == "text"
    assert spec.runtime_status == "active_global"
    assert is_owner_editable_field(spec) is True


@pytest.mark.asyncio
async def test_developer_can_edit_start_text():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}start_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="سلام {mention}"))),
        ),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_global,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value="سلام {mention}")),
    ):
        await handler.__wrapped__(client, query)

    set_global.assert_awaited_once_with(
        "start_text",
        "سلام {mention}",
        updated_by=settings.DEVELOPER_ID,
    )
    assert t("fa", "texts_links.saved_text") in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_owner_can_edit_start_text_override():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_set_text")
    query = _pm_query(_OWNER_ID, f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}start_text")
    client = SimpleNamespace(send_message=AsyncMock())
    ask_mock = AsyncMock(return_value=AskResult(message=SimpleNamespace(text="owner value")))

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel._ask", ask_mock),
        patch("app.handlers.owner_panel.owner_text_link_service.set_owner_override", AsyncMock()) as set_owner,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=SimpleNamespace(mode="text", raw="owner value", source="owner")),
        ),
    ):
        await handler.__wrapped__(client, query)

    ask_mock.assert_awaited_once()
    set_owner.assert_awaited_once_with(
        _OWNER_ID,
        "start_text",
        "text",
        "owner value",
        updated_by=_OWNER_ID,
    )
    assert t("fa", "texts_links.owner_saved_text") in client.send_message.await_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "prefix"),
    [
        ("own_text_set_text", CB["OWN_TEXT_SET_TEXT_PREFIX"]),
        ("own_text_clear", CB["OWN_TEXT_CLEAR_PREFIX"]),
    ],
)
async def test_owner_start_text_callbacks_are_active(handler_name: str, prefix: str):
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, handler_name)
    query = _pm_query(_OWNER_ID, f"{prefix}start_text")
    client = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel._ask", AsyncMock(return_value=AskResult(message=SimpleNamespace(text="new start")))) as ask_mock,
        patch("app.handlers.owner_panel.owner_text_link_service.set_owner_override", AsyncMock()) as set_owner,
        patch("app.handlers.owner_panel.owner_text_link_service.clear_owner_override", AsyncMock()) as clear_owner,
        patch(
            "app.handlers.owner_panel.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=SimpleNamespace(mode="text", raw="new start", source="owner")),
        ) as get_owner,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=SimpleNamespace(mode="text", raw="new start", source="owner")),
        ),
    ):
        await handler.__wrapped__(client, query)

    query.answer.assert_awaited()
    if prefix == CB["OWN_TEXT_SET_TEXT_PREFIX"]:
        ask_mock.assert_awaited_once()
        set_owner.assert_awaited_once()
    elif prefix == CB["OWN_TEXT_CLEAR_PREFIX"]:
        clear_owner.assert_not_awaited()
        query.message.edit_text.assert_awaited_once()
        cbs = _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"])
        assert any(cb.startswith(CB["OWN_TEXT_CLEAR_EXEC_PREFIX"]) for cb in cbs)
        assert any(cb.startswith(CB["OWN_TEXT_CLEAR_ABORT_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_owner_start_text_detail_is_owner_editable():
    with patch(
        "app.services.owner_text_link_service.get_effective_text_link",
        AsyncMock(return_value=SimpleNamespace(mode="text", raw="owner start", source="owner")),
    ):
        text, kb = await build_text_field_payload(
            "fa",
            "owner",
            "start_text",
            owner_user_id=_OWNER_ID,
        )

    cbs = _kb_callbacks(kb)
    assert t("fa", "texts_links.runtime_effects.owner_private_start_message") in text
    assert f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}start_text" in cbs
    assert f"{CB['OWN_TEXT_SET_MEDIA_PREFIX']}start_text" in cbs
    assert f"{CB['OWN_TEXT_CLEAR_PREFIX']}start_text" in cbs
    assert f"{CB['OWN_TEXT_PREVIEW_PREFIX']}start_text" not in cbs


@pytest.mark.asyncio
async def test_private_start_uses_single_owner_start_text_override():
    with (
        patch("app.services.bot_settings_service.user_repo.get_all_owners", AsyncMock(return_value=[SimpleNamespace(user_id=_OWNER_ID)])),
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=SimpleNamespace(mode="text", raw="Owner hello {mention}", source="owner")),
        ),
    ):
        text = await get_start_welcome_text("en", mention="@tester")

    assert text == "Owner hello @tester"


def test_start_keyboard_hides_empty_links_and_empty_rows():
    kb = KeyboardFactory.start_menu(
        "fa",
        {
            "creator": "",
            "bot_channel": "https://t.me/bot",
            "support_group": "",
            "guide_channel": "",
            "custom_link": "",
            "sudo_1": "",
            "sudo_2": "",
        },
    )

    assert kb is not None
    assert _kb_urls(kb) == ["https://t.me/bot"]
    assert all(row for row in kb.inline_keyboard)
    assert all(len(row) <= 2 for row in kb.inline_keyboard)


def test_start_keyboard_returns_no_markup_when_no_links_are_configured():
    assert KeyboardFactory.start_menu("fa", {}) is None


def test_start_keyboard_creator_cta_is_full_width_when_pv_link_exists():
    kb = KeyboardFactory.start_menu(
        "fa",
        {
            "creator": "https://t.me/creator",
            "bot_channel": "https://t.me/bot",
            "support_group": "https://t.me/support",
        },
    )

    assert kb.inline_keyboard[0][0].url == "https://t.me/creator"
    assert kb.inline_keyboard[0][0].text == "🛒 خرید از سازنده"
    assert len(kb.inline_keyboard[0]) == 1


@pytest.mark.asyncio
async def test_start_creator_cta_falls_back_to_developer_link():
    values = {
        "developer_pv_link": None,
        "developer_link": "https://t.me/fallback_creator",
        "sudo_link_1": None,
        "sudo_link_2": None,
        "guide_channel_link": None,
        "bot_channel_link": None,
        "support_group_link": None,
        "custom_link": None,
        "sudo_links": None,
    }

    async def _get_setting(key: str, **kwargs):
        return values.get(key)

    with (
        patch("app.services.bot_settings_service.settings.DEVELOPER_LINK", ""),
        patch("app.services.bot_settings_service.settings_repo.get_bot_setting", side_effect=_get_setting),
    ):
        links = await build_start_links(client=None)

    assert links["creator"] == "https://t.me/fallback_creator"


def test_start_keyboard_compacts_secondary_buttons_in_two_column_rows():
    kb = KeyboardFactory.start_menu(
        "fa",
        {
            "creator": "https://t.me/creator",
            "bot_channel": "https://t.me/bot",
            "support_group": "https://t.me/support",
            "guide_channel": "https://t.me/guide",
            "custom_link": "https://example.com/custom",
            "sudo_1": "https://t.me/sudo1",
            "sudo_2": "https://t.me/sudo2",
        },
    )

    rows = [[btn.url for btn in row] for row in kb.inline_keyboard]
    assert rows == [
        ["https://t.me/creator"],
        ["https://t.me/bot", "https://t.me/support"],
        ["https://t.me/guide", "https://example.com/custom"],
        ["https://t.me/sudo1", "https://t.me/sudo2"],
    ]
    assert all(len(row) <= 2 for row in rows[1:])


def test_custom_and_sudo_links_appear_in_start_when_set():
    kb = KeyboardFactory.start_menu(
        "fa",
        {
            "custom_link": "https://example.com/custom",
            "sudo_1": "https://t.me/sudo1",
            "sudo_2": "https://t.me/sudo2",
        },
    )

    labels = _kb_labels(kb)
    urls = _kb_urls(kb)
    assert "🔗 لینک دلخواه" in labels
    assert "🛒 خرید از سودو ۱" in labels
    assert "🛒 خرید از سودو ۲" in labels
    assert "https://example.com/custom" in urls
    assert "https://t.me/sudo1" in urls
    assert "https://t.me/sudo2" in urls


def test_start_menu_callback_data_unchanged_and_not_rendered_as_primary_flow():
    kb = KeyboardFactory.start_menu("fa", {"creator": "https://t.me/creator"})
    assert CB["DEV_TEXT_SET_TEXT_PREFIX"] == "dev:text:txt:"
    assert CB["OWN_TEXT_SET_TEXT_PREFIX"] == "own:text:txt:"
    assert CB["START_PRICING"] == "start:pricing"
    assert CB["START_ABOUT"] == "start:about"
    assert _kb_callbacks(kb) == []
