from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config.settings import settings
from app.services.panel_router import build_private_root_payload
from app.utils.decorators import developer_only, owner_or_above, sudo_or_above
from app.utils.i18n import t
from app.utils.ui import CB


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


def _kb_urls(kb) -> set[str]:
    return {
        btn.url
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "url", None)
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_id", "is_owner", "is_sudo", "expected_cb", "expected_url"),
    [
        (settings.DEVELOPER_ID, False, False, CB["DEV_CAT_CREDIT"], None),
        (910001, True, False, CB["OWN_STATS"], None),
        (910002, False, True, CB["SUDO_STATUS"], None),
        (910003, False, False, None, "https://t.me/creator"),
    ],
)
async def test_role_menu_visibility(
    user_id: int,
    is_owner: bool,
    is_sudo: bool,
    expected_cb: str | None,
    expected_url: str | None,
):
    links = {
        "creator": "https://t.me/creator",
        "sudo_1": "https://t.me/sudo1",
        "sudo_2": "https://t.me/sudo2",
        "guide_channel": "https://t.me/guide",
        "bot_channel": "https://t.me/channel",
        "support_group": "https://t.me/support",
        "custom_link": "https://t.me/custom",
        "add_to_group": "https://t.me/test_bot?startgroup=true",
        "add_to_channel": "https://t.me/test_bot?startchannel=true",
    }
    with (
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=is_owner)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=is_sudo)),
        patch("app.services.panel_router.build_start_links", AsyncMock(return_value=links)),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="0")),
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.is_sudo_panel_enabled", AsyncMock(return_value=True)),
    ):
        _, kb = await build_private_root_payload(
            client=None,
            user_id=user_id,
            first_name="Tester",
            lang="fa",
            include_welcome=False,
        )
    cbs = _kb_callbacks(kb)
    urls = _kb_urls(kb)
    if expected_cb:
        assert expected_cb in cbs
    if expected_url:
        assert expected_url in urls
        assert CB["START_PRICING"] not in cbs


@pytest.mark.asyncio
async def test_restricted_callback_denies_non_developer():
    called = False

    @developer_only
    async def _restricted(client, update):
        nonlocal called
        called = True

    query = _pm_query(12345)
    await _restricted(AsyncMock(), query)

    assert called is False
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")
    assert query.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_restricted_callback_denies_sudo_for_owner_actions():
    called = False

    @owner_or_above
    async def _restricted(client, update):
        nonlocal called
        called = True

    query = _pm_query(55555)
    with patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=False)):
        await _restricted(AsyncMock(), query)

    assert called is False
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")
    assert query.answer.await_args.kwargs["show_alert"] is True


@pytest.mark.asyncio
async def test_restricted_callback_denies_regular_for_sudo_actions():
    called = False

    @sudo_or_above
    async def _restricted(client, update):
        nonlocal called
        called = True

    query = _pm_query(77777)
    with patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=False)):
        await _restricted(AsyncMock(), query)

    assert called is False
    query.answer.assert_awaited_once()
    assert query.answer.await_args.args[0] == t("fa", "common.errors.no_access")
    assert query.answer.await_args.kwargs["show_alert"] is True
