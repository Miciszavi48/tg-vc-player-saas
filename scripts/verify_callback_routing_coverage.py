#!/usr/bin/env python3
"""Offline callback routing coverage verifier.

The script registers the real application handlers on an in-memory Pyrogram
client and probes visible/representative callback samples against the actual
dispatcher filters. It does not start Telegram, Redis, PostgreSQL, or PyTgCalls.
"""

# ruff: noqa: E402

from __future__ import annotations

import ast
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.callback-routing-verify.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from pyrogram import Client
from pyrogram.enums import ChatType
from pyrogram.handlers import CallbackQueryHandler
from pyrogram.types import CallbackQuery, Chat, Message, User

from app.config.settings import settings
from app.handlers import register_all
from app.handlers.callback_route_diag import (
    NON_ROUTE_GROUPS,
    _filter_repr,
    _handler_module,
    _handler_name,
    _is_rejection_handler,
    _iter_callback_handlers,
    _pattern_matches,
    _regex_patterns,
)
from app.handlers.priority import FALLBACK_CALLBACK_GROUP
from app.services.playback_callback_dispatcher import EXPECTED_CALLBACKS
from app.utils.ui import CB

USER_ID = int(settings.DEVELOPER_ID or os.environ.get("DEVELOPER_ID", "123456789"))
GROUP_ID = -1003700458073
CHANNEL_ID = -1002000000001
ISSUED_AT = 1234567890
YOUTUBE_ID = "dQw4w9WgXcQ"


@dataclass(frozen=True)
class CallbackSample:
    sample: str
    family: str
    source: str
    kind: str
    context: str
    enforce: bool = True
    inert: bool = False


@dataclass(frozen=True)
class RouteResult:
    sample: CallbackSample
    matched: bool
    group: int | None
    handler_name: str
    module: str
    filter_repr: str
    pattern: str
    fallback: bool
    status: str
    notes: str


_SAMPLE_BY_NAME = {
    "chat_id": str(GROUP_ID),
    "target_chat_id": str(GROUP_ID),
    "group_id": str(GROUP_ID),
    "channel_id": str(CHANNEL_ID),
    "user_id": str(USER_ID),
    "requester_id": str(USER_ID),
    "target_id": str(USER_ID),
    "target_uid": str(USER_ID),
    "target_user_id": str(USER_ID),
    "owner_id": str(USER_ID),
    "sudo_id": str(USER_ID),
    "helper_id": str(USER_ID),
    "message_id": "55",
    "page": "1",
    "days": "30",
    "scope": "all",
    "period": "week",
    "lang": "fa",
    "invoice_id": "7",
    "report_id": "7",
    "command_id": "7",
    "field": "start_text",
    "video_id": YOUTUBE_ID,
}


def _family(data: str) -> str:
    if data == "noop":
        return "noop"
    return data.split(":", 1)[0] if ":" in data else data


def _context_for(data: str) -> str:
    if data.startswith(("Add:", "grp:", "h:", "search:", "dl:")):
        return "group"
    if data in EXPECTED_CALLBACKS:
        return "group"
    if data.startswith(("pb:radio:", "pb:tv:", "pb:sat:", "pg:vip:", "fav:")):
        return "group"
    return "private"


