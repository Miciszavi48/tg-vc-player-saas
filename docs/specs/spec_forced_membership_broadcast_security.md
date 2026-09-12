# Specification v2: Forced Membership, Channel Security & Broadcast System

> **Canonical References:**
> - [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [../REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> **Implementation status (2026-07-19): PARTIAL / SUPERSEDED IN PART.** Force-join and broadcast workflows are implemented, while the old global "Channel Security" concept is superseded by per-chat Call Security. Treat the architecture and path lists below as design history; verify runtime behavior in [../features/force_join.md](../features/force_join.md), [../features/broadcast.md](../features/broadcast.md), and [../features/call_security.md](../features/call_security.md).
> **Codebase**: tg-vc-player-saas (Python 3.12, kurigram, SQLAlchemy 2.0 async, Redis 7)  
> **Historical verification scope**: early `models.py`, cache/UI patterns, force-join, broadcast service, and monolith i18n. Current source has 51 ORM models and split i18n fragments.

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                           HANDLER LAYER                              │
│                                                                      │
│  force_join_panel.py          broadcast_panel.py                     │
│  (admin CRUD for targets,     (send/forward flows,                   │
│   toggle, verify, test)        history, cancel)                      │
│                                                                      │
│  force_join.py  ← REFACTOR    callbacks.py (existing)               │
│  (user-facing gate +           (nav, playback — no changes)          │
│   "Check Again" handler)                                             │
└───────────┬────────────────────────────┬─────────────────────────────┘
            │                            │
┌───────────▼────────────────────────────▼─────────────────────────────┐
│                          SERVICE LAYER                               │
│                                                                      │
│  ForcedMembershipService            BroadcastServiceV2               │
│  ├─ check(user_id)                  ├─ extract_payload(message)      │
│  ├─ add_target(client, identifier)  ├─ create(admin_id, payload)     │
│  ├─ remove_target(target_id)        ├─ execute(broadcast_id)         │
│  ├─ toggle(enabled)                 ├─ cancel(broadcast_id)          │
│  ├─ verify_target(client, target)   ├─ get_history(limit)            │
│  └─ verify_all(client)              └─ get_detail(broadcast_id)      │
│                                                                      │
│  (Existing BroadcastService kept for backward compat; V2 wraps it)   │
└───────────┬────────────────────────────┬─────────────────────────────┘
            │                            │
┌───────────▼────────────────┐ ┌─────────▼────────────────────────────┐
│     REPOSITORY LAYER       │ │          CACHE LAYER                  │
│                            │ │                                       │
│  force_join_repo.py        │ │  cache.py  (existing module)          │
│  ├─ get_active_targets()   │ │  ├─ fm:targets  (target list)        │
│  ├─ get_all_targets()      │ │  ├─ fm:ok:{uid} (verified users)     │
│  ├─ upsert_target(...)     │ │  ├─ fm:rl:{uid} (rate limit)         │
│  ├─ deactivate(id)         │ │  ├─ bc:{id}:*   (broadcast state)    │
│  ├─ update_verify(id,st)   │ │  └─ lock:*      (existing locks)     │
│  └─ get_target_by_id(id)   │ │                                       │
│                            │ │                                       │
│  broadcast_repo.py         │ │                                       │
│  ├─ create(broadcast)      │ │                                       │
│  ├─ update_status(id,st)   │ │                                       │
│  ├─ update_counts(id,s,f)  │ │                                       │
│  ├─ get_recent(limit)      │ │                                       │
│  └─ get_by_id(id)          │ │                                       │
└────────────┬───────────────┘ └──────────────┬────────────────────────┘
             │                                │
      ┌──────▼──────┐                 ┌───────▼────────┐
      │ PostgreSQL  │                 │   Redis 7      │
      └─────────────┘                 └────────────────┘
```

### File Inventory (new + modified)

| Action | File | Purpose |
|--------|------|---------|
| NEW | `app/services/forced_membership_service.py` | Core membership logic |
| NEW | `app/services/broadcast_service_v2.py` | Persistent broadcast engine |
| NEW | `app/repositories/force_join_repo.py` | DB CRUD for targets |
| NEW | `app/repositories/broadcast_repo.py` | DB CRUD for broadcasts |
| NEW | `app/handlers/force_join_panel.py` | Admin panel handler |
| NEW | `app/handlers/broadcast_panel.py` | Broadcast admin panel handler |
| MODIFY | `app/handlers/force_join.py` | Refactor to use service |
| MODIFY | `app/database/models.py` | Extend ForceJoinChannel + add Broadcast |
| MODIFY | `app/utils/cache.py` | Add fm: / bc: key helpers |
| MODIFY | `app/utils/ui.py` | Add CB constants + keyboard builders |
| MODIFY | `app/handlers/__init__.py` | Register new modules |
| MODIFY | `app/resources/strings/fa.json` | New i18n keys |
| MODIFY | `app/resources/strings/en.json` | New i18n keys |
| NEW | `app/database/migrations/versions/0004_fm_broadcast.py` | Alembic migration |

---

## 2. Database Schema

### 2.1 Extend Existing `force_join_channels` Table

Current columns (verified in `models.py:386–397`):
`id`, `channel_id`, `channel_username`, `invite_link`, `added_by`, `added_at`, `is_active`

**Add these columns:**

```python
display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
chat_type: Mapped[str] = mapped_column(
    String(16), nullable=False, server_default="channel",
)
position: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
verify_status: Mapped[str] = mapped_column(
    String(32), nullable=False, server_default="pending",
)
verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
updated_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
)
```

New index: `ix_force_join_channels_position` on `(position)`.

### 2.2 New Table: `broadcasts`

```python
class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)         # send | forward
    target_scope: Mapped[str] = mapped_column(String(16), nullable=False) # users | groups | channels
    payload_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    entities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption_entities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Indexes: `ix_broadcasts_status`, `ix_broadcasts_created_at`.

