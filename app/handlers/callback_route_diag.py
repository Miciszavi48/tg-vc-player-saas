"""Dispatcher diagnostics for high-risk visible callback families."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable

from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, Chat, Message, User

from app.handlers.priority import (
    BOT_DISABLED_CALLBACK_GROUP,
    CALLBACK_TRACE_GROUP,
    FALLBACK_CALLBACK_GROUP,
    GLOBAL_BAN_GROUP,
    LANG_BIND_GROUP,
    PYROMOD_CALLBACK_GROUP,
)

logger = logging.getLogger("callback_route_diag")

ANALYTICS_CALLBACK_SAMPLES: tuple[tuple[str, str], ...] = (
    ("an:home", "an:home"),
    ("an:yesterday", "an:yesterday"),
    ("an:7d", "an:7d"),
    ("an:14d", "an:14d"),
    ("an:30d", "an:30d"),
    ("an:peak", "an:peak"),
    ("an:errors", "an:errors"),
    ("an:drill:feature", "an:drill:feature"),
    ("an:drill:chattype", "an:drill:chattype"),
    ("an:drill:role", "an:drill:role"),
)

CALLSTATS_CALLBACK_SAMPLES: tuple[tuple[str, str], ...] = (
    ("grp_callstats", "grp:callstats:sel:all:all:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:all:week:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:all:today:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:vip:all:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:vip:week:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:vip:today:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:admin:all:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:admin:week:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:admin:today:-1003700458073:6909288370"),
)

STARTUP_CALLBACK_ROUTE_SAMPLES: tuple[tuple[str, str], ...] = (
    ("an:home", "an:home"),
    ("an:yesterday", "an:yesterday"),
    ("an:7d", "an:7d"),
    ("an:30d", "an:30d"),
    ("an:peak", "an:peak"),
    ("an:errors", "an:errors"),
    ("an:drill:feature", "an:drill:feature"),
    ("grp_callstats", "grp:callstats:sel:all:today:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:vip:week:-1003700458073:6909288370"),
    ("grp_callstats", "grp:callstats:sel:admin:all:-1003700458073:6909288370"),
    ("grp_callstats_close", "grp:callstats:close:-1003700458073:6909288370"),
    ("hlp:home", "hlp:home"),
    ("hlp:add", "hlp:add"),
    ("hlp:add:otp", "hlp:add:otp"),
    ("hlp:import", "hlp:import"),
    ("hlp:list", "hlp:list"),
    ("hlp:health", "hlp:health"),
    ("hlp:rotkey", "hlp:rotkey"),
    ("hlp:stats", "hlp:stats"),
    ("h:home", "h:home:U6909288370"),
    ("h:play", "h:play:U6909288370"),
    ("h:play:radio", "h:play:radio:U6909288370"),
    ("nav:back", "nav:back"),
    ("nav:start", "nav:start"),
    ("nav:close", "nav:close"),
    ("wz:home", "wz:home"),
    ("wz:back", "wz:back:dev_lists"),
    ("wz:cancel", "wz:cancel:dev_lists"),
    ("pb:pause", "pb:pause"),
    ("pb:fav:list", "pb:fav:list"),
    ("pb:type:radio", "pb:type:radio"),
    ("pb:type:satellite", "pb:type:satellite"),
    ("search:play", "search:play:dQw4w9WgXcQ"),
    ("bcw:start", "bcw:start"),
    ("bc:history", "bc:history"),
    ("dev:status", "dev:status"),
    ("dev:cat:lists", "dev:cat:lists"),
    ("own:stats", "own:stats"),
    ("sudo:stats", "sudo:stats"),
    ("fm:panel", "fm:panel"),
    ("postinst:panel", "postinst:panel"),
    ("dev_sudo_perm", "dev:sp:t:g:6909288370:1"),
    ("owner_sudo_perm", "own:sp:t:g:6909288370:1"),
    (
        "install_setup",
        "Add:Fa:ShowSetCharge:G-1003700458073:U6909288370:T1234567890",
    ),
)

NON_ROUTE_GROUPS = frozenset({
    LANG_BIND_GROUP,
    CALLBACK_TRACE_GROUP,
    PYROMOD_CALLBACK_GROUP,
    BOT_DISABLED_CALLBACK_GROUP,
    GLOBAL_BAN_GROUP,
    FALLBACK_CALLBACK_GROUP,
})


@dataclass(frozen=True)
class CallbackRouteCheck:
    label: str
    sample: str
    matched: bool
    group: int | None
    handler_name: str
    filter_repr: str
    module: str
    pattern: str
    reason: str


def all_callback_route_samples() -> tuple[tuple[str, str], ...]:
    return ANALYTICS_CALLBACK_SAMPLES + CALLSTATS_CALLBACK_SAMPLES


def _handler_callback(handler: CallbackQueryHandler) -> Any:
    return getattr(handler, "original_callback", handler.callback)


def _handler_name(handler: CallbackQueryHandler) -> str:
    cb = _handler_callback(handler)
    return getattr(cb, "__name__", getattr(cb, "__qualname__", "callback"))


def _handler_module(handler: CallbackQueryHandler) -> str:
    cb = _handler_callback(handler)
    return getattr(cb, "__module__", "")


_INTENTIONAL_REJECTION_HANDLERS = frozenset({"dev_removed_financial_surface"})


def _is_rejection_handler(handler: CallbackQueryHandler) -> bool:
    name = _handler_name(handler).lower()
    if name in _INTENTIONAL_REJECTION_HANDLERS:
        return False
    return (
        "unknown" in name
        or "malformed" in name
        or name.endswith("_invalid")
        or name.endswith("_fallback")
    )


def _regex_patterns(flt: Any) -> list[str]:
    patterns: list[str] = []
    seen: set[int] = set()

    def walk(node: Any) -> None:
        if node is None or id(node) in seen:
            return
        seen.add(id(node))
        pattern = getattr(getattr(node, "p", None), "pattern", None)
        if pattern is not None:
            patterns.append(str(pattern))
        for attr in ("base", "other"):
            if hasattr(node, attr):
                walk(getattr(node, attr))

    walk(flt)
    return patterns


def _filter_repr(flt: Any) -> str:
    patterns = _regex_patterns(flt)
    if patterns:
        return "RegexFilter(" + " & ".join(patterns) + ")"
    if flt is None:
        return "None"
    return type(flt).__name__


def _pattern_matches(pattern: str, sample: str) -> bool:
    try:
        return re.search(pattern, sample) is not None
    except re.error:
        return False


def _sample_family(sample: str) -> str:
    return sample.split(":", 1)[0] if ":" in sample else sample


def _handler_family(patterns: Iterable[str], module: str) -> str:
    joined = "\n".join(patterns)
    if "app.handlers.analytics_panel" == module or "an:" in joined:
        return "analytics"
    if "app.handlers.call_stats_panel" == module or "grp:callstats" in joined:
        return "callstats"
    return "unknown"


def _probe_user_id() -> int:
    try:
        from app.config.settings import settings

        return int(settings.DEVELOPER_ID or 6909288370)
    except Exception:
        return 6909288370


def _build_callback_query(sample: str) -> CallbackQuery:
    group_context = sample.startswith((
        "Add:",
        "grp:",
        "h:",
        "pb:",
        "search:",
    ))
    if group_context:
        chat_id = -1003700458073
        chat_type = ChatType.SUPERGROUP
    else:
        chat_id = _probe_user_id()
        chat_type = ChatType.PRIVATE
    chat = Chat(id=chat_id, type=chat_type)
    message = Message(id=406, chat=chat)
    return CallbackQuery(
        id="callback_route_diag_probe",
        from_user=User(id=_probe_user_id(), is_bot=False),
        chat_instance="callback_route_diag",
        message=message,
        data=sample,
    )


def _iter_callback_handlers(bot: Client) -> Iterable[tuple[int, CallbackQueryHandler]]:
    for group, handlers in sorted(bot.dispatcher.groups.items()):
        for handler in handlers:
            if isinstance(handler, CallbackQueryHandler):
                yield group, handler


async def check_callback_route(
    bot: Client,
    label: str,
    sample: str,
) -> CallbackRouteCheck:
    pattern_handlers: list[tuple[int, CallbackQueryHandler, list[str]]] = []
    denied_handlers: list[str] = []
    fallback_match: tuple[int, CallbackQueryHandler, str] | None = None

    for group, handler in _iter_callback_handlers(bot):
        patterns = _regex_patterns(handler.filters)
        matching_pattern = next((p for p in patterns if _pattern_matches(p, sample)), "")
        if not matching_pattern:
            continue
        pattern_handlers.append((group, handler, patterns))
        query = _build_callback_query(sample)
        try:
            matched = bool(await handler.check(bot, query))
        except Exception as exc:
            denied_handlers.append(f"{_handler_name(handler)}@{group}=error:{type(exc).__name__}")
            continue
        if not matched:
            denied_handlers.append(f"{_handler_name(handler)}@{group}=False")
            continue
        if group == FALLBACK_CALLBACK_GROUP:
            fallback_match = (group, handler, matching_pattern)
            continue
        if group in NON_ROUTE_GROUPS:
            denied_handlers.append(f"{_handler_name(handler)}@{group}=non_route_group")
            continue
        if _is_rejection_handler(handler):
            return CallbackRouteCheck(
                label=label,
                sample=sample,
                matched=False,
                group=group,
                handler_name=_handler_name(handler),
                filter_repr=_filter_repr(handler.filters),
                module=_handler_module(handler),
                pattern=matching_pattern,
                reason="rejection_handler",
            )
        return CallbackRouteCheck(
            label=label,
            sample=sample,
            matched=True,
            group=group,
            handler_name=_handler_name(handler),
            filter_repr=_filter_repr(handler.filters),
            module=_handler_module(handler),
            pattern=matching_pattern,
            reason="matched",
        )

    if not pattern_handlers:
        reason = "regex_mismatch"
    elif denied_handlers:
        reason = "filter_denied:" + ",".join(denied_handlers[:4])
    elif fallback_match is not None:
        reason = "handler_group_unreachable:fallback_only"
    else:
        reason = "no_registered_or_filter_denied"

    return CallbackRouteCheck(
        label=label,
        sample=sample,
        matched=False,
        group=None,
        handler_name="none",
        filter_repr="none",
        module="none",
        pattern="none",
        reason=reason,
    )


async def run_callback_route_checks(
    bot: Client,
    samples: Iterable[tuple[str, str]] | None = None,
) -> list[CallbackRouteCheck]:
    selected = tuple(samples or all_callback_route_samples())
    return [await check_callback_route(bot, label, sample) for label, sample in selected]


async def log_callback_route_checks(bot: Client) -> list[CallbackRouteCheck]:
    checks = await run_callback_route_checks(bot, STARTUP_CALLBACK_ROUTE_SAMPLES)
    for check in checks:
        log = logger.info if check.matched else logger.error
        log(
            "callback_diag.route_check label=%s sample=%s matched=%s group=%s "
            "handler=%s pattern=%s module=%s reason=%s",
            check.label,
            check.sample,
            check.matched,
            check.group,
            check.handler_name,
            check.pattern,
            check.module,
            check.reason,
        )
    return checks
