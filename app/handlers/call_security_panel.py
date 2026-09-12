"""Call Security panel handlers (امنیت کال)."""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.repositories import admin_repo, call_security_repo
from app.services import call_security_service
from app.utils.bot_guards import is_developer
from app.utils.filters import group_chat_filter, install_chat_filter, music_admin_filter
from app.utils.i18n import AUTO_LANG, t
from app.services.panel_message_service import panel_callback_edit, remember_panel_from_query
from app.utils.ask_result import deliver_ask_outcome, safe_stop_listening
from app.utils.safe_ask import safe_ask
from app.utils.ui import CB, KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_install = install_chat_filter()
_grp_admin = music_admin_filter() & group_chat_filter()

_TOGGLE_MAP: dict[str, str] = {
    CB["GRP_CALLSEC_TOGGLE"]: "enabled",
    CB["GRP_CALLSEC_OWNERS"]: "owner_access_enabled",
    CB["GRP_CALLSEC_MUTE_IN"]: "mute_incoming_enabled",
    CB["GRP_CALLSEC_SUMMARY"]: "summary_enabled",
    CB["GRP_CALLSEC_REPORT"]: "report_enabled",
}
_TOGGLE_REGEX = "^(" + "|".join(re.escape(cb) for cb in _TOGGLE_MAP) + ")$"


async def _can_open_panel(user_id: int, chat_id: int, client: Client | None = None) -> bool:
    """Return whether the user may open the Call Security panel."""
    if client is not None:
        from app.utils.player_permissions import can_manage_call_security

        return await can_manage_call_security(client, chat_id, user_id)
    if is_developer(user_id):
        return True
    return await admin_repo.is_player_deputy_or_above(user_id, chat_id)


async def render_call_security_panel(
    client: Client,
    query: CallbackQuery,
    call_py,
) -> None:
    """Render or refresh the Call Security panel."""
    chat_id = query.message.chat.id
    user_id = query.from_user.id
    settings = await call_security_repo.get_or_create_call_security_settings(chat_id)
    caps = await call_security_service.get_panel_capabilities(chat_id, call_py)
    show_owner = await call_security_service.can_toggle_owner_access(user_id, chat_id, client=client)
    text = call_security_service.build_panel_text(_LANG, settings, caps)
    kb = call_security_service.build_panel_keyboard(
        _LANG,
        settings,
        show_owner_access=show_owner,
    )
    await panel_callback_edit(client, query, text, kb, answer=False)


async def render_call_security_panel_message(
    client: Client,
    message: Message,
    call_py,
) -> None:
    """Send Call Security panel as a new message (channel text entry)."""
    chat_id = message.chat.id
    user_id = message.from_user.id if message.from_user else 0
    settings = await call_security_repo.get_or_create_call_security_settings(chat_id)
    caps = await call_security_service.get_panel_capabilities(chat_id, call_py)
    show_owner = await call_security_service.can_toggle_owner_access(user_id, chat_id, client=client)
    text = call_security_service.build_panel_text(_LANG, settings, caps)
    kb = call_security_service.build_panel_keyboard(
        _LANG,
        settings,
        show_owner_access=show_owner,
    )
    await message.reply(text, reply_markup=kb)


