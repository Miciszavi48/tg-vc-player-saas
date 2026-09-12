#!/usr/bin/env python3
"""Project-wide callback dispatch matrix auditor.

Extends scripts/verify_callback_routing_coverage.py (which probes FRESH queries
with the ALLOWED actor only) with the dimensions where routing bugs hide:

  * stale queries (``query.message is None`` — old/edited/deleted panel);
  * denied actors (non-developer) — permission denial must be explicit,
    never a silent skip into ``unknown_callback``;
  * faithful group-ordered replay (first accepted handler per group runs,
    guard groups pass through, terminal groups StopPropagation, ``route_seen``
    suppresses the known-prefix fallback);
  * duplicate-ownership / shadowing detection (all accepting route handlers
    per sample, not just the winner);
  * families with dedicated routes missing from ``_KNOWN_CB_PREFIX``;
  * route-group handlers with no regex pattern at all.
  * relevant MessageHandler paths alongside RawUpdateHandler observers, using
    real registration order and first-match-per-group breaks.

Writes a machine-readable inventory to
``docs/reports/current/callback_dispatch_matrix.json`` (no secrets, no runtime
data — synthetic probe IDs only). Exit code 1 when enforced defects exist.

Run: ``python scripts/audit_callback_dispatch_matrix.py [--check]``
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.callback-matrix-audit.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from verify_callback_routing_coverage import (  # noqa: I001  (scripts-dir import)
    GROUP_ID,
    USER_ID,
    CallbackSample,
    build_samples,
)

from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.handlers import CallbackQueryHandler, MessageHandler, RawUpdateHandler
from pyrogram.types import CallbackQuery, Chat, Message, User

from app.handlers import register_all
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

OUTSIDER_ID = 987_654_321  # synthetic non-developer probe actor
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

# Duplicate owners that are deliberate, documented chains (dev-panel shortcut
# handlers reuse the same shared render_* helpers as the canonical owner).
INTENTIONAL_DUPLICATE_OWNERS = {
    "hlp:home": {"dev_shortcut_helper_home", "hlp_home"},
    "an:home": {"dev_shortcut_analytics_home", "an_home"},
    "bcw:start": {"dev_shortcut_bcw_start", "bcw_start"},
    "bc:history": {"dev_shortcut_bc_history", "bc_history"},
}


@dataclass
class ProbeOutcome:
    """Result of one dispatch replay for (sample, actor, freshness)."""

    answering: str = "NONE"
    answering_module: str = ""
    group: int | None = None
    kind: str = "none"  # route | rejection | unknown_fallback | none
    runs: list[str] = field(default_factory=list)
    post_route_unknown: bool = False
    filter_errors: int = 0


@dataclass
class SampleRow:
    sample: str
    family: str
    source: str
    kind: str
    context: str
    enforce: bool
    inert: bool
    fresh_allowed: ProbeOutcome | None = None
    stale_allowed: ProbeOutcome | None = None
    fresh_denied: ProbeOutcome | None = None
    acceptors: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)


@dataclass
class MessageProbe:
    """First claimant in every matching group for one synthetic Message."""

    label: str
    text: str
    path: list[str] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)


def _build_query(data: str, *, actor: int, stale: bool, context: str) -> CallbackQuery:
    if stale:
        message = None
    else:
        if context == "group":
            chat = Chat(id=GROUP_ID, type=ChatType.SUPERGROUP)
        else:
            chat = Chat(id=actor, type=ChatType.PRIVATE)
        message = Message(id=406, chat=chat)
    return CallbackQuery(
        id="callback_matrix_probe",
        from_user=User(id=actor, is_bot=False),
        chat_instance="callback_matrix",
        message=message,
        data=data,
    )


def _iter_cb_handlers(bot: Client):
    for group in sorted(bot.dispatcher.groups):
        for handler in bot.dispatcher.groups[group]:
            if isinstance(handler, CallbackQueryHandler):
                yield group, handler


async def _accepts(bot: Client, handler: CallbackQueryHandler, query) -> bool | str:
    try:
        return bool(await handler.check(bot, query))
    except Exception as exc:  # filter raised (env-dependent repo/redis path)
        return f"error:{type(exc).__name__}"


def _has_inverted_filter(flt) -> bool:
    """True when the filter tree contains an InvertFilter — the extracted regex
    is then a NEGATIVE requirement and the regex pre-filter must not be used."""
    seen: set[int] = set()

    def walk(node) -> bool:
        if node is None or id(node) in seen:
            return False
        seen.add(id(node))
        if type(node).__name__ == "InvertFilter":
            return True
        return any(walk(getattr(node, attr, None)) for attr in ("base", "other"))

    return walk(flt)


def _classify_handler(handler: CallbackQueryHandler) -> str:
    name = _handler_name(handler)
    if name in UNKNOWN_FALLBACKS:
        return "unknown_fallback"
    if _is_rejection_handler(handler):
        return "rejection"
    return "route"


async def replay(
    bot: Client,
    sample: CallbackSample,
    *,
    actor: int,
    stale: bool,
) -> ProbeOutcome:
    """Faithful Kurigram dispatch replay; returns the answering handler."""
    out = ProbeOutcome()
    route_seen = False
    for group in sorted(bot.dispatcher.groups):
        for handler in bot.dispatcher.groups[group]:
            if isinstance(handler, RawUpdateHandler):
                accepted = await _accepts(bot, handler, object())
                if accepted is True:
                    out.runs.append(f"raw:{getattr(handler.callback, '__name__', 'raw')}@{group}")
                    break
                if isinstance(accepted, str):
                    out.filter_errors += 1
                continue
            if not isinstance(handler, CallbackQueryHandler):
                continue
            patterns = _regex_patterns(handler.filters)
            # Regex pre-filter for speed; pattern-less and inverted-regex
            # handlers must always be fully checked.
            if (
                patterns
                and not _has_inverted_filter(handler.filters)
                and not any(_pattern_matches(p, sample.sample) for p in patterns)
            ):
                continue
            query = _build_query(
                sample.sample, actor=actor, stale=stale, context=sample.context
            )
            accepted = await _accepts(bot, handler, query)
            if isinstance(accepted, str):
                out.filter_errors += 1
                continue
            if not accepted:
                continue
            name = _handler_name(handler)
            if group in GUARD_GROUPS:
                out.runs.append(f"guard:{name}@{group}")
                break  # guard runs, dispatch proceeds to next group
            # The grp:* family fallback self-suppresses via the route_seen
            # marker exactly like the group-1000 known-prefix fallback.
            if name == "grp_permission_or_unknown_fallback" and route_seen:
                out.runs.append(f"suppressed:{name}@{group}")
                break
            if group == FALLBACK_CALLBACK_GROUP:
                if name == "known_prefix_unknown_callback_fallback" and route_seen:
                    out.runs.append(f"suppressed:{name}@{group}")
                    break
                if route_seen:
                    # unknown_callback_fallback after a real route ran: alert is
                    # dedup-suppressed at runtime but flags a missing known
                    # prefix family.
                    out.post_route_unknown = True
                    out.runs.append(f"post_route:{name}@{group}")
                    break
            kind = _classify_handler(handler)
            out.runs.append(f"{kind}:{name}@{group}")
            if out.answering == "NONE":
                out.answering = name
                out.answering_module = _handler_module(handler)
                out.group = group
                out.kind = kind
            if group != FALLBACK_CALLBACK_GROUP:
                route_seen = True
            if group in TERMINAL_GROUPS:
                return out
            break
    return out


async def collect_acceptors(bot: Client, sample: CallbackSample) -> list[str]:
    """Every non-guard, non-fallback handler whose full check accepts (fresh,
    allowed actor) — regardless of dispatch order. >1 distinct handler means
    duplicate ownership (the later ones are shadowed)."""
    found: list[str] = []
    for group, handler in _iter_cb_handlers(bot):
        if group in GUARD_GROUPS or group == FALLBACK_CALLBACK_GROUP:
            continue
        patterns = _regex_patterns(handler.filters)
        if (
            patterns
            and not _has_inverted_filter(handler.filters)
            and not any(_pattern_matches(p, sample.sample) for p in patterns)
        ):
            continue
        query = _build_query(sample.sample, actor=USER_ID, stale=False, context=sample.context)
        accepted = await _accepts(bot, handler, query)
        if accepted is True:
            name = _handler_name(handler)
            if _classify_handler(handler) == "route":
                found.append(f"{name}@{group}:{_handler_module(handler)}")
    return found


def _build_message(text: str, *, actor: int = USER_ID) -> Message:
    return Message(
        id=407,
        chat=Chat(id=actor, type=ChatType.PRIVATE),
        from_user=User(id=actor, is_bot=False),
        text=text,
    )


async def replay_message_path(bot: Client, text: str) -> list[str]:
    """Replay raw/message matching with Kurigram's per-group first-match break.

    This probe represents the normal-return path through inactive FSM
    listeners. ContinuePropagation would keep scanning the current group;
    StopPropagation would terminate dispatch. Active FSM consumption is
    covered by focused callback tests and documented group contracts.
    """
    message = _build_message(text)
    path: list[str] = []
    for group in sorted(bot.dispatcher.groups):
        for handler in bot.dispatcher.groups[group]:
            if isinstance(handler, RawUpdateHandler):
                accepted = await _accepts(bot, handler, object())
                if accepted is True:
                    path.append(f"raw:{_handler_name(handler)}@{group}")
                    break
                continue
            if not isinstance(handler, MessageHandler):
                continue
            accepted = await _accepts(bot, handler, message)
            if accepted is True:
                path.append(f"message:{_handler_name(handler)}@{group}")
                break
    return path


async def build_message_probes(bot: Client) -> list[MessageProbe]:
    probe = MessageProbe(
        label="helper_proxy_private_developer_text",
        text="socks5://host.example:1080",
    )
    probe.path = await replay_message_path(bot, probe.text)
    expected = [
        f"raw:call_security_raw_handler@{CALL_SECURITY_RAW_GROUP}",
        f"message:otp_text_handler@{HELPER_OTP_INPUT_GROUP}",
        f"message:hlp_proxy_input@{HELPER_PROXY_INPUT_GROUP}",
    ]
    missing = [claimant for claimant in expected if claimant not in probe.path]
    if missing:
        probe.defects.append("missing_claimants:" + ",".join(missing))
    elif not (
        probe.path.index(expected[0])
        < probe.path.index(expected[1])
        < probe.path.index(expected[2])
    ):
        probe.defects.append("raw_otp_proxy_order_invalid")
    if HELPER_OTP_INPUT_GROUP == HELPER_PROXY_INPUT_GROUP:
        probe.defects.append("otp_proxy_same_group_shadow")
    return [probe]


def _known_prefix_regex():
    import re as _re

    from app.handlers import callbacks as _cbm

    # _KNOWN_CB_PREFIX is defined inside register(); recover it from source to
    # avoid needing a bot. Fall back to scanning the module source.
    import inspect

    src = inspect.getsource(_cbm)
    start = src.index("_KNOWN_CB_PREFIX = re.compile(")
    chunk = src[start : src.index(")", src.index("re.IGNORECASE"))]
    lines = [ln.strip() for ln in chunk.splitlines() if ln.strip().startswith('r"')]
    pattern = "".join(ln.strip().strip(",").strip('r"') for ln in lines)
    return _re.compile(pattern, _re.IGNORECASE)


# Panel roots a user plausibly taps on an old message to re-enter a flow.
# These get the helper-panel treatment (identity filter + in-handler guard) so
# a stale tap still re-renders. Deep action/toggle/confirm callbacks act on the
# specific message and may legitimately answer the "reopen menu" alert instead.
def _is_panel_root(sample: str) -> bool:
    if sample in {"nav:back", "nav:start", "nav:close", "wz:home", "fm:panel",
                  "postinst:panel"}:
        return True
    return sample.endswith(":home")


def analyse(rows: list[SampleRow]) -> tuple[dict, dict]:
    defect_counts: dict[str, int] = {}
    info_counts: dict[str, int] = {}

    def add_defect(row: SampleRow, code: str) -> None:
        row.defects.append(code)
        defect_counts[code] = defect_counts.get(code, 0) + 1

    def add_info(code: str) -> None:
        info_counts[code] = info_counts.get(code, 0) + 1

    known_re = _known_prefix_regex()
    for row in rows:
        if row.inert:
            continue
        fa, sa, fd = row.fresh_allowed, row.stale_allowed, row.fresh_denied
        if fa is None:
            continue
        # A. fresh routing broken (the baseline verifier also enforces this)
        if row.enforce and fa.kind in {"unknown_fallback", "none"}:
            add_defect(row, "fresh_no_dedicated_route")
        elif row.enforce and fa.kind == "rejection":
            add_defect(row, "fresh_rejection_for_valid_sample")
        # B. stale behavior: silence is a defect (spinner hangs); the unknown
        # alert is the designed "reopen menu" UX except on panel roots.
        if fa.kind == "route" and sa is not None:
            if sa.kind == "none":
                add_defect(row, "stale_silent_no_answer")
            elif sa.kind == "unknown_fallback":
                if _is_panel_root(row.sample):
                    add_defect(row, "stale_root_degraded")
                else:
                    add_info("stale_reopen_menu_ux")
            elif sa.kind == "rejection":
                add_info("stale_explicit_rejection")
            elif sa.answering != fa.answering:
                add_info("stale_answering_handler_differs")
        # C. denied actor: group-context buttons are visible to every member —
        # denial must be explicit, not unknown_callback or silence.
        if fa.kind == "route" and fd is not None:
            if row.context == "group" and fd.kind in {"unknown_fallback", "none"}:
                add_defect(row, "denied_actor_unknown_or_silent_in_group")
            elif row.context == "private" and fd.kind in {"unknown_fallback", "none"}:
                add_info("denied_private_forged_tap_unknown")
        # D. family missing from _KNOWN_CB_PREFIX
        if fa.kind == "route" and not known_re.search(row.sample):
            add_defect(row, "family_missing_from_known_prefix")
        if fa.post_route_unknown or (sa is not None and sa.post_route_unknown):
            if "family_missing_from_known_prefix" not in row.defects:
                add_defect(row, "post_route_unknown_alert")
        # E. duplicate ownership / shadowing. Same-module duplicates are
        # deliberate registration-order chains (help_center legacy routing,
        # dev_panel removed-financial rejection surface); cross-module
        # duplicates silently shadow the later module and are real defects.
        distinct = {a.split("@", 1)[0] for a in row.acceptors}
        modules = {a.split(":", 1)[1] for a in row.acceptors if ":" in a}
        if len(distinct) > 1:
            allowed = INTENTIONAL_DUPLICATE_OWNERS.get(row.sample)
            if allowed and distinct <= allowed:
                add_info("duplicate_intentional_dev_shortcut")
            elif len(modules) <= 1:
                add_info("duplicate_same_module_chain")
            else:
                add_defect(row, "duplicate_owners_cross_module")
    return defect_counts, info_counts


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 on enforced defects")
    parser.add_argument(
        "--out",
        default=str(ROOT / "docs" / "reports" / "current" / "callback_dispatch_matrix.json"),
    )
    args = parser.parse_args()

    from app.database.engine import init_db

    await init_db()

    bot = Client(
        "callback_matrix_audit", api_id=1, api_hash="x", bot_token="1:xx", in_memory=True
    )
    register_all(bot, None)
    await asyncio.sleep(0.3)

    samples, unresolved_static = build_samples()
    rows: list[SampleRow] = []
    for sample in samples:
        row = SampleRow(
            sample=sample.sample,
            family=sample.family,
            source=sample.source,
            kind=sample.kind,
            context=sample.context,
            enforce=sample.enforce,
            inert=sample.inert,
        )
        if not sample.inert:
            row.fresh_allowed = await replay(bot, sample, actor=USER_ID, stale=False)
            row.stale_allowed = await replay(bot, sample, actor=USER_ID, stale=True)
            row.fresh_denied = await replay(bot, sample, actor=OUTSIDER_ID, stale=False)
            row.acceptors = await collect_acceptors(bot, sample)
        rows.append(row)

    # Route-group handlers with no regex pattern at all (accept via custom
    # filters only) — inventoried because the regex pre-filter of every audit
    # tool would otherwise never consider them.
    patternless = [
        f"{_handler_name(h)}@{g}:{_handler_module(h)}"
        for g, h in _iter_cb_handlers(bot)
        if g not in GUARD_GROUPS
        and g != FALLBACK_CALLBACK_GROUP
        and not _regex_patterns(h.filters)
    ]

    defect_counts, info_counts = analyse(rows)
    message_probes = await build_message_probes(bot)
    message_probe_defects = [
        f"{probe.label}:{defect}"
        for probe in message_probes
        for defect in probe.defects
    ]

    handler_total = sum(1 for _ in _iter_cb_handlers(bot))
    message_handler_total = sum(
        isinstance(handler, MessageHandler)
        for handlers in bot.dispatcher.groups.values()
        for handler in handlers
    )
    raw_handler_total = sum(
        isinstance(handler, RawUpdateHandler)
        for handlers in bot.dispatcher.groups.values()
        for handler in handlers
    )
    report = OrderedDict(
        generated_by="scripts/audit_callback_dispatch_matrix.py",
        propagation_model={
            "normal_return": "break current group and continue with the next group",
            "ContinuePropagation": "continue scanning later handlers in the current group",
            "StopPropagation": "terminate dispatch for the update",
        },
        probe_actors={"allowed": "settings.DEVELOPER_ID", "denied": OUTSIDER_ID},
        totals=OrderedDict(
            samples=len(rows),
            enforced=sum(1 for r in rows if r.enforce and not r.inert),
            registered_callback_handlers=handler_total,
            registered_message_handlers=message_handler_total,
            registered_raw_handlers=raw_handler_total,
            message_probes=len(message_probes),
            message_probe_defects=len(message_probe_defects),
            unresolved_static_callback_expressions=unresolved_static,
            patternless_route_handlers=len(patternless),
            defect_counts=defect_counts,
            informational_counts=info_counts,
        ),
        patternless_route_handlers=patternless,
        message_probe_defects=message_probe_defects,
        message_probes=[asdict(probe) for probe in message_probes],
        defects=[asdict(r) for r in rows if r.defects],
        rows=[asdict(r) for r in rows],
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {out_path}")
    print(
        f"samples={len(rows)} callbacks={handler_total} messages={message_handler_total} "
        f"raw={raw_handler_total} patternless={len(patternless)}"
    )
    for code, count in sorted(defect_counts.items()):
        print(f"  DEFECT {code}: {count}")
    for code, count in sorted(info_counts.items()):
        print(f"  info {code}: {count}")
    enforced_defects = [
        r for r in rows if r.defects and r.enforce and not r.inert
    ]
    print(f"enforced_rows_with_defects={len(enforced_defects)}")
    for defect in message_probe_defects:
        print(f"  DEFECT message_probe:{defect}")
    if args.check and (enforced_defects or message_probe_defects):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
