from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
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

from app.handlers import owner_panel
from app.repositories import admin_report_repo
from app.repositories.admin_report_repo import ChatInstallRow
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
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


def _compiled_sql(stmt) -> str:  # noqa: ANN001
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


def _sample_rows() -> list[ChatInstallRow]:
    return [
        ChatInstallRow(
            chat_id=-100,
            chat_type="group",
            title="G",
            invite_link=None,
            credit_days=0,
            expire_at=None,
        ),
        ChatInstallRow(
            chat_id=-200,
            chat_type="channel",
            title="C",
            invite_link=None,
            credit_days=0,
            expire_at=None,
        ),
    ]


def test_no_credit_list_lines_format():
    group_label = owner_panel.label(owner_panel._LANG, "chat_type", "group")
    channel_label = owner_panel.label(owner_panel._LANG, "chat_type", "channel")
    unavailable = owner_panel.t(owner_panel._LANG, "reports.value_unavailable")
    lines = owner_panel._no_credit_list_lines(_sample_rows())
    assert lines == [
        owner_panel.t(
            owner_panel._LANG,
            "list_fmt.no_credit_item",
            chat_id=-100,
            chat_type=group_label,
            days=0,
            status=unavailable,
        ),
        owner_panel.t(
            owner_panel._LANG,
            "list_fmt.no_credit_item",
            chat_id=-200,
            chat_type=channel_label,
            days=0,
            status=unavailable,
        ),
    ]


@pytest.mark.asyncio
async def test_get_no_credit_page_empty():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    count_result = MagicMock()
    count_result.scalar.return_value = 0
    sess.execute = AsyncMock(return_value=count_result)

    with patch("app.repositories.admin_report_repo.async_session", return_value=sess):
        rows, total_pages = await admin_report_repo.get_no_credit_page(0, page_size=50)

    assert rows == []
    assert total_pages == 1


@pytest.mark.asyncio
async def test_get_no_credit_page_applies_sql_limit():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)

    count_result = MagicMock()
    count_result.scalar.return_value = 3

    page_result = MagicMock()
    page_result.all.return_value = [
        SimpleNamespace(
            chat_id=-100,
            chat_type="group",
            chat_title="G1",
            invite_link=None,
            credit_days=0,
            expire_at=None,
            group_status="active",
            channel_status=None,
        ),
    ]

    sess.execute = AsyncMock(side_effect=[count_result, page_result])

    with patch("app.repositories.admin_report_repo.async_session", return_value=sess):
        rows, total_pages = await admin_report_repo.get_no_credit_page(0, page_size=50)

    assert len(rows) == 1
    assert rows[0].chat_type == "group"
    assert total_pages == 1
    count_stmt = sess.execute.await_args_list[0].args[0]
    page_stmt = sess.execute.await_args_list[1].args[0]
    count_sql = _compiled_sql(count_stmt)
    assert "groups.status = 'active'" in count_sql
    assert "channels.status = 'active'" in count_sql
    assert page_stmt._limit_clause.value == 50  # noqa: SLF001


@pytest.mark.asyncio
async def test_count_no_credit_chats_requires_active_install_source():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar.return_value = 0
    sess.execute = AsyncMock(return_value=result)

    with patch("app.repositories.admin_report_repo.async_session", return_value=sess):
        await admin_report_repo.count_no_credit_chats()

    sql = _compiled_sql(sess.execute.await_args.args[0])
    assert "groups.status = 'active'" in sql
    assert "channels.status = 'active'" in sql


@pytest.mark.asyncio
async def test_count_renewal_chats_requires_active_install_source():
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar.return_value = 0
    sess.execute = AsyncMock(return_value=result)

    with patch("app.repositories.admin_report_repo.async_session", return_value=sess):
        await admin_report_repo.count_renewal_chats(hours=24)

    sql = _compiled_sql(sess.execute.await_args.args[0])
    assert "groups.status = 'active'" in sql
    assert "channels.status = 'active'" in sql


@pytest.mark.asyncio
async def test_own_list_no_credit_bounded_repo():
    # own_list_no_credit now delegates to the paginated/detail-capable Groups
    # renderer (owner-scoped, DB-paginated) rather than calling the flat
    # unpaginated _get_owner_no_credit_rows helper directly.
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_list_no_credit")
    query = _pm_query(900001, CB["OWN_LIST_NO_CREDIT"])

    with patch(
        "app.handlers.owner_panel._fetch_owner_group_list_page",
        AsyncMock(return_value=(_sample_rows(), 1)),
    ) as fetch_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    fetch_mock.assert_awaited_once_with(900001, owner_panel._OWNER_KIND_NO_CREDIT, 0)
    assert query.message.edit_text.await_count == 1
    text = query.message.edit_text.call_args.args[0]
    assert "-100" in text
    assert "-200" in text


@pytest.mark.asyncio
async def test_own_no_credit_reports_bounded_repo():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_no_credit_reports")
    query = _pm_query(900001, CB["OWN_NO_CREDIT_REPORTS"])

    with (
        patch(
            "app.handlers.owner_panel._get_owner_no_credit_rows",
            AsyncMock(return_value=_sample_rows()),
        ) as rows_mock,
        patch(
            "app.handlers.owner_panel.CreditService.get_zero_credit_chats",
            AsyncMock(),
        ) as load_all_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    rows_mock.assert_awaited_once_with(900001)
    load_all_mock.assert_not_awaited()
    assert query.message.edit_text.await_count == 1


@pytest.mark.asyncio
async def test_own_list_no_credit_empty_alert():
    # Empty state is now rendered as an edited message (with a Done keyboard)
    # rather than a bare callback alert, matching the other paginated lists.
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_list_no_credit")
    query = _pm_query(900001, CB["OWN_LIST_NO_CREDIT"])

    with patch(
        "app.handlers.owner_panel._fetch_owner_group_list_page",
        AsyncMock(return_value=([], 1)),
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.message.edit_text.await_count == 1
