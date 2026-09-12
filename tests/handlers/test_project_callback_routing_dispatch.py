"""Project-wide dispatch-level callback routing regression suite.

Extends the helper-panel pattern (tests/test_helper_panel_callback_routing_dispatch.py)
to the whole project: registers every module in ``app.handlers._MODULES`` order,
then replays faithful Kurigram group-ordered dispatch with real filter
evaluation. Covers:

  * critical panel roots and family routes — fresh AND stale (``message=None``);
  * allowed and denied actors (explicit deny, never a silent skip);
  * group-scope permission feedback (``grp:`` family fallback);
  * dynamic parser round-trips (valid payload vs malformed variant);
  * intentionally obsolete surfaces and truly unknown callbacks;
  * keyboard completeness: every enforced visible callback has a dedicated
    non-fallback route;
  * cross-module duplicate-ownership (shadowing) guard.

Direct ``await handler(client, query)`` tests bypass filters/groups and cannot
catch routing regressions — this suite is the dispatch-level net.
"""
# ruff: noqa: E402  (env setup and the pyromod stub must precede app imports)
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-project-dispatch.db")
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

_SCRIPTS = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from pyrogram.enums import ChatType
from pyrogram.handlers import CallbackQueryHandler, MessageHandler, RawUpdateHandler
from pyrogram.types import CallbackQuery, Chat, Message, User

from app.config.settings import settings
from app.handlers import _MODULES
from app.handlers.callback_route_diag import (
    _handler_module,
    _handler_name,
    _is_rejection_handler,
    _pattern_matches,
    _regex_patterns,
)
from app.handlers.priority import (
    BOT_DISABLED_CALLBACK_GROUP,
    CALL_SECURITY_RAW_GROUP,
    CALLBACK_TRACE_GROUP,
    FALLBACK_CALLBACK_GROUP,
    GLOBAL_BAN_GROUP,
    HELPER_OTP_INPUT_GROUP,
    HELPER_PROXY_INPUT_GROUP,
    HELP_CALLBACK_GROUP,
    LANG_BIND_GROUP,
    PANEL_CALLBACK_GROUP,
    PYROMOD_CALLBACK_GROUP,
)

DEV = next(iter(settings.DEVELOPER_IDS))
OUTSIDER = 987_654_321
GROUP_ID = -1003700458073

GUARD_GROUPS = frozenset({
    LANG_BIND_GROUP,
    CALLBACK_TRACE_GROUP,
    PYROMOD_CALLBACK_GROUP,
    BOT_DISABLED_CALLBACK_GROUP,
    GLOBAL_BAN_GROUP,
})
TERMINAL_GROUPS = frozenset({HELP_CALLBACK_GROUP, PANEL_CALLBACK_GROUP})
UNKNOWN_FALLBACKS = frozenset({
    "unknown_callback_fallback",
    "known_prefix_unknown_callback_fallback",
})
# Handlers that self-suppress via the route_seen marker set by the safety
# wrapper when an earlier route-group handler already ran.
ROUTE_SEEN_SUPPRESSED = frozenset({
    "known_prefix_unknown_callback_fallback",
    "grp_permission_or_unknown_fallback",
})


class _RecorderBot:
    """Record callback, message, and raw registrations in dispatcher order."""

    def __init__(self) -> None:
        self.groups: dict[int, list] = {}

        class _Dispatcher:
            groups: dict = {}

        self.dispatcher = _Dispatcher()

    def on_callback_query(self, filters=None, group=0):  # noqa: ANN001
        def _decorator(fn):
            self.groups.setdefault(group, []).append(
                CallbackQueryHandler(fn, filters)
            )
            return fn

        return _decorator

    def on_raw_update(self, filters=None, group=0):  # noqa: ANN001
        def _decorator(fn):
            self.groups.setdefault(group, []).append(RawUpdateHandler(fn, filters))
            return fn

        return _decorator

    def on_message(self, filters=None, group=0):  # noqa: ANN001
        def _decorator(fn):
            self.groups.setdefault(group, []).append(MessageHandler(fn, filters))
            return fn

        return _decorator

    def __getattr__(self, name):  # tolerate on_message etc.
        if name.startswith("on_"):
            def _factory(*args, **kwargs):
                def _decorator(fn):
                    return fn

                return _decorator

            return _factory
        # Anything else must behave like a missing attribute: modules use
        # `getattr(bot, "_x_registered", False)` idempotency guards, and a
        # truthy placeholder would silently skip their whole registration.
        raise AttributeError(name)


