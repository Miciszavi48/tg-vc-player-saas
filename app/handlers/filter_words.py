from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from app.handlers.priority import FILTER_WORDS_GROUP
from app.repositories import filter_repo, settings_repo
from app.utils.cache import get_filterwords_cached, set_filterwords_cached

logger = logging.getLogger(__name__)


async def _load_filter_words() -> list[str]:
    """Load filter words with Redis cache → DB fallback."""
    cached = await get_filterwords_cached()
    if cached is not None:
        return cached
    words = await filter_repo.get_filter_words()
    await set_filterwords_cached(words)
    return words


def register(bot: Client, call_py) -> None:  # noqa: ARG001

    @bot.on_message(filters.group & filters.text, group=FILTER_WORDS_GROUP)
    async def filter_words_watcher(client: Client, message: Message):
        """Check every group text message against filter words (§23.1–23.2).

        Spec: auto-delete matching messages silently.
        Developer is exempt.  All other users are subject to filtering.
        """
        user = message.from_user
        if user is None:
            message.continue_propagation()
            return
        from app.utils.bot_guards import is_developer

        if is_developer(user.id):
            message.continue_propagation()
            return

        cs = await settings_repo.get_chat_settings(message.chat.id)
        if cs is None or not cs.filter_enabled:
            message.continue_propagation()
            return

        words = await _load_filter_words()
        if not words:
            message.continue_propagation()
            return

        msg_text = (message.text or "").lower()
        if not msg_text:
            message.continue_propagation()
            return

        for word in words:
            if word.lower() in msg_text:
                try:
                    await message.delete()
                    logger.debug(
                        "filter_words_deleted chat_id=%s user_id=%s",
                        message.chat.id,
                        user.id,
                    )
                except Exception:
                    logger.debug("Failed to delete filtered message in %s", message.chat.id)
                return

        message.continue_propagation()
