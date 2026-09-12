"""Slash-free group call text command handlers."""

from __future__ import annotations

import logging
import re
from typing import Any

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.repositories import admin_repo, user_repo
from app.services import group_text_call_command_service as call_cmd_service
from app.services import group_runtime_state_service
from app.services.language_service import resolve_lang_from_update
from app.services.media_capability_service import build_now_playing_controls
from app.services.now_playing_renderer import NowPlayingContext, render_now_playing_text
from app.services.cover_art_service import reply_now_playing_with_optional_cover
from app.utils.bot_guards import is_developer
from app.utils.group_text_commands import (
    GroupTextCommandType,
    ParsedGroupTextCommand,
    group_text_command_filter,
    parse_group_text_command,
)
from app.repositories import call_stats_settings_repo
from app.utils.i18n import AUTO_LANG, t
from app.utils.sudo_permissions import sudo_has_permission
from app.utils.ui import KeyboardFactory

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_ADMIN_COMMANDS = {
    GroupTextCommandType.END_CALL,
    GroupTextCommandType.START_CALL,
    GroupTextCommandType.MUTE_CALL,
    GroupTextCommandType.UNMUTE_CALL,
    GroupTextCommandType.INVITE_CALL,
    GroupTextCommandType.AUTO_CALL_STATS,
    GroupTextCommandType.CALL_MUTE,
    GroupTextCommandType.CALL_COMMENT,
    GroupTextCommandType.SET_TITLE,
    GroupTextCommandType.CALL_STATS_PANEL,
    GroupTextCommandType.REPEAT,
    GroupTextCommandType.AUTO_CLEAR,
    GroupTextCommandType.EQUALIZER,
    GroupTextCommandType.CALL_REPORT,
    GroupTextCommandType.SET_CHANNEL,
    GroupTextCommandType.CHANNEL_PLAYBACK,
}
# Deputy+ only, per the evidenced '#دستورات_معاون_گروه' actor scope.
_DEPUTY_COMMANDS = {GroupTextCommandType.RESET_CALL_STATS}
_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{5,32}$")
_EQUALIZER_CB_RE = re.compile(r"^eq:set:(?P<preset>[a-z]{1,16})$")


def _chat_type_value(message: Message) -> str:
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    return str(getattr(chat_type, "value", chat_type) or "")


def _is_group_message(message: Message) -> bool:
    return _chat_type_value(message) in {"group", "supergroup"}


async def _reply(message: Message, text: str, **kwargs: Any) -> None:
    reply = getattr(message, "reply", None)
    if callable(reply):
        await reply(text, **kwargs)
        return
    reply_text = getattr(message, "reply_text", None)
    if callable(reply_text):
        await reply_text(text, **kwargs)


async def _is_telegram_group_admin(client: Client, chat_id: int, user_id: int) -> bool:
    get_member = getattr(client, "get_chat_member", None)
    if not callable(get_member):
        return False
    try:
        member = await get_member(chat_id, user_id)
    except Exception:
        return False
    status = getattr(getattr(member, "status", None), "value", None) or getattr(
        member,
        "status",
        None,
    )
    status = str(status or "").lower()
    if status in {"creator", "owner"}:
        return True
    if status != "administrator":
        return False
    privileges = getattr(member, "privileges", None) or member
    known_flags = [
        "can_manage_video_chats",
        "can_manage_voice_chats",
        "can_manage_chat",
        "can_restrict_members",
    ]
    present = [getattr(privileges, name, None) for name in known_flags]
    if all(value is None for value in present):
        return True
    return any(bool(value) for value in present)


async def can_manage_call_command_user(
    client: Client,
    chat_id: int,
    user_id: int,
) -> bool:
    """Return whether a user may manage group call commands in a chat."""
    if is_developer(user_id):
        return True
    if await user_repo.is_owner(user_id):
        return True
    if await sudo_has_permission(user_id, "can_manage_chat_settings"):
        return True
    if await admin_repo.is_music_admin_or_above(user_id, chat_id):
        return True
    if await admin_repo.is_video_admin(user_id, chat_id):
        return True
    return await _is_telegram_group_admin(client, chat_id, user_id)