@pytest.fixture(scope="module")
def bot() -> _RecorderBot:
    recorder = _RecorderBot()
    for module in _MODULES:
        module.register(recorder, None)
    return recorder


def _query(data: str, *, actor: int = DEV, stale: bool = False, context: str = "private") -> CallbackQuery:
    if stale:
        message = None
    else:
        if context == "group":
            chat = Chat(id=GROUP_ID, type=ChatType.SUPERGROUP)
        else:
            chat = Chat(id=actor, type=ChatType.PRIVATE)
        message = Message(id=406, chat=chat)
    return CallbackQuery(
        id="project_dispatch_probe",
        from_user=User(id=actor, is_bot=False),
        chat_instance="project_dispatch",
        message=message,
        data=data,
    )


async def _accepts(handler: CallbackQueryHandler, query: CallbackQuery) -> bool:
    try:
        return bool(await handler.check(None, query))
    except Exception:
        return False


def _message(text: str, *, actor: int = DEV) -> Message:
    return Message(
        id=407,
        chat=Chat(id=actor, type=ChatType.PRIVATE),
        from_user=User(id=actor, is_bot=False),
        text=text,
    )


async def resolve_matching_message_path(
    bot: _RecorderBot,
    text: str,
    *,
    actor: int = DEV,
) -> list[str]:
    """Replay matching and first-match-per-group breaks for a typed message.

    The path records every group claimant. A normal callback return advances to
    the next group; ContinuePropagation would scan the next handler in the same
    group, while StopPropagation would terminate the path. The proxy probe uses
    the normal-return path through inactive earlier FSM listeners.
    """
    message = _message(text, actor=actor)
    path: list[str] = []
    for group in sorted(bot.groups):
        for handler in bot.groups[group]:
            if isinstance(handler, RawUpdateHandler):
                if await handler.check(None, object()):
                    path.append(f"raw:{_handler_name(handler)}@{group}")
                    break
                continue
            if not isinstance(handler, MessageHandler):
                continue
            try:
                accepted = bool(await handler.check(None, message))
            except Exception:
                accepted = False
            if accepted:
                path.append(f"message:{_handler_name(handler)}@{group}")
                break
    return path


async def resolve_answering_handler(
    bot: _RecorderBot,
    data: str,
    *,
    actor: int = DEV,
    stale: bool = False,
    context: str = "private",
) -> str:
    """Faithful replay; returns the handler name that answers the user."""
    route_seen = False
    answering: str | None = None
    for group in sorted(bot.groups):
        for handler in bot.groups[group]:
            if isinstance(handler, RawUpdateHandler):
                if await handler.check(None, object()):
                    break
                continue
            if not isinstance(handler, CallbackQueryHandler):
                continue
            patterns = _regex_patterns(handler.filters)
            inverted = any(
                type(node).__name__ == "InvertFilter"
                for node in _walk_filters(handler.filters)
            )
            if patterns and not inverted and not any(
                _pattern_matches(p, data) for p in patterns
            ):
                continue
            if not await _accepts(handler, _query(data, actor=actor, stale=stale, context=context)):
                continue
            name = _handler_name(handler)
            if group in GUARD_GROUPS:
                break  # guard runs, dispatch proceeds to the next group
            if name in ROUTE_SEEN_SUPPRESSED and route_seen:
                break
            if answering is None:
                answering = name
            if group != FALLBACK_CALLBACK_GROUP:
                route_seen = True
            if group in TERMINAL_GROUPS:
                return answering
            break
    return answering or "NONE"


def _walk_filters(flt):
    seen: set[int] = set()
    stack = [flt]
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        yield node
        for attr in ("base", "other"):
            stack.append(getattr(node, attr, None))


