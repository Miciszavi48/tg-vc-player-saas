from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def _handler():
    bot = _RecorderBot()
    helper_panel.register(bot, None)
    return next(fn for fn in bot.callback_handlers if fn.__name__ == "hlp_home")


def _query():
    return SimpleNamespace(
        id="query-id-no-helper",
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        data=CB["HLP_HOME"],
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=settings.DEVELOPER_ID, type=SimpleNamespace(value="private")),
            text="menu",
            edit_text=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


def _helper(*, status: str, calls: int = 0, max_calls: int = 5, cooldown_until=None):
    return SimpleNamespace(
        id=1,
        status=status,
        current_active_calls=calls,
        max_concurrent_calls=max_calls,
        cooldown_until=cooldown_until,
        banned_until=None,
    )


async def _open_with_helpers(helpers: list):
    query = _query()
    with (
        patch("app.handlers.helper_panel._clear_helper_wizard_states", AsyncMock()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_all_helpers",
            AsyncMock(return_value=helpers),
        ),
    ):
        await _handler()(SimpleNamespace(send_message=AsyncMock()), query)
    return query.message.edit_text.await_args.args[0]


@pytest.mark.asyncio
async def test_hlp_home_opens_with_no_helpers():
    text = await _open_with_helpers([])

    assert t("fa", "admin.helpers.title") in text
    assert t("fa", "admin.helpers.no_helpers_notice") in text


@pytest.mark.asyncio
async def test_hlp_home_opens_when_all_helpers_quarantined():
    text = await _open_with_helpers([_helper(status="quarantined"), _helper(status="quarantined")])

    assert t("fa", "admin.helpers.title") in text
    assert t("fa", "admin.helpers.all_quarantined_notice") in text


@pytest.mark.asyncio
async def test_hlp_home_opens_when_active_helpers_are_unavailable():
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    text = await _open_with_helpers([
        _helper(status="active", calls=5, max_calls=5),
        _helper(status="active", cooldown_until=future),
    ])

    assert t("fa", "admin.helpers.title") in text
    assert t("fa", "admin.helpers.no_available_notice") in text