async def _can_manage_call_command(client: Client, message: Message) -> bool:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if user_id is None or chat_id is None:
        return False
    return await can_manage_call_command_user(client, int(chat_id), int(user_id))


async def _can_manage_deputy_command(client: Client, message: Message) -> bool:
    """Deputy+ gate for commands the evidence scopes to '#دستورات_معاون_گروه'."""
    from app.utils.player_permissions import can_manage_call_security

    user_id = getattr(getattr(message, "from_user", None), "id", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if user_id is None or chat_id is None:
        return False
    return await can_manage_call_security(client, int(chat_id), int(user_id))


def _parser_error_key(error: str | None) -> str | None:
    return {
        "missing_duration": "group_text_call.missing_duration",
        "invalid_duration": "group_text_call.invalid_duration",
        "duration_too_large": "group_text_call.duration_too_large",
        "missing_mode": "group_text_call.missing_mode",
        "invalid_mode": "group_text_call.invalid_mode",
        "invalid_day": "group_text_call.invalid_day",
        "missing_title": "group_text_call.missing_title",
        "missing_target": "group_text_call.missing_target",
        "missing_cadence": "group_text_call.missing_cadence",
        "missing_channel": "group_text_call.missing_channel",
        "invalid_channel": "group_text_call.invalid_channel",
        "invalid_cadence": "group_text_call.invalid_cadence",
    }.get(error or "")


def _result_key(result: call_cmd_service.CommandActionResult) -> str:
    return {
        "no_active_call": "group_text_call.no_active_call",
        "api_unavailable": "group_text_call.api_unavailable",
        "api_error": "group_text_call.api_error",
        "raw_api_unavailable": "group_text_call.api_unavailable",
        "db_error": "group_text_call.db_error",
        "no_helper_available": "group_text_call.no_helper",
        "no_helper_session": "group_text_call.no_helper",
        "helper_join_failed": "group_text_call.helper_join_failed",
        "helper_not_in_group": "group_text_call.helper_not_in_group",
        "helper_not_admin": "group_text_call.helper_not_admin",
        "helper_session_unavailable": "group_text_call.helper_session_unavailable",
        "participant_not_found": "group_text_call.target_not_found",
        "permission_denied": "group_text_call.api_permission_denied",
        "flood_wait": "group_text_call.api_error",
        "no_users": "group_text_call.no_users",
        "special_stats_disabled": "group_text_call.special_stats_disabled",
        "link_unavailable": "group_text_call.link_unavailable",
    }.get(result.reason, "group_text_call.api_error")


async def _resolve_target_user_id(
    client: Client,
    message: Message,
    target_text: str | None,
) -> int | None:
    if target_text:
        token = target_text.strip().split(maxsplit=1)[0]
        if re.fullmatch(r"\d+", token):
            return int(token)
        if _USERNAME_RE.fullmatch(token):
            get_users = getattr(client, "get_users", None)
            if callable(get_users):
                try:
                    user = await get_users(token)
                    uid = getattr(user, "id", None)
                    if uid is not None:
                        return int(uid)
                except Exception:
                    return None

    reply = getattr(message, "reply_to_message", None)
    reply_user = getattr(reply, "from_user", None)
    if reply_user is not None and getattr(reply_user, "id", None) is not None:
        return int(reply_user.id)

    raw_text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    for entity in getattr(message, "entities", None) or []:
        entity_type = getattr(getattr(entity, "type", None), "value", None) or getattr(
            entity,
            "type",
            None,
        )
        if str(entity_type) == "text_mention":
            user = getattr(entity, "user", None)
            if user is not None and getattr(user, "id", None) is not None:
                return int(user.id)
        if str(entity_type) == "mention":
            offset = int(getattr(entity, "offset", 0))
            length = int(getattr(entity, "length", 0))
            username = raw_text[offset: offset + length]
            if _USERNAME_RE.fullmatch(username):
                get_users = getattr(client, "get_users", None)
                if callable(get_users):
                    try:
                        user = await get_users(username)
                        uid = getattr(user, "id", None)
                        if uid is not None:
                            return int(uid)
                    except Exception:
                        return None
    return None


async def _handle_get_panel(message: Message) -> None:
    chat_id = int(message.chat.id)
    state = await call_cmd_service.get_current_playback(chat_id)
    if state is None:
        await _reply(message, t(_LANG, "group_text_call.panel_empty"))
        return
    lang = await resolve_lang_from_update(message)
    text = await render_now_playing_text(
        chat_id,
        NowPlayingContext(
            title=getattr(state, "title", None),
            media_type=getattr(state, "media_type", "audio"),
        ),
        lang=lang,
    )
    await reply_now_playing_with_optional_cover(
        message,
        chat_id=chat_id,
        text=text,
        reply_markup=await build_now_playing_controls(lang, chat_id),
    )


async def _handle_end_call(message: Message, call_py: Any, parsed: ParsedGroupTextCommand) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    if parsed.minutes is None:
        result = await call_cmd_service.end_call_now(call_py, int(message.chat.id))
        if not result.ok:
            await _reply(message, t(_LANG, _result_key(result)))
            return
        await _reply(message, t(_LANG, "group_text_call.end_immediate"))
        return

    result = await call_cmd_service.schedule_call_end(
        call_py,
        int(message.chat.id),
        int(parsed.minutes or 0),
        requested_by=int(user_id) if user_id is not None else None,
    )
    if not result.ok:
        await _reply(message, t(_LANG, _result_key(result)))
        return
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.end_scheduled",
            minutes=str(parsed.minutes),
            end_at=result.end_at.astimezone().strftime("%Y-%m-%d %H:%M") if result.end_at else "-",
        ),
    )


