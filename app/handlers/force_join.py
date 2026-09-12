from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.repositories import user_repo
from app.utils.bot_guards import is_developer
from app.utils.sudo_permissions import can_use_sudo_admin_bypass
from app.services.analytics_service import track_event
from app.services.forced_membership_service import ForcedMembershipService
from app.utils.cache import check_fm_rate_limit
from app.utils.i18n import t
from app.utils.ui import CB

logger = logging.getLogger(__name__)

_DEFAULT_LANG = "fa"


async def check_force_join(
    client: Client, message: Message, user_id: int
) -> bool:
    """Return True if the user passes force-join checks (or is exempt)."""
    if is_developer(user_id):
        return True
    if await user_repo.is_owner(user_id):
        return True
    if await can_use_sudo_admin_bypass(user_id):
        return True

    missing = await ForcedMembershipService.check(client, user_id)
    await track_event("fm.check", feature="fm")
    if missing is None:
        await track_event("fm.passed", feature="fm")
        return True

    await track_event("fm.blocked", feature="fm")
    lang = _DEFAULT_LANG
    rows: list[list[InlineKeyboardButton]] = []
    for tgt in missing:
        link = tgt.get("invite_link") or f"https://t.me/{tgt.get('username', '')}"
        label = tgt.get("display_name") or tgt.get("username") or str(tgt["channel_id"])
        rows.append([InlineKeyboardButton(
            t(lang, "fm.join_button", channel=label), url=link,
        )])

    rows.append(
        [InlineKeyboardButton(t(lang, "fm.check_button"), callback_data=CB["START_FORCE_JOIN"])]
    )

    await message.reply(
        t(lang, "fm.join_required"),
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return False


# Keep old imports available for backward compat with dev_panel
async def _get_force_join_channels():
    from app.repositories import force_join_repo
    return await force_join_repo.get_active_targets()


async def _add_force_join_channel(
    channel_id: int,
    channel_username: str | None = None,
    invite_link: str | None = None,
    added_by: int | None = None,
):
    from app.repositories import force_join_repo
    return await force_join_repo.upsert_target(
        channel_id=channel_id,
        channel_username=channel_username,
        invite_link=invite_link,
        added_by=added_by,
    )


async def _remove_force_join_channel(channel_id: int) -> None:
    await ForcedMembershipService.remove_target(channel_id)


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    @bot.on_callback_query(filters.regex(f"^{CB['START_FORCE_JOIN']}$"))
    async def on_force_join_check(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        lang = _DEFAULT_LANG

        if not await check_fm_rate_limit(user_id):
            await query.answer(t(lang, "fm.rate_limited"), show_alert=True)
            return

        missing = await ForcedMembershipService.check(client, user_id)
        if missing is None:
            await query.answer(t(lang, "fm.check_passed"), show_alert=False)
            try:
                await query.message.delete()
            except Exception:
                pass
        else:
            await query.answer(t(lang, "fm.check_failed"), show_alert=True)
