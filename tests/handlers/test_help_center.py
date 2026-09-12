"""Focused tests for reference-complete Help Center callbacks."""
from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

HELP_USER_ID = 123456789
OTHER_USER_ID = 987654321


def _callbacks(markup) -> list[str]:
    return [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    ]


def _button_texts(markup) -> list[str]:
    return [
        btn.text
        for row in markup.inline_keyboard
        for btn in row
    ]


class _FakeMessage:
    def __init__(self, *, chat_type: str = "supergroup") -> None:
        chat_id = -100123 if chat_type in ("group", "supergroup", "channel") else HELP_USER_ID
        self.chat = SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type))
        self.delete = AsyncMock()
        self.reply = AsyncMock()


class _FakeQuery:
    def __init__(
        self,
        data: str,
        user_id: int = HELP_USER_ID,
        *,
        chat_type: str = "supergroup",
    ) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = _FakeMessage(chat_type=chat_type)
        self.answer = AsyncMock()


class _MessageRecorderBot:
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
            def _register(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _fake_help_message(chat_type: str) -> _FakeMessage:
    message = _FakeMessage(chat_type=chat_type)
    message.from_user = SimpleNamespace(id=HELP_USER_ID)
    message.text = "راهنما"
    message.caption = None
    return message


async def _fake_panel_edit(client, query, text, reply_markup=None):  # noqa: ARG001
    await query.answer()
    query.edited_text = text
    query.edited_markup = reply_markup
    return True


def test_help_home_buttons_are_reference_complete_and_user_bound():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.help_home("fa", "regular", is_group=True, user_id=HELP_USER_ID)

    assert _button_texts(kb) == [
        "• ارتقا و عزل",
        "• پخش",
        "• عمومی",
        "• کاربردی",
        "• بستن",
    ]
    assert _callbacks(kb) == [
        f"{CB['HELP_PROMOTE']}:U{HELP_USER_ID}",
        f"{CB['HELP_PLAYBACK']}:U{HELP_USER_ID}",
        f"{CB['HELP_PUBLIC']}:U{HELP_USER_ID}",
        f"{CB['HELP_UTILITY']}:U{HELP_USER_ID}",
        f"{CB['HELP_CLOSE']}:U{HELP_USER_ID}",
    ]


@pytest.mark.asyncio
async def test_help_command_opens_full_panel_in_group():
    from app.handlers import help_center
    from app.utils.i18n import t
    from app.utils.ui import CB

    bot = _MessageRecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "help_command")
    message = _fake_help_message("supergroup")

    await handler(None, message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0] == t("fa", "help.title")
    callbacks = _callbacks(message.reply.await_args.kwargs["reply_markup"])
    assert f"{CB['HELP_PLAYBACK']}:U{HELP_USER_ID}" in callbacks
    assert f"{CB['HELP_PUBLIC']}:U{HELP_USER_ID}" in callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["private", "channel"])
async def test_help_command_outside_group_does_not_open_full_panel(chat_type: str):
    from app.handlers import help_center
    from app.utils.i18n import t

    bot = _MessageRecorderBot()
    help_center.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "help_command")
    message = _fake_help_message(chat_type)

    await handler(None, message)

    message.reply.assert_awaited_once_with(t("fa", "help.group_only"))


def test_play_public_promote_submenus_are_user_bound():
    from app.utils.ui import CB, KeyboardFactory

    play_cbs = _callbacks(KeyboardFactory.help_play_menu("fa", HELP_USER_ID))
    public_cbs = _callbacks(KeyboardFactory.help_public_menu("fa", HELP_USER_ID))
    promote_cbs = _callbacks(KeyboardFactory.help_promote_menu("fa", HELP_USER_ID))

    for expected in (
        CB["HELP_PLAY_REPLY"],
        CB["HELP_PLAY_LINK"],
        CB["HELP_PLAY_AUTO_MUSIC"],
        CB["HELP_PLAY_AUTO_VIDEO"],
        CB["HELP_PLAY_YOUTUBE"],
        CB["HELP_PLAY_RADIO"],
        CB["HELP_PLAY_TV"],
        CB["HELP_PLAY_SATELLITE"],
        CB["HELP_PLAY_CONTROLS"],
    ):
        assert f"{expected}:U{HELP_USER_ID}" in play_cbs
    assert f"{CB['HELP_PLAY_SERIAL']}:U{HELP_USER_ID}" not in play_cbs

    assert f"{CB['HELP_PUBLIC_GROUP']}:U{HELP_USER_ID}" in public_cbs
    assert f"{CB['HELP_PUBLIC_USER']}:U{HELP_USER_ID}" in public_cbs
    assert f"{CB['HELP_PROMOTE_DEPUTY']}:U{HELP_USER_ID}" in promote_cbs
    assert f"{CB['HELP_PROMOTE_ADMIN']}:U{HELP_USER_ID}" in promote_cbs
    assert f"{CB['HELP_PROMOTE_VIP']}:U{HELP_USER_ID}" in promote_cbs
    assert all(cb.endswith(f":U{HELP_USER_ID}") for cb in play_cbs + public_cbs + promote_cbs)


