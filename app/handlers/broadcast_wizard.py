"""Advanced broadcast wizard with FSM, checkbox targeting, and scheduling."""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

import jdatetime
from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.database.models import Broadcast
from app.handlers.priority import (
    BROADCAST_PAYLOAD_INPUT_GROUP,
    BROADCAST_SCHEDULE_INPUT_GROUP,
)
from app.repositories import broadcast_repo
from app.services.broadcast_service_v2 import BroadcastServiceV2
from app.utils.cache import get_redis
from app.utils.diagnostic_logging import create_logged_task
from app.utils.ask_result import safe_delete_user_input, safe_stop_propagation
from app.utils.button_style import mark_toggle_state
from app.utils.redis_keys import (
    TTL_BCW_CONFIRM_CLAIM,
    TTL_BCW_STATE,
    bcw_confirm_claim_key,
    bcw_state_key,
    wizard_return_key,
)
from app.utils.decorators import developer_only
from app.utils.filters import dev_filter, private_chat_filter
from app.utils.i18n import AUTO_LANG, label, t
from app.utils.ui import CB, compatible_inline_button
from app.services.panel_message_service import deliver_panel_outcome, panel_callback_edit
from app.services.wizard_ui import (
    TOKEN_DEV_BROADCAST,
    WIZARD_BROADCAST,
    build_done_kb,
    detect_active_wizard_state_in_redis,
    remember_return_token,
    resolve_navigation_payload,
    safe_edit_navigation_message,
)

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_pm_dev = dev_filter() & private_chat_filter()


# ── Redis-backed FSM state ──────────────────────────────────────────────

async def _set_state(user_id: int, state: dict) -> None:
    r = await get_redis()
    await r.set(bcw_state_key(user_id), json.dumps(state, default=str), ex=TTL_BCW_STATE)


async def _get_state(user_id: int) -> dict | None:
    r = await get_redis()
    raw = await r.get(bcw_state_key(user_id))
    return json.loads(raw) if raw else None


async def _clear_state(user_id: int) -> None:
    r = await get_redis()
    await r.delete(bcw_state_key(user_id), wizard_return_key(user_id))


async def begin_broadcast_wizard(
    client: Client,
    query: CallbackQuery,
    *,
    legacy_notice: bool = False,
) -> None:
    """Start the advanced broadcast wizard (step 1: capture payload)."""
    from app.utils.ask_result import safe_stop_listening

    await safe_stop_listening(client, query.message.chat.id, user_id=query.from_user.id)
    logger.debug("BCW start clicked user_id=%s legacy=%s", query.from_user.id, legacy_notice)
    prompt = t(_LANG, "broadcast.wizard.step1_prompt")
    if legacy_notice:
        prompt = t(_LANG, "broadcast.legacy_redirect_notice") + "\n\n" + prompt
    try:
        await _clear_state(query.from_user.id)
        await remember_return_token(query.from_user.id, TOKEN_DEV_BROADCAST)
        await _set_state(
            query.from_user.id,
            {
                "wizard_id": secrets.token_urlsafe(12),
                "step": "awaiting_payload",
                "panel_chat_id": query.message.chat.id,
                "panel_message_id": (
                    getattr(query.message, "id", None)
                    or getattr(query.message, "message_id", None)
                ),
            },
        )
    except Exception:
        logger.warning(
            "BCW start state unavailable user_id=%s",
            query.from_user.id,
            exc_info=True,
        )
        await panel_callback_edit(
            client,
            query,
            t(_LANG, "common.errors.try_later"),
            build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
        )
        return
    ok = await panel_callback_edit(
        client,
        query,
        prompt,
        InlineKeyboardMarkup([
            [InlineKeyboardButton(t(_LANG, "broadcast.wizard.cancel_btn"), callback_data=CB["BCW_CANCEL"])],
        ]),
    )
    if not ok:
        await _clear_state(query.from_user.id)
        return
    logger.debug("BCW state written user_id=%s step=awaiting_payload", query.from_user.id)


