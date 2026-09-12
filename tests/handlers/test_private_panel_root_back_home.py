"""Behavioral verification: Dev/Owner/Sudo root Back → private /start home.

Drives shipped keyboard builders and navigation handlers. External I/O
(Redis, Telegram network, DB role lookups) is mocked; payload assembly and
handlers under test are real.
"""
from __future__ import annotations

import os
import sys
from contextlib import ExitStack, contextmanager
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, call, patch

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

from app.config.settings import settings  # noqa: E402
from app.handlers import callbacks  # noqa: E402
from app.services.panel_router import (  # noqa: E402
    build_private_start_home_payload,
    detect_private_role,
)
from app.utils.button_style import ButtonStyle, apply_button_style_policy  # noqa: E402
from app.utils.filters import dev_filter, owner_filter, sudo_filter  # noqa: E402
from app.utils.i18n import t  # noqa: E402
from app.utils.ui import CB, KeyboardFactory  # noqa: E402

_DEV = int(settings.DEVELOPER_ID)
_OWNER = 920001
_SUDO = 920002
_REGULAR = 920003
_PANEL_MARKERS = {
    "developer": CB["DEV_CAT_CREDIT"],
    "owner": CB["OWN_STATS"],
    "sudo": CB["SUDO_STATUS"],
}
_ENTRY_LABEL = {
    "developer": "start.menu.developer_panel",
    "owner": "start.menu.management_panel",
    "sudo": "start.menu.management_panel",
}
_PUBLIC_LINKS = {
    "creator": "https://t.me/creator",
    "support_group": "https://t.me/support",
}


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


def _handler(name: str):
    bot = _RecorderBot()
    callbacks.register(bot, None)
    for fn in bot.callback_handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _kb_callbacks(kb) -> list[str]:
    if kb is None:
        return []
    return [
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    ]


def _root_kb(role: str, lang: str = "fa"):
    if role == "developer":
        return KeyboardFactory.developer_panel(lang)
    if role == "owner":
        return KeyboardFactory.owner_panel(lang)
    if role == "sudo":
        return KeyboardFactory.sudo_panel(lang)
    raise AssertionError(role)


def _pm_query(
    user_id: int,
    data: str,
    *,
    first_name: str = "User",
    chat_type: str | object = "private",
    chat_id: int | None = None,
    text: str | None = "panel root",
    caption: str | None = None,
    edit_side_effect=None,
):
    if isinstance(chat_type, str):
        type_obj = SimpleNamespace(value=chat_type)
    else:
        type_obj = chat_type
    cid = chat_id if chat_id is not None else user_id
    msg = SimpleNamespace(
        id=77,
        message_id=77,
        chat=SimpleNamespace(id=cid, type=type_obj),
        text=text,
        caption=caption,
        edit_text=AsyncMock(side_effect=edit_side_effect),
        edit_caption=AsyncMock(side_effect=edit_side_effect),
        delete=AsyncMock(),
        reply=AsyncMock(),
    )
    return SimpleNamespace(
        id=f"q-{data}-{user_id}",
        data=data,
        from_user=SimpleNamespace(id=user_id, first_name=first_name, username="u"),
        answer=AsyncMock(),
        message=msg,
    )


def _client():
    return SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(return_value=SimpleNamespace(id=999, message_id=999)),
        stop_listening=AsyncMock(),
    )


@contextmanager
def _role_io(role: str, *, clear_state: AsyncMock | None = None, welcome: str | None = None):
    """Patch external I/O around real start-home payload + navigation handlers."""
    clear = clear_state if clear_state is not None else AsyncMock()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.services.panel_router.detect_private_role",
                AsyncMock(return_value=role),
            )
        )
        stack.enter_context(
            patch(
                "app.services.start_customization_runtime.resolve_single_active_owner_user_id",
                AsyncMock(return_value=_OWNER if role == "owner" else None),
            )
        )
        stack.enter_context(
            patch(
                "app.services.start_customization_runtime.get_start_welcome_text",
                AsyncMock(return_value=welcome or f"welcome-{role}"),
            )
        )
        stack.enter_context(
            patch(
                "app.services.start_customization_runtime.build_start_links",
                AsyncMock(return_value=_PUBLIC_LINKS),
            )
        )
        stack.enter_context(
            patch(
                "app.services.start_customization_service.render_start_menu",
                AsyncMock(
                    return_value=SimpleNamespace(
                        rows=(),
                        uses_legacy_fallback=True,
                    )
                ),
            )
        )
        stack.enter_context(
            patch(
                "app.services.start_customization_service.get_random_message_item",
                AsyncMock(return_value=None),
            )
        )
        stack.enter_context(patch("app.services.panel_message_service.get_redis", AsyncMock()))
        stack.enter_context(patch("app.handlers.callbacks.clear_runtime_state", clear))
        yield clear


