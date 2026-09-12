"""Single-Owner ``/start`` override invariant.

The Owner ``/start`` override must be resolved from the *acting* user's own
identity (``user_repo.is_owner(user_id)``), never from an ambiguous unordered
"first Owner row" lookup. These tests pin that behavior so a future refactor
cannot reintroduce a global first-row resolution.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.services.panel_router import build_private_root_payload, detect_private_role
from app.services.bot_settings_service import resolve_single_active_owner_user_id
from app.utils.ui import CB

OWNER_ID = 910001
OTHER_ID = 920002

_LINKS = {
    "creator": "https://t.me/creator",
    "guide_channel": "https://t.me/guide",
    "bot_channel": "https://t.me/channel",
    "support_group": "https://t.me/support",
    "add_to_group": "https://t.me/test_bot?startgroup=true",
    "add_to_channel": "https://t.me/test_bot?startchannel=true",
}


def _kb_callbacks(kb) -> set[str]:
    if kb is None:
        return set()
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


@contextmanager
def _panel_router_env(is_owner_mock: AsyncMock, get_all_owners_mock: AsyncMock):
    with (
        patch("app.services.panel_router.user_repo.is_owner", is_owner_mock),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.services.panel_router.user_repo.get_all_owners", get_all_owners_mock),
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.is_sudo_panel_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.build_start_links", AsyncMock(return_value=_LINKS)),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
        patch("app.services.panel_router.settings_repo.get_bot_setting_bool", AsyncMock(return_value=True)),
    ):
        yield


@pytest.mark.asyncio
async def test_start_override_resolves_by_acting_user_identity():
    """The acting owner id gets the owner panel; a different id does not."""
    # is_owner is True *only* for the specific acting owner — a per-user check.
    is_owner = AsyncMock(side_effect=lambda uid: uid == OWNER_ID)
    get_all_owners = AsyncMock(return_value=[])

    with _panel_router_env(is_owner, get_all_owners):
        _, owner_kb = await build_private_root_payload(
            client=None, user_id=OWNER_ID, first_name="Owner", lang="fa", include_welcome=False
        )
        _, other_kb = await build_private_root_payload(
            client=None, user_id=OTHER_ID, first_name="Other", lang="fa", include_welcome=False
        )

    assert CB["OWN_STATS"] in _kb_callbacks(owner_kb)
    assert CB["OWN_STATS"] not in _kb_callbacks(other_kb)
    # is_owner was consulted with the acting user's own id, not a scan.
    assert is_owner.await_args_list[0].args[0] == OWNER_ID


@pytest.mark.asyncio
async def test_start_override_never_uses_first_owner_row_lookup():
    """The override must not resolve via an unordered global owner-row lookup."""
    is_owner = AsyncMock(return_value=True)
    get_all_owners = AsyncMock(return_value=[])

    with _panel_router_env(is_owner, get_all_owners):
        role = await detect_private_role(OWNER_ID)
        _, kb = await build_private_root_payload(
            client=None, user_id=OWNER_ID, first_name="Owner", lang="fa", include_welcome=False
        )

    assert role == "owner"
    assert CB["OWN_STATS"] in _kb_callbacks(kb)
    # No ambiguous "first Owner row" resolution during the /start override.
    get_all_owners.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_owner_actor_never_gets_owner_panel_even_when_owners_exist():
    """Owners existing in the table must not grant a non-owner the owner panel."""
    is_owner = AsyncMock(side_effect=lambda uid: uid == OWNER_ID)
    # A populated owner table must be irrelevant to a non-owner actor.
    get_all_owners = AsyncMock(
        return_value=[type("O", (), {"user_id": OWNER_ID})()]
    )

    with _panel_router_env(is_owner, get_all_owners):
        role = await detect_private_role(OTHER_ID)
        _, kb = await build_private_root_payload(
            client=None, user_id=OTHER_ID, first_name="Other", lang="fa", include_welcome=False
        )

    assert role == "regular"
    assert CB["OWN_STATS"] not in _kb_callbacks(kb)
    get_all_owners.assert_not_awaited()


@pytest.mark.asyncio
async def test_designated_start_owner_resolves_without_ambiguous_row_order():
    get_all_owners = AsyncMock()
    with (
        patch(
            "app.services.bot_settings_service.settings_repo.get_bot_setting",
            AsyncMock(return_value=str(OWNER_ID)),
        ),
        patch(
            "app.services.bot_settings_service.user_repo.is_owner",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.bot_settings_service.user_repo.get_all_owners",
            get_all_owners,
        ),
    ):
        resolved = await resolve_single_active_owner_user_id()

    assert resolved == OWNER_ID
    get_all_owners.assert_not_awaited()