async def _handle_start_call(client: Client, message: Message, call_py: Any) -> None:
    result = await call_cmd_service.start_call(client, call_py, int(message.chat.id))
    if result.ok:
        key = (
            "group_text_call.start_already_active"
            if result.reason == "already_active"
            else "group_text_call.start_requested"
        )
        await _reply(message, t(_LANG, key))
        return
    await _reply(message, t(_LANG, _result_key(result)))


async def _handle_mute(
    client: Client,
    message: Message,
    call_py: Any,
    parsed: ParsedGroupTextCommand,
    *,
    muted: bool,
) -> None:
    target_id = await _resolve_target_user_id(client, message, parsed.target_text)
    if target_id is None:
        await _reply(message, t(_LANG, "group_text_call.target_not_found"))
        return
    if muted:
        result = await call_cmd_service.mute_participant(call_py, int(message.chat.id), target_id)
    else:
        result = await call_cmd_service.unmute_participant(call_py, int(message.chat.id), target_id)
    if result.ok:
        # CALLSEC-01: report bot-driven voice-chat moderation to the owners.
        await call_cmd_service.notify_owners_of_moderation(
            client,
            int(message.chat.id),
            action="mute" if muted else "unmute",
            target_user_id=target_id,
            actor_user_id=getattr(getattr(message, "from_user", None), "id", None),
            chat_title=getattr(getattr(message, "chat", None), "title", None),
        )
        await _reply(
            message,
            t(
                _LANG,
                "group_text_call.mute_success" if muted else "group_text_call.unmute_success",
                user=str(target_id),
            ),
        )
        return
    await _reply(message, t(_LANG, _result_key(result)))


async def _handle_invite(
    client: Client,
    message: Message,
    call_py: Any,
    parsed: ParsedGroupTextCommand,
) -> None:
    chat_id = int(message.chat.id)
    if parsed.invite_scope == "admins":
        result = await call_cmd_service.invite_admins(client, call_py, chat_id)
    elif parsed.invite_scope == "recent":
        result = await call_cmd_service.invite_recent(client, call_py, chat_id)
    elif parsed.invite_scope == "special":
        result = await call_cmd_service.invite_special(client, call_py, chat_id)
    else:
        target_id = await _resolve_target_user_id(client, message, parsed.target_text)
        if target_id is None:
            await _reply(message, t(_LANG, "group_text_call.target_not_found"))
            return
        result = await call_cmd_service.invite_user(client, call_py, chat_id, target_id)
    if result.ok:
        await _reply(message, t(_LANG, "group_text_call.invite_success", count=str(result.count)))
        return
    await _reply(message, t(_LANG, _result_key(result)))


