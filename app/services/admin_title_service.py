"""Safe helpers for manual Telegram admin titles and promotions."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from pyrogram.types import ChatAdministratorRights

logger = logging.getLogger(__name__)

_ADMIN_STATUSES = {"administrator", "creator"}
_NON_MEMBER_STATUSES = {"left", "kicked", "banned"}


@dataclass(frozen=True)
class AdminTitlePreflightResult:
    """Read-only status for a manual admin-title application attempt.

    Attributes:
        ok: Whether all preflight checks passed.
        message_key: Localized message key describing the result.
        bot_status: Telegram membership status observed for the bot.
        target_status: Telegram membership status observed for the target user.
    """

    ok: bool
    message_key: str
    bot_status: str | None = None
    target_status: str | None = None


@dataclass(frozen=True)
class ManualPromotionPreflightResult:
    """Read-only status for a manual Telegram admin promotion attempt.

    Attributes:
        ok: Whether promotion may proceed.
        message_key: Localized message key describing the result.
        bot_status: Telegram membership status observed for the bot.
        bot_can_promote: Whether the bot can promote members in this chat.
        target_status: Telegram membership status observed for the target user.
        target_is_admin: True when the target is already an administrator.
        target_is_creator: True when the target is the chat creator.
    """

    ok: bool
    message_key: str
    bot_status: str | None = None
    bot_can_promote: bool = False
    target_status: str | None = None
    target_is_admin: bool = False
    target_is_creator: bool = False


def _status_value(member) -> str | None:
    raw_status = getattr(member, "status", None)
    value = getattr(raw_status, "value", raw_status)
    return str(value) if value is not None else None


def _bot_can_manage_titles(member) -> bool:
    """Best-effort check for title-management rights exposed by Pyrogram/Kurigram."""
    status = _status_value(member)
    if status == "creator":
        return True
    privileges = getattr(member, "privileges", None)
    if privileges is None:
        return True
    for attr in ("can_promote_members", "can_manage_chat"):
        value = getattr(privileges, attr, None)
        if value is True:
            return True
    return False


def _bot_can_promote_members(member) -> bool:
    """Return whether the bot may promote other members in this chat."""
    status = _status_value(member)
    if status == "creator":
        return True
    privileges = getattr(member, "privileges", None)
    if privileges is None:
        return False
    return getattr(privileges, "can_promote_members", False) is True


def build_minimal_promotion_privileges() -> ChatAdministratorRights:
    """Return a conservative fixed privilege set for manual Telegram promotion."""
    return ChatAdministratorRights(
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=False,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_post_messages=False,
        can_edit_messages=False,
        can_pin_messages=False,
    )


async def preflight_admin_title_apply(
    client,
    chat_id: int,
    target_user_id: int,
) -> AdminTitlePreflightResult:
    """Verify bot and target state before setting a Telegram admin title.

    Args:
        client: Pyrogram/Kurigram client used for read-only chat-member checks.
        chat_id: Numeric Telegram group/channel identifier.
        target_user_id: User whose existing Telegram admin title may be changed.

    Returns:
        AdminTitlePreflightResult: Safe, localized preflight outcome.
    """
    try:
        bot_member = await client.get_chat_member(chat_id, "me")
    except Exception as exc:
        logger.info(
            "admin title preflight failed while checking bot: chat_id=%s error=%s",
            chat_id,
            type(exc).__name__,
        )
        return AdminTitlePreflightResult(False, "admin_titles.apply_bot_not_admin")

    bot_status = _status_value(bot_member)
    if bot_status not in _ADMIN_STATUSES:
        return AdminTitlePreflightResult(
            False,
            "admin_titles.apply_bot_not_admin",
            bot_status=bot_status,
        )
    if not _bot_can_manage_titles(bot_member):
        return AdminTitlePreflightResult(
            False,
            "admin_titles.apply_bot_no_rights",
            bot_status=bot_status,
        )

    try:
        target_member = await client.get_chat_member(chat_id, target_user_id)
    except Exception as exc:
        logger.info(
            "admin title preflight failed while checking target: chat_id=%s target=%s error=%s",
            chat_id,
            target_user_id,
            type(exc).__name__,
        )
        return AdminTitlePreflightResult(
            False,
            "admin_titles.apply_target_not_admin",
            bot_status=bot_status,
        )

    target_status = _status_value(target_member)
    if target_status not in _ADMIN_STATUSES:
        return AdminTitlePreflightResult(
            False,
            "admin_titles.apply_target_not_admin",
            bot_status=bot_status,
            target_status=target_status,
        )

    return AdminTitlePreflightResult(
        True,
        "admin_titles.apply_preflight_ok",
        bot_status=bot_status,
        target_status=target_status,
    )


async def preflight_manual_telegram_promotion(
    client,
    chat_id: int,
    target_user_id: int,
) -> ManualPromotionPreflightResult:
    """Verify bot and target state before promoting a user to Telegram admin.

    Args:
        client: Pyrogram/Kurigram client used for read-only chat-member checks.
        chat_id: Numeric Telegram group/channel identifier.
        target_user_id: User who may be promoted to Telegram administrator.

    Returns:
        ManualPromotionPreflightResult: Safe, localized preflight outcome.
    """
    try:
        bot_member = await client.get_chat_member(chat_id, "me")
    except Exception as exc:
        logger.info(
            "manual promotion preflight failed while checking bot: chat_id=%s error=%s",
            chat_id,
            type(exc).__name__,
        )
        return ManualPromotionPreflightResult(False, "admin_titles.prom_bot_not_admin")

    bot_status = _status_value(bot_member)
    bot_can_promote = _bot_can_promote_members(bot_member)
    if bot_status not in _ADMIN_STATUSES:
        return ManualPromotionPreflightResult(
            False,
            "admin_titles.prom_bot_not_admin",
            bot_status=bot_status,
            bot_can_promote=bot_can_promote,
        )
    if not bot_can_promote:
        return ManualPromotionPreflightResult(
            False,
            "admin_titles.prom_bot_no_promote_rights",
            bot_status=bot_status,
            bot_can_promote=False,
        )

    try:
        target_member = await client.get_chat_member(chat_id, target_user_id)
    except Exception as exc:
        logger.info(
            "manual promotion preflight failed while checking target: chat_id=%s target=%s error=%s",
            chat_id,
            target_user_id,
            type(exc).__name__,
        )
        return ManualPromotionPreflightResult(
            False,
            "admin_titles.prom_target_not_in_chat",
            bot_status=bot_status,
            bot_can_promote=True,
        )

    target_status = _status_value(target_member)
    if target_status == "creator":
        return ManualPromotionPreflightResult(
            False,
            "admin_titles.prom_target_creator",
            bot_status=bot_status,
            bot_can_promote=True,
            target_status=target_status,
            target_is_creator=True,
        )
    if target_status == "administrator":
        return ManualPromotionPreflightResult(
            False,
            "admin_titles.prom_target_already_admin",
            bot_status=bot_status,
            bot_can_promote=True,
            target_status=target_status,
            target_is_admin=True,
        )
    if target_status in _NON_MEMBER_STATUSES:
        return ManualPromotionPreflightResult(
            False,
            "admin_titles.prom_target_not_in_chat",
            bot_status=bot_status,
            bot_can_promote=True,
            target_status=target_status,
        )

    return ManualPromotionPreflightResult(
        True,
        "admin_titles.prom_preflight_ok",
        bot_status=bot_status,
        bot_can_promote=True,
        target_status=target_status,
    )


async def promote_telegram_admin(
    client,
    chat_id: int,
    target_user_id: int,
) -> bool:
    """Promote a chat member to Telegram administrator with minimal privileges.

    Args:
        client: Pyrogram/Kurigram client.
        chat_id: Numeric Telegram group/channel identifier.
        target_user_id: Member to promote.

    Returns:
        bool: True when Telegram confirms the promotion.

    Raises:
        Exception: Telegram RPC or transport errors from the client.
    """
    privileges = build_minimal_promotion_privileges()
    try:
        return bool(
            await client.promote_chat_member(
                chat_id,
                target_user_id,
                privileges=privileges,
            )
        )
    except Exception:
        logger.exception(
            "manual promotion failed: chat_id=%s target=%s",
            chat_id,
            target_user_id,
        )
        raise


async def apply_admin_title(
    client,
    chat_id: int,
    target_user_id: int,
    title: str,
) -> bool:
    """Apply an already-stored title to an existing Telegram admin.

    Args:
        client: Pyrogram/Kurigram client.
        chat_id: Numeric Telegram group/channel identifier.
        target_user_id: Already-admin user whose title should be updated.
        title: Validated stored title to apply.

    Returns:
        bool: True when Telegram confirms the title update.

    Raises:
        ValueError: If the client rejects title application for this target.
        Exception: Telegram RPC or transport errors from the client.
    """
    try:
        return bool(await client.set_administrator_title(chat_id, target_user_id, title))
    except ValueError:
        logger.info(
            "admin title apply rejected by client validation: chat_id=%s target=%s",
            chat_id,
            target_user_id,
        )
        raise
    except Exception:
        logger.exception(
            "admin title apply failed: chat_id=%s target=%s",
            chat_id,
            target_user_id,
        )
        raise