### 2.3 Settings Storage

`force_join_enabled` and `channel_security_enabled` already stored in `bot_settings` table via `settings_repo.get_bot_setting()` / `set_bot_setting()`. No new settings table needed.

---

## 3. Redis Keys

| Key | Value | TTL | Written by | Read by |
|-----|-------|-----|------------|---------|
| `fm:targets` | JSON `[{channel_id, username, invite_link, display_name, chat_type}]` | 120s | `force_join_repo` on cache miss | `ForcedMembershipService.check` |
| `fm:ok:{user_id}` | `"1"` | 180s | `check` after all-pass | `check` fast path |
| `fm:rl:{user_id}` | `"1"` | 5s | "Check Again" handler | "Check Again" handler |
| `lock:fm:verify` | UUID | 30s | `verify_all` | `verify_all` |
| `lock:bc:{id}` | UUID | 600s | `execute` | `execute` |
| `bc:{id}:cursor` | `"{offset}"` | 2h | `execute` loop | `execute` resume |
| `bc:{id}:sent` | `"{count}"` | 2h | `execute` loop | `execute` + admin poll |
| `bc:{id}:fail` | `"{count}"` | 2h | `execute` loop | `execute` + admin poll |
| `bc:active` | `"{broadcast_id}"` | none | `execute` start | panel (show running) |

All Redis calls use `decode_responses=True` (project convention).

---

## 4. Admin Panel UX

### 4.1 Forced Membership Panel

Entry: existing `dev:force_join_manage` / `own:force_join_manage` callbacks.

```
┌─────────────────────────────────────────┐
│  ┈┅┅━━| 🔐 Forced Membership |━━┅┅┈    │
│                                         │
│  Status: ✅ Enabled / ❌ Disabled       │
│  Targets: 3 active                      │
│                                         │
│  [Toggle Enable/Disable]  [Add Target]  │
│  [List Targets]           [Verify All]  │
│  [Test My Membership]                   │
│  [Back]                                 │
└─────────────────────────────────────────┘
```

**Add Target** — uses `_ask()` flow:
1. Bot: "Send the channel @username, numeric ID, or invite link"
2. Bot calls `getChat(identifier)` → validates
3. Bot calls `getChatMember(chat_id, bot_me.id)` → checks bot is admin
4. Bot shows summary: `Title: X | Type: channel | Bot admin: ✅`
5. Admin: Confirm / Cancel
6. On confirm → `force_join_repo.upsert_target(...)`, invalidate `fm:targets`

**List Targets** → inline message:
```
1. @channel_one  ✅ verified  [🗑 Remove]
2. @channel_two  ⚠️ bot_not_admin  [🗑 Remove]
3. Private (invite)  ✅ verified  [🗑 Remove]

[Re-Verify All]  [Back]
```