# ── Critical routes: fresh / stale / denied ─────────────────────────────────
# (sample, context, expected_fresh, expected_stale, expected_denied)
# expected_* are sets of acceptable answering handlers; every entry must be an
# explicit route or explicit deny — never an unknown fallback, never NONE.
CRITICAL_ROUTES = [
    # panel roots repaired in this audit (identity filter + in-handler guard)
    ("fm:panel", "private", {"fm_panel"}, {"fm_panel"}, {"fm_panel"}),
    ("dev:banall:home", "private", {"dev_banall_home"}, {"dev_banall_home"}, {"dev_banall_home"}),
    ("dev:texts:home", "private", {"dev_texts_home"}, {"dev_texts_home"}, {"dev_texts_home"}),
    ("dev:texts_links", "private", {"dev_texts_home"}, {"dev_texts_home"}, {"dev_texts_home"}),
    ("own:texts:home", "private", {"own_texts_home"}, {"own_texts_home"}, {"own_texts_home"}),
    ("yts:home", "private", {"youtube_sessions_home"}, {"youtube_sessions_home"}, {"youtube_sessions_home"}),
    ("fct:home", "private", {"fast_creat_tokens_home"}, {"fast_creat_tokens_home"}, {"fast_creat_tokens_home"}),
    # helper panel (repaired 2026-07-17; dev shortcut owns hlp:home by design)
    ("hlp:home", "private", {"dev_shortcut_helper_home"}, {"dev_shortcut_helper_home"}, {"dev_shortcut_helper_home"}),
    ("hlp:add", "private", {"hlp_add"}, {"hlp_add"}, {"hlp_add"}),
    ("hlp:list", "private", {"hlp_list"}, {"hlp_list"}, {"hlp_list"}),
    ("hlp:health", "private", {"hlp_health"}, {"hlp_health"}, {"hlp_health"}),
    ("hlp:rotkey", "private", {"hlp_rotate_key"}, {"hlp_rotate_key"}, {"hlp_rotate_key"}),
    ("hlp:stats", "private", {"hlp_stats"}, {"hlp_stats"}, {"hlp_stats"}),
    # navigation (identity-filtered, role-free)
    ("nav:back", "private", {"nav_back"}, {"nav_back"}, {"nav_back"}),
    ("nav:start", "private", {"nav_start"}, {"nav_start"}, {"nav_start"}),
    # group panel: specific route for privileged actor; explicit family deny
    # for non-privileged members; stale answers explicitly too.
    (
        "grp:settings",
        "group",
        {"grp_settings"},
        {"grp_permission_or_unknown_fallback"},
        {"grp_permission_or_unknown_fallback"},
    ),
    (
        "grp:set:queue",
        "group",
        {"grp_toggle_setting"},
        {"grp_permission_or_unknown_fallback"},
        {"grp_permission_or_unknown_fallback"},
    ),
    (
        "pg:vip:1",
        "group",
        {"grp_vip_page"},
        {"grp_permission_or_unknown_fallback"},
        {"grp_permission_or_unknown_fallback"},
    ),
    # ROLE-04 VIP grant duration picker: identity-only filter, guard in-handler,
    # so fresh, stale and denied taps all reach the owning route.
    (
        "vipd:dp:12345:0:0",
        "group",
        {"vip_duration_picker"},
        {"vip_duration_picker"},
        {"vip_duration_picker"},
    ),
    (
        "vipd:ok:12345:3:2",
        "group",
        {"vip_duration_picker"},
        {"vip_duration_picker"},
        {"vip_duration_picker"},
    ),
    # MISC-07 equalizer preset picker: same route-first + in-handler guard shape.
    (
        "eq:set:bassboost",
        "group",
        {"equalizer_preset_choice"},
        {"equalizer_preset_choice"},
        {"equalizer_preset_choice"},
    ),
    # CONTENT-03/05 catalog taxonomy: the group step must out-rank the
    # ``pb:sat:``/``pb:radio:`` select handlers rather than be swallowed.
    (
        "pb:sat:g:movies",
        "group",
        {"on_satellite_group"},
        {"on_satellite_group"},
        {"on_satellite_group"},
    ),
    (
        "pb:sat:g:movies:p:2",
        "group",
        {"on_satellite_group"},
        {"on_satellite_group"},
        {"on_satellite_group"},
    ),
    (
        "pb:radio:c:ir",
        "group",
        {"on_radio_group"},
        {"on_radio_group"},
        {"on_radio_group"},
    ),
    # PANEL-02: support links are scope-filtered only (group_chat_filter), so any
    # group member reaches them — same design as their sibling support buttons.
    # A stale tap has no chat, so it falls to the explicit family fallback.
    (
        "grp:support:bot_channel",
        "group",
        {"grp_bot_channel"},
        {"grp_permission_or_unknown_fallback"},
        {"grp_bot_channel"},
    ),
    (
        "grp:support:messenger",
        "group",
        {"grp_messenger"},
        {"grp_permission_or_unknown_fallback"},
        {"grp_messenger"},
    ),
    # PANEL-05 user-panel actions: identity filter, guard re-checked in-handler,
    # so fresh, stale and denied taps all reach the owning route.
    (
        "up:promote:12345",
        "group",
        {"user_panel_action"},
        {"user_panel_action"},
        {"user_panel_action"},
    ),
    (
        "up:ban:12345",
        "group",
        {"user_panel_action"},
        {"user_panel_action"},
        {"user_panel_action"},
    ),
]


