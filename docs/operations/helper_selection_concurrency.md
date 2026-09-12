# Helper selection concurrency — Phase 2D-5A / 2D-5B plan

> **Current Status:**
> This document is preserved as operational/historical context detailing the Phase 2D-5A and 2D-5B helper reservation implementation.
> For current canonical details on the helper subsystem, see:
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)

## 1. Current helper selection flow

### Storage

| Table | Role |
|-------|------|
| `helper_accounts` | Telegram helper sessions, `status`, `current_active_calls`, `max_concurrent_calls`, cooldown/ban timestamps |
| `helper_chat_bindings` | One row per `chat_id` → `helper_account_id` (sticky assignment) |

### `HelperPoolService.get_best_helper(chat_id)`

1. **Separate DB session** (no explicit transaction spanning selection + increment).
2. If `helper_chat_bindings` has a row for `chat_id` and the bound helper is `status == "active"` → return that helper **without** re-checking `current_active_calls < max_concurrent_calls`.
3. Else pool query:
   - `status == "active"`
   - `current_active_calls < max_concurrent_calls`
   - `cooldown_until` null or past
   - `banned_until` null or past
   - `ORDER BY current_active_calls ASC LIMIT 1`
4. Returns `None` if no row matches.

**Redis:** not used in `get_best_helper`. Redis is used later for `ensure_helper_joined` idempotency (`idem:helper:join:*`) and join locks (`helper:join:{id}`).

### Call path (`CallService`)

Used for **group** voice joins (`join_group_call` / `join_voice_chat`), not a separate channel-specific helper API.

Typical sequence:

1. `join_voice_chat` → `_ensure_helper_in_chat(chat_id)`
   - `get_best_helper` → `ensure_helper_joined` → `bind_chat_to_helper`
   - may `quarantine_helper` + retry with another helper
   - logs `helper.join_chat` via `helper_event_repo` on success
2. After successful PyTgCalls join → `increment_active_calls(chat_id)` (binding lookup + SQL `UPDATE`)
3. On leave → `decrement_active_calls(chat_id)` (`WHERE current_active_calls > 0`)

**Selection and increment are not atomic:** different sessions/transactions; increment runs only after a successful stream join.

### Capacity

Enforced in pool SQL (`current_active_calls < max_concurrent_calls`). Bound-helper fast path can return an at-capacity helper until a later failure/quarantine path.

---

## 2. Current race condition

Two concurrent plays in different chats can both run `get_best_helper` before either `bind_chat_to_helper` or `increment_active_calls` commits. Both can read the same helper with the lowest `current_active_calls` and proceed.

There is no `FOR UPDATE`, `SKIP LOCKED`, or optimistic `WHERE active_calls < max` on selection.

---

## 3. Why this causes over-assignment

`current_active_calls` is incremented **after** join success, while selection reads stale counts. Multiple chats can be assigned the same helper and exceed `max_concurrent_calls` in reality even when SQL filters look correct at selection time.

---

## 4. Future strategy options

| Option | Approach | Pros | Cons |
|--------|----------|------|------|
| **A** | Single transaction: `SELECT … FOR UPDATE SKIP LOCKED` on chosen row, increment, commit; then bind | DB-native, fits PostgreSQL pool | Must refactor `get_best_helper` + bind + increment; handle bound-chat path |
| **B** | Redis lock `helper:select` or per-helper lock during pick+increment | No migration | Extra failure mode if Redis down; not durable count |
| **C** | Optimistic `UPDATE … SET current_active_calls = current_active_calls + 1 WHERE id = ? AND current_active_calls < max` retry loop | Simple counter bump | Retry storms; still need bind ordering |

---

## 5. Recommended strategy (Phase 2D-5B)

**Option A (conservative):** PostgreSQL transaction with row-level lock on `helper_accounts`:

1. Begin transaction.
2. If binding exists and helper still eligible → lock that row.
3. Else `SELECT id FROM helper_accounts WHERE … ORDER BY current_active_calls ASC FOR UPDATE SKIP LOCKED LIMIT 1`.
4. Increment `current_active_calls` in same transaction.
5. Upsert `helper_chat_bindings`.
6. Commit.

Keep Redis join idempotency as-is; do not use Redis as source of truth for capacity.

Also tighten bound-helper path to respect `current_active_calls < max_concurrent_calls` (product decision — document in 5B PR).

---

## 6. Files likely to change in Phase 2D-5B

- `app/services/helper_pool_service.py` — atomic `assign_helper_for_chat` (new) or refactor `get_best_helper` + increment
- `app/services/call_service.py` — call single assignment API; remove separate increment or make increment idempotent no-op when already assigned in txn
- `tests/test_helper_selection_concurrency_phase2d5a.py` — extend with atomic tests
- Optional: `tests/test_helper_management.py` — update mocks

**Not in scope:** PyTgCalls streams, scheduler, migrations (unless index proven necessary later).