async def _handle_missing_state(client: Client, query: CallbackQuery) -> None:
    await query.answer(t(_LANG, "broadcast.wizard.expired"), show_alert=True)
    await _edit_bcw_message(
        client,
        query,
        t(_LANG, "broadcast.wizard.expired"),
        build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
    )


async def _get_required_state(client: Client, query: CallbackQuery) -> dict | None:
    try:
        state = await _get_state(query.from_user.id)
    except Exception:
        logger.warning(
            "BCW state unavailable user_id=%s",
            query.from_user.id,
            exc_info=True,
        )
        await query.answer(t(_LANG, "common.errors.try_later"), show_alert=True)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "common.errors.try_later"),
            build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
        )
        return None
    if not state:
        await _handle_missing_state(client, query)
        return None
    await query.answer()
    return state


async def _claim_confirmation(user_id: int, state: dict) -> bool:
    wizard_id = str(state.get("wizard_id") or "").strip()
    if not wizard_id:
        # Compatibility for a state created immediately before deployment.
        wizard_id = (
            f"legacy-{state.get('panel_chat_id', 'x')}-"
            f"{state.get('panel_message_id', 'x')}-"
            f"{state.get('admin_msg_id', 'x')}"
        )
    redis = await get_redis()
    claimed = await redis.set(
        bcw_confirm_claim_key(user_id, wizard_id),
        "1",
        nx=True,
        ex=TTL_BCW_CONFIRM_CLAIM,
    )
    return bool(claimed)


async def _edit_bcw_message(
    client: Client,
    query: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> bool:
    return await safe_edit_navigation_message(
        client,
        query.message,
        text,
        reply_markup=reply_markup,
        user_id=query.from_user.id,
    )


_BCW_MEDIA_ATTRS = (
    "photo", "video", "document", "audio",
    "animation", "voice", "video_note", "sticker",
)
_JALALI_SCHEDULE_RE = re.compile(r"^\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{1,2}$")
_HOURS_SCHEDULE_RE = re.compile(r"^\d{1,4}$")


def _looks_like_bcw_payload(message: Message) -> bool:
    """Return True when a message resembles broadcast wizard payload input."""
    if message.text and message.text.startswith("/"):
        return False
    if message.text:
        return True
    if message.caption:
        return True
    return any(getattr(message, attr, None) for attr in _BCW_MEDIA_ATTRS)


def _looks_like_schedule_text(text: str | None) -> bool:
    """Return True when text resembles datetime or hours schedule input."""
    normalized = (text or "").strip()
    if not normalized or normalized.startswith("/"):
        return False
    if _JALALI_SCHEDULE_RE.match(normalized):
        return True
    if _HOURS_SCHEDULE_RE.match(normalized):
        try:
            hours = int(normalized)
        except ValueError:
            return False
        return 1 <= hours <= 8760
    return False


async def _reply_bcw_session_expired(
    client: Client,
    message: Message,
    *,
    reason: str,
) -> None:
    """Notify developer that the broadcast wizard Redis session is no longer valid."""
    logger.debug(
        "BCW session expired message shown user_id=%s reason=%s",
        message.from_user.id,
        reason,
    )
    if not any(
        callable(getattr(client, name, None))
        for name in ("edit_message_text", "edit_message_caption", "send_message")
    ):
        await message.reply(
            t(_LANG, "broadcast.wizard.session_expired"),
            reply_markup=build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
        )
        return
    await deliver_panel_outcome(
        client,
        message.chat.id,
        message.from_user.id,
        t(_LANG, "broadcast.wizard.session_expired"),
        build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
    )


async def _deliver_bcw_input_outcome(
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


# ── Keyboard builders ───────────────────────────────────────────────────

def _back_btn(lang: str, cb: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(t(lang, "broadcast.wizard.back_btn"), callback_data=cb)]


def _cancel_btn(lang: str) -> list[InlineKeyboardButton]:
    return [
        compatible_inline_button(
            t(lang, "broadcast.wizard.cancel_btn"),
            callback_data=CB["BCW_CANCEL"],
        )
    ]


def _targets_keyboard(lang: str, selected: set) -> InlineKeyboardMarkup:
    def _icon(key: str) -> str:
        return t(lang, "broadcast.wizard.tgt_selected") if key in selected else t(lang, "broadcast.wizard.tgt_unselected")

    def _target_button(
        key: str,
        label_key: str,
        callback_data: str,
    ) -> InlineKeyboardButton:
        button = InlineKeyboardButton(
            f"{_icon(key)} {t(lang, label_key)}",
            callback_data=callback_data,
        )
        return mark_toggle_state(button, key in selected)

    return InlineKeyboardMarkup([
        [
            _target_button(
                "users",
                "broadcast.wizard.tgt_users",
                CB["BCW_TGT_USERS"],
            ),
            _target_button(
                "groups",
                "broadcast.wizard.tgt_groups",
                CB["BCW_TGT_GROUPS"],
            ),
        ],
        [
            _target_button(
                "channels",
                "broadcast.wizard.tgt_channels",
                CB["BCW_TGT_CHANNELS"],
            ),
        ],
        [InlineKeyboardButton(t(lang, "broadcast.wizard.tgt_next"), callback_data=CB["BCW_TGT_NEXT"])],
        _cancel_btn(lang) + _back_btn(lang, CB["BCW_BACK_MODE"]),
    ])


def _mode_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "broadcast.wizard.mode_send"), callback_data=CB["BCW_MODE_SEND"]),
            InlineKeyboardButton(t(lang, "broadcast.wizard.mode_fwd"), callback_data=CB["BCW_MODE_FWD"]),
        ],
        _cancel_btn(lang) + _back_btn(lang, CB["BCW_START"]),
    ])


