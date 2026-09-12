"""Format rich user-info text for the Id command."""

from __future__ import annotations

import logging

from pyrogram import Client
from pyrogram.types import Message, User

from app.repositories import admin_repo, user_repo
from app.repositories import group_text_call_command_repo as call_stats_repo
from app.utils.bot_guards import is_developer
from app.utils.i18n import t

logger = logging.getLogger(__name__)

_TELEGRAM_CAPTION_MAX = 1024
_ROLE_I18N_KEYS = {
    "developer": "public_cmd.role_developer",
    "owner": "public_cmd.role_owner",
    "sudo": "public_cmd.role_sudo",
    "player_owner": "public_cmd.role_player_owner",
    "player_deputy": "public_cmd.role_player_deputy",
    "music_admin": "public_cmd.role_music_admin",
    "video_admin": "public_cmd.role_video_admin",
    "vip": "public_cmd.role_vip",
    "regular": "public_cmd.role_regular",
}


def resolve_target_user(message: Message) -> User | None:
    """Return the replied user when present, otherwise the message sender."""
    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is not None:
        reply_user = getattr(reply_to, "from_user", None)
        if reply_user is not None:
            return reply_user
    return getattr(message, "from_user", None)


def format_display_name(user: User | None) -> str:
    """Build a safe display name without leaking phone or internal fields."""
    if user is None:
        return "-"
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    return full or "-"


def format_username(username: str | None) -> str:
    """Format a Telegram username or a clean fallback."""
    if not username:
        return "-"
    clean = str(username).strip().lstrip("@")
    return f"@{clean}" if clean else "-"


async def resolve_dc_id(client: Client, user_id: int, lang: str = "fa") -> str:
    """Best-effort DC id lookup with a safe fallback label."""
    try:
        full_user = await client.get_users(user_id)
        dc_id = getattr(full_user, "dc_id", None)
        if dc_id is not None:
            return str(dc_id)
    except Exception:
        logger.debug("dc_id lookup failed user_id=%s", user_id, exc_info=True)
    return t(lang, "public_cmd.dc_unknown")


async def resolve_chat_dc_id(client: Client, chat_id: int, lang: str = "fa") -> str:
    """Best-effort datacenter lookup for a chat with a safe fallback label.

    Kurigram only populates ``Chat.dc_id`` from the chat photo (or ``stats_dc``
    for channels), so a chat without a photo legitimately resolves to unknown.
    """
    try:
        chat = await client.get_chat(chat_id)
        dc_id = getattr(chat, "dc_id", None)
        # Only a real integer is meaningful; anything else (missing photo, a
        # non-numeric placeholder) must fall back to the unknown label.
        if isinstance(dc_id, int) and not isinstance(dc_id, bool):
            return str(dc_id)
    except Exception:
        logger.debug("chat dc_id lookup failed chat_id=%s", chat_id, exc_info=True)
    return t(lang, "public_cmd.dc_unknown")


async def resolve_role_key(user_id: int, chat_id: int | None) -> str:
    """Detect the highest-priority role key for a user."""
    if is_developer(user_id):
        return "developer"
    if await user_repo.is_owner(user_id):
        return "owner"
    if await user_repo.is_sudo(user_id):
        return "sudo"
    if chat_id is not None:
        if await admin_repo.is_player_owner(user_id, chat_id):
            return "player_owner"
        if await admin_repo.is_player_deputy(user_id, chat_id):
            return "player_deputy"
        if await admin_repo.is_music_admin(user_id, chat_id):
            return "music_admin"
        if await admin_repo.is_video_admin(user_id, chat_id):
            return "video_admin"
        if await admin_repo.is_vip(user_id, chat_id):
            return "vip"
    return "regular"


async def resolve_role_label(user_id: int, chat_id: int | None, lang: str) -> str:
    """Return a localized role label for display."""
    role_key = await resolve_role_key(user_id, chat_id)
    i18n_key = _ROLE_I18N_KEYS.get(role_key, "public_cmd.role_regular")
    return t(lang, i18n_key)


