"""Phase C3-2: owner panel text/link UI writes to owner_text_links only."""
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
from app.handlers import dev_panel, owner_panel
from app.services.owner_text_link_service import TextLinkValue
from app.services.texts_links_ui import (
    AskResult,
    build_text_field_payload,
    build_texts_hub_payload,
    field_label_key,
    get_field_spec,
)
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory

_PURE_OWNER = 900_101
_NON_OWNER = 900_202
_GLOBAL_START = "global-start-value"
_OWNER_START = "owner-start-value"
_MEDIA_JSON = '{"__mode":"media","file_id":"photo_big","caption":"cap"}'


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
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


@pytest.mark.asyncio
async def test_pure_owner_can_open_owner_text_hub():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_texts_home")
    query = _pm_query(_PURE_OWNER, CB["OWN_TEXTS_HOME"])
    client = SimpleNamespace()

    with patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)):
        await handler.__wrapped__(client, query)

    query.message.edit_text.assert_awaited_once()
    body = query.message.edit_text.await_args.args[0]
    assert t("fa", "texts_links.owner_hub_title") in body
    assert t("fa", "texts_links.owner_scope_note") in body


@pytest.mark.asyncio
async def test_non_owner_cannot_open_owner_text_hub():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_texts_home")
    query = _pm_query(_NON_OWNER, CB["OWN_TEXTS_HOME"])
    client = SimpleNamespace()

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.utils.decorators._deny_access", AsyncMock()) as deny_mock,
    ):
        await handler(client, query)

    deny_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dev_text_save_still_writes_bot_settings():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}start_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="hello"))),
        ),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value="hello")),
    ):
        await handler.__wrapped__(client, query)

    set_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_text_save_writes_owner_override_not_bot_settings():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_set_text")
    query = _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}about_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text=_OWNER_START))),
        ),
        patch(
            "app.handlers.owner_panel.owner_text_link_service.set_owner_override",
            AsyncMock(),
        ) as set_override,
        patch("app.handlers.owner_panel.settings_repo.set_bot_setting", AsyncMock()) as set_global,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(
                return_value=TextLinkValue(
                    mode="text",
                    raw=_OWNER_START,
                    source="owner",
                    owner_user_id=_PURE_OWNER,
                ),
            ),
        ),
    ):
        await handler.__wrapped__(client, query)

    set_override.assert_awaited_once()
    set_global.assert_not_awaited()
    assert t("fa", "texts_links.owner_saved_text") in query.message.edit_text.await_args.args[0]
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_link_save_writes_owner_override():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_set_text")
    query = _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}developer_link")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="https://t.me/owner"))),
        ),
        patch(
            "app.handlers.owner_panel.owner_text_link_service.set_owner_override",
            AsyncMock(),
        ) as set_override,
        patch("app.handlers.owner_panel.settings_repo.set_bot_setting", AsyncMock()) as set_global,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(
                return_value=TextLinkValue(
                    mode="link",
                    raw="https://t.me/owner",
                    source="owner",
                    owner_user_id=_PURE_OWNER,
                ),
            ),
        ),
    ):
        await handler.__wrapped__(client, query)

    set_override.assert_awaited_once_with(
        _PURE_OWNER,
        "developer_link",
        "link",
        "https://t.me/owner",
        updated_by=_PURE_OWNER,
    )
    set_global.assert_not_awaited()
    assert t("fa", "texts_links.owner_saved_link") in query.message.edit_text.await_args.args[0]
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_media_save_writes_owner_override():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_set_media")
    query = _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_MEDIA_PREFIX']}about_text")
    media_msg = SimpleNamespace(
        text=None,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="photo_big")],
        document=None,
        caption="cap",
    )
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel._ask", AsyncMock(return_value=AskResult(message=media_msg))),
        patch(
            "app.handlers.owner_panel.owner_text_link_service.set_owner_override",
            AsyncMock(),
        ) as set_override,
        patch("app.handlers.owner_panel.settings_repo.set_bot_setting", AsyncMock()) as set_global,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(
                return_value=TextLinkValue(mode="media", raw=_MEDIA_JSON, source="owner"),
            ),
        ),
    ):
        await handler.__wrapped__(client, query)

    set_override.assert_awaited_once()
    set_global.assert_not_awaited()
    assert t("fa", "texts_links.owner_saved_media") in query.message.edit_text.await_args.args[0]
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_clear_shows_confirm_prompt_before_deleting_override():
    # Tapping Clear must not delete the override immediately -- it requires a
    # confirm step, matching every other destructive owner action.
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_clear")
    query = _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_CLEAR_PREFIX']}about_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel.owner_text_link_service.clear_owner_override",
            AsyncMock(return_value=True),
        ) as clear_override,
    ):
        await handler.__wrapped__(client, query)

    clear_override.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.call_args.kwargs.get("reply_markup") or query.message.edit_text.call_args.args[1]
    cbs = {btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data}
    assert any(cb.startswith(CB["OWN_TEXT_CLEAR_EXEC_PREFIX"]) for cb in cbs)
    assert any(cb.startswith(CB["OWN_TEXT_CLEAR_ABORT_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_owner_clear_confirm_deletes_override_only():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_clear_confirm")
    issued_at = int(__import__("time").time())
    query = _pm_query(
        _PURE_OWNER,
        f"{CB['OWN_TEXT_CLEAR_EXEC_PREFIX']}about_text:{_PURE_OWNER}:{issued_at}",
    )
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel.owner_text_link_service.clear_owner_override",
            AsyncMock(return_value=True),
        ) as clear_override,
        patch("app.handlers.owner_panel.settings_repo.set_bot_setting", AsyncMock()) as set_global,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(
                return_value=TextLinkValue(mode="text", raw=_GLOBAL_START, source="global"),
            ),
        ),
    ):
        await handler.__wrapped__(client, query)

    clear_override.assert_awaited_once_with(_PURE_OWNER, "about_text")
    set_global.assert_not_awaited()
    assert t("fa", "texts_links.cleared_override") in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_owner_clear_confirm_denies_wrong_actor():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_clear_confirm")
    issued_at = int(__import__("time").time())
    other_user = 900_303
    query = _pm_query(
        other_user,
        f"{CB['OWN_TEXT_CLEAR_EXEC_PREFIX']}about_text:{_PURE_OWNER}:{issued_at}",
    )

    with patch(
        "app.handlers.owner_panel.owner_text_link_service.clear_owner_override",
        AsyncMock(),
    ) as clear_override:
        await handler.__wrapped__(SimpleNamespace(), query)

    clear_override.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_owner_clear_abort_does_not_delete_override():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_clear_abort")
    issued_at = int(__import__("time").time())
    query = _pm_query(
        _PURE_OWNER,
        f"{CB['OWN_TEXT_CLEAR_ABORT_PREFIX']}about_text:{_PURE_OWNER}:{issued_at}",
    )

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel.owner_text_link_service.clear_owner_override",
            AsyncMock(),
        ) as clear_override,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(
                return_value=TextLinkValue(mode="text", raw=_GLOBAL_START, source="global"),
            ),
        ),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    clear_override.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_field_preview_shows_owner_source():
    with patch(
        "app.services.owner_text_link_service.get_effective_text_link",
        AsyncMock(
            return_value=TextLinkValue(
                mode="text",
                raw=_OWNER_START,
                source="owner",
                owner_user_id=_PURE_OWNER,
            ),
        ),
    ):
        text, _kb = await build_text_field_payload(
            "fa",
            "owner",
            "about_text",
            owner_user_id=_PURE_OWNER,
        )

    assert t("fa", "texts_links.source_owner") in text
    assert _OWNER_START in text


