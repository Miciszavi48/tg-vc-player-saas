from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.enums import ChatMembersFilter
from pyrogram.types import ChatMemberUpdated, Message

from app.config.settings import settings
from app.repositories import blacklist_repo, group_repo, log_repo, settings_repo
from app.repositories import channel_repo
from app.services import owner_scope_service
from app.services.bot_settings_service import get_bot_channel_link, get_guide_channel_link
from app.services import CreditService, InstallPolicyService, NotificationService
from app.services.analytics_service import track_event
from app.services import group_membership_age_service
from app.utils.cache import acquire_lock, release_lock
from app.utils.i18n import t
from app.utils.sudo_permissions import permission_denial_key, sudo_has_permission

logger = logging.getLogger(__name__)

_LANG = "fa"


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    @bot.on_chat_member_updated()
    async def on_member_update(client: Client, update: ChatMemberUpdated):
        """Detect when the bot is added to or removed from a group/channel."""
        if update.new_chat_member is None:
            return

        me = await client.get_me()
        if update.new_chat_member.user.id != me.id:
            try:
                await _record_member_status_change(update)
            except Exception as exc:
                logger.debug(
                    "membership status tracking skipped chat_id=%s err=%s",
                    getattr(update.chat, "id", None),
                    type(exc).__name__,
                )
            return

        chat = update.chat
        chat_id = chat.id
        chat_title = chat.title or str(chat_id)
        chat_type = "channel" if str(chat.type.value) == "channel" else "group"
        triggered_by = update.from_user.id if update.from_user else None

        new_status = update.new_chat_member.status.value
        old_status = update.old_chat_member.status.value if update.old_chat_member else None

        # Bot added to chat
        if new_status in ("member", "administrator") and old_status in (None, "left", "kicked"):
            await _on_new_install(client, chat_id, chat_title, chat_type, triggered_by)

        # Bot removed from chat
        elif new_status in ("left", "kicked") and old_status in ("member", "administrator"):
            await _on_left(client, chat_id, chat_title, chat_type, triggered_by)

    @bot.on_message(filters.new_chat_members)
    async def on_new_member_message(client: Client, message: Message):
        """Fallback: detect bot added via new_chat_members service message."""
        me = await client.get_me()
        for member in message.new_chat_members:
            if message.chat.type.value in ("group", "supergroup") and member.id != me.id:
                try:
                    await group_membership_age_service.record_member_join(
                        message.chat.id,
                        int(member.id),
                        _event_datetime(message),
                        source="new_chat_members",
                    )
                except Exception as exc:
                    logger.debug(
                        "membership new-member tracking skipped chat_id=%s user_id=%s err=%s",
                        message.chat.id,
                        member.id,
                        type(exc).__name__,
                    )
            if member.id == me.id:
                chat = message.chat
                chat_id = chat.id
                chat_title = chat.title or str(chat_id)
                chat_type = "channel" if str(chat.type.value) == "channel" else "group"
                triggered_by = message.from_user.id if message.from_user else None
                await _on_new_install(client, chat_id, chat_title, chat_type, triggered_by)
                break

    @bot.on_message(filters.left_chat_member)
    async def on_left_member_message(client: Client, message: Message):
        """Fallback: detect bot removed via left_chat_member service message."""
        me = await client.get_me()
        if message.left_chat_member and message.left_chat_member.id != me.id:
            if message.chat.type.value in ("group", "supergroup"):
                try:
                    await group_membership_age_service.record_member_left(
                        message.chat.id,
                        int(message.left_chat_member.id),
                        _event_datetime(message),
                        source="left_chat_member",
                    )
                except Exception as exc:
                    logger.debug(
                        "membership left-member tracking skipped chat_id=%s user_id=%s err=%s",
                        message.chat.id,
                        message.left_chat_member.id,
                        type(exc).__name__,
                    )
            return
        if message.left_chat_member and message.left_chat_member.id == me.id:
            chat = message.chat
            chat_id = chat.id
            chat_title = chat.title or str(chat_id)
            chat_type = "channel" if str(chat.type.value) == "channel" else "group"
            triggered_by = message.from_user.id if message.from_user else None
            await _on_left(client, chat_id, chat_title, chat_type, triggered_by)


async def _on_new_install(
    client: Client,
    chat_id: int,
    chat_title: str,
    chat_type: str,
    triggered_by: int | None,
) -> None:
    """Handle bot being installed in a group or channel.

    Protected by distributed lock to prevent double-install when
    multiple instances receive the same ChatMemberUpdated event.
    """
    from app.utils.redis_keys import install_lock_key
    lock_key = install_lock_key(chat_id)
    token = await acquire_lock(lock_key, ttl_ms=10_000)
    if token is None:
        return
    try:
        await _do_install(client, chat_id, chat_title, chat_type, triggered_by)
    finally:
        await release_lock(lock_key, token)


def _event_datetime(update_or_message) -> object | None:
    return getattr(update_or_message, "date", None)


async def _record_member_status_change(update: ChatMemberUpdated) -> None:
    """Track non-bot group membership from Telegram member updates."""
    chat = update.chat
    if getattr(chat.type, "value", None) not in ("group", "supergroup"):
        return
    new_member = update.new_chat_member
    if new_member is None or getattr(new_member, "user", None) is None:
        return
    user_id = int(new_member.user.id)
    new_status = new_member.status.value
    old_status = update.old_chat_member.status.value if update.old_chat_member else None
    event_at = _event_datetime(update)

    if new_status in ("member", "administrator") and old_status in (None, "left", "kicked"):
        await group_membership_age_service.record_member_join(
            chat.id,
            user_id,
            event_at,
            source="chat_member_update",
        )
    elif new_status in ("left", "kicked") and old_status in ("member", "administrator"):
        await group_membership_age_service.record_member_left(
            chat.id,
            user_id,
            event_at,
            source="chat_member_update",
        )