**Remove Target** → `fm:rm:{channel_id}` callback → confirm → soft-delete (`is_active=False`) → invalidate cache.

**Test My Membership** → runs `check_membership(admin_user_id)` and shows result inline.

### 4.2 Broadcast Panel

Reuse existing dev/owner broadcast callback buttons. **New addition**: history panel.

**Enhancement to existing flow** — persist broadcast to DB:
1. When admin triggers `dev:bc:group` (or any broadcast button):
   - `_ask()` captures the message
   - `extract_payload(message)` → `Broadcast` row created with `status=pending`
   - Show confirm with broadcast ID + estimated recipients
   - On confirm → `status=running`, execute async
   - On complete → `status=done`, update counts

**History panel** — new button in dev panel:
```
┌───────────────────────────────────────────┐
│  ┈┅┅━━| 📊 Broadcast History |━━┅┅┈      │
│                                           │
│  #42 send→users  ✅ done  450/460  2m ago │
│  #41 fwd→groups  ✅ done  12/12   1h ago  │
│  #40 send→users  ❌ canceled  120/460     │
│                                           │
│  [Back]                                   │
└───────────────────────────────────────────┘
```

### 4.3 CB Constants to Add

```python
# Forced Membership admin panel
"FM_PANEL": "fm:panel",
"FM_TOGGLE": "fm:toggle",
"FM_LIST": "fm:list",
"FM_ADD": "fm:add",
"FM_ADD_CONFIRM": "fm:add:ok",
"FM_ADD_CANCEL": "fm:add:no",
"FM_REMOVE": "fm:rm",          # fm:rm:{channel_id}
"FM_VERIFY_ALL": "fm:verify",
"FM_TEST": "fm:test",

# Channel Security (embedded in FM panel)
"CS_VERIFY_ALL": "cs:verify",
"CS_DISABLE_BROKEN": "cs:fix",

# Broadcast
"BC_HISTORY": "bc:history",
"BC_DETAIL": "bc:detail",      # bc:detail:{id}
"BC_CANCEL": "bc:cancel",      # bc:cancel:{id}
```

---

## 5. Core Pseudo-code

### 5.1 ForcedMembershipService

```python
class ForcedMembershipService:

    @staticmethod
    async def is_enabled() -> bool:
        val = await settings_repo.get_bot_setting("force_join_enabled")
        return val in ("1", "true")

    @staticmethod
    async def get_targets_cached() -> list[dict]:
        r = await get_redis()
        raw = await r.get("fm:targets")
        if raw:
            return json.loads(raw)
        targets = await force_join_repo.get_active_targets()
        data = [
            {"channel_id": t.channel_id, "username": t.channel_username,
             "invite_link": t.invite_link, "display_name": t.display_name,
             "chat_type": t.chat_type}
            for t in targets
        ]
        await r.set("fm:targets", json.dumps(data), ex=120)
        return data

    @staticmethod
    async def invalidate_cache():
        r = await get_redis()
        await r.delete("fm:targets")

    @staticmethod
    async def check(client, user_id: int) -> list[dict] | None:
        """Returns None if user passes, or list of missing targets."""
        if not await ForcedMembershipService.is_enabled():
            return None
        r = await get_redis()
        if await r.get(f"fm:ok:{user_id}"):
            return None

        targets = await ForcedMembershipService.get_targets_cached()
        if not targets:
            return None

        missing = []
        for t in targets:
            try:
                member = await client.get_chat_member(t["channel_id"], user_id)
                if member.status.value not in ("member", "administrator", "creator"):
                    missing.append(t)
            except Exception:
                missing.append(t)

        if not missing:
            await r.set(f"fm:ok:{user_id}", "1", ex=180)
            return None
        return missing

    @staticmethod
    async def add_target(client, identifier: str, added_by: int) -> dict:
        """Validate via Telegram API, then persist."""
        chat = await client.get_chat(identifier)  # raises on failure
        chat_id = chat.id
        username = getattr(chat, "username", None)
        invite = getattr(chat, "invite_link", None)
        title = getattr(chat, "title", None)
        chat_type = chat.type.value  # "channel" / "group" / "supergroup"

        # Check bot is admin
        bot_me = await client.get_me()
        verify_status = "ok"
        try:
            bot_member = await client.get_chat_member(chat_id, bot_me.id)
            if bot_member.status.value not in ("administrator", "creator"):
                verify_status = "bot_not_admin"
        except Exception:
            verify_status = "bot_not_admin"

        target = await force_join_repo.upsert_target(
            channel_id=chat_id,
            channel_username=username,
            invite_link=invite,
            display_name=title,
            chat_type=chat_type,
            added_by=added_by,
            verify_status=verify_status,
        )
        await ForcedMembershipService.invalidate_cache()
        return {"target": target, "verify_status": verify_status}

    @staticmethod
    async def remove_target(channel_id: int):
        await force_join_repo.deactivate(channel_id)
        await ForcedMembershipService.invalidate_cache()

    @staticmethod
    async def verify_all(client) -> dict[str, int]:
        token = await acquire_lock("fm:verify", ttl_ms=30_000)
        if not token:
            return {"error": "already_running"}
        try:
            targets = await force_join_repo.get_all_targets()
            ok = broken = 0
            for t in targets:
                status = await ForcedMembershipService._verify_one(client, t)
                await force_join_repo.update_verify_status(t.id, status)
                if status == "ok":
                    ok += 1
                else:
                    broken += 1
            await ForcedMembershipService.invalidate_cache()
            return {"ok": ok, "broken": broken}
        finally:
            await release_lock("fm:verify", token)

    @staticmethod
    async def _verify_one(client, target) -> str:
        try:
            chat = await client.get_chat(target.channel_id)
        except Exception:
            return "inaccessible"
        try:
            bot_me = await client.get_me()
            m = await client.get_chat_member(target.channel_id, bot_me.id)
            if m.status.value not in ("administrator", "creator"):
                return "bot_not_admin"
        except Exception:
            return "bot_not_admin"
        # Update username if changed
        new_un = getattr(chat, "username", None)
        if new_un != target.channel_username:
            await force_join_repo.update_username(target.id, new_un)
        return "ok"
```

