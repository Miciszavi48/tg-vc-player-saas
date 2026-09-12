"""Developer resource-lists expansion: keyboard layout, repositories, handlers."""
from __future__ import annotations

import os
import sys
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
from app.handlers import dev_panel
from app.repositories import admin_report_repo
from app.repositories.admin_report_repo import ChatInstallRow, UserListRow
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


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _kb_labels(kb) -> list[str]:
    return [btn.text for row in kb.inline_keyboard for btn in row]


def _pm_query(user_id: int, data: str = "dummy"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _group_row(**overrides) -> ChatInstallRow:
    base = dict(
        chat_id=-1001,
        chat_type="group",
        title="Group A",
        invite_link="https://t.me/joinchat/abc",
        credit_days=10,
        expire_at=None,
        status="active",
        installed_by=555,
    )
    base.update(overrides)
    return ChatInstallRow(**base)


_TARGET_LIST_CB_KEYS = [
    "DEV_LIST_GROUPS",
    "DEV_LIST_UNLIMITED_GROUPS",
    "DEV_LIST_CALL_SECURITY_GROUPS",
    "DEV_LIST_RENEWAL_GROUPS",
    "DEV_LIST_NO_CREDIT",
    "DEV_LIST_PLAYBACK_GROUPS",
    "DEV_LIST_TEST_GROUPS",
    "DEV_LIST_MUSIC_GROUPS",
    "DEV_LIST_VIDEO_GROUPS",
    "DEV_LIST_INACTIVE_CHANNELS",
    "DEV_LIST_INACTIVE_GROUPS",
    "DEV_LIST_INACTIVE_USERS",
    "DEV_LIST_ACTIVE_USERS",
]


# ── UI / keyboard ─────────────────────────────────────────────────────────────


def test_dev_sub_lists_contains_all_target_buttons():
    for lang in ("fa", "en"):
        kb = KeyboardFactory.dev_sub_lists(lang)
        cbs = _kb_callbacks(kb)
        for key in _TARGET_LIST_CB_KEYS:
            assert CB[key] in cbs, f"missing {key} for {lang}"
        assert CB["DEV_LIST_CHANNELS"] in cbs
        assert CB["DEV_LEAVE_GROUP"] in cbs
        assert CB["NAV_BACK"] in cbs


def test_dev_sub_lists_has_no_missing_labels():
    for lang in ("fa", "en"):
        for label_text in _kb_labels(KeyboardFactory.dev_sub_lists(lang)):
            assert "[missing:" not in label_text
            assert "[invalid:" not in label_text


def test_dev_sub_lists_layout_matches_target_rows():
    kb = KeyboardFactory.dev_sub_lists("fa")
    rows = [[btn.callback_data for btn in row] for row in kb.inline_keyboard]
    assert rows[0] == [CB["DEV_LIST_GROUPS"], CB["DEV_LIST_UNLIMITED_GROUPS"]]
    assert rows[1] == [CB["DEV_LIST_CALL_SECURITY_GROUPS"]]
    assert rows[2] == [CB["DEV_LIST_RENEWAL_GROUPS"], CB["DEV_LIST_NO_CREDIT"]]
    assert rows[3] == [CB["DEV_LIST_PLAYBACK_GROUPS"], CB["DEV_LIST_TEST_GROUPS"]]
    assert rows[4] == [CB["DEV_LIST_MUSIC_GROUPS"], CB["DEV_LIST_VIDEO_GROUPS"]]
    assert rows[5] == [CB["DEV_LIST_INACTIVE_CHANNELS"], CB["DEV_LIST_INACTIVE_GROUPS"]]
    assert rows[6] == [CB["DEV_LIST_INACTIVE_USERS"], CB["DEV_LIST_ACTIVE_USERS"]]
    assert rows[-1] == [CB["NAV_BACK"]]


# ── Repository queries (SQLite via conftest bootstrap) ───────────────────────


async def _seed(models: list) -> None:
    from app.database.engine import async_session

    async with async_session() as session:
        async with session.begin():
            for model in models:
                session.add(model)


async def _chat_ids(fetch, **kwargs) -> set[int]:
    rows, _ = await fetch(0, page_size=500, **kwargs)
    return {r.chat_id for r in rows}


@pytest.mark.asyncio
async def test_repo_unlimited_groups_query():
    from app.database.models import Group, GroupCredit

    await _seed([
        Group(chat_id=-911001, chat_title="Unl A", status="active"),
        GroupCredit(chat_id=-911001, chat_type="group", credit_days=3650, status="unlimited"),
        Group(chat_id=-911002, chat_title="Normal", status="active"),
        GroupCredit(chat_id=-911002, chat_type="group", credit_days=5, status="active"),
    ])
    ids = await _chat_ids(admin_report_repo.get_unlimited_groups_page)
    assert -911001 in ids
    assert -911002 not in ids


@pytest.mark.asyncio
async def test_repo_call_security_groups_query():
    from app.database.models import CallSecuritySettings, Group

    await _seed([
        Group(chat_id=-911011, chat_title="Sec On", status="active"),
        CallSecuritySettings(chat_id=-911011, enabled=True),
        Group(chat_id=-911012, chat_title="Sec Off", status="active"),
        CallSecuritySettings(chat_id=-911012, enabled=False),
    ])
    ids = await _chat_ids(admin_report_repo.get_call_security_groups_page)
    assert -911011 in ids
    assert -911012 not in ids


@pytest.mark.asyncio
async def test_repo_playback_groups_query():
    from app.database.models import Group, PlaybackState

    await _seed([
        Group(chat_id=-911021, chat_title="Playing", status="active"),
        PlaybackState(chat_id=-911021, media_type="audio"),
        Group(chat_id=-911022, chat_title="Idle", status="active"),
    ])
    ids = await _chat_ids(admin_report_repo.get_playback_groups_page)
    assert -911021 in ids
    assert -911022 not in ids


@pytest.mark.asyncio
async def test_repo_trial_groups_query():
    from app.database.models import Group, GroupCredit

    await _seed([
        Group(chat_id=-911031, chat_title="Trial", status="active"),
        GroupCredit(chat_id=-911031, chat_type="group", credit_days=3, is_trial=True, status="active"),
        Group(chat_id=-911032, chat_title="Paid", status="active"),
        GroupCredit(chat_id=-911032, chat_type="group", credit_days=30, is_trial=False, status="active"),
    ])
    ids = await _chat_ids(admin_report_repo.get_trial_groups_page)
    assert -911031 in ids
    assert -911032 not in ids


@pytest.mark.asyncio
async def test_repo_music_and_video_groups_query():
    from app.database.models import ChatSettings, Group

    await _seed([
        Group(chat_id=-911041, chat_title="Music off", status="active"),
        ChatSettings(chat_id=-911041, chat_type="group", music_enabled=False, video_enabled=True),
        Group(chat_id=-911042, chat_title="Video off", status="active"),
        ChatSettings(chat_id=-911042, chat_type="group", music_enabled=True, video_enabled=False),
        # No settings row => defaults (both enabled).
        Group(chat_id=-911043, chat_title="Defaults", status="active"),
    ])
    music_ids = await _chat_ids(admin_report_repo.get_media_mode_groups_page, media="music")
    video_ids = await _chat_ids(admin_report_repo.get_media_mode_groups_page, media="video")

    assert -911041 not in music_ids
    assert -911042 in music_ids
    assert -911043 in music_ids

    assert -911041 in video_ids
    assert -911042 not in video_ids
    assert -911043 in video_ids


@pytest.mark.asyncio
async def test_repo_inactive_groups_and_channels_query():
    from app.database.models import Channel, Group

    await _seed([
        Group(chat_id=-911051, chat_title="Gone", status="inactive"),
        Group(chat_id=-911052, chat_title="Alive", status="active"),
        Channel(chat_id=-911053, chat_title="Chan gone", status="inactive"),
        Channel(chat_id=-911054, chat_title="Chan alive", status="active"),
    ])
    group_ids = await _chat_ids(admin_report_repo.get_inactive_groups_page)
    chan_ids = await _chat_ids(admin_report_repo.get_inactive_channels_page)

    assert -911051 in group_ids
    assert -911052 not in group_ids
    assert -911053 in chan_ids
    assert -911054 not in chan_ids


@pytest.mark.asyncio
async def test_repo_users_query_by_ban_state():
    from app.database.models import User

    await _seed([
        User(user_id=911061, username="alive_user", is_banned=False),
        User(user_id=911062, username="banned_user", is_banned=True),
    ])
    active_rows, _, active_total = await admin_report_repo.get_users_page(
        0, page_size=500, banned=False
    )
    banned_rows, _, banned_total = await admin_report_repo.get_users_page(
        0, page_size=500, banned=True
    )
    active_ids = {r.user_id for r in active_rows}
    banned_ids = {r.user_id for r in banned_rows}

    assert 911061 in active_ids
    assert 911061 not in banned_ids
    assert 911062 in banned_ids
    assert 911062 not in active_ids
    assert active_total >= 1
    assert banned_total >= 1


# ── Handlers / callbacks ─────────────────────────────────────────────────────


_EXTENDED_CASES = [
    ("DEV_LIST_UNLIMITED_GROUPS", "get_unlimited_groups_page", "u", {}),
    ("DEV_LIST_CALL_SECURITY_GROUPS", "get_call_security_groups_page", "s", {}),
    ("DEV_LIST_PLAYBACK_GROUPS", "get_playback_groups_page", "p", {}),
    ("DEV_LIST_TEST_GROUPS", "get_trial_groups_page", "t", {}),
    ("DEV_LIST_MUSIC_GROUPS", "get_media_mode_groups_page", "a", {"media": "music"}),
    ("DEV_LIST_VIDEO_GROUPS", "get_media_mode_groups_page", "v", {"media": "video"}),
    ("DEV_LIST_INACTIVE_GROUPS", "get_inactive_groups_page", "i", {}),
    ("DEV_LIST_INACTIVE_CHANNELS", "get_inactive_channels_page", "x", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cb_key, repo_fn, source, _extra", _EXTENDED_CASES)
async def test_extended_list_callback_opens_page(cb_key, repo_fn, source, _extra):
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_extended")
    query = _pm_query(settings.DEVELOPER_ID, CB[cb_key])
    ctype = "channel" if source == "x" else "group"
    row = _group_row(chat_type=ctype)

    with patch(
        f"app.handlers.dev_panel.admin_report_repo.{repo_fn}",
        AsyncMock(return_value=([row], 1)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.message.edit_text.await_count == 1
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    ctype_char = "c" if ctype == "channel" else "g"
    payload = f"{source}:{ctype_char}:{row.chat_id}:0"
    assert f"{CB['DEV_LIST_DETAIL_PREFIX']}{payload}" in cbs
    assert f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}{payload}" in cbs
    assert "wz:back:dev_lists" in cbs


@pytest.mark.asyncio
async def test_extended_list_pagination_and_nav_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_extended_page")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['PAGE_DEV_LISTX_PREFIX']}u:1")
    row = _group_row()

    with patch(
        "app.handlers.dev_panel.admin_report_repo.get_unlimited_groups_page",
        AsyncMock(return_value=([row], 3)),
    ) as repo_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    assert repo_mock.call_args.args[0] == 1
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['PAGE_DEV_LISTX_PREFIX']}u:0" in cbs
    assert f"{CB['PAGE_DEV_LISTX_PREFIX']}u:2" in cbs


@pytest.mark.asyncio
async def test_extended_list_unknown_source_is_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_extended_page")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['PAGE_DEV_LISTX_PREFIX']}z:0")

    await handler.__wrapped__(SimpleNamespace(), query)

    assert query.answer.await_count >= 1
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_extended_list_empty_state():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_extended")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_UNLIMITED_GROUPS"])

    with patch(
        "app.handlers.dev_panel.admin_report_repo.get_unlimited_groups_page",
        AsyncMock(return_value=([], 1)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "[missing:" not in text
    assert text.strip()


@pytest.mark.asyncio
async def test_non_developer_cannot_open_extended_lists():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_extended")
    query = _pm_query(99999, CB["DEV_LIST_UNLIMITED_GROUPS"])

    with patch(
        "app.handlers.dev_panel.admin_report_repo.get_unlimited_groups_page",
        AsyncMock(return_value=([_group_row()], 1)),
    ):
        result = await handler(SimpleNamespace(), query)

    assert result is None
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_users_list_renders_rows_and_total():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_active_users")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_ACTIVE_USERS"])
    user = UserListRow(
        user_id=424242, username="someone", first_name="Some", is_banned=False, last_seen=None
    )

    with patch(
        "app.handlers.dev_panel.admin_report_repo.get_users_page",
        AsyncMock(return_value=([user], 1, 1)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "424242" in text
    assert "@someone" in text
    assert "[missing:" not in text
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert "wz:back:dev_lists" in cbs


@pytest.mark.asyncio
async def test_inactive_users_pagination():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_users_page")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['PAGE_DEV_USERS_PREFIX']}b:1")
    user = UserListRow(
        user_id=515151, username=None, first_name=None, is_banned=True, last_seen=None
    )

    with patch(
        "app.handlers.dev_panel.admin_report_repo.get_users_page",
        AsyncMock(return_value=([user], 3, 25)),
    ) as repo_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    assert repo_mock.call_args.kwargs["banned"] is True
    assert repo_mock.call_args.args[0] == 1
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['PAGE_DEV_USERS_PREFIX']}b:0" in cbs
    assert f"{CB['PAGE_DEV_USERS_PREFIX']}b:2" in cbs


@pytest.mark.asyncio
async def test_users_empty_state():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_inactive_users")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_INACTIVE_USERS"])

    with patch(
        "app.handlers.dev_panel.admin_report_repo.get_users_page",
        AsyncMock(return_value=([], 1, 0)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "[missing:" not in text
    assert text.strip()


@pytest.mark.asyncio
async def test_extended_source_detail_roundtrip():
    """Detail rows opened from an extended list return to the same source page."""
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    detail_handler = _handler_by_name(bot.callback_handlers, "dev_list_detail")
    row = _group_row(chat_id=-911071)
    query = _pm_query(
        settings.DEVELOPER_ID, f"{CB['DEV_LIST_DETAIL_PREFIX']}u:g:{row.chat_id}:2"
    )

    with patch(
        "app.handlers.dev_panel.admin_report_repo.get_install_row",
        AsyncMock(return_value=row),
    ):
        await detail_handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_LIST_BACK_PREFIX']}u:2" in cbs
