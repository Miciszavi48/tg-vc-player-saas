"""Role-boundary guardrails for the Developer / Owner / Sudo panel split.

Proves the four boundaries required by the role-ladder UI guardrails:

* Developer retains technical authority (developer-only routes + dev panel).
* Owner can reach the intended ``own:*`` operational routes.
* Sudo cannot access Owner (``owner_or_above``) routes.
* Regular users cannot access Owner routes.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.utils.decorators import developer_only, owner_or_above
from app.utils.filters import owner_filter, private_chat_filter
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory

DEVELOPER_ID = settings.DEVELOPER_ID
OWNER_ID = 910001
SUDO_ID = 910002
REGULAR_ID = 910003


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _pm_query(user_id: int):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=user_id, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def _group_query(user_id: int):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=-100910001, type=SimpleNamespace(value="group")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


async def _invoke(decorator, user_id: int, *, is_owner: bool = False) -> bool:
    """Run a decorated no-op as ``user_id``; return True when the body ran."""
    ran = False

    @decorator
    async def _protected(client, update):  # noqa: ANN001
        nonlocal ran
        ran = True

    query = _pm_query(user_id)
    with (
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=is_owner)),
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
    ):
        await _protected(AsyncMock(), query)
    return ran, query


# ── Developer technical authority ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_developer_retains_developer_only_authority():
    ran, _ = await _invoke(developer_only, DEVELOPER_ID)
    assert ran is True


@pytest.mark.asyncio
async def test_owner_cannot_reach_developer_only_routes():
    ran, query = await _invoke(developer_only, OWNER_ID, is_owner=True)
    assert ran is False
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_developer_panel_exposes_technical_authority_routes():
    cbs = _kb_callbacks(KeyboardFactory.developer_panel("fa"))
    # Developer keeps the technical/global control surface.
    assert CB["DEV_CAT_CREDIT"] in cbs
    # Owner operational routes never leak into the developer root.
    assert not any(cb.startswith("own:") for cb in cbs)


# ── Owner reaches own:* operational routes ─────────────────────────────────


@pytest.mark.asyncio
async def test_owner_allowed_on_owner_routes():
    ran, _ = await _invoke(owner_or_above, OWNER_ID, is_owner=True)
    assert ran is True


@pytest.mark.asyncio
async def test_developer_allowed_on_owner_routes():
    # Developer is above Owner in the ladder and bypasses the owner check.
    ran, _ = await _invoke(owner_or_above, DEVELOPER_ID)
    assert ran is True


def test_owner_panel_exposes_operational_routes():
    cbs = _kb_callbacks(KeyboardFactory.owner_panel("fa"))
    for route in ("OWN_STATS", "OWN_GROUPS", "OWN_CREDIT", "OWN_SUDOS", "OWN_MODERATION"):
        assert CB[route] in cbs, route
    # Developer global controls never leak into the owner root.
    assert not any(cb.startswith("dev:") for cb in cbs)


@pytest.mark.asyncio
async def test_owner_panel_filter_rejects_group_chat_callbacks():
    pm_owner_filter = owner_filter() & private_chat_filter()
    with patch("app.utils.filters.user_repo.is_owner", AsyncMock(return_value=True)):
        assert await pm_owner_filter(None, _pm_query(OWNER_ID)) is True
        assert await pm_owner_filter(None, _group_query(OWNER_ID)) is False


# ── Sudo cannot access Owner routes ────────────────────────────────────────


@pytest.mark.asyncio
async def test_sudo_denied_on_owner_routes():
    ran, query = await _invoke(owner_or_above, SUDO_ID, is_owner=False)
    assert ran is False
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")
    assert query.answer.await_args.kwargs.get("show_alert") is True


# ── Regular users cannot access Owner routes ───────────────────────────────


@pytest.mark.asyncio
async def test_regular_user_denied_on_owner_routes():
    ran, query = await _invoke(owner_or_above, REGULAR_ID, is_owner=False)
    assert ran is False
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")
    assert query.answer.await_args.kwargs.get("show_alert") is True
