"""Shared Pyrogram handler group priorities for routing order."""

from __future__ import annotations

# Language binding and bot-disabled callback guard.
LANG_BIND_GROUP = -1000
# Global ban guard runs before pyromod listen (-980) and callback trace (-990).
GLOBAL_BAN_GROUP = -995
CALLBACK_TRACE_GROUP = -990
# pyromod listen handler (kurigram); excluded from mark_route_seen.
PYROMOD_CALLBACK_GROUP = -980
BOT_DISABLED_CALLBACK_GROUP = -950

# Retired Developer financial callbacks must pre-empt their retained group-0
# implementations so old buttons cannot reopen removed payment surfaces.
REMOVED_FINANCIAL_CALLBACK_GROUP = -940

# Filterless RawUpdateHandler observers match every raw Telegram update. Keep
# them out of group 0 so they cannot trigger Kurigram's per-group break before
# later typed callback/message handlers are evaluated.
CALL_SECURITY_RAW_GROUP = -900

# Group-scoped navigation overrides run before private/group-0 navigation.
GROUP_NAV_CALLBACK_GROUP = -850

# Help Center user-bound callbacks — before generic group 0 panel handlers.
HELP_CALLBACK_GROUP = -845

# Visible panel callbacks that must route before generic group-0 handlers.
PANEL_CALLBACK_GROUP = -840

# Private command and FSM listeners. Overlapping listener families use
# adjacent groups because Kurigram evaluates only the first match in a group.
PRIVATE_COMMAND_GROUP = -100
HELPER_OTP_INPUT_GROUP = -90
HELPER_PROXY_INPUT_GROUP = -89
BROADCAST_SCHEDULE_INPUT_GROUP = -85
BROADCAST_PAYLOAD_INPUT_GROUP = -80

# High-priority text/slash commands (help, charge, panel, play) — after guards,
# before generic group-0 feature handlers.
PRIORITY_COMMAND_GROUP = -15

# Early global guards (blacklist, force-join, membership tracking).
GUARD_GROUP = -10

# Filter-words auto-delete watcher (must continue_propagation on non-delete paths).
FILTER_WORDS_GROUP = -5

# Default feature handlers. Callback and message handlers share the numeric
# group but remain type-scoped by Kurigram's parser selection.
DEFAULT_HANDLER_GROUP = 0

# Manual safe_ask fallback listener. It is registered only for the lifetime of
# one prompt and must be removed by exact Handler-object identity.
SAFE_ASK_LISTENER_GROUP = 99

# Family-level explicit feedback for group-visible callbacks whose specific
# routes keep deny-capable admin filters (grp:*): a denied member's tap must
# get an explicit no-access alert, not the generic unknown_callback. Runs after
# every specific route group, before the global fallbacks; the handler is
# suppressed via the route_seen marker when a specific route already ran.
GROUP_FAMILY_FALLBACK_GROUP = 900

# Unknown / fallback callbacks (lowest priority = runs last within dispatcher).
FALLBACK_CALLBACK_GROUP = 1000


