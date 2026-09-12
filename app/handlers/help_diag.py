"""Startup and runtime diagnostics for Help Center callback routing."""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from typing import Any

from pyrogram import Client
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, Message, User, Chat

from app.handlers.help_center import (
    HELP_CALLBACK_PATTERN,
    HELP_ROUTE_SPECS,
    _HELP_CALLBACK_PATTERN_RE,
    _find_help_callback_handlers,
    parse_help_callback,
)
from app.handlers.priority import HELP_CALLBACK_GROUP
from app.utils.ui import CB

logger = logging.getLogger("help_diag")

_HELP_SAMPLE_CALLBACKS = (
    ("h:play", f"{CB['HELP_PLAYBACK']}:U123456789"),
    ("h:promote", f"{CB['HELP_PROMOTE']}:U123456789"),
    ("h:public", f"{CB['HELP_PUBLIC']}:U123456789"),
    ("h:close", f"{CB['HELP_CLOSE']}:U123456789"),
)

_WORKING_SAMPLE_CALLBACKS = (
    ("grp:settings", CB["GRP_SETTINGS"]),
    ("grp:help", CB["GRP_HELP"]),
    ("nav:close", CB["NAV_CLOSE"]),
    ("nav:start", CB["NAV_START"]),
)


def _build_kurigram_callback_query(
    data: str,
    *,
    user_id: int = 123456789,
    chat_id: int = -1003740677405,
    message_id: int = 406,
) -> CallbackQuery:
    """Build a Kurigram ``CallbackQuery`` for dispatcher ``Handler.check`` probes."""
    from pyrogram.enums import ChatType

    chat = Chat(id=chat_id, type=ChatType.SUPERGROUP)
    message = Message(id=message_id, chat=chat)
    return CallbackQuery(
        id="help_diag_probe",
        from_user=User(id=user_id, is_bot=False),
        chat_instance="help_diag",
        message=message,
        data=data,
    )


def log_register_all_help_modules(modules: list[Any]) -> None:
    """Log which registration modules include Help Center."""
    names = [getattr(m, "__name__", str(m)) for m in modules]
    logger.info(
        "help_diag.register_all.enter modules=%s help_center_included=%s",
        ",".join(names),
        "app.handlers.help_center" in names,
    )


def log_help_center_register_called() -> None:
    """Log that ``help_center.register`` executed."""
    logger.info("help_diag.register_help_center.called")


def log_help_handlers_added(count: int) -> None:
    """Log how many explicit Help route handlers were registered."""
    logger.info(
        "help_diag.handlers_added count=%s group=%s combined_pattern=%s",
        count,
        HELP_CALLBACK_GROUP,
        HELP_CALLBACK_PATTERN,
    )
    for route_name, pattern in HELP_ROUTE_SPECS:
        logger.info(
            "help_diag.handler_added name=%s group=%s pattern=%s",
            route_name,
            HELP_CALLBACK_GROUP,
            pattern,
        )


def scan_dispatcher_help_handlers(bot: Client) -> list[tuple[int, str, Any]]:
    """Return ``(group, route_name, handler)`` tuples for Help routes."""
    found: list[tuple[int, str, Any]] = []
    for group, handlers in sorted(bot.dispatcher.groups.items()):
        for handler in handlers:
            if not isinstance(handler, CallbackQueryHandler):
                continue
            orig = getattr(handler, "original_callback", handler.callback)
            name = getattr(orig, "__name__", "")
            if name.startswith("help_route_"):
                found.append((group, name, handler))
    return found


def log_dispatcher_scan(bot: Client) -> None:
    """Log every Help route handler present in the dispatcher."""
    found = scan_dispatcher_help_handlers(bot)
    if not found:
        logger.error("help_diag.dispatcher_scan result=missing help_route handlers=0")
        return
    for group, name, handler in found:
        flt = getattr(handler, "filters", None)
        logger.info(
            "help_diag.dispatcher_scan group=%s handler=%s filter=%r",
            group,
            name,
            flt,
        )


async def _probe_handler_check(
    bot: Client,
    handler: CallbackQueryHandler,
    data: str,
    *,
    user_id: int = 123456789,
) -> bool:
    query = _build_kurigram_callback_query(data, user_id=user_id)
    try:
        return bool(await handler.check(bot, query))
    except Exception as exc:
        logger.error(
            "help_diag.check sample=%s handler=%s result=error exception_class=%s",
            data,
            getattr(getattr(handler, "original_callback", handler.callback), "__name__", "?"),
            type(exc).__name__,
        )
        return False


