from __future__ import annotations

from typing import Any

from app.repositories import settings_repo, user_repo
from app.utils.i18n import normalize_lang, set_current_lang


def _chat_type_value(chat: Any) -> str | None:
    raw_type = getattr(chat, "type", None)
    value = getattr(raw_type, "value", raw_type)
    return str(value) if value is not None else None


def _extract_chat_context(update: Any) -> tuple[int | None, str | None]:
    message = getattr(update, "message", None)
    if message is not None:
        chat = getattr(message, "chat", None)
        if chat is not None:
            return getattr(chat, "id", None), _chat_type_value(chat)
    chat = getattr(update, "chat", None)
    if chat is not None:
        return getattr(chat, "id", None), _chat_type_value(chat)
    return None, None


def _extract_user_id(update: Any) -> int | None:
    user = getattr(update, "from_user", None)
    if user is not None:
        return getattr(user, "id", None)
    message = getattr(update, "message", None)
    if message is not None:
        user = getattr(message, "from_user", None)
        if user is not None:
            return getattr(user, "id", None)
    return None


def _settings_chat_type(chat_type: str | None) -> str | None:
    if chat_type is None:
        return None
    if chat_type in ("group", "supergroup", "forum"):
        return "group"
    if chat_type == "channel":
        return "channel"
    return None


async def resolve_lang(
    *, chat_id: int | None, user_id: int | None = None, chat_type: str | None = None
) -> str:
    try:
        if chat_id is not None:
            settings_chat_type = _settings_chat_type(chat_type)
            if chat_type is None or settings_chat_type is not None:
                chat_settings = await settings_repo.get_chat_settings(
                    chat_id,
                    settings_chat_type or "group",
                )
                if chat_settings is not None:
                    lang = getattr(chat_settings, "language", None)
                    if lang:
                        return normalize_lang(str(lang))

        if user_id is not None:
            user = await user_repo.get_user(user_id)
            if user is not None:
                user_lang = getattr(user, "language", None) or getattr(user, "lang_code", None)
                if user_lang:
                    return normalize_lang(str(user_lang))
    except Exception:
        # Language lookup must never block UI rendering.
        return "fa"

    return "fa"


async def resolve_lang_from_update(update: Any) -> str:
    chat_id, chat_type = _extract_chat_context(update)
    return await resolve_lang(
        chat_id=chat_id,
        chat_type=chat_type,
        user_id=_extract_user_id(update),
    )


async def bind_lang_from_update(update: Any) -> str:
    lang = await resolve_lang_from_update(update)
    set_current_lang(lang)
    return lang