async def _handle_toggle(message: Message, parsed: ParsedGroupTextCommand) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    updated_by = int(user_id) if user_id is not None else None
    chat_id = int(message.chat.id)
    enabled = bool(parsed.mode)
    if parsed.command == GroupTextCommandType.AUTO_CALL_STATS:
        result = await call_cmd_service.set_auto_stats(chat_id, enabled, updated_by=updated_by)
        key = "group_text_call.auto_stats_state"
    elif parsed.command == GroupTextCommandType.CALL_MUTE:
        result = await call_cmd_service.set_call_mute(chat_id, enabled, updated_by=updated_by)
        key = (
            "group_text_call.call_mute_state_live"
            if result.applied_live
            else "group_text_call.call_mute_state_saved"
        )
    else:
        result = await call_cmd_service.set_call_comment(chat_id, enabled, updated_by=updated_by)
        key = (
            "group_text_call.call_comment_state_live"
            if result.applied_live
            else "group_text_call.call_comment_state_saved"
        )
    if not result.ok:
        await _reply(message, t(_LANG, _result_key(result)))
        return
    await _reply(
        message,
        t(
            _LANG,
            key,
            state=t(_LANG, "common.labels.on" if enabled else "common.labels.off"),
        ),
    )


async def _handle_stats_panel(client: Client, message: Message) -> None:
    chat_id = int(message.chat.id)
    if not await call_stats_settings_repo.get_enabled(chat_id):
        await _reply(message, t(_LANG, "call_stats_panel.feature_disabled"))
        return
    user_id = int(message.from_user.id) if message.from_user else 0
    await _reply(
        message,
        t(_LANG, "call_stats_panel.menu_title"),
        reply_markup=KeyboardFactory.call_stats_selection_menu(_LANG, chat_id, user_id),
    )


async def _handle_title(message: Message, call_py: Any, parsed: ParsedGroupTextCommand) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    result = await call_cmd_service.set_call_title(
        call_py,
        int(message.chat.id),
        parsed.title or "",
        updated_by=int(user_id) if user_id is not None else None,
    )
    if result.ok:
        await _reply(message, t(_LANG, "group_text_call.title_applied", title=parsed.title or ""))
        return
    await _reply(message, t(_LANG, _result_key(result)))


async def _handle_link(client: Client, message: Message, call_py: Any) -> None:
    # CALLMGMT-04 note: an explicit bot-side "public chats only" gate was tried
    # and reverted. The evidence's own open question leaves it unresolved whether
    # the restriction should be enforced here or simply arise from Telegram's
    # invite-export behaviour, and the existing real-action tests encode the
    # current contract (link is gated by active-call + raw-API availability, not
    # by chat visibility). Enforcing it here regressed five of them.
    result = await call_cmd_service.get_call_link(client, call_py, message.chat)
    if result.ok and result.link:
        await _reply(message, t(_LANG, "group_text_call.link_result", link=result.link))
        return
    await _reply(message, t(_LANG, _result_key(result)))



async def _handle_repeat(message: Message, parsed: ParsedGroupTextCommand) -> None:
    enabled = bool(parsed.mode)
    result = await call_cmd_service.set_repeat(int(message.chat.id), enabled)
    if not result.ok:
        key = (
            "group_text_call.repeat_live_unsupported"
            if result.reason == "repeat_live_unsupported"
            else _result_key(result)
        )
        await _reply(message, t(_LANG, key))
        return
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.repeat_state",
            state=t(_LANG, "common.labels.on" if enabled else "common.labels.off"),
        ),
    )


async def _handle_auto_clear(message: Message, parsed: ParsedGroupTextCommand) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    enabled = bool(parsed.mode)
    result = await call_cmd_service.set_auto_clear_stopped(
        int(message.chat.id),
        enabled,
        updated_by=int(user_id) if user_id is not None else None,
    )
    if not result.ok:
        await _reply(message, t(_LANG, _result_key(result)))
        return
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.auto_clear_state",
            state=t(_LANG, "common.labels.on" if enabled else "common.labels.off"),
        ),
    )