async def _do_install(
    client: Client,
    chat_id: int,
    chat_title: str,
    chat_type: str,
    triggered_by: int | None,
) -> None:
    blacklist_entity_type = "channel" if chat_type == "channel" else "group"
    if await blacklist_repo.is_blacklisted(chat_id, blacklist_entity_type):
        await track_event("install.failed", feature="install", status="fail")
        await client.send_message(chat_id, t(_LANG, "install.blacklisted"))
        try:
            await client.leave_chat(chat_id)
        except Exception:
            pass
        return

    max_members = settings.MAX_GROUP_MEMBERS
    if max_members > 0 and chat_type == "group":
        try:
            count = await client.get_chat_members_count(chat_id)
            if count > max_members:
                await client.send_message(
                    chat_id, t(_LANG, "install.member_limit", limit=max_members)
                )
                try:
                    await client.leave_chat(chat_id)
                except Exception:
                    pass
                return
        except Exception:
            pass

    max_admins = settings.MAX_CHANNEL_ADMINS
    if max_admins > 0 and chat_type == "channel":
        try:
            admin_count = 0
            async for _ in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
                admin_count += 1
            if admin_count > max_admins:
                await client.send_message(
                    chat_id, t(_LANG, "install.admin_limit", limit=max_admins)
                )
                try:
                    await client.leave_chat(chat_id)
                except Exception:
                    pass
                return
        except Exception:
            pass

    installer_role = await InstallPolicyService.determine_installer_role(triggered_by)
    if installer_role == "sudo" and triggered_by is not None:
        perm_field = "can_manage_channels" if chat_type == "channel" else "can_manage_groups"
        if not await sudo_has_permission(triggered_by, perm_field):
            await client.send_message(chat_id, t(_LANG, permission_denial_key(perm_field)))
            try:
                await client.leave_chat(chat_id)
            except Exception:
                pass
            return

    if chat_type == "channel":
        await channel_repo.upsert_channel(
            chat_id=chat_id,
            chat_title=chat_title,
            installed_by=triggered_by,
        )
    else:
        await group_repo.upsert_group(
            chat_id=chat_id,
            chat_title=chat_title,
            installed_by=triggered_by,
        )

    await settings_repo.create_defaults(chat_id, chat_type)
    await track_event(f"install.created.{chat_type}", feature="install", role=installer_role or "user")

    trial_days = settings.TRIAL_DAYS
    try:
        credit = await CreditService.activate_trial(chat_id, chat_type)
        trial_days = credit.credit_days
        await track_event("credit.trial_activated", feature="credit")
        msg_text = (
            t(_LANG, "install.success", chat_title=chat_title)
            + "\n"
            + t(_LANG, "install.trial_activated", days=trial_days)
        )
    except ValueError:
        msg_text = t(_LANG, "install.success", chat_title=chat_title)

    if installer_role in ("developer", "owner", "sudo"):
        owner_id = await owner_scope_service.resolve_owner_user_id_for_chat(
            chat_id,
            chat_type,
        )
        guide_link = await get_guide_channel_link(owner_user_id=owner_id)
        if not guide_link:
            guide_link = await get_bot_channel_link(owner_user_id=owner_id)
        if not guide_link:
            guide_link = "https://t.me"
        charged_by_str = str(triggered_by) if triggered_by else None
        from app.utils.ui import KeyboardFactory as KF
        await client.send_message(
            chat_id,
            msg_text + "\n\n" + t(_LANG, "install.panel_title"),
            reply_markup=KF.post_install_panel(
                _LANG, trial_days, charged_by_str, guide_link,
            ),
        )
    else:
        await client.send_message(chat_id, msg_text)

    await log_repo.log_install(
        chat_id=chat_id,
        chat_title=chat_title,
        chat_type=chat_type,
        triggered_by=triggered_by,
        sudo_id=triggered_by,
        action="install",
    )

    policy = await InstallPolicyService.get_policy()
    policy_mode = policy.policy_mode if policy else "open"

    await NotificationService.notify_install(
        client,
        chat_id,
        chat_type,
        chat_title,
        sudo_info=str(triggered_by) if triggered_by else None,
        installer_role=installer_role,
        policy_mode=policy_mode,
    )


async def _on_left(
    client: Client,
    chat_id: int,
    chat_title: str,
    chat_type: str,
    triggered_by: int | None,
) -> None:
    """Handle bot being removed from a group or channel."""
    from app.utils.redis_keys import install_lock_key
    lock_key = install_lock_key(chat_id)
    token = await acquire_lock(lock_key, ttl_ms=10_000)
    if token is None:
        return
    try:
        if chat_type == "channel":
            await channel_repo.deactivate_channel(chat_id)
        else:
            await group_repo.deactivate_group(chat_id)

        await log_repo.log_install(
            chat_id=chat_id,
            chat_title=chat_title,
            chat_type=chat_type,
            triggered_by=triggered_by,
            sudo_id=triggered_by,
            action="uninstall",
        )

        await NotificationService.notify_uninstall(client, chat_id, chat_type, chat_title)
    finally:
        await release_lock(lock_key, token)
