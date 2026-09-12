"""Phase C-1 owner install lineage resolver and owner list/status hardening tests."""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
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
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.database.models import Group
from app.handlers import owner_panel
from app.repositories.admin_report_repo import ChatInstallRow
from app.services import owner_scope_service
from app.utils.i18n import t
from app.utils.ui import CB

OWNER_A = 100001
OWNER_B = 100002
SUDO_A = 200001
SUDO_B = 200002
DEV_ID = 123456789
GROUP_DIRECT = -100100
GROUP_SUDO_A = -100200
GROUP_OTHER = -100300
CHANNEL_SUDO_A = -100400
CHANNEL_OTHER = -100500


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, *, scalar=None, scalars=None, rows=None):
        self._scalar = scalar
        self._scalars = scalars if scalars is not None else []
        self._rows = rows if rows is not None else []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _FakeScalars(self._scalars)

    def all(self):
        return self._rows


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _register_other(*args, **kwargs):  # noqa: ANN002, ANN003
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


def _install_row(
    chat_id: int,
    chat_type: str,
    installed_by: int | None,
    *,
    credit_days: int = 5,
) -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=chat_id,
        chat_title=f"title-{chat_id}",
        invite_link=None,
        status="active",
        installed_by=installed_by,
        credit_days=credit_days,
        expire_at=None,
        chat_type=chat_type,
        group_status="active",
        channel_status="active",
    )


def _rows_for_owner(
    owner_id: int,
    sudo_ids: frozenset[int],
    *,
    credit_days: int = 5,
) -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    fixtures = [
        (GROUP_DIRECT, "group", OWNER_A),
        (GROUP_SUDO_A, "group", SUDO_A),
        (GROUP_OTHER, "group", OWNER_B),
        (CHANNEL_SUDO_A, "channel", SUDO_A),
        (CHANNEL_OTHER, "channel", SUDO_B),
    ]
    for chat_id, chat_type, installed_by in fixtures:
        in_scope = installed_by == owner_id or installed_by in sudo_ids
        if in_scope:
            rows.append(_install_row(chat_id, chat_type, installed_by, credit_days=credit_days))
    return rows


def _patch_owner_session(owner_id: int, sudo_ids: frozenset[int], *, credit_days: int = 5):
    session = AsyncMock()

    async def _execute(stmt):
        compiled = str(stmt)
        if "FROM sudos" in compiled or "sudos.user_id" in compiled:
            return _FakeResult(scalars=list(sudo_ids))
        rows = _rows_for_owner(owner_id, sudo_ids, credit_days=credit_days)
        if "FROM groups" in compiled or "FROM channels" in compiled:
            if "groups" in compiled:
                rows = [r for r in rows if r.chat_type == "group"]
            else:
                rows = [r for r in rows if r.chat_type == "channel"]
        if "coalesce(groups.chat_title, channels.chat_title)" in compiled.lower():
            rows = [r for r in rows if r.credit_days <= 0]
        return _FakeResult(rows=rows)

    session.execute = AsyncMock(side_effect=_execute)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return patch("app.handlers.owner_panel.async_session", return_value=cm), patch(
        "app.services.owner_scope_service.get_sudo_user_ids_for_owner",
        AsyncMock(return_value=sudo_ids),
    )


@pytest.mark.asyncio
async def test_owner_direct_install_in_group_list():
    sudo_ids = frozenset({SUDO_A})
    patches = _patch_owner_session(OWNER_A, sudo_ids)
    with patches[0], patches[1]:
        rows = await owner_panel._get_owner_install_rows(OWNER_A, "group")
    chat_ids = {row.chat_id for row in rows}
    assert GROUP_DIRECT in chat_ids


@pytest.mark.asyncio
async def test_sudo_installed_group_appears_under_owner():
    sudo_ids = frozenset({SUDO_A})
    patches = _patch_owner_session(OWNER_A, sudo_ids)
    with patches[0], patches[1]:
        rows = await owner_panel._get_owner_install_rows(OWNER_A, "group")
    chat_ids = {row.chat_id for row in rows}
    assert GROUP_SUDO_A in chat_ids


@pytest.mark.asyncio
async def test_sudo_installed_channel_appears_under_owner():
    sudo_ids = frozenset({SUDO_A})
    patches = _patch_owner_session(OWNER_A, sudo_ids)
    with patches[0], patches[1]:
        rows = await owner_panel._get_owner_install_rows(OWNER_A, "channel")
    chat_ids = {row.chat_id for row in rows}
    assert CHANNEL_SUDO_A in chat_ids