# ── Keyboard surface ─────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", ["fa", "en"])
@pytest.mark.parametrize("role", ["developer", "owner", "sudo"])
def test_root_keyboard_exactly_one_back_no_close(role: str, lang: str):
    kb = _root_kb(role, lang)
    callbacks = _kb_callbacks(kb)
    assert callbacks.count(CB["NAV_START"]) == 1
    assert CB["NAV_CLOSE"] not in callbacks
    assert CB["NAV_BACK"] not in callbacks
    back = next(
        btn
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data == CB["NAV_START"]
    )
    assert back.text == t(lang, "common.buttons.back")
    assert _PANEL_MARKERS[role] in callbacks


@pytest.mark.parametrize("lang", ["fa", "en"])
@pytest.mark.parametrize("role", ["developer", "owner", "sudo"])
@pytest.mark.asyncio
async def test_root_back_button_uses_navigation_primary_style(role: str, lang: str):
    kb = _root_kb(role, lang)
    await apply_button_style_policy(kb, style_mode="advanced")
    back = next(
        btn
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data == CB["NAV_START"]
    )
    assert back.style == ButtonStyle.PRIMARY


# ── Real start-home payload ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["developer", "owner", "sudo"])
async def test_start_home_payload_one_role_correct_management_entry(role: str):
    with _role_io(role):
        text, kb = await build_private_start_home_payload(
            _client(),
            {"developer": _DEV, "owner": _OWNER, "sudo": _SUDO}[role],
            role.title(),
            lang="fa",
        )
    assert "welcome" in text or text
    cbs = _kb_callbacks(kb)
    assert cbs.count(CB["WZ_HOME"]) == 1
    assert _PANEL_MARKERS[role] not in cbs
    assert CB["NAV_START"] not in cbs
    entry = kb.inline_keyboard[0][0]
    assert entry.callback_data == CB["WZ_HOME"]
    assert entry.text == t("fa", _ENTRY_LABEL[role])


@pytest.mark.asyncio
async def test_start_home_payload_regular_has_no_management_entry():
    with _role_io("regular", welcome="welcome-regular"):
        text, kb = await build_private_start_home_payload(
            _client(), _REGULAR, "Reg", lang="fa"
        )
    assert text == "welcome-regular"
    assert CB["WZ_HOME"] not in _kb_callbacks(kb)
    assert _PANEL_MARKERS["developer"] not in _kb_callbacks(kb)


# ── Handler behavioral paths (real payload assembly) ─────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "role"),
    [(_DEV, "developer"), (_OWNER, "owner"), (_SUDO, "sudo")],
)
async def test_nav_start_edits_to_start_home_without_send(user_id: int, role: str):
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(user_id, CB["NAV_START"], first_name=role.title())
    with _role_io(role) as clear_state:
        await handler(client, query)

    clear_state.assert_awaited_once_with(client, user_id, user_id)
    assert clear_state.await_args_list[0] == call(client, user_id, user_id)
    query.message.edit_text.assert_awaited_once()
    client.send_message.assert_not_called()
    query.message.delete.assert_not_awaited()
    query.answer.assert_awaited_once_with()

    text_arg = query.message.edit_text.await_args.args[0]
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    assert "welcome" in text_arg
    cbs = _kb_callbacks(kb)
    assert cbs.count(CB["WZ_HOME"]) == 1
    assert _PANEL_MARKERS[role] not in cbs
    assert kb.inline_keyboard[0][0].text == t("fa", _ENTRY_LABEL[role])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "role"),
    [(_DEV, "developer"), (_OWNER, "owner"), (_SUDO, "sudo")],
)
async def test_stale_nav_close_private_returns_start_home(user_id: int, role: str):
    handler = _handler("nav_close")
    client = _client()
    query = _pm_query(user_id, CB["NAV_CLOSE"], first_name=role.title())
    with _role_io(role) as clear_state:
        await handler(client, query)

    clear_state.assert_awaited_once()
    query.message.delete.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    client.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()
    assert _kb_callbacks(query.message.edit_text.await_args.kwargs["reply_markup"]).count(
        CB["WZ_HOME"]
    ) == 1