def test_detail_page_back_and_close_are_user_bound():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.help_detail_nav("fa", CB["HELP_PLAYBACK"], HELP_USER_ID)

    assert _callbacks(kb) == [
        f"{CB['HELP_CLOSE']}:U{HELP_USER_ID}",
        f"{CB['HELP_PLAYBACK']}:U{HELP_USER_ID}",
    ]
    assert _button_texts(kb) == ["• بستن", "• برگشت"]


@pytest.mark.asyncio
async def test_correct_user_can_navigate_and_spinner_is_answered():
    from app.handlers.help_center import handle_help_callback
    from app.utils.ui import CB

    query = _FakeQuery(f"{CB['HELP_PLAYBACK']}:U{HELP_USER_ID}", HELP_USER_ID)
    with patch("app.handlers.help_center.panel_callback_edit", side_effect=_fake_panel_edit) as edit:
        await handle_help_callback(None, query)

    assert query.answer.await_count == 1
    edit.assert_awaited_once()
    assert f"{CB['HELP_PLAY_REPLY']}:U{HELP_USER_ID}" in _callbacks(query.edited_markup)


@pytest.mark.asyncio
async def test_help_callback_private_chat_is_denied_without_rendering():
    from app.handlers.help_center import handle_help_callback
    from app.utils.i18n import t
    from app.utils.ui import CB

    query = _FakeQuery(
        f"{CB['HELP_PLAYBACK']}:U{HELP_USER_ID}",
        HELP_USER_ID,
        chat_type="private",
    )
    with patch("app.handlers.help_center.panel_callback_edit", side_effect=_fake_panel_edit) as edit:
        await handle_help_callback(None, query)

    query.answer.assert_awaited_once_with(
        t("fa", "help.errors.group_only_callback"),
        show_alert=True,
    )
    edit.assert_not_awaited()
    query.message.delete.assert_not_awaited()
    assert not hasattr(query, "edited_markup")


@pytest.mark.asyncio
async def test_help_callback_channel_chat_is_denied_without_rendering():
    from app.handlers.help_center import handle_help_callback
    from app.utils.i18n import t
    from app.utils.ui import CB

    query = _FakeQuery(
        f"{CB['HELP_PUBLIC']}:U{HELP_USER_ID}",
        HELP_USER_ID,
        chat_type="channel",
    )
    with patch("app.handlers.help_center.panel_callback_edit", side_effect=_fake_panel_edit) as edit:
        await handle_help_callback(None, query)

    query.answer.assert_awaited_once_with(
        t("fa", "help.errors.group_only_callback"),
        show_alert=True,
    )
    edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_user_press_is_rejected_without_edit_or_close():
    from app.handlers.help_center import handle_help_callback
    from app.utils.ui import CB

    query = _FakeQuery(f"{CB['HELP_PLAYBACK']}:U{HELP_USER_ID}", OTHER_USER_ID)
    with patch("app.handlers.help_center.panel_callback_edit", side_effect=_fake_panel_edit) as edit:
        await handle_help_callback(None, query)

    query.answer.assert_awaited_once_with("این دکمه برای شما نیست.", show_alert=True)
    edit.assert_not_awaited()
    query.message.delete.assert_not_awaited()
    assert not hasattr(query, "edited_markup")