@pytest.mark.asyncio
async def test_other_owner_direct_install_excluded():
    sudo_ids = frozenset({SUDO_A})
    patches = _patch_owner_session(OWNER_A, sudo_ids)
    with patches[0], patches[1]:
        rows = await owner_panel._get_owner_install_rows(OWNER_A, "group")
    chat_ids = {row.chat_id for row in rows}
    assert GROUP_OTHER not in chat_ids


@pytest.mark.asyncio
async def test_other_owner_sudo_install_excluded():
    sudo_ids = frozenset({SUDO_A})
    patches = _patch_owner_session(OWNER_A, sudo_ids)
    with patches[0], patches[1]:
        rows = await owner_panel._get_owner_install_rows(OWNER_A, "channel")
    chat_ids = {row.chat_id for row in rows}
    assert CHANNEL_OTHER not in chat_ids


@pytest.mark.asyncio
async def test_own_stats_counts_owner_and_sudo_installs_only():
    sudo_ids = frozenset({SUDO_A})
    group_rows = [
        ChatInstallRow(chat_id=GROUP_DIRECT, chat_type="group", title="G1", invite_link=None, credit_days=3, expire_at=None),
        ChatInstallRow(chat_id=GROUP_SUDO_A, chat_type="group", title="G2", invite_link=None, credit_days=2, expire_at=None),
    ]
    channel_rows = [
        ChatInstallRow(chat_id=CHANNEL_SUDO_A, chat_type="channel", title="C1", invite_link=None, credit_days=4, expire_at=None),
    ]
    with (
        patch.object(owner_panel, "_LANG", "fa"),
        patch.object(owner_panel, "_get_owner_install_rows", AsyncMock(side_effect=[group_rows, channel_rows])),
        patch.object(
            owner_scope_service,
            "get_owner_sudos",
            AsyncMock(return_value=[SimpleNamespace(user_id=SUDO_A)]),
        ),
    ):
        text = await owner_panel._build_owner_status_text(OWNER_A)
    expected = t(
        "fa",
        "owner_mgmt.scoped_status",
        groups=2,
        channels=1,
        users=t("fa", "common.labels.unknown"),
        sudos=1,
        credits=9,
    )
    assert text == expected
    assert t("fa", "common.labels.unknown") not in text


def test_owner_install_rows_show_resource_status_credit_and_installer():
    rows = [
        ChatInstallRow(
            chat_id=GROUP_DIRECT,
            chat_type="group",
            title="G1",
            invite_link="https://t.me/g1",
            credit_days=3,
            expire_at=None,
            status="active",
            installed_by=OWNER_A,
        )
    ]

    with patch.object(owner_panel, "_LANG", "fa"):
        text = owner_panel._format_install_rows(rows)

    assert "شناسه" in text
    assert str(GROUP_DIRECT) in text
    assert "وضعیت" in text
    assert "فعال" in text
    assert "اعتبار: 3 روز" in text
    assert str(OWNER_A) in text


def test_owner_no_credit_rows_show_status_and_zero_credit():
    rows = [
        ChatInstallRow(
            chat_id=GROUP_DIRECT,
            chat_type="group",
            title="G1",
            invite_link=None,
            credit_days=0,
            expire_at=None,
            status="active",
            installed_by=OWNER_A,
        )
    ]

    with patch.object(owner_panel, "_LANG", "fa"):
        lines = owner_panel._no_credit_list_lines(rows)

    assert str(GROUP_DIRECT) in lines[0]
    assert "وضعیت: فعال" in lines[0]
    assert "اعتبار: 0 روز" in lines[0]


@pytest.mark.asyncio
async def test_no_credit_list_scoped_to_owner_lineage():
    sudo_ids = frozenset({SUDO_A})
    patches = _patch_owner_session(OWNER_A, sudo_ids, credit_days=0)
    with patches[0], patches[1]:
        rows = await owner_panel._get_owner_no_credit_rows(OWNER_A)
    assert rows
    for row in rows:
        assert row.installed_by in {OWNER_A, SUDO_A}
    chat_ids = {row.chat_id for row in rows}
    assert GROUP_OTHER not in chat_ids
    assert CHANNEL_OTHER not in chat_ids