@pytest.mark.asyncio
async def test_nav_close_accepts_plain_string_chat_type_private():
    """Chat type may be a plain string rather than an enum with .value."""
    handler = _handler("nav_close")
    client = _client()
    query = _pm_query(_DEV, CB["NAV_CLOSE"], chat_type="private")
    query.message.chat.type = "private"
    with _role_io("developer"):
        await handler(client, query)
    query.message.delete.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_nav_close_group_still_deletes_only():
    handler = _handler("nav_close")
    client = _client()
    query = _pm_query(
        _REGULAR,
        CB["NAV_CLOSE"],
        chat_type="supergroup",
        chat_id=-100999,
    )
    await handler(client, query)
    query.message.delete.assert_awaited_once()
    client.send_message.assert_not_called()
    query.message.edit_text.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_edit_failure_sends_exactly_one_fallback_and_remembers_it():
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(
        _DEV,
        CB["NAV_START"],
        edit_side_effect=RuntimeError("MESSAGE_ID_INVALID"),
    )
    with (
        _role_io("developer"),
        patch(
            "app.services.panel_message_service.remember_panel_message",
            AsyncMock(return_value=True),
        ) as remember,
    ):
        await handler(client, query)

    assert query.message.edit_text.await_count >= 1
    client.send_message.assert_awaited_once()
    query.message.delete.assert_awaited_once()
    query.answer.assert_awaited_once_with()
    remember.assert_awaited()
    remembered_id = remember.await_args.args[2]
    assert remembered_id == 999
    kb = client.send_message.await_args.kwargs["reply_markup"]
    assert _kb_callbacks(kb).count(CB["WZ_HOME"]) == 1


@pytest.mark.asyncio
async def test_media_backed_panel_edit_uses_caption_then_no_duplicate_send():
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(
        _DEV,
        CB["NAV_START"],
        text=None,
        caption="old media caption",
    )
    with _role_io("developer"):
        await handler(client, query)

    query.message.edit_caption.assert_awaited_once()
    client.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()
    kb = query.message.edit_caption.await_args.kwargs["reply_markup"]
    assert _kb_callbacks(kb).count(CB["WZ_HOME"]) == 1


@pytest.mark.asyncio
async def test_message_not_modified_counts_as_success_no_fallback():
    class MessageNotModified(Exception):
        pass

    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(
        _DEV,
        CB["NAV_START"],
        edit_side_effect=MessageNotModified("MESSAGE_NOT_MODIFIED"),
    )
    with _role_io("developer"):
        await handler(client, query)

    client.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_repeated_back_does_not_spam_messages():
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(_DEV, CB["NAV_START"])
    with _role_io("developer") as clear_state:
        await handler(client, query)
        await handler(client, query)

    assert query.message.edit_text.await_count == 2
    client.send_message.assert_not_called()
    assert query.answer.await_count == 2
    assert clear_state.await_count == 2


@pytest.mark.asyncio
async def test_role_change_between_panel_open_and_back_drops_management_entry():
    """User was sudo when panel opened; demoted before Back → regular home."""
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(_SUDO, CB["NAV_START"], first_name="WasSudo")
    with _role_io("regular", welcome="welcome-regular"):
        await handler(client, query)

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    assert CB["WZ_HOME"] not in _kb_callbacks(kb)
    assert CB["SUDO_STATUS"] not in _kb_callbacks(kb)
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_missing_fsm_state_still_returns_home():
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(_OWNER, CB["NAV_START"], first_name="Owner")
    with _role_io("owner", clear_state=AsyncMock(side_effect=RuntimeError("no state"))):
        await handler(client, query)

    query.message.edit_text.assert_awaited_once()
    client.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_forged_nav_start_for_regular_user_cannot_open_panel():
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(_REGULAR, CB["NAV_START"], first_name="Reg")
    with _role_io("regular", welcome="welcome-regular"):
        await handler(client, query)

    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["WZ_HOME"] not in cbs
    assert CB["DEV_CAT_CREDIT"] not in cbs
    assert CB["OWN_STATS"] not in cbs
    assert CB["SUDO_STATUS"] not in cbs


