"""Unified per-user panel (PANEL-05).

`پنل کاربر` opens one panel for a target user (reply or numeric id / @username)
showing their group status, numeric id, rank and global-ban flag together, with
action sections gated by the issuer's access level and by group-vs-private
context. The individual promote/demote/ban commands remain available; this is
the consolidated surface the evidence describes.

Not shown: the evidenced "معاف اجبار" (force-exempt) flag. No force-exempt
concept exists in the current schema — it appears only in the legacy `sample.py`
script, which no app module imports — so it is omitted rather than invented.
"""

from __future__ import annotations

import logging
import re

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.repositories import admin_repo, global_ban_repo
from app.utils.i18n import AUTO_LANG, t

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG

_USER_PANEL_CMDS = ["پنل کاربر پلیر", "پنل کاربر", "Upanel player", "User panel"]
_USERNAME_RE = re.compile(r"^@[A-Za-z0-9_]{5,32}$")
USER_PANEL_CB_RE = re.compile(
    r"^up:(?P<action>promote|demote|ban|unban):(?P<uid>\d{1,20})$"
)


def _build_filter(cmds: list[str]):
    pattern = "|".join(re.escape(c) for c in cmds)
    return filters.regex(rf"^(?:{pattern})(?:\s|$)", flags=re.IGNORECASE)


def _argument(text: str, cmds: list[str]) -> str:
    stripped = (text or "").strip()
    for cmd in sorted(cmds, key=len, reverse=True):
        if stripped.lower().startswith(cmd.lower()):
            return stripped[len(cmd):].strip()
    return ""


def _is_group_chat(message: Message) -> bool:
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    return str(getattr(chat_type, "value", chat_type) or "") in {"group", "supergroup"}


async def resolve_target_user_id(client: Client, message: Message, rest: str) -> int | None:
    """Resolve the panel's subject from a reply, numeric id or @username."""
    reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
    if reply_user is not None and getattr(reply_user, "id", None) is not None:
        return int(reply_user.id)

    token = (rest or "").strip().split()[0] if (rest or "").strip() else ""
    if token.isdigit():
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
    return None


async def build_user_panel(
    client: Client,
    *,
    target_id: int,
    chat_id: int | None,
    actor_id: int,
    is_group: bool,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Aggregate status/id/rank/global-ban and the permitted action buttons."""
    from app.services.user_info_formatter_service import resolve_role_label
    from app.utils.sudo_permissions import can_use_sudo_admin_bypass

    rank = await resolve_role_label(target_id, chat_id if is_group else None, _LANG)
    banned = await global_ban_repo.is_globally_banned(target_id)

    lines = [
        t(_LANG, "user_panel.title"),
        t(_LANG, "user_panel.user_id", user_id=str(target_id)),
        t(_LANG, "user_panel.rank", rank=rank),
        t(
            _LANG,
            "user_panel.global_ban",
            state=t(_LANG, "common.labels.on" if banned else "common.labels.off"),
        ),
    ]

    rows: list[list[InlineKeyboardButton]] = []
    if is_group and chat_id is not None:
        is_admin = await admin_repo.is_music_admin(target_id, int(chat_id))
        lines.insert(
            3,
            t(
                _LANG,
                "user_panel.group_status",
                state=t(
                    _LANG,
                    "user_panel.status_admin" if is_admin else "user_panel.status_member",
                ),
            ),
        )
        # Promote/demote is scoped to the group and to the issuer's local rank.
        if await admin_repo.is_player_deputy_or_above(actor_id, int(chat_id)):
            rows.append(
                [
                    InlineKeyboardButton(
                        t(_LANG, "user_panel.btn_demote" if is_admin else "user_panel.btn_promote"),
                        callback_data=(
                            f"up:{'demote' if is_admin else 'promote'}:{target_id}"
                        ),
                    )
                ]
            )

    # Restrict/unrestrict (global ban) is sudo+ only, in either context.
    if await can_use_sudo_admin_bypass(actor_id):
        rows.append(
            [
                InlineKeyboardButton(
                    t(_LANG, "user_panel.btn_unban" if banned else "user_panel.btn_ban"),
                    callback_data=f"up:{'unban' if banned else 'ban'}:{target_id}",
                )
            ]
        )

    return "\n".join(lines), InlineKeyboardMarkup(rows) if rows else None


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Register the unified per-user panel command and its actions."""

    @bot.on_message(_build_filter(_USER_PANEL_CMDS), group=PRIORITY_COMMAND_GROUP)
    async def user_panel_command(client: Client, message: Message):
        actor_id = getattr(getattr(message, "from_user", None), "id", None)
        if actor_id is None:
            return
        rest = _argument(message.text or message.caption or "", _USER_PANEL_CMDS)
        target_id = await resolve_target_user_id(client, message, rest)
        if target_id is None:
            await message.reply(t(_LANG, "user_panel.target_required"))
            return
        chat_id = getattr(getattr(message, "chat", None), "id", None)
        text, markup = await build_user_panel(
            client,
            target_id=target_id,
            chat_id=int(chat_id) if chat_id is not None else None,
            actor_id=int(actor_id),
            is_group=_is_group_chat(message),
        )
        await message.reply(text, reply_markup=markup)

    @bot.on_callback_query(filters.regex(USER_PANEL_CB_RE))
    async def user_panel_action(client: Client, query):
        """Apply a panel action; permission is re-checked in-handler."""
        from app.utils.callback_trace import safe_answer_callback
        from app.utils.sudo_permissions import can_use_sudo_admin_bypass

        match = USER_PANEL_CB_RE.match(str(getattr(query, "data", "") or ""))
        actor_id = getattr(getattr(query, "from_user", None), "id", None)
        chat = getattr(getattr(query, "message", None), "chat", None)
        chat_id = getattr(chat, "id", None)
        if match is None or actor_id is None:
            await safe_answer_callback(
                query, t(_LANG, "common.errors.unknown_callback"), show_alert=True
            )
            return

        action = match.group("action")
        target_id = int(match.group("uid"))

        if action in {"ban", "unban"}:
            if not await can_use_sudo_admin_bypass(int(actor_id)):
                await safe_answer_callback(
                    query, t(_LANG, "user_panel.no_access"), show_alert=True
                )
                return
            if action == "ban":
                await global_ban_repo.add_global_ban(target_id, created_by=int(actor_id))
            else:
                await global_ban_repo.remove_global_ban(target_id)
            await safe_answer_callback(query, t(_LANG, "user_panel.done"))
            return

        # promote / demote are group-scoped and need a deputy+ issuer.
        if chat_id is None or not await admin_repo.is_player_deputy_or_above(
            int(actor_id), int(chat_id)
        ):
            await safe_answer_callback(
                query, t(_LANG, "user_panel.no_access"), show_alert=True
            )
            return
        if action == "promote":
            await admin_repo.promote_music_admin(int(chat_id), target_id, promoted_by=int(actor_id))
        else:
            await admin_repo.demote_music_admin(int(chat_id), target_id)
        await safe_answer_callback(query, t(_LANG, "user_panel.done"))
