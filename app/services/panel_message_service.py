"""Track and edit the active panel message to avoid navigation spam."""

from __future__ import annotations

import json
import logging
from typing import Any

from pyrogram import Client
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.utils.cache import get_redis
from app.utils.redis_keys import TTL_PANEL_MESSAGE, panel_message_key
from app.utils.telegram_message import safe_edit_message, safe_edit_or_send

logger = logging.getLogger(__name__)


def _panel_message_id(message: Message | None) -> int | None:
    """Return a real Telegram message id, ignoring mock or malformed values."""
    if message is None:
        return None
    message_id = getattr(message, "id", None) or getattr(message, "message_id", None)
    if isinstance(message_id, bool):
        return None
    return message_id if isinstance(message_id, int) else None


async def remember_panel_message(
    chat_id: int,
    user_id: int,
    message_id: int,
) -> bool:
    """Persist the message id users should navigate within."""
    payload = json.dumps({"chat_id": chat_id, "message_id": message_id})
    try:
        redis = await get_redis()
        await redis.set(
            panel_message_key(chat_id, user_id),
            payload,
            ex=TTL_PANEL_MESSAGE,
        )
        if chat_id > 0:
            await redis.set(
                panel_message_key(chat_id, None),
                payload,
                ex=TTL_PANEL_MESSAGE,
            )
        return True
    except Exception:
        # Panel metadata improves continuity but must never prevent a Telegram
        # edit/send that is otherwise valid.
        logger.warning(
            "remember_panel_message unavailable chat_id=%s user_id=%s",
            chat_id,
            user_id,
            exc_info=True,
        )
        return False


async def remember_panel_from_query(query: CallbackQuery) -> None:
    """Store the callback's message as the active panel anchor."""
    if query.message is None or query.from_user is None:
        return
    message_id = _panel_message_id(query.message)
    if message_id is None:
        return
    await remember_panel_message(
        query.message.chat.id,
        query.from_user.id,
        message_id,
    )


async def remember_panel_from_message(message: Message, user_id: int) -> None:
    """Store a command-entry panel message as the navigation anchor."""
    message_id = _panel_message_id(message)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    if not isinstance(chat_id, int) or isinstance(chat_id, bool) or message_id is None:
        return
    await remember_panel_message(chat_id, user_id, message_id)


async def get_panel_message_id(chat_id: int, user_id: int | None) -> int | None:
    """Load a stored panel message id for edit-first navigation."""
    try:
        redis = await get_redis()
    except Exception:
        logger.warning(
            "get_panel_message_id unavailable chat_id=%s user_id=%s",
            chat_id,
            user_id,
            exc_info=True,
        )
        return None
    keys = [panel_message_key(chat_id, user_id)]
    if user_id is not None:
        keys.append(panel_message_key(chat_id, None))
    for key in keys:
        try:
            raw = await redis.get(key)
        except Exception:
            logger.warning(
                "get_panel_message_id read failed chat_id=%s user_id=%s",
                chat_id,
                user_id,
                exc_info=True,
            )
            return None
        if not raw:
            continue
        try:
            data = json.loads(raw)
            # Never use an anchor copied from another chat. Telegram message
            # ids are chat-local, so accepting it could edit an unrelated
            # message with the same numeric id in the current private chat.
            if int(data["chat_id"]) != chat_id:
                continue
            return int(data["message_id"])
        except (TypeError, ValueError, KeyError):
            continue
    return None


