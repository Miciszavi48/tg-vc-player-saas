# Daily credit deduction — Phase 2D-4A / 2D-4B plan

> **Current Status:**
> This document is preserved as operational/historical context detailing the Phase 2D-4A and 2D-4B batching implementation.
> For current canonical details on the credit subsystem, see:
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)

## 1. Current behavior summary

### Entry points

| Path | Used by cron? | Notes |
|------|---------------|-------|
| `CreditService.daily_deduct_all()` | **Yes** — `scheduler.midnight_credit_deduct` | Production path |
| `credit_repo.daily_deduct_all()` | **No** | Duplicate logic; no distributed lock, no cache invalidation, no `CREDIT_EXPIRED_PENDING` |

### Scheduler

- Job: `midnight_credit_deduct`, APScheduler cron **`hour=0, minute=0`**, id `midnight_credit_deduct`.
- Flow:
  1. `count = await CreditService.daily_deduct_all()`
  2. Read Redis set `credit:expired_pending_leave` (`CREDIT_EXPIRED_PENDING`)
  3. For each member: `CreditService.auto_leave_check(cid, call_py=..., bot=...)`
  4. `DELETE` the pending set
- Auto-leave and Telegram notifications run **after** the deduct transaction, not inside it.
- Job-level `try/except` logs `midnight_credit_deduct failed` and does not re-raise.

### `CreditService.daily_deduct_all()` (authoritative)

1. **Distributed lock**: `acquire_lock("cron:daily_deduct", ttl_ms=120_000)`. If lock not acquired → log and **`return 0`** (no DB work).
2. **DB transaction** (`async_session` + `session.begin()`):
   - `SELECT GroupCredit … WHERE status = 'active' AND credit_days > 0 FOR UPDATE`
   - No `chat_type` filter (groups and channels both eligible).
   - For each row: `credit_days = max(0, credit_days - 1)`; if result is `0` → `status = 'expired'`, collect `chat_id` in `expired_chat_ids`.
   - Returns **`count`** = number of rows processed (not only newly expired).
3. **After commit** (outside transaction):
   - `invalidate_credit(chat_id)` for **every** processed row (failures logged per chat, do not fail the job return value).
   - If any expired: `SADD credit:expired_pending_leave`, `EXPIRE` 86400 on the set key.
4. **`finally`**: `release_lock("cron:daily_deduct", token)`.

### What does *not* happen on nightly deduct

- No `CreditHistory` rows.
- No `charge` / wallet / trial mutations.
- No in-transaction Telegram sends.
- No direct `auto_leave_check` inside `daily_deduct_all` (scheduler handles that).

### Eligibility (SQL, not Python filter)

- Included: `status == "active"` and `credit_days > 0`.
- Excluded: already zero/negative days, `expired` or other non-active status.

---

## 2. Risk areas (why 2D-4B is high-risk)

| Risk | Detail |
|------|--------|
| Double deduction | Multiple app instances; mitigated today by Redis lock for full run |
| Partial commit | Batching commits mid-run could deduct twice on restart if not idempotent |
| Lock hold time | Single txn + `FOR UPDATE` on all active rows blocks concurrent credit ops |
| Redis / cache drift | Invalidation must run for every processed `chat_id` after its batch commits |
| Auto-leave ordering | Pending set must include all chats that hit zero in that run before scheduler reads it |
| Repo dead path | `credit_repo.daily_deduct_all` must not become cron entry by mistake |

---

## 3. What Phase 2D-4A tests protect

File: `tests/test_daily_deduct_behavior_phase2d4a.py`

| Test | Protects |
|------|----------|
| Scheduler uses `CreditService`, not `credit_repo` | Cron wiring |
| Lock skip → `0`, no session | Singleton behavior |
| SQL `active` + `credit_days > 0` + `FOR UPDATE` | Eligibility query shape |
| Decrement by 1, stay active when days > 1 | Deduction math |
| Last day → `expired`, Redis pending, cache invalidate | Expiry side effects |
| Group + channel rows in one run | No accidental type filter |
| No `CreditHistory` / `session.add` | History non-goal |
| Return count = processed rows | API contract |
| Cache invalidate failure still returns count | Post-txn resilience |
| `session.begin()` used | Transaction boundary |
| Scheduler auto-leave after pending set | GAP-2 integration |
| Repo path: commit, no lock/redis | Dead-path documentation |

Existing: `tests/test_concurrency.py::TestDailyDeductSingleton` (integration-style lock + real DB when env available).

---

## 4. Phase 2D-4B batching design (implemented; historical reference)

### Goals

- Reduce transaction duration and row lock footprint at midnight.
- Preserve all 2D-4A behaviors unless product explicitly changes them.

### Candidate approach

1. Keep **global** `cron:daily_deduct` lock for the whole job (or document stricter per-batch idempotency before narrowing lock).
2. Loop batches of **~200** rows:
   - `SELECT … WHERE status='active' AND credit_days>0 ORDER BY id LIMIT 200 FOR UPDATE SKIP LOCKED` (or keyset `id > last_id` — design choice in 4B).
   - Same in-loop mutation: decrement, set `expired` when zero.
   - **Commit per batch**.
   - After each batch: invalidate cache for that batch’s chat IDs; `SADD` expired IDs to `CREDIT_EXPIRED_PENDING`.
3. Return total processed count (sum of batch sizes).
4. Scheduler unchanged: still one `daily_deduct_all()` call, then auto-leave pass.

### Restart / idempotency (design before coding)

- Option A: job-run marker in Redis/DB (`deduct:date:2026-06-05`) so a restarted instance does not re-deduct the same calendar day.
- Option B: only deduct rows with `last_deducted_at < today` (schema change — **out of scope** unless approved).
- Default recommendation: **do not ship 4B** without an explicit idempotency story.

---

## 5. Explicit non-goals (2D-4B unless approved)

- Migrations or new indexes.
- Writing `CreditHistory` on nightly deduct.
- Changing warning job schedules or SQL.
- Changing auto-leave policy or Telegram copy.
- Replacing Redis lock with only DB advisory lock.
- Switching cron to `credit_repo.daily_deduct_all`.

---

## 6. Rollback plan

1. Revert 2D-4B commit(s); 2D-4A tests should fail if behavior regresses.
2. Redeploy previous image; midnight job resumes single-transaction path.
3. Monitor: lock wait, `midnight_credit_deduct` log count, `credit:expired_pending_leave` size, support reports of double-charge.

---

## 7. Tests required before 2D-4B merge

- All `tests/test_daily_deduct_behavior_phase2d4a.py` green.
- `tests/test_concurrency.py::TestDailyDeductSingleton` green (or documented env skip).
- New 4B tests (to add in 4B): multi-batch commit, partial restart simulation, pending set union across batches, total count, no double decrement under lock failure mid-run.
- Regression: `test_gap_closure_batch1.py` auto-leave tests, scheduler credit warning tests.
- Optional disposable DB: row-count / lock-duration sample (read-only metrics), not required for unit merge.

---

## 8. Recommended next phase

**Phase 2D-4B-1 (done)** — `last_daily_deducted_on` + migration `0019_daily_deduct_idem`; legacy and batched paths filter by DB date; batching still default off. See [daily_deduct_idempotency.md](daily_deduct_idempotency.md).

**Phase 2D-4B-2 (done)** — `scripts/validate_daily_deduct_batching.py` disposable harness; run before enabling batching in staging.

**Phase 2D-4B-3 (optional)** — production enablement of batched path after disposable + staging PASS.

**Phase 2D-4B-0 (done)** — feature flag + staging batched path (Redis done-set since replaced by DB column in 4B-1).
