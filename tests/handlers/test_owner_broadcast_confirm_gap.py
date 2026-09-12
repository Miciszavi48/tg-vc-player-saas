"""Owner broadcast confirmation gap-fix tests.

Owner send/forward broadcast previously fired immediately after the ask step
with no confirmation, unlike every other destructive Owner action (sudo
remove, force-join remove, ban-all, title clear). This proves the new
actor-bound, TTL'd confirm step gates broadcast creation/execution, and that
malformed/forged/stale confirm callbacks are denied safely without ever
creating a broadcast. Uses mocks only — no live broadcasts are sent.
"""
from __future__ import annotations

import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "pyromod" not in sys.modules:  # pragma: no cover - test shim
    _pyromod = ModuleType("pyromod")
    _pyromod_exc = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    _pyromod_exc.ListenerStopped = _ListenerStopped
    _pyromod.exceptions = _pyromod_exc
    sys.modules["pyromod"] = _pyromod
    sys.modules["pyromod.exceptions"] = _pyromod_exc

from app.handlers import owner_panel  # noqa: E402
from app.utils.ask_result import AskResult  # noqa: E402
from app.utils.ui import CB  # noqa: E402

OWNER_ID = 820001


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
            # Unwrap the @owner_or_above guard so tests exercise the handler
            # body directly with an arbitrary owner id (not DEVELOPER_ID).
            return getattr(fn, "__wrapped__", fn)
    raise AssertionError(f"handler not found: {name}")


@pytest.fixture
def bot():
    b = _RecorderBot()
    owner_panel.register(b, None)
    return b


def _pm_query(user_id: int, data: str = ""):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id),
            edit_text=AsyncMock(),
        ),
    )


def _fake_source_message(chat_id: int = 820001, message_id: int = 555):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        id=message_id,
        empty=False,
        text="Hello broadcast",
        entities=None,
    )


def _claim_redis(*outcomes):
    return SimpleNamespace(set=AsyncMock(side_effect=list(outcomes)))


def _consume_task(coro, **_kwargs):
    coro.close()


