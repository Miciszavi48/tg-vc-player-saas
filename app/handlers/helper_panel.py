from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.handlers.priority import HELPER_PROXY_INPUT_GROUP, PANEL_CALLBACK_GROUP
from app.config.settings import instance_session_name
from app.repositories import helper_event_repo
from app.services.helper_pool_service import HelperPoolService
from app.services.panel_message_service import deliver_panel_outcome, panel_callback_edit
from app.services.wizard_ui import (
    TOKEN_HELPER_HOME,
    WIZARD_HELPER_PROXY,
    build_done_kb,
    detect_active_wizard_state_in_redis,
    remember_return_token,
)
from app.utils.ask_result import (
    safe_delete_user_input,
    safe_stop_listening,
    safe_stop_propagation,
)
from app.utils.bot_guards import is_developer
from app.utils.cache import get_redis
from app.utils.callback_trace import (
    safe_answer_callback,
    safe_edit_or_send_callback,
    trace_callback_event,
    trace_guard,
    trace_helper_summary,
    trace_unhandled,
)
from app.utils.decorators import developer_only
from app.utils.diagnostic_logging import callback_data_prefix, safe_exc_name
from app.utils.filters import dev_filter, private_chat_filter
from app.utils.helpers import parse_bounded_int
from app.utils.i18n import AUTO_LANG, t
from app.utils.i18n import label as localized_label
from app.utils.redis_keys import (
    helper_otp_state_key,
    helper_proxy_state_key,
    wizard_return_key,
)
from app.utils.ui import CB, KeyboardFactory, compatible_inline_button

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_pm_dev = dev_filter() & private_chat_filter()
_HELPER_ROTATE_CONFIRM_TTL_SECONDS = 300


@dataclass(frozen=True)
class HelperPanelSummary:
    total: int
    active: int
    disabled: int
    quarantined: int
    available: int


def _parse_helper_rotate_token(data: str, prefix: str) -> tuple[int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 2:
        return None
    try:
        user_id = int(parts[0])
        issued_at = int(parts[1])
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return user_id, issued_at


def _parse_prefixed_positive_int(data: str | None, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    if not raw or ":" in raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _is_stale_helper_rotate_token(issued_at: int) -> bool:
    now = int(time.time())
    return issued_at > now + 60 or now - issued_at > _HELPER_ROTATE_CONFIRM_TTL_SECONDS


async def _rotate_helper_session_keys() -> tuple[int, int]:
    helpers = await HelperPoolService.get_all_helpers()
    rotated = errors = 0
    for h in helpers:
        if not h.session_string_enc:
            continue
        try:
            plain = HelperPoolService.decrypt_session(h.session_string_enc)
            if plain is None:
                errors += 1
                continue
            new_enc = HelperPoolService.encrypt_session(plain)
            from sqlalchemy import update

            from app.database.engine import async_session
            from app.database.models import HelperAccount

            async with async_session() as session:
                async with session.begin():
                    await session.execute(
                        update(HelperAccount)
                        .where(HelperAccount.id == h.id)
                        .values(session_string_enc=new_enc)
                    )
            rotated += 1
        except Exception:
            errors += 1
    return rotated, errors


def _is_private(query: CallbackQuery) -> bool:
    chat = query.message.chat if query.message else None
    return chat is not None and chat.type.value == "private"


def _callback_context(
    query: CallbackQuery,
) -> tuple[str | None, int | None, int | None, str]:
    chat = query.message.chat if query.message else None
    chat_type = chat.type.value if chat and getattr(chat, "type", None) else None
    message_chat_id = chat.id if chat else None
    user_id = query.from_user.id if query.from_user else None
    return chat_type, user_id, message_chat_id, query.data or ""


async def _safe_answer(
    query: CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
) -> None:
    await safe_answer_callback(query, text, show_alert=show_alert)


async def _safe_helper_edit(
    client: Client | None,
    query: CallbackQuery,
    text: str,
    *,
    reply_markup=None,
    selected_handler: str,
) -> str:
    if client is not None:
        ok = await panel_callback_edit(
            client,
            query,
            text,
            reply_markup,
            answer=False,
        )
        result = "edited" if ok else "fallback_attempted"
        trace_callback_event(
            "helper.edit.result",
            query,
            handler=selected_handler,
            result=result,
        )
        return result
    return await safe_edit_or_send_callback(
        client,
        query,
        text,
        reply_markup=reply_markup,
        handler=selected_handler,
    )


def _is_known_helper_callback(data: str) -> bool:
    exact = {
        CB["HLP_HOME"],
        CB["HLP_LIST"],
        CB["HLP_ADD"],
        CB["HLP_IMPORT_SESSION"],
        CB["HLP_ROTATE_KEY"],
        CB["HLP_HEALTH_CHECK"],
        CB["HLP_ADD_OTP"],
        CB["HLP_STATS"],
        "hlp:proxy:cancel",
        "hlp:otp:cancel",
        "hlp:otp:back:phone",
        "hlp:otp:back:code",
        "hlp:imp:back:session",
        "hlp:imp:back:phone",
        "hlp:imp:back:max_calls",
    }
    prefixes = (
        CB["HLP_DETAIL_PREFIX"],
        CB["HLP_SET_PROXY_PREFIX"],
        CB["HLP_ENABLE"],
        CB["HLP_ENABLE_CONFIRM_PREFIX"],
        CB["HLP_ENABLE_ABORT_PREFIX"],
        CB["HLP_DISABLE"],
        CB["HLP_DISABLE_CONFIRM_PREFIX"],
        CB["HLP_DISABLE_ABORT_PREFIX"],
        CB["HLP_QUARANTINE"],
        CB["HLP_QUARANTINE_CONFIRM_PREFIX"],
        CB["HLP_QUARANTINE_ABORT_PREFIX"],
        CB["HLP_UNQUARANTINE"],
        CB["HLP_UNQUARANTINE_CONFIRM_PREFIX"],
        CB["HLP_UNQUARANTINE_ABORT_PREFIX"],
        CB["HLP_ROTATE_CONFIRM_PREFIX"],
        CB["HLP_ROTATE_CANCEL_PREFIX"],
    )
    return data in exact or any(data.startswith(prefix) for prefix in prefixes)


async def _resolve_bot_username(client: Client | None) -> str | None:
    if client is None:
        return None
    get_me = getattr(client, "get_me", None)
    if not callable(get_me):
        return None
    try:
        me = await get_me()
    except Exception:
        logger.debug(
            "helper private guard could not resolve bot username", exc_info=True
        )
        return None
    username = getattr(me, "username", None)
    return username or None


async def _guard_private(
    query: CallbackQuery,
    bot_username: str | None = None,
    *,
    client: Client | None = None,
    selected_handler: str = "helper",
) -> bool:
    chat_type, user_id, message_chat_id, data = _callback_context(query)
    if _is_private(query):
        trace_guard(
            query, guard="private_chat", result="allow", handler=selected_handler
        )
        logger.info(
            "callback guard result selected_handler=%s guard=private result=pass "
            "data=%s chat_type=%s user_id=%s message_chat_id=%s",
            selected_handler,
            callback_data_prefix(data),
            chat_type,
            user_id,
            message_chat_id,
        )
        return False
    trace_guard(
        query,
        guard="private_chat",
        result="deny",
        reason="wrong_scope",
        handler=selected_handler,
    )
    logger.info(
        "callback guard result selected_handler=%s guard=private result=wrong_scope "
        "data=%s chat_type=%s user_id=%s message_chat_id=%s",
        selected_handler,
        callback_data_prefix(data),
        chat_type,
        user_id,
        message_chat_id,
    )
    await _safe_answer(query, t(_LANG, "admin.helpers.private_only"), show_alert=True)
    if bot_username is None:
        bot_username = await _resolve_bot_username(client)
    rows = []
    if bot_username:
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "admin.helpers.open_private_btn"),
                    url=f"https://t.me/{bot_username}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                t(_LANG, "common.buttons.back"), callback_data=CB["NAV_BACK"]
            )
        ]
    )
    kb = InlineKeyboardMarkup(rows)
    await _safe_helper_edit(
        client,
        query,
        t(_LANG, "admin.helpers.private_only"),
        reply_markup=kb,
        selected_handler=selected_handler,
    )
    return True


