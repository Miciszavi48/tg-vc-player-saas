"""Text/link field usage alignment and truthful panel UX tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
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
    FIELD_SPECS,
    build_text_field_payload,
    build_texts_hub_payload,
    field_label_key,
    field_runtime_status_key,
    get_field_spec,
    is_owner_editable_field,
    is_storage_only_field,
)
from app.utils.i18n import t
from app.utils.ui import CB

_ALL_FIELDS = {
    "start_text",
    "helper_text",
    "about_text",
    "tariff_text",
    "helper_start_text",
    "developer_link",
    "bot_channel_link",
    "support_group_link",
    "guide_channel_link",
    "developer_pv_link",
    "broadcast_channel_link",
    "custom_link",
    "sudo_link_1",
    "sudo_link_2",
}
_STORAGE_ONLY = {"helper_text", "helper_start_text", "broadcast_channel_link"}
_MEDIA_FIELDS = {"start_text", "helper_text", "about_text", "tariff_text", "helper_start_text"}
_LINK_FIELDS = _ALL_FIELDS - _MEDIA_FIELDS
_PURE_OWNER = 900_401
_MEDIA_JSON = '{"__mode":"media","file_id":"photo_big","caption":"cap"}'


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


def _leaf_keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys |= _leaf_keys(value, full)
        else:
            keys.add(full)
    return keys


def test_all_14_fields_are_present_in_field_specs():
    assert {spec.key for spec in FIELD_SPECS} == _ALL_FIELDS


def test_storage_only_fields_are_clearly_marked_in_metadata_and_labels():
    for field in _STORAGE_ONLY:
        spec = get_field_spec(field)
        assert spec is not None
        assert is_storage_only_field(spec)
        assert "فقط ذخیره" in t("fa", field_runtime_status_key(spec))
        assert t("fa", field_label_key(spec, "dev")).strip()


@pytest.mark.asyncio
async def test_developer_hub_separates_storage_only_fields_from_active_fields():
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        text, _kb = await build_texts_hub_payload("fa", "dev")

    storage_index = text.index(t("fa", "texts_links.group_storage_only"))
    assert text.index(t("fa", "texts_links.group_text")) < storage_index
    assert text.index(t("fa", "texts_links.group_link")) < storage_index
    assert text.index(t("fa", "texts_links.helper_text")) > storage_index
    assert text.index(t("fa", "texts_links.broadcast_channel_link")) > storage_index


@pytest.mark.asyncio
async def test_owner_hub_separates_storage_only_fields_from_active_fields():
    with (
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=TextLinkValue(mode="empty", raw=None, source="empty")),
        ),
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)),
    ):
        text, _kb = await build_texts_hub_payload("fa", "owner", owner_user_id=_PURE_OWNER)

    storage_index = text.index(t("fa", "texts_links.group_storage_only"))
    assert text.index(t("fa", "texts_links.group_text")) < storage_index
    assert text.index(t("fa", "texts_links.group_link")) < storage_index
    assert text.index(t("fa", "texts_links.helper_start_text")) > storage_index
    assert t("fa", "texts_links.source_empty") in text


@pytest.mark.asyncio
async def test_field_detail_shows_runtime_location_text():
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        text, _kb = await build_text_field_payload("fa", "dev", "start_text")

    assert "وضعیت:" in text
    assert "محل نمایش" in text
    assert "<code>/start</code>" in text


@pytest.mark.asyncio
async def test_owner_field_detail_shows_owner_runtime_effect():
    with patch(
        "app.services.owner_text_link_service.get_effective_text_link",
        AsyncMock(return_value=TextLinkValue(mode="empty", raw=None, source="empty")),
    ):
        text, _kb = await build_text_field_payload(
            "fa",
            "owner",
            "developer_pv_link",
            owner_user_id=_PURE_OWNER,
    )

    assert "⚡ اثر" in text
    assert "پشتیبانی (گروه‌های سازنده)" in text
    assert "بدون اثر" in text


@pytest.mark.asyncio
async def test_developer_pv_link_label_and_detail_explain_priority():
    label = t("fa", "texts_links.developer_pv_link")
    assert "اصلی" in label
    assert "سازنده" in label

    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        text, _kb = await build_text_field_payload("fa", "dev", "developer_pv_link")
    assert "اولویت" in text
    assert "لینک برنامه‌نویس" in text


def test_developer_link_owner_label_is_not_plain_developer_label():
    spec = get_field_spec("developer_link")
    assert spec is not None
    owner_label = t("fa", field_label_key(spec, "owner"))
    assert owner_label != "لینک برنامه‌نویس"
    assert "خرید از سازنده" in owner_label


def test_sudo_buy_link_labels_mention_start_or_buy():
    for field in ("sudo_link_1", "sudo_link_2"):
        label = t("fa", f"texts_links.{field}")
        assert "خرید" in label


@pytest.mark.asyncio
async def test_broadcast_channel_link_is_marked_inactive_storage_only():
    spec = get_field_spec("broadcast_channel_link")
    assert spec is not None
    assert is_storage_only_field(spec)
    assert "غیرفعال" in t("fa", field_runtime_status_key(spec))
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        text, _kb = await build_text_field_payload("fa", "dev", "broadcast_channel_link")
    assert "فقط ذخیره" in text
    assert "تأثیری در رابط کاربری ندارد" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", sorted(_MEDIA_FIELDS))
async def test_media_capable_fields_show_caption_or_storage_limitation_note(field: str):
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        text, _kb = await build_text_field_payload("fa", "dev", field)

    assert ("فقط کپشن نمایش داده می‌شود" in text) or ("فایل مدیا فقط در دیتابیس ذخیره می‌شود" in text)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", sorted(_LINK_FIELDS))
async def test_link_only_fields_do_not_show_media_caption_button(field: str):
    with patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=None)):
        _text, kb = await build_text_field_payload("fa", "dev", field)

    cbs = _kb_callbacks(kb)
    assert f"{CB['DEV_TEXT_SET_MEDIA_PREFIX']}{field}" not in cbs


@pytest.mark.asyncio
async def test_existing_dev_save_text_link_and_media_still_work():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    text_handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")
    media_handler = _handler_by_name(bot.callback_handlers, "dev_text_set_media")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.dev_panel._ask", AsyncMock(return_value=AskResult(message=SimpleNamespace(text="hello")))),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_text,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value="hello")),
    ):
        await text_handler.__wrapped__(
            client,
            _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}start_text"),
        )
    set_text.assert_awaited_once_with("start_text", "hello", updated_by=settings.DEVELOPER_ID)

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="https://t.me/example"))),
        ),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_link,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value="https://t.me/example")),
    ):
        await text_handler.__wrapped__(
            client,
            _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}bot_channel_link"),
        )
    set_link.assert_awaited_once_with("bot_channel_link", "https://t.me/example", updated_by=settings.DEVELOPER_ID)

    media_msg = SimpleNamespace(
        text=None,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="photo_big")],
        document=None,
        caption="cap",
    )
    with (
        patch("app.handlers.dev_panel._ask", AsyncMock(return_value=AskResult(message=media_msg))),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_media,
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(return_value=_MEDIA_JSON)),
    ):
        await media_handler.__wrapped__(
            client,
            _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_TEXT_SET_MEDIA_PREFIX']}start_text"),
        )
    set_media.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_owner_save_text_link_and_media_still_work():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    text_handler = _handler_by_name(bot.callback_handlers, "own_text_set_text")
    media_handler = _handler_by_name(bot.callback_handlers, "own_text_set_media")
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel._ask", AsyncMock(return_value=AskResult(message=SimpleNamespace(text="owner text")))),
        patch("app.handlers.owner_panel.owner_text_link_service.set_owner_override", AsyncMock()) as set_text,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=TextLinkValue(mode="text", raw="owner text", source="owner")),
        ),
    ):
        await text_handler.__wrapped__(
            client,
            _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}about_text"),
        )
    set_text.assert_awaited_once_with(_PURE_OWNER, "about_text", "text", "owner text", updated_by=_PURE_OWNER)

    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch(
            "app.handlers.owner_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="https://t.me/owner"))),
        ),
        patch("app.handlers.owner_panel.owner_text_link_service.set_owner_override", AsyncMock()) as set_link,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=TextLinkValue(mode="link", raw="https://t.me/owner", source="owner")),
        ),
    ):
        await text_handler.__wrapped__(
            client,
            _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_TEXT_PREFIX']}developer_link"),
        )
    set_link.assert_awaited_once_with(_PURE_OWNER, "developer_link", "link", "https://t.me/owner", updated_by=_PURE_OWNER)

    media_msg = SimpleNamespace(
        text=None,
        photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id="photo_big")],
        document=None,
        caption="cap",
    )
    with (
        patch("app.handlers.owner_panel.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel._ask", AsyncMock(return_value=AskResult(message=media_msg))),
        patch("app.handlers.owner_panel.owner_text_link_service.set_owner_override", AsyncMock()) as set_media,
        patch(
            "app.services.owner_text_link_service.get_effective_text_link",
            AsyncMock(return_value=TextLinkValue(mode="media", raw=_MEDIA_JSON, source="owner")),
        ),
    ):
        await media_handler.__wrapped__(
            client,
            _pm_query(_PURE_OWNER, f"{CB['OWN_TEXT_SET_MEDIA_PREFIX']}about_text"),
        )
    set_media.assert_awaited_once()


def test_start_text_owner_metadata_is_owner_editable():
    spec = get_field_spec("start_text")
    assert spec is not None
    assert is_owner_editable_field(spec)


def test_text_link_callback_data_unchanged():
    assert CB["DEV_TEXTS_LINKS"] == "dev:texts_links"
    assert CB["DEV_TEXTS_HOME"] == "dev:texts:home"
    assert CB["DEV_TEXT_FIELD_PREFIX"] == "dev:text:f:"
    assert CB["DEV_TEXT_SET_TEXT_PREFIX"] == "dev:text:txt:"
    assert CB["DEV_TEXT_SET_MEDIA_PREFIX"] == "dev:text:med:"
    assert CB["DEV_TEXT_CLEAR_PREFIX"] == "dev:text:clr:"
    assert CB["DEV_TEXT_PREVIEW_PREFIX"] == "dev:text:prv:"
    assert CB["OWN_TEXTS_LINKS"] == "own:texts_links"
    assert CB["OWN_TEXTS_HOME"] == "own:texts:home"
    assert CB["OWN_TEXT_FIELD_PREFIX"] == "own:text:f:"
    assert CB["OWN_TEXT_SET_TEXT_PREFIX"] == "own:text:txt:"
    assert CB["OWN_TEXT_SET_MEDIA_PREFIX"] == "own:text:med:"
    assert CB["OWN_TEXT_CLEAR_PREFIX"] == "own:text:clr:"
    assert CB["OWN_TEXT_PREVIEW_PREFIX"] == "own:text:prv:"


def test_persian_english_key_parity():
    from app.utils.i18n import texts

    fa = texts.load("fa")
    en = texts.load("en")
    assert _leaf_keys(fa) == _leaf_keys(en)
