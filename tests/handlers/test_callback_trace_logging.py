from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.utils.callback_trace import (
    safe_answer_callback,
    safe_edit_or_send_callback,
    trace_callback_event,
    trace_received,
    trace_unhandled,
)


def _query(data: str = "hlp:home"):
    return SimpleNamespace(
        id="query-id-1",
        data=data,
        from_user=SimpleNamespace(id=6909288370),
        answer=AsyncMock(),
        message=SimpleNamespace(
            id=321,
            chat=SimpleNamespace(id=6909288370, type=SimpleNamespace(value="private")),
            text="menu",
            edit_text=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_callback_trace_received_answer_and_unhandled(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    query = _query("hlp:home")

    trace_received(query)
    await safe_answer_callback(query)
    trace_unhandled(query, reason="test_unknown")

    text = caplog.text
    assert "callback.received" in text
    assert "callback_length=8" in text
    assert "message_id=321" in text
    assert "callback.answer" in text
    assert "result=ok" in text
    assert "callback.unhandled" in text
    assert "reason=test_unknown" in text
    assert "cbid=" in text


@pytest.mark.asyncio
async def test_safe_edit_or_send_callback_replies_when_edit_fails(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    query = _query("hlp:home")
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("cant edit socks5://u:secret@host:1080"))

    result = await safe_edit_or_send_callback(
        SimpleNamespace(send_message=AsyncMock()),
        query,
        "panel",
        handler="helper_panel.hlp_home",
    )

    assert result == "fallback_sent"
    query.message.reply.assert_awaited_once()
    assert "callback.edit" in caplog.text
    assert "callback.edit.start" in caplog.text
    assert "callback.edit.fallback" in caplog.text
    assert "fallback_replied" in caplog.text
    assert "secret" not in caplog.text


def test_custom_callback_event_logs_safe_fields(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    query = _query("hlp:home")

    trace_callback_event(
        "hlp_home.context",
        query,
        handler="hlp_home",
        route_type="helper_home",
        result="allowed",
    )

    assert "hlp_home.context" in caplog.text
    assert "handler=hlp_home" in caplog.text
    assert "route_type=helper_home" in caplog.text
    assert "result=allowed" in caplog.text