### 5.2 BroadcastServiceV2

```python
class BroadcastServiceV2:

    @staticmethod
    def extract_payload(message: Message) -> dict:
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
    async def create(admin_id: int, mode: str, target_scope: str,
                     payload: dict, source_chat_id: int | None = None,
                     source_message_id: int | None = None) -> Broadcast:
        bc = Broadcast(
            admin_id=admin_id, mode=mode, target_scope=target_scope,
            source_chat_id=source_chat_id, source_message_id=source_message_id,
            **payload,
        )
        return await broadcast_repo.create(bc)

    @staticmethod
    async def execute(client, broadcast_id: int):
        token = await acquire_lock(f"bc:{broadcast_id}", ttl_ms=600_000)
        if not token:
            return
        r = await get_redis()
        try:
            bc = await broadcast_repo.get_by_id(broadcast_id)
            if bc.status != "pending":
                return
            await broadcast_repo.update_status(broadcast_id, "running")
            await r.set("bc:active", str(broadcast_id))

            recipients = await BroadcastServiceV2._get_recipients(bc.target_scope)
            await broadcast_repo.update_total(broadcast_id, len(recipients))

            cursor = int(await r.get(f"bc:{broadcast_id}:cursor") or "0")
            sent = int(await r.get(f"bc:{broadcast_id}:sent") or "0")
            fail = int(await r.get(f"bc:{broadcast_id}:fail") or "0")

            for i in range(cursor, len(recipients)):
                chat_id = recipients[i]

                # Check cancellation every 50 messages
                if i % 50 == 0:
                    fresh = await broadcast_repo.get_by_id(broadcast_id)
                    if fresh.status == "canceled":
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
                        await asyncio.sleep(wait + 1)
                        try:
                            if bc.mode == "forward":
                                await client.forward_messages(
                                    chat_id, bc.source_chat_id, bc.source_message_id,
                                )
                            else:
                                await BroadcastServiceV2._send_one(client, chat_id, bc)
                            sent += 1
                            continue
                        except Exception:
                            pass
                    fail += 1

                # Pipeline update every 25 messages
                if (i + 1) % 25 == 0:
                    pipe = r.pipeline()
                    pipe.set(f"bc:{broadcast_id}:cursor", str(i + 1), ex=7200)
                    pipe.set(f"bc:{broadcast_id}:sent", str(sent), ex=7200)
                    pipe.set(f"bc:{broadcast_id}:fail", str(fail), ex=7200)
                    await pipe.execute()

                await asyncio.sleep(0.04)  # ~25 msg/sec

            final_status = "done"
            fresh = await broadcast_repo.get_by_id(broadcast_id)
            if fresh.status == "canceled":
                final_status = "canceled"
            await broadcast_repo.finish(broadcast_id, final_status, sent, fail)
        finally:
            await r.delete("bc:active")
            for suffix in ("cursor", "sent", "fail"):
                await r.delete(f"bc:{broadcast_id}:{suffix}")
            await release_lock(f"bc:{broadcast_id}", token)

    @staticmethod
    async def _send_one(client, chat_id: int, bc: Broadcast):
        """Re-create the message preserving formatting via entities."""
        entities = _rebuild_entities(bc.entities_json)
        cap_entities = _rebuild_entities(bc.caption_entities_json)

        senders = {
            "text":      lambda: client.send_message(chat_id, bc.text_content, entities=entities),
            "photo":     lambda: client.send_photo(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "video":     lambda: client.send_video(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "document":  lambda: client.send_document(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "audio":     lambda: client.send_audio(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "animation": lambda: client.send_animation(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "voice":     lambda: client.send_voice(chat_id, bc.file_id, caption=bc.caption, caption_entities=cap_entities),
            "sticker":   lambda: client.send_sticker(chat_id, bc.file_id),
        }
        sender = senders.get(bc.payload_type)
        if sender:
            await sender()
        elif bc.text_content:
            await client.send_message(chat_id, bc.text_content)

    @staticmethod
    async def cancel(broadcast_id: int):
        await broadcast_repo.update_status(broadcast_id, "canceled")

    @staticmethod
    async def _get_recipients(scope: str) -> list[int]:
        if scope == "groups":
            groups = await group_repo.get_all_active_groups()
            return [g.chat_id for g in groups]
        elif scope == "channels":
            from app.database.engine import async_session as _as
            from app.database.models import Channel
            async with _as() as s:
                result = await s.execute(
                    select(Channel).where(Channel.status == "active")
                )
                return [c.chat_id for c in result.scalars().all()]
        else:
            users = await user_repo.get_all_users()
            return [u.user_id for u in users]
```

