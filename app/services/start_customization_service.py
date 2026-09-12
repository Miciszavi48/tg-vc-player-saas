"""Service and render descriptors for private start customization."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from random import Random
from typing import Protocol

from app.database.models import StartCustomizationMessage
from app.repositories import start_customization_repo as repo
from app.services.install_policy_service import InstallPolicyService
from app.utils.i18n import t

CATEGORY_START = repo.CATEGORY_START
CATEGORY_ABILITY = repo.CATEGORY_ABILITY
CATEGORY_TEST = repo.CATEGORY_TEST
CATEGORY_USE = repo.CATEGORY_USE
CATEGORY_HISTORY = repo.CATEGORY_HISTORY
CATEGORY_NOTE = repo.CATEGORY_NOTE
CATEGORIES = repo.CATEGORIES

SLOT_KEYS = repo.SLOT_KEYS
SLOT_INDEX_BY_KEY = repo.SLOT_INDEX_BY_KEY
SLOT_KEY_BY_INDEX = repo.SLOT_KEY_BY_INDEX
STYLE_SIMPLE = "simple"
STYLE_ADVANCED = "advanced"
DEFAULT_ADVANCED_COLOR_SEQUENCE = ("R", "B", "G", "N", "N", "N", "N", "R")
_DEFAULT_ADVANCED_COLOR_BY_SLOT = dict(
    zip(SLOT_KEYS, DEFAULT_ADVANCED_COLOR_SEQUENCE, strict=True)
)


def next_style_mode(style_mode: str) -> str:
    """Return the opposite start button style mode for panel toggles."""
    return STYLE_ADVANCED if style_mode == STYLE_SIMPLE else STYLE_SIMPLE


def effective_button_color_token(
    setting: ButtonSlotSetting,
    *,
    style_mode: str,
) -> str | None:
    """Resolve stored color overrides and the automatic advanced palette."""
    if style_mode != STYLE_ADVANCED:
        return None
    if setting.color_token is not None:
        return setting.color_token
    return _DEFAULT_ADVANCED_COLOR_BY_SLOT[setting.slot_key]

START_CATEGORY_CALLBACK_PREFIX = "start:cat:"
MAX_BUTTON_LABEL_CHARS = 64
MAX_MESSAGE_TEXT_CHARS = 4096
MAX_MESSAGE_CAPTION_CHARS = 1024

_INTERNAL_CATEGORY_BY_SLOT = {
    "test": CATEGORY_TEST,
    "use": CATEGORY_USE,
    "history": CATEGORY_HISTORY,
    "ability": CATEGORY_ABILITY,
    "note": CATEGORY_NOTE,
}
_URL_LINK_BY_SLOT = {
    "purchase": "creator",
    "commands": "guide_channel",
    "support": "support_group",
}
_NEW_LAYOUT_ROWS = (
    ("purchase",),
    ("test",),
    ("use",),
    ("history", "ability"),
    ("commands", "support"),
    ("note",),
)
_LEGACY_LINK_ORDER = (
    ("creator", "start.menu.buy_from_creator"),
    ("bot_channel", "start.menu.bot_channel"),
    ("support_group", "start.menu.support_group"),
    ("guide_channel", "start.menu.guide_channel"),
    ("custom_link", "start.menu.custom_link"),
    ("sudo_1", "start.menu.buy_from_sudo_1"),
    ("sudo_2", "start.menu.buy_from_sudo_2"),
)
class RandomChoice(Protocol):
    def choices(self, population: list[int], weights: list[int], k: int) -> list[int]:
        ...

    def choice(self, seq: list[int]) -> int:
        ...


@dataclass(frozen=True)
class EffectiveStyle:
    mode: str
    source_scope: str


@dataclass(frozen=True)
class ButtonSlotSetting:
    slot_index: int
    slot_key: str
    custom_text: str | None = None
    color_token: str | None = None
    emoji_text: str | None = None
    emoji_entities_json: str | None = None
    source_scope: str = "default"


@dataclass(frozen=True)
class StartButtonDescriptor:
    slot_index: int
    slot_key: str
    label: str
    base_label: str
    behavior: str
    url: str | None = None
    callback_data: str | None = None
    color_token: str | None = None
    emoji_text: str | None = None
    emoji_entities_json: str | None = None
    icon_custom_emoji_id: int | None = None
    source_scope: str = "default"


@dataclass(frozen=True)
class RenderedStartMenu:
    style_mode: str
    source_scope: str
    rows: tuple[tuple[StartButtonDescriptor, ...], ...]
    uses_legacy_fallback: bool


@dataclass(frozen=True)
class MessageItemDescriptor:
    id: int
    category: str
    source_scope: str
    send_strategy: str
    source_chat_id: int | None = None
    source_message_id: int | None = None
    source_chat_type: str | None = None
    message_type: str | None = None
    text: str | None = None
    caption: str | None = None
    media_file_id: str | None = None
    media_type: str | None = None
    entities_json: str | None = None
    extra_json: str | None = None


def normalize_scope(scope_type: str, owner_user_id: int | None = None) -> tuple[str, int]:
    return repo.normalize_scope(scope_type, owner_user_id)


def category_callback_data(category: str) -> str:
    return f"{START_CATEGORY_CALLBACK_PREFIX}{repo.validate_category(category)}"


def _normalize_color_token(word: str) -> str | None:
    w = word.strip().lower()
    if not w:
        return None
    if w in ("r", "red", "قرمز"):
        return "R"
    if w in ("g", "green", "سبز"):
        return "G"
    if w in ("b", "blue", "آبی"):
        return "B"
    if w in ("n", "normal", "none", "default", "بدون رنگ", "بدون"):
        return "N"
    return None


def _extract_color_token_from_line(line: str) -> str | None:
    stripped = str(line or "").strip()
    if not stripped:
        return None
    if "بدون" in stripped:
        return "N"
    parts = re.split(r"[-.:\s]+", stripped)
    parts = [part.strip() for part in parts if part.strip()]
    for part in parts:
        if part.isdigit():
            continue
        token = _normalize_color_token(part)
        if token is not None:
            return token
    if parts:
        return _normalize_color_token(parts[-1])
    return None


def parse_color_sequence(value: str) -> tuple[str, ...]:
    raw = str(value or "").strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) == len(SLOT_KEYS):
        indexed: dict[int, str] = {}
        positional: list[str] = []
        has_explicit_index = False
        for line in lines:
            match = re.match(r"^(\d+)\s*[-.:]\s*(.+)$", line)
            if match is not None:
                has_explicit_index = True
                slot_index = int(match.group(1))
                if slot_index < 1 or slot_index > len(SLOT_KEYS):
                    raise ValueError("color sequence must contain exactly eight tokens")
                color_token = _extract_color_token_from_line(match.group(2))
                if color_token is None:
                    raise ValueError("color sequence must contain exactly eight tokens")
                indexed[slot_index] = color_token
                continue
            color_token = _extract_color_token_from_line(line)
            if color_token is None:
                raise ValueError("color sequence must contain exactly eight tokens")
            positional.append(color_token)
        if has_explicit_index:
            if len(indexed) != len(SLOT_KEYS):
                raise ValueError("color sequence must contain exactly eight tokens")
            return tuple(indexed[index] for index in range(1, len(SLOT_KEYS) + 1))
        return tuple(positional)

    chars = [char.lower() for char in raw if char.isalpha()]
    if len(chars) == len(SLOT_KEYS):
        fallback_colors = []
        for char in chars:
            token = _normalize_color_token(char)
            if token is None:
                raise ValueError("color sequence must contain exactly eight tokens")
            fallback_colors.append(token)
        return tuple(fallback_colors)

    raise ValueError("color sequence must contain exactly eight tokens")


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _python_index_from_utf16_offset(value: str, offset: int) -> int:
    if offset < 0:
        raise ValueError("invalid UTF-16 entity offset")
    consumed = 0
    for index, char in enumerate(value):
        if consumed == offset:
            return index
        consumed += _utf16_length(char)
        if consumed > offset:
            raise ValueError("UTF-16 entity offset splits a surrogate pair")
    if consumed == offset:
        return len(value)
    raise ValueError("UTF-16 entity offset is outside the message")


def _load_entities(entities_json: str | None) -> list[dict[str, object]]:
    if not entities_json:
        return []
    try:
        raw = json.loads(entities_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _is_cluster_extension(char: str) -> bool:
    codepoint = ord(char)
    return (
        bool(unicodedata.combining(char))
        or codepoint in {0xFE0E, 0xFE0F, 0x20E3}
        or 0x1F3FB <= codepoint <= 0x1F3FF
        or 0xE0020 <= codepoint <= 0xE007F
    )


def _continuous_emoji_segments(value: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split an adjacent emoji run into pragmatic grapheme-like clusters."""
    segments: list[tuple[int, int]] = []
    index = start
    while index < end:
        cluster_start = index
        first = ord(value[index])
        index += 1
        if 0x1F1E6 <= first <= 0x1F1FF and index < end:
            second = ord(value[index])
            if 0x1F1E6 <= second <= 0x1F1FF:
                index += 1
        while index < end:
            if _is_cluster_extension(value[index]):
                index += 1
                continue
            if value[index] == "\u200d" and index + 1 < end:
                index += 2
                while index < end and _is_cluster_extension(value[index]):
                    index += 1
                continue
            break
        segments.append((cluster_start, index))
    return segments


