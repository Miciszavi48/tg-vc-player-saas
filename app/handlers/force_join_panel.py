from __future__ import annotations

import logging
import re
import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from pyromod.exceptions import ListenerStopped

from app.repositories import settings_repo
from app.services.forced_membership_service import ForcedMembershipService
from app.utils.decorators import developer_only
from app.utils.ask_result import (
    deliver_ask_outcome,
    prompt_for_panel_input,
    safe_delete_user_input,
    safe_stop_listening,
)
from app.utils.filters import dev_filter, private_chat_filter
from app.utils.i18n import AUTO_LANG, t
from app.utils.ui import CB, KeyboardFactory
from app.services.wizard_ui import (
    TOKEN_DEV_SETTINGS,
    TOKEN_ROLE_ROOT,
    build_cancel_kb,
    build_done_kb,
    cancel_and_resolve,
    pop_return_token,
    remember_return_token,
)

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_TIMEOUT = 60
_FM_REMOVE_CONFIRM_TTL_SECONDS = 300
_pm_dev = dev_filter() & private_chat_filter()
_FM_REMOVE_PREFIX = f"{CB['FM_REMOVE']}:"


def _parse_fm_remove_request(data: str) -> int | None:
    if not data.startswith(_FM_REMOVE_PREFIX):
        return None
    if data.startswith(CB["FM_REMOVE_EXEC_PREFIX"]) or data.startswith(CB["FM_REMOVE_ABORT_PREFIX"]):
        return None
    raw = data[len(_FM_REMOVE_PREFIX):].strip()
    if not raw or ":" in raw or len(raw) > 20:
        return None
    if raw.startswith("-"):
        digits = raw[1:]
    else:
        digits = raw
    if not digits.isdigit():
        return None
    channel_id = int(raw)
    if channel_id == 0:
        return None
    return channel_id


def _parse_fm_remove_bound(data: str, prefix: str) -> tuple[int, int, int] | None:
    if not data.startswith(prefix):
        return None
    parts = data[len(prefix):].split(":")
    if len(parts) != 3:
        return None
    channel_id = int(parts[0]) if parts[0].lstrip("-").isdigit() else None
    if channel_id is None or channel_id == 0:
        return None
    try:
        user_id = int(parts[1])
        issued_at = int(parts[2])
    except ValueError:
        return None
    if user_id <= 0 or issued_at <= 0:
        return None
    return channel_id, user_id, issued_at


def _is_stale_fm_remove_token(issued_at: int) -> bool:
    now = int(time.time())
    return issued_at > now + 60 or now - issued_at > _FM_REMOVE_CONFIRM_TTL_SECONDS


def _fm_remove_confirm_kb(channel_id: int, user_id: int, issued_at: int) -> InlineKeyboardMarkup:
    payload = f"{channel_id}:{user_id}:{issued_at}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.confirm"),
                    callback_data=f"{CB['FM_REMOVE_EXEC_PREFIX']}{payload}",
                ),
                InlineKeyboardButton(
                    t(_LANG, "common.buttons.cancel_inline"),
                    callback_data=f"{CB['FM_REMOVE_ABORT_PREFIX']}{payload}",
                ),
            ],
            [InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=CB["FM_PANEL"])],
        ]
    )


async def _render_fm_panel(query: CallbackQuery) -> None:
    enabled = await ForcedMembershipService.is_enabled()
    targets = await ForcedMembershipService.get_targets_cached()
    state = t(_LANG, "status_indicator.active") if enabled else t(_LANG, "status_indicator.inactive")
    header = t(_LANG, "admin.fm.title") + "\n" + t(
        _LANG,
        "status_summary.row",
        feature=t(_LANG, "status_summary.feature_forced_membership"),
        state=state,
    )
    await query.message.edit_text(
        header,
        reply_markup=KeyboardFactory.forced_membership_panel(_LANG, enabled, len(targets)),
    )


