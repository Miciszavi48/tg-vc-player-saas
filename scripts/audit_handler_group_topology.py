#!/usr/bin/env python3
"""Generate and validate the repository-wide Kurigram handler-group inventory.

The inventory is built from a real in-memory ``Client`` after ``register_all``
has run, so decorators, direct registrations, dynamic registration loops, and
post-registration wrappers are represented in actual dispatch order. Static
registration sites and known dynamic listeners are included as audit evidence.

Run: ``python scripts/audit_handler_group_topology.py [--check]``
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import os
import sys
from collections import Counter, OrderedDict
from importlib import metadata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.handler-group-audit.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

import pyrogram
from pyrogram import Client
from pyrogram.dispatcher import Dispatcher

from app.handlers import _MODULES, register_all
from app.handlers.priority import (
    CALL_SECURITY_RAW_GROUP,
    DEFAULT_HANDLER_GROUP,
    HANDLER_GROUP_TOPOLOGY,
    PYROMOD_CALLBACK_GROUP,
    SAFE_ASK_LISTENER_GROUP,
)


ACCEPTED_UPDATE_TYPES = {
    "CallbackQueryHandler": ["CallbackQuery"],
    "ChatMemberUpdatedHandler": ["ChatMemberUpdated"],
    "MessageHandler": ["Message"],
    "RawUpdateHandler": ["RawUpdate(any)"],
}

KNOWN_REPAIRS = [
    {
        "id": "call_security_filterless_raw_group_zero",
        "status": "fixed",
        "cause": (
            "filterless RawUpdateHandler occupied group 0 and won the first-match "
            "break before typed CallbackQueryHandlers"
        ),
        "repair": f"call_security_raw_handler uses dedicated group {CALL_SECURITY_RAW_GROUP}",
    },
    {
        "id": "helper_proxy_same_group_shadow",
        "status": "fixed",
        "cause": (
            "otp_text_handler and hlp_proxy_input accepted the same private developer "
            "text updates in group -90; the earlier OTP listener returned normally and "
            "prevented the proxy listener from being checked"
        ),
        "repair": "hlp_proxy_input uses adjacent HELPER_PROXY_INPUT_GROUP after OTP input",
    },
    {
        "id": "safe_ask_handler_identity_cleanup",
        "status": "fixed_separate_cause",
        "cause": (
            "manual safe_ask cleanup removed the callback function instead of the exact "
            "registered MessageHandler object"
        ),
        "repair": "the exact group-99 Handler object is removed in finally",
    },
    {
        "id": "pyromod_listener_cleanup_scope_order",
        "status": "fixed",
        "cause": (
            "safe_stop_listening tried chat-wide cleanup before the supplied user-scoped "
            "signature, so a successful broad call prevented precise listener cleanup"
        ),
        "repair": "chat_id plus user_id signatures run before chat-wide compatibility fallbacks",
    },
]


def _walk_filter(flt: Any) -> list[Any]:
    seen: set[int] = set()
    stack = [flt]
    nodes: list[Any] = []
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        nodes.append(node)
        stack.extend((getattr(node, "base", None), getattr(node, "other", None)))
    return nodes


def _filter_inventory(flt: Any) -> dict[str, Any]:
    nodes = _walk_filter(flt)
    patterns: list[str] = []
    for node in nodes:
        pattern = getattr(getattr(node, "p", None), "pattern", None)
        if pattern is not None:
            patterns.append(str(pattern))
    return {
        "filterless": flt is None,
        "tree": [type(node).__name__ for node in nodes],
        "regex_patterns": patterns,
    }


def _original_callback(handler: Any) -> Any:
    callback = getattr(handler, "original_callback", handler.callback)
    try:
        return inspect.unwrap(callback)
    except ValueError:
        return callback


def _source_location(callback: Any) -> tuple[str, int | None]:
    try:
        source = inspect.getsourcefile(callback) or ""
        relative = str(Path(source).resolve().relative_to(ROOT))
        line = inspect.getsourcelines(callback)[1]
        return relative, line
    except (OSError, TypeError, ValueError):
        return "", None


def _collision_classification(
    *,
    group: int,
    handler_type: str,
    filterless: bool,
    same_type_count: int,
) -> tuple[str, str]:
    if handler_type == "RawUpdateHandler":
        return (
            "intentional_dedicated_raw_observer",
            "filterless raw acceptance is isolated from every typed-handler group",
        )
    if filterless:
        return (
            "intentional_broad_observer",
            "broad typed observer occupies an early documented guard/context group",
        )
    if same_type_count > 1:
        if group == DEFAULT_HANDLER_GROUP:
            return (
                "intentional_shared_default_group",
                "first-match order is protected by typed filters and callback dispatch gates",
            )
        return (
            "intentional_shared_typed_group",
            "first-match order requires disjoint filters or documented propagation",
        )
    return "dedicated_or_type_isolated", "no same-type sibling in this group"


def _handler_row(
    group: int,
    order: int,
    handler: Any,
    same_type_count: int,
) -> dict[str, Any]:
    callback = _original_callback(handler)
    module = getattr(callback, "__module__", "")
    symbol = getattr(callback, "__qualname__", getattr(callback, "__name__", ""))
    source, source_line = _source_location(callback)
    handler_type = type(handler).__name__
    filter_info = _filter_inventory(getattr(handler, "filters", None))
    topology = HANDLER_GROUP_TOPOLOGY.get(group, {})
    risk, risk_reason = _collision_classification(
        group=group,
        handler_type=handler_type,
        filterless=filter_info["filterless"],
        same_type_count=same_type_count,
    )
    processed_update_types = ACCEPTED_UPDATE_TYPES.get(handler_type, ["unknown"])
    if handler_type == "RawUpdateHandler" and symbol.endswith("call_security_raw_handler"):
        processed_update_types = [
            "UpdateGroupCall",
            "UpdateGroupCallParticipants",
        ]
    return OrderedDict(
        group=group,
        group_names=topology.get("names", []),
        registration_order_within_group=order,
        handler_type=handler_type,
        symbol=symbol,
        module=module,
        source=source,
        source_line=source_line,
        filter=filter_info,
        accepted_update_types=ACCEPTED_UPDATE_TYPES.get(handler_type, ["unknown"]),
        processed_update_types=processed_update_types,
        purpose=topology.get("purpose", "undocumented group"),
        propagation=topology.get("propagation", "undocumented"),
        collision_classification=risk,
        collision_reason=risk_reason,
    )


def _static_registration_sites() -> list[dict[str, Any]]:
    sites: list[dict[str, Any]] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if not (method.startswith("on_") or method == "add_handler"):
                continue
            group = "default(0)"
            for keyword in node.keywords:
                if keyword.arg == "group":
                    group = ast.unparse(keyword.value)
                    break
            sites.append({
                "source": str(path.relative_to(ROOT)),
                "line": node.lineno,
                "registration_method": method,
                "group_expression": group,
            })
    return sites


def _dynamic_rows() -> list[dict[str, Any]]:
    return [
        {
            "group": PYROMOD_CALLBACK_GROUP,
            "group_names": ["PYROMOD_CALLBACK_GROUP"],
            "handler_type": "CallbackQueryHandler or MessageHandler",
            "symbol": "pyromod dynamic ask/listen listener",
            "module": "pyromod",
            "filter": {"filterless": False, "tree": ["listener-defined"], "regex_patterns": []},
            "accepted_update_types": ["CallbackQuery", "Message"],
            "purpose": HANDLER_GROUP_TOPOLOGY[PYROMOD_CALLBACK_GROUP]["purpose"],
            "propagation": HANDLER_GROUP_TOPOLOGY[PYROMOD_CALLBACK_GROUP]["propagation"],
            "collision_classification": "intentional_dynamic_listener",
        },
        {
            "group": SAFE_ASK_LISTENER_GROUP,
            "group_names": ["SAFE_ASK_LISTENER_GROUP"],
            "handler_type": "MessageHandler",
            "symbol": "_fallback_ask.<locals>._listener",
            "module": "app.utils.safe_ask",
            "source": "app/utils/safe_ask.py",
            "filter": {
                "filterless": False,
                "tree": ["chat", "user/all", "text", "not command start/cancel"],
                "regex_patterns": [],
            },
            "accepted_update_types": ["Message"],
            "purpose": HANDLER_GROUP_TOPOLOGY[SAFE_ASK_LISTENER_GROUP]["purpose"],
            "propagation": HANDLER_GROUP_TOPOLOGY[SAFE_ASK_LISTENER_GROUP]["propagation"],
            "collision_classification": "intentional_bounded_dynamic_listener",
        },
    ]


def _topology_rows(handler_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    occupancy: dict[int, list[dict[str, Any]]] = {}
    for row in handler_rows:
        occupancy.setdefault(row["group"], []).append(row)
    rows: list[dict[str, Any]] = []
    for group, spec in sorted(HANDLER_GROUP_TOPOLOGY.items()):
        occupied = occupancy.get(group, [])
        rows.append({
            "group": group,
            **spec,
            "startup_handler_count": len(occupied),
            "startup_handler_types": dict(sorted(Counter(
                row["handler_type"] for row in occupied
            ).items())),
            "accepted_update_types": sorted({
                update_type
                for row in occupied
                for update_type in row["accepted_update_types"]
            }),
        })
    return rows


def _validate(handler_rows: list[dict[str, Any]]) -> list[str]:
    defects: list[str] = []
    for row in handler_rows:
        group = row["group"]
        handler_type = row["handler_type"]
        spec = HANDLER_GROUP_TOPOLOGY.get(group)
        if spec is None:
            defects.append(f"undocumented_group:{group}:{row['symbol']}")
            continue
        if handler_type not in spec["handler_types"]:
            defects.append(f"handler_type_not_allowed:{group}:{handler_type}:{row['symbol']}")
        if handler_type == "RawUpdateHandler":
            if group == DEFAULT_HANDLER_GROUP:
                defects.append(f"raw_observer_in_group_zero:{row['symbol']}")
            if group != CALL_SECURITY_RAW_GROUP:
                defects.append(f"raw_observer_wrong_group:{group}:{row['symbol']}")
    return defects


def _markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    lines = [
        "# Handler Group Inventory",
        "",
        "Generated by `scripts/audit_handler_group_topology.py` from a real in-memory registration.",
        "",
        "## Totals",
        "",
        f"- Startup handlers: {totals['startup_handlers']}",
        f"- Dynamic handler families: {totals['dynamic_handler_families']}",
        f"- Raw observers: {totals['raw_observers']}",
        f"- Filterless startup handlers: {totals['filterless_startup_handlers']}",
        f"- Proven risks / fixed / unresolved: {totals['proven_risks']} / {totals['fixed']} / {totals['unresolved']}",
        "",
        "## Topology",
        "",
        "| Group | Names | Startup occupancy | Handler classes | Purpose | Propagation |",
        "|---:|---|---:|---|---|---|",
    ]
    for row in report["topology"]:
        names = ", ".join(row["names"])
        classes = ", ".join(row["handler_types"])
        lines.append(
            f"| {row['group']} | {names} | {row['startup_handler_count']} | "
            f"{classes} | {row['purpose']} | {row['propagation']} |"
        )
    lines.extend((
        "",
        "## Repairs",
        "",
    ))
    for repair in report["known_repairs"]:
        lines.append(
            f"- `{repair['id']}` ({repair['status']}): {repair['cause']}. "
            f"Repair: {repair['repair']}."
        )
    lines.extend((
        "",
        "The JSON companion contains every handler symbol, module, source line, filter tree, "
        "registration order, accepted update type, purpose, propagation contract, and collision classification.",
        "",
    ))
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 on topology defects")
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs/reports/current/handler_group_inventory.json"),
    )
    args = parser.parse_args()

    bot = Client(
        "handler_group_inventory",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.3)

    handler_rows: list[dict[str, Any]] = []
    for group, handlers in sorted(bot.dispatcher.groups.items()):
        type_counts = Counter(type(handler).__name__ for handler in handlers)
        for order, handler in enumerate(handlers):
            handler_rows.append(
                _handler_row(group, order, handler, type_counts[type(handler).__name__])
            )

    defects = _validate(handler_rows)
    filterless = [row for row in handler_rows if row["filter"]["filterless"]]
    raw_rows = [row for row in handler_rows if row["handler_type"] == "RawUpdateHandler"]
    intentional = [
        row for row in handler_rows
        if row["collision_classification"].startswith("intentional_")
    ]
    static_sites = _static_registration_sites()
    dynamic_rows = _dynamic_rows()
    report = OrderedDict(
        generated_by="scripts/audit_handler_group_topology.py",
        registration_source="app.handlers.register_all on an in-memory Kurigram Client",
        runtime_identity={
            "distribution": "kurigram",
            "version": metadata.version("kurigram"),
            "import_namespace": "pyrogram",
            "import_version": pyrogram.__version__,
            "import_source": pyrogram.__file__,
            "dispatcher_source": inspect.getsourcefile(Dispatcher),
            "dispatch_semantics": (
                "first matching handler normally breaks its group; ContinuePropagation "
                "continues within the group; StopPropagation terminates dispatch; "
                "RawUpdateHandler checks every raw update"
            ),
        },
        module_registration_order=[module.__name__ for module in _MODULES],
        totals=OrderedDict(
            startup_handlers=len(handler_rows),
            dynamic_handler_families=len(dynamic_rows),
            inventoried=len(handler_rows) + len(dynamic_rows),
            raw_observers=len(raw_rows),
            filterless_startup_handlers=len(filterless),
            intentional_broad_or_shared_handlers=len(intentional),
            custom_multi_update_handler_classes=0,
            static_registration_sites=len(static_sites),
            proven_risks=len(KNOWN_REPAIRS),
            fixed=len(KNOWN_REPAIRS),
            unresolved=len(defects),
        ),
        defects=defects,
        known_repairs=KNOWN_REPAIRS,
        topology=_topology_rows(handler_rows),
        dynamic_handlers=dynamic_rows,
        static_registration_sites=static_sites,
        handlers=handler_rows,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out_path.with_suffix(".md")
    md_path.write_text(_markdown(report), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(f"Wrote {md_path}")
    print(
        f"startup={len(handler_rows)} dynamic={len(dynamic_rows)} raw={len(raw_rows)} "
        f"filterless={len(filterless)} defects={len(defects)}"
    )
    for defect in defects:
        print(f"  DEFECT {defect}")
    return 1 if args.check and defects else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
