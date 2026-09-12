"""Dispatch-level routing regression tests for Helper panel callbacks.

Unlike the sibling helper-panel tests, these do NOT call handlers directly by
name. They register the real modules, capture each handler's (group, filters),
and replay Pyrogram/Kurigram's dispatch loop with real filter evaluation:

    - groups are processed in ascending order
    - within a group the first handler whose filter accepts wins, then dispatch
      breaks to the next group (Pyrogram `break`)
    - terminal groups (HELP/PANEL) StopPropagation after running
    - once any route handler runs, the group-1000 known-prefix fallback is
      suppressed (mirrors the `_musicbot_callback_route_seen` guard)

This is the only layer that can catch a producer/consumer or registration-order
regression, e.g. a filter-level `_pm_dev` guard causing a valid tap to fall
through to `hlp_unknown_or_denied` / `unknown_callback`. The `stale` variant
(``query.message is None`` — an old/edited panel, the "please open the menu
again" case) is what makes a filter-level scope guard evaluate False.
"""
from __future__ import annotations

import os
import sys
from collections import OrderedDict
from types import ModuleType

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-hlp-dispatch.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:  # match sibling suites' lightweight stub
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from pyrogram.enums import ChatType
from pyrogram.types import CallbackQuery, Chat, Message, User

from app.config.settings import settings
from app.handlers import callbacks, dev_panel, helper_panel
from app.handlers.priority import (
    FALLBACK_CALLBACK_GROUP,
    HELP_CALLBACK_GROUP,
    PANEL_CALLBACK_GROUP,
)

DEV = next(iter(settings.DEVELOPER_IDS))
TERMINAL_GROUPS = {HELP_CALLBACK_GROUP, PANEL_CALLBACK_GROUP}

# Handlers that produce the "unknown button" reply rather than a real route.
FALLBACK_HANDLERS = {
    "hlp_unknown_or_denied",
    "unknown_callback_fallback",
    "known_prefix_unknown_callback_fallback",
}
# Pass-through guards that continue_propagation instead of answering.
GUARD_HANDLERS = {
    "_bind_lang_callback",
    "_trace_callback_received",
    "global_ban_callback_guard",
    "_bot_operational_callback_guard",
}


class _RecorderBot:
    """Captures on_callback_query registrations with their group + filters."""

    def __init__(self) -> None:
        self.groups: "OrderedDict[int, list]" = OrderedDict()

    def on_callback_query(self, filters=None, group=0):  # noqa: ANN001
        def _decorator(fn):
            self.groups.setdefault(group, []).append((fn.__name__, filters))
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator


def _build_bot() -> _RecorderBot:
    bot = _RecorderBot()
    # Registration order mirrors app.handlers._MODULES: callbacks (fallbacks +
    # nav_back) and dev_panel (hlp:home shortcut) register before helper_panel.
    callbacks.register(bot, None)
    dev_panel.register(bot, None)
    helper_panel.register(bot, None)
    return bot


def _query(data: str, *, stale: bool = False, private: bool = True) -> CallbackQuery:
    if stale:
        message = None
    else:
        chat_type = ChatType.PRIVATE if private else ChatType.SUPERGROUP
        chat_id = DEV if private else -1001234567890
        message = Message(id=1, chat=Chat(id=chat_id, type=chat_type))
    return CallbackQuery(
        id="probe",
        from_user=User(id=DEV, is_bot=False),
        chat_instance="probe",
        message=message,
        data=data,
    )


async def _accepts(flt, query) -> bool:
    if flt is None:
        return True
    try:
        return bool(await flt(None, query))
    except Exception:
        return False


async def _resolve_answering_handler(bot: _RecorderBot, data: str, *, stale=False) -> str:
    """Replay dispatch and return the handler that ANSWERS the user."""
    route_seen = False
    answering: str | None = None
    for group in sorted(bot.groups):
        for name, flt in bot.groups[group]:
            if not await _accepts(flt, _query(data, stale=stale)):
                continue
            if name in GUARD_HANDLERS:
                break  # guard runs then continues to the next group
            # group-1000 known-prefix fallback yields once a route already ran
            if (
                group == FALLBACK_CALLBACK_GROUP
                and name == "known_prefix_unknown_callback_fallback"
                and route_seen
            ):
                break
            if answering is None:
                answering = name
            if group not in {FALLBACK_CALLBACK_GROUP}:
                route_seen = True
            if group in TERMINAL_GROUPS:
                return answering
            break  # first match in group -> next group
    return answering or "NONE"


# Intended consumer for every helper-home button + the working controls.
EXPECTED_ROUTE = {
    "hlp:home": {"dev_shortcut_helper_home", "hlp_home"},
    "hlp:list": {"hlp_list"},
    "hlp:health": {"hlp_health"},
    "hlp:rotkey": {"hlp_rotate_key"},
    "hlp:stats": {"hlp_stats"},
    "hlp:add": {"hlp_add"},
    "nav:back": {"nav_back"},
}


@pytest.mark.parametrize("data,expected", sorted(EXPECTED_ROUTE.items()))
def test_helper_callbacks_route_to_intended_handler_fresh(data, expected):
    bot = _build_bot()
    import asyncio

    answering = asyncio.get_event_loop().run_until_complete(
        _resolve_answering_handler(bot, data)
    )
    assert answering in expected, f"{data} -> {answering}, expected one of {expected}"
    assert answering not in FALLBACK_HANDLERS, f"{data} fell through to {answering}"


@pytest.mark.parametrize(
    "data,expected",
    sorted({k: v for k, v in EXPECTED_ROUTE.items() if k.startswith("hlp:")}.items()),
)
def test_helper_callbacks_do_not_fall_through_on_stale_message(data, expected):
    """Regression: a stale panel (message=None) made filter-level `_pm_dev`
    deny, skipping the handler and leaking the tap to `unknown_callback`.
    Identity-only filters must still reach the intended handler."""
    bot = _build_bot()
    import asyncio

    answering = asyncio.get_event_loop().run_until_complete(
        _resolve_answering_handler(bot, data, stale=True)
    )
    assert answering not in FALLBACK_HANDLERS, (
        f"{data} fell through to {answering} on a stale message"
    )
    assert answering in expected, f"{data} -> {answering}, expected one of {expected}"


def test_truly_unknown_callback_still_reaches_unknown_fallback():
    bot = _build_bot()
    import asyncio

    answering = asyncio.get_event_loop().run_until_complete(
        _resolve_answering_handler(bot, "totally:bogus:callback")
    )
    assert answering == "unknown_callback_fallback"


def test_helper_home_visible_routes_have_no_filter_level_pm_dev():
    """Guard the fix: none of the five visible helper-home routes may re-introduce
    a decorator/filter-level `_pm_dev`; permission is enforced in-handler."""
    from app.utils.filters import dev_filter

    dev_name = getattr(dev_filter(), "name", "DevFilter")

    def _mentions_dev_filter(flt) -> bool:
        seen: set[int] = set()

        def walk(node) -> bool:
            if node is None or id(node) in seen:
                return False
            seen.add(id(node))
            if getattr(node, "name", None) == dev_name:
                return True
            return any(walk(getattr(node, a, None)) for a in ("base", "other"))

        return walk(flt)

    bot = _build_bot()
    handlers = {name: flt for hs in bot.groups.values() for name, flt in hs}
    for name in ("hlp_home", "hlp_list", "hlp_health", "hlp_rotate_key", "hlp_stats",
                 "hlp_add", "dev_shortcut_helper_home"):
        assert not _mentions_dev_filter(handlers[name]), (
            f"{name} must not guard dev at the filter level (route-first + in-handler guard)"
        )
