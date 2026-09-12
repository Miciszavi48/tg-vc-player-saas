from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.handlers import helper_panel
from app.utils.i18n import t
from app.utils.ui import CB


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


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _query(data: str = CB["HLP_HOME"]):
    return SimpleNamespace(
        id="query-id-hlp-home",
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID, first_name="Dev"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=settings.DEVELOPER_ID, type=SimpleNamespace(value="private")),
            text="menu",
            edit_text=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


def _group_query(data: str = CB["HLP_HOME"]):
    query = _query(data)
    query.message.chat = SimpleNamespace(id=-100123, type=SimpleNamespace(value="supergroup"))
    return query


def _client():
    return SimpleNamespace(get_me=AsyncMock(), send_message=AsyncMock())


def _helper(*, helper_id: int, status: str = "active", calls: int = 0, max_calls: int = 5):
    return SimpleNamespace(
        id=helper_id,
        status=status,
        current_active_calls=calls,
        max_concurrent_calls=max_calls,
        cooldown_until=None,
        banned_until=None,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_hlp_home_registered_private_developer_and_answers_before_helper_reads(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _query()

    async def _helpers_after_answer():
        assert query.answer.await_count == 1
        return [_helper(helper_id=1), _helper(helper_id=2, status="disabled")]

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_all_helpers",
            AsyncMock(side_effect=_helpers_after_answer),
        ),
    ):
        await handler(_client(), query)

    query.answer.assert_awaited_once_with()
    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert t("fa", "admin.helpers.title") in text
    assert "در دسترس" in text
    logs = caplog.text
    assert "hlp_home.context" in logs
    assert "hlp_home.received" in logs
    assert "callback.helper.summary" in logs
    assert "callback.edit.success" in logs


@pytest.mark.asyncio
async def test_hlp_home_wrong_chat_logs_guard_denied(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _group_query()

    await handler(_client(), query)

    query.answer.assert_awaited_once()
    assert "hlp_home.context" in caplog.text
    assert "hlp_home.guard_denied" in caplog.text
    assert "reason=wrong_chat_type" in caplog.text


@pytest.mark.asyncio
async def test_hlp_home_does_not_call_join_session_or_group_helper_paths():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "hlp_home")
    query = _query()

    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_all_helpers",
            AsyncMock(return_value=[_helper(helper_id=1)]),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(side_effect=AssertionError("helper home must not join")),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_detailed",
            AsyncMock(side_effect=AssertionError("helper home must not join")),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_helper_session",
            AsyncMock(side_effect=AssertionError("helper home must not open sessions")),
        ),
        patch(
            "app.services.call_service.ensure_helper_present_for_group",
            AsyncMock(side_effect=AssertionError("helper home must not use group helper binding")),
        ),
    ):
        await handler(_client(), query)

    query.answer.assert_awaited_once_with()
    query.message.edit_text.assert_awaited_once()