def test_filterless_raw_observer_is_isolated_from_group_zero(bot: _RecorderBot):
    raw_groups = {
        group
        for group, handlers in bot.groups.items()
        if any(isinstance(handler, RawUpdateHandler) for handler in handlers)
    }
    assert raw_groups == {CALL_SECURITY_RAW_GROUP}
    assert CALL_SECURITY_RAW_GROUP != 0


async def test_raw_observer_cannot_block_helper_proxy_typed_message(bot: _RecorderBot):
    path = await resolve_matching_message_path(
        bot,
        "socks5://host.example:1080",
    )

    raw = f"raw:call_security_raw_handler@{CALL_SECURITY_RAW_GROUP}"
    otp = f"message:otp_text_handler@{HELPER_OTP_INPUT_GROUP}"
    proxy = f"message:hlp_proxy_input@{HELPER_PROXY_INPUT_GROUP}"
    assert raw in path
    assert otp in path
    assert proxy in path
    assert path.index(raw) < path.index(otp) < path.index(proxy)
    assert HELPER_OTP_INPUT_GROUP != HELPER_PROXY_INPUT_GROUP


@pytest.mark.parametrize(
    "sample,context,expected,_stale_expected,_denied_expected",
    CRITICAL_ROUTES,
    ids=[row[0] for row in CRITICAL_ROUTES],
)
async def test_critical_route_fresh_allowed(bot, sample, context, expected, _stale_expected, _denied_expected):
    answering = await resolve_answering_handler(bot, sample, context=context)
    assert answering in expected, f"{sample} fresh -> {answering}, expected {expected}"
    assert answering not in UNKNOWN_FALLBACKS


@pytest.mark.parametrize(
    "sample,context,_expected,stale_expected,_denied_expected",
    CRITICAL_ROUTES,
    ids=[row[0] for row in CRITICAL_ROUTES],
)
async def test_critical_route_stale_answers_explicitly(bot, sample, context, _expected, stale_expected, _denied_expected):
    answering = await resolve_answering_handler(bot, sample, stale=True, context=context)
    assert answering in stale_expected, (
        f"{sample} stale -> {answering}, expected {stale_expected}"
    )
    assert answering != "NONE", f"{sample} stale tap would hang the spinner"
    assert answering not in UNKNOWN_FALLBACKS


@pytest.mark.parametrize(
    "sample,context,_expected,_stale_expected,denied_expected",
    CRITICAL_ROUTES,
    ids=[row[0] for row in CRITICAL_ROUTES],
)
async def test_critical_route_denied_actor_gets_explicit_feedback(bot, sample, context, _expected, _stale_expected, denied_expected):
    answering = await resolve_answering_handler(bot, sample, actor=OUTSIDER, context=context)
    assert answering in denied_expected, (
        f"{sample} denied actor -> {answering}, expected {denied_expected}"
    )
    assert answering != "NONE"
    assert answering not in UNKNOWN_FALLBACKS, (
        f"{sample}: permission denial must not surface as unknown_callback"
    )


# ── Dynamic parser round-trips ──────────────────────────────────────────────


async def test_parser_round_trip_helper_rotate_confirm(bot):
    valid = await resolve_answering_handler(bot, f"hlp:rotkey:confirm:{DEV}:1234567890")
    malformed = await resolve_answering_handler(bot, "hlp:rotkey:confirm:garbage")
    assert valid == "hlp_rotate_key_confirm"
    assert malformed == "hlp_rotate_key_malformed"


async def test_parser_round_trip_callstats_selection(bot):
    valid = await resolve_answering_handler(
        bot, f"grp:callstats:sel:all:week:{GROUP_ID}:{DEV}", context="group"
    )
    assert valid == "call_stats_select"


