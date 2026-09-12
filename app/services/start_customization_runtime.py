"""Runtime delivery helpers for customized private start content."""

from __future__ import annotations

import json
import logging
from typing import Any

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, Message

from app.services import start_customization_service as start_custom
from app.services.bot_settings_service import (
    build_start_links,
    get_start_welcome_text,
    resolve_single_active_owner_user_id,
)
from app.utils.helpers import mention_user
from app.utils.ui import KeyboardFactory

logger = logging.getLogger(__name__)


def _rebuild_entities(json_str: str | None) -> list | None:
    if not json_str:
        return None
    try:
        from pyrogram.enums import MessageEntityType
        from pyrogram.types import MessageEntity

        raw = json.loads(json_str)
        result = []
        for entry in raw:
            result.append(
                MessageEntity(
                    type=MessageEntityType(entry["type"]),
                    offset=entry["offset"],
                    length=entry["length"],
                    url=entry.get("url"),
                    language=entry.get("language"),
                    custom_emoji_id=entry.get("custom_emoji_id"),
                )
            )
        return result or None
    except Exception:
        logger.debug("start_customization entities rebuild failed", exc_info=True)
        return None


def _anchor_reply_kwargs(anchor_message: Message | None) -> dict[str, Any]:
    if anchor_message is None:
        return {}
    message_id = getattr(anchor_message, "id", None)
    if message_id is None:
        message_id = getattr(anchor_message, "message_id", None)
    if message_id is None:
        return {}
    return {"reply_to_message_id": int(message_id)}


async def build_regular_start_payload(
    client: Client | None,
    *,
    user_id: int,
    first_name: str,
    lang: str,
) -> tuple[
    start_custom.MessageItemDescriptor | None,
    str,
    InlineKeyboardMarkup | None,
]:
    owner_user_id = await resolve_single_active_owner_user_id()
    mention = mention_user(user_id, first_name or str(user_id))
    fallback_text = await get_start_welcome_text(
        lang,
        mention=mention,
        owner_user_id=owner_user_id,
    )
    links = await build_start_links(client, owner_user_id=owner_user_id)
    rendered_menu = await start_custom.render_start_menu(
        lang,
        links,
        owner_user_id=owner_user_id,
        chat_id=None,
    )
    item = await start_custom.get_random_message_item(
        category=start_custom.CATEGORY_START,
        owner_user_id=owner_user_id,
        chat_id=None,
    )
    return item, fallback_text, KeyboardFactory.start_custom_menu(rendered_menu)


async def deliver_regular_start(
    client: Client,
    message: Message,
    *,
    user_id: int,
    first_name: str,
    lang: str,
    management_role: str | None = None,
) -> str:
    item, fallback_text, reply_markup = await build_regular_start_payload(
        client,
        user_id=user_id,
        first_name=first_name,
        lang=lang,
    )
    if management_role in {"developer", "owner", "sudo"}:
        reply_markup = KeyboardFactory.with_management_entry(
            lang,
            management_role,
            reply_markup,
        )
    return await deliver_message_item(
        client,
        chat_id=message.chat.id,
        anchor_message=message,
        item=item,
        fallback_text=fallback_text,
        reply_markup=reply_markup,
    )