def _emoji_slot_segments(
    value: str,
    entities_json: str | None = None,
) -> list[tuple[int, int, int, int]]:
    raw = str(value or "")
    token_matches = list(re.finditer(r"\S+", raw))
    python_segments: list[tuple[int, int]]
    if len(token_matches) == len(SLOT_KEYS):
        python_segments = [(match.start(), match.end()) for match in token_matches]
    else:
        custom_entities = [
            entry
            for entry in _load_entities(entities_json)
            if entry.get("custom_emoji_id") not in (None, "")
        ]
        custom_entities.sort(key=lambda entry: int(entry.get("offset", 0)))
        if len(custom_entities) == len(SLOT_KEYS):
            python_segments = []
            for entry in custom_entities:
                offset = int(entry.get("offset", 0))
                length = int(entry.get("length", 0))
                start = _python_index_from_utf16_offset(raw, offset)
                end = _python_index_from_utf16_offset(raw, offset + length)
                python_segments.append((start, end))
        else:
            stripped_start = len(raw) - len(raw.lstrip())
            stripped_end = len(raw.rstrip())
            if stripped_start >= stripped_end or any(
                char.isspace() for char in raw[stripped_start:stripped_end]
            ):
                raise ValueError("emoji sequence must contain exactly eight items")
            python_segments = _continuous_emoji_segments(raw, stripped_start, stripped_end)

    if len(python_segments) != len(SLOT_KEYS):
        raise ValueError("emoji sequence must contain exactly eight items")

    return [
        (
            start,
            end,
            _utf16_length(raw[:start]),
            _utf16_length(raw[:end]),
        )
        for start, end in python_segments
    ]


