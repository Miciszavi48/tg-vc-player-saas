"""Repository helpers for durable private start customization state."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select, update

from app.database.engine import async_session
from app.database.models import (
    StartButtonConfig,
    StartCustomizationMessage,
    StartStyleConfig,
)

CATEGORY_START = "start"
CATEGORY_ABILITY = "ability"
CATEGORY_TEST = "test"
CATEGORY_USE = "use"
CATEGORY_HISTORY = "history"
CATEGORY_NOTE = "note"

CATEGORIES: tuple[str, ...] = (
    CATEGORY_START,
    CATEGORY_ABILITY,
    CATEGORY_TEST,
    CATEGORY_USE,
    CATEGORY_HISTORY,
    CATEGORY_NOTE,
)

SLOT_KEYS: tuple[str, ...] = (
    "purchase",
    "test",
    "use",
    "history",
    "ability",
    "commands",
    "support",
    "note",
)
SLOT_INDEX_BY_KEY: dict[str, int] = {key: index for index, key in enumerate(SLOT_KEYS, start=1)}
SLOT_KEY_BY_INDEX: dict[int, str] = {index: key for key, index in SLOT_INDEX_BY_KEY.items()}

COLOR_TOKENS = frozenset({"R", "G", "B", "N"})
STYLE_MODES = frozenset({"simple", "advanced"})
SCOPE_TYPES = frozenset({"global", "owner"})
BUTTON_PARTS = frozenset({"text", "color", "emoji"})

_UNSET = object()


def _dialect_insert(session, model):
    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        return insert(model)
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        return insert(model)
    raise RuntimeError(
        f"start customization upserts do not support {dialect_name!r}"
    )


def normalize_scope(scope_type: str, owner_user_id: int | None = None) -> tuple[str, int]:
    normalized = (scope_type or "").strip().lower()
    if normalized not in SCOPE_TYPES:
        raise ValueError("scope_type must be 'global' or 'owner'")
    if normalized == "global":
        return normalized, 0
    owner_id = int(owner_user_id or 0)
    if owner_id <= 0:
        raise ValueError("owner_user_id must be a positive integer for owner scope")
    return normalized, owner_id


def validate_category(category: str) -> str:
    normalized = (category or "").strip().lower()
    if normalized not in CATEGORIES:
        raise ValueError("invalid start customization category")
    return normalized


def normalize_slot(slot: int | str) -> tuple[str, int]:
    if isinstance(slot, int):
        key = SLOT_KEY_BY_INDEX.get(slot)
        if key is None:
            raise ValueError("invalid start button slot")
        return key, slot

    raw = str(slot or "").strip().lower()
    if raw.isdigit():
        return normalize_slot(int(raw))
    index = SLOT_INDEX_BY_KEY.get(raw)
    if index is None:
        raise ValueError("invalid start button slot")
    return raw, index


def validate_color_token(color_token: str | None) -> str | None:
    if color_token is None:
        return None
    token = color_token.strip().upper()
    if token not in COLOR_TOKENS:
        raise ValueError("invalid start button color token")
    return token


def validate_style_mode(style_mode: str) -> str:
    normalized = (style_mode or "").strip().lower()
    if normalized not in STYLE_MODES:
        raise ValueError("invalid start style mode")
    return normalized


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    return str(value)


async def create_message_item(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    category: str,
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
    created_by: int | None = None,
    updated_by: int | None = None,
) -> StartCustomizationMessage:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    category_value = validate_category(category)
    weight_value = int(weight)
    if weight_value <= 0:
        raise ValueError("weight must be positive")

    async with async_session() as session:
        async with session.begin():
            row = StartCustomizationMessage(
                scope_type=scope,
                scope_owner_user_id=owner_id,
                category=category_value,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                source_chat_type=_normalize_optional_text(source_chat_type),
                message_type=_normalize_optional_text(message_type),
                text=_normalize_optional_text(text),
                caption=_normalize_optional_text(caption),
                media_file_id=_normalize_optional_text(media_file_id),
                media_type=_normalize_optional_text(media_type),
                entities_json=_normalize_optional_text(entities_json),
                extra_json=_normalize_optional_text(extra_json),
                sort_order=int(sort_order),
                weight=weight_value,
                created_by=created_by,
                updated_by=updated_by if updated_by is not None else created_by,
            )
            session.add(row)
        await session.refresh(row)
        return row


async def list_active_message_items(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    category: str,
) -> list[StartCustomizationMessage]:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    category_value = validate_category(category)
    async with async_session() as session:
        result = await session.execute(
            select(StartCustomizationMessage)
            .where(
                StartCustomizationMessage.scope_type == scope,
                StartCustomizationMessage.scope_owner_user_id == owner_id,
                StartCustomizationMessage.category == category_value,
                StartCustomizationMessage.is_active.is_(True),
            )
            .order_by(
                StartCustomizationMessage.sort_order.asc(),
                StartCustomizationMessage.id.asc(),
            )
        )
        return list(result.scalars().all())


async def clear_message_category(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    category: str,
    updated_by: int | None = None,
) -> int:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    category_value = validate_category(category)
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(StartCustomizationMessage.id).where(
                    StartCustomizationMessage.scope_type == scope,
                    StartCustomizationMessage.scope_owner_user_id == owner_id,
                    StartCustomizationMessage.category == category_value,
                    StartCustomizationMessage.is_active.is_(True),
                )
            )
            ids = [int(row_id) for row_id in result.scalars().all()]
            if not ids:
                return 0
            await session.execute(
                update(StartCustomizationMessage)
                .where(StartCustomizationMessage.id.in_(ids))
                .values(is_active=False, updated_by=updated_by)
            )
            return len(ids)


async def upsert_button_slot_config(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    slot: int | str,
    custom_text: str | None | Any = _UNSET,
    color_token: str | None | Any = _UNSET,
    emoji_text: str | None | Any = _UNSET,
    emoji_entities_json: str | None | Any = _UNSET,
    updated_by: int | None = None,
) -> StartButtonConfig:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    slot_key, slot_index = normalize_slot(slot)
    color_value = (
        validate_color_token(color_token)
        if color_token is not _UNSET
        else _UNSET
    )

    async with async_session() as session:
        async with session.begin():
            insert_values: dict[str, Any] = {
                "scope_type": scope,
                "scope_owner_user_id": owner_id,
                "slot_key": slot_key,
                "slot_index": slot_index,
                "updated_by": updated_by,
            }
            update_values: dict[str, Any] = {
                "slot_index": slot_index,
                "updated_at": func.now(),
            }
            if custom_text is not _UNSET:
                value = _normalize_optional_text(custom_text)
                insert_values["custom_text"] = value
                update_values["custom_text"] = value
            if color_value is not _UNSET:
                insert_values["color_token"] = color_value
                update_values["color_token"] = color_value
            if emoji_text is not _UNSET:
                value = _normalize_optional_text(emoji_text)
                insert_values["emoji_text"] = value
                update_values["emoji_text"] = value
            if emoji_entities_json is not _UNSET:
                value = _normalize_optional_text(emoji_entities_json)
                insert_values["emoji_entities_json"] = value
                update_values["emoji_entities_json"] = value
            if updated_by is not None:
                update_values["updated_by"] = updated_by
            statement = _dialect_insert(session, StartButtonConfig).values(
                **insert_values
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        StartButtonConfig.scope_type,
                        StartButtonConfig.scope_owner_user_id,
                        StartButtonConfig.slot_key,
                    ],
                    set_=update_values,
                )
            )
            row = (
                await session.execute(
                    select(StartButtonConfig).where(
                        StartButtonConfig.scope_type == scope,
                        StartButtonConfig.scope_owner_user_id == owner_id,
                        StartButtonConfig.slot_key == slot_key,
                    )
                )
            ).scalar_one()
        await session.refresh(row)
        return row


async def upsert_button_slot_configs(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    slot_values: dict[int | str, dict[str, Any]],
    updated_by: int | None = None,
) -> list[StartButtonConfig]:
    """Upsert multiple slot configs for one scope inside one transaction."""
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    normalized_values: list[tuple[str, int, dict[str, Any]]] = []
    for slot, values in slot_values.items():
        slot_key, slot_index = normalize_slot(slot)
        color = values.get("color_token", _UNSET)
        if color is not _UNSET:
            values = dict(values)
            values["color_token"] = validate_color_token(color)
        normalized_values.append((slot_key, slot_index, values))

    async with async_session() as session:
        async with session.begin():
            for slot_key, slot_index, values in normalized_values:
                insert_values: dict[str, Any] = {
                    "scope_type": scope,
                    "scope_owner_user_id": owner_id,
                    "slot_key": slot_key,
                    "slot_index": slot_index,
                    "updated_by": updated_by,
                }
                update_values: dict[str, Any] = {
                    "slot_index": slot_index,
                    "updated_at": func.now(),
                }
                if "custom_text" in values:
                    value = _normalize_optional_text(values["custom_text"])
                    insert_values["custom_text"] = value
                    update_values["custom_text"] = value
                if "color_token" in values:
                    insert_values["color_token"] = values["color_token"]
                    update_values["color_token"] = values["color_token"]
                if "emoji_text" in values:
                    value = _normalize_optional_text(values["emoji_text"])
                    insert_values["emoji_text"] = value
                    update_values["emoji_text"] = value
                if "emoji_entities_json" in values:
                    value = _normalize_optional_text(
                        values["emoji_entities_json"]
                    )
                    insert_values["emoji_entities_json"] = value
                    update_values["emoji_entities_json"] = value
                if updated_by is not None:
                    update_values["updated_by"] = updated_by
                statement = _dialect_insert(session, StartButtonConfig).values(
                    **insert_values
                )
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            StartButtonConfig.scope_type,
                            StartButtonConfig.scope_owner_user_id,
                            StartButtonConfig.slot_key,
                        ],
                        set_=update_values,
                    )
                )
            rows = list(
                (
                    await session.execute(
                        select(StartButtonConfig)
                        .where(
                            StartButtonConfig.scope_type == scope,
                            StartButtonConfig.scope_owner_user_id == owner_id,
                            StartButtonConfig.slot_key.in_(
                                [item[0] for item in normalized_values]
                            ),
                        )
                        .order_by(StartButtonConfig.slot_index)
                    )
                ).scalars()
            )
        for row in rows:
            await session.refresh(row)
        return rows


async def get_button_slot_config(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    slot: int | str,
) -> StartButtonConfig | None:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    slot_key, _ = normalize_slot(slot)
    async with async_session() as session:
        result = await session.execute(
            select(StartButtonConfig).where(
                StartButtonConfig.scope_type == scope,
                StartButtonConfig.scope_owner_user_id == owner_id,
                StartButtonConfig.slot_key == slot_key,
            )
        )
        return result.scalar_one_or_none()


async def list_button_slot_configs(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
) -> list[StartButtonConfig]:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    async with async_session() as session:
        result = await session.execute(
            select(StartButtonConfig)
            .where(
                StartButtonConfig.scope_type == scope,
                StartButtonConfig.scope_owner_user_id == owner_id,
            )
            .order_by(StartButtonConfig.slot_index.asc())
        )
        return list(result.scalars().all())


async def clear_button_slot_part(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    slot: int | str,
    part: str,
    updated_by: int | None = None,
) -> bool:
    normalized_part = (part or "").strip().lower()
    if normalized_part not in BUTTON_PARTS:
        raise ValueError("invalid start button config part")
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    slot_key, _ = normalize_slot(slot)

    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(StartButtonConfig).where(
                    StartButtonConfig.scope_type == scope,
                    StartButtonConfig.scope_owner_user_id == owner_id,
                    StartButtonConfig.slot_key == slot_key,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            if normalized_part == "text":
                row.custom_text = None
            elif normalized_part == "color":
                row.color_token = None
            else:
                row.emoji_text = None
                row.emoji_entities_json = None
            if updated_by is not None:
                row.updated_by = updated_by
            return True


async def clear_button_slot_part_for_all_slots(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    part: str,
    updated_by: int | None = None,
) -> int:
    """Clear one config part from all eight slots for one scope atomically."""
    normalized_part = (part or "").strip().lower()
    if normalized_part not in BUTTON_PARTS:
        raise ValueError("invalid start button config part")
    scope, owner_id = normalize_scope(scope_type, owner_user_id)

    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(StartButtonConfig).where(
                    StartButtonConfig.scope_type == scope,
                    StartButtonConfig.scope_owner_user_id == owner_id,
                )
            )
            rows = list(result.scalars().all())
            changed = 0
            for row in rows:
                before = (
                    row.custom_text,
                    row.color_token,
                    row.emoji_text,
                    row.emoji_entities_json,
                )
                if normalized_part == "text":
                    row.custom_text = None
                elif normalized_part == "color":
                    row.color_token = None
                else:
                    row.emoji_text = None
                    row.emoji_entities_json = None
                after = (
                    row.custom_text,
                    row.color_token,
                    row.emoji_text,
                    row.emoji_entities_json,
                )
                if before != after:
                    changed += 1
                if updated_by is not None:
                    row.updated_by = updated_by
            return changed


async def set_style_mode(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    style_mode: str,
    updated_by: int | None = None,
) -> StartStyleConfig:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    mode = validate_style_mode(style_mode)
    async with async_session() as session:
        async with session.begin():
            statement = _dialect_insert(session, StartStyleConfig).values(
                scope_type=scope,
                scope_owner_user_id=owner_id,
                style_mode=mode,
                updated_by=updated_by,
            )
            update_values: dict[str, Any] = {
                "style_mode": mode,
                "updated_at": func.now(),
            }
            if updated_by is not None:
                update_values["updated_by"] = updated_by
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        StartStyleConfig.scope_type,
                        StartStyleConfig.scope_owner_user_id,
                    ],
                    set_=update_values,
                )
            )
            row = (
                await session.execute(
                    select(StartStyleConfig).where(
                        StartStyleConfig.scope_type == scope,
                        StartStyleConfig.scope_owner_user_id == owner_id,
                    )
                )
            ).scalar_one()
        await session.refresh(row)
        return row


async def toggle_style_mode(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
    initial_mode: str,
    updated_by: int | None = None,
) -> StartStyleConfig:
    """Atomically insert the first toggle or flip the existing stored mode."""
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    mode = validate_style_mode(initial_mode)
    async with async_session() as session:
        async with session.begin():
            statement = _dialect_insert(session, StartStyleConfig).values(
                scope_type=scope,
                scope_owner_user_id=owner_id,
                style_mode=mode,
                updated_by=updated_by,
            )
            update_values: dict[str, Any] = {
                "style_mode": case(
                    (StartStyleConfig.style_mode == "simple", "advanced"),
                    else_="simple",
                ),
                "updated_at": func.now(),
            }
            if updated_by is not None:
                update_values["updated_by"] = updated_by
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        StartStyleConfig.scope_type,
                        StartStyleConfig.scope_owner_user_id,
                    ],
                    set_=update_values,
                )
            )
            row = (
                await session.execute(
                    select(StartStyleConfig).where(
                        StartStyleConfig.scope_type == scope,
                        StartStyleConfig.scope_owner_user_id == owner_id,
                    )
                )
            ).scalar_one()
        await session.refresh(row)
        return row


async def get_style_config(
    *,
    scope_type: str,
    owner_user_id: int | None = None,
) -> StartStyleConfig | None:
    scope, owner_id = normalize_scope(scope_type, owner_user_id)
    async with async_session() as session:
        result = await session.execute(
            select(StartStyleConfig).where(
                StartStyleConfig.scope_type == scope,
                StartStyleConfig.scope_owner_user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()