def _expanded_prefix_sample(key: str, value: str) -> str:
    exact = {
        "INSTALL_SETUP_PREFIX": (
            f"{value}ShowSetCharge:G{GROUP_ID}:U{USER_ID}:T{ISSUED_AT}"
        ),
        "WZ_CANCEL_PREFIX": f"{value}dev_lists",
        "WZ_BACK_PREFIX": f"{value}dev_lists",
        "FM_REMOVE": f"{value}:{CHANNEL_ID}",
        "BC_DETAIL": f"{value}:7",
        "BC_CANCEL": f"{value}:7",
        "DEV_SUDO_PERM_TOGGLE_PREFIX": f"{value}g:{USER_ID}:1",
        "DEV_SUDO_PERM_DO_PREFIX": f"{value}g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}",
        "DEV_SUDO_PERM_NO_PREFIX": f"{value}g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}",
        "OWN_SUDO_PERM_TOGGLE_PREFIX": f"{value}g:{USER_ID}:1",
        "OWN_SUDO_PERM_DO_PREFIX": f"{value}g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}",
        "OWN_SUDO_PERM_NO_PREFIX": f"{value}g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}",
    }
    if key in exact:
        return exact[key]
    if key.startswith("HELP_") and value.startswith("h:"):
        return f"{value}:U{USER_ID}"
    if value.endswith(":"):
        if "TEXT" in key:
            return f"{value}start_text"
        if "PERM" in key:
            return f"{value}play:{USER_ID}:1"
        if "CLEAR" in key or "EXEC" in key or "ABORT" in key or "CONFIRM" in key:
            return f"{value}{GROUP_ID}:{USER_ID}:{ISSUED_AT}"
        if "DETAIL" in key:
            return f"{value}{USER_ID}:1"
        if "PAGE" in key or key.startswith("PAGE_") or "LIST_BACK" in key:
            return f"{value}1"
        if "REMOVE" in key or key.endswith("_RM_PREFIX"):
            return f"{value}{GROUP_ID}:1"
        if "LEAVE" in key:
            return f"{value}{GROUP_ID}:g:1"
        if "FAV" in key:
            return f"{value}1"
        if "HLP_" in key:
            return f"{value}{USER_ID}"
        return f"{value}1"
    return value


def _resolve_cb_subscript(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "CB"
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in CB
    ):
        return CB[node.slice.value]
    return None


def _resolve_formatted(node: ast.AST) -> str | None:
    value = _resolve_cb_subscript(node)
    if value is not None:
        return value
    if isinstance(node, ast.Name):
        return _SAMPLE_BY_NAME.get(node.id)
    if isinstance(node, ast.Attribute):
        return _SAMPLE_BY_NAME.get(node.attr)
    if isinstance(node, ast.Constant):
        return str(node.value)
    return None