@pytest.mark.asyncio
async def test_forged_group_callback_denied():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _FakeResult(scalar=OWNER_B),
            _FakeResult(scalar=OWNER_B),
        ]
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("app.services.owner_scope_service.async_session", return_value=cm):
        allowed = await owner_scope_service.assert_owner_install_access(
            OWNER_A,
            GROUP_OTHER,
            "group",
            actor_user_id=OWNER_A,
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_forged_channel_callback_denied():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _FakeResult(scalar=SUDO_B),
            _FakeResult(scalar=OWNER_B),
            _FakeResult(scalar=OWNER_B),
        ]
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("app.services.owner_scope_service.async_session", return_value=cm):
        allowed = await owner_scope_service.assert_owner_install_access(
            OWNER_A,
            CHANNEL_OTHER,
            "channel",
            actor_user_id=OWNER_A,
        )
    assert allowed is False


@pytest.mark.asyncio
async def test_deny_owner_install_not_in_scope_shows_message():
    query = _pm_query(OWNER_A, "own:test")
    with patch(
        "app.handlers.owner_panel.owner_scope_service.assert_owner_install_access",
        AsyncMock(return_value=False),
    ):
        denied = await owner_panel._deny_owner_install_not_in_scope(
            query,
            OWNER_A,
            GROUP_OTHER,
            "group",
        )
    assert denied is True
    query.answer.assert_awaited_once()
    assert t("fa", "owner_mgmt.not_in_scope") in query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_developer_override_allows_install_access():
    with patch("app.services.owner_scope_service.is_developer", return_value=True):
        allowed = await owner_scope_service.install_belongs_to_owner(
            OWNER_A,
            GROUP_OTHER,
            "group",
            actor_user_id=DEV_ID,
        )
    assert allowed is True


@pytest.mark.asyncio
async def test_pure_owner_can_access_owner_broadcast_route():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_bc_private")
    invoke = getattr(handler, "__wrapped__", handler)
    query = _pm_query(OWNER_A, CB["OWN_BROADCAST_PRIVATE"])
    message = SimpleNamespace(text="hi", entities=None, chat=SimpleNamespace(id=OWNER_A), id=5)
    client = SimpleNamespace(ask=AsyncMock(return_value=message), send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel.is_developer", return_value=False),
        patch("app.handlers.owner_panel.BroadcastServiceV2.create_broadcast", AsyncMock(return_value=SimpleNamespace(id=77))),
        patch("app.handlers.owner_panel.BroadcastServiceV2._count_recipients", AsyncMock(return_value=3)),
        patch("app.handlers.owner_panel.BroadcastServiceV2.execute", AsyncMock()),
        patch("app.handlers.owner_panel.create_logged_task"),
    ):
        await invoke(client, query)

    assert client.ask.await_count == 1
    client.send_message.assert_awaited()


def test_callback_data_unchanged():
    expected = {
        "OWN_STATS": "own:stats",
        "OWN_CAT_INSTALLS": "own:cat:installs",
        "OWN_LIST_GROUPS": "own:list:groups",
        "OWN_LIST_CHANNELS": "own:list:channels",
        "OWN_LIST_NO_CREDIT": "own:list:no_credit",
        "OWN_NO_CREDIT_REPORTS": "own:no_credit_reports",
        "OWN_BROADCAST_PRIVATE": "own:bc:private",
    }
    for key, value in expected.items():
        assert CB[key] == value


def test_install_scope_clause_includes_owner_and_sudo_ids():
    clause = owner_scope_service.install_scope_clause(Group, OWNER_A, frozenset({SUDO_A}))
    compiled = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert str(OWNER_A) in compiled
    assert str(SUDO_A) in compiled
    assert " OR " in compiled.upper()


@pytest.mark.asyncio
async def test_resolve_owner_from_sudo_installer():
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[
            _FakeResult(scalar=None),
            _FakeResult(scalar=OWNER_A),
            _FakeResult(scalar=OWNER_A),
        ]
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("app.services.owner_scope_service.async_session", return_value=cm):
        resolved = await owner_scope_service.resolve_owner_user_id_from_installer(SUDO_A)
    assert resolved == OWNER_A


@pytest.mark.asyncio
async def test_resolve_owner_from_direct_owner_installer():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_FakeResult(scalar=OWNER_A))
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    with patch("app.services.owner_scope_service.async_session", return_value=cm):
        resolved = await owner_scope_service.resolve_owner_user_id_from_installer(OWNER_A)
    assert resolved == OWNER_A