### 5.3 Entity Serialization Helpers

```python
def _serialize_entities(entities) -> str | None:
    if not entities:
        return None
    return json.dumps([
        {
            "type": e.type.value if hasattr(e.type, "value") else str(e.type),
            "offset": e.offset, "length": e.length,
            "url": getattr(e, "url", None),
            "user_id": e.user.id if getattr(e, "user", None) else None,
            "language": getattr(e, "language", None),
            "custom_emoji_id": getattr(e, "custom_emoji_id", None),
        }
        for e in entities
    ])


def _rebuild_entities(json_str: str | None) -> list | None:
    if not json_str:
        return None
    from pyrogram.types import MessageEntity
    from pyrogram.enums import MessageEntityType
    raw = json.loads(json_str)
    result = []
    for e in raw:
        etype = MessageEntityType(e["type"])
        ent = MessageEntity(
            type=etype, offset=e["offset"], length=e["length"],
            url=e.get("url"), language=e.get("language"),
            custom_emoji_id=e.get("custom_emoji_id"),
        )
        result.append(ent)
    return result or None
```

### 5.4 Refactored force_join.py (User-Facing Gate)

```python
async def check_force_join(client: Client, message: Message, user_id: int) -> bool:
    if user_id == settings.DEVELOPER_ID:
        return True
    if await user_repo.is_sudo_or_above(user_id):
        return True

    missing = await ForcedMembershipService.check(client, user_id)
    if missing is None:
        return True

    lang = _DEFAULT_LANG
    rows = []
    for t in missing:
        link = t["invite_link"] or f"https://t.me/{t['username']}"
        label = t.get("display_name") or t.get("username") or str(t["channel_id"])
        rows.append([InlineKeyboardButton(
            t(lang, "fm.join_button", channel=label), url=link,
        )])
    rows.append([InlineKeyboardButton(
        t(lang, "fm.check_button"), callback_data=CB["START_FORCE_JOIN"],
    )])
    await message.reply(
        t(lang, "fm.join_required"),
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return False
```

