# Redis Cache and Distributed Locks

This document maps out the caching layer and Redis dependencies for the Telegram Voice Chat Music & Player Bot. Redis provides ephemeral storage for rate limits, session context, locks, and cache hints.

> [!WARNING]
> **Redis is NOT the source of truth for canonical application state.** Pending sets (like `credit:expired_pending_leave`) act as heuristics for scheduling, but any destructive actions (like removing a bot from a group or deleting data) must strictly re-verify the canonical state against PostgreSQL before proceeding.

## Architecture

*   All keys and TTL values are centralized in `app/utils/redis_keys.py`.
*   The Redis client singleton is managed in `app/utils/cache.py`, initialized via `settings.REDIS_URL`.
*   The application strictly requires `decode_responses=True` on all Redis clients.
*   **Test Environment**: Test suites (`TEST_MODE=1`) use `fakeredis.aioredis.FakeRedis` by default unless `ALLOW_EXTERNAL_REDIS=1` is provided.

## Distributed Lock Implementation

Certain operations (e.g., credit transactions, broadcast dispatch, cache eviction) require mutual exclusion. The lock framework uses a layered approach:
1.  **Primary**: Redis `SET key token PX ttl NX` paired with a Lua script for safe release.
2.  **Fallback**: PostgreSQL `pg_try_advisory_lock` handles lock acquisition if the Redis node is unreachable.
To trace locks across systems, `pgadv:` is appended as a prefix when tokens are issued by the PostgreSQL fallback.

## Key Families Registry

| Domain | Key Pattern | TTL (default) | Purpose |
|---|---|---|---|
| **Chat Settings** | `settings:{chat_type}:{chat_id}` | 300s | Caches active UI/Playback features for a chat. |
| **Credit Status** | `credit:{chat_type}:{chat_id}` | 60s | Brief cache of parsed `GroupCredit` days remaining. |
| **Credit Warning** | `credit:warn:{type}:{id}:{days}:{date}` | 48h | Anti-spam cooldown ensuring warnings don't flood chats. |
| **Role Status** | `role:{user_id}` | 600s | Caches resolved permission status across group interactions. |
| **Bot Settings** | `botset:{key}` | 300s | Caches remote configurations set by `BotSetting`. |
| **Blacklist/Ban** | `blacklist:group:{id}`<br>`global_ban:user:{id}` | 300s | Fast-path drops for banned entities. |
| **Call Security** | `callsec:{chat_id}:state` | 24h | Active state tracker for the chat's call security flags. |
| **Broadcast** | `bc:{broadcast_id}:cursor` | 2h | Resumable offset state for the asynchronous broadcast engine. |
| **Analytics** | `an:{day}:{hour}:{scope}:{key}:{metric}` | 35d | Buffered analytic counters periodically flushed to the DB. |
| **Playback** | `playlist:{chat_id}` (lock)<br>`seek:{chat_id}` (state) | N/A | Tracks seek offsets and guards against queue race conditions. |
| **Helper Setup** | `wz:helper_otp:{user_id}` | 300s | Wizard state bridging telegram and the Helper OTP listener. |
| **Admin Panels** | `panelmsg:{chat_id}` | 1800s | Anchors the "Edit-First" panel UX to a specific message ID. |
| **Call Stats toggle** | `botset:call_stats:{chat_id}:enabled` | 300s | Cached `BotSetting` for per-chat call-stats feature gate (`call_stats_settings_repo`). |
| **Id command mode** | `botset:id_command:{chat_id}:output_mode`<br>`botset:id_command:{chat_id}:show_call_stats` | 300s | Cached `BotSetting` for Id output mode (`simple`/`photo`) and call-stats-in-Id toggle (`id_command_settings_repo`). |
| **Group call text** | `botset:group_text_call:{chat_id}:*` | 300s | Cached scheduled-end, auto-stats, mute/comment, and title keys for slash-free call commands. |

Canonical persistence for call-stats and Id settings is PostgreSQL `bot_settings` (string PK). Redis `botset:*` entries are cache hints only.

## Maintenance

Stale keys in Redis generally evict themselves according to their defined TTL. Persistent lists (e.g., `fm:targets`, `filterwords`) lack TTLs but are actively purged and replaced during system sync sweeps by `cache.py`.