@pytest.mark.asyncio
async def test_owner_field_preview_shows_global_fallback():
    with patch(
        "app.services.owner_text_link_service.get_effective_text_link",
        AsyncMock(
            return_value=TextLinkValue(mode="text", raw=_GLOBAL_START, source="global"),
        ),
    ):
        text, _kb = await build_text_field_payload(
            "fa",
            "owner",
            "about_text",
            owner_user_id=_PURE_OWNER,
        )

    assert t("fa", "texts_links.using_global_fallback") in text
    assert _GLOBAL_START in text


@pytest.mark.asyncio
async def test_owner_link_field_hides_media_button():
    with patch(
        "app.services.owner_text_link_service.get_effective_text_link",
        AsyncMock(return_value=TextLinkValue(mode="empty", raw=None, source="empty")),
    ):
        _text, kb = await build_text_field_payload(
            "fa",
            "owner",
            "developer_link",
            owner_user_id=_PURE_OWNER,
        )

    cbs = _kb_callbacks(kb)
    assert not any(cb.startswith(CB["OWN_TEXT_SET_MEDIA_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_owner_editable_text_field_keeps_media_button():
    with patch(
        "app.services.owner_text_link_service.get_effective_text_link",
        AsyncMock(return_value=TextLinkValue(mode="empty", raw=None, source="empty")),
    ):
        _text, kb = await build_text_field_payload(
            "fa",
            "owner",
            "about_text",
            owner_user_id=_PURE_OWNER,
        )

    cbs = _kb_callbacks(kb)
    assert f"{CB['OWN_TEXT_SET_MEDIA_PREFIX']}about_text" in cbs


@pytest.mark.asyncio
async def test_invalid_owner_link_rejected_without_writes():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_set_text")
    query = _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}developer_link")
    client = SimpleNamespace(send_message=AsyncMock())

    ask = AsyncMock(
        side_effect=[
            AskResult(message=SimpleNamespace(text="not-a-link")),
            AskResult(message=None, abort_reason="listener_stopped", user_notified=True),
        ]
    )
    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel._ask", ask),
        patch(
            "app.handlers.owner_panel.owner_text_link_service.set_owner_override",
            AsyncMock(),
        ) as set_override,
        patch("app.handlers.owner_panel.settings_repo.set_bot_setting", AsyncMock()) as set_global,
    ):
        await handler.__wrapped__(client, query)

    set_override.assert_not_awaited()
    set_global.assert_not_awaited()
    assert ask.await_count == 2
    assert t("fa", "texts_links.invalid_link") in ask.await_args_list[1].kwargs["prompt_text"]
    client.send_message.assert_not_awaited()


def test_owner_developer_link_label_is_not_developer_wording():
    spec = get_field_spec("developer_link")
    assert spec is not None
    owner_label = t("fa", field_label_key(spec, "owner"))
    global_label = t("fa", field_label_key(spec, "dev"))
    assert owner_label == "لینک سازنده / خرید از سازنده"
    assert global_label == "لینک برنامه‌نویس"
    assert owner_label != global_label
    assert "خرید از سازنده" in owner_label


def test_callback_constants_unchanged():
    assert CB["DEV_TEXT_SET_TEXT_PREFIX"] == "dev:text:txt:"
    assert CB["OWN_TEXT_SET_TEXT_PREFIX"] == "own:text:txt:"
    assert CB["OWN_TEXT_CLEAR_PREFIX"] == "own:text:clr:"


@pytest.mark.asyncio
async def test_legacy_owner_settings_alias_routes_to_owner_moderation():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_cat_settings")
    query = _pm_query(_PURE_OWNER, CB["OWN_CAT_SETTINGS"])
    client = SimpleNamespace()

    await handler.__wrapped__(client, query)

    query.message.edit_text.assert_awaited_once()
    assert t("fa", "panels.owner.root_moderation") in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_owner_text_ask_abort_still_no_silent():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_text_set_text")
    query = _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}about_text")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
        ),
    ):
        await handler.__wrapped__(client, query)

    assert t("fa", "texts_links.ask_cancelled_or_timeout") in client.send_message.await_args.args[1]


