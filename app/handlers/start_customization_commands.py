"""Reply-based start customization command handlers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any

from pyrogram import Client
from pyrogram.types import Message

from app.handlers.priority import PRIORITY_COMMAND_GROUP
from app.repositories import user_repo
from app.services import start_customization_service as start_custom
from app.services.bot_settings_service import (
    resolve_log_channel_id,
    resolve_single_active_owner_user_id,
)
from app.utils.bot_guards import is_developer
from app.utils.i18n import AUTO_LANG, t
from app.utils.start_customization_commands import (
    StartCustomizationCommand,
    parse_start_customization_command,
    start_customization_command_filter,
)

logger = logging.getLogger(__name__)

_LANG = AUTO_LANG
_MEDIA_ATTRS: tuple[tuple[str, str], ...] = (
    ("photo", "photo"),
    ("video", "video"),
    ("document", "document"),
    ("audio", "audio"),
    ("animation", "animation"),
    ("voice", "voice"),
    ("video_note", "video_note"),
    ("sticker", "sticker"),
)
_BUTTON_PART_TO_SERVICE_PART = {
    "color": "color",
    "emoji": "emoji",
    "text": "text",
}


@dataclass(frozen=True)
class StartCustomizationTarget:
    label: str
    scope_type: str
    owner_user_id: int | None = None


@dataclass(frozen=True)
class TargetExecutionResult:
    target: StartCustomizationTarget
    ok: bool
    reason: str
    count: int = 0


def _chat_type_value(message: Message) -> str:
    chat_type = getattr(getattr(message, "chat", None), "type", None)
    return str(getattr(chat_type, "value", chat_type) or "")


def _is_private_message(message: Message) -> bool:
    return _chat_type_value(message) == "private"


def _is_report_candidate(message: Message) -> bool:
    return _chat_type_value(message) in {"group", "supergroup", "channel"}


def _actor_user_id(message: Message) -> int | None:
    user_id = getattr(getattr(message, "from_user", None), "id", None)
    return int(user_id) if user_id is not None else None


async def _reply(message: Message, text: str, **kwargs: Any) -> None:
    reply = getattr(message, "reply", None)
    if callable(reply):
        await reply(text, **kwargs)
        return
    reply_text = getattr(message, "reply_text", None)
    if callable(reply_text):
        await reply_text(text, **kwargs)


def _stop_message_propagation(message: Message) -> None:
    stop_propagation = getattr(message, "stop_propagation", None)
    if callable(stop_propagation):
        stop_propagation()


async def _is_trusted_report_group(message: Message) -> bool:
    configured = await resolve_log_channel_id()
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    return configured is not None and chat_id is not None and int(chat_id) == configured


async def _resolve_targets(
    message: Message,
    *,
    actor_user_id: int,
) -> tuple[list[StartCustomizationTarget], str | None]:
    if _is_private_message(message):
        if is_developer(actor_user_id):
            return [StartCustomizationTarget("global", "global")], None
        if await user_repo.is_owner(actor_user_id):
            operational_owner_id = await resolve_single_active_owner_user_id()
            if operational_owner_id != actor_user_id:
                return [], "owner_scope_inactive"
            return [
                StartCustomizationTarget(
                    f"owner:{actor_user_id}",
                    "owner",
                    actor_user_id,
                )
            ], None
        return [], "no_access"

    if _is_report_candidate(message):
        if not await _is_trusted_report_group(message):
            return [], "untrusted_report_group"
        actor_is_developer = is_developer(actor_user_id)
        actor_is_owner = await user_repo.is_owner(actor_user_id)
        if not (actor_is_developer or actor_is_owner):
            return [], "no_access"
        owner_user_id = await resolve_single_active_owner_user_id()
        if owner_user_id is None:
            return [], "owner_scope_inactive"
        if actor_is_owner and not actor_is_developer and actor_user_id != owner_user_id:
            return [], "no_access"
        return [
            StartCustomizationTarget(
                f"owner:{owner_user_id}",
                "owner",
                owner_user_id,
            )
        ], None

    return [], "wrong_scope"


def _serialize_entities(entities: object | None) -> str | None:
    if not entities:
        return None
    result: list[dict[str, object]] = []
    for entity in entities:
        entity_type = getattr(entity, "type", None)
        entry: dict[str, object] = {
            "type": getattr(entity_type, "value", None) or str(entity_type),
            "offset": int(getattr(entity, "offset", 0)),
            "length": int(getattr(entity, "length", 0)),
        }
        for attr in ("url", "language", "custom_emoji_id"):
            value = getattr(entity, attr, None)
            if value:
                entry[attr] = value
        user = getattr(entity, "user", None)
        user_id = getattr(user, "id", None)
        if user_id is not None:
            entry["user_id"] = int(user_id)
        result.append(entry)
    return json.dumps(result, ensure_ascii=False)


def _message_id(message: Message) -> int | None:
    value = getattr(message, "id", None)
    if value is None:
        value = getattr(message, "message_id", None)
    return int(value) if value is not None else None


def _media_file_id(media: object) -> str | None:
    if isinstance(media, list):
        if not media:
            return None
        media = media[-1]
    file_id = getattr(media, "file_id", None)
    return str(file_id) if file_id else None


def _extract_source_payload(message: Message) -> dict[str, Any] | None:
    source_chat = getattr(message, "chat", None)
    source_chat_id = getattr(source_chat, "id", None)
    source_message_id = _message_id(message)
    source_chat_type = str(
        getattr(getattr(source_chat, "type", None), "value", None)
        or getattr(source_chat, "type", "")
        or ""
    )

    text = getattr(message, "text", None)
    if text:
        return {
            "source_chat_id": source_chat_id,
            "source_message_id": source_message_id,
            "source_chat_type": source_chat_type,
            "message_type": "text",
            "text": text,
            "caption": None,
            "media_file_id": None,
            "media_type": None,
            "entities_json": _serialize_entities(getattr(message, "entities", None)),
        }

    for attr, media_type in _MEDIA_ATTRS:
        media = getattr(message, attr, None)
        file_id = _media_file_id(media) if media else None
        if file_id:
            return {
                "source_chat_id": source_chat_id,
                "source_message_id": source_message_id,
                "source_chat_type": source_chat_type,
                "message_type": media_type,
                "text": None,
                "caption": getattr(message, "caption", None),
                "media_file_id": file_id,
                "media_type": media_type,
                "entities_json": _serialize_entities(
                    getattr(message, "caption_entities", None)
                ),
            }

    caption = getattr(message, "caption", None)
    if caption:
        return {
            "source_chat_id": source_chat_id,
            "source_message_id": source_message_id,
            "source_chat_type": source_chat_type,
            "message_type": "text",
            "text": caption,
            "caption": None,
            "media_file_id": None,
            "media_type": None,
            "entities_json": _serialize_entities(
                getattr(message, "caption_entities", None)
            ),
        }

    return None


def _reply_text(message: Message) -> str:
    reply = getattr(message, "reply_to_message", None)
    return str(
        getattr(reply, "text", None)
        or getattr(reply, "caption", None)
        or ""
    )


async def _execute_for_target(
    parsed: StartCustomizationCommand,
    message: Message,
    target: StartCustomizationTarget,
    *,
    actor_user_id: int,
) -> TargetExecutionResult:
    try:
        if parsed.target == "message":
            category = parsed.category or ""
            if parsed.action == "add":
                if category == "test" and not await start_custom.is_test_category_exposed():
                    return TargetExecutionResult(target, False, "test_policy_denied")
                reply = getattr(message, "reply_to_message", None)
                if reply is None:
                    return TargetExecutionResult(target, False, "no_reply")
                payload = _extract_source_payload(reply)
                if payload is None:
                    return TargetExecutionResult(target, False, "unsupported_media")
                try:
                    await start_custom.add_message_pool_item(
                        scope_type=target.scope_type,
                        owner_user_id=target.owner_user_id,
                        category=category,
                        actor_user_id=actor_user_id,
                        **payload,
                    )
                except ValueError:
                    return TargetExecutionResult(target, False, "invalid_content")
                return TargetExecutionResult(target, True, "added", 1)

            count = await start_custom.clear_category_pool(
                scope_type=target.scope_type,
                owner_user_id=target.owner_user_id,
                category=category,
                actor_user_id=actor_user_id,
            )
            return TargetExecutionResult(target, True, "cleaned", int(count))

        part = parsed.button_part or ""
        service_part = _BUTTON_PART_TO_SERVICE_PART.get(part)
        if service_part is None:
            return TargetExecutionResult(target, False, "unsupported_command")

        if parsed.action == "clean":
            count = await start_custom.clear_button_part_sequence(
                scope_type=target.scope_type,
                owner_user_id=target.owner_user_id,
                part=service_part,
                actor_user_id=actor_user_id,
            )
            return TargetExecutionResult(target, True, "cleaned", int(count))

        reply = getattr(message, "reply_to_message", None)
        if reply is None:
            return TargetExecutionResult(target, False, "no_reply")
        value = _reply_text(message)
        if not value.strip():
            return TargetExecutionResult(target, False, "unsupported_media")

        try:
            if part == "color":
                await start_custom.set_button_color_sequence(
                    scope_type=target.scope_type,
                    owner_user_id=target.owner_user_id,
                    value=value,
                    actor_user_id=actor_user_id,
                )
            elif part == "emoji":
                await start_custom.set_button_emoji_sequence(
                    scope_type=target.scope_type,
                    owner_user_id=target.owner_user_id,
                    value=value,
                    emoji_entities_json=_serialize_entities(
                        getattr(reply, "entities", None)
                        or getattr(reply, "caption_entities", None)
                    ),
                    actor_user_id=actor_user_id,
                )
            else:
                await start_custom.set_button_text_lines(
                    scope_type=target.scope_type,
                    owner_user_id=target.owner_user_id,
                    value=value,
                    actor_user_id=actor_user_id,
                )
        except ValueError:
            return TargetExecutionResult(target, False, f"invalid_{part}")
        return TargetExecutionResult(target, True, "added", 8)
    except Exception:
        logger.exception(
            "start customization command failed action=%s target=%s scope=%s",
            parsed.action,
            parsed.target,
            target.label,
        )
        return TargetExecutionResult(target, False, "db_error")


def _category_label(category: str | None) -> str:
    key = f"start_customization_commands.categories.{category or 'start'}"
    return t(_LANG, key)


def _button_part_label(part: str | None) -> str:
    key = f"start_customization_commands.button_parts.{part or 'text'}"
    return t(_LANG, key)


def _item_label(parsed: StartCustomizationCommand) -> str:
    if parsed.target == "message":
        return _category_label(parsed.category)
    return _button_part_label(parsed.button_part)


def _failure_text(reason: str) -> str:
    key = {
        "no_reply": "start_customization_commands.no_reply",
        "invalid_color": "start_customization_commands.invalid_color",
        "invalid_emoji": "start_customization_commands.invalid_emoji",
        "invalid_text": "start_customization_commands.invalid_text_lines",
        "invalid_content": "start_customization_commands.invalid_content",
        "unsupported_media": "start_customization_commands.unsupported_media",
        "no_access": "start_customization_commands.no_access",
        "untrusted_report_group": "start_customization_commands.untrusted_report_group",
        "test_policy_denied": "start_customization_commands.test_policy_denied",
        "owner_scope_inactive": "start_customization_commands.owner_scope_inactive",
        "wrong_scope": "start_customization_commands.wrong_scope",
    }.get(reason, "start_customization_commands.db_error")
    return t(_LANG, key)


def _success_text(
    parsed: StartCustomizationCommand,
    results: list[TargetExecutionResult],
) -> str:
    ok_count = sum(1 for result in results if result.ok)
    total = len(results)
    item = _item_label(parsed)
    if ok_count != total:
        failures = total - ok_count
        return t(
            _LANG,
            "start_customization_commands.fanout_partial",
            ok=ok_count,
            failed=failures,
        )
    if parsed.action == "add":
        if parsed.target == "message":
            return t(
                _LANG,
                "start_customization_commands.add_message_success",
                item=item,
            )
        if parsed.button_part == "color":
            return t(_LANG, "start_customization_commands.add_color_success")
        return t(_LANG, "start_customization_commands.add_success", item=item)
    changed = sum(result.count for result in results)
    if parsed.target == "button" and parsed.button_part == "color":
        return t(
            _LANG,
            "start_customization_commands.clean_color_success",
            count=changed,
        )
    return t(
        _LANG,
        "start_customization_commands.clean_success",
        item=item,
        count=changed,
    )


async def _handle_command(
    client: Client,
    message: Message,
    parsed: StartCustomizationCommand,
) -> None:
    actor = _actor_user_id(message)
    if actor is None:
        await _reply(message, _failure_text("no_access"))
        return

    targets, error = await _resolve_targets(message, actor_user_id=actor)
    if error is not None:
        await _reply(message, _failure_text(error))
        return

    results = [
        await _execute_for_target(parsed, message, target, actor_user_id=actor)
        for target in targets
    ]
    if not results:
        await _reply(message, _failure_text("db_error"))
        return
    if all(not result.ok for result in results):
        await _reply(message, _failure_text(results[0].reason))
        return

    await _reply(message, _success_text(parsed, results))


def register(bot: Client, call_py) -> None:  # noqa: ARG001
    """Register start customization text commands."""

    @bot.on_message(start_customization_command_filter(), group=PRIORITY_COMMAND_GROUP)
    async def start_customization_command(client: Client, message: Message):
        parsed = parse_start_customization_command(message)
        if parsed is None:
            continue_propagation = getattr(message, "continue_propagation", None)
            if callable(continue_propagation):
                continue_propagation()
            return

        await _handle_command(client, message, parsed)
        _stop_message_propagation(message)