def _as_aware_utc(value):
    if value is None:
        return None
    tzinfo = getattr(value, "tzinfo", None)
    if tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _helper_available_for_panel(helper, now: datetime) -> bool:
    if getattr(helper, "status", None) != "active":
        return False
    current = int(getattr(helper, "current_active_calls", 0) or 0)
    max_calls = int(getattr(helper, "max_concurrent_calls", 0) or 0)
    if max_calls <= 0 or current >= max_calls:
        return False
    cooldown_until = _as_aware_utc(getattr(helper, "cooldown_until", None))
    if cooldown_until is not None and cooldown_until > now:
        return False
    banned_until = _as_aware_utc(getattr(helper, "banned_until", None))
    if banned_until is not None and banned_until > now:
        return False
    return True


async def _get_helper_panel_summary() -> HelperPanelSummary:
    helpers = await HelperPoolService.get_all_helpers()
    now = datetime.now(timezone.utc)
    active = sum(1 for h in helpers if getattr(h, "status", None) == "active")
    disabled = sum(1 for h in helpers if getattr(h, "status", None) == "disabled")
    quarantined = sum(1 for h in helpers if getattr(h, "status", None) == "quarantined")
    available = sum(1 for h in helpers if _helper_available_for_panel(h, now))
    return HelperPanelSummary(
        total=len(helpers),
        active=active,
        disabled=disabled,
        quarantined=quarantined,
        available=available,
    )


async def _get_counts() -> tuple[int, int, int]:
    summary = await _get_helper_panel_summary()
    return summary.active, summary.disabled, summary.quarantined


def _helper_home_text(summary: HelperPanelSummary) -> str:
    lines = [
        t(_LANG, "admin.helpers.title"),
        "",
        t(
            _LANG,
            "admin.helpers.summary_detailed",
            total=summary.total,
            active=summary.active,
            disabled=summary.disabled,
            quarantined=summary.quarantined,
            available=summary.available,
        ),
    ]
    if summary.total == 0:
        lines.extend(["", t(_LANG, "admin.helpers.no_helpers_notice")])
    elif summary.quarantined == summary.total:
        lines.extend(["", t(_LANG, "admin.helpers.all_quarantined_notice")])
    elif summary.available == 0:
        lines.extend(["", t(_LANG, "admin.helpers.no_available_notice")])
    return "\n".join(lines)


