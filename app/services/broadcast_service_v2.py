from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.database.engine import async_session
from app.database.models import Broadcast, Channel, Group, User
from app.repositories import broadcast_repo
from app.services.analytics_service import track_event
from app.utils.cache import (
    acquire_lock,
    clear_bc_active,
    clear_bc_progress,
    get_bc_progress,
    release_lock,
    set_bc_active,
    update_bc_progress,
)

logger = logging.getLogger(__name__)

_SEND_DELAY = 0.04
_BATCH_UPDATE_INTERVAL = 25
_CANCEL_CHECK_INTERVAL = 50
BROADCAST_RECIPIENT_BATCH_SIZE = 500


def _filter_cutoff(filter_type: str) -> datetime | None:
    if filter_type == "7d":
        return datetime.now(timezone.utc) - timedelta(days=7)
    if filter_type == "30d":
        return datetime.now(timezone.utc) - timedelta(days=30)
    return None


class BroadcastServiceV2:

    @staticmethod
    def extract_payload(message) -> dict:
        """Capture everything needed to reproduce the message."""
        if message.text:
            return {
                "payload_type": "text",
                "text_content": message.text,
                "entities_json": _serialize_entities(message.entities),
                "caption": None,
                "caption_entities_json": None,
                "file_id": None,
            }

        media_attrs = [
            ("photo", "photo"), ("video", "video"),
            ("document", "document"), ("audio", "audio"),
            ("animation", "animation"), ("voice", "voice"),
            ("video_note", "video_note"), ("sticker", "sticker"),
        ]
        for attr, ptype in media_attrs:
            media = getattr(message, attr, None)
            if media:
                return {
                    "payload_type": ptype,
                    "text_content": None,
                    "entities_json": None,
                    "caption": message.caption,
                    "caption_entities_json": _serialize_entities(message.caption_entities),
                    "file_id": media.file_id,
                }

        return {
            "payload_type": "text",
            "text_content": message.text or "",
            "entities_json": None,
            "caption": None,
            "caption_entities_json": None,
            "file_id": None,
        }

    @staticmethod
    async def create_broadcast(
        admin_id: int,
        mode: str,
        target_scope: str,
        payload: dict,
        source_chat_id: int | None = None,
        source_message_id: int | None = None,
    ) -> Broadcast:
        bc = Broadcast(
            admin_id=admin_id,
            mode=mode,
            target_scope=target_scope,
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            payload_type=payload.get("payload_type"),
            text_content=payload.get("text_content"),
            entities_json=payload.get("entities_json"),
            caption=payload.get("caption"),
            caption_entities_json=payload.get("caption_entities_json"),
            file_id=payload.get("file_id"),
        )
        return await broadcast_repo.create(bc)

    @staticmethod
    async def execute(client, broadcast_id: int) -> None:
        from app.utils.redis_keys import bc_lock_key
        token = await acquire_lock(bc_lock_key(broadcast_id), ttl_ms=600_000)
        if not token:
            return
        try:
            bc = await broadcast_repo.get_by_id(broadcast_id)
            if bc is None or bc.status != "pending":
                return
            await broadcast_repo.update_status(broadcast_id, "running")
            await set_bc_active(broadcast_id)
            bc_event = f"broadcast.created.{bc.mode}" if bc.mode in ("send", "forward") else "broadcast.created.send"
            await track_event(bc_event, feature="broadcast")

            filter_type = getattr(bc, "filter_type", None) or "all"
            total = await BroadcastServiceV2._count_recipients(bc.target_scope, filter_type)
            await broadcast_repo.update_total(broadcast_id, total)

            cursor, sent, fail = await get_bc_progress(broadcast_id)
            i = cursor
            stopped = False

            while i < total and not stopped:
                batch = await BroadcastServiceV2._get_recipient_batch(
                    bc.target_scope,
                    filter_type,
                    offset=i,
                    limit=BROADCAST_RECIPIENT_BATCH_SIZE,
                )
                if not batch:
                    break

                for chat_id in batch:
                    if i % _CANCEL_CHECK_INTERVAL == 0 and i > cursor:
                        fresh = await broadcast_repo.get_by_id(broadcast_id)
                        if fresh and fresh.status == "canceled":
                            stopped = True
                            break

                    try:
                        if bc.mode == "forward":
                            await client.forward_messages(
                                chat_id, bc.source_chat_id, bc.source_message_id,
                            )
                        else:
                            await BroadcastServiceV2._send_one(client, chat_id, bc)
                        sent += 1
                    except Exception as exc:
                        exc_name = type(exc).__name__
                        if "FloodWait" in exc_name:
                            wait = getattr(exc, "value", 5)
                            if not isinstance(wait, (int, float)):
                                wait = 5
                            logger.warning("Broadcast FloodWait: sleeping %s seconds", wait)
                            await asyncio.sleep(wait + 1)
                            try:
                                if bc.mode == "forward":
                                    await client.forward_messages(
                                        chat_id, bc.source_chat_id, bc.source_message_id,
                                    )
                                else:
                                    await BroadcastServiceV2._send_one(client, chat_id, bc)
                                sent += 1
                                i += 1
                                continue
                            except Exception:
                                pass
                        fail += 1

                    if (i + 1) % _BATCH_UPDATE_INTERVAL == 0:
                        await update_bc_progress(broadcast_id, i + 1, sent, fail)

                    await asyncio.sleep(_SEND_DELAY)
                    i += 1

            final_status = "done"
            fresh = await broadcast_repo.get_by_id(broadcast_id)
            if fresh and fresh.status == "canceled":
                final_status = "canceled"
            elif bc.interval_hours is not None:
                final_status = "pending"
            await broadcast_repo.finish(broadcast_id, final_status, sent, fail)
            await track_event("broadcast.sent", feature="broadcast", value=sent)
            if fail > 0:
                await track_event("broadcast.failed", feature="broadcast", value=fail)
        except Exception:
            logger.exception("Broadcast %s execution error", broadcast_id)
            await broadcast_repo.finish(broadcast_id, "failed", 0, 0)
        finally:
            await clear_bc_active()
            await clear_bc_progress(broadcast_id)
            await release_lock(bc_lock_key(broadcast_id), token)

    @staticmethod
    async def cancel(broadcast_id: int) -> None:
        await broadcast_repo.update_status(broadcast_id, "canceled")

    @staticmethod
    async def _send_one(client, chat_id: int, bc: Broadcast) -> None:
        entities = _rebuild_entities(bc.entities_json)
        cap_entities = _rebuild_entities(bc.caption_entities_json)

        senders = {
            "text": lambda: client.send_message(chat_id, bc.text_content, entities=entities),
            "photo": lambda: client.send_photo(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "video": lambda: client.send_video(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "document": lambda: client.send_document(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "audio": lambda: client.send_audio(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "animation": lambda: client.send_animation(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "voice": lambda: client.send_voice(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "sticker": lambda: client.send_sticker(chat_id, bc.file_id),
        }
        sender = senders.get(bc.payload_type)
        if sender:
            await sender()
        elif bc.text_content:
            await client.send_message(chat_id, bc.text_content)

    @staticmethod
    async def _count_recipients(scope: str, filter_type: str = "all") -> int:
        cutoff = _filter_cutoff(filter_type)
        async with async_session() as session:
            if scope == "groups":
                stmt = select(func.count()).select_from(Group).where(Group.status == "active")
                if cutoff is not None:
                    stmt = stmt.where(
                        Group.last_activity.is_not(None),
                        Group.last_activity >= cutoff,
                    )
            elif scope == "channels":
                stmt = select(func.count()).select_from(Channel).where(Channel.status == "active")
                if cutoff is not None:
                    stmt = stmt.where(
                        Channel.last_activity.is_not(None),
                        Channel.last_activity >= cutoff,
                    )
            else:
                stmt = select(func.count()).select_from(User).where(User.is_banned.is_(False))
                if cutoff is not None:
                    stmt = stmt.where(User.last_seen >= cutoff)
            result = await session.execute(stmt)
            return int(result.scalar() or 0)

    @staticmethod
    async def _get_recipient_batch(
        scope: str,
        filter_type: str,
        offset: int,
        limit: int,
    ) -> list[int]:
        cutoff = _filter_cutoff(filter_type)
        async with async_session() as session:
            if scope == "groups":
                stmt = select(Group.chat_id).where(Group.status == "active")
                if cutoff is not None:
                    stmt = stmt.where(
                        Group.last_activity.is_not(None),
                        Group.last_activity >= cutoff,
                    )
                stmt = stmt.order_by(Group.chat_id.asc()).offset(offset).limit(limit)
            elif scope == "channels":
                stmt = select(Channel.chat_id).where(Channel.status == "active")
                if cutoff is not None:
                    stmt = stmt.where(
                        Channel.last_activity.is_not(None),
                        Channel.last_activity >= cutoff,
                    )
                stmt = stmt.order_by(Channel.chat_id.asc()).offset(offset).limit(limit)
            else:
                stmt = select(User.user_id).where(User.is_banned.is_(False))
                if cutoff is not None:
                    stmt = stmt.where(User.last_seen >= cutoff)
                stmt = stmt.order_by(User.user_id.asc()).offset(offset).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    async def _get_recipients(scope: str, filter_type: str = "all") -> list[int]:
        """Legacy helper: materialize all recipient IDs via batched SQL queries."""
        total = await BroadcastServiceV2._count_recipients(scope, filter_type)
        if total == 0:
            return []
        ids: list[int] = []
        offset = 0
        while offset < total:
            batch = await BroadcastServiceV2._get_recipient_batch(
                scope, filter_type, offset=offset, limit=BROADCAST_RECIPIENT_BATCH_SIZE,
            )
            if not batch:
                break
            ids.extend(batch)
            offset += len(batch)
            if len(batch) < BROADCAST_RECIPIENT_BATCH_SIZE:
                break
        return ids


def _serialize_entities(entities) -> str | None:
    if not entities:
        return None
    result = []
    for e in entities:
        entry = {
            "type": e.type.value if hasattr(e.type, "value") else str(e.type),
            "offset": e.offset,
            "length": e.length,
        }
        if getattr(e, "url", None):
            entry["url"] = e.url
        if getattr(e, "user", None):
            entry["user_id"] = e.user.id
        if getattr(e, "language", None):
            entry["language"] = e.language
        if getattr(e, "custom_emoji_id", None):
            entry["custom_emoji_id"] = e.custom_emoji_id
        result.append(entry)
    return json.dumps(result)


def _rebuild_entities(json_str: str | None) -> list | None:
    if not json_str:
        return None
    try:
        from pyrogram.types import MessageEntity
        from pyrogram.enums import MessageEntityType
        raw = json.loads(json_str)
        result = []
        for e in raw:
            etype = MessageEntityType(e["type"])
            ent = MessageEntity(
                type=etype,
                offset=e["offset"],
                length=e["length"],
                url=e.get("url"),
                language=e.get("language"),
                custom_emoji_id=e.get("custom_emoji_id"),
            )
            result.append(ent)
        return result or None
    except Exception:
        logger.debug("Failed to rebuild entities from JSON")
        return None