"Check Again" handler with rate limiting:
```python
async def on_force_join_check(client, query):
    user_id = query.from_user.id
    r = await get_redis()

    # Rate limit
    if await r.get(f"fm:rl:{user_id}"):
        await query.answer(t(lang, "fm.rate_limited"), show_alert=True)
        return
    await r.set(f"fm:rl:{user_id}", "1", ex=5)

    missing = await ForcedMembershipService.check(client, user_id)
    if missing is None:
        await query.answer(t(lang, "fm.check_passed"), show_alert=False)
        await query.message.delete()
    else:
        await query.answer(t(lang, "fm.check_failed"), show_alert=True)
```

---

## 6. Error Handling & Logging

| Scenario | Response | Log |
|----------|----------|-----|
| `getChat` fails on add_target | Return error to admin: i18n `admin.fm.add_inaccessible` | WARNING with chat identifier (no invite link) |
| Bot not admin in target | Show verify_status, allow save with warning | INFO |
| `getChatMember` fails during user check | Treat as "not joined" (fail closed) | DEBUG |
| FloodWait during broadcast | Sleep `value + 1`, retry once | WARNING with seconds |
| UserIsBlocked / PeerIdInvalid during broadcast | Increment fail_count, skip | DEBUG |
| Concurrent broadcast start | Lock acquired returns None → reject | INFO |
| Redis down during membership check | Fall through to DB query | WARNING |
| Admin adds duplicate channel_id | Upsert: reactivate existing row | INFO |

**Log fields**: `feature=fm|bc`, `action`, `admin_id`, `user_id`, `target_id`, `broadcast_id`.
**Never log**: `invite_link`, session strings, file_ids.

---

## 7. Security Considerations

1. **Invite links**: Stored in DB column `invite_link`. Never included in log output. Admin panel shows masked version (`https://t.me/+A***Z`).
2. **Rate limits**: `fm:rl:{user_id}` (5s TTL) on "Check Again". Admin `_ask()` has 60s timeout.
3. **Permissions**: All FM/BC panel handlers gated by `developer_only` or `owner_or_above` decorators. Non-admins get `t(lang, "common.errors.no_access")`.
4. **Broadcast idempotency**: `lock:bc:{id}` prevents double execution. Cursor-based resume prevents duplicate sends after restart.
5. **Input validation**: `getChat()` validates target before persisting. Numeric IDs parsed via `parse_user_id()`.
6. **Anti-tamper**: Scheduled `verify_all` (hourly via APScheduler) detects removed bot / changed usernames / inaccessible channels.
7. **Fail closed**: If membership check errors, user is treated as not-joined.

---

## 8. Test Plan

### Unit Tests

| Test | Input | Expected |
|------|-------|----------|
| `check` returns None when `fm:ok:{uid}` cached | Redis mock with key set | None |
| `check` returns missing list for non-member | Mock `getChatMember` → status "left" | list with 1 target |
| `check` returns None when disabled | `force_join_enabled` = "0" | None |
| `extract_payload` captures text + entities | Message with bold + link | dict with entities_json |
| `extract_payload` captures photo + caption entities | Photo message with italic caption | dict with file_id + caption_entities_json |
| `_serialize_entities` / `_rebuild_entities` roundtrip | List of 3 entity types | Identical offset/length/type |
| `_verify_one` returns "inaccessible" | Mock getChat raises ChannelPrivate | "inaccessible" |
| `_verify_one` returns "bot_not_admin" | Mock getChatMember → "member" | "bot_not_admin" |
| Rate limit blocks rapid "Check Again" | Two calls < 5s apart | Second returns early |

### Integration Tests

| Test | Setup | Verify |
|------|-------|--------|
| Add target → cache invalidated | Mock getChat success | `fm:targets` key deleted |
| Remove target → user no longer prompted | Remove then check | `check` returns None |
| Broadcast send mode → entities preserved | Create + execute with mock client | `send_message` called with entities arg |
| Broadcast forward mode → `forward_messages` called | Create forward broadcast | `forward_messages` called with source IDs |
| FloodWait → sleep + retry | Mock first call raises FloodWait(3) | sleep(4) called, second attempt made |
| Broadcast cancel mid-run | Set status to "canceled" after 10 msgs | Loop exits, final status = "canceled" |
| Concurrent broadcast lock | Two `execute` calls | Second returns immediately |

### Manual Checklist

