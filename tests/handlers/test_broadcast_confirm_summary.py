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

from app.config.settings import settings
from app.repositories import broadcast_repo
from app.handlers import broadcast_wizard
from app.utils.ui import CB


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


class _SessionContext:
    def __init__(self, session) -> None:
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


@pytest.mark.asyncio
async def test_broadcast_confirm_now_emits_single_final_summary():
    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bcw_confirm")
    invoke = getattr(handler, "__wrapped__", handler)

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        data=CB["BCW_CONFIRM"],
    )
    state = {
        "payload": {"payload_type": "text", "text_content": "hello"},
        "targets": ["users", "groups", "channels"],
        "mode": "send",
        "schedule": "now",
        "admin_chat_id": 100,
        "admin_msg_id": 200,
        "filter": "all",
    }

    def _consume_task(coro, **_kwargs):
        coro.close()

    create_task_mock = MagicMock(side_effect=_consume_task)
    async def _create_many(rows):
        for index, row in enumerate(rows, start=1):
            row.id = index
        return rows

    with (
        patch("app.handlers.broadcast_wizard._get_state", AsyncMock(return_value=state)),
        patch("app.handlers.broadcast_wizard._clear_state", AsyncMock()),
        patch("app.handlers.broadcast_wizard._claim_confirmation", AsyncMock(return_value=True)),
        patch(
            "app.handlers.broadcast_wizard.broadcast_repo.create_many",
            AsyncMock(side_effect=_create_many),
        ) as create_mock,
        patch(
            "app.handlers.broadcast_wizard.create_logged_task",
            create_task_mock,
        ),
    ):
        await invoke(SimpleNamespace(), query)

    create_mock.assert_awaited_once()
    assert len(create_mock.await_args.args[0]) == 3
    assert create_task_mock.call_count == 3
    assert query.message.edit_text.await_count == 1
    final_text = query.message.edit_text.await_args.args[0]
    assert "3" in final_text
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    cbs = _callback_data_set(kb)
    assert f"{CB['WZ_BACK_PREFIX']}dev_broadcast" in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_repeated_broadcast_confirm_tap_persists_only_once():
    bot = _RecorderBot()
    broadcast_wizard.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bcw_confirm")
    invoke = getattr(handler, "__wrapped__", handler)
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        data=CB["BCW_CONFIRM"],
    )
    state = {
        "wizard_id": "one-wizard",
        "payload": {"payload_type": "text", "text_content": "hello"},
        "targets": ["users"],
        "mode": "send",
        "schedule": "at",
        "run_at": "2030-01-01T00:00:00+00:00",
        "admin_chat_id": 100,
        "admin_msg_id": 200,
        "filter": "all",
    }

    async def _create_many(rows):
        rows[0].id = 1
        return rows

    create_many = AsyncMock(side_effect=_create_many)
    with (
        patch("app.handlers.broadcast_wizard._get_state", AsyncMock(return_value=state)),
        patch("app.handlers.broadcast_wizard._clear_state", AsyncMock()),
        patch(
            "app.handlers.broadcast_wizard._claim_confirmation",
            AsyncMock(side_effect=[True, False]),
        ),
        patch(
            "app.handlers.broadcast_wizard.broadcast_repo.create_many",
            create_many,
        ),
        patch("app.scheduler.scheduler.add_job", MagicMock()),
    ):
        await invoke(SimpleNamespace(), query)
        await invoke(SimpleNamespace(), query)

    create_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_repo_create_many_flushes_ids_and_commits_once():
    rows = [SimpleNamespace(id=None), SimpleNamespace(id=None)]
    session = SimpleNamespace(
        add_all=MagicMock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )

    with patch.object(
        broadcast_repo,
        "async_session",
        return_value=_SessionContext(session),
    ):
        result = await broadcast_repo.create_many(rows)

    assert result is rows
    session.add_all.assert_called_once_with(rows)
    session.flush.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.refresh.assert_not_awaited()