def split_emoji_entities_per_slot(
    value: str,
    entities_json: str | None,
) -> tuple[str | None, ...]:
    if not entities_json:
        return tuple(None for _ in SLOT_KEYS)
    all_entities = _load_entities(entities_json)
    if not all_entities:
        return tuple(None for _ in SLOT_KEYS)

    segments = _emoji_slot_segments(value, entities_json)
    result: list[str | None] = []
    for _python_start, _python_end, start, end in segments:
        slot_entities = []
        for entry in all_entities:
            if not isinstance(entry, dict):
                continue
            offset = int(entry.get("offset", 0))
            length = int(entry.get("length", 0))
            if offset >= start and offset + length <= end:
                slot_entities.append({**entry, "offset": offset - start})
        result.append(
            json.dumps(slot_entities, ensure_ascii=False) if slot_entities else None
        )
    return tuple(result)


def parse_button_text_lines(value: str) -> tuple[str, ...]:
    lines = [line.strip() for line in str(value or "").splitlines()]
    if len(lines) != len(SLOT_KEYS):
        raise ValueError("button text input must contain exactly eight lines")
    for line in lines:
        _validate_button_label(line)
    return tuple(lines)


def parse_emoji_sequence(
    value: str,
    entities_json: str | None = None,
) -> tuple[str | None, ...]:
    raw = str(value or "")
    items = tuple(raw[start:end] or None for start, end, _u16_start, _u16_end in _emoji_slot_segments(raw, entities_json))
    for item in items:
        if item is None or len(item) > 16:
            raise ValueError("invalid emoji item")
    return items