async def _ask(
    client: Client,
    chat_id: int,
    key: str,
    lang: str = AUTO_LANG,
    *,
    user_id: int | None = None,
    return_to: str = TOKEN_ROLE_ROOT,
    prompt_text: str | None = None,
) -> Message | None:
    if user_id is not None:
        await remember_return_token(user_id, return_to)
    stopped = await safe_stop_listening(client, chat_id, user_id=user_id)
    logger.debug(
        "force_join ask start key=%s chat_id=%s user_id=%s stop_listening=%s",
        key,
        chat_id,
        user_id,
        stopped,
    )
    try:
        resp = await prompt_for_panel_input(
            client,
            chat_id,
            user_id,
            prompt_text or t(lang, key),
            build_cancel_kb(lang, return_to),
            timeout=_TIMEOUT,
        )
        if resp and resp.text and resp.text.strip().lower() in ("/cancel", "cancel", "لغو"):
            await safe_delete_user_input(resp)
            if user_id is not None:
                text, kb = await cancel_and_resolve(
                    client,
                    user_id,
                    chat_id,
                    "private",
                    lang=lang,
                    return_to=return_to,
                )
                await deliver_ask_outcome(client, chat_id, user_id, text, kb)
            else:
                await deliver_ask_outcome(
                    client,
                    chat_id,
                    None,
                    t(lang, "common.cancelled"),
                    build_done_kb(lang, return_to),
                )
            return None
        await safe_delete_user_input(resp)
        if user_id is not None:
            await pop_return_token(user_id)
        return resp
    except ListenerStopped:
        logger.debug("force_join ask listener stopped key=%s chat_id=%s user_id=%s", key, chat_id, user_id)
        return None
    except Exception as exc:
        logger.debug(
            "force_join ask failed key=%s chat_id=%s user_id=%s reason=%s",
            key,
            chat_id,
            user_id,
            type(exc).__name__,
        )
        await deliver_ask_outcome(
            client,
            chat_id,
            user_id,
            t(lang, "ask.timeout"),
            build_done_kb(lang, return_to),
        )
        if user_id is not None:
            await pop_return_token(user_id)
        return None


