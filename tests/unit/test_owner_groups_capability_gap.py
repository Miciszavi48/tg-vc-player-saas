"""Owner Groups capability gap-fix tests.

Covers the real end-to-end behavior added to close the verified Groups gap:
pagination, per-row detail, read-only credit lists, leave (confirm+TTL+actor-bound),
inactive/renewal list kinds, owner-scope enforcement, stale credit-callback denial,
and malformed-callback safety. Uses the real SQLite test database (patched globally
by conftest.py), not mocked sessions, so the actual SQL scoping/pagination is exercised.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from app.database.engine import async_session
from app.database.models import Channel, Group, GroupCredit, Owner, Sudo
from app.handlers import owner_panel
from app.utils.ui import CB

OWNER_A = 810001
OWNER_B = 810002
SUDO_OF_A = 810011


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _pm_query(user_id: int):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        data="",
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


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
            def _register(*args, **kwargs):  # noqa: ANN002, ANN003
                def _decorator(fn):
                    return fn

                return _decorator

            return _register
        raise AttributeError(name)


def _handler(bot: _RecorderBot, name: str):
    for fn in bot.callback_handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


@pytest.fixture
def bot():
    b = _RecorderBot()
    owner_panel.register(b, None)
    return b


@pytest.fixture(autouse=True)
async def _seed_owner_groups_fixture():
    async with async_session() as session:
        await session.execute(delete(GroupCredit).where(GroupCredit.chat_id.between(-899999, -800000)))
        await session.execute(delete(Group).where(Group.chat_id.between(-899999, -800000)))
        await session.execute(delete(Channel).where(Channel.chat_id.between(-899999, -800000)))
        await session.execute(delete(Owner).where(Owner.user_id.in_([OWNER_A, OWNER_B])))
        await session.execute(delete(Sudo).where(Sudo.user_id == SUDO_OF_A))
        session.add_all(
            [
                Owner(user_id=OWNER_A, is_active=True),
                Owner(user_id=OWNER_B, is_active=True),
                Sudo(user_id=SUDO_OF_A, added_by=OWNER_A, is_active=True),
            ]
        )
        # Owner A: 4 active groups (direct), 1 active group via sudo, 1 inactive group.
        for i in range(4):
            chat_id = -800100 - i
            session.add(Group(chat_id=chat_id, chat_title=f"A-Group-{i}", status="active", installed_by=OWNER_A))
            session.add(GroupCredit(chat_id=chat_id, chat_type="group", credit_days=30, status="active"))
        session.add(Group(chat_id=-800200, chat_title="A-Sudo-Group", status="active", installed_by=SUDO_OF_A))
        session.add(GroupCredit(chat_id=-800200, chat_type="group", credit_days=1, status="active"))
        session.add(Group(chat_id=-800300, chat_title="A-Inactive-Group", status="inactive", installed_by=OWNER_A))
        # Owner B: 1 active group, must never appear in Owner A's scope.
        session.add(Group(chat_id=-800900, chat_title="B-Group", status="active", installed_by=OWNER_B))
        session.add(GroupCredit(chat_id=-800900, chat_type="group", credit_days=30, status="active"))
        # Owner A: one no-credit group.
        session.add(Group(chat_id=-800400, chat_title="A-NoCredit-Group", status="active", installed_by=OWNER_A))
        session.add(GroupCredit(chat_id=-800400, chat_type="group", credit_days=0, status="active"))
        await session.commit()
    yield
    async with async_session() as session:
        await session.execute(delete(GroupCredit).where(GroupCredit.chat_id.between(-899999, -800000)))
        await session.execute(delete(Group).where(Group.chat_id.between(-899999, -800000)))
        await session.execute(delete(Channel).where(Channel.chat_id.between(-899999, -800000)))
        await session.execute(delete(Owner).where(Owner.user_id.in_([OWNER_A, OWNER_B])))
        await session.execute(delete(Sudo).where(Sudo.user_id == SUDO_OF_A))
        await session.commit()


@pytest.mark.asyncio
async def test_active_groups_paginated_and_owner_scoped():
    # Owner A's active groups: 4 direct + 1 via sudo + 1 no-credit-but-active = 6.
    rows_p0, total_pages = await owner_panel._fetch_owner_group_list_page(
        OWNER_A, owner_panel._OWNER_KIND_ACTIVE_GROUPS, 0
    )
    assert len(rows_p0) == 3  # page_size=3
    assert total_pages == 2
    titles_p0 = {r.title for r in rows_p0}
    assert "B-Group" not in titles_p0

    rows_p1, _ = await owner_panel._fetch_owner_group_list_page(
        OWNER_A, owner_panel._OWNER_KIND_ACTIVE_GROUPS, 1
    )
    assert len(rows_p1) == 3
    all_titles = titles_p0 | {r.title for r in rows_p1}
    assert "A-Sudo-Group" in all_titles
    assert "B-Group" not in all_titles


@pytest.mark.asyncio
async def test_owner_b_scope_excludes_owner_a_groups():
    rows, _ = await owner_panel._fetch_owner_group_list_page(
        OWNER_B, owner_panel._OWNER_KIND_ACTIVE_GROUPS, 0
    )
    titles = {r.title for r in rows}
    assert titles == {"B-Group"}


@pytest.mark.asyncio
async def test_inactive_groups_list_kind():
    rows, _ = await owner_panel._fetch_owner_group_list_page(
        OWNER_A, owner_panel._OWNER_KIND_INACTIVE_GROUPS, 0
    )
    titles = {r.title for r in rows}
    assert titles == {"A-Inactive-Group"}


@pytest.mark.asyncio
async def test_no_credit_list_kind():
    rows, _ = await owner_panel._fetch_owner_group_list_page(
        OWNER_A, owner_panel._OWNER_KIND_NO_CREDIT, 0
    )
    titles = {r.title for r in rows}
    assert titles == {"A-NoCredit-Group"}


@pytest.mark.asyncio
async def test_renewal_list_kind_finds_near_expiry_group():
    rows, _ = await owner_panel._fetch_owner_group_list_page(
        OWNER_A, owner_panel._OWNER_KIND_RENEWAL, 0
    )
    titles = {r.title for r in rows}
    assert "A-Sudo-Group" in titles  # credit_days=1 <= threshold


@pytest.mark.asyncio
async def test_list_page_handler_renders_pagination_and_actions(bot):
    query = _pm_query(OWNER_A)
    await owner_panel._render_owner_group_list_page(query, owner_panel._OWNER_KIND_ACTIVE_GROUPS, 0)
    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert any(cb.startswith(CB["OWN_GRP_DETAIL_PREFIX"]) for cb in cbs)
    assert not any(cb.startswith(CB["OWN_GRP_CREDIT_INC_PREFIX"]) for cb in cbs)
    assert not any(cb.startswith(CB["OWN_GRP_CREDIT_DEC_PREFIX"]) for cb in cbs)
    assert any(cb.startswith(CB["OWN_GRP_LEAVE_CONFIRM_PREFIX"]) for cb in cbs)
    assert any(cb.startswith(CB["OWN_GRP_LIST_PREFIX"]) for cb in cbs)  # next-page nav


@pytest.mark.asyncio
async def test_inactive_list_detail_has_no_credit_or_leave_actions():
    query = _pm_query(OWNER_A)
    await owner_panel._render_owner_group_detail(query, owner_panel._OWNER_KIND_INACTIVE_GROUPS, -800300, 0)
    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert not any(cb.startswith(CB["OWN_GRP_LEAVE_CONFIRM_PREFIX"]) for cb in cbs)
    assert not any(cb.startswith(CB["OWN_GRP_CREDIT_INC_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_detail_back_to_list_preserves_kind_and_page():
    query = _pm_query(OWNER_A)
    await owner_panel._render_owner_group_detail(query, owner_panel._OWNER_KIND_ACTIVE_GROUPS, -800101, 1)
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert f"{CB['OWN_GRP_LIST_PREFIX']}{owner_panel._OWNER_KIND_ACTIVE_GROUPS}:1" in cbs


@pytest.mark.asyncio
async def test_owner_b_denied_detail_on_owner_a_chat():
    query = _pm_query(OWNER_B)
    await owner_panel._render_owner_group_detail(query, owner_panel._OWNER_KIND_ACTIVE_GROUPS, -800101, 0)
    query.message.edit_text.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_credit_inc_stale_callback_denied_without_db_mutation(bot):
    from sqlalchemy import func, select

    from app.database.models import CreditHistory

    handler = _handler(bot, "own_group_credit_inc")
    query = _pm_query(OWNER_A)
    query.data = f"{CB['OWN_GRP_CREDIT_INC_PREFIX']}{owner_panel._OWNER_KIND_ACTIVE_GROUPS}:-800101:0"

    async with async_session() as session:
        before_days = (
            await session.execute(select(GroupCredit.credit_days).where(GroupCredit.chat_id == -800101))
        ).scalar_one()
        before_history = (
            await session.execute(
                select(func.count()).select_from(CreditHistory).where(CreditHistory.chat_id == -800101)
            )
        ).scalar_one()

    ask_result = SimpleNamespace(message=SimpleNamespace(text="7"))
    with patch.object(owner_panel, "_ask", AsyncMock(return_value=ask_result)):
        await handler(AsyncMock(), query)

    query.answer.assert_awaited()
    assert any(call.kwargs.get("show_alert") for call in query.answer.await_args_list)

    async with async_session() as session:
        after_days = (
            await session.execute(select(GroupCredit.credit_days).where(GroupCredit.chat_id == -800101))
        ).scalar_one()
        after_history = (
            await session.execute(
                select(func.count()).select_from(CreditHistory).where(CreditHistory.chat_id == -800101)
            )
        ).scalar_one()
    assert after_days == before_days
    assert after_history == before_history


@pytest.mark.asyncio
async def test_credit_action_denied_for_out_of_scope_chat(bot):
    handler = _handler(bot, "own_group_credit_inc")
    query = _pm_query(OWNER_B)
    query.data = f"{CB['OWN_GRP_CREDIT_INC_PREFIX']}{owner_panel._OWNER_KIND_ACTIVE_GROUPS}:-800101:0"
    await handler(AsyncMock(), query)
    query.answer.assert_awaited()
    assert any(
        call.kwargs.get("show_alert") for call in query.answer.await_args_list
    )


@pytest.mark.asyncio
async def test_leave_flow_confirm_actor_bound_and_deactivates(bot):
    confirm = _handler(bot, "own_group_leave_confirm")
    exec_handler = _handler(bot, "own_group_leave_exec")
    cancel_handler = _handler(bot, "own_group_leave_cancel")

    query = _pm_query(OWNER_A)
    query.data = f"{CB['OWN_GRP_LEAVE_CONFIRM_PREFIX']}{owner_panel._OWNER_KIND_ACTIVE_GROUPS}:-800103:0"
    client = SimpleNamespace(leave_chat=AsyncMock())
    await confirm(client, query)
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    exec_cb = next(cb for cb in cbs if cb.startswith(CB["OWN_GRP_LEAVE_EXEC_PREFIX"]))
    cancel_cb = next(cb for cb in cbs if cb.startswith(CB["OWN_GRP_LEAVE_CANCEL_PREFIX"]))

    # Wrong actor cannot execute another user's confirm token.
    forged_query = _pm_query(OWNER_B)
    forged_query.data = exec_cb
    await exec_handler(client, forged_query)
    forged_query.answer.assert_awaited()
    assert any(c.kwargs.get("show_alert") for c in forged_query.answer.await_args_list)
    client.leave_chat.assert_not_awaited()

    # Cancel returns to the list without leaving.
    cancel_query = _pm_query(OWNER_A)
    cancel_query.data = cancel_cb
    await cancel_handler(client, cancel_query)
    client.leave_chat.assert_not_awaited()
    cancel_query.message.edit_text.assert_awaited_once()

    # Correct actor executes leave -> chat deactivated.
    exec_query = _pm_query(OWNER_A)
    exec_query.data = exec_cb
    await exec_handler(client, exec_query)
    client.leave_chat.assert_awaited_once_with(-800103)

    async with async_session() as session:
        from sqlalchemy import select

        row = (await session.execute(select(Group).where(Group.chat_id == -800103))).scalar_one()
        assert row.status == "inactive"


@pytest.mark.asyncio
async def test_leave_exec_rejects_stale_confirm_token(bot):
    exec_handler = _handler(bot, "own_group_leave_exec")
    stale_issued_at = int(time.time()) - owner_panel._OWNER_CONFIRM_TTL_SECONDS - 10
    query = _pm_query(OWNER_A)
    query.data = (
        f"{CB['OWN_GRP_LEAVE_EXEC_PREFIX']}{owner_panel._OWNER_KIND_ACTIVE_GROUPS}:-800102:0:{OWNER_A}:{stale_issued_at}"
    )
    client = SimpleNamespace(leave_chat=AsyncMock())
    await exec_handler(client, query)
    client.leave_chat.assert_not_awaited()
    query.answer.assert_awaited()


@pytest.mark.parametrize(
    "bad_data",
    [
        "own:grp:d:not_a_kind:-800101:0",  # invalid kind token
        "own:grp:d:act_g:not_a_number:0",  # non-numeric chat_id
        "own:grp:d:act_g:-800101",  # wrong arity
    ],
)
@pytest.mark.asyncio
async def test_malformed_detail_callback_denied_safely(bot, bad_data: str):
    handler = _handler(bot, "own_group_detail")
    query = _pm_query(OWNER_A)
    query.data = bad_data
    await handler(AsyncMock(), query)
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True
    query.message.edit_text.assert_not_awaited()