def format_hms_duration(seconds: int) -> str:
    """Format seconds as zero-padded HH:MM:SS."""
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


async def build_call_stats_block(chat_id: int, user_id: int, lang: str) -> str | None:
    """Build the optional voice-call attendance block for one user."""
    from app.services import call_stats_panel_service as panel_svc

    user_seconds, percent, rank_index = await panel_svc.fetch_today_user_stats(
        chat_id, user_id
    )
    if user_seconds == 0 and rank_index is None:
        start, end = await panel_svc.period_bounds(chat_id, "today")
        any_rows = await call_stats_repo.get_call_stats_scoped(
            chat_id, start=start, end=end, allowed_user_ids=None, limit=1
        )
        if not any_rows:
            return None

    if rank_index is not None:
        rank_label = t(lang, "public_cmd.user_info_stats_rank_value", rank=str(rank_index))
    else:
        rank_label = t(lang, "public_cmd.user_info_stats_rank_none")

    return "\n".join(
        [
            t(lang, "public_cmd.user_info_stats_separator"),
            t(
                lang,
                "public_cmd.user_info_stats_duration",
                duration=format_hms_duration(user_seconds),
            ),
            t(
                lang,
                "public_cmd.user_info_stats_percent",
                percent=str(percent),
            ),
            t(
                lang,
                "public_cmd.user_info_stats_rank",
                rank=rank_label,
            ),
        ]
    )


def truncate_caption(text: str, *, max_len: int = _TELEGRAM_CAPTION_MAX) -> str:
    """Trim text to Telegram caption limits, preferring to drop the stats block."""
    if len(text) <= max_len:
        return text
    separator = t("fa", "public_cmd.user_info_stats_separator")
    if separator in text:
        base, _sep, _stats = text.partition(separator)
        if len(base) <= max_len:
            return base.rstrip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


async def build_user_info_text(
    *,
    client: Client,
    user: User,
    chat_id: int | None,
    lang: str,
    include_call_stats: bool,
) -> str:
    """Build the full user-info message body for the Id command."""
    user_id = int(user.id)
    username = format_username(getattr(user, "username", None))
    dc_label = await resolve_dc_id(client, user_id, lang)
    role_label = await resolve_role_label(user_id, chat_id, lang)

    lines = [
        t(lang, "public_cmd.user_info_name", name=format_display_name(user)),
        t(lang, "public_cmd.user_info_username", username=username),
        t(lang, "public_cmd.user_info_dc", dc_id=dc_label),
        t(lang, "public_cmd.user_info_id", user_id=str(user_id)),
        t(lang, "public_cmd.user_info_role", role=role_label),
    ]

    if include_call_stats and chat_id is not None:
        stats_block = await build_call_stats_block(chat_id, user_id, lang)
        if stats_block:
            lines.append(stats_block)

    return truncate_caption("\n".join(lines))


async def reply_user_info(
    client: Client,
    message: Message,
    *,
    output_mode: str,
    include_call_stats: bool,
    lang: str,
) -> None:
    """Send user info as text or photo+caption depending on output mode."""
    target = resolve_target_user(message)
    if target is None:
        await message.reply(t(lang, "common.errors.no_access"))
        return

    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    chat_type = getattr(getattr(chat, "type", None), "value", None)
    is_group = chat_type in ("group", "supergroup")

    if output_mode == "inactive" and is_group:
        # Admins disabled the lookup reply for this chat; stay silent by design.
        return
    effective_chat_id = int(chat_id) if is_group and chat_id is not None else None

    body = await build_user_info_text(
        client=client,
        user=target,
        chat_id=effective_chat_id,
        lang=lang,
        include_call_stats=include_call_stats and is_group,
    )

    if output_mode == "photo" and is_group:
        try:
            async for photo in client.get_chat_photos(target.id, limit=1):
                await message.reply_photo(photo.file_id, caption=body)
                return
        except Exception:
            logger.debug(
                "id photo fallback chat_id=%s user_id=%s",
                chat_id,
                target.id,
                exc_info=True,
            )

    await message.reply(body)