def _filter_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "broadcast.wizard.filter_all"), callback_data=CB["BCW_FILTER_ALL"]),
            InlineKeyboardButton(t(lang, "broadcast.wizard.filter_7d"), callback_data=CB["BCW_FILTER_7D"]),
        ],
        [InlineKeyboardButton(t(lang, "broadcast.wizard.filter_30d"), callback_data=CB["BCW_FILTER_30D"])],
        _cancel_btn(lang) + _back_btn(lang, CB["BCW_BACK_TGT"]),
    ])


def _schedule_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "broadcast.wizard.send_now"), callback_data=CB["BCW_SEND_NOW"]),
            InlineKeyboardButton(t(lang, "broadcast.wizard.send_at"), callback_data=CB["BCW_SEND_AT"]),
        ],
        [
            InlineKeyboardButton(t(lang, "broadcast.wizard.send_after"), callback_data=CB["BCW_SEND_AFTER"]),
            InlineKeyboardButton(t(lang, "broadcast.wizard.send_recurring"), callback_data=CB["BCW_SEND_RECURRING"]),
        ],
        _cancel_btn(lang) + _back_btn(lang, CB["BCW_BACK_FILTER"]),
    ])


def _confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "broadcast.wizard.confirm_btn"), callback_data=CB["BCW_CONFIRM"])],
        _cancel_btn(lang) + _back_btn(lang, CB["BCW_BACK_SCHED"]),
    ])


def _build_confirm_text(state: dict) -> str:
    lang = _LANG
    mode_label = label(lang, "broadcast_mode", state.get("mode", "send"))
    targets = ", ".join(
        label(lang, "broadcast_scope", target)
        for target in state.get("targets", [])
    )
    flt = label(lang, "broadcast_filter", state.get("filter", "all"))
    schedule = state.get("schedule", "now")
    if schedule == "at":
        sched_label = state.get("run_at", "?")
    elif schedule == "after":
        sched_label = state.get("run_at", "?")
    elif schedule == "recurring":
        hours = state.get("interval_hours", "?")
        sched_label = (
            t(lang, "broadcast.wizard.schedule_every_hours", hours=hours)
            if hours != "?"
            else "?"
        )
    else:
        sched_label = t(lang, "broadcast.wizard.send_now")
    return t(
        lang,
        "broadcast.wizard.confirm_title",
        mode=mode_label,
        targets=targets,
        filter=flt,
        schedule=sched_label,
    )