# Canonical topology used by the repository-wide handler inventory. Every
# intentional non-default group belongs here; values describe allowed handler
# classes and the propagation contract that makes the occupancy safe.
HANDLER_GROUP_TOPOLOGY: dict[int, dict[str, object]] = {
    LANG_BIND_GROUP: {
        "names": ["LANG_BIND_GROUP"],
        "handler_types": ["CallbackQueryHandler", "MessageHandler"],
        "purpose": "bind language context before guards and routes",
        "propagation": "normal return; dispatcher continues with the next group",
    },
    GLOBAL_BAN_GROUP: {
        "names": ["GLOBAL_BAN_GROUP"],
        "handler_types": ["CallbackQueryHandler", "MessageHandler"],
        "purpose": "block globally banned actors before all feature routes",
        "propagation": "conditional StopPropagation; normal return when allowed",
    },
    CALLBACK_TRACE_GROUP: {
        "names": ["CALLBACK_TRACE_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "observe and trace callbacks before listener interception",
        "propagation": "normal return; dispatcher continues with the next group",
    },
    PYROMOD_CALLBACK_GROUP: {
        "names": ["PYROMOD_CALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler", "MessageHandler"],
        "purpose": "reserved for dynamic Pyromod ask/listen interception",
        "propagation": "listener-defined; stale listeners are cleared before this group",
        "dynamic": True,
    },
    BOT_DISABLED_CALLBACK_GROUP: {
        "names": ["BOT_DISABLED_CALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "reject non-developer callbacks while the bot is disabled",
        "propagation": "conditional StopPropagation; normal return when allowed",
    },
    REMOVED_FINANCIAL_CALLBACK_GROUP: {
        "names": ["REMOVED_FINANCIAL_CALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "terminate callbacks for removed financial surfaces",
        "propagation": "terminal StopPropagation",
    },
    CALL_SECURITY_RAW_GROUP: {
        "names": ["CALL_SECURITY_RAW_GROUP"],
        "handler_types": ["RawUpdateHandler"],
        "purpose": "observe group-call raw updates without occupying a typed-handler group",
        "propagation": "normal return; dispatcher continues with the next group",
    },
    GROUP_NAV_CALLBACK_GROUP: {
        "names": ["GROUP_NAV_CALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "group-chat navigation overrides",
        "propagation": "terminal StopPropagation after handling",
    },
    HELP_CALLBACK_GROUP: {
        "names": ["HELP_CALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "user-bound Help Center routes",
        "propagation": "terminal StopPropagation via callback wrapper",
    },
    PANEL_CALLBACK_GROUP: {
        "names": ["PANEL_CALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "high-priority visible panel routes",
        "propagation": "terminal StopPropagation via callback wrapper",
    },
    PRIVATE_COMMAND_GROUP: {
        "names": ["PRIVATE_COMMAND_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "private start and cancel commands",
        "propagation": "terminal StopPropagation after handling",
    },
    HELPER_OTP_INPUT_GROUP: {
        "names": ["HELPER_OTP_INPUT_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "helper OTP and contact FSM input",
        "propagation": "conditional StopPropagation when the OTP FSM consumes input",
    },
    HELPER_PROXY_INPUT_GROUP: {
        "names": ["HELPER_PROXY_INPUT_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "helper proxy FSM input after OTP listeners decline it",
        "propagation": "conditional StopPropagation when the proxy FSM consumes input",
    },
    BROADCAST_SCHEDULE_INPUT_GROUP: {
        "names": ["BROADCAST_SCHEDULE_INPUT_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "broadcast schedule text input",
        "propagation": "conditional StopPropagation when schedule input is consumed",
    },
    BROADCAST_PAYLOAD_INPUT_GROUP: {
        "names": ["BROADCAST_PAYLOAD_INPUT_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "broadcast payload capture after schedule listeners decline it",
        "propagation": "conditional StopPropagation when payload input is consumed",
    },
    PRIORITY_COMMAND_GROUP: {
        "names": ["PRIORITY_COMMAND_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "exact high-priority group and management commands",
        "propagation": "first matching command wins; filters must be disjoint",
    },
    GUARD_GROUP: {
        "names": ["GUARD_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "group blacklist, bot-state, membership and force-join guard",
        "propagation": "explicit ContinuePropagation on pass; StopPropagation on deny",
    },
    FILTER_WORDS_GROUP: {
        "names": ["FILTER_WORDS_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "filter-word and service-message deletion observers (disjoint filters: text vs service events)",
        "propagation": "explicit ContinuePropagation unless the message is deleted",
    },
    DEFAULT_HANDLER_GROUP: {
        "names": ["DEFAULT_HANDLER_GROUP"],
        "handler_types": [
            "CallbackQueryHandler",
            "ChatMemberUpdatedHandler",
            "MessageHandler",
        ],
        "purpose": "default feature handlers with type-specific and route-specific filters",
        "propagation": "first matching handler per update type wins in this group",
    },
    SAFE_ASK_LISTENER_GROUP: {
        "names": ["SAFE_ASK_LISTENER_GROUP"],
        "handler_types": ["MessageHandler"],
        "purpose": "temporary manual safe_ask response listener",
        "propagation": "normal return; exact Handler object removed in finally",
        "dynamic": True,
    },
    GROUP_FAMILY_FALLBACK_GROUP: {
        "names": ["GROUP_FAMILY_FALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "explicit denied or malformed feedback for group callback families",
        "propagation": "route_seen-suppressed fallback",
    },
    FALLBACK_CALLBACK_GROUP: {
        "names": ["FALLBACK_CALLBACK_GROUP"],
        "handler_types": ["CallbackQueryHandler"],
        "purpose": "known-prefix and unknown callback safety fallbacks",
        "propagation": "terminal fallback; known-prefix route_seen suppression",
    },
}