def register(bot: Client, call_py) -> None:
    """Register Call Security panel callback and ask handlers."""

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CALLSEC']}$") & _install)
    async def open_call_security(client: Client, query: CallbackQuery):  # noqa: ARG001
        user_id = query.from_user.id
        chat_id = query.message.chat.id
        if not await _can_open_panel(user_id, chat_id, client):
            await query.answer(t(_LANG, "call_security.no_permission"), show_alert=True)
            return
        await render_call_security_panel(client, query, call_py)

    @bot.on_callback_query(filters.regex(_TOGGLE_REGEX) & _install)
    async def toggle_call_security_field(client: Client, query: CallbackQuery):  # noqa: ARG001
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        field = _TOGGLE_MAP[query.data]
        updated, err_key = await call_security_service.toggle_field(
            chat_id,
            field,
            user_id,
            call_py=call_py,
            client=client,
        )
        if err_key:
            await query.answer(t(_LANG, err_key), show_alert=True)
            return
        label_key = f"call_security.feature_{field.replace('_enabled', '')}"
        if field == "enabled":
            label_key = "call_security.feature_enabled"
        elif field == "owner_access_enabled":
            label_key = "call_security.btn_owner_access"
        elif field == "mute_incoming_enabled":
            label_key = "call_security.feature_mute_incoming"
        elif field == "summary_enabled":
            label_key = "call_security.feature_summary"
        elif field == "report_enabled":
            label_key = "call_security.feature_report"
        feature = t(_LANG, label_key)
        state = t(
            _LANG,
            "common.labels.on" if getattr(updated, field) else "common.labels.off",
        )
        await query.answer(t(_LANG, "status.setting_value", feature=feature, value=state))
        await render_call_security_panel(client, query, call_py)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CALLSEC_AGE']}$") & _install)
    async def ask_membership_age_days(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        settings = await call_security_repo.get_or_create_call_security_settings(chat_id)
        if not await call_security_service.can_manage(user_id, chat_id, settings, client=client):
            await query.answer(t(_LANG, "call_security.no_permission"), show_alert=True)
            return
        await query.answer()
        await remember_panel_from_query(query)
        current_days = call_security_service.get_membership_age_threshold_days(settings)
        resp = await safe_ask(
            client,
            chat_id,
            "call_security.ask_membership_age",
            lang=_LANG,
            user_id=user_id,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            t(_LANG, "common.buttons.back"),
                            callback_data=CB["GRP_CALLSEC_AGE_CANCEL"],
                        )
                    ]
                ]
            ),
            days=current_days,
        )
        if resp is None:
            return
        raw_text = (resp.text or "").strip()
        if raw_text.lower() in ("/cancel", "cancel", "لغو"):
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                t(_LANG, "texts_links.ask_cancelled_or_timeout"),
                call_security_service.build_panel_keyboard(
                    _LANG,
                    settings,
                    show_owner_access=await call_security_service.can_toggle_owner_access(
                        user_id, chat_id, client=client
                    ),
                ),
                query_message=query.message,
            )
            return
        try:
            days = int(raw_text)
        except ValueError:
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                t(_LANG, "call_security.invalid_membership_age"),
                None,
                query_message=query.message,
            )
            return
        if days < 0 or days > 3650:
            await deliver_ask_outcome(
                client,
                chat_id,
                user_id,
                t(_LANG, "call_security.invalid_membership_age"),
                None,
                query_message=query.message,
            )
            return
        await call_security_repo.set_membership_age_days(chat_id, days, updated_by=user_id)
        await render_call_security_panel(client, query, call_py)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CALLSEC_AGE_CANCEL']}$") & _install)
    async def cancel_membership_age_ask(client: Client, query: CallbackQuery):
        chat_id = query.message.chat.id
        user_id = query.from_user.id
        settings = await call_security_repo.get_or_create_call_security_settings(chat_id)
        if not await call_security_service.can_manage(user_id, chat_id, settings, client=client):
            await query.answer(t(_LANG, "call_security.no_permission"), show_alert=True)
            return
        await safe_stop_listening(client, chat_id, user_id=user_id)
        await query.answer(t(_LANG, "common.cancelled"), show_alert=False)
        await render_call_security_panel(client, query, call_py)

    @bot.on_callback_query(filters.regex(f"^{CB['GRP_CALLSEC_BACK']}$") & _install)
    async def call_security_back(client: Client, query: CallbackQuery):  # noqa: ARG001
        chat = query.message.chat
        if chat.type.value in ("group", "supergroup"):
            from app.handlers.group_panel import render_group_settings_panel

            await render_group_settings_panel(client, query)
            return
        await query.answer()
        await query.message.edit_text(
            t(_LANG, "call_security.channel_back_hint"),
            reply_markup=KeyboardFactory.back_button(_LANG),
        )