async def _handle_reset_cadence(message: Message, parsed: ParsedGroupTextCommand) -> None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    cadence = str(parsed.cadence or "")
    result = await call_cmd_service.set_call_stats_reset_cadence(
        int(message.chat.id),
        cadence,
        updated_by=int(user_id) if user_id is not None else None,
    )
    if not result.ok:
        await _reply(message, t(_LANG, _result_key(result)))
        return
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.reset_cadence_set",
            cadence=t(_LANG, f"group_text_call.cadence_{cadence}"),
        ),
    )


async def _handle_call_report(message: Message, parsed: ParsedGroupTextCommand) -> None:
    enabled = bool(parsed.mode)
    result = await call_cmd_service.set_call_report_dm(int(message.chat.id), enabled)
    if not result.ok:
        await _reply(message, t(_LANG, _result_key(result)))
        return
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.call_report_state",
            state=t(_LANG, "common.labels.on" if enabled else "common.labels.off"),
        ),
    )


async def _handle_set_channel(
    client: Client, message: Message, parsed: ParsedGroupTextCommand,
) -> None:
    result = await call_cmd_service.link_playback_channel(
        client, int(message.chat.id), int(parsed.target_chat_id or 0)
    )
    if not result.ok:
        await _reply(
            message,
            t(
                _LANG,
                {
                    "not_admin_here": "group_text_call.channel_link_not_admin_here",
                    "not_admin_channel": "group_text_call.channel_link_not_admin_channel",
                    "channel_not_found": "group_text_call.channel_link_not_found",
                    "api_unavailable": "group_text_call.api_unavailable",
                }.get(result.reason, "group_text_call.db_error"),
            ),
        )
        return
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.channel_linked",
            title=result.title or "",
            channel_id=str(result.count),
        ),
    )


async def _handle_channel_playback(message: Message, parsed: ParsedGroupTextCommand) -> None:
    enabled = bool(parsed.mode)
    result = await call_cmd_service.set_channel_playback(int(message.chat.id), enabled)
    if not result.ok:
        key = (
            "group_text_call.channel_playback_no_link"
            if result.reason == "no_channel_link"
            else "group_text_call.db_error"
        )
        await _reply(message, t(_LANG, key))
        return
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.channel_playback_state",
            state=t(_LANG, "common.labels.on" if enabled else "common.labels.off"),
            title=result.title or "",
        ),
    )


async def _handle_equalizer(message: Message) -> None:
    chat_id = int(message.chat.id)
    current = await call_cmd_service.get_equalizer_preset(chat_id)
    await _reply(
        message,
        t(
            _LANG,
            "group_text_call.equalizer_title",
            preset=t(_LANG, f"group_text_call.eq_preset_{current}"),
        ),
        reply_markup=KeyboardFactory.equalizer_menu(_LANG, current),
    )


