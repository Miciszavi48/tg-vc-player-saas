"""Owner navigation isolation guardrails.

Proves that no Owner Back / Home / Cancel / Done path can resolve to:

* the Developer root panel,
* a ``dev:*`` callback,
* a ``dev_*`` wizard navigation token, or
* a Developer keyboard builder.

Covers both the *static* surface (every Owner keyboard builder) and the
*dynamic* resolution (``resolve_navigation_payload`` for owner/dev tokens).
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.services.wizard_ui import (
    TOKEN_DEV_CREDIT,
    TOKEN_DEV_ROOT,
    TOKEN_OWNER_ROOT,
    TOKEN_ROLE_ROOT,
    _safe_token,
    build_cancel_kb,
    build_done_kb,
    resolve_navigation_payload,
    wz_back_callback,
    wz_cancel_callback,
)
from app.utils.ui import CB, KeyboardFactory

OWNER_ID = 910001

_LINKS = {
    "creator": "https://t.me/creator",
    "guide_channel": "https://t.me/guide",
    "bot_channel": "https://t.me/channel",
    "support_group": "https://t.me/support",
    "add_to_group": "https://t.me/test_bot?startgroup=true",
    "add_to_channel": "https://t.me/test_bot?startchannel=true",
}

# Every Owner keyboard builder, invoked with default args.
_OWNER_KEYBOARDS = {
    "owner_panel": lambda: KeyboardFactory.owner_panel("fa"),
    "owner_sub_groups": lambda: KeyboardFactory.owner_sub_groups("fa"),
    "owner_sub_credit": lambda: KeyboardFactory.owner_sub_credit("fa"),
    "owner_sub_lists": lambda: KeyboardFactory.owner_sub_lists("fa"),
    "owner_sub_moderation": lambda: KeyboardFactory.owner_sub_moderation("fa"),
    "owner_sub_media": lambda: KeyboardFactory.owner_sub_media("fa"),
    "owner_sub_sudo_titles": lambda: KeyboardFactory.owner_sub_sudo_titles("fa"),
    "owner_sub_installs": lambda: KeyboardFactory.owner_sub_installs("fa"),
    "owner_sub_broadcast": lambda: KeyboardFactory.owner_sub_broadcast("fa"),
    "owner_sub_settings": lambda: KeyboardFactory.owner_sub_settings("fa"),
    "owner_sub_users": lambda: KeyboardFactory.owner_sub_users("fa"),
    "owner_sub_reports": lambda: KeyboardFactory.owner_sub_reports("fa"),
    "owner_sub_billing": lambda: KeyboardFactory.owner_sub_billing("fa"),
    "owner_sub_texts": lambda: KeyboardFactory.owner_sub_texts("fa"),
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


def _nav_token(cb: str) -> str | None:
    """Return the wizard token embedded in a wz:back:/wz:cancel: callback."""
    for prefix in (CB["WZ_BACK_PREFIX"], CB["WZ_CANCEL_PREFIX"]):
        if cb.startswith(prefix):
            return cb[len(prefix):]
    return None


@contextmanager
def _owner_router_env():
    with (
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.services.panel_router.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.is_sudo_panel_enabled", AsyncMock(return_value=True)),
        patch("app.services.panel_router.build_start_links", AsyncMock(return_value=_LINKS)),
        patch("app.services.panel_router.get_start_welcome_text", AsyncMock(return_value="welcome")),
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
        patch("app.services.panel_router.settings_repo.get_bot_setting_bool", AsyncMock(return_value=True)),
    ):
        yield


# ── Static surface: no dev leakage in any Owner keyboard ───────────────────


@pytest.mark.parametrize("name", sorted(_OWNER_KEYBOARDS))
def test_owner_keyboard_has_no_dev_callbacks(name: str):
    cbs = _kb_callbacks(_OWNER_KEYBOARDS[name]())
    leaked = {cb for cb in cbs if cb.startswith("dev:")}
    assert not leaked, f"{name} leaks dev callbacks: {leaked}"


@pytest.mark.parametrize("name", sorted(_OWNER_KEYBOARDS))
def test_owner_keyboard_nav_tokens_are_not_dev(name: str):
    cbs = _kb_callbacks(_OWNER_KEYBOARDS[name]())
    for cb in cbs:
        token = _nav_token(cb)
        if token is not None:
            assert not token.startswith("dev"), f"{name} emits dev nav token: {token}"


def test_owner_keyboards_never_reference_dev_root_token():
    for name, factory in _OWNER_KEYBOARDS.items():
        for cb in _kb_callbacks(factory()):
            token = _nav_token(cb)
            assert token != TOKEN_DEV_ROOT, f"{name} routes to dev root"


# ── Done / Cancel keyboards for owner flows carry owner_root only ──────────


def test_owner_done_and_cancel_keyboards_target_owner_root():
    done = _kb_callbacks(build_done_kb("fa", TOKEN_OWNER_ROOT))
    cancel = _kb_callbacks(build_cancel_kb("fa", TOKEN_OWNER_ROOT))

    assert wz_back_callback(TOKEN_OWNER_ROOT) in done
    assert wz_cancel_callback(TOKEN_OWNER_ROOT) in cancel
    for cb in done | cancel:
        token = _nav_token(cb)
        if token is not None:
            assert token == TOKEN_OWNER_ROOT


def test_safe_token_falls_back_to_role_root_not_dev():
    assert _safe_token("nonexistent_token") == TOKEN_ROLE_ROOT
    assert not _safe_token("nonexistent_token").startswith("dev")


# ── Dynamic resolution: owner tokens resolve to the owner panel ────────────


@pytest.mark.asyncio
async def test_owner_root_token_resolves_to_owner_panel():
    with _owner_router_env():
        _, kb = await resolve_navigation_payload(
            client=None,
            user_id=OWNER_ID,
            chat_type="private",
            return_to=TOKEN_OWNER_ROOT,
            lang="fa",
        )
    cbs = _kb_callbacks(kb)
    assert CB["OWN_STATS"] in cbs
    assert not any(cb.startswith("dev:") for cb in cbs)


@pytest.mark.asyncio
async def test_owner_home_token_resolves_to_owner_panel():
    # WZ_HOME resolves the role root, which for an owner is the owner panel.
    with _owner_router_env():
        _, kb = await resolve_navigation_payload(
            client=None,
            user_id=OWNER_ID,
            chat_type="private",
            return_to=None,  # None -> _safe_token -> role_root -> role-aware payload
            lang="fa",
        )
    cbs = _kb_callbacks(kb)
    assert CB["OWN_STATS"] in cbs
    assert CB["DEV_CAT_CREDIT"] not in cbs


@pytest.mark.asyncio
async def test_owner_cannot_resolve_developer_nav_token():
    """A forged dev token in an owner's back/home is denied, bouncing to owner root."""
    with _owner_router_env():
        _, kb = await resolve_navigation_payload(
            client=None,
            user_id=OWNER_ID,
            chat_type="private",
            return_to=TOKEN_DEV_CREDIT,
            lang="fa",
        )
    cbs = _kb_callbacks(kb)
    assert CB["OWN_STATS"] in cbs
    assert not any(cb.startswith("dev:") for cb in cbs)
    assert CB["DEV_CAT_CREDIT"] not in cbs