def _custom_emoji_id(entities_json: str | None) -> int | None:
    for entry in _load_entities(entities_json):
        raw_id = entry.get("custom_emoji_id")
        if raw_id in (None, ""):
            continue
        try:
            custom_emoji_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if custom_emoji_id > 0:
            return custom_emoji_id
    return None


def _validate_button_label(value: str) -> str:
    label = str(value or "").strip()
    if not label:
        raise ValueError("button label must not be empty")
    if len(label) > MAX_BUTTON_LABEL_CHARS:
        raise ValueError("button label is too long")
    return label


def _validate_message_payload(
    *,
    text: str | None,
    caption: str | None,
    media_file_id: str | None,
    source_chat_id: int | None,
    source_message_id: int | None,
) -> None:
    has_source = source_chat_id is not None and source_message_id is not None
    has_text = bool(str(text or "").strip())
    has_caption = bool(str(caption or "").strip())
    has_media = bool(str(media_file_id or "").strip())
    if not (has_source or has_text or has_caption or has_media):
        raise ValueError("category message must not be empty")
    if text is not None and len(str(text)) > MAX_MESSAGE_TEXT_CHARS:
        raise ValueError("message text is too long")
    if caption is not None and len(str(caption)) > MAX_MESSAGE_CAPTION_CHARS:
        raise ValueError("message caption is too long")


def _message_descriptor(
    row: StartCustomizationMessage,
    *,
    source_scope: str,
) -> MessageItemDescriptor:
    if row.source_chat_id is not None and row.source_message_id is not None:
        strategy = "copy_source"
    elif row.media_file_id:
        strategy = "send_media"
    elif row.text or row.caption:
        strategy = "send_text"
    else:
        strategy = "unavailable"
    return MessageItemDescriptor(
        id=int(row.id),
        category=str(row.category),
        source_scope=source_scope,
        send_strategy=strategy,
        source_chat_id=row.source_chat_id,
        source_message_id=row.source_message_id,
        source_chat_type=row.source_chat_type,
        message_type=row.message_type,
        text=row.text,
        caption=row.caption,
        media_file_id=row.media_file_id,
        media_type=row.media_type,
        entities_json=row.entities_json,
        extra_json=row.extra_json,
    )


async def get_effective_style_mode(
    *, owner_user_id: int | None = None
) -> EffectiveStyle:
    if owner_user_id is not None:
        owner_style = await repo.get_style_config(
            scope_type="owner",
            owner_user_id=owner_user_id,
        )
        if owner_style is not None:
            return EffectiveStyle(owner_style.style_mode, "owner")

    global_style = await repo.get_style_config(scope_type="global")
    if global_style is not None:
        return EffectiveStyle(global_style.style_mode, "global")
    return EffectiveStyle(STYLE_SIMPLE, "default")