def test_owner_root_exposes_texts_category():
    kb = KeyboardFactory.owner_panel("fa")
    all_cb = [btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data]
    assert CB["OWN_START_TEXT"] in all_cb
    assert CB["OWN_CAT_TEXTS"] not in all_cb


@pytest.mark.asyncio
async def test_owner_hub_lists_scoped_sources():
    async def _effective(key: str, *, owner_user_id: int | None = None):
        if key == "about_text":
            return TextLinkValue(mode="text", raw=_OWNER_START, source="owner")
        return TextLinkValue(mode="text", raw=_GLOBAL_START, source="global")

    with (
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(side_effect=_effective),
        ),
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=_GLOBAL_START)),
    ):
        text, _kb = await build_texts_hub_payload("fa", "owner", owner_user_id=_PURE_OWNER)

    assert t("fa", "texts_links.source_owner") in text
    assert t("fa", "texts_links.source_global") in text


def test_c32_string_keys_exist_and_json_parity():
    from app.utils.i18n import texts

    fa = texts.load("fa")
    en = texts.load("en")

    keys = [
        "texts_links.owner_hub_title",
        "texts_links.owner_scope_note",
        "texts_links.source_owner",
        "texts_links.source_global",
        "texts_links.using_global_fallback",
        "texts_links.owner_saved_text",
        "texts_links.cleared_override",
        "texts_links.owner_labels.developer_link",
        "texts_links.group_storage_only",
        "texts_links.runtime_status.active_owner_override",
        "texts_links.runtime_effects.owner_group_support_creator",
        "texts_links.media_runtime.caption_only",
    ]

    def _get(data: dict, dotted: str) -> str:
        node = data
        for part in dotted.split("."):
            node = node[part]
        return node

    for key in keys:
        assert _get(fa, key)
        assert _get(en, key)