async def deliver_message_item(
    client: Client,
    *,
    chat_id: int,
    anchor_message: Message | None,
    item: start_custom.MessageItemDescriptor | None,
    fallback_text: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> str:
    if item is None:
        await _reply_or_send_text(
            client,
            chat_id=chat_id,
            anchor_message=anchor_message,
            text=fallback_text,
            reply_markup=reply_markup,
            entities=None,
        )
        return "fallback_text"

    if item.send_strategy == "copy_source":
        if await _copy_source(
            client,
            chat_id=chat_id,
            anchor_message=anchor_message,
            item=item,
            reply_markup=reply_markup,
        ):
            return "copied_source"
        logger.info(
            "start_customization.source_copy_fallback item_id=%s category=%s",
            item.id,
            item.category,
        )
        return await _deliver_snapshot_fallback(
            client,
            chat_id=chat_id,
            anchor_message=anchor_message,
            item=item,
            fallback_text=fallback_text,
            reply_markup=reply_markup,
            result_prefix="fallback_after_copy_failed",
        )

    if item.send_strategy == "send_media":
        if await _send_media(
            client,
            chat_id=chat_id,
            anchor_message=anchor_message,
            item=item,
            reply_markup=reply_markup,
        ):
            return "sent_media"
        logger.info(
            "start_customization.media_send_fallback item_id=%s category=%s",
            item.id,
            item.category,
        )

    text = (item.text or item.caption or fallback_text or "").strip()
    await _reply_or_send_text(
        client,
        chat_id=chat_id,
        anchor_message=anchor_message,
        text=text,
        reply_markup=reply_markup,
        entities=_rebuild_entities(item.entities_json),
    )
    return "sent_text"


async def _deliver_snapshot_fallback(
    client: Client,
    *,
    chat_id: int,
    anchor_message: Message | None,
    item: start_custom.MessageItemDescriptor,
    fallback_text: str,
    reply_markup: InlineKeyboardMarkup | None,
    result_prefix: str,
) -> str:
    if await _send_media(
        client,
        chat_id=chat_id,
        anchor_message=anchor_message,
        item=item,
        reply_markup=reply_markup,
    ):
        return f"sent_media_{result_prefix}"

    snapshot_text = (item.text or item.caption or "").strip()
    if snapshot_text:
        await _reply_or_send_text(
            client,
            chat_id=chat_id,
            anchor_message=anchor_message,
            text=snapshot_text,
            reply_markup=reply_markup,
            entities=_rebuild_entities(item.entities_json),
        )
        return f"sent_snapshot_{result_prefix}"

    await _reply_or_send_text(
        client,
        chat_id=chat_id,
        anchor_message=anchor_message,
        text=fallback_text,
        reply_markup=reply_markup,
        entities=None,
    )
    return result_prefix


async def _copy_source(
    client: Client,
    *,
    chat_id: int,
    anchor_message: Message | None,
    item: start_custom.MessageItemDescriptor,
    reply_markup: InlineKeyboardMarkup | None,
) -> bool:
    if item.source_chat_id is None or item.source_message_id is None:
        return False
    copy_message = getattr(client, "copy_message", None)
    if not callable(copy_message):
        return False
    try:
        kwargs: dict[str, Any] = {"reply_markup": reply_markup}
        kwargs.update(_anchor_reply_kwargs(anchor_message))
        await copy_message(
            chat_id,
            item.source_chat_id,
            item.source_message_id,
            **kwargs,
        )
        return True
    except Exception:
        logger.debug(
            "start_customization copy_message failed item_id=%s",
            item.id,
            exc_info=True,
        )
        return False


async def _send_media(
    client: Client,
    *,
    chat_id: int,
    anchor_message: Message | None,
    item: start_custom.MessageItemDescriptor,
    reply_markup: InlineKeyboardMarkup | None,
) -> bool:
    file_id = str(item.media_file_id or "").strip()
    if not file_id:
        return False
    media_type = str(item.media_type or item.message_type or "").strip().lower()
    method_name_by_type = {
        "photo": "send_photo",
        "video": "send_video",
        "document": "send_document",
        "audio": "send_audio",
        "animation": "send_animation",
        "voice": "send_voice",
        "sticker": "send_sticker",
        "video_note": "send_video_note",
    }
    method_name = method_name_by_type.get(media_type)
    if method_name is None:
        return False
    sender = getattr(client, method_name, None)
    if not callable(sender):
        return False

    kwargs: dict[str, Any] = {
        "reply_markup": reply_markup,
        **_anchor_reply_kwargs(anchor_message),
    }
    if media_type not in {"sticker", "video_note"}:
        kwargs["caption"] = item.caption
        kwargs["caption_entities"] = _rebuild_entities(item.entities_json)
    try:
        await sender(chat_id, file_id, **kwargs)
        return True
    except Exception:
        logger.debug(
            "start_customization media send failed item_id=%s media_type=%s",
            item.id,
            media_type,
            exc_info=True,
        )
        return False


async def _reply_or_send_text(
    client: Client,
    *,
    chat_id: int,
    anchor_message: Message | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    entities: list | None,
) -> None:
    kwargs: dict[str, Any] = {"reply_markup": reply_markup}
    if entities:
        kwargs["entities"] = entities
    reply = getattr(anchor_message, "reply", None) if anchor_message is not None else None
    if callable(reply):
        await reply(text, **kwargs)
        return
    kwargs.update(_anchor_reply_kwargs(anchor_message))
    await client.send_message(chat_id, text, **kwargs)