---

## 7. Tests required before 2D-5B

- All `tests/test_helper_selection_concurrency_phase2d5a.py` (baseline behavior)
- New: concurrent assignment test on disposable DB or deterministic transaction mocks
- Existing `tests/test_helper_fixes.py`, `tests/test_helper_management.py` regression
- No over-capacity under N parallel assigns (synthetic helpers)

---

## 8. Rollback plan

Revert 2D-5B commit; behavior returns to 5A-locked non-atomic path. Monitor `current_active_calls` vs real active VC count after deploy.

---

## 9. Non-goals

- Changing PyTgCalls / FFmpeg routing
- Helper OTP wizard / panel UI
- New migrations or indexes in 5B unless profiling demands
- Daily deduction batching enablement
- Broadcast / credit / favorites / VIP changes

---

## 10. Phase 2D-5A deliverables (done)

- Pure helpers `_helper_is_pool_eligible`, `_helper_sort_key` (document SQL semantics)
- Behavior-lock tests: `tests/test_helper_selection_concurrency_phase2d5a.py`
- **No** production locking or selection behavior change beyond additive pure helpers

---

## 11. Phase 2D-5B deliverables (done)

### Atomic reservation

- `HelperPoolService.reserve_best_helper(chat_id)` — one transaction: lock row (`FOR UPDATE SKIP LOCKED`), increment `current_active_calls`, commit; returns `ReservedHelper` DTO.
- Bound path: eligible binding (active, capacity, cooldown/ban) tried first; at-capacity bound helper falls through to pool.
- Pool path: `ORDER BY current_active_calls ASC, id ASC`, `SKIP LOCKED`, increment in same transaction.
- `HelperPoolService.release_helper_reservation(helper_id)` — decrements when join/setup fails before active call.
- `get_best_helper` kept read-only for panels/tests; **no migration or index**.

### Call flow

1. `_ensure_helper_in_chat` → `reserve_best_helper` → Telegram `ensure_helper_joined` → `bind` (outside txn).
2. `join_voice_chat` — no post-join `increment_active_calls`; `finally` releases if stream join never becomes active.
3. `leave_voice_chat` — still `decrement_active_calls(chat_id)` once per normal end.

### Lock duration

DB row lock is held only inside `reserve_best_helper` / `release_helper_reservation` transactions — **not** during PyTgCalls or Telegram HTTP.

### Failure release

- Pre-stream join failure: `release_helper_reservation` before quarantine/retry.
- Post-reserve setup failure (transcode/stream/join): `join_voice_chat` `finally` when `call_active` is false.

### Tests

- `tests/test_helper_atomic_reservation_phase2d5b.py`
- Updated `tests/test_helper_selection_concurrency_phase2d5a.py`, `tests/test_helper_management.py`

---

## 12. Phase 2D-5C deliverables (done)

### Disposable PostgreSQL integration

- **File:** `tests/test_helper_atomic_reservation_disposable_phase2d5c.py`
- **Database:** `musicbot_disposable` only (or explicit safe test DB markers); refuses `musicbot_dev` and production-like names.
- **Alembic:** the disposable test skips unless its database is migrated to the dynamically discovered head. The current repository head is `0033_instance_database_ownership`; migrate the disposable database before running.
- **Isolation:** test phones `+99052d5c*`, chat_ids `-990000002000` … `-990000002099`; deletes only rows created in each test.
- **No validation script** — pytest is the operator entry point.

### Integration coverage

1. Reserve increments `current_active_calls` by 1 (DB + DTO after 5C fix).
2. Release decrements; never below zero.
3. Concurrent reserve with `max_concurrent_calls=1` — at most one success (skips if disposable DB has extra eligible helpers).
4. Concurrent reserve with two test helpers — different IDs.
5. At-capacity / ineligible / bound / bound-at-capacity pool fallback.
6. Release restores capacity.
7. Indirect: reservation returns before simulated join sleep; second reserve can proceed (lock not held during “Telegram” work).

### 5C production fix

- **`ReservedHelper.current_active_calls`:** after SQL increment, `session.refresh(helper)` before building DTO (fixes double-count in DTO when ORM already reflected `+1`).

### Operational note (capacity drift)

- If helper capacity looks stuck, inspect `helper_accounts.current_active_calls`.
- Normal recovery: active call end → `leave_voice_chat` → `decrement_active_calls(chat_id)`.
- Failed setup before active call: `release_helper_reservation(helper_id)`.
- Do **not** edit counts manually without backup and investigation.

### Run integration tests

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://musicbot:devpassword@127.0.0.1:5432/musicbot_disposable"
pytest tests/test_helper_atomic_reservation_disposable_phase2d5c.py -q
```

Skipped tests mean disposable DB unavailable, wrong Alembic head, or pre-existing eligible helpers breaking isolation — **not** a PASS.
