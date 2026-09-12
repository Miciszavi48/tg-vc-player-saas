# Concurrency Map — Multi-Instance Safety

> Last verified against repository: 2026-07-19

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)

This document maps every critical operation to its concurrency controls.
The system is designed to run multiple app instances safely behind a
shared Redis + PostgreSQL backend.

## Lock Key Conventions

| Pattern | Scope | TTL | Usage |
|---|---|---|---|
| `lock:credit:{chat_id}` | per-chat | 5s | credit charge/deduct/trial |
| `lock:chat:{chat_id}` | per-chat | 5s | playlist mutations, play/stop |
| `lock:install:{chat_id}` | per-chat | 10s | install/uninstall events |
| `lock:wallet:{sudo_user_id}` | per-user | 5s | wallet top-up/deduct |
| `lock:cron:daily_deduct` | global singleton | 120s | midnight credit deduction |
| `helper:join:{helper_id}` | per-helper | varies | helper voice-chat join serialization |
| `bc:{broadcast_id}` | per-broadcast | varies | broadcast wizard / send coordination |

## Operations

### Credit Operations

| Operation | Lock Key | DB Transaction | Idempotency | Failure Handling |
|---|---|---|---|---|
| `CreditService.charge()` | `lock:credit:{chat_id}` | `SELECT FOR UPDATE` on `group_credits` | Append-only history — duplicate charges create separate history rows | Lock timeout → RuntimeError → retry at handler level |
| `CreditService.deduct()` | `lock:credit:{chat_id}` | `SELECT FOR UPDATE` on `group_credits` | Same | Same |
| `CreditService.activate_trial()` | `lock:credit:{chat_id}` | `SELECT FOR UPDATE` + `is_trial` guard | Trial can only activate once per chat (`is_trial=True` check) | ValueError if already trialed |
| `CreditService.charge_with_wallet()` | `lock:credit:{chat_id}` | Single transaction: wallet deduct + credit charge + history — all atomic | Wallet deduction and credit charge in same DB transaction | Rolls back both on any failure |
| `CreditService.daily_deduct_all()` | `lock:cron:daily_deduct` | `SELECT FOR UPDATE` on all active credits | Global lock prevents double-deduction across instances | Lock not acquired → skip silently |

### Install / Uninstall

| Operation | Lock Key | DB Transaction | Idempotency | Failure Handling |
|---|---|---|---|---|
| `_on_new_install()` | `lock:install:{chat_id}` | `upsert_group` (ON CONFLICT) + `create_defaults` + `activate_trial` | Group upsert is idempotent; trial guard prevents double-activation | Lock timeout → skip (another instance handled it) |
| `_on_left()` | `lock:install:{chat_id}` | `deactivate_group` | Deactivation is idempotent (set status) | Same |

### Playback (Play/Stop/Pause/Resume)

| Operation | Lock Key | DB Transaction | Idempotency | Failure Handling |
|---|---|---|---|---|
| `CallService.join_voice_chat()` | `lock:chat:{chat_id}` (acquired by handler) | Writes `playback_states` | Rejoining same chat replaces state | pytgcalls errors caught and logged |
| `CallService.leave_voice_chat()` | `lock:chat:{chat_id}` (acquired by handler) | Deletes `playback_states` | Leaving already-left chat is safe | Errors caught |
| `CallService.play_next()` | `lock:chat:{chat_id}` | `advance_queue` with `SELECT FOR UPDATE` | Queue position shift is atomic | Falls back to leave on failure |

### Queue / Playlist

| Operation | Lock Key | DB Transaction | Idempotency | Failure Handling |
|---|---|---|---|---|
| `add_to_queue()` | `lock:chat:{chat_id}` | `SELECT FOR UPDATE` on max position | Unique constraint on (chat_id, position) | Lock prevents race |
| `remove_from_queue()` | `lock:chat:{chat_id}` | DELETE within lock | Idempotent (no error if missing) | Same |
| `clear_queue()` | `lock:chat:{chat_id}` | DELETE all for chat | Idempotent | Same |
| `advance_queue()` | `lock:chat:{chat_id}` | `SELECT FOR UPDATE` + delete + shift | Lock ensures single advancer | Same |

### Wallet Operations

| Operation | Lock Key | DB Transaction | Idempotency | Failure Handling |
|---|---|---|---|---|
| Owner top-up | `lock:wallet:{sudo_user_id}` | `SELECT FOR UPDATE` on `sudo_wallets` | Each top-up creates unique transaction row | Lock timeout → retry message |
| Sudo charge deduction | Inside `charge_with_wallet` | `SELECT FOR UPDATE` on wallet + credit in same txn | Transaction record with ref_id | Rolls back on insufficient funds |

### Cron / Scheduled Jobs

| Job | Lock Key | Behaviour Under Multi-Instance |
|---|---|---|
| `midnight_credit_deduct` | `lock:cron:daily_deduct` | Only one instance runs; others skip |
| `check_trial_expiry` | **No global lock** | Each instance may run; job uses DB `SELECT` + per-row updates (idempotent trial expiry) |
| `check_*_credit_warnings` | Redis `credit:warn:{chat_id}:{days}:{date}` SET NX EX 48h | One warning per chat/days-left/local-date; fail-closed if Redis unavailable |
| `cleanup_downloads` | No lock (local filesystem) | Each instance cleans its own downloads |
| `health_check` | No lock (read-only) | Multiple health reports acceptable |

## Fallback: Postgres Advisory Locks

When Redis is unavailable, `acquire_lock()` automatically falls back to
`pg_try_advisory_lock()`.  The connection is kept alive until
`release_lock()` calls `pg_advisory_unlock()` on the same connection.

## Rate Limiting for Telegram API

Telegram rate limits are managed via:
- `RECOVERY_GLOBAL_PER_SECOND` — max recovery re-joins per second
- `RECOVERY_HELPER_PER_MINUTE` — per-helper join rate
- `BroadcastService._FLOOD_SLEEP` — inter-message delay during broadcasts
- `asyncio.Semaphore(DOWNLOAD_SEMAPHORE)` — concurrent download limit

FloodWait exceptions trigger:
1. Helper quarantine (`status='cooldown'`, `cooldown_until = now + seconds`)
2. Exponential backoff sleep
3. Re-enqueue of the failed operation