def register(bot: Client, call_py) -> None:
    """Register slash-free group call text commands."""

    @bot.on_message(group_text_command_filter(), group=PRIORITY_COMMAND_GROUP)
    async def group_text_call_command(client: Client, message: Message):
        parsed = parse_group_text_command(message)
        if parsed is None:
            continue_propagation = getattr(message, "continue_propagation", None)
            if callable(continue_propagation):
                continue_propagation()
            return

        logger.debug(
            "group_text_call_command matched command=%s chat_id=%s user_id=%s",
            parsed.command.value,
            getattr(getattr(message, "chat", None), "id", None),
            getattr(getattr(message, "from_user", None), "id", None),
        )

        if not _is_group_message(message):
            await _reply(message, t(_LANG, "group_text_call.group_only"))
            return
        if not await group_runtime_state_service.require_active_group(int(message.chat.id), "group"):
            await _reply(message, t(_LANG, "manager_text.group_not_managed"))
            return

        error_key = _parser_error_key(parsed.error)
        if error_key is not None:
            await _reply(message, t(_LANG, error_key))
            return

        if parsed.command in _ADMIN_COMMANDS and not await _can_manage_call_command(client, message):
            await _reply(message, t(_LANG, "group_text_call.no_permission"))
            return
        if parsed.command in _DEPUTY_COMMANDS and not await _can_manage_deputy_command(client, message):
            await _reply(message, t(_LANG, "group_text_call.no_permission"))
            return

        if parsed.command == GroupTextCommandType.GET_PANEL:
            await _handle_get_panel(message)
        elif parsed.command == GroupTextCommandType.END_CALL:
            await _handle_end_call(message, call_py, parsed)
        elif parsed.command == GroupTextCommandType.START_CALL:
            await _handle_start_call(client, message, call_py)
        elif parsed.command == GroupTextCommandType.MUTE_CALL:
            await _handle_mute(client, message, call_py, parsed, muted=True)
        elif parsed.command == GroupTextCommandType.UNMUTE_CALL:
            await _handle_mute(client, message, call_py, parsed, muted=False)
        elif parsed.command == GroupTextCommandType.INVITE_CALL:
            await _handle_invite(client, message, call_py, parsed)
        elif parsed.command in {
            GroupTextCommandType.AUTO_CALL_STATS,
            GroupTextCommandType.CALL_MUTE,
            GroupTextCommandType.CALL_COMMENT,
        }:
            await _handle_toggle(message, parsed)
        elif parsed.command == GroupTextCommandType.CALL_STATS_PANEL:
            await _handle_stats_panel(client, message)
        elif parsed.command == GroupTextCommandType.SET_TITLE:
            await _handle_title(message, call_py, parsed)
        elif parsed.command == GroupTextCommandType.GET_CALL_LINK:
            await _handle_link(client, message, call_py)
        elif parsed.command == GroupTextCommandType.REPEAT:
            await _handle_repeat(message, parsed)
        elif parsed.command == GroupTextCommandType.AUTO_CLEAR:
            await _handle_auto_clear(message, parsed)
        elif parsed.command == GroupTextCommandType.RESET_CALL_STATS:
            await _handle_reset_cadence(message, parsed)
        elif parsed.command == GroupTextCommandType.EQUALIZER:
            await _handle_equalizer(message)
        elif parsed.command == GroupTextCommandType.CALL_REPORT:
            await _handle_call_report(message, parsed)
        elif parsed.command == GroupTextCommandType.SET_CHANNEL:
            await _handle_set_channel(client, message, parsed)
        elif parsed.command == GroupTextCommandType.CHANNEL_PLAYBACK:
            await _handle_channel_playback(message, parsed)

    @bot.on_callback_query(filters.regex(_EQUALIZER_CB_RE))
    async def equalizer_preset_choice(client: Client, query):
        """Apply an equalizer preset; permission is enforced in-handler."""
        from app.utils.callback_trace import safe_answer_callback
        from app.utils.player_permissions import can_open_group_settings

        match = _EQUALIZER_CB_RE.match(str(getattr(query, "data", "") or ""))
        chat_id = getattr(getattr(getattr(query, "message", None), "chat", None), "id", None)
        actor_id = getattr(getattr(query, "from_user", None), "id", None)
        if match is None or chat_id is None:
            await safe_answer_callback(
                query, t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return
        if actor_id is None or not await can_open_group_settings(
            client, int(chat_id), int(actor_id)
        ):
            await safe_answer_callback(
                query, t(_LANG, "group_text_call.no_permission"), show_alert=True
            )
            return

        preset = match.group("preset")
        result = await call_cmd_service.set_equalizer_preset(int(chat_id), preset)
        if not result.ok:
            await safe_answer_callback(
                query, t(_LANG, "group_text_call.db_error"), show_alert=True
            )
            return
        await safe_answer_callback(
            query,
            t(_LANG, "group_text_call.equalizer_applied",
              preset=t(_LANG, f"group_text_call.eq_preset_{preset}")),
        )
        edit = getattr(query, "edit_message_text", None)
        if callable(edit):
            await edit(
                t(_LANG, "group_text_call.equalizer_title",
                  preset=t(_LANG, f"group_text_call.eq_preset_{preset}")),
                reply_markup=KeyboardFactory.equalizer_menu(_LANG, preset),
            )