@pytest.mark.asyncio
async def test_owner_broadcast_shows_preview_not_immediate_send(bot):
    handler = _handler(bot, "own_bc_group")
    query = _pm_query(OWNER_ID)
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch.object(
            owner_panel, "_ask", AsyncMock(return_value=AskResult(message=_fake_source_message()))
        ),
        patch.object(owner_panel, "notify_ask_abort", AsyncMock(return_value=False)),
        patch.object(owner_panel.BroadcastServiceV2, "_count_recipients", AsyncMock(return_value=42)),
        patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock()) as create_mock,
        patch.object(owner_panel, "create_logged_task", MagicMock()),
    ):
        await handler(client, query)

    # No broadcast is created until the owner explicitly confirms.
    create_mock.assert_not_awaited()
    client.send_message.assert_awaited_once()
    text = client.send_message.await_args.args[1]
    assert "42" in text
    kb = client.send_message.await_args.kwargs["reply_markup"]
    cbs = {btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data}
    assert any(cb.startswith(CB["OWN_BC_CONFIRM_EXEC_PREFIX"]) for cb in cbs)
    assert any(cb.startswith(CB["OWN_BC_CONFIRM_CANCEL_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_confirm_exec_creates_and_schedules_broadcast(bot):
    handler = _handler(bot, "own_bc_confirm_exec")
    issued_at = int(time.time())
    query = _pm_query(
        OWNER_ID, f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}{OWNER_ID}:555:gc:{OWNER_ID}:{issued_at}"
    )
    client = SimpleNamespace(get_messages=AsyncMock(return_value=_fake_source_message()))
    bc = SimpleNamespace(id=99)

    with (
        patch.object(owner_panel, "get_redis", AsyncMock(return_value=_claim_redis(True))),
        patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock(return_value=bc)) as create_mock,
        patch.object(owner_panel.BroadcastServiceV2, "_count_recipients", AsyncMock(return_value=42)),
        patch.object(owner_panel.BroadcastServiceV2, "execute", AsyncMock()),
        patch.object(
            owner_panel,
            "create_logged_task",
            MagicMock(side_effect=_consume_task),
        ) as task_mock,
    ):
        await handler(client, query)

    create_mock.assert_awaited_once()
    assert create_mock.await_args.kwargs["admin_id"] == OWNER_ID
    assert create_mock.await_args.kwargs["target_scope"] == "groups"
    task_mock.assert_called_once()
    query.message.edit_text.assert_awaited_once()
    assert "99" in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_confirm_exec_denies_wrong_actor(bot):
    handler = _handler(bot, "own_bc_confirm_exec")
    issued_at = int(time.time())
    other_user = 820002
    query = _pm_query(
        other_user, f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}{OWNER_ID}:555:gc:{OWNER_ID}:{issued_at}"
    )
    client = SimpleNamespace(get_messages=AsyncMock())

    with patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock()) as create_mock:
        await handler(client, query)

    create_mock.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_confirm_exec_denies_stale_token(bot):
    handler = _handler(bot, "own_bc_confirm_exec")
    stale_issued_at = int(time.time()) - owner_panel._OWNER_CONFIRM_TTL_SECONDS - 10
    query = _pm_query(
        OWNER_ID, f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}{OWNER_ID}:555:gc:{OWNER_ID}:{stale_issued_at}"
    )
    client = SimpleNamespace(get_messages=AsyncMock())

    with (
        patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock()) as create_mock,
        patch.object(owner_panel, "_show_owner_root", AsyncMock()) as root_mock,
    ):
        await handler(client, query)

    create_mock.assert_not_awaited()
    root_mock.assert_awaited_once()
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_confirm_cancel_creates_no_broadcast(bot):
    handler = _handler(bot, "own_bc_confirm_cancel")
    issued_at = int(time.time())
    query = _pm_query(
        OWNER_ID, f"{CB['OWN_BC_CONFIRM_CANCEL_PREFIX']}{OWNER_ID}:555:gc:{OWNER_ID}:{issued_at}"
    )

    with patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock()) as create_mock:
        await handler(SimpleNamespace(), query)

    create_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.parametrize(
    "bad_data",
    [
        f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}abc:555:gc:1:1",  # non-numeric chat_id
        f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}1:555:zz:1:1",  # unknown mode code
        f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}1:555:gc:1",  # wrong arity
    ],
)
@pytest.mark.asyncio
async def test_confirm_exec_rejects_malformed_payload(bot, bad_data: str):
    handler = _handler(bot, "own_bc_confirm_exec")
    query = _pm_query(OWNER_ID, bad_data)
    with patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock()) as create_mock:
        await handler(SimpleNamespace(), query)
    create_mock.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_confirm_exec_handles_expired_source_message(bot):
    handler = _handler(bot, "own_bc_confirm_exec")
    issued_at = int(time.time())
    query = _pm_query(
        OWNER_ID, f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}{OWNER_ID}:555:gc:{OWNER_ID}:{issued_at}"
    )
    client = SimpleNamespace(get_messages=AsyncMock(return_value=None))

    with (
        patch.object(owner_panel, "get_redis", AsyncMock(return_value=_claim_redis(True))),
        patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock()) as create_mock,
    ):
        await handler(client, query)

    create_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    assert "expired" in query.message.edit_text.await_args.args[0].lower() or "دیگر" in query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_repeated_owner_broadcast_confirm_does_not_persist_or_redraw_twice(bot):
    handler = _handler(bot, "own_bc_confirm_exec")
    issued_at = int(time.time())
    query = _pm_query(
        OWNER_ID,
        f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}{OWNER_ID}:555:gc:{OWNER_ID}:{issued_at}",
    )
    client = SimpleNamespace(get_messages=AsyncMock(return_value=_fake_source_message()))
    bc = SimpleNamespace(id=99)
    create_mock = AsyncMock(return_value=bc)

    with (
        patch.object(owner_panel, "get_redis", AsyncMock(return_value=_claim_redis(True, False))),
        patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", create_mock),
        patch.object(owner_panel.BroadcastServiceV2, "_count_recipients", AsyncMock(return_value=42)),
        patch.object(owner_panel.BroadcastServiceV2, "execute", AsyncMock()),
        patch.object(
            owner_panel,
            "create_logged_task",
            MagicMock(side_effect=_consume_task),
        ),
    ):
        await handler(client, query)
        await handler(client, query)

    create_mock.assert_awaited_once()
    client.get_messages.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_broadcast_redis_claim_failure_fails_closed(bot):
    handler = _handler(bot, "own_bc_confirm_exec")
    issued_at = int(time.time())
    query = _pm_query(
        OWNER_ID,
        f"{CB['OWN_BC_CONFIRM_EXEC_PREFIX']}{OWNER_ID}:555:gc:{OWNER_ID}:{issued_at}",
    )
    client = SimpleNamespace(get_messages=AsyncMock())

    with (
        patch.object(owner_panel, "get_redis", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch.object(owner_panel.BroadcastServiceV2, "create_broadcast", AsyncMock()) as create_mock,
    ):
        await handler(client, query)

    create_mock.assert_not_awaited()
    client.get_messages.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