def _resolve_callback_expr(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    subscript = _resolve_cb_subscript(node)
    if subscript is not None:
        return subscript
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                resolved = _resolve_formatted(value.value)
                if resolved is None:
                    return None
                parts.append(resolved)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve_callback_expr(node.left)
        right = _resolve_callback_expr(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.Call):
        func_name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if func_name == "_help_cb" and node.args:
            base = _resolve_callback_expr(node.args[0])
            if base is None:
                return None
            if len(node.args) > 1:
                return f"{base}:U{USER_ID}"
            return base
    return None


def _call_name(node: ast.Call) -> str:
    return getattr(node.func, "id", getattr(node.func, "attr", ""))


def _extract_static_visible_callbacks() -> tuple[list[CallbackSample], int]:
    found: list[CallbackSample] = []
    unresolved = 0
    for path in sorted((ROOT / "app").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            expr: ast.AST | None = None
            kind = "static"
            if name == "InlineKeyboardButton":
                for kw in node.keywords:
                    if kw.arg == "callback_data":
                        expr = kw.value
                        kind = "static"
                        break
            elif name == "_btn" and len(node.args) >= 2:
                expr = node.args[1]
                kind = "factory_static"
            elif name == "_help_btn" and len(node.args) >= 3:
                expr = ast.Call(
                    func=ast.Name(id="_help_cb", ctx=ast.Load()),
                    args=[node.args[2], ast.Constant(USER_ID)],
                    keywords=[],
                )
                kind = "factory_static"
            if expr is None:
                continue
            value = _resolve_callback_expr(expr)
            if value is None:
                unresolved += 1
                continue
            if ":" not in value and value != "noop":
                continue
            enforce = not (
                value == CB["HELP_HOME"]
                and rel == "app/utils/ui.py"
                and node.lineno >= 2000
            )
            found.append(
                CallbackSample(
                    sample=value,
                    family=_family(value),
                    source=f"{rel}:{node.lineno}",
                    kind=kind,
                    context=_context_for(value),
                    enforce=enforce,
                    inert=value == "noop",
                )
            )
    return found, unresolved


def _curated_dynamic_samples() -> list[CallbackSample]:
    samples: list[tuple[str, str]] = []
    for scope in ("all", "vip", "admin"):
        for period in ("all", "week", "today"):
            samples.append(
                (
                    "callstats_selection",
                    f"grp:callstats:sel:{scope}:{period}:{GROUP_ID}:{USER_ID}",
                )
            )
    samples.extend([
        ("callstats_close", f"grp:callstats:close:{GROUP_ID}:{USER_ID}"),
        ("search_play", f"search:play:{YOUTUBE_ID}"),
        ("radio_station", "pb:radio:bbc_world"),
        ("tv_channel", "pb:tv:irib1"),
        ("sat_page", "pb:sat:page:1"),
        ("sat_channel", "pb:sat:gem_tv"),
        ("group_vip_page", "pg:vip:1"),
        ("group_vip_demote", f"grp:vip:rm:{USER_ID}:1"),
        ("group_vip_clear_confirm", f"grp:vip:clear_confirm:{GROUP_ID}:{USER_ID}:{ISSUED_AT}"),
        ("group_vip_clear_cancel", f"grp:vip:clear_cancel:{GROUP_ID}:{USER_ID}:{ISSUED_AT}"),
        ("group_clear_confirm", f"grp:mgmt:clear_confirm:{GROUP_ID}:{USER_ID}:{ISSUED_AT}"),
        ("group_clear_cancel", f"grp:mgmt:clear_cancel:{GROUP_ID}:{USER_ID}:{ISSUED_AT}"),
        (
            "install_setup",
            f"Add:Fa:ShowSetCharge:G{GROUP_ID}:U{USER_ID}:T{ISSUED_AT}",
        ),
        ("helper_detail", f"hlp:d:{USER_ID}"),
        ("helper_proxy", f"hlp:proxy:{USER_ID}"),
        ("helper_enable", f"hlp:en:{USER_ID}"),
        ("helper_disable", f"hlp:dis:{USER_ID}"),
        ("helper_quarantine", f"hlp:q:{USER_ID}"),
        ("helper_unquarantine", f"hlp:uq:{USER_ID}"),
        ("helper_enable_confirm", f"hlp:en:do:{USER_ID}:{USER_ID}:{ISSUED_AT}"),
        ("helper_enable_abort", f"hlp:en:no:{USER_ID}:{USER_ID}:{ISSUED_AT}"),
        ("helper_rotate_confirm", f"hlp:rotkey:confirm:{USER_ID}:{ISSUED_AT}"),
        ("helper_rotate_cancel", f"hlp:rotkey:cancel:{USER_ID}:{ISSUED_AT}"),
        ("helper_otp_cancel", "hlp:otp:cancel"),
        ("helper_otp_back_phone", "hlp:otp:back:phone"),
        ("helper_otp_back_code", "hlp:otp:back:code"),
        ("helper_import_back_session", "hlp:imp:back:session"),
        ("broadcast_detail", "bc:detail:7"),
        ("broadcast_cancel", "bc:cancel:7"),
        ("broadcast_cancel_confirm", f"bc:cancel:confirm:7:{USER_ID}:{ISSUED_AT}"),
        ("broadcast_cancel_abort", f"bc:cancel:abort:7:{USER_ID}:{ISSUED_AT}"),
        ("force_join_remove", f"fm:rm:{CHANNEL_ID}"),
        ("force_join_remove_exec", f"fm:rm:do:{CHANNEL_ID}:{USER_ID}:{ISSUED_AT}"),
        ("force_join_remove_abort", f"fm:rm:no:{CHANNEL_ID}:{USER_ID}:{ISSUED_AT}"),
        ("start_category_ability", "start:cat:ability"),
        ("start_category_test", "start:cat:test"),
        ("start_category_use", "start:cat:use"),
        ("start_category_history", "start:cat:history"),
        ("start_category_note", "start:cat:note"),
        ("dev_start_style_toggle", "dev:start:style:toggle"),
        ("owner_start_style_toggle", "own:start:style:toggle"),
        ("dev_monthly_invoice_detail", "dev:monthly_invoice:detail:7"),
        ("dev_list_detail", f"dev:list:detail:g:g:{GROUP_ID}:1"),
        ("dev_list_back", "dev:list:back:g:1"),
        ("dev_list_credit_inc", f"dev:list:credit:inc:g:g:{GROUP_ID}:1"),
        ("dev_list_credit_dec", f"dev:list:credit:dec:g:g:{GROUP_ID}:1"),
        ("dev_force_join_page", "dev:fj:pg:1"),
        ("dev_force_join_remove", f"dev:fj:rm:{CHANNEL_ID}:1"),
        ("dev_force_join_remove_exec", f"dev:fj:rm:do:{CHANNEL_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("dev_force_join_remove_abort", f"dev:fj:rm:no:{CHANNEL_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("dev_leave_confirm", f"dev:leave:cfm:{GROUP_ID}:g:1"),
        ("dev_leave_exec", f"dev:leave:do:{GROUP_ID}:g:1"),
        ("dev_leave_cancel", f"dev:leave:no:{GROUP_ID}:g:1"),
        ("dev_sudo_detail", f"dev:sudo:detail:{USER_ID}:1"),
        ("dev_sudo_back", "dev:sudo:back:1"),
        ("dev_sudo_perm_toggle", f"dev:sp:t:g:{USER_ID}:1"),
        ("dev_sudo_perm_do", f"dev:sp:y:g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("dev_sudo_perm_no", f"dev:sp:n:g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("owner_sudo_detail", f"own:sudo:detail:{USER_ID}:1"),
        ("owner_sudo_back", "own:sudo:back:1"),
        ("owner_sudo_perm_toggle", f"own:sp:t:g:{USER_ID}:1"),
        ("owner_sudo_perm_do", f"own:sp:y:g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("owner_sudo_perm_no", f"own:sp:n:g:{USER_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("owner_force_join_page", "own:fj:pg:1"),
        ("owner_force_join_remove", f"own:fj:rm:{CHANNEL_ID}:1"),
        ("owner_text_field", "own:text:f:start_text"),
        ("owner_text_set", "own:text:txt:start_text"),
        ("owner_text_media", "own:text:med:start_text"),
        ("owner_text_clear", "own:text:clr:start_text"),
        ("owner_text_preview", "own:text:prv:start_text"),
        ("sudo_leave_confirm", f"sudo:leave_installs:confirm:{USER_ID}"),
        ("sudo_leave_cancel", f"sudo:leave_installs:cancel:{USER_ID}"),
        ("sudo_group_list_active", "sudo:grp:l:active:1"),
        ("sudo_group_list_no_credit", "sudo:grp:l:no_credit:1"),
        ("sudo_group_detail", f"sudo:grp:d:active:{GROUP_ID}:1"),
        ("sudo_group_credit_inc", f"sudo:grp:ci:active:{GROUP_ID}:1"),
        ("sudo_group_credit_dec", f"sudo:grp:cd:active:{GROUP_ID}:1"),
        ("sudo_group_leave_confirm", f"sudo:grp:lv:active:{GROUP_ID}:1"),
        ("sudo_group_leave_exec", f"sudo:grp:lv:do:active:{GROUP_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("sudo_group_leave_cancel", f"sudo:grp:lv:no:active:{GROUP_ID}:1:{USER_ID}:{ISSUED_AT}"),
        ("fav_info", "fav:info:1"),
        ("fav_remove", "fav:rm:1"),
        ("fav_page", "pg:fav:1"),
        ("dev_page_groups", "pg:dg:1"),
        ("dev_page_channels", "pg:dc:1"),
        ("dev_page_no_credit", "pg:dnc:1"),
        ("dev_page_renewal", "pg:drn:1"),
        ("dev_page_listx", "pg:dx:g:1"),
        ("dev_page_users", "pg:dux:a:1"),
        ("dev_page_owners", "pg:dev:owners:1"),
        ("dev_page_sudos", "pg:dev:sudos:1"),
        ("owner_page_owners", "pg:own:owners:1"),
        ("owner_page_sudos", "pg:own:sudos:1"),
        ("youtube_sessions_dev", "dev:yt_sessions"),
        ("youtube_sessions_owner", "own:yt_sessions"),
        ("youtube_sessions_home", "yts:home"),
        ("youtube_sessions_list", "yts:list:0"),
        ("youtube_sessions_detail", "yts:detail:7:0"),
        ("youtube_sessions_test", "yts:test:7:0"),
        ("youtube_sessions_enable", "yts:enable:7:0"),
        ("youtube_sessions_disable", "yts:disable:7:0"),
        ("youtube_sessions_delete_prompt", "yts:del:prompt:7:0"),
        ("youtube_sessions_delete_confirm", f"yts:del:confirm:7:0:{USER_ID}:{ISSUED_AT}"),
        ("youtube_sessions_delete_cancel", f"yts:del:cancel:7:0:{USER_ID}:{ISSUED_AT}"),
        ("youtube_sessions_rekey_prompt", "yts:rekey:prompt"),
        ("youtube_sessions_rekey_confirm", f"yts:rekey:confirm:{USER_ID}:{ISSUED_AT}"),
        ("youtube_sessions_rekey_cancel", f"yts:rekey:cancel:{USER_ID}:{ISSUED_AT}"),
        ("youtube_download_audio", "dl:fmt:a:token123"),
        ("youtube_download_video", "dl:fmt:v:token123"),
        ("youtube_download_cancel", "dl:fmt:x:token123"),
        ("fast_creat_tokens_dev", "dev:fast_creat_tokens"),
        ("fast_creat_tokens_owner", "own:fast_creat_tokens"),
        ("fast_creat_tokens_home", "fct:home"),
        ("fast_creat_tokens_provider", "fct:provider:instagram"),
        ("fast_creat_tokens_add", "fct:add:instagram"),
        ("fast_creat_tokens_list", "fct:list:instagram:0"),
        ("fast_creat_tokens_detail", "fct:detail:instagram:7:0"),
        ("fast_creat_tokens_enable", "fct:enable:instagram:7:0"),
        ("fast_creat_tokens_disable", "fct:disable:instagram:7:0"),
        ("fast_creat_tokens_delete_prompt", "fct:del:prompt:instagram:7:0"),
        ("fast_creat_tokens_delete_confirm", f"fct:del:confirm:instagram:7:0:{USER_ID}:{ISSUED_AT}"),
        ("fast_creat_tokens_delete_cancel", f"fct:del:cancel:instagram:7:0:{USER_ID}:{ISSUED_AT}"),
    ])
    for cb in EXPECTED_CALLBACKS:
        samples.append(("playback_control", cb))
    visible_help_keys = (
        "HELP_HOME",
        "HELP_PLAYBACK",
        "HELP_PUBLIC",
        "HELP_PROMOTE",
        "HELP_UTILITY",
        "HELP_CLOSE",
        "HELP_PLAY_REPLY",
        "HELP_PLAY_LINK",
        "HELP_PLAY_AUTO_MUSIC",
        "HELP_PLAY_AUTO_VIDEO",
        "HELP_PLAY_YOUTUBE",
        "HELP_PLAY_RADIO",
        "HELP_PLAY_SERIAL",
        "HELP_PLAY_TV",
        "HELP_PLAY_SATELLITE",
        "HELP_PLAY_CONTROLS",
        "HELP_PUBLIC_GROUP",
        "HELP_PUBLIC_USER",
        "HELP_PROMOTE_DEPUTY",
        "HELP_PROMOTE_ADMIN",
        "HELP_PROMOTE_VIP",
    )
    for key in visible_help_keys:
        samples.append(("help_bound", f"{CB[key]}:U{USER_ID}"))
    return [
        CallbackSample(
            sample=value,
            family=_family(value),
            source=label,
            kind="curated_dynamic",
            context=_context_for(value),
            inert=value == "noop",
        )
        for label, value in samples
    ]


def _constant_surface_samples() -> list[CallbackSample]:
    rows: list[CallbackSample] = []
    for key, value in CB.items():
        sample = _expanded_prefix_sample(key, value)
        rows.append(
            CallbackSample(
                sample=sample,
                family=_family(sample),
                source=f"CB[{key}]",
                kind="constant_surface",
                context=_context_for(sample),
                enforce=False,
                inert=sample == "noop",
            )
        )
    return rows


def build_samples() -> tuple[list[CallbackSample], int]:
    static, unresolved = _extract_static_visible_callbacks()
    samples = static + _curated_dynamic_samples() + _constant_surface_samples()
    deduped: list[CallbackSample] = []
    seen: set[tuple[str, str, str]] = set()
    for sample in samples:
        key = (sample.sample, sample.context, sample.kind)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sample)
    return deduped, unresolved


def _build_query(sample: CallbackSample) -> CallbackQuery:
    if sample.context == "group":
        chat = Chat(id=GROUP_ID, type=ChatType.SUPERGROUP)
    else:
        chat = Chat(id=USER_ID, type=ChatType.PRIVATE)
    return CallbackQuery(
        id="callback_routing_coverage_probe",
        from_user=User(id=USER_ID, is_bot=False),
        chat_instance="callback_routing_coverage",
        message=Message(id=406, chat=chat),
        data=sample.sample,
    )


async def _check_sample(bot: Client, sample: CallbackSample) -> RouteResult:
    if sample.inert:
        return RouteResult(
            sample=sample,
            matched=True,
            group=None,
            handler_name="noop",
            module="intentional_inert",
            filter_repr="noop",
            pattern="noop",
            fallback=False,
            status="inert",
            notes="intentional status-only callback",
        )

    denied: list[str] = []
    fallback: tuple[int, CallbackQueryHandler, str] | None = None
    any_pattern = False

    for group, handler in _iter_callback_handlers(bot):
        patterns = _regex_patterns(handler.filters)
        matching_pattern = next((p for p in patterns if _pattern_matches(p, sample.sample)), "")
        if not matching_pattern:
            continue
        any_pattern = True
        try:
            matched = bool(await handler.check(bot, _build_query(sample)))
        except Exception as exc:
            denied.append(f"{_handler_name(handler)}@{group}=error:{type(exc).__name__}")
            continue
        if not matched:
            denied.append(f"{_handler_name(handler)}@{group}=False")
            continue
        if group == FALLBACK_CALLBACK_GROUP:
            fallback = (group, handler, matching_pattern)
            continue
        if group in NON_ROUTE_GROUPS:
            denied.append(f"{_handler_name(handler)}@{group}=non_route_group")
            continue
        if _is_rejection_handler(handler):
            return RouteResult(
                sample=sample,
                matched=False,
                group=group,
                handler_name=_handler_name(handler),
                module=_handler_module(handler),
                filter_repr=_filter_repr(handler.filters),
                pattern=matching_pattern,
                fallback=False,
                status="rejection_handler",
                notes="visible callback selected a rejection handler",
            )
        return RouteResult(
            sample=sample,
            matched=True,
            group=group,
            handler_name=_handler_name(handler),
            module=_handler_module(handler),
            filter_repr=_filter_repr(handler.filters),
            pattern=matching_pattern,
            fallback=False,
            status="matched",
            notes="",
        )

    if fallback is not None:
        group, handler, pattern = fallback
        return RouteResult(
            sample=sample,
            matched=False,
            group=group,
            handler_name=_handler_name(handler),
            module=_handler_module(handler),
            filter_repr=_filter_repr(handler.filters),
            pattern=pattern,
            fallback=True,
            status="fallback_only",
            notes="known-prefix fallback matched before any dedicated route",
        )

    status = "regex_mismatch" if not any_pattern else "filter_denied"
    notes = "" if not denied else ",".join(denied[:4])
    if sample.kind == "constant_surface" and not sample.enforce:
        status = f"constant_{status}"
    return RouteResult(
        sample=sample,
        matched=False,
        group=None,
        handler_name="none",
        module="none",
        filter_repr="none",
        pattern="none",
        fallback=False,
        status=status,
        notes=notes,
    )


def _print_table(rows: list[RouteResult]) -> None:
    print("| sample | family | source | matched | group | handler | module | fallback? | status | notes |")
    print("|---|---|---|---:|---:|---|---|---:|---|---|")
    for row in rows:
        group = "" if row.group is None else str(row.group)
        sample = row.sample.sample.replace("|", "\\|")
        source = row.sample.source.replace("|", "\\|")
        notes = row.notes.replace("|", "\\|")
        print(
            f"| {sample} | {row.sample.family} | {source} | {row.matched} | {group} | "
            f"{row.handler_name} | {row.module} | {row.fallback} | {row.status} | {notes} |"
        )


def _print_summary(
    rows: list[RouteResult],
    *,
    unresolved_static: int,
    handler_count: int,
) -> None:
    static_count = sum(1 for row in rows if row.sample.kind in {"static", "factory_static"})
    curated_count = sum(1 for row in rows if row.sample.kind == "curated_dynamic")
    constant_count = sum(1 for row in rows if row.sample.kind == "constant_surface")
    fallback_count = sum(1 for row in rows if row.fallback and row.sample.enforce)
    failed_count = sum(1 for row in rows if row.sample.enforce and row.status not in {"matched", "inert"})
    print()
    print("summary:")
    print(f"  static_visible_callbacks={static_count}")
    print(f"  curated_dynamic_samples={curated_count}")
    print(f"  constant_surface_samples={constant_count}")
    print(f"  representative_samples={len(rows)}")
    print(f"  registered_callback_handlers={handler_count}")
    print(f"  fallback_only_enforced_samples={fallback_count}")
    print(f"  unresolved_static_callback_expressions={unresolved_static}")
    print(f"  failed_enforced_samples={failed_count}")


async def main() -> int:
    bot = Client(
        "callback_routing_coverage_verify",
        api_id=1,
        api_hash="x",
        bot_token="1:xx",
        in_memory=True,
    )
    register_all(bot, None)
    await asyncio.sleep(0.2)

    samples, unresolved_static = build_samples()
    rows = [await _check_sample(bot, sample) for sample in samples]
    _print_table(rows)
    handler_count = sum(1 for _group, _handler in _iter_callback_handlers(bot))
    _print_summary(rows, unresolved_static=unresolved_static, handler_count=handler_count)

    failures = [
        row
        for row in rows
        if row.sample.enforce and row.status not in {"matched", "inert"}
    ]
    if len(rows) < 400:
        print("FAIL: representative callback sample count is below 400")
        return 1
    if failures:
        print("FAIL: visible callback samples missing dedicated non-fallback routes")
        for row in failures[:50]:
            print(f"- {row.sample.sample}: {row.status} {row.notes}")
        return 1
    print("PASS: visible callback samples route to dedicated non-fallback handlers")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
