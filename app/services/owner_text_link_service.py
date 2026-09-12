"""Owner-scoped text/link override service with global fallback reads."""

from __future__ import annotations

from dataclasses import dataclass

from app.repositories import owner_text_link_repo, settings_repo
from app.services.texts_links_ui import decode_setting_value

_VALID_MODES = frozenset({"empty", "text", "link", "media"})


@dataclass(frozen=True)
class TextLinkValue:
    """Resolved text/link value from owner override or global fallback."""

    mode: str
    raw: str | None
    source: str
    owner_user_id: int | None = None


def _decode_raw_value(kind: str, raw: str | None) -> TextLinkValue:
    """Decode a stored value string into a ``TextLinkValue`` without crashing."""
    if raw is None or str(raw).strip() == "":
        return TextLinkValue(mode="empty", raw=None, source="empty")

    parsed = decode_setting_value(raw)
    parsed_mode = parsed.get("mode")
    if parsed_mode == "media":
        return TextLinkValue(mode="media", raw=raw, source="pending")

    if kind == "link":
        link_value = str(parsed.get("value") or raw).strip()
        if not link_value:
            return TextLinkValue(mode="empty", raw=None, source="pending")
        return TextLinkValue(mode="link", raw=link_value, source="pending")

    text_value = str(parsed.get("value") or raw).strip()
    if not text_value:
        return TextLinkValue(mode="empty", raw=None, source="pending")
    return TextLinkValue(mode="text", raw=text_value, source="pending")


def _with_source(
    value: TextLinkValue,
    *,
    source: str,
    owner_user_id: int | None = None,
) -> TextLinkValue:
    """Return a copy of ``value`` with source metadata applied."""
    mode = value.mode if value.mode in _VALID_MODES else "text"
    return TextLinkValue(
        mode=mode,
        raw=value.raw,
        source=source,
        owner_user_id=owner_user_id,
    )


async def get_owner_override(owner_user_id: int, key: str) -> TextLinkValue | None:
    """Return owner override value when a row exists.

    Args:
        owner_user_id: Telegram user id of the owner/creator.
        key: Whitelisted text/link field key.

    Returns:
        ``TextLinkValue`` with ``source='owner'`` or ``None`` when no override row.
    """
    row = await owner_text_link_repo.get_owner_text_link(owner_user_id, key)
    if row is None:
        return None
    decoded = _decode_raw_value(row.kind, row.value)
    if decoded.mode == "empty":
        return None
    return _with_source(decoded, source="owner", owner_user_id=int(row.owner_user_id))


async def get_effective_text_link(
    key: str,
    *,
    owner_user_id: int | None = None,
) -> TextLinkValue:
    """Resolve effective value: owner override, then global, then empty.

    Args:
        key: Whitelisted text/link field key.
        owner_user_id: Optional owner scope for override lookup.

    Returns:
        ``TextLinkValue`` tagged with ``source`` of ``owner``, ``global``, or ``empty``.
    """
    field_key = (key or "").strip()
    if not field_key:
        return TextLinkValue(mode="empty", raw=None, source="empty")

    if owner_user_id is not None:
        override = await get_owner_override(owner_user_id, field_key)
        if override is not None:
            return override

    global_raw = await settings_repo.get_bot_setting(field_key)
    if global_raw is None or str(global_raw).strip() == "":
        return TextLinkValue(mode="empty", raw=None, source="empty")

    from app.services.texts_links_ui import get_field_spec

    spec = get_field_spec(field_key)
    kind = spec.kind if spec is not None else "text"
    decoded = _decode_raw_value(kind, global_raw)
    if decoded.mode == "empty":
        return TextLinkValue(mode="empty", raw=None, source="empty")
    return _with_source(decoded, source="global")


async def set_owner_override(
    owner_user_id: int,
    key: str,
    kind: str,
    value: str | None,
    *,
    updated_by: int | None = None,
) -> None:
    """Persist an owner override without touching global ``bot_settings``.

    Args:
        owner_user_id: Telegram user id of the owner/creator.
        key: Whitelisted text/link field key.
        kind: ``text`` or ``link``.
        value: Override payload to store.
        updated_by: Optional Telegram actor id performing the write.
    """
    await owner_text_link_repo.set_owner_text_link(
        owner_user_id,
        key,
        kind,
        value,
        updated_by=updated_by,
    )


async def clear_owner_override(owner_user_id: int, key: str) -> bool:
    """Remove an owner override row only.

    Args:
        owner_user_id: Telegram user id of the owner/creator.
        key: Whitelisted text/link field key.

    Returns:
        ``True`` when a row was deleted; ``False`` when no override existed.
    """
    return await owner_text_link_repo.clear_owner_text_link(owner_user_id, key)
