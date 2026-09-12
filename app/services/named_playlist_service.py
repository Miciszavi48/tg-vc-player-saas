"""Business rules for named playlists (TRANSPORT-09/10/11).

Validation lives here so every entry point (text command, future panel) enforces
the same name, uniqueness and length constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.repositories import named_playlist_repo as repo


@dataclass(frozen=True)
class PlaylistResult:
    ok: bool
    reason: str = "ok"
    name: str = ""
    count: int = 0
    total: int = 0
    rows: list[Any] = field(default_factory=list)


def validate_name(name: str) -> tuple[str, str | None]:
    """Return ``(normalized, error)`` for a candidate playlist name."""
    normalized = repo.normalize_name(name)
    if not normalized:
        return "", "missing_name"
    if len(normalized) > repo.MAX_NAME_LENGTH:
        return normalized, "name_too_long"
    return normalized, None


async def create(chat_id: int, name: str, *, created_by: int | None = None) -> PlaylistResult:
    normalized, error = validate_name(name)
    if error:
        return PlaylistResult(False, error, name=normalized)
    if await repo.get_playlist(chat_id, normalized) is not None:
        return PlaylistResult(False, "already_exists", name=normalized)
    if await repo.count_playlists(chat_id) >= repo.MAX_PLAYLISTS_PER_CHAT:
        return PlaylistResult(False, "too_many_playlists", name=normalized)
    await repo.create_playlist(chat_id, normalized, created_by=created_by)
    return PlaylistResult(True, "created", name=normalized)


async def rename(chat_id: int, old_name: str, new_name: str) -> PlaylistResult:
    normalized, error = validate_name(new_name)
    if error:
        return PlaylistResult(False, error, name=normalized)
    if await repo.get_playlist(chat_id, old_name) is None:
        return PlaylistResult(False, "not_found", name=repo.normalize_name(old_name))
    existing = await repo.get_playlist(chat_id, normalized)
    if existing is not None and existing.name.lower() != repo.normalize_name(old_name).lower():
        return PlaylistResult(False, "already_exists", name=normalized)
    await repo.rename_playlist(chat_id, old_name, normalized)
    return PlaylistResult(True, "renamed", name=normalized)


async def delete(chat_id: int, name: str) -> PlaylistResult:
    normalized = repo.normalize_name(name)
    if not normalized:
        return PlaylistResult(False, "missing_name")
    removed = await repo.delete_playlist(chat_id, normalized)
    return PlaylistResult(removed, "deleted" if removed else "not_found", name=normalized)


async def add_media(
    chat_id: int,
    name: str,
    *,
    source: str,
    title: str | None,
    media_type: str = "audio",
    added_by: int | None = None,
) -> PlaylistResult:
    """TRANSPORT-11: append media to a NAMED playlist."""
    normalized = repo.normalize_name(name)
    playlist = await repo.get_playlist(chat_id, normalized)
    if playlist is None:
        return PlaylistResult(False, "not_found", name=normalized)
    if not str(source or "").strip():
        return PlaylistResult(False, "missing_source", name=normalized)
    if await repo.count_items(playlist.id) >= repo.MAX_ITEMS_PER_PLAYLIST:
        return PlaylistResult(False, "playlist_full", name=normalized)
    await repo.add_item(
        playlist.id,
        source=source,
        title=title,
        media_type=media_type,
        added_by=added_by,
    )
    count = await repo.count_items(playlist.id)
    return PlaylistResult(True, "added", name=normalized, count=count)


async def list_page(chat_id: int, page: int = 0, per_page: int = 10) -> PlaylistResult:
    """TRANSPORT-10: one page of playlists plus the total for pagination."""
    rows, total = await repo.list_playlists(chat_id, page=page, per_page=per_page)
    return PlaylistResult(True, "listed", total=total, rows=list(rows), count=len(rows))


async def items_for_play(chat_id: int, name: str) -> PlaylistResult:
    """TRANSPORT-10: resolve a playlist by name into its ordered items."""
    normalized = repo.normalize_name(name)
    playlist = await repo.get_playlist(chat_id, normalized)
    if playlist is None:
        return PlaylistResult(False, "not_found", name=normalized)
    items = await repo.get_items(playlist.id)
    if not items:
        return PlaylistResult(False, "playlist_empty", name=normalized)
    return PlaylistResult(True, "ok", name=normalized, rows=items, count=len(items))
