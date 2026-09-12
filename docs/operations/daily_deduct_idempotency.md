# Daily deduction idempotency — Phase 2D-4B decision

> **Current Status:**
> This document is preserved as operational/historical context detailing the idempotency logic added in migration `0019`.
> For current canonical details on the credit subsystem, see:
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)

## 1. Current decision (2D-4B-1)

**Production-grade idempotency is DB-backed via `group_credits.last_daily_deducted_on` (PostgreSQL `DATE`, nullable).**

| Path | Idempotency | Default |
|------|-------------|---------|
| Legacy (`_daily_deduct_legacy`) | SQL: `last_daily_deducted_on IS NULL OR < today` | **Yes** (`DAILY_DEDUCT_BATCHING_ENABLED=false`) |
| Batched (`_daily_deduct_batched`) | Same SQL filter + stamp on process | Staging only (flag `true`) |

**Date rule:** `CreditService._daily_deduct_today()` uses `date.today()` (server local calendar date), aligned with APScheduler cron `hour=0, minute=0` on the app host. No separate timezone config in this phase.

**Second run same day:** Eligible query returns 0 rows → `daily_deduct_all()` returns `0`; no extra decrement; no duplicate `credit:expired_pending_leave` entries for chats already stamped today.

**Batching:** Still **disabled by default**. Batched path is safe for same-day reruns after 2D-4B-1; production enablement of batching still requires ops/staging validation (shorter transactions, partial-failure behavior).

**Redis `cron:daily_deduct:done:{date}`:** Removed from batched path (2D-4B-1); DB column is authoritative. Helper `daily_deduct_done_key()` may remain unused for now.

## 2. Migration (0019)

- **File:** `0019_group_credit_daily_deduct_idempotency.py`
- **Revision:** `0019_daily_deduct_idem`
- **Column:** `last_daily_deducted_on DATE NULL` — no backfill
- **Index:** `idx_group_credits_daily_deduct_due` partial on `(last_daily_deducted_on, id) WHERE status='active' AND credit_days>0` (PostgreSQL `CONCURRENTLY`)

## 3. Why Redis lock alone is not enough

The lock prevents concurrent instances during one run. It does **not** prevent double deduction after a mid-run crash + later rerun the same calendar day (without `last_daily_deducted_on`).

## 4. Historical: 2D-4B-0 (staging)

- Added disabled-by-default batched path with Redis done-set (superseded for idempotency by 2D-4B-1).
- See git history / changelog for 2D-4B-0 details.

## 5. Production safety

1. Run `alembic upgrade head` (current head `0033_instance_database_ownership`; idempotency column added in `0019_daily_deduct_idem`) before deploy.
2. Keep `DAILY_DEDUCT_BATCHING_ENABLED=false` unless ops approves batched path.
3. Manual re-run of midnight job same day should process **0** rows after a successful run.
4. `scripts/db_schema_drift_check.py` expects column + index.

## 6. Tests before enabling production batching

- `tests/test_daily_deduct_behavior_phase2d4a.py`
- `tests/test_daily_deduct_batching_phase2d4b.py`
- `tests/test_daily_deduct_idempotency_phase2d4b1.py`
- `tests/test_concurrency.py::TestDailyDeductSingleton` (when Redis/DB available)
- Disposable DB: `alembic upgrade head` + drift checker exit 0

## 7. Disposable validation harness (2D-4B-2)

Before enabling `DAILY_DEDUCT_BATCHING_ENABLED=true` in staging or production:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://musicbot:devpassword@127.0.0.1:5432/musicbot_disposable"
cd app; alembic upgrade head; alembic current; cd ..
python scripts/db_schema_drift_check.py
python scripts/validate_daily_deduct_batching.py
```

Script: `scripts/validate_daily_deduct_batching.py` — mutates only `chat_id` in `[-990000000100, -990000000000]`.

Production enablement checklist:

1. `alembic upgrade head` (current head `0033_instance_database_ownership`; idempotency from `0019_daily_deduct_idem`)
2. Drift checker exit 0
3. Disposable validation **PASS**
4. Staging validation **PASS** with batching enabled
5. Manual ops approval

Never set `DAILY_DEDUCT_BATCHING_ENABLED=true` in production without staging proof.

## 8. Recommended next phase

**2D-4B-3 (optional):** Enable batched path in production after disposable + staging PASS. **2D-5+:** Other audit items (warning job merge, etc.).