async def get_effective_button_slot_settings(
    *, owner_user_id: int | None = None
) -> tuple[ButtonSlotSetting, ...]:
    global_rows = await repo.list_button_slot_configs(scope_type="global")
    owner_rows = []
    if owner_user_id is not None:
        owner_rows = await repo.list_button_slot_configs(
            scope_type="owner",
            owner_user_id=owner_user_id,
        )

    global_by_key = {row.slot_key: row for row in global_rows}
    owner_by_key = {row.slot_key: row for row in owner_rows}
    settings: list[ButtonSlotSetting] = []
    for slot_index, slot_key in enumerate(SLOT_KEYS, start=1):
        global_row = global_by_key.get(slot_key)
        owner_row = owner_by_key.get(slot_key)
        custom_text = _first_not_none(
            getattr(owner_row, "custom_text", None),
            getattr(global_row, "custom_text", None),
        )
        color_token = _first_not_none(
            getattr(owner_row, "color_token", None),
            getattr(global_row, "color_token", None),
        )
        emoji_text = _first_not_none(
            getattr(owner_row, "emoji_text", None),
            getattr(global_row, "emoji_text", None),
        )
        emoji_entities_json = _first_not_none(
            getattr(owner_row, "emoji_entities_json", None),
            getattr(global_row, "emoji_entities_json", None),
        )
        source_scope = "default"
        if any(
            value is not None
            for value in (
                getattr(owner_row, "custom_text", None),
                getattr(owner_row, "color_token", None),
                getattr(owner_row, "emoji_text", None),
            )
        ):
            source_scope = "owner"
        elif any(
            value is not None
            for value in (
                getattr(global_row, "custom_text", None),
                getattr(global_row, "color_token", None),
                getattr(global_row, "emoji_text", None),
            )
        ):
            source_scope = "global"
        settings.append(
            ButtonSlotSetting(
                slot_index=slot_index,
                slot_key=slot_key,
                custom_text=custom_text,
                color_token=color_token,
                emoji_text=emoji_text,
                emoji_entities_json=emoji_entities_json,
                source_scope=source_scope,
            )
        )
    return tuple(settings)