async def edit_stored_panel(
    client: Client,
    chat_id: int,
    user_id: int | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    """Edit the stored panel message in-place."""
    message_id = await get_panel_message_id(chat_id, user_id)
    if message_id is None:
        return False

    edit_text = getattr(client, "edit_message_text", None)
    if not callable(edit_text):
        return False

    kwargs: dict[str, Any] = {"reply_markup": reply_markup}
    try:
        await edit_text(chat_id, message_id, text, **kwargs)
        return True
    except Exception as exc:
        from app.utils.telegram_message import is_message_not_modified

        if is_message_not_modified(exc):
            return True
        logger.debug(
            "edit_stored_panel edit_message_text failed chat_id=%s reason=%s",
            chat_id,
            type(exc).__name__,
        )

    edit_caption = getattr(client, "edit_message_caption", None)
    if not callable(edit_caption):
        return False
    try:
        await edit_caption(chat_id, message_id, text, **kwargs)
        return True
    except Exception as exc:
        from app.utils.telegram_message import is_message_not_modified

        if is_message_not_modified(exc):
            return True
        logger.debug(
            "edit_stored_panel edit_message_caption failed chat_id=%s reason=%s",
            chat_id,
            type(exc).__name__,
        )
    return False


async def deliver_panel_outcome(
    client: Client,
    chat_id: int,
    user_id: int | None,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    *,
    query_message: Message | None = None,
) -> Message | None:
    """Edit-first delivery for ask outcomes, toggles, and navigation."""
    if query_message is not None and user_id is not None:
        if await safe_edit_message(query_message, text, reply_markup=reply_markup):
            message_id = _panel_message_id(query_message)
            if message_id is not None:
                await remember_panel_message(chat_id, user_id, message_id)
            return query_message

    if user_id is not None and await edit_stored_panel(
        client, chat_id, user_id, text, reply_markup
    ):
        return None

    sent = await safe_edit_or_send(
        client,
        query_message,
        text,
        reply_markup=reply_markup,
        fallback_chat_id=chat_id,
        attempt_edit=query_message is None,
    )
    if sent is not None and user_id is not None:
        sent_id = _panel_message_id(sent)
        if sent_id is not None:
            await remember_panel_message(chat_id, user_id, sent_id)
    return sent


async def replace_panel_with_photo(
    client: Client,
    query: CallbackQuery,
    photo: str,
    *,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> Message | None:
    """Replace a text/caption panel with a photo-backed panel exactly once."""
    if query.message is None or query.from_user is None:
        return None
    old_message = query.message
    sent = await client.send_photo(
        old_message.chat.id,
        photo,
        caption=caption,
        reply_markup=reply_markup,
    )
    if sent is None:
        return None
    query.message = sent
    sent_id = _panel_message_id(sent)
    if sent_id is not None:
        await remember_panel_message(
            old_message.chat.id,
            query.from_user.id,
            sent_id,
        )
    old_id = _panel_message_id(old_message)
    if old_id is not None and old_id != sent_id:
        delete = getattr(old_message, "delete", None)
        if callable(delete):
            try:
                await delete()
            except Exception:
                logger.debug(
                    "replace_panel_with_photo stale panel delete failed chat_id=%s message_id=%s",
                    old_message.chat.id,
                    old_id,
                    exc_info=True,
                )
    return sent


async def panel_callback_edit(
    client: Client,
    query: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    *,
    answer: bool = True,
) -> bool:
    """Answer callback and edit the panel message (edit-first policy)."""
    from app.utils.callback_trace import trace_callback_event, trace_edit
    from app.utils.telegram_message import answer_callback_safe

    if answer:
        await answer_callback_safe(query)
    if query.message is None or query.from_user is None:
        trace_edit(query, result="failed", handler="panel_callback_edit")
        return False
    trace_callback_event(
        "callback.edit.start",
        query,
        handler="panel_callback_edit",
        target="callback_message",
    )
    ok = await safe_edit_message(query.message, text, reply_markup=reply_markup)
    if ok:
        await remember_panel_from_query(query)
        trace_edit(query, result="edited", handler="panel_callback_edit")
        return True

    sent = await safe_edit_or_send(
        client,
        query.message,
        text,
        reply_markup=reply_markup,
        fallback_chat_id=query.message.chat.id,
        attempt_edit=False,
    )
    if sent is not None:
        sent_id = _panel_message_id(sent)
        if sent_id is not None:
            await remember_panel_message(
                query.message.chat.id,
                query.from_user.id,
                sent_id,
            )
        trace_edit(query, result="fallback_sent", handler="panel_callback_edit")
        return True

    trace_edit(query, result="failed", handler="panel_callback_edit")
    return False