async def _clear_helper_wizard_states(user_id: int) -> None:
    r = await get_redis()
    await r.delete(
        helper_otp_state_key(user_id),
        helper_proxy_state_key(user_id),
        wizard_return_key(user_id),
    )


async def render_helper_home_panel(client: Client, query: CallbackQuery, *, answer: bool = True) -> bool:
    """Render helper home via panel_callback_edit (dev shortcut + hlp:home)."""
    chat_type, user_id, message_chat_id, data = _callback_context(query)
    if query.message and query.message.chat and query.from_user:
        await safe_stop_listening(
            client,
            query.message.chat.id,
            user_id=query.from_user.id,
        )
    trace_callback_event("hlp_home.received", query, handler="render_helper_home_panel")
    if user_id is None:
        return False
    try:
        await _clear_helper_wizard_states(user_id)
        summary = await _get_helper_panel_summary()
        trace_helper_summary(
            query,
            helpers_total=summary.total,
            active=summary.active,
            disabled=summary.disabled,
            quarantined=summary.quarantined,
            available=summary.available,
        )
    except Exception as exc:
        trace_callback_event(
            "hlp_home.exception",
            query,
            level="error",
            handler="render_helper_home_panel",
            phase="load_or_cleanup",
            error=exc,
        )
        logger.warning(
            "callback helper_home load failed data=%s chat_type=%s user_id=%s "
            "message_chat_id=%s exc=%s",
            callback_data_prefix(data),
            chat_type,
            user_id,
            message_chat_id,
            safe_exc_name(exc),
        )
        return await panel_callback_edit(
            client,
            query,
            t(_LANG, "admin.helpers.panel_render_failed"),
            build_done_kb(_LANG, TOKEN_HELPER_HOME),
            answer=answer,
        )
    return await panel_callback_edit(
        client,
        query,
        _helper_home_text(summary),
        KeyboardFactory.helper_home(
            _LANG,
            summary.active,
            summary.disabled,
            summary.quarantined,
        ),
        answer=answer,
    )


async def _fetch_helper(helper_id: int):
    from sqlalchemy import select

    from app.database.engine import async_session
    from app.database.models import HelperAccount

    async with async_session() as session:
        result = await session.execute(
            select(HelperAccount).where(HelperAccount.id == helper_id)
        )
        return result.scalar_one_or_none()


def _helper_detail_text(helper) -> str:
    text = t(
        _LANG,
        "admin.helpers.detail_title",
        id=helper.id,
        status=localized_label(_LANG, "helper_status", helper.status),
        calls=helper.current_active_calls,
        max_calls=helper.max_concurrent_calls,
        q_count=helper.quarantine_count,
    )
    if helper.device_model:
        text += "\n\n" + t(
            _LANG,
            "admin.helpers.detail_antiban",
            device=helper.device_model,
            system=helper.system_version or "—",
            app=helper.app_version or "—",
        )
    if helper.proxy_host:
        text += "\n\n" + t(
            _LANG,
            "admin.helpers.detail_proxy",
            proxy=f"{helper.proxy_type}://{helper.proxy_host}:{helper.proxy_port}",
        )
    return text


async def _render_helper_detail(
    query: CallbackQuery,
    helper_id: int,
    *,
    client: Client | None = None,
    selected_handler: str = "helper_detail",
) -> bool:
    helper = await _fetch_helper(helper_id)
    if helper is None:
        await _safe_helper_edit(
            client,
            query,
            t(_LANG, "common.errors.try_later"),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t(_LANG, "common.buttons.back"),
                            callback_data=CB["HLP_HOME"],
                        )
                    ],
                ]
            ),
            selected_handler=selected_handler,
        )
        return False
    await _safe_helper_edit(
        client,
        query,
        _helper_detail_text(helper),
        reply_markup=KeyboardFactory.helper_detail(_LANG, helper.id, helper.status),
        selected_handler=selected_handler,
    )
    return True


def _parse_helper_action_bound(data: str, prefix: str) -> tuple[int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix) :].split(":")
    if len(parts) != 3:
        return None
    try:
        helper_id = int(parts[0])
        user_id = int(parts[1])
        issued_at = int(parts[2])
    except ValueError:
        return None
    if helper_id <= 0 or user_id <= 0 or issued_at <= 0:
        return None
    return helper_id, user_id, issued_at


_PROXY_INPUT_RE = re.compile(
    r"^(socks5|socks4|http|mtproto)://(?:([^:@]+):([^@]+)@)?([A-Za-z0-9._-]+):(\d+)$",
    re.IGNORECASE,
)


def _looks_like_proxy_text(text: str | None) -> bool:
    """Return True when text resembles helper proxy wizard input."""
    normalized = (text or "").strip()
    if not normalized or normalized.startswith("/"):
        return False
    if normalized.lower() == "none":
        return True
    return bool(_PROXY_INPUT_RE.match(normalized))