@pytest.mark.asyncio
async def test_malformed_user_bound_callback_is_rejected_safely():
    from app.handlers.help_center import handle_help_callback

    query = _FakeQuery("h:play:Ubad", HELP_USER_ID)
    with patch("app.handlers.help_center.panel_callback_edit", side_effect=_fake_panel_edit) as edit:
        await handle_help_callback(None, query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True
    edit.assert_not_awaited()
    query.message.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_button_is_user_bound_and_safe():
    from app.handlers.help_center import handle_help_callback
    from app.utils.ui import CB

    query = _FakeQuery(f"{CB['HELP_CLOSE']}:U{HELP_USER_ID}", HELP_USER_ID)

    await handle_help_callback(None, query)

    query.answer.assert_awaited_once_with()
    query.message.delete.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_deleted_or_non_editable_help_message_does_not_crash():
    from app.handlers.help_center import handle_help_callback
    from app.utils.ui import CB

    query = _FakeQuery(f"{CB['HELP_PUBLIC']}:U{HELP_USER_ID}", HELP_USER_ID)
    with patch("app.handlers.help_center.panel_callback_edit", AsyncMock(return_value=False)):
        await handle_help_callback(None, query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs["show_alert"] is True


def test_every_reference_help_page_has_bound_parse_coverage():
    from app.handlers.help_center import REFERENCE_HELP_PAGES, parse_help_callback

    assert len(REFERENCE_HELP_PAGES) == 20
    for page in REFERENCE_HELP_PAGES:
        parsed = parse_help_callback(f"{page}:U{HELP_USER_ID}")
        assert parsed is not None
        assert parsed.page == page
        assert parsed.bound_user_id == HELP_USER_ID


@pytest.mark.asyncio
async def test_every_reference_help_page_routes_for_correct_user():
    from app.handlers.help_center import REFERENCE_HELP_PAGES, handle_help_callback

    with patch("app.handlers.help_center.panel_callback_edit", side_effect=_fake_panel_edit):
        for page in sorted(REFERENCE_HELP_PAGES):
            query = _FakeQuery(f"{page}:U{HELP_USER_ID}", HELP_USER_ID)
            await handle_help_callback(None, query)
            query.answer.assert_awaited_once()
            assert hasattr(query, "edited_markup"), page
            assert _callbacks(query.edited_markup), page


def test_every_reference_detail_page_has_text_back_and_close():
    from app.handlers.help_center import _DETAIL_PAGES
    from app.utils.ui import CB, KeyboardFactory
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa_pages = load_fa_i18n()["help"]["pages"]
    en_pages = load_en_i18n()["help"]["pages"]

    for page_cb, (text_key, parent_cb) in _DETAIL_PAGES.items():
        assert text_key in fa_pages
        assert text_key in en_pages
        assert fa_pages[text_key]
        assert en_pages[text_key]

        nav = KeyboardFactory.help_detail_nav("fa", parent_cb, HELP_USER_ID)
        assert _callbacks(nav) == [
            f"{CB['HELP_CLOSE']}:U{HELP_USER_ID}",
            f"{parent_cb}:U{HELP_USER_ID}",
        ]
        assert _button_texts(nav) == ["• بستن", "• برگشت"]
        assert page_cb.startswith("h:")


def test_generated_reference_callbacks_are_parseable_and_have_handler_pages():
    from app.handlers.help_center import REFERENCE_HELP_PAGES, parse_help_callback
    from app.utils.ui import CB, KeyboardFactory

    markups = [
        KeyboardFactory.help_home("fa", "regular", is_group=True, user_id=HELP_USER_ID),
        KeyboardFactory.help_play_menu("fa", HELP_USER_ID),
        KeyboardFactory.help_public_menu("fa", HELP_USER_ID),
        KeyboardFactory.help_promote_menu("fa", HELP_USER_ID),
    ]
    generated_pages = set()
    for cb_data in [cb for markup in markups for cb in _callbacks(markup)]:
        parsed = parse_help_callback(cb_data)
        assert parsed is not None
        assert parsed.bound_user_id == HELP_USER_ID
        generated_pages.add(parsed.page)

    assert CB["HELP_CLOSE"] in generated_pages
    assert CB["HELP_UTILITY"] in generated_pages
    assert REFERENCE_HELP_PAGES - {CB["HELP_HOME"], CB["HELP_PLAY_SERIAL"]} <= generated_pages
    assert CB["HELP_PLAY_SERIAL"] not in generated_pages


def test_old_unbound_h_callbacks_remain_parse_compatible():
    from app.handlers.help_center import parse_help_callback
    from app.utils.ui import CB

    parsed = parse_help_callback(CB["HELP_HOME"])

    assert parsed is not None
    assert parsed.page == CB["HELP_HOME"]
    assert parsed.bound_user_id is None


def test_callback_data_length_is_safe_and_no_legacy_help_format_is_generated():
    from app.utils.ui import CB, KeyboardFactory

    user_id = 99999999999999999999
    markups = [
        KeyboardFactory.help_home("fa", "regular", is_group=True, user_id=user_id),
        KeyboardFactory.help_play_menu("fa", user_id),
        KeyboardFactory.help_public_menu("fa", user_id),
        KeyboardFactory.help_promote_menu("fa", user_id),
        KeyboardFactory.help_detail_nav("fa", CB["HELP_PROMOTE"], user_id),
    ]
    cbs = [cb for markup in markups for cb in _callbacks(markup)]

    assert cbs
    assert all(len(cb.encode("utf-8")) <= 64 for cb in cbs)
    assert all(cb.startswith("h:") for cb in cbs)
    assert all(not cb.startswith("Help:") for cb in cbs)
    assert all(":G" not in cb for cb in cbs)


def test_help_text_contains_reference_persian_and_english_aliases():
    from tests.i18n_test_utils import load_fa_i18n

    help_pages = load_fa_i18n()["help"]["pages"]
    joined = "\n".join(help_pages.values())

    for needle in (
        "پخش خودکار موزیک",
        "Play Auto Music",
        "پخش ماهواره",
        "Satellite Play",
        "پخش سریال",
        "Serial Play",
        "SetVip Player",
        "ClearListVip Player",
        "Speed Down",
        "Link Call",
    ):
        assert needle in joined


def test_unsupported_reference_commands_are_documented_but_hidden_from_visible_buttons():
    from app.utils.ui import CB, KeyboardFactory

    play_cbs = _callbacks(KeyboardFactory.help_play_menu("fa", HELP_USER_ID))
    assert f"{CB['HELP_PLAY_SERIAL']}:U{HELP_USER_ID}" not in play_cbs

    missing_doc = Path("docs/HELP_REFERENCE_MISSING_COMMANDS.md").read_text(encoding="utf-8")

    for needle in (
        "Serial Play",
    ):
        assert needle in missing_doc


def test_different_name_reference_commands_are_documented_in_mappings():
    mappings_doc = Path("docs/HELP_REFERENCE_COMMAND_MAPPINGS.md").read_text(encoding="utf-8")

    for needle in (
        "Play Auto Music",
        "پخش خودکار موزیک",
        "Play Auto Video",
        "پخش خودکار ویدئو",
        "Search Youtube",
        "سرچ یوتیوب",
        "/search",
        "Tv Play",
        "پخش تلویزیون",
        "Radio Play",
        "پخش رادیو",
        "Satellite Play",
        "پخش ماهواره",
        "Serial Play",
        "پخش سریال",
        "Speed Down",
        "Speed Up",
        "Volume-",
        "Volume+",
        "Front",
        "Back",
        "Group id",
        "Channel id",
        "Status Player",
        "Show Id Status Photo",
        "Show Id Status Simple",
        "Id",
        "ListVip Player",
        "ClearListVip Player",
        "Start Call",
        "End Call",
        "Link Call",
        "SetDeputy Player",
        "SetVip Player",
        "synced",
        "help alias added",
    ):
        assert needle in mappings_doc


def test_synced_help_aliases_are_not_marked_fully_missing():
    missing_doc = Path("docs/HELP_REFERENCE_MISSING_COMMANDS.md").read_text(encoding="utf-8")

    for needle in (
        "Play Auto Music",
        "پخش خودکار موزیک",
        "Play Auto Video",
        "پخش خودکار ویدئو",
        "Search Youtube",
        "Radio Play",
        "پخش رادیو",
        "Satellite Play",
        "پخش ماهواره",
        "Tv Play",
        "Stop Play",
        "Pause Play",
        "Speed Up",
        "Speed Down",
        "افزایش سرعت",
        "کاهش سرعت",
        "Volume+",
        "Volume-",
        "SetDeputy Player",
        "SetVip Player",
        "Channel id",
        "شناسه کانال",
    ):
        assert needle not in missing_doc


ROUTED_HELP_COMMANDS = {
    "پخش",
    "Play",
    "پخش خودکار موزیک",
    "Play Auto Music",
    "پخش خودکار ویدئو",
    "Play Auto Video",
    "توقف پخش",
    "Stop Play",
    "مکث پلیر",
    "Pause Play",
    "ازسرگیری",
    "Resume Play",
    "پخش بیصدا",
    "Mute Play",
    "پخش باصدا",
    "UnMute Play",
    "کاهش سرعت",
    "Speed Down",
    "افزایش سرعت",
    "Speed Up",
    "کاهش صدا",
    "Volume-",
    "افزایش صدا",
    "Volume+",
    "جلو",
    "Front",
    "عقب",
    "Back",
    "تنظیم صدا",
    "Set Volume",
    "شروع کال",
    "Start Call",
    "پایان کال",
    "End Call",
    "پایان کال 25",
    "End Call 25",
    "لینک کال",
    "Link Call",
    "پخش رادیو",
    "Radio Play",
    "پخش ماهواره",
    "Satellite Play",
    "پخش تیوی",
    "پخش تلویزیون",
    "Tv Play",
    "شناسه گروه",
    "Group id",
    "شناسه کانال",
    "Channel id",
    "وضعیت پلیر",
    "Status Player",
    "وضعیت نمایش شناسه عکس",
    "وضعیت نمایش شناسه ساده",
    "Show Id Status Photo",
    "Show Id Status Simple",
    "آیدی",
    "Id",
    "اعتبار پلیر",
    "Expire Player",
    "ارتقا ویژه پلیر",
    "عزل ویژه پلیر",
    "SetVip Player",
    "RemVip Player",
    "لیست ویژه پلیر",
    "پاکسازی لیست ویژه پلیر",
    "ListVip Player",
    "ClearListVip Player",
    "ارتقا مقام پلیر",
    "عزل مقام پلیر",
    "Promote Player",
    "Demote Player",
    "لیست مدیران پلیر",
    "پاکسازی لیست مدیران پلیر",
    "ListAdmin Player",
    "ClearListAdmin Player",
    "پیکربندی پلیر",
    "Config Player",
    "ارتقا معاون پلیر",
    "عزل معاون پلیر",
    "SetDeputy Player",
    "RemDeputy Player",
    "لیست معاونان پلیر",
    "پاکسازی لیست معاونان پلیر",
    "ListDeputy Player",
    "ClearListDeputy Player",
}

PLACEHOLDER_HELP_COMMANDS = {
    "پخش سریال",
    "Serial Play",
}

LATER_PHASE_HELP_COMMANDS: set[str] = set()

CUSTOMER_HELP_SECTIONS: dict[str, str] = {
    "promote_deputy": "SetDeputy Player",
    "promote_admin": "Config Player",
    "promote_vip": "ClearListVip Player",
    "public_group": "Show Id Status Simple",
    "public_user": "Id",
    "play_link": "Play",
    "play_reply": "Play",
    "play_auto_video": "Play Auto Video",
    "play_auto_music": "Play Auto Music",
    "play_radio": "Radio Play",
    "play_youtube": "Search Youtube",
    "play_tv": "Tv Play",
    "play_serial": "Serial Play",
    "play_controls": "Link Call",
    "play_satellite": "Satellite Play",
    "utility": "امنیت ویس کال",
}


def test_customer_help_sections_present_in_fa_i18n():
    from tests.i18n_test_utils import load_fa_i18n

    pages = load_fa_i18n()["help"]["pages"]
    for page_key, needle in CUSTOMER_HELP_SECTIONS.items():
        assert page_key in pages, page_key
        assert needle in pages[page_key], page_key


def test_customer_help_serial_page_documents_coming_soon():
    from tests.i18n_test_utils import load_fa_i18n

    serial_text = load_fa_i18n()["help"]["pages"]["play_serial"]
    assert "به‌زودی" in serial_text
    assert "Serial Play" in serial_text


def _help_md_reference_commands() -> set[str]:
    commands = set()
    for line in Path("help.md").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("✧ "):
            commands.add(stripped[2:].strip())
    # These commands are represented in the Help UI/menu and customer i18n even
    # though this workspace help.md dump is missing those exact command lines.
    commands.update({
        "پخش خودکار ویدئو",
        "Play Auto Video",
        "شناسه کانال",
        "Channel id",
    })
    return commands


def test_every_help_md_command_is_classified_once():
    reference_commands = _help_md_reference_commands()
    reference_commands.update(LATER_PHASE_HELP_COMMANDS)

    assert ROUTED_HELP_COMMANDS.isdisjoint(LATER_PHASE_HELP_COMMANDS)
    assert ROUTED_HELP_COMMANDS.isdisjoint(PLACEHOLDER_HELP_COMMANDS)
    assert PLACEHOLDER_HELP_COMMANDS.isdisjoint(LATER_PHASE_HELP_COMMANDS)
    assert ROUTED_HELP_COMMANDS | PLACEHOLDER_HELP_COMMANDS | LATER_PHASE_HELP_COMMANDS == reference_commands


def test_reference_command_docs_do_not_contradict_classification():
    coverage_doc = Path("docs/HELP_REFERENCE_COMMAND_COVERAGE.md").read_text(encoding="utf-8")
    mappings_doc = Path("docs/HELP_REFERENCE_COMMAND_MAPPINGS.md").read_text(encoding="utf-8")
    missing_doc = Path("docs/HELP_REFERENCE_MISSING_COMMANDS.md").read_text(encoding="utf-8")
    routed_docs = coverage_doc + "\n" + mappings_doc

    for command in ROUTED_HELP_COMMANDS:
        assert f"`{command}`" in routed_docs, command
        assert f"`{command}`" not in missing_doc, command

    for command in PLACEHOLDER_HELP_COMMANDS:
        assert f"`{command}`" in routed_docs, command
        assert f"`{command}`" in missing_doc, command

    for command in LATER_PHASE_HELP_COMMANDS:
        assert f"`{command}`" in missing_doc, command


def test_help_i18n_parity():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    assert set(_flatten(load_fa_i18n()["help"])) == set(_flatten(load_en_i18n()["help"]))
    assert set(load_fa_i18n()["help_content"]) == set(load_en_i18n()["help_content"])


# Telegram HTML tags accepted by Bot API / kurigram DEFAULT parse mode.
_TELEGRAM_HTML_TAGS = (
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "span",
    "tg-spoiler",
    "tg-emoji",
    "a",
    "code",
    "pre",
    "blockquote",
)


def _iter_help_text_leaves(tree: dict, prefix: str = "help") -> list[tuple[str, str]]:
    """Yield (dotted_key, text) for all string leaves under help / help_content."""
    leaves: list[tuple[str, str]] = []

    def walk(obj, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(obj, str):
            leaves.append((path, obj))

    walk(tree.get("help", {}), "help")
    walk(tree.get("help_content", {}), "help_content")
    return leaves


def _assert_balanced_telegram_html(key: str, text: str) -> None:
    for tag in _TELEGRAM_HTML_TAGS:
        opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", text, flags=re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}>", text, flags=re.IGNORECASE))
        assert opens == closes, f"{key}: unbalanced <{tag}> open={opens} close={closes}"


def _strip_html_for_visible(text: str) -> str:
    """Approximate visible text: drop tags, keep body content and real newlines."""
    without_tags = re.sub(r"</?[a-zA-Z0-9-]+(?:\s[^>]*)?>", "", text)
    return without_tags


def test_help_texts_have_real_newlines_not_literal_backslash_n():
    """JSON must load real newlines; double-escaped \\n must not reach users."""
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    for lang, tree in (("fa", load_fa_i18n()), ("en", load_en_i18n())):
        pages = tree["help"]["pages"]
        assert pages, lang
        for page_key, text in pages.items():
            key = f"{lang}.help.pages.{page_key}"
            assert "\\n" not in text, f"{key}: literal backslash-n present"
            # Reference detail pages are multi-paragraph and must use real breaks.
            if page_key != "utility" or "\n" in text:
                if page_key.startswith("play_") or page_key.startswith("public_") or page_key.startswith("promote_"):
                    assert "\n" in text, f"{key}: expected real newlines"


def test_help_and_help_content_html_tags_are_balanced_and_supported():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    allowed = set(_TELEGRAM_HTML_TAGS)
    for lang, tree in (("fa", load_fa_i18n()), ("en", load_en_i18n())):
        for key, text in _iter_help_text_leaves(tree):
            full = f"{lang}.{key}"
            _assert_balanced_telegram_html(full, text)
            found = {t.lower() for t in re.findall(r"</?([a-zA-Z0-9-]+)(?:\s[^>]*)?>", text)}
            unsupported = found - allowed
            assert not unsupported, f"{full}: unsupported tags {unsupported}"


@pytest.mark.asyncio
async def test_help_texts_parse_under_default_parse_mode_and_preserve_visible_content():
    """Prove help strings are format-safe and render under DEFAULT (HTML+Markdown)."""
    import asyncio

    from pyrogram import enums
    from pyrogram.parser.parser import Parser

    from app.utils.i18n import TextService
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    class _Client:
        parse_mode = enums.ParseMode.DEFAULT

    parser = Parser(_Client())
    service = TextService()

    for lang, tree in (("fa", load_fa_i18n()), ("en", load_en_i18n())):
        for key, raw in _iter_help_text_leaves(tree):
            # t() must resolve and survive .format() with no kwargs (no stray braces).
            resolved = service.t(lang, key)
            assert not resolved.startswith("[missing:"), key
            assert not resolved.startswith("[invalid:"), key
            assert resolved == raw, f"{lang}.{key}: t() diverged from resource leaf"

            parsed = await parser.parse(resolved)
            message = parsed["message"] or ""
            assert "\\n" not in message, f"{lang}.{key}: literal \\n after parse"

            # Visible content (tags removed) must match resource body after parse.
            expected_visible = _strip_html_for_visible(raw)
            # Parser may normalize some entities; compare without tags on both sides.
            actual_visible = message
            # Collapse only accidental pure whitespace differences at ends.
            assert actual_visible.strip() == expected_visible.strip(), (
                f"{lang}.{key}: visible content changed by parse mode"
            )


def test_help_page_resource_files_do_not_double_escape_newlines():
    """Raw JSON fragments must encode newlines as \\n, not \\\\n."""
    for rel in (
        "app/resources/i18n/fa/system/help.json",
        "app/resources/i18n/en/system/help.json",
    ):
        raw = Path(rel).read_text(encoding="utf-8")
        assert r"\\n" not in raw, f"{rel}: double-escaped newline sequences in file"


def test_no_hardcoded_legacy_or_user_ids_in_help_sources():
    ui_source = Path("app/utils/ui.py").read_text(encoding="utf-8")
    help_ui_source = ui_source[ui_source.index("# ── Help Center keyboards") :]
    sources = [
        Path("app/handlers/help_center.py").read_text(encoding="utf-8"),
        help_ui_source,
        Path("app/resources/i18n/fa/system/help.json").read_text(encoding="utf-8"),
        Path("app/resources/i18n/en/system/help.json").read_text(encoding="utf-8"),
    ]
    joined = "\n".join(sources)

    for legacy in ("Help:Home", "Help:Play", "Help:Close", "Help:Public", "Help:Promote"):
        assert legacy not in joined
    assert ":G" not in joined
    assert "6909288370" not in joined
    assert "U690" not in joined


def test_no_runtime_command_handlers_were_added_to_help_center():
    src = Path("app/handlers/help_center.py").read_text(encoding="utf-8")

    assert src.count("@bot.on_message") == 1
    assert "help_command_filter()" in src
    assert "HELP_ROUTE_SPECS" in src
    assert "_register_help_callback_routes" in src
    assert "HELP_CALLBACK_GROUP" in src
    assert "_help_center_registered" in src
    assert "help_callback_filter" not in src


def test_help_callback_pattern_matches_user_bound_callbacks():
    from app.handlers.help_center import (
        HELP_CALLBACK_PATTERN,
        _HELP_CALLBACK_PATTERN_RE,
        parse_help_callback,
    )
    from app.utils.ui import CB

    bound_data = f"{CB['HELP_PROMOTE']}:U{HELP_USER_ID}"
    assert parse_help_callback(bound_data) is not None
    assert _HELP_CALLBACK_PATTERN_RE.fullmatch(bound_data)
    assert re.compile(HELP_CALLBACK_PATTERN).fullmatch(bound_data)

    assert not _HELP_CALLBACK_PATTERN_RE.fullmatch("h:play:Ubad")
    assert not _HELP_CALLBACK_PATTERN_RE.fullmatch("totally:unknown")


def test_every_reference_help_page_matches_help_callback_pattern():
    import re

    from app.handlers.help_center import HELP_CALLBACK_PATTERN, REFERENCE_HELP_PAGES

    pattern = re.compile(HELP_CALLBACK_PATTERN)
    for page in REFERENCE_HELP_PAGES:
        data = f"{page}:U{HELP_USER_ID}"
        assert pattern.fullmatch(data), page


def test_help_center_register_is_idempotent():
    import asyncio

    from pyrogram import Client

    from app.handlers import help_center, register_all
    from app.handlers.help_center import _find_help_callback_handler

    async def _run() -> None:
        bot = Client(
            "test_help_idempotent",
            api_id=1,
            api_hash="x",
            bot_token="1:xx",
            in_memory=True,
        )
        register_all(bot, None)
        await asyncio.sleep(0.1)
        first, _ = _find_help_callback_handler(bot)
        assert first is not None
        register_all(bot, None)
        await asyncio.sleep(0.1)
        second, _ = _find_help_callback_handler(bot)
        assert second is first

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_register_all_routes_user_bound_help_callbacks_in_supergroup():
    import sys
    from types import ModuleType
    from unittest.mock import MagicMock

    if "pyromod" not in sys.modules:
        pyromod_module = ModuleType("pyromod")
        pyromod_exceptions = ModuleType("pyromod.exceptions")
        pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
        pyromod_module.exceptions = pyromod_exceptions
        sys.modules["pyromod"] = pyromod_module
        sys.modules["pyromod.exceptions"] = pyromod_exceptions

    from app.handlers import register_all
    from app.utils.ui import CB

    bot = _MessageRecorderBot()
    register_all(bot, None)
    router = next(
        fn for fn in bot.callback_handlers if fn.__name__ == "help_route_play"
    )

    query = _FakeQuery(f"{CB['HELP_PLAYBACK']}:U{HELP_USER_ID}", HELP_USER_ID)
    query.message.chat = SimpleNamespace(
        id=-1003740677405,
        type=SimpleNamespace(value="supergroup"),
    )

    with patch(
        "app.handlers.help_center.panel_callback_edit",
        side_effect=_fake_panel_edit,
    ) as edit, patch(
        "app.services.panel_message_service.get_redis",
        AsyncMock(),
    ):
        await router(MagicMock(), query)

    edit.assert_awaited_once()
    assert query.answer.await_count >= 1
    assert f"{CB['HELP_PLAY_REPLY']}:U{HELP_USER_ID}" in _callbacks(query.edited_markup)


@pytest.mark.asyncio
async def test_register_all_help_close_deletes_message_for_bound_user():
    import sys
    from types import ModuleType
    from unittest.mock import MagicMock

    if "pyromod" not in sys.modules:
        pyromod_module = ModuleType("pyromod")
        pyromod_exceptions = ModuleType("pyromod.exceptions")
        pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
        pyromod_module.exceptions = pyromod_exceptions
        sys.modules["pyromod"] = pyromod_module
        sys.modules["pyromod.exceptions"] = pyromod_exceptions

    from app.handlers import register_all
    from app.utils.ui import CB

    class _RecorderBot:
        def __init__(self) -> None:
            self.callback_handlers: list = []

        def on_callback_query(self, *args, **kwargs):
            def _decorator(fn):
                self.callback_handlers.append(fn)
                return fn

            return _decorator

        def on_message(self, *args, **kwargs):
            def _decorator(fn):
                return fn

            return _decorator

        def __getattr__(self, name: str):
            if name.startswith("on_"):

                def _register(*args, **kwargs):
                    def _decorator(fn):
                        return fn

                    return _decorator

                return _register
            raise AttributeError(name)

    bot = _RecorderBot()
    register_all(bot, None)
    router = next(
        fn for fn in bot.callback_handlers if fn.__name__ == "help_route_close"
    )
    query = _FakeQuery(f"{CB['HELP_CLOSE']}:U{HELP_USER_ID}", HELP_USER_ID)
    query.message.chat = SimpleNamespace(
        id=-1003740677405,
        type=SimpleNamespace(value="supergroup"),
    )

    await router(MagicMock(), query)

    query.answer.assert_awaited_once_with()
    query.message.delete.assert_awaited_once_with()


def _dispatch_query(data: str, user_id: int):
    """CallbackQuery stand-in for kurigram ``Handler.check`` tests (pyromod-safe)."""
    from pyrogram.types import CallbackQuery

    query = MagicMock(spec=CallbackQuery)
    query.id = "test-query"
    query.data = data
    query.from_user = SimpleNamespace(id=user_id, username="testuser")
    query.message = SimpleNamespace(
        id=1,
        chat=SimpleNamespace(id=-100, username=None),
    )
    query.chat_instance = ""
    query.inline_message_id = None
    return query


def _find_help_callback_handler(bot):
    """Return the registered ``help_callback_router`` CallbackQueryHandler."""
    from app.handlers.help_center import _find_help_callback_handler as _find

    return _find(bot)


@pytest.mark.asyncio
async def test_help_callback_handler_check_matches_user_bound_callbacks():
    """Prove kurigram ``Handler.check`` selects Help for production ``h:*:U{id}`` data."""
    import asyncio

    from pyrogram import Client

    from app.handlers import help_center, register_all
    from app.utils.ui import CB

    bot = Client(
        "test_help_dispatch",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.1)

    from app.handlers.priority import HELP_CALLBACK_GROUP

    handler, group = _find_help_callback_handler(bot)
    assert handler is not None, "help route handler missing from dispatcher"
    assert group == HELP_CALLBACK_GROUP

    for cb_key in ("HELP_PROMOTE", "HELP_PLAYBACK", "HELP_PUBLIC", "HELP_CLOSE"):
        data = f"{CB[cb_key]}:U{HELP_USER_ID}"
        query = _dispatch_query(data, HELP_USER_ID)
        matched = False
        for _group, _name, route_handler in help_center._find_help_callback_handlers(bot):
            if await route_handler.check(bot, query):
                matched = True
                break
        assert matched is True, data

    unknown_query = _dispatch_query("totally:unknown", HELP_USER_ID)
    assert await handler.check(bot, unknown_query) is False


@pytest.mark.asyncio
async def test_help_callback_handler_check_rejects_malformed_user_suffix():
    """Strict regex rejects ``h:play:Ubad`` before handler body runs."""
    import asyncio

    from pyrogram import Client

    from app.handlers import register_all
    from app.handlers.help_center import parse_help_callback

    bot = Client(
        "test_help_malformed",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.1)

    handler, _group = _find_help_callback_handler(bot)
    assert handler is not None

    query = _dispatch_query("h:play:Ubad", HELP_USER_ID)
    assert await handler.check(bot, query) is False
    assert parse_help_callback(query.data) is None


@pytest.mark.asyncio
async def test_help_callback_not_handled_by_fallback_when_route_seen():
    """After Help wrapper runs, known-prefix fallback must not answer again."""
    import asyncio
    from unittest.mock import MagicMock

    from pyrogram import Client, StopPropagation
    from pyrogram.handlers import CallbackQueryHandler

    from app.handlers import _wrap_callback_handlers_with_auto_answer, register_all
    from app.handlers.priority import FALLBACK_CALLBACK_GROUP
    from app.utils.ui import CB

    bot = Client(
        "test_help_fallback",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.1)
    _wrap_callback_handlers_with_auto_answer(bot)

    help_handler, _group = _find_help_callback_handler(bot)
    assert help_handler is not None

    fallback = None
    for handler in bot.dispatcher.groups.get(FALLBACK_CALLBACK_GROUP, []):
        if not isinstance(handler, CallbackQueryHandler):
            continue
        orig = getattr(handler, "original_callback", handler.callback)
        if getattr(orig, "__name__", "") == "known_prefix_unknown_callback_fallback":
            fallback = handler
            break
    assert fallback is not None

    query = _FakeQuery(f"{CB['HELP_PROMOTE']}:U{HELP_USER_ID}", HELP_USER_ID)
    query.message.chat = SimpleNamespace(
        id=-1003740677405,
        type=SimpleNamespace(value="supergroup"),
    )

    with patch(
        "app.handlers.help_center.panel_callback_edit",
        AsyncMock(return_value=True),
    ), patch(
        "app.services.panel_message_service.get_redis",
        AsyncMock(),
    ):
        with pytest.raises(StopPropagation):
            await help_handler.callback(MagicMock(), query)

    assert getattr(query, "_musicbot_callback_route_seen", False) is True


@pytest.mark.asyncio
async def test_verify_help_callback_router_after_register_all():
    import asyncio

    from pyrogram import Client

    from app.handlers import register_all
    from app.handlers.help_center import verify_help_callback_router

    bot = Client(
        "test_help_verify",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.1)
    assert await verify_help_callback_router(bot, sample_user_id=HELP_USER_ID) is True


@pytest.mark.asyncio
async def test_stale_message_listener_blocks_help_until_cleared():
    """Pyromod listeners for another user on the same message must not block Help."""
    import asyncio
    import sys
    from unittest.mock import AsyncMock, MagicMock

    if not getattr(sys.modules.get("pyromod"), "__file__", None):
        pytest.skip("real pyromod package required")

    from pyrogram import Client, filters
    from pyromod.types import ListenerTypes

    from app.handlers import register_all
    from app.handlers.help_center import _find_help_callback_handler
    from app.utils.ask_result import (
        safe_stop_chat_callback_listeners,
        safe_stop_message_callback_listeners,
    )
    from app.utils.ui import CB

    bot = Client(
        "test_help_listener_block",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await bot.start()

    chat_id = -1003740677405
    message_id = 406
    wrong_user = 111111

    async def waiter():
        try:
            await bot.listen(
                filters=filters.user(wrong_user),
                listener_type=ListenerTypes.CALLBACK_QUERY,
                chat_id=chat_id,
                message_id=message_id,
                timeout=30,
            )
        except Exception:
            return

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0.2)

    query = _dispatch_query(f"{CB['HELP_PROMOTE']}:U{HELP_USER_ID}", HELP_USER_ID)
    query.message.id = message_id
    query.message.chat.id = chat_id

    handler, _group = _find_help_callback_handler(bot)
    assert handler is not None
    assert await handler.check(bot, query) is True

    assert await safe_stop_message_callback_listeners(bot, chat_id, message_id) is True
    assert await safe_stop_chat_callback_listeners(bot, chat_id) is True
    assert await handler.check(bot, query) is True

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await bot.stop()


def _flatten(d, prefix=""):
    keys = []
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_flatten(v, full))
        else:
            keys.append(full)
    return keys