- [ ] Add public channel target → verify join prompt for non-member
- [ ] Add private channel (invite link) → verify URL button works
- [ ] Remove target → verify user no longer blocked
- [ ] Toggle off → verify gate bypassed
- [ ] Re-Verify All → verify broken targets flagged
- [ ] "Send to All" text with **bold** + _italic_ + [link] → verify formatting preserved
- [ ] "Send to All" photo with caption → verify caption formatting preserved
- [ ] "Forward to All" → verify "Forwarded from" header present
- [ ] Cancel running broadcast → verify stops within ~2 seconds
- [ ] Broadcast history → verify last 20 shown with correct stats
- [ ] Rate limit: rapid-click "Check Again" → verify throttled after first

---

## 9. Migration

### Alembic: `0004_fm_broadcast.py`

```python
"""Extend force_join_channels + add broadcasts table."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003_credit_history_partition_prep"

def upgrade():
    # 1. Extend force_join_channels
    op.add_column("force_join_channels",
        sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("force_join_channels",
        sa.Column("chat_type", sa.String(16), nullable=False, server_default="channel"))
    op.add_column("force_join_channels",
        sa.Column("position", sa.Integer, nullable=False, server_default="0"))
    op.add_column("force_join_channels",
        sa.Column("verify_status", sa.String(32), nullable=False, server_default="pending"))
    op.add_column("force_join_channels",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("force_join_channels",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("force_join_channels",
        sa.Column("last_error", sa.Text, nullable=True))
    op.add_column("force_join_channels",
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False))
    op.create_index("ix_fjc_position", "force_join_channels", ["position"])

    # 2. Create broadcasts table
    op.create_table("broadcasts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("admin_id", sa.BigInteger, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("target_scope", sa.String(16), nullable=False),
        sa.Column("payload_type", sa.String(32), nullable=True),
        sa.Column("text_content", sa.Text, nullable=True),
        sa.Column("entities_json", sa.Text, nullable=True),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("caption_entities_json", sa.Text, nullable=True),
        sa.Column("file_id", sa.String(512), nullable=True),
        sa.Column("source_chat_id", sa.BigInteger, nullable=True),
        sa.Column("source_message_id", sa.BigInteger, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("total_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fail_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_broadcasts_admin_id", "broadcasts", ["admin_id"])
    op.create_index("ix_broadcasts_status", "broadcasts", ["status"])
    op.create_index("ix_broadcasts_created_at", "broadcasts", ["created_at"])

def downgrade():
    op.drop_table("broadcasts")
    op.drop_index("ix_fjc_position", table_name="force_join_channels")
    for col in ("updated_at", "last_error", "last_checked_at", "verified_at",
                "verify_status", "position", "chat_type", "display_name"):
        op.drop_column("force_join_channels", col)
```

---

## 10. Implementation Task List

| # | Task | Files | Est. |
|---|------|-------|------|
| 1 | Write + run Alembic migration 0004 | `migrations/versions/` | 30m |
| 2 | Update `ForceJoinChannel` model with new columns | `models.py` | 15m |
| 3 | Add `Broadcast` model | `models.py` | 15m |
| 4 | Create `force_join_repo.py` | `repositories/` | 45m |
| 5 | Create `broadcast_repo.py` | `repositories/` | 30m |
| 6 | Add FM cache helpers to `cache.py` | `utils/cache.py` | 20m |
| 7 | Write `ForcedMembershipService` | `services/` | 1.5h |
| 8 | Write `BroadcastServiceV2` | `services/` | 2h |
| 9 | Add CB constants to `ui.py` | `utils/ui.py` | 10m |
| 10 | Add keyboard factory methods | `utils/ui.py` | 30m |
| 11 | Add i18n keys to `fa.json` + `en.json` | `resources/strings/` | 30m |
| 12 | Create `force_join_panel.py` handler | `handlers/` | 2h |
| 13 | Refactor `force_join.py` to use service | `handlers/force_join.py` | 1h |
| 14 | Create `broadcast_panel.py` handler | `handlers/` | 2h |
| 15 | Register new modules in `__init__.py` | `handlers/__init__.py` | 10m |
| 16 | Add hourly verify-all job to `scheduler.py` | `scheduler.py` | 20m |
| 17 | Write unit tests | `tests/` | 2h |
| 18 | Write integration tests | `tests/` | 2h |
| 19 | Manual QA against checklist | — | 1h |

