from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.safe_ask import _fallback_ask
from app.handlers.priority import SAFE_ASK_LISTENER_GROUP


class _ListenerClient:
    def __init__(self) -> None:
        self.handlers: dict[int, list] = {}
        self.handler_added = asyncio.Event()
        self.send_message = AsyncMock()

    def add_handler(self, handler, group=0) -> None:
        self.handlers.setdefault(group, []).append(handler)
        self.handler_added.set()

    def remove_handler(self, handler, group=0) -> None:
        self.handlers[group].remove(handler)
        if not self.handlers[group]:
            del self.handlers[group]


async def _start_fallback(client: _ListenerClient, *, timeout: float = 1):
    task = asyncio.create_task(
        _fallback_ask(client, 123, "prompt", timeout, 456, "en")
    )
    await asyncio.wait_for(client.handler_added.wait(), timeout=1)
    assert len(client.handlers.get(SAFE_ASK_LISTENER_GROUP, [])) == 1
    return task


@pytest.mark.asyncio
async def test_group_99_listener_removed_after_success():
    client = _ListenerClient()
    task = await _start_fallback(client)
    handler = client.handlers[SAFE_ASK_LISTENER_GROUP][0]
    message = SimpleNamespace(text="answer")

    await handler.callback(client, message)

    assert await task is message
    assert SAFE_ASK_LISTENER_GROUP not in client.handlers


@pytest.mark.asyncio
async def test_group_99_listener_removed_after_timeout():
    client = _ListenerClient()

    result = await _fallback_ask(client, 123, "prompt", 0, 456, "en")

    assert result is None
    assert SAFE_ASK_LISTENER_GROUP not in client.handlers


@pytest.mark.asyncio
async def test_group_99_listener_removed_after_cancellation():
    client = _ListenerClient()
    task = await _start_fallback(client)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert SAFE_ASK_LISTENER_GROUP not in client.handlers


@pytest.mark.asyncio
async def test_group_99_listener_removed_after_exception():
    client = _ListenerClient()

    with patch("app.utils.safe_ask.asyncio.wait_for", AsyncMock(side_effect=RuntimeError("boom"))):
        with pytest.raises(RuntimeError, match="boom"):
            await _fallback_ask(client, 123, "prompt", 1, 456, "en")

    assert SAFE_ASK_LISTENER_GROUP not in client.handlers


def test_fallback_uses_one_handler_object_for_registration_and_cleanup():
    source = (Path(__file__).resolve().parents[2] / "app/utils/safe_ask.py").read_text(
        encoding="utf-8"
    )

    assert source.count("async def _listener") == 1
    assert source.count("MessageHandler(") == 1
    assert source.count("add_handler(handler, group=SAFE_ASK_LISTENER_GROUP)") == 1
    assert source.count("remove_handler(handler, group=SAFE_ASK_LISTENER_GROUP)") == 1