async def run_help_check_probes(bot: Client) -> dict[str, bool]:
    """Run ``Handler.check`` probes for Help and working reference callbacks."""
    results: dict[str, bool] = {}
    help_handlers = _find_help_callback_handlers(bot)
    if not help_handlers:
        for label, data in _HELP_SAMPLE_CALLBACKS:
            results[data] = False
            logger.error("help_diag.check sample=%s label=%s result=False reason=no_help_handler", data, label)
        return results

    for label, data in _HELP_SAMPLE_CALLBACKS:
        matched = False
        for _group, _name, handler in help_handlers:
            ok = await _probe_handler_check(bot, handler, data)
            if ok:
                matched = True
                logger.info("help_diag.check sample=%s label=%s handler=%s result=True", data, label, _name)
                break
        if not matched:
            py_re = bool(_HELP_CALLBACK_PATTERN_RE.match(data))
            parsed = parse_help_callback(data) is not None
            logger.error(
                "help_diag.check sample=%s label=%s result=False py_regex=%s parse_help=%s",
                data,
                label,
                py_re,
                parsed,
            )
        results[data] = matched

    for label, data in _WORKING_SAMPLE_CALLBACKS:
        ok_any = False
        for group, handlers in bot.dispatcher.groups.items():
            for handler in handlers:
                if not isinstance(handler, CallbackQueryHandler):
                    continue
                orig = getattr(handler, "original_callback", handler.callback)
                name = getattr(orig, "__name__", "")
                if label == "grp:help" and name != "grp_help":
                    continue
                if label == "grp:settings" and name != "grp_settings":
                    continue
                if label == "nav:close" and name != "nav_close":
                    continue
                ok = await _probe_handler_check(bot, handler, data)
                logger.info(
                    "help_diag.check sample=%s label=%s handler=%s group=%s result=%s",
                    data,
                    label,
                    name,
                    group,
                    ok,
                )
                ok_any = ok
                break
            if ok_any:
                break
        results[data] = ok_any

    return results


async def verify_help_routing(bot: Client) -> bool:
    """Full post-start verification with Kurigram ``CallbackQuery`` probes."""
    log_dispatcher_scan(bot)
    results = await run_help_check_probes(bot)
    help_ok = all(results.get(data, False) for _label, data in _HELP_SAMPLE_CALLBACKS)
    if help_ok:
        logger.info(
            "help_diag.verify result=True help_handlers=%s group=%s pattern=%s",
            len(_find_help_callback_handlers(bot)),
            HELP_CALLBACK_GROUP,
            HELP_CALLBACK_PATTERN,
        )
    else:
        logger.error(
            "help_diag.verify result=False probes=%s handlers=%s group=%s",
            {k: v for k, v in results.items() if k.startswith("h:")},
            len(_find_help_callback_handlers(bot)),
            HELP_CALLBACK_GROUP,
        )
    return help_ok


def log_callback_diag_received(query: CallbackQuery, *, stage: str) -> None:
    """Log structured routing context for Help-related prefixes."""
    data = str(getattr(query, "data", "") or "")
    prefix = data.split(":", 1)[0] if data else ""
    if prefix not in {"h", "hlp", "grp", "nav"}:
        return
    py_match = bool(_HELP_CALLBACK_PATTERN_RE.match(data)) if prefix == "h" else None
    parsed = parse_help_callback(data) if prefix == "h" else None
    logger.info(
        "callback_diag.received data=%s prefix=%s stage=%s py_regex=%s parse_help=%s route_seen=%s",
        data[:64],
        prefix,
        stage,
        py_match,
        parsed is not None if prefix == "h" else None,
        getattr(query, "_musicbot_callback_route_seen", False),
    )


async def log_help_fallback_decision(
    bot: Client,
    query: CallbackQuery,
    *,
    reason: str,
) -> None:
    """Dump Help routing evidence when a known ``h:`` callback hits fallback."""
    data = str(getattr(query, "data", "") or "")
    if not data.startswith("h:"):
        return

    py_match = bool(_HELP_CALLBACK_PATTERN_RE.match(data))
    parsed = parse_help_callback(data)
    handlers = _find_help_callback_handlers(bot)
    check_results: list[str] = []
    probe = _build_kurigram_callback_query(
        data,
        user_id=getattr(getattr(query, "from_user", None), "id", None) or 0,
        chat_id=getattr(getattr(getattr(query, "message", None), "chat", None), "id", None) or 0,
        message_id=getattr(getattr(query, "message", None), "id", None) or 0,
    )
    for group, name, handler in handlers:
        try:
            ok = await handler.check(bot, probe)
        except Exception as exc:
            ok = f"err:{type(exc).__name__}"
        check_results.append(f"{name}@{group}={ok}")

    logger.error(
        "callback_diag.fallback_decision data=%s reason=%s py_regex=%s parse_help=%s "
        "route_seen=%s help_handlers=%s handler_checks=%s",
        data[:64],
        reason,
        py_match,
        parsed is not None,
        getattr(query, "_musicbot_callback_route_seen", False),
        len(handlers),
        ";".join(check_results) or "none",
    )