**Total estimated**: ~16 hours

---

## 11. Performance & Security Checklist

- [ ] `fm:ok:{user_id}` cache hit avoids all `getChatMember` API calls
- [ ] `fm:targets` cache avoids DB query on every message
- [ ] Cache invalidated on every admin target add/remove/edit
- [ ] `fm:rl:{user_id}` prevents "Check Again" abuse (5s cooldown)
- [ ] Broadcast cursor-based resume: safe to restart mid-broadcast
- [ ] Broadcast lock prevents double execution
- [ ] Redis pipeline for batch counter updates (every 25 messages)
- [ ] `asyncio.sleep(0.04)` between sends ≈ 25 msg/sec (under Telegram limits)
- [ ] FloodWait handled: sleep `value + 1`, single retry
- [ ] Entity-based formatting (not parse_mode) for exact reproduction
- [ ] Invite links never logged
- [ ] All admin panels gated by decorators
- [ ] Broken targets auto-flagged by hourly scheduled job
- [ ] Broadcast history capped at last 20 (no unbounded queries)

---

## 12. i18n Keys to Add

### Structure (both `fa.json` and `en.json`)

```jsonc
{
  "admin": {
    "fm": {
      "title": "...",                    // Panel header
      "status_enabled": "...",           // "Status: ✅ Enabled"
      "status_disabled": "...",          // "Status: ❌ Disabled"
      "target_count": "...",             // "Targets: {count} active"
      "toggle_btn": "...",              // "Enable/Disable"
      "add_btn": "...",                  // "Add Target"
      "list_btn": "...",                 // "List Targets"
      "verify_btn": "...",              // "Re-Verify All"
      "test_btn": "...",                 // "Test My Membership"
      "add_prompt": "...",               // "Send @username, ID, or invite link"
      "add_validating": "...",           // "Validating..."
      "add_summary": "...",              // "Title: {title}\nType: {type}\nBot admin: {status}"
      "add_success": "...",              // "Target added ✅"
      "add_inaccessible": "...",         // "Bot cannot access this chat"
      "add_bot_not_admin": "...",        // "Warning: bot is not admin"
      "add_already_exists": "...",       // "Target already exists (reactivated)"
      "remove_confirm": "...",           // "Remove {title}?"
      "remove_success": "...",           // "Target removed ✅"
      "list_item": "...",                // "{pos}. {title}  {status_badge}"
      "list_empty": "...",               // "No targets configured"
      "verify_started": "...",           // "Verifying..."
      "verify_done": "...",              // "Done: {ok} ok, {broken} broken"
      "verify_already_running": "...",   // "Verification already in progress"
      "test_passed": "...",              // "You are a member of all targets ✅"
      "test_failed": "...",              // "You are NOT a member of: {channels}"
      "badge_ok": "✅",
      "badge_broken": "⚠️",
      "badge_pending": "⏳"
    },
    "bc": {
      "history_title": "...",            // "Broadcast History"
      "history_item": "...",             // "#{id} {mode}→{scope} {status} {sent}/{total}"
      "history_empty": "...",            // "No broadcasts yet"
      "confirm_send": "...",             // "Send to {count} {scope}?"
      "confirm_forward": "...",          // "Forward to {count} {scope}?"
      "started": "...",                  // "Broadcast #{id} started..."
      "done": "...",                     // "Broadcast #{id} done: {sent}/{total}"
      "canceled": "...",                 // "Broadcast #{id} canceled"
      "already_running": "...",          // "A broadcast is already running"
      "cancel_btn": "...",               // "Cancel Broadcast"
      "cancel_success": "..."            // "Broadcast canceled"
    }
  },
  "fm": {
    "join_required": "...",              // "Please join these channels to continue:"
    "join_button": "...",                // "Join {channel}"
    "check_button": "...",               // "I Joined ✅ Check Again"
    "check_passed": "...",               // "Verified ✅"
    "check_failed": "...",               // "You haven't joined all channels yet"
    "rate_limited": "..."                // "Please wait a few seconds"
  }
}
```

**Total new keys**: 40 (× 2 languages = 80 entries).

Existing keys to preserve: `force_join_mgmt.*`, `broadcast.*`, `status.force_join_label` — map old call sites to new keys or keep both during transition.