@pytest.mark.asyncio
async def test_nav_start_in_group_is_no_op_answered():
    handler = _handler("nav_start")
    client = _client()
    query = _pm_query(_DEV, CB["NAV_START"], chat_type="supergroup", chat_id=-1001)
    await handler(client, query)
    query.message.edit_text.assert_not_awaited()
    client.send_message.assert_not_called()
    query.answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_deleted_message_answers_safely_without_crash():
    handler = _handler("nav_start")
    client = _client()
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=_DEV, first_name="Dev"),
        answer=AsyncMock(),
        message=None,
        data=CB["NAV_START"],
    )
    await handler(client, query)
    query.answer.assert_awaited()
    client.send_message.assert_not_called()


# ── Authorization filters remain role-gated ──────────────────────────────


@pytest.mark.asyncio
async def test_dev_owner_sudo_filters_reject_regular_and_group_local():
    dev = dev_filter()
    own = owner_filter()
    sudo = sudo_filter()

    async def _cb(uid: int):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=uid),
            message=SimpleNamespace(chat=SimpleNamespace(id=uid, type=SimpleNamespace(value="private"))),
        )

    regular = await _cb(_REGULAR)
    with patch("app.utils.filters.user_repo.is_owner", AsyncMock(return_value=False)), patch(
        "app.utils.filters.user_repo.is_sudo", AsyncMock(return_value=False)
    ):
        assert await dev(None, regular) is False
        assert await own(None, regular) is False
        assert await sudo(None, regular) is False

    owner_q = await _cb(_OWNER)
    with patch("app.utils.filters.user_repo.is_owner", AsyncMock(return_value=True)), patch(
        "app.utils.filters.user_repo.is_sudo", AsyncMock(return_value=False)
    ):
        assert await dev(None, owner_q) is False
        assert await own(None, owner_q) is True
        assert await sudo(None, owner_q) is False

    sudo_q = await _cb(_SUDO)
    with patch("app.utils.filters.user_repo.is_owner", AsyncMock(return_value=False)), patch(
        "app.utils.filters.user_repo.is_sudo", AsyncMock(return_value=True)
    ):
        assert await dev(None, sudo_q) is False
        assert await own(None, sudo_q) is False
        assert await sudo(None, sudo_q) is True

    dev_q = await _cb(_DEV)
    with patch("app.utils.filters.user_repo.is_owner", AsyncMock(return_value=False)), patch(
        "app.utils.filters.user_repo.is_sudo", AsyncMock(return_value=False)
    ):
        assert await dev(None, dev_q) is True
        # developer bypasses owner/sudo filters by design
        assert await own(None, dev_q) is True
        assert await sudo(None, dev_q) is True


@pytest.mark.asyncio
async def test_detect_private_role_precedence_developer_over_owner_sudo():
    with (
        patch("app.services.panel_router.is_developer", return_value=True),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=True)),
    ):
        assert await detect_private_role(_DEV) == "developer"

    with (
        patch("app.services.panel_router.is_developer", return_value=False),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=True)),
    ):
        assert await detect_private_role(_OWNER) == "owner"

    with (
        patch("app.services.panel_router.is_developer", return_value=False),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=True)),
    ):
        assert await detect_private_role(_SUDO) == "sudo"

    with (
        patch("app.services.panel_router.is_developer", return_value=False),
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
    ):
        assert await detect_private_role(_REGULAR) == "regular"


def test_panel_modules_still_bind_private_role_filters():
    """Static guard: private panel routes remain filter-gated in source."""
    from pathlib import Path

    dev_src = Path("app/handlers/dev_panel.py").read_text(encoding="utf-8")
    own_src = Path("app/handlers/owner_panel.py").read_text(encoding="utf-8")
    sudo_src = Path("app/handlers/sudo_panel.py").read_text(encoding="utf-8")
    assert "dev_filter()" in dev_src and "private_chat_filter()" in dev_src
    assert "owner_filter()" in own_src and "private_chat_filter()" in own_src
    assert "sudo_filter()" in sudo_src or "_pm_sudo" in sudo_src
    assert "private_chat_filter()" in sudo_src