async def test_parser_round_trip_yts_detail(bot):
    valid = await resolve_answering_handler(bot, "yts:detail:7:0")
    malformed = await resolve_answering_handler(bot, "yts:detail:not-a-number")
    assert valid == "youtube_sessions_detail"
    # yts: is a known family: malformed payloads land on the known-prefix
    # fallback (route_seen unset), never on the generic unknown handler.
    assert malformed == "known_prefix_unknown_callback_fallback"


# ── Fallback integrity ──────────────────────────────────────────────────────


async def test_truly_unknown_callback_reaches_unknown_fallback(bot):
    answering = await resolve_answering_handler(bot, "totally:bogus:callback")
    assert answering == "unknown_callback_fallback"


async def test_obsolete_financial_surface_owns_its_callbacks(bot):
    for sample in ("dev:rate:base", "dev:invoice", "dev:monthly_invoice"):
        answering = await resolve_answering_handler(bot, sample)
        assert answering == "dev_removed_financial_surface", (
            f"{sample} -> {answering}: removed surface must pre-empt retained handlers"
        )


async def test_group_family_fallback_defers_to_specific_routes(bot):
    """The grp family fallback must never intercept a healthy specific route."""
    answering = await resolve_answering_handler(bot, "grp:settings", context="group")
    assert answering == "grp_settings"


# ── Keyboard completeness: every visible callback has a dedicated route ─────


def _route_pattern_index(bot: _RecorderBot):
    index = []  # (name, module, group, patterns)
    for group in sorted(bot.groups):
        if group in GUARD_GROUPS or group == FALLBACK_CALLBACK_GROUP:
            continue
        for handler in bot.groups[group]:
            if not isinstance(handler, CallbackQueryHandler):
                continue
            if _is_rejection_handler(handler):
                continue
            patterns = _regex_patterns(handler.filters)
            if patterns:
                index.append(
                    (_handler_name(handler), _handler_module(handler), group, patterns)
                )
    return index


def test_every_enforced_keyboard_callback_has_a_dedicated_route(bot):
    from verify_callback_routing_coverage import build_samples

    index = _route_pattern_index(bot)
    samples, _unresolved = build_samples()
    missing = []
    for sample in samples:
        if not sample.enforce or sample.inert:
            continue
        if not any(
            any(_pattern_matches(p, sample.sample) for p in patterns)
            for _name, _module, _group, patterns in index
        ):
            missing.append(f"{sample.sample} ({sample.source})")
    assert not missing, (
        "keyboard callbacks with no registered non-fallback route: "
        + ", ".join(missing[:20])
    )


# ── Cross-module duplicate ownership (shadowing) guard ──────────────────────

# Exact-identity handlers registered in more than one module on purpose:
# - dev-panel shortcuts reuse the canonical panels' shared render helpers;
# - nav:back / wz:home are scope-split: group_panel owns group-chat taps at
#   group -850 (filter `_grp_any`), callbacks owns private/stale taps at
#   group 0 — the fall-through lands on a real route, never a fallback.
EXPECTED_CROSS_MODULE_OWNERS = {
    "hlp:home": {"app.handlers.dev_panel", "app.handlers.helper_panel"},
    "an:home": {"app.handlers.dev_panel", "app.handlers.analytics_panel"},
    "h:about": {"app.handlers.dev_panel", "app.handlers.help_center"},
    "bcw:start": {"app.handlers.dev_panel", "app.handlers.broadcast_wizard"},
    "bc:history": {"app.handlers.dev_panel", "app.handlers.broadcast_panel"},
    "nav:back": {"app.handlers.callbacks", "app.handlers.group_panel"},
    "wz:home": {"app.handlers.callbacks", "app.handlers.group_panel"},
}


def test_no_undocumented_cross_module_exact_identity_owners(bot):
    """Two modules registering the same anchored exact identity means the later
    module is silently shadowed (the 2026-07-17 hlp:home bug class)."""
    exact_owners: dict[str, set[str]] = {}
    for name, module, _group, patterns in _route_pattern_index(bot):
        for pattern in patterns:
            if pattern.startswith("^") and pattern.endswith("$"):
                body = pattern[1:-1]
                if any(ch in body for ch in "\\[](?*+|"):
                    continue  # dynamic pattern, not an exact identity
                exact_owners.setdefault(body, set()).add(module)
    violations = {
        identity: sorted(modules)
        for identity, modules in exact_owners.items()
        if len(modules) > 1 and modules != EXPECTED_CROSS_MODULE_OWNERS.get(identity)
    }
    assert not violations, f"undocumented cross-module identity owners: {violations}"