# ── Handler registration ────────────────────────────────────────────────

def register(bot: Client, call_py) -> None:  # noqa: ARG001

    # ── Step 1: start / capture payload ──────────────────────────────

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_START']}$") & _pm_dev)
    @developer_only
    async def bcw_start(client: Client, query: CallbackQuery):
        await begin_broadcast_wizard(client, query)

    @bot.on_message(
        filters.private & dev_filter(),
        group=BROADCAST_PAYLOAD_INPUT_GROUP,
    )
    async def bcw_capture_payload(client: Client, message: Message):
        logger.debug("BCW payload handler entered user_id=%s", message.from_user.id)
        from app.services.wizard_ui import clear_runtime_state

        redis_client = await get_redis()
        active_wizard, _ = await detect_active_wizard_state_in_redis(
            redis_client,
            message.from_user.id,
            heal_conflicts=True,
        )
        if active_wizard and active_wizard != WIZARD_BROADCAST:
            return

        if message.text and message.text.startswith("/"):
            await clear_runtime_state(
                client,
                message.from_user.id,
                message.chat.id,
            )
            return

        state = await _get_state(message.from_user.id)
        step = state.get("step") if state else None
        if step != "awaiting_payload":
            if _looks_like_bcw_payload(message):
                if state is None:
                    return_token = await redis_client.get(
                        wizard_return_key(message.from_user.id),
                    )
                    if return_token == TOKEN_DEV_BROADCAST:
                        await _reply_bcw_session_expired(
                            client,
                            message,
                            reason="missing_state",
                        )
                        await redis_client.delete(
                            wizard_return_key(message.from_user.id)
                        )
                        await safe_stop_propagation(message)
                elif not state.get("stale_input_notified"):
                    state["stale_input_notified"] = True
                    await _set_state(message.from_user.id, state)
                    await _reply_bcw_session_expired(
                        client,
                        message,
                        reason="wrong_step",
                    )
                    await safe_stop_propagation(message)
            return

        payload = BroadcastServiceV2.extract_payload(message)
        state.update({
            "step": "mode_selection",
            "payload": payload,
            "admin_chat_id": message.chat.id,
            "admin_msg_id": message.id,
        })
        await _set_state(message.from_user.id, state)
        await _deliver_bcw_input_outcome(
            client,
            message,
            t(_LANG, "broadcast.wizard.step2_title"),
            _mode_keyboard(_LANG),
        )
        await safe_stop_propagation(message)

    # ── Step 2: mode selection ───────────────────────────────────────

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_MODE_SEND']}$|^{CB['BCW_MODE_FWD']}$") & _pm_dev)
    @developer_only
    async def bcw_mode(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        mode = "send" if query.data == CB["BCW_MODE_SEND"] else "forward"
        state["mode"] = mode
        state["step"] = "target_selection"
        state["targets"] = state.get("targets", [])
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.step3_title"),
            _targets_keyboard(_LANG, set(state["targets"])),
        )

    # ── Step 3: checkbox target selection ────────────────────────────

    @bot.on_callback_query(filters.regex(r"^bcw:tgt:(users|groups|chans)$") & _pm_dev)
    @developer_only
    async def bcw_toggle_target(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        key = query.data.split(":")[2]
        target_map = {"users": "users", "groups": "groups", "chans": "channels"}
        target = target_map.get(key, key)
        targets = set(state.get("targets", []))
        if target in targets:
            targets.discard(target)
        else:
            targets.add(target)
        state["targets"] = list(targets)
        await _set_state(query.from_user.id, state)
        await query.message.edit_reply_markup(reply_markup=_targets_keyboard(_LANG, targets))

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_TGT_NEXT']}$") & _pm_dev)
    @developer_only
    async def bcw_targets_next(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        if not state.get("targets"):
            await _edit_bcw_message(
                client,
                query,
                t(_LANG, "broadcast.wizard.tgt_none"),
                _targets_keyboard(_LANG, set(state.get("targets", []))),
            )
            return
        state["step"] = "filter_selection"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.step4_title"),
            _filter_keyboard(_LANG),
        )

    # ── Step 4: audience filter ──────────────────────────────────────

    @bot.on_callback_query(filters.regex(r"^bcw:flt:(all|7d|30d)$") & _pm_dev)
    @developer_only
    async def bcw_filter(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        flt = query.data.split(":")[2]
        state["filter"] = flt
        state["step"] = "schedule_selection"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.step5_title"),
            _schedule_keyboard(_LANG),
        )

    # ── Step 5: scheduling ───────────────────────────────────────────

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_SEND_NOW']}$") & _pm_dev)
    @developer_only
    async def bcw_send_now(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        state["schedule"] = "now"
        state["step"] = "confirm"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            _build_confirm_text(state),
            _confirm_keyboard(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_SEND_AT']}$") & _pm_dev)
    @developer_only
    async def bcw_send_at(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        state["step"] = "awaiting_datetime"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.ask_datetime"),
            InlineKeyboardMarkup([
                _cancel_btn(_LANG),
                _back_btn(_LANG, CB["BCW_BACK_SCHED"]),
            ]),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_SEND_AFTER']}$|^{CB['BCW_SEND_RECURRING']}$") & _pm_dev)
    @developer_only
    async def bcw_send_hours(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        is_recurring = query.data == CB["BCW_SEND_RECURRING"]
        state["step"] = "awaiting_hours_recurring" if is_recurring else "awaiting_hours"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.ask_hours"),
            InlineKeyboardMarkup([
                _cancel_btn(_LANG),
                _back_btn(_LANG, CB["BCW_BACK_SCHED"]),
            ]),
        )

    # ── Text input handlers (datetime + hours) ──────────────────────

    @bot.on_message(
        filters.private & filters.text & dev_filter(),
        group=BROADCAST_SCHEDULE_INPUT_GROUP,
    )
    async def bcw_text_input(client: Client, message: Message):
        logger.debug("BCW text handler entered user_id=%s", message.from_user.id)
        from app.services.wizard_ui import (
            clear_runtime_state,
            clear_wizard_and_allow_command,
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
        redis_client = await get_redis()
        active_wizard, _ = await detect_active_wizard_state_in_redis(
            redis_client,
            message.from_user.id,
            heal_conflicts=True,
        )
        if active_wizard and active_wizard != WIZARD_BROADCAST:
            return

        state = await _get_state(message.from_user.id)
        step = state.get("step") if state else None
        schedule_steps = ("awaiting_datetime", "awaiting_hours", "awaiting_hours_recurring")

        if step not in schedule_steps:
            if _looks_like_schedule_text(message.text) and state is None:
                return_token = await redis_client.get(
                    wizard_return_key(message.from_user.id)
                )
                if return_token != TOKEN_DEV_BROADCAST:
                    return
                await safe_delete_user_input(message)
                await _reply_bcw_session_expired(
                    client,
                    message,
                    reason="missing_state",
                )
                await redis_client.delete(wizard_return_key(message.from_user.id))
                await safe_stop_propagation(message)
            elif (
                _looks_like_schedule_text(message.text)
                and step in {"confirm", "schedule_selection"}
                and not state.get("stale_input_notified")
            ):
                state["stale_input_notified"] = True
                await _set_state(message.from_user.id, state)
                reason = "missing_state" if state is None else "wrong_step"
                await safe_delete_user_input(message)
                await _reply_bcw_session_expired(client, message, reason=reason)
                await safe_stop_propagation(message)
            return

        await safe_delete_user_input(message)

        if step == "awaiting_datetime":
            try:
                jdt = jdatetime.datetime.strptime(message.text.strip(), "%Y/%m/%d %H:%M")
                gdt = jdt.togregorian()
                run_at = datetime(gdt.year, gdt.month, gdt.day, gdt.hour, gdt.minute, tzinfo=timezone.utc)
                state["schedule"] = "at"
                state["run_at"] = run_at.isoformat()
                state["step"] = "confirm"
                await _set_state(message.from_user.id, state)
                await _deliver_bcw_input_outcome(
                    client,
                    message,
                    _build_confirm_text(state),
                    _confirm_keyboard(_LANG),
                )
                await safe_stop_propagation(message)
            except (ValueError, AttributeError):
                await _deliver_bcw_input_outcome(
                    client,
                    message,
                    t(_LANG, "broadcast.wizard.invalid_datetime"),
                    InlineKeyboardMarkup([
                        _cancel_btn(_LANG),
                        _back_btn(_LANG, CB["BCW_BACK_SCHED"]),
                    ]),
                )
                await safe_stop_propagation(message)

        elif step in ("awaiting_hours", "awaiting_hours_recurring"):
            try:
                hours = int(message.text.strip())
                if hours < 1 or hours > 8760:
                    raise ValueError
                if step == "awaiting_hours_recurring":
                    state["schedule"] = "recurring"
                    state["interval_hours"] = hours
                else:
                    state["schedule"] = "after"
                    run_at = datetime.now(timezone.utc) + timedelta(hours=hours)
                    state["run_at"] = run_at.isoformat()
                state["step"] = "confirm"
                await _set_state(message.from_user.id, state)
                await _deliver_bcw_input_outcome(
                    client,
                    message,
                    _build_confirm_text(state),
                    _confirm_keyboard(_LANG),
                )
                await safe_stop_propagation(message)
            except (ValueError, AttributeError):
                await _deliver_bcw_input_outcome(
                    client,
                    message,
                    t(_LANG, "common.errors.invalid_number"),
                    InlineKeyboardMarkup([
                        _cancel_btn(_LANG),
                        _back_btn(_LANG, CB["BCW_BACK_SCHED"]),
                    ]),
                )
                await safe_stop_propagation(message)

    # ── Confirm & execute ────────────────────────────────────────────

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_CONFIRM']}$") & _pm_dev)
    @developer_only
    async def bcw_confirm(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        try:
            if not await _claim_confirmation(query.from_user.id, state):
                return
        except Exception:
            logger.warning(
                "BCW confirmation claim unavailable user_id=%s",
                query.from_user.id,
                exc_info=True,
            )
            await _edit_bcw_message(
                client,
                query,
                t(_LANG, "common.errors.try_later"),
                build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
            )
            return
        await _clear_state(query.from_user.id)

        payload = state.get("payload", {})
        run_at = None
        interval_hours = None
        schedule = state.get("schedule", "now")

        if schedule in ("at", "after") and state.get("run_at"):
            run_at = datetime.fromisoformat(state["run_at"])
        if schedule == "recurring":
            interval_hours = state.get("interval_hours")
            run_at = datetime.now(timezone.utc) + timedelta(hours=interval_hours)

        pending: list[Broadcast] = []
        for target_scope in state.get("targets", ["users"]):
            pending.append(Broadcast(
                admin_id=query.from_user.id,
                mode=state.get("mode", "send"),
                target_scope=target_scope,
                payload_type=payload.get("payload_type"),
                text_content=payload.get("text_content"),
                entities_json=payload.get("entities_json"),
                caption=payload.get("caption"),
                caption_entities_json=payload.get("caption_entities_json"),
                file_id=payload.get("file_id"),
                source_chat_id=state.get("admin_chat_id"),
                source_message_id=state.get("admin_msg_id"),
                run_at=run_at,
                interval_hours=interval_hours,
                target_types_json=json.dumps(state.get("targets", [])),
                filter_type=state.get("filter", "all"),
                source_admin_chat_id=state.get("admin_chat_id"),
                source_admin_msg_id=state.get("admin_msg_id"),
            ))

        try:
            created = await broadcast_repo.create_many(pending)
        except Exception:
            logger.exception(
                "BCW atomic create failed user_id=%s target_count=%s",
                query.from_user.id,
                len(pending),
            )
            await _edit_bcw_message(
                client,
                query,
                t(_LANG, "common.errors.try_later"),
                build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
            )
            return

        for bc in created:
            if schedule == "now":
                create_logged_task(
                    BroadcastServiceV2.execute(client, bc.id),
                    name=f"broadcast_execute_{bc.id}",
                )
            elif schedule in ("at", "after"):
                from app.scheduler import schedule_broadcast_job
                schedule_broadcast_job(client, bc)
            elif schedule == "recurring":
                from app.scheduler import schedule_broadcast_job
                schedule_broadcast_job(client, bc)

        total = len(created)
        if total == 0:
            await _edit_bcw_message(
                client,
                query,
                t(_LANG, "common.errors.try_later"),
                build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
            )
            return

        if schedule == "now":
            final_text = t(_LANG, "broadcast.wizard.final_now", total=total)
        elif schedule in ("at", "after"):
            final_text = t(_LANG, "broadcast.wizard.final_scheduled", total=total, time=str(run_at))
        else:
            final_text = t(
                _LANG,
                "broadcast.wizard.final_recurring",
                total=total,
                hours=interval_hours,
            )

        await _edit_bcw_message(
            client,
            query,
            final_text,
            build_done_kb(_LANG, TOKEN_DEV_BROADCAST),
        )

    # ── Cancel ───────────────────────────────────────────────────────

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_CANCEL']}$") & _pm_dev)
    @developer_only
    async def bcw_cancel(client: Client, query: CallbackQuery):
        await _clear_state(query.from_user.id)
        text, kb = await resolve_navigation_payload(
            client,
            query.from_user.id,
            query.message.chat.type.value,
            TOKEN_DEV_BROADCAST,
            lang=_LANG,
        )
        ok = await _edit_bcw_message(client, query, text, kb)
        if ok:
            await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        else:
            await query.answer(t(_LANG, "common.errors.navigation_failed"), show_alert=True)

    # ── Back navigation ──────────────────────────────────────────────

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_BACK_MODE']}$") & _pm_dev)
    @developer_only
    async def bcw_back_mode(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        state["step"] = "mode_selection"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.step2_title"),
            reply_markup=_mode_keyboard(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_BACK_TGT']}$") & _pm_dev)
    @developer_only
    async def bcw_back_tgt(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        state["step"] = "target_selection"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.step3_title"),
            reply_markup=_targets_keyboard(_LANG, set(state.get("targets", []))),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_BACK_FILTER']}$") & _pm_dev)
    @developer_only
    async def bcw_back_filter(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        state["step"] = "filter_selection"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.step4_title"),
            reply_markup=_filter_keyboard(_LANG),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['BCW_BACK_SCHED']}$") & _pm_dev)
    @developer_only
    async def bcw_back_sched(client: Client, query: CallbackQuery):
        state = await _get_required_state(client, query)
        if state is None:
            return
        state["step"] = "schedule_selection"
        await _set_state(query.from_user.id, state)
        await _edit_bcw_message(
            client,
            query,
            t(_LANG, "broadcast.wizard.step5_title"),
            reply_markup=_schedule_keyboard(_LANG),
        )


async def _run_scheduled_broadcast(client: Client, broadcast_id: int) -> None:
    try:
        await BroadcastServiceV2.execute(client, broadcast_id)
    except Exception:
        logger.exception("Scheduled broadcast %d failed", broadcast_id)