def _first_not_none(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


async def is_test_category_exposed(*, chat_id: int | None = None) -> bool:
    if chat_id is not None and await InstallPolicyService.is_free_install(chat_id):
        return False
    policy = await InstallPolicyService.get_policy()
    if policy is None:
        return False
    mode = str(getattr(policy, "policy_mode", "") or "").strip().lower()
    if mode in {"free", "open"}:
        return False
    charge_on_install = bool(getattr(policy, "charge_on_install", False))
    return charge_on_install and mode in {"paid", "hybrid"}


async def get_random_message_item(
    *,
    category: str,
    owner_user_id: int | None = None,
    chat_id: int | None = None,
    rng: RandomChoice | None = None,
) -> MessageItemDescriptor | None:
    category_value = repo.validate_category(category)
    if category_value == CATEGORY_TEST and not await is_test_category_exposed(
        chat_id=chat_id
    ):
        return None

    if owner_user_id is not None:
        owner_rows = await repo.list_active_message_items(
            scope_type="owner",
            owner_user_id=owner_user_id,
            category=category_value,
        )
        if owner_rows:
            return _select_message(owner_rows, rng=rng, source_scope="owner")

    global_rows = await repo.list_active_message_items(
        scope_type="global",
        category=category_value,
    )
    if not global_rows:
        return None
    return _select_message(global_rows, rng=rng, source_scope="global")


def _select_message(
    rows: list[StartCustomizationMessage],
    *,
    rng: RandomChoice | None,
    source_scope: str,
) -> MessageItemDescriptor:
    randomizer = rng or Random()
    indexes = list(range(len(rows)))
    weights = [max(1, int(getattr(row, "weight", 1) or 1)) for row in rows]
    if hasattr(randomizer, "choices"):
        index = randomizer.choices(indexes, weights=weights, k=1)[0]
    else:
        index = randomizer.choice(indexes)
    return _message_descriptor(rows[index], source_scope=source_scope)


async def list_message_pool_state(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    category: str,
) -> tuple[MessageItemDescriptor, ...]:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    rows = await repo.list_active_message_items(
        scope_type=scope,
        owner_user_id=owner_id if scope == "owner" else None,
        category=category,
    )
    return tuple(_message_descriptor(row, source_scope=scope) for row in rows)


async def add_message_pool_item(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    category: str,
    actor_user_id: int | None,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    source_chat_type: str | None = None,
    message_type: str | None = None,
    text: str | None = None,
    caption: str | None = None,
    media_file_id: str | None = None,
    media_type: str | None = None,
    entities_json: str | None = None,
    extra_json: str | None = None,
    sort_order: int = 0,
    weight: int = 1,
) -> MessageItemDescriptor:
    _validate_message_payload(
        text=text,
        caption=caption,
        media_file_id=media_file_id,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
    )
    row = await repo.create_message_item(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        category=category,
        source_chat_id=source_chat_id,
        source_message_id=source_message_id,
        source_chat_type=source_chat_type,
        message_type=message_type,
        text=text,
        caption=caption,
        media_file_id=media_file_id,
        media_type=media_type,
        entities_json=entities_json,
        extra_json=extra_json,
        sort_order=sort_order,
        weight=weight,
        created_by=actor_user_id,
        updated_by=actor_user_id,
    )
    scope, _ = normalize_scope(scope_type, owner_user_id)
    return _message_descriptor(row, source_scope=scope)


async def clear_category_pool(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    category: str,
    actor_user_id: int | None,
) -> int:
    return await repo.clear_message_category(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        category=category,
        updated_by=actor_user_id,
    )


async def set_style_mode(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    style_mode: str,
    actor_user_id: int | None,
) -> EffectiveStyle:
    row = await repo.set_style_mode(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        style_mode=style_mode,
        updated_by=actor_user_id,
    )
    return EffectiveStyle(row.style_mode, row.scope_type)


async def toggle_style_mode(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    actor_user_id: int | None,
) -> EffectiveStyle:
    current = await get_effective_style_mode(owner_user_id=owner_user_id)
    row = await repo.toggle_style_mode(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        initial_mode=next_style_mode(current.mode),
        updated_by=actor_user_id,
    )
    return EffectiveStyle(row.style_mode, row.scope_type)


async def set_button_text_lines(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    value: str,
    actor_user_id: int | None,
) -> tuple[ButtonSlotSetting, ...]:
    lines = parse_button_text_lines(value)
    current = await get_effective_button_slot_settings(owner_user_id=owner_user_id)
    by_index = {setting.slot_index: setting for setting in current}
    for index, label in enumerate(lines, start=1):
        setting = by_index[index]
        _compose_button_label(label, setting.emoji_text, setting.emoji_entities_json)
    slot_values = {
        index: {"custom_text": lines[index - 1]}
        for index in range(1, len(SLOT_KEYS) + 1)
    }
    await repo.upsert_button_slot_configs(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        slot_values=slot_values,
        updated_by=actor_user_id,
    )
    return await get_effective_button_slot_settings(owner_user_id=owner_user_id)


async def set_button_color_sequence(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    value: str,
    actor_user_id: int | None,
) -> tuple[ButtonSlotSetting, ...]:
    colors = parse_color_sequence(value)
    slot_values = {
        index: {"color_token": colors[index - 1]}
        for index in range(1, len(SLOT_KEYS) + 1)
    }
    await repo.upsert_button_slot_configs(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        slot_values=slot_values,
        updated_by=actor_user_id,
    )
    return await get_effective_button_slot_settings(owner_user_id=owner_user_id)


async def set_button_emoji_sequence(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    value: str,
    emoji_entities_json: str | None = None,
    actor_user_id: int | None,
) -> tuple[ButtonSlotSetting, ...]:
    emojis = parse_emoji_sequence(value, emoji_entities_json)
    per_slot_entities = split_emoji_entities_per_slot(value, emoji_entities_json)
    current = await get_effective_button_slot_settings(owner_user_id=owner_user_id)
    by_index = {setting.slot_index: setting for setting in current}
    for index, emoji in enumerate(emojis, start=1):
        setting = by_index[index]
        if setting.custom_text:
            candidate_labels = (setting.custom_text,)
        else:
            candidate_labels = (
                _base_label("fa", setting.slot_key),
                _base_label("en", setting.slot_key),
            )
        for candidate in candidate_labels:
            _compose_button_label(candidate, emoji, per_slot_entities[index - 1])
    slot_values = {
        index: {
            "emoji_text": emojis[index - 1],
            "emoji_entities_json": per_slot_entities[index - 1],
        }
        for index in range(1, len(SLOT_KEYS) + 1)
    }
    await repo.upsert_button_slot_configs(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        slot_values=slot_values,
        updated_by=actor_user_id,
    )
    return await get_effective_button_slot_settings(owner_user_id=owner_user_id)


async def clear_button_part(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    slot: int | str,
    part: str,
    actor_user_id: int | None,
) -> bool:
    return await repo.clear_button_slot_part(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        slot=slot,
        part=part,
        updated_by=actor_user_id,
    )


async def clear_button_part_sequence(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    part: str,
    actor_user_id: int | None,
) -> int:
    return await repo.clear_button_slot_part_for_all_slots(
        scope_type=scope_type,
        owner_user_id=owner_user_id,
        part=part,
        updated_by=actor_user_id,
    )


async def render_start_menu(
    lang: str,
    links: dict[str, str],
    *,
    owner_user_id: int | None = None,
    chat_id: int | None = None,
    expose_test_slot: bool | None = None,
) -> RenderedStartMenu:
    style = await get_effective_style_mode(owner_user_id=owner_user_id)
    settings = await get_effective_button_slot_settings(owner_user_id=owner_user_id)
    has_customization = await _has_any_customization(owner_user_id=owner_user_id)
    if not has_customization:
        return _legacy_rendered_menu(lang, links)

    test_exposed = (
        await is_test_category_exposed(chat_id=chat_id)
        if expose_test_slot is None
        else expose_test_slot
    )
    by_key = {setting.slot_key: setting for setting in settings}
    rendered_rows: list[tuple[StartButtonDescriptor, ...]] = []
    for row_keys in _NEW_LAYOUT_ROWS:
        row: list[StartButtonDescriptor] = []
        for slot_key in row_keys:
            if slot_key == "test" and not test_exposed:
                continue
            descriptor = _render_slot(
                lang,
                links,
                by_key[slot_key],
                style_mode=style.mode,
            )
            if descriptor is not None:
                row.append(descriptor)
        if row:
            rendered_rows.append(tuple(row))
    return RenderedStartMenu(
        style_mode=style.mode,
        source_scope=style.source_scope,
        rows=tuple(rendered_rows),
        uses_legacy_fallback=False,
    )


def _render_slot(
    lang: str,
    links: dict[str, str],
    setting: ButtonSlotSetting,
    *,
    style_mode: str,
) -> StartButtonDescriptor | None:
    slot_key = setting.slot_key
    base_label = _base_label(lang, slot_key)
    label = _validate_button_label(setting.custom_text or base_label)
    color_token = None
    emoji_text = None
    emoji_entities_json = None
    icon_custom_emoji_id = None
    if style_mode == STYLE_ADVANCED:
        color_token = effective_button_color_token(setting, style_mode=style_mode)
        emoji_text = setting.emoji_text
        emoji_entities_json = setting.emoji_entities_json
        icon_custom_emoji_id = _custom_emoji_id(emoji_entities_json)
        label = _compose_button_label(label, emoji_text, emoji_entities_json)

    if slot_key in _URL_LINK_BY_SLOT:
        url = _link(links, _URL_LINK_BY_SLOT[slot_key])
        if not url:
            return None
        return StartButtonDescriptor(
            slot_index=setting.slot_index,
            slot_key=slot_key,
            label=label,
            base_label=base_label,
            behavior="url",
            url=url,
            color_token=color_token,
            emoji_text=emoji_text,
            emoji_entities_json=emoji_entities_json,
            icon_custom_emoji_id=icon_custom_emoji_id,
            source_scope=setting.source_scope,
        )

    category = _INTERNAL_CATEGORY_BY_SLOT[slot_key]
    return StartButtonDescriptor(
        slot_index=setting.slot_index,
        slot_key=slot_key,
        label=label,
        base_label=base_label,
        behavior="callback",
        callback_data=category_callback_data(category),
        color_token=color_token,
        emoji_text=emoji_text,
        emoji_entities_json=emoji_entities_json,
        icon_custom_emoji_id=icon_custom_emoji_id,
        source_scope=setting.source_scope,
    )


def _legacy_rendered_menu(lang: str, links: dict[str, str]) -> RenderedStartMenu:
    rows: list[tuple[StartButtonDescriptor, ...]] = []
    creator = _legacy_url_descriptor(lang, links, "creator", "start.menu.buy_from_creator")
    if creator is not None:
        rows.append((creator,))

    secondary: list[StartButtonDescriptor] = []
    for link_name, label_key in _LEGACY_LINK_ORDER[1:]:
        descriptor = _legacy_url_descriptor(lang, links, link_name, label_key)
        if descriptor is not None:
            secondary.append(descriptor)
    for index in range(0, len(secondary), 2):
        rows.append(tuple(secondary[index : index + 2]))

    return RenderedStartMenu(
        style_mode=STYLE_SIMPLE,
        source_scope="default",
        rows=tuple(rows),
        uses_legacy_fallback=True,
    )


def _legacy_url_descriptor(
    lang: str,
    links: dict[str, str],
    link_name: str,
    label_key: str,
) -> StartButtonDescriptor | None:
    url = _link(links, link_name)
    if not url:
        return None
    label = t(lang, label_key)
    return StartButtonDescriptor(
        slot_index=0,
        slot_key=link_name,
        label=label,
        base_label=label,
        behavior="url",
        url=url,
    )


async def _has_any_customization(*, owner_user_id: int | None = None) -> bool:
    if await repo.get_style_config(scope_type="global") is not None:
        return True
    if await repo.list_button_slot_configs(scope_type="global"):
        return True
    for category in CATEGORIES:
        if await repo.list_active_message_items(scope_type="global", category=category):
            return True

    if owner_user_id is None:
        return False
    if await repo.get_style_config(
        scope_type="owner",
        owner_user_id=owner_user_id,
    ) is not None:
        return True
    if await repo.list_button_slot_configs(
        scope_type="owner",
        owner_user_id=owner_user_id,
    ):
        return True
    for category in CATEGORIES:
        if await repo.list_active_message_items(
            scope_type="owner",
            owner_user_id=owner_user_id,
            category=category,
        ):
            return True
    return False


def _base_label(lang: str, slot_key: str) -> str:
    return t(lang, f"start.customization.buttons.{slot_key}")


def _compose_button_label(
    label: str,
    emoji_text: str | None,
    emoji_entities_json: str | None,
) -> str:
    base = _validate_button_label(label)
    if emoji_text and _custom_emoji_id(emoji_entities_json) is None:
        return _validate_button_label(f"{str(emoji_text).strip()} {base}")
    return base


def _link(links: dict[str, str], name: str) -> str:
    return str(links.get(name) or "").strip()