async def _send_done(
    client: Client,
    chat_id: int,
    text: str,
    *,
    user_id: int | None = None,
) -> None:
    await deliver_ask_outcome(
        client,
        chat_id,
        user_id,
        text,
        build_done_kb(_LANG, TOKEN_DEV_SETTINGS),
    )


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    # Identity-only filter: a deny-capable filter would make the dispatcher
    # skip this panel root on a stale message (query.message is None) and leak
    # the tap to unknown_callback. Role is enforced by @developer_only; scope
    # and staleness are answered explicitly below.
    @bot.on_callback_query(filters.regex(f"^{CB['FM_PANEL']}$"))
    @developer_only
    async def fm_panel(client: Client, query: CallbackQuery):
        chat = query.message.chat if query.message else None
        if chat is None or getattr(chat.type, "value", chat.type) != "private":
            await query.answer(
                t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        await query.answer()
        enabled = await ForcedMembershipService.is_enabled()
        targets = await ForcedMembershipService.get_targets_cached()
        state = t(_LANG, "status_indicator.active") if enabled else t(_LANG, "status_indicator.inactive")
        header = t(_LANG, "admin.fm.title") + "\n" + t(
            _LANG, "status_summary.row",
            feature=t(_LANG, "status_summary.feature_forced_membership"),
            state=state,
        )
        await query.message.edit_text(
            header,
            reply_markup=KeyboardFactory.forced_membership_panel(_LANG, enabled, len(targets)),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['FM_TOGGLE']}$") & _pm_dev)
    @developer_only
    async def fm_toggle(client: Client, query: CallbackQuery):
        current = await settings_repo.get_bot_setting("force_join_enabled")
        new_val = "0" if current == "1" else "1"
        await settings_repo.set_bot_setting("force_join_enabled", new_val, updated_by=query.from_user.id)
        label = t(_LANG, "status.force_join_label")
        if new_val == "1":
            await query.answer(t(_LANG, "status.toggled_on", feature=label), show_alert=True)
        else:
            await query.answer(t(_LANG, "status.toggled_off", feature=label), show_alert=True)

        enabled = new_val == "1"
        targets = await ForcedMembershipService.get_targets_cached()
        await query.message.edit_text(
            t(_LANG, "admin.fm.title"),
            reply_markup=KeyboardFactory.forced_membership_panel(_LANG, enabled, len(targets)),
        )

    @bot.on_callback_query(filters.regex(f"^{CB['FM_LIST']}$") & _pm_dev)
    @developer_only
    async def fm_list(client: Client, query: CallbackQuery):
        from app.repositories import force_join_repo
        targets = await force_join_repo.get_active_targets()
        if not targets:
            await query.answer(t(_LANG, "admin.fm.list_empty"), show_alert=True)
            return

        rows = []
        for tgt in targets:
            badge = t(_LANG, f"admin.fm.badge_{tgt.verify_status}") if tgt.verify_status in ("ok", "broken", "pending") else "❓"
            name = tgt.display_name or tgt.channel_username or str(tgt.channel_id)
            label = t(_LANG, "admin.fm.list_item", badge=badge, name=name)
            rows.append([
                InlineKeyboardButton(label, callback_data=f"{CB['FM_PANEL']}"),
                InlineKeyboardButton("🗑", callback_data=f"{_FM_REMOVE_PREFIX}{tgt.channel_id}"),
            ])
        rows.append([InlineKeyboardButton(t(_LANG, "common.buttons.back"), callback_data=CB["FM_PANEL"])])
        await query.message.edit_text(
            t(_LANG, "admin.fm.title"),
            reply_markup=InlineKeyboardMarkup(rows),
        )

    @bot.on_callback_query(filters.regex(rf"^{re.escape(_FM_REMOVE_PREFIX)}-?\d+$") & _pm_dev)
    @developer_only
    async def fm_remove(client: Client, query: CallbackQuery):
        channel_id = _parse_fm_remove_request(query.data)
        if channel_id is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer()
        issued_at = int(time.time())
        await query.message.edit_text(
            t(_LANG, "admin.fm.remove_confirm"),
            reply_markup=_fm_remove_confirm_kb(channel_id, query.from_user.id, issued_at),
        )

    @bot.on_callback_query(filters.regex(r"^fm:rm:do:-?\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def fm_remove_confirm(client: Client, query: CallbackQuery):
        parsed = _parse_fm_remove_bound(query.data, CB["FM_REMOVE_EXEC_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        channel_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_fm_remove_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await ForcedMembershipService.remove_target(channel_id)
        await query.answer(t(_LANG, "admin.fm.remove_success"), show_alert=True)
        await _render_fm_panel(query)

    @bot.on_callback_query(filters.regex(r"^fm:rm:no:-?\d+:\d+:\d+$") & _pm_dev)
    @developer_only
    async def fm_remove_abort(client: Client, query: CallbackQuery):
        parsed = _parse_fm_remove_bound(query.data, CB["FM_REMOVE_ABORT_PREFIX"])
        if parsed is None:
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        _channel_id, bound_user_id, issued_at = parsed
        if query.from_user.id != bound_user_id:
            await query.answer(t(_LANG, "common.errors.no_access"), show_alert=True)
            return
        if _is_stale_fm_remove_token(issued_at):
            await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)
            return
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await _render_fm_panel(query)

    @bot.on_callback_query(filters.regex(r"^fm:rm:(?:do|no):.*") & _pm_dev)
    @developer_only
    async def fm_remove_malformed(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _parse_fm_remove_bound(query.data, CB["FM_REMOVE_EXEC_PREFIX"]) is not None:
            return
        if _parse_fm_remove_bound(query.data, CB["FM_REMOVE_ABORT_PREFIX"]) is not None:
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(rf"^{re.escape(_FM_REMOVE_PREFIX)}") & _pm_dev)
    @developer_only
    async def fm_remove_invalid(client: Client, query: CallbackQuery):  # noqa: ARG001
        if _parse_fm_remove_request(query.data) is not None:
            return
        await query.answer(t(_LANG, "common.errors.invalid_callback"), show_alert=True)

    @bot.on_callback_query(filters.regex(f"^{CB['FM_ADD']}$") & _pm_dev)
    @developer_only
    async def fm_add(client: Client, query: CallbackQuery):
        await query.answer()
        chat_id = query.message.chat.id
        retry_prompt: str | None = None
        while True:
            resp = await _ask(
                client,
                chat_id,
                "admin.fm.add_prompt",
                user_id=query.from_user.id,
                return_to=TOKEN_DEV_SETTINGS,
                prompt_text=retry_prompt,
            )
            if resp is None:
                return
            identifier = (resp.text or "").strip()
            if identifier:
                break
            retry_prompt = (
                f"{t(_LANG, 'force_join_mgmt.invalid_identifier')}\n\n"
                f"{t(_LANG, 'admin.fm.add_prompt')}"
            )

        result = await ForcedMembershipService.add_target(client, identifier, query.from_user.id)
        if "error" in result:
            if result["error"] == "inaccessible":
                await _send_done(client, chat_id, t(_LANG, "admin.fm.add_inaccessible"))
            return

        msg = t(_LANG, "admin.fm.add_success")
        if result["verify_status"] == "bot_not_admin":
            msg += "\n" + t(_LANG, "admin.fm.add_bot_not_admin")
        await _send_done(client, chat_id, msg)

    @bot.on_callback_query(filters.regex(f"^{CB['FM_VERIFY_ALL']}$") & _pm_dev)
    @developer_only
    async def fm_verify_all(client: Client, query: CallbackQuery):
        await query.answer(t(_LANG, "admin.fm.verify_started"), show_alert=False)
        result = await ForcedMembershipService.verify_all(client)
        if "error" in result:
            await _send_done(client, query.message.chat.id, t(_LANG, "admin.fm.verify_already_running"))
            return
        await _send_done(client, query.message.chat.id, t(_LANG, "admin.fm.verify_done", ok=result["ok"], broken=result["broken"]))

    @bot.on_callback_query(filters.regex(f"^{CB['FM_TEST']}$") & _pm_dev)
    @developer_only
    async def fm_test(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        missing = await ForcedMembershipService.check(client, user_id)
        if missing is None:
            await query.answer(t(_LANG, "admin.fm.test_passed"), show_alert=True)
        else:
            names = ", ".join(
                m.get("display_name") or m.get("username") or str(m["channel_id"])
                for m in missing
            )
            await query.answer(
                t(_LANG, "admin.fm.test_failed", channels=names),
                show_alert=True,
            )
