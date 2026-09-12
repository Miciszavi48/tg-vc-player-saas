# Specification: Multi-Helper Account Management via CLI

> **Canonical References:**
> - [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [../REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> **Implementation status (2026-07-19): IMPLEMENTED WITH LATER EXTENSIONS.** The helper pool CLI, encrypted sessions, OTP onboarding, reservations, proxy/device identity, and panel flows exist in current source. This specification remains the product baseline; use [../features/helper_bot.md](../features/helper_bot.md) and `python -m app.tools.helper_pool_cli --help` for current commands and limitations.
> **Codebase**: tg-vc-player-saas (Python 3.12, kurigram fork, PyTgCalls, PostgreSQL 16, Redis 7)

---

## 1. Goal and Scope

### What are Helper Accounts?

Helper accounts are real Telegram **user** accounts (not bots) used by the system to join voice chats and stream audio/video. The bot account cannot join voice chats directly — it delegates streaming to one or more helper user sessions managed via PyTgCalls.

### Account Separation

| Role | Type | Purpose |
|------|------|---------|
| Bot | Bot API token | Command handling, message processing, admin panels |
| Helper | MTProto user session | Join voice chats, stream media via PyTgCalls |
| Developer | Human admin | Full bot control, highest privilege |
| Owner | Human admin | Bot owner, manages sudos |
| Sudo | Human reseller | Installs bot in chats, charges credit |

### Success Criteria

1. Adding/managing many helpers is fast, safe, auditable, recoverable after restart
2. Joining helpers to chats and binding them is deterministic and idempotent
3. Operations are multi-instance safe and resilient to Telegram rate limits
4. Sessions are encrypted at rest; secrets never appear in logs
5. CLI supports both interactive and batch (scripted) workflows

---

## 2. Repository Audit Findings

| Component | Exists? | Path(s) | Notes |
|-----------|---------|---------|-------|
| HelperAccount model | **YES** | `app/database/models.py:509–534` | 18 columns: id, phone, session_string_enc, status, max_concurrent_calls, current_active_calls, max_joins_per_hour, joins_last_hour, last_login_at, last_used_at, created_at, updated_at, notes, cooldown_until, banned_until, last_error, last_error_at. **MISSING**: tg_user_id, display_name, username, quarantine_count |
| HelperChatBinding model | **YES** | `app/database/models.py:539–553` | PK=chat_id, helper_account_id, bound_at, updated_at. **MISSING**: bound_by, binding_state, last_error |
| Session encryption (Fernet) | **YES** | `app/services/helper_pool_service.py:132–160` | encrypt_session / decrypt_session with current + old key fallback |
| Existing CLI | **YES** | `app/tools/helper_pool_cli.py` | Commands: add, list, disable, enable, set-capacity, test, rotate-chat, rotate-key. **MISSING**: add-batch, show, delete, import-session, export-session, join-chat, leave-chat, bind-chat, unbind-chat, rebind-chat, ensure-joined, health-check, quarantine, unquarantine, stats |
| Helper selection strategy | **YES** | `app/services/helper_pool_service.py:19–50` | `get_best_helper`: tries bound helper first, then least-loaded active helper |
| Quarantine/cooldown | **YES** | `app/services/helper_pool_service.py:83–103` | `quarantine_helper`: sets status=quarantined, cooldown_until, last_error. **MISSING**: quarantine_count increment |
| Health check watchdog | **YES** | `app/scheduler.py:317–342` + `app/services/watchdog.py` | `helper_health_watchdog` probes session validity every 15 min. Watchdog restores helpers past cooldown_until |
| Join chat logic | **MISSING** | — | No `join_chat`/`leave_chat` via helper session in service layer |
| Bind/unbind logic | **YES** | `app/services/helper_pool_service.py:53–80` | `bind_chat_to_helper`, `release_chat` |
| Helper audit/event log | **MISSING** | — | No `helper_events` table or audit trail |
| Recovery service | **YES** | `app/services/recovery_service.py` | Restores active playback states after restart; uses PlaybackState.helper_account_id |
| Config settings | **YES** | `app/config/settings.py:142–166` | `HELPER_SESSION_KEY_CURRENT`, `HELPER_SESSION_KEY_OLD`, `HELPER_DEFAULT_MAX_CALLS`, `HELPER_DEFAULT_MAX_JOINS_PER_HOUR` |

---

## 3. Data Model

### 3.1 HelperAccount — Extend Existing

**Current columns** (keep all): id, phone, session_string_enc, status, max_concurrent_calls, current_active_calls, max_joins_per_hour, joins_last_hour, last_login_at, last_used_at, created_at, updated_at, notes, cooldown_until, banned_until, last_error, last_error_at

**New columns to add via migration:**

```python
tg_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
username: Mapped[str | None] = mapped_column(String(128), nullable=True)
quarantine_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
```

### 3.2 HelperChatBinding — Extend Existing

**Current columns** (keep all): chat_id (PK), helper_account_id (FK), bound_at, updated_at

**New columns to add:**

```python
bound_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
binding_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="bound")
last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`binding_state` values: `bound`, `pending_join`, `join_failed`

### 3.3 HelperEvent — New Table (Audit Log)

```python
class HelperEvent(Base):
    __tablename__ = "helper_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, server_default="cli")
    helper_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
```

Indexes: `(event_type)`, `(helper_account_id)`, `(created_at)`.

Event types: `helper.add`, `helper.login`, `helper.disable`, `helper.enable`, `helper.delete`, `helper.join_chat`, `helper.leave_chat`, `helper.bind`, `helper.unbind`, `helper.rebind`, `helper.quarantine`, `helper.unquarantine`, `helper.rotate_key`, `helper.health_check`, `helper.import_session`

---

## 4. Redis Keys, Locks, and TTLs

| Key | Purpose | TTL | Notes |
|-----|---------|-----|-------|
| `lock:helper:add` | Prevent concurrent add | 30s | Distributed lock via existing `acquire_lock` |
| `lock:helper:login:{id}` | Prevent concurrent login for same helper | 120s | Interactive login can be slow |
| `lock:helper:join:{id}:{chat_id}` | Prevent double-join | 60s | |
| `lock:helper:bind:{chat_id}` | Prevent concurrent bind for same chat | 30s | |
| `lock:helper:rotate_key` | Prevent concurrent key rotation | 300s | |
| `idem:helper:join:{id}:{chat_id}` | Idempotency: skip if recently joined | 3600s | Prevents rapid re-join |
| `idem:helper:bind:{chat_id}` | Idempotency: skip if recently bound | 300s | |
| `cache:helper:pool:v1` | Cached active helper list | 60s | Invalidate on add/remove/quarantine |
| `cache:helper:stats:{range}` | Cached stats output | 300s | |
| `helper:floodwait:{id}` | FloodWait backoff per helper | dynamic | TTL = wait_seconds from Telegram |

Use Redis pipelines for: batch stats counter reads, pool cache invalidation after multi-step operations.

---

## 5. CLI Design

### 5.1 Existing Commands (Verified)

| Command | Exists | Notes |
|---------|--------|-------|
| `add --phone` | YES | Prompts for session string interactively |
| `list [--all]` | YES | Shows table of helpers |
| `disable --id` | YES | Sets status=disabled |
| `enable --id` | YES | Via `activate_helper` |
| `set-capacity --id --max-calls` | YES | Updates max_concurrent_calls |
| `test --id` | YES | Creates temp Pyrogram client, calls get_me |
| `rotate-chat --chat-id --target-helper-id` | YES | Calls `bind_chat_to_helper` |
| `rotate-key` | YES | Re-encrypts all sessions with current key |

### 5.2 New Commands to Implement

#### `helper show <id>`
```bash
python -m app.tools.helper_pool_cli show --id 3
```
- Output: all fields for helper #3 (phone masked, session omitted)
- `--json` flag for machine-readable output
- Exit code: 0 success, 1 not found

#### `helper add-batch --file helpers.json`
```bash
python -m app.tools.helper_pool_cli add-batch --file helpers.json
```
- Input JSON format:
  ```json
  [
    {"phone": "+989...", "display_name": "H1", "string_session": "...", "max_calls": 50},
    {"phone": "+989...", "display_name": "H2", "string_session": "..."}
  ]
  ```
- Validates: phone format, duplicate phone check, session decryptability
- Skips existing (by phone), reports per-entry status
- Emits `helper.add` audit event per entry

#### `helper delete --id <id>`
```bash
python -m app.tools.helper_pool_cli delete --id 3 --confirm
```
- Safe deletion: refuses if helper has active bindings (use `--force` to override)
- Sets status=deleted, does NOT remove DB row (soft delete)
- Releases all bindings if `--force`

#### `helper import-session --id <id> --string-session <session>`
```bash
python -m app.tools.helper_pool_cli import-session --id 3 --string-session "BQ..."
```
- Encrypts and stores session
- Verifies via `get_me()` call
- Updates tg_user_id, username, display_name from Telegram
- Emits `helper.import_session` audit event

#### `helper export-session --id <id>`
```bash
python -m app.tools.helper_pool_cli export-session --id 3 --confirm-danger
```
- Requires `--confirm-danger` flag
- Prints decrypted session to stdout
- **Never logs the session**

#### `helper join-chat --id <id> --chat <@username|chat_id|invite_link>`
```bash
python -m app.tools.helper_pool_cli join-chat --id 3 --chat "@my_channel"
```
- Acquires `lock:helper:join:{id}:{chat_id}`
- Checks idempotency key `idem:helper:join:{id}:{chat_id}`
- Creates temp Pyrogram client from helper session
- Calls `client.join_chat(identifier)`
- On FloodWait: reports wait time, exits with code 2
- Emits `helper.join_chat` audit event

#### `helper leave-chat --id <id> --chat <chat_id>`
```bash
python -m app.tools.helper_pool_cli leave-chat --id 3 --chat -1001234
```
- Calls `client.leave_chat(chat_id)`
- Removes binding if exists
- Emits `helper.leave_chat` audit event

#### `helper bind-chat --chat <chat_id> --id <helper_id>`
```bash
python -m app.tools.helper_pool_cli bind-chat --chat -1001234 --id 3
```
- Acquires `lock:helper:bind:{chat_id}`
- Creates/updates HelperChatBinding
- Sets binding_state=bound
- Invalidates `cache:helper:pool:v1`
- Emits `helper.bind` audit event

#### `helper unbind-chat --chat <chat_id>`
```bash
python -m app.tools.helper_pool_cli unbind-chat --chat -1001234
```
- Removes binding row
- Emits `helper.unbind` audit event

#### `helper rebind-chat --chat <chat_id>`
```bash
python -m app.tools.helper_pool_cli rebind-chat --chat -1001234
```
- Calls `get_best_helper(chat_id)` to auto-select
- Binds the selected helper
- Emits `helper.rebind` audit event

#### `helper ensure-joined --chat <chat_id>`
```bash
python -m app.tools.helper_pool_cli ensure-joined --chat -1001234
```
- Looks up binding for chat_id
- Checks membership via `get_chat_member`
- If not a member, attempts `join_chat`
- Reports status

#### `helper health-check [--all | --id <id>]`
```bash
python -m app.tools.helper_pool_cli health-check --all
```
- For each helper: decrypt session, call `get_me()`, report status
- On failure: set last_error, optionally quarantine
- Emits `helper.health_check` audit event

#### `helper quarantine --id <id> --minutes N --reason "..."`
```bash
python -m app.tools.helper_pool_cli quarantine --id 3 --minutes 60 --reason "FloodWait"
```
- Sets status=quarantined, cooldown_until, last_error, increments quarantine_count
- Emits `helper.quarantine` audit event

#### `helper unquarantine --id <id>`
```bash
python -m app.tools.helper_pool_cli unquarantine --id 3
```
- Calls `activate_helper`
- Emits `helper.unquarantine` audit event

#### `helper stats --range <24h|7d|30d>`
```bash
python -m app.tools.helper_pool_cli stats --range 7d
```
- Queries helper_events and helper_accounts for summary
- Output: total helpers, active/quarantined/disabled counts, total bindings, events by type
- `--json` flag for machine output

### 5.3 Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (not found, validation, DB) |
| 2 | Rate limited (FloodWait) |
| 3 | Lock conflict (another operation in progress) |

---

## 6. Operational Flows

### 6.1 Onboarding a New Helper

```
1. helper add --phone "+989..."
   → Prompts for session string (or skip)
   → Encrypts session, creates DB row
   → Emits helper.add event

2. helper import-session --id N --string-session "BQ..."
   (if session was not provided in step 1)
   → Encrypts, stores, verifies via get_me()
   → Populates tg_user_id, username, display_name

3. helper test --id N
   → Confirms login works

4. helper health-check --id N
   → Full health probe

5. Helper is now status=active and ready for binding
```

### 6.2 Adding Helper to a Group/Channel Install

```
1. Resolve chat identifier:
   - @username → getChat → chat_id
   - Numeric → use directly
   - Invite link → join_chat returns chat_id

2. helper join-chat --id N --chat <identifier>
   → Acquire lock:helper:join:{N}:{chat_id}
   → Check idem:helper:join:{N}:{chat_id}
   → Create temp Pyrogram client
   → Call client.join_chat(identifier)
   → On FloodWait: sleep + retry once, else exit 2
   → Verify membership via get_chat_member
   → Set idem key (1h TTL)
   → Emit helper.join_chat event

3. helper bind-chat --chat <chat_id> --id N
   → Acquire lock:helper:bind:{chat_id}
   → Upsert HelperChatBinding (binding_state=bound)
   → Invalidate cache:helper:pool:v1
   → Emit helper.bind event

4. On ANY failure: rollback (leave chat if just joined), log error, emit event
```

### 6.3 Replacing Helper for a Chat (Rebind)

```
Triggers: helper quarantined, FloodWait, helper deleted, manual

1. helper rebind-chat --chat <chat_id>
   → get_best_helper(chat_id) picks least-loaded active helper
   → New helper joins chat (join-chat flow)
   → Bind new helper (bind-chat flow)
   → Old helper leaves chat (leave-chat) after new binding is confirmed
   → Emit helper.rebind event

Multi-instance safety: lock:helper:bind:{chat_id} prevents races
Stream continuity: rebind + stream restart is a best-effort operation
```

### 6.4 Uninstall or Leaving Chats

```
When bot is removed from a chat (or credit expires and auto-leave triggers):

1. Lookup binding: SELECT FROM helper_chat_bindings WHERE chat_id = X
2. If binding exists:
   a. Helper leaves chat via client.leave_chat(chat_id)
   b. Delete binding row
   c. Invalidate cache
   d. Emit helper.leave_chat event
3. Clean up PlaybackState and related caches
```

---

## 7. Safety, Compliance, and Security

### Secret Handling Policy
- Session strings: encrypted at rest via Fernet, decrypted only in memory for temporary Pyrogram client creation
- **Never log** session strings, invite links, or Fernet keys
- `mask_phone()` used for all phone display (existing in `app/utils/helpers.py:25`)
- `export-session` requires explicit `--confirm-danger` flag

### Encryption Design
- Algorithm: Fernet (AES-128-CBC + HMAC-SHA256)
- Keys: `HELPER_SESSION_KEY_CURRENT` (primary), `HELPER_SESSION_KEY_OLD` (rotation fallback)
- Decrypt tries current key first, falls back to old key
- `rotate-key` re-encrypts all sessions with current key

### Access Control
- CLI is server-side only — requires SSH/shell access
- No bot-side command to add/remove helpers (only developer panel shows status)
- Audit log tracks actor for every operation

### Telegram Rate Limits
- `max_joins_per_hour` per helper (default 300)
- On FloodWait: store `helper:floodwait:{id}` with TTL = wait_seconds
- Stagger joins: 2-second delay between consecutive joins
- Health checks: max 1 concurrent per helper (lock)

---

## 8. Multi-Instance Concurrency Map

| Operation | Lock Key | Idempotency Key | DB Transaction | Failure Mode |
|-----------|----------|-----------------|----------------|--------------|
| helper add | `lock:helper:add` | phone unique constraint | session.begin | Duplicate phone → error |
| helper login | `lock:helper:login:{id}` | — | — | Timeout → error |
| helper join chat | `lock:helper:join:{id}:{chat_id}` | `idem:helper:join:{id}:{chat_id}` | — | FloodWait → exit 2 |
| helper bind chat | `lock:helper:bind:{chat_id}` | `idem:helper:bind:{chat_id}` | session.begin | Race → one wins |
| rotate key | `lock:helper:rotate_key` | — | per-helper txn | Partial → resume |
| health check | `lock:helper:login:{id}` | — | — | Probe fail → quarantine |
| quarantine | — | — | session.begin | — |

---

## 9. Integration Points with Bot Runtime

### Helper Selection for Playback
- `CallService.join_voice_chat()` → `HelperPoolService.get_best_helper(chat_id)` → returns bound or least-loaded helper
- Runtime creates PyTgCalls instance with helper session

### Quarantine During Playback
- If `join_group_call` fails with UserRestricted/FloodWait → `quarantine_helper(helper_id, reason, duration)`
- Triggers rebind for the chat

### Pre-stream Join Check
- Before starting stream: verify helper is member of the chat
- If not: attempt `join_chat` via helper session
- If join fails: try next helper (rebind)

### Recovery After Restart
- `recovery_service.py` reads `playback_states` with `helper_account_id`
- For each state: verify helper is still active and in the chat
- Skip if helper quarantined; rebind if needed

### Modules to Modify
- `app/services/call_service.py` — add pre-stream join check
- `app/services/helper_pool_service.py` — add `join_chat_as_helper()`, `leave_chat_as_helper()`, `ensure_helper_joined()`
- `app/services/recovery_service.py` — add helper availability check before resume

---

## 10. Observability and Logs

### Structured Log Fields
```python
logger.info("Helper operation",
    extra={
        "component": "helper",
        "action": "join_chat",
        "helper_id": 3,
        "chat_id": -1001234,
        "status": "success",
    })
```

### Log Levels
| Event | Level |
|-------|-------|
| Helper added/enabled/disabled | INFO |
| Session encrypted/decrypted | DEBUG (no secrets) |
| Join chat success | INFO |
| FloodWait encountered | WARNING |
| Health check failure | WARNING |
| Quarantine triggered | WARNING |
| Session decrypt failure | ERROR |
| Key rotation error | ERROR |

### Telegram Log Channel
- Helper quarantined → send notification to LOG_CHANNEL_ID
- Health check degraded → send summary to LOG_CHANNEL_ID
- Uses existing `NotificationService.notify_error()` pattern

---

## 11. Test Plan

### Unit Tests
| Test | Module |
|------|--------|
| `test_encrypt_decrypt_roundtrip` | `helper_pool_service.py` |
| `test_decrypt_with_old_key_fallback` | `helper_pool_service.py` |
| `test_mask_phone` | `utils/helpers.py` |
| `test_cli_parser_all_commands` | `helper_pool_cli.py` — verify argparse accepts all commands |
| `test_add_batch_validation` | Reject invalid phone, skip duplicates |
| `test_quarantine_increments_count` | Verify quarantine_count field |

### Integration Tests (Mock Pyrogram)
| Test | Description |
|------|-------------|
| `test_join_chat_success` | Mock `client.join_chat` → verify binding created + event emitted |
| `test_join_chat_floodwait` | Mock FloodWait → verify exit code 2 |
| `test_bind_idempotent` | Two concurrent binds → only one succeeds (lock) |
| `test_health_check_quarantines_failed` | Mock `get_me()` failure → verify helper quarantined |
| `test_rebind_selects_least_loaded` | Mock pool → verify correct helper selected |

### Manual Checklist
- [ ] Add a helper via CLI with real phone + session
- [ ] Run `test` to verify login
- [ ] Run `join-chat` with a test channel
- [ ] Run `bind-chat` and verify via `list`
- [ ] Run `health-check --all` and verify output
- [ ] Run `quarantine` and verify helper excluded from selection
- [ ] Run `unquarantine` and verify helper available again
- [ ] Run `rotate-key` after setting new HELPER_SESSION_KEY_CURRENT
- [ ] Run `stats --range 24h` and verify counts

---

## 12. Migration Plan

### Alembic Migration: `0006_helper_extensions.py`

```python
def upgrade():
    # Extend helper_accounts
    op.add_column("helper_accounts",
        sa.Column("tg_user_id", sa.BigInteger, nullable=True, unique=True))
    op.add_column("helper_accounts",
        sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("helper_accounts",
        sa.Column("username", sa.String(128), nullable=True))
    op.add_column("helper_accounts",
        sa.Column("quarantine_count", sa.Integer, nullable=False, server_default="0"))

    # Extend helper_chat_bindings
    op.add_column("helper_chat_bindings",
        sa.Column("bound_by", sa.BigInteger, nullable=True))
    op.add_column("helper_chat_bindings",
        sa.Column("binding_state", sa.String(32), nullable=False, server_default="bound"))
    op.add_column("helper_chat_bindings",
        sa.Column("last_error", sa.Text, nullable=True))

    # Create helper_events
    op.create_table("helper_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False, server_default="cli"),
        sa.Column("helper_account_id", sa.Integer, nullable=True),
        sa.Column("chat_id", sa.BigInteger, nullable=True),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_helper_events_type", "helper_events", ["event_type"])
    op.create_index("ix_helper_events_helper", "helper_events", ["helper_account_id"])
    op.create_index("ix_helper_events_created", "helper_events", ["created_at"])

def downgrade():
    op.drop_table("helper_events")
    for col in ("last_error", "binding_state", "bound_by"):
        op.drop_column("helper_chat_bindings", col)
    for col in ("quarantine_count", "username", "display_name", "tg_user_id"):
        op.drop_column("helper_accounts", col)
```

### Backfill Strategy
- `tg_user_id`, `display_name`, `username`: populated on next `test` or `health-check` per helper
- `quarantine_count`: defaults to 0
- `binding_state`: defaults to "bound" for all existing rows

---

## 13. Implementation Checklist

### MUST (Core)

| # | Task | Est. |
|---|------|------|
| 1 | Alembic migration `0006_helper_extensions` | 30m |
| 2 | Update HelperAccount + HelperChatBinding models with new columns | 15m |
| 3 | Add HelperEvent model | 15m |
| 4 | Create `app/repositories/helper_event_repo.py` (append-only write, query by type/helper/range) | 30m |
| 5 | Add `join_chat_as_helper()`, `leave_chat_as_helper()`, `ensure_helper_joined()` to HelperPoolService | 2h |
| 6 | Add new CLI commands: show, add-batch, delete, import-session, export-session | 2h |
| 7 | Add new CLI commands: join-chat, leave-chat, bind-chat, unbind-chat, rebind-chat, ensure-joined | 3h |
| 8 | Add new CLI commands: health-check, quarantine, unquarantine, stats | 1.5h |
| 9 | Add Redis lock/idempotency keys to all CLI operations | 1h |
| 10 | Add audit event emission to all CLI commands | 1h |
| 11 | Write unit tests (encryption, CLI parser, validation) | 1.5h |
| 12 | Write integration tests (mock Pyrogram join/leave, lock contention) | 2h |

### SHOULD (Reliability)

| # | Task | Est. |
|---|------|------|
| 13 | Pre-stream join check in `CallService.join_voice_chat` | 1h |
| 14 | FloodWait backoff Redis key + respect in join/health flows | 30m |
| 15 | Quarantine count tracking + auto-disable at threshold | 30m |
| 16 | Log channel notifications for quarantine/health events | 30m |

### OPTIONAL (Polish)

| # | Task | Est. |
|---|------|------|
| 17 | `--json` output flag for all CLI commands | 1h |
| 18 | Pool cache invalidation on add/remove/quarantine | 30m |
| 19 | Periodic helper_events cleanup (retain 90 days) | 30m |
| 20 | Dashboard summary in dev panel (helper count, active/quarantined) | 1h |

**Total estimated**: ~20 hours