async def _reply_proxy_session_expired(message: Message, *, reason: str) -> None:
    """Notify developer that the helper proxy Redis session is no longer valid."""
    logger.debug(
        "Helper proxy session expired message shown user_id=%s reason=%s",
        message.from_user.id,
        reason,
    )
    message_vars = getattr(message, "__dict__", {})
    client = (
        message_vars.get("_client")
        if isinstance(message_vars, dict) and "_client" in message_vars
        else None
    )
    if client is None:
        await message.reply(
            t(_LANG, "admin.helpers.proxy_session_expired"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        return
    await deliver_panel_outcome(
        client,
        message.chat.id,
        message.from_user.id,
        t(_LANG, "admin.helpers.proxy_session_expired"),
        build_done_kb(_LANG, TOKEN_HELPER_HOME),
    )


def _proxy_prompt_kb(helper_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.back"),
                    callback_data=f"{CB['HLP_DETAIL_PREFIX']}{helper_id}",
                )
            ],
            [
                compatible_inline_button(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data="hlp:proxy:cancel",
                )
            ],
        ]
    )


async def _deliver_proxy_input_outcome(
    client: Client,
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if not any(
        callable(getattr(client, name, None))
        for name in ("edit_message_text", "edit_message_caption", "send_message")
    ):
        await message.reply(text, reply_markup=reply_markup)
        return
    await deliver_panel_outcome(
        client,
        message.chat.id,
        message.from_user.id,
        text,
        reply_markup,
    )


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    # Route on callback identity only. Permission/scope is enforced INSIDE the
    # handler (`@developer_only` -> explicit no_access, `_guard_private` ->
    # explicit private_only). Guarding at the FILTER level with `_pm_dev` makes
    # the dispatcher skip the handler when the filter denies (e.g. a stale panel
    # where `query.message is None`), leaking the tap to the `^hlp:` fallback.
    @bot.on_callback_query(filters.regex(f"^{CB['HLP_HOME']}$"))
    @developer_only
    async def hlp_home(client: Client, query: CallbackQuery):
        chat_type, user_id, message_chat_id, data = _callback_context(query)
        trace_callback_event(
            "hlp_home.context",
            query,
            handler="hlp_home",
            chat_type=chat_type,
            message_chat_id=message_chat_id,
        )
        if await _guard_private(
            query,
            client=client,
            selected_handler="helper_home",
        ):
            trace_callback_event(
                "hlp_home.guard_denied",
                query,
                level="warning",
                handler="hlp_home",
                guard="private_chat",
                result="denied",
                reason="wrong_chat_type",
            )
            return
        await _safe_answer(query)
        await render_helper_home_panel(client, query, answer=False)

    # Gold-standard route: identity-only filter + terminal PANEL group +
    # in-handler dev/private guards. Terminal group StopPropagates so the tap
    # never reaches a group-0 fallback; identity filter means a stale panel
    # cannot skip the handler either (see hlp_home note).
    @bot.on_callback_query(
        filters.regex(f"^{CB['HLP_ADD']}$"),
        group=PANEL_CALLBACK_GROUP,
    )
    @developer_only
    async def hlp_add(client: Client, query: CallbackQuery):
        trace_callback_event("helper_add.clicked", query, handler="hlp_add")
        if await _guard_private(query, client=client, selected_handler="hlp_add"):
            trace_callback_event(
                "helper_add.guard_denied",
                query,
                level="warning",
                handler="hlp_add",
                guard="private_chat",
                result="denied",
            )
            return
        await _safe_answer(query)
        trace_callback_event(
            "helper_add.answer_sent", query, handler="hlp_add", result="success"
        )
        await _clear_helper_wizard_states(query.from_user.id)
        await remember_return_token(query.from_user.id, TOKEN_HELPER_HOME)
        trace_callback_event(
            "helper_add.wizard_started", query, handler="hlp_add", result="method_menu"
        )
        await _safe_helper_edit(
            client,
            query,
            t(_LANG, "admin.helpers.add_method_title"),
            reply_markup=KeyboardFactory.helper_add_menu(_LANG),
            selected_handler="hlp_add",
        )

    @bot.on_callback_query(filters.regex(f"^{CB['HLP_LIST']}$"))
    @developer_only
    async def hlp_list(client: Client, query: CallbackQuery):
        if await _guard_private(query, client=client, selected_handler="hlp_list"):
            return
        await _safe_answer(query)
        await _clear_helper_wizard_states(query.from_user.id)
        helpers = await HelperPoolService.get_all_helpers()
        if not helpers:
            await _safe_helper_edit(
                client,
                query,
                t(_LANG, "admin.helpers.list_empty"),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                t(_LANG, "common.buttons.back"),
                                callback_data=CB["HLP_HOME"],
                            )
                        ],
                    ]
                ),
                selected_handler="hlp_list",
            )
            return
        rows = []
        for h in helpers:
            badge = {"active": "✅", "disabled": "❌", "quarantined": "⚠️"}.get(
                h.status, "❓"
            )
            label = t(
                _LANG,
                "admin.helpers.list_item",
                id=h.id,
                status_badge=badge,
                calls=h.current_active_calls,
                max_calls=h.max_concurrent_calls,
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        label, callback_data=f"{CB['HLP_DETAIL_PREFIX']}{h.id}"
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.back"), callback_data=CB["HLP_HOME"]
                )
            ]
        )
        await _safe_helper_edit(
            client,
            query,
            t(_LANG, "admin.helpers.title"),
            reply_markup=InlineKeyboardMarkup(rows),
            selected_handler="hlp_list",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:d:\d+$") & _pm_dev)
    @developer_only
    async def hlp_detail(client: Client, query: CallbackQuery):
        if await _guard_private(query, client=client, selected_handler="hlp_detail"):
            return
        helper_id = _parse_prefixed_positive_int(query.data, CB["HLP_DETAIL_PREFIX"])
        if helper_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await _safe_answer(query)
        await _clear_helper_wizard_states(query.from_user.id)
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:proxy:\d+$") & _pm_dev)
    @developer_only
    async def hlp_set_proxy_start(client: Client, query: CallbackQuery):
        from app.utils.ask_result import safe_stop_listening

        if await _guard_private(
            query, client=client, selected_handler="hlp_set_proxy_start"
        ):
            return
        helper_id = _parse_prefixed_positive_int(query.data, CB["HLP_SET_PROXY_PREFIX"])
        if helper_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await _safe_answer(query)
        await safe_stop_listening(
            client, query.message.chat.id, user_id=query.from_user.id
        )
        logger.debug(
            "Helper proxy start clicked user_id=%s helper_id=%s",
            query.from_user.id,
            helper_id,
        )
        await _clear_helper_wizard_states(query.from_user.id)
        r = await get_redis()
        import json as _json

        await r.set(
            helper_proxy_state_key(query.from_user.id),
            _json.dumps(
                {
                    "step": "awaiting_proxy",
                    "helper_id": helper_id,
                    "panel_chat_id": query.message.chat.id,
                    "panel_message_id": (
                        getattr(query.message, "id", None)
                        or getattr(query.message, "message_id", None)
                    ),
                }
            ),
            ex=300,
        )
        logger.debug(
            "Helper proxy state written user_id=%s helper_id=%s",
            query.from_user.id,
            helper_id,
        )
        await remember_return_token(query.from_user.id, TOKEN_HELPER_HOME)
        await _safe_helper_edit(
            client,
            query,
            t(_LANG, "admin.helpers.proxy_ask"),
            reply_markup=_proxy_prompt_kb(helper_id),
            selected_handler="hlp_set_proxy_start",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:proxy:cancel$") & _pm_dev)
    @developer_only
    async def hlp_proxy_cancel(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_proxy_cancel"
        ):
            return
        await _safe_answer(query)
        await _clear_helper_wizard_states(query.from_user.id)
        await _safe_helper_edit(
            client,
            query,
            t(_LANG, "common.cancelled"),
            reply_markup=build_done_kb(_LANG, TOKEN_HELPER_HOME),
            selected_handler="hlp_proxy_cancel",
        )

    @bot.on_message(
        filters.private & filters.text & _pm_dev,
        group=HELPER_PROXY_INPUT_GROUP,
    )
    async def hlp_proxy_input(client: Client, message: Message):
        import json as _json

        from app.services.wizard_ui import (
            clear_runtime_state,
            clear_wizard_and_allow_command,
        )

        logger.debug(
            "Helper proxy input handler entered user_id=%s", message.from_user.id
        )
        if await clear_wizard_and_allow_command(client, message, lang=_LANG):
            return
        if (message.text or "").startswith("/"):
            await clear_runtime_state(
                client,
                message.from_user.id,
                message.chat.id,
            )
            return
        r = await get_redis()
        active_wizard, _ = await detect_active_wizard_state_in_redis(
            r,
            message.from_user.id,
            heal_conflicts=True,
        )
        if active_wizard and active_wizard != WIZARD_HELPER_PROXY:
            return
        text = message.text.strip()
        raw = await r.get(helper_proxy_state_key(message.from_user.id))
        if not raw:
            return_token = await r.get(wizard_return_key(message.from_user.id))
            if _looks_like_proxy_text(text) and return_token == TOKEN_HELPER_HOME:
                await safe_delete_user_input(message)
                await _reply_proxy_session_expired(message, reason="missing_state")
                await r.delete(wizard_return_key(message.from_user.id))
                await safe_stop_propagation(message)
            return
        state = _json.loads(raw)
        if state.get("step") != "awaiting_proxy":
            if _looks_like_proxy_text(text):
                await _reply_proxy_session_expired(message, reason="wrong_step")
                await safe_stop_propagation(message)
            return

        helper_id = state["helper_id"]
        await safe_delete_user_input(message)

        from sqlalchemy import update as sa_update

        from app.database.engine import async_session
        from app.database.models import HelperAccount

        if text.lower() == "none":
            async with async_session() as session:
                async with session.begin():
                    await session.execute(
                        sa_update(HelperAccount)
                        .where(HelperAccount.id == helper_id)
                        .values(
                            proxy_type=None,
                            proxy_host=None,
                            proxy_port=None,
                            proxy_username=None,
                            proxy_password=None,
                        )
                    )
            await r.delete(helper_proxy_state_key(message.from_user.id))
            await _deliver_proxy_input_outcome(
                client,
                message,
                t(_LANG, "admin.helpers.proxy_removed", id=helper_id),
                build_done_kb(_LANG, TOKEN_HELPER_HOME),
            )
            await safe_stop_propagation(message)
            return

        m = _PROXY_INPUT_RE.match(text)
        if not m:
            await _deliver_proxy_input_outcome(
                client,
                message,
                t(_LANG, "admin.helpers.proxy_invalid"),
                _proxy_prompt_kb(helper_id),
            )
            await safe_stop_propagation(message)
            return

        p_type, p_user, p_pass, p_host, p_port = m.groups()
        proxy_port = parse_bounded_int(p_port, min_value=1, max_value=65_535)
        if proxy_port is None:
            await _deliver_proxy_input_outcome(
                client,
                message,
                t(_LANG, "admin.helpers.proxy_invalid"),
                _proxy_prompt_kb(helper_id),
            )
            await safe_stop_propagation(message)
            return
        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    sa_update(HelperAccount)
                    .where(HelperAccount.id == helper_id)
                    .values(
                        proxy_type=p_type.upper(),
                        proxy_host=p_host,
                        proxy_port=proxy_port,
                        proxy_username=p_user,
                        proxy_password=p_pass,
                    )
                )
        await r.delete(helper_proxy_state_key(message.from_user.id))
        await _deliver_proxy_input_outcome(
            client,
            message,
            t(
                _LANG,
                "admin.helpers.proxy_set_ok",
                id=helper_id,
                proxy=f"{p_type}://{p_host}:{p_port}",
            ),
            build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )
        await safe_stop_propagation(message)

    @bot.on_callback_query(filters.regex(r"^hlp:en:\d+$") & _pm_dev)
    @developer_only
    async def hlp_enable(client: Client, query: CallbackQuery):
        if await _guard_private(query, client=client, selected_handler="hlp_enable"):
            return
        helper_id = _parse_prefixed_positive_int(query.data, CB["HLP_ENABLE"])
        if helper_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await _safe_answer(query)
        issued_at = int(time.time())
        await _safe_helper_edit(
            client,
            query,
            t(
                _LANG,
                "admin.helpers.action_confirm",
                action=t(_LANG, "admin.helpers.enable_btn"),
                id=helper_id,
            ),
            reply_markup=KeyboardFactory.helper_state_action_confirm(
                _LANG,
                CB["HLP_ENABLE_CONFIRM_PREFIX"],
                CB["HLP_ENABLE_ABORT_PREFIX"],
                helper_id,
                query.from_user.id,
                issued_at,
            ),
            selected_handler="hlp_enable",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:en:do:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_enable_confirm(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_enable_confirm"
        ):
            return
        parsed = _parse_helper_action_bound(query.data, CB["HLP_ENABLE_CONFIRM_PREFIX"])
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await HelperPoolService.activate_helper(helper_id)
        await helper_event_repo.log_event(
            "helper.enable", actor="panel", helper_account_id=helper_id
        )
        await query.answer(
            t(_LANG, "admin.helpers.enabled", id=helper_id), show_alert=True
        )
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:en:no:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_enable_abort(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_enable_abort"
        ):
            return
        parsed = _parse_helper_action_bound(query.data, CB["HLP_ENABLE_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:dis:\d+$") & _pm_dev)
    @developer_only
    async def hlp_disable(client: Client, query: CallbackQuery):
        if await _guard_private(query, client=client, selected_handler="hlp_disable"):
            return
        helper_id = _parse_prefixed_positive_int(query.data, CB["HLP_DISABLE"])
        if helper_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await _safe_answer(query)
        issued_at = int(time.time())
        await _safe_helper_edit(
            client,
            query,
            t(
                _LANG,
                "admin.helpers.action_confirm",
                action=t(_LANG, "admin.helpers.disable_btn"),
                id=helper_id,
            ),
            reply_markup=KeyboardFactory.helper_state_action_confirm(
                _LANG,
                CB["HLP_DISABLE_CONFIRM_PREFIX"],
                CB["HLP_DISABLE_ABORT_PREFIX"],
                helper_id,
                query.from_user.id,
                issued_at,
            ),
            selected_handler="hlp_disable",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:dis:do:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_disable_confirm(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_disable_confirm"
        ):
            return
        parsed = _parse_helper_action_bound(
            query.data, CB["HLP_DISABLE_CONFIRM_PREFIX"]
        )
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        from sqlalchemy import update

        from app.database.engine import async_session
        from app.database.models import HelperAccount

        async with async_session() as session:
            async with session.begin():
                await session.execute(
                    update(HelperAccount)
                    .where(HelperAccount.id == helper_id)
                    .values(status="disabled")
                )
        await helper_event_repo.log_event(
            "helper.disable", actor="panel", helper_account_id=helper_id
        )
        await query.answer(
            t(_LANG, "admin.helpers.disabled", id=helper_id), show_alert=True
        )
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:dis:no:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_disable_abort(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_disable_abort"
        ):
            return
        parsed = _parse_helper_action_bound(query.data, CB["HLP_DISABLE_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:q:\d+$") & _pm_dev)
    @developer_only
    async def hlp_quarantine(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_quarantine"
        ):
            return
        helper_id = _parse_prefixed_positive_int(query.data, CB["HLP_QUARANTINE"])
        if helper_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await _safe_answer(query)
        issued_at = int(time.time())
        await _safe_helper_edit(
            client,
            query,
            t(
                _LANG,
                "admin.helpers.action_confirm",
                action=t(_LANG, "admin.helpers.quarantine_btn"),
                id=helper_id,
            ),
            reply_markup=KeyboardFactory.helper_state_action_confirm(
                _LANG,
                CB["HLP_QUARANTINE_CONFIRM_PREFIX"],
                CB["HLP_QUARANTINE_ABORT_PREFIX"],
                helper_id,
                query.from_user.id,
                issued_at,
            ),
            selected_handler="hlp_quarantine",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:q:do:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_quarantine_confirm(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_quarantine_confirm"
        ):
            return
        parsed = _parse_helper_action_bound(
            query.data, CB["HLP_QUARANTINE_CONFIRM_PREFIX"]
        )
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await HelperPoolService.quarantine_helper(helper_id, "manual_panel", 3600)
        await helper_event_repo.log_event(
            "helper.quarantine", actor="panel", helper_account_id=helper_id
        )
        await query.answer(
            t(_LANG, "admin.helpers.quarantined", id=helper_id), show_alert=True
        )
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:q:no:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_quarantine_abort(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_quarantine_abort"
        ):
            return
        parsed = _parse_helper_action_bound(
            query.data, CB["HLP_QUARANTINE_ABORT_PREFIX"]
        )
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:uq:\d+$") & _pm_dev)
    @developer_only
    async def hlp_unquarantine(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_unquarantine"
        ):
            return
        helper_id = _parse_prefixed_positive_int(query.data, CB["HLP_UNQUARANTINE"])
        if helper_id is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await _safe_answer(query)
        issued_at = int(time.time())
        await _safe_helper_edit(
            client,
            query,
            t(
                _LANG,
                "admin.helpers.action_confirm",
                action=t(_LANG, "admin.helpers.unquarantine_btn"),
                id=helper_id,
            ),
            reply_markup=KeyboardFactory.helper_state_action_confirm(
                _LANG,
                CB["HLP_UNQUARANTINE_CONFIRM_PREFIX"],
                CB["HLP_UNQUARANTINE_ABORT_PREFIX"],
                helper_id,
                query.from_user.id,
                issued_at,
            ),
            selected_handler="hlp_unquarantine",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:uq:do:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_unquarantine_confirm(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_unquarantine_confirm"
        ):
            return
        parsed = _parse_helper_action_bound(
            query.data, CB["HLP_UNQUARANTINE_CONFIRM_PREFIX"]
        )
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await HelperPoolService.activate_helper(helper_id)
        await helper_event_repo.log_event(
            "helper.unquarantine", actor="panel", helper_account_id=helper_id
        )
        await query.answer(
            t(_LANG, "admin.helpers.unquarantined", id=helper_id), show_alert=True
        )
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(filters.regex(r"^hlp:uq:no:\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_unquarantine_abort(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_unquarantine_abort"
        ):
            return
        parsed = _parse_helper_action_bound(
            query.data, CB["HLP_UNQUARANTINE_ABORT_PREFIX"]
        )
        if parsed is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        helper_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_helper_detail(query, helper_id)

    @bot.on_callback_query(
        filters.regex(r"^hlp:(?:en|dis|q|uq):(?:do|no):.*") & _pm_dev
    )
    async def hlp_state_action_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        prefixes = (
            CB["HLP_ENABLE_CONFIRM_PREFIX"],
            CB["HLP_ENABLE_ABORT_PREFIX"],
            CB["HLP_DISABLE_CONFIRM_PREFIX"],
            CB["HLP_DISABLE_ABORT_PREFIX"],
            CB["HLP_QUARANTINE_CONFIRM_PREFIX"],
            CB["HLP_QUARANTINE_ABORT_PREFIX"],
            CB["HLP_UNQUARANTINE_CONFIRM_PREFIX"],
            CB["HLP_UNQUARANTINE_ABORT_PREFIX"],
        )
        for prefix in prefixes:
            if _parse_helper_action_bound(query.data, prefix) is not None:
                return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    # Identity-only filter; dev+private enforced in-handler (see hlp_home note).
    @bot.on_callback_query(filters.regex(f"^{CB['HLP_ROTATE_KEY']}$"))
    @developer_only
    async def hlp_rotate_key(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_rotate_key"
        ):
            return
        await _safe_answer(query)
        issued_at = int(time.time())
        await _safe_helper_edit(
            client,
            query,
            t(_LANG, "admin.helpers.rotate_key_confirm"),
            reply_markup=KeyboardFactory.helper_rotate_key_confirm(
                _LANG,
                query.from_user.id,
                issued_at,
            ),
            selected_handler="hlp_rotate_key",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:rotkey:confirm:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_rotate_key_confirm(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_rotate_key_confirm"
        ):
            return
        payload = _parse_helper_rotate_token(
            query.data, CB["HLP_ROTATE_CONFIRM_PREFIX"]
        )
        if payload is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        user_id, issued_at = payload
        if user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer()
        rotated, errors = await _rotate_helper_session_keys()
        await helper_event_repo.log_event(
            "helper.rotate_key",
            actor="panel",
            metadata={"rotated": rotated, "errors": errors},
        )
        await deliver_panel_outcome(
            client,
            query.message.chat.id,
            query.from_user.id,
            t(_LANG, "admin.helpers.rotate_key_done", rotated=rotated, errors=errors),
            build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )

    @bot.on_callback_query(filters.regex(r"^hlp:rotkey:cancel:\d+:\d+$") & _pm_dev)
    @developer_only
    async def hlp_rotate_key_cancel(client: Client, query: CallbackQuery):
        if await _guard_private(
            query, client=client, selected_handler="hlp_rotate_key_cancel"
        ):
            return
        payload = _parse_helper_rotate_token(query.data, CB["HLP_ROTATE_CANCEL_PREFIX"])
        if payload is None:
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        user_id, issued_at = payload
        if user_id != query.from_user.id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_helper_rotate_token(issued_at):
            await query.answer(
                t(_LANG, "common.errors.invalid_callback"), show_alert=True
            )
            return
        await query.answer(
            t(_LANG, "admin.helpers.rotate_key_cancelled"), show_alert=True
        )
        await _safe_helper_edit(
            client,
            query,
            t(_LANG, "admin.helpers.rotate_key_cancelled"),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t(_LANG, "common.buttons.back"),
                            callback_data=CB["HLP_HOME"],
                        )
                    ],
                ]
            ),
            selected_handler="hlp_rotate_key_cancel",
        )

    @bot.on_callback_query(
        filters.regex(r"^hlp:rotkey:(?:confirm|cancel):(?!\d+:\d+$)") & _pm_dev
    )
    async def hlp_rotate_key_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['HLP_HEALTH_CHECK']}$"))
    @developer_only
    async def hlp_health(client: Client, query: CallbackQuery):
        if await _guard_private(query, client=client, selected_handler="hlp_health"):
            return
        await _safe_answer(query)
        helpers = await HelperPoolService.get_all_helpers()
        ok = fail = 0
        for h in helpers:
            if h.status not in ("active", "quarantined"):
                continue
            session_str = await HelperPoolService.get_helper_session(h.id)
            if session_str is None:
                fail += 1
                continue
            try:
                from pyrogram import Client as PClient

                pc = PClient(
                    name=instance_session_name(f"hp_{h.id}"),
                    api_id=1,
                    api_hash="x",
                    session_string=session_str,
                    in_memory=True,
                )
                async with pc:
                    await pc.get_me()
                ok += 1
            except Exception:
                fail += 1
                await HelperPoolService.quarantine_helper(
                    h.id, "health_check_fail", 1800
                )
        await helper_event_repo.log_event(
            "helper.health_check", actor="panel", metadata={"ok": ok, "fail": fail}
        )
        await deliver_panel_outcome(
            client,
            query.message.chat.id,
            query.from_user.id,
            t(_LANG, "admin.helpers.health_done", ok=ok, fail=fail),
            build_done_kb(_LANG, TOKEN_HELPER_HOME),
        )

    # Identity-only filter; dev+private enforced in-handler (see hlp_home note).
    @bot.on_callback_query(filters.regex(f"^{CB['HLP_STATS']}$"))
    @developer_only
    async def hlp_stats(client: Client, query: CallbackQuery):
        if await _guard_private(query, client=client, selected_handler="hlp_stats"):
            return
        await _safe_answer(query)
        helpers = await HelperPoolService.get_all_helpers()
        active = sum(1 for h in helpers if h.status == "active")
        disabled = sum(1 for h in helpers if h.status == "disabled")
        quarantined = sum(1 for h in helpers if h.status == "quarantined")
        from sqlalchemy import func, select

        from app.database.engine import async_session
        from app.database.models import HelperChatBinding

        async with async_session() as s:
            bindings = (
                await s.execute(select(func.count()).select_from(HelperChatBinding))
            ).scalar() or 0
        text = t(
            _LANG,
            "admin.helpers.stats_title",
            total=len(helpers),
            active=active,
            disabled=disabled,
            quarantined=quarantined,
            bindings=bindings,
        )
        await _safe_helper_edit(
            client,
            query,
            text,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t(_LANG, "common.buttons.back"),
                            callback_data=CB["HLP_HOME"],
                        )
                    ],
                ]
            ),
            selected_handler="hlp_stats",
        )

    @bot.on_callback_query(filters.regex(r"^hlp:"))
    async def hlp_unknown_or_denied(client: Client, query: CallbackQuery):  # noqa: ARG001
        chat_type, user_id, message_chat_id, data = _callback_context(query)
        if user_id is None or not is_developer(user_id):
            trace_guard(
                query, guard="developer_only", result="deny", reason="not_developer"
            )
            logger.info(
                "callback received data=%s chat_type=%s user_id=%s message_chat_id=%s "
                "selected_handler=helper_fallback guard_result=deny",
                data,
                chat_type,
                user_id,
                message_chat_id,
            )
            await _safe_answer(
                query, t(_LANG, "common.errors.no_access"), show_alert=True
            )
            return
        if not _is_private(query) and _is_known_helper_callback(data):
            trace_guard(
                query,
                guard="private_chat",
                result="deny",
                reason="wrong_scope",
                handler="helper_fallback",
            )
            logger.info(
                "callback received data=%s chat_type=%s user_id=%s message_chat_id=%s "
                "selected_handler=helper_fallback guard_result=wrong_scope",
                data,
                chat_type,
                user_id,
                message_chat_id,
            )
            await _safe_answer(
                query, t(_LANG, "admin.helpers.private_only"), show_alert=True
            )
            return
        trace_unhandled(query, reason="unknown_helper_callback")
        logger.info(
            "callback received data=%s chat_type=%s user_id=%s message_chat_id=%s "
            "route=post_dispatch_unhandled handler=none prefix=hlp",
            data,
            chat_type,
            user_id,
            message_chat_id,
        )
        await _safe_answer(
            query, t(_LANG, "common.errors.unknown_callback"), show_alert=True
        )
