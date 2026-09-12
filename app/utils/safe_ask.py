from __future__ import annotations

import asyncio
import logging
from typing import Any

from pyrogram import Client, filters
from pyrogram.types import Message
from pyromod.exceptions import ListenerStopped

from app.utils.ask_result import safe_stop_listening
from app.utils.i18n import t

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60


async def safe_ask(
    client: Client,
    chat_id: int,
    prompt_key: str,
    lang: str = "fa",
    timeout: int = _DEFAULT_TIMEOUT,
    user_id: int | None = None,
    reply_markup: Any | None = None,
    **prompt_kwargs: Any,
) -> Message | None:
    """Send a prompt and wait for a text reply.

    Tries ``client.ask()`` (pyromod) first; falls back to manual
    ``wait_for_message`` on any failure so admin flows never break.
    Cancels any stale pyromod listener for this chat before registering
    a new one.
    """
    prompt_text = t(lang, prompt_key, **prompt_kwargs)

    stopped = await safe_stop_listening(client, chat_id, user_id=user_id)
    logger.debug(
        "safe_ask start prompt_key=%s chat_id=%s user_id=%s stop_listening=%s",
        prompt_key,
        chat_id,
        user_id,
        stopped,
    )

    try:
        response = await client.ask(
            chat_id, prompt_text, timeout=timeout,
            filters=filters.user(user_id) if user_id else None,
            reply_markup=reply_markup,
        )
        return response
    except ListenerStopped:
        logger.debug("safe_ask listener stopped prompt_key=%s chat_id=%s user_id=%s", prompt_key, chat_id, user_id)
        await client.send_message(chat_id, t(lang, "texts_links.ask_cancelled_or_timeout"))
        return None
    except (ImportError, AttributeError):
        logger.debug("pyromod ask() unavailable, using fallback")
    except asyncio.TimeoutError:
        await client.send_message(chat_id, t(lang, "ask.timeout"))
        return None
    except Exception:
        logger.debug("ask() failed, falling back to manual wait")

    return await _fallback_ask(client, chat_id, prompt_text, timeout, user_id, lang, reply_markup)


async def _fallback_ask(
    client: Client,
    chat_id: int,
    prompt_text: str,
    timeout: int,
    user_id: int | None,
    lang: str,
    reply_markup: Any | None = None,
) -> Message | None:
    """Manual wait-for-message fallback when pyromod is unavailable."""
    await client.send_message(chat_id, prompt_text, reply_markup=reply_markup)

    future: asyncio.Future[Message] = asyncio.get_event_loop().create_future()

    async def _listener(_: Client, msg: Message) -> None:
        if not future.done():
            future.set_result(msg)

    # add_handler returns the Handler object; remove_handler needs that object,
    # not the callback function — passing the function makes the (async)
    # removal fail silently and leaks a permanent group-99 text listener.
    # Imported lazily: app.handlers.__init__ imports modules that import this
    # one (call_security_panel -> safe_ask), so a module-level import here makes
    # `import app.utils.safe_ask` before app.handlers a circular-import failure.
    from app.handlers.priority import SAFE_ASK_LISTENER_GROUP
    from pyrogram.handlers import MessageHandler

    handler = MessageHandler(
        _listener,
        filters.chat(chat_id)
        & (filters.user(user_id) if user_id else filters.all)
        & filters.text
        & ~filters.command(["start", "cancel"]),
    )
    client.add_handler(handler, group=SAFE_ASK_LISTENER_GROUP)

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        await client.send_message(chat_id, t(lang, "ask.timeout"))
        return None
    finally:
        try:
            client.remove_handler(handler, group=SAFE_ASK_LISTENER_GROUP)
        except (ValueError, KeyError):
            pass
