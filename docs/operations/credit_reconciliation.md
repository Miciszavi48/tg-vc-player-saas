# Credit History Reconciliation Plan (Read-Only)

**Date:** 2026-06-05  
**Scope:** Analysis plan; **Phase 2C-1 implemented** in `0018_credit_history_reconcile_orphan.py` (revision `0018_credit_hist_orphan`)  
**Evidence DB:** Local disposable `musicbot_disposable` only (SELECT introspection)  
**Historical migration (2C-1):** `0018_credit_hist_orphan` — orphan `credit_history_partitioned` removed on fresh disposable; drift checker **WARNING** (ORM PK deferred), not CRITICAL
**Project Alembic head (current):** `0039_hot_seat` — see [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md) and [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)<br>
**Deferred:** ORM PK `(id, operated_at)` alignment (Phase 2C-2); monthly partitions on canonical `credit_history` (optional later)

> **Current Status:**
> This document is preserved as operational/historical context (Snapshot: 2026-06-05). It documents the database drift anomaly resolved in migration `0018`.
> For current canonical details, see:
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)

---

## Pre-Audit Checklist

### Git status (`git status --short`)

```
 M .gitignore
 M CHANGELOG.md
 M SERVER_NATIVE_DEPLOYMENT_GUIDE.md
?? DB_SCHEMA_DRIFT_AND_INDEX_VALIDATION_REPORT.md
?? app/config.env.disposable.example
?? app/database/migrations/versions/0017_safe_hot_path_indexes.py
?? scripts/db_schema_drift_check.py
?? scripts/load_disposable_env.ps1
?? tests/test_db_schema_drift_check.py
```

(`app/config.env.disposable` is gitignored — not listed.)

### `app/config.env.disposable`

- **Ignored:** yes — `.gitignore:7:config.env.disposable` → `app/config.env.disposable`

### `app/config.env.disposable.example`

- **Tracked status:** untracked (`??`) — safe to commit as template

### Required files

| File | Exists |
|------|--------|
| `scripts/db_schema_drift_check.py` | Yes |
| `app/database/migrations/versions/0003_credit_history_partition_prep.py` | Yes (revision id `0003_credit_history_partition`) |
| `app/database/migrations/versions/0007_credit_history_partition.py` | Yes |
| `app/database/migrations/versions/0017_safe_hot_path_indexes.py` | Yes |
| `app/database/models.py` | Yes |
| `app/repositories/credit_repo.py` | Yes |

### Audit plan table (executed)

| Step | Command / action | Purpose | Safety | Result |
|------|------------------|---------|--------|--------|
| 1 | `git status`, `git check-ignore` | Repo/env hygiene | Read-only | See above |
| 2 | Read models, migrations, repos, tests | Code facts | No DB | Documented §2 |
| 3 | SELECT on `musicbot_disposable` | Live schema facts | Disposable only, SELECT | Documented §3 |
| 4 | `python scripts/db_schema_drift_check.py` | Drift classification | Read-only | Pre-2C-1: CRITICAL; post-2C-1: WARNING |
| 5 | Write this plan | Next-phase design | No mutations | This file |

---

## 1. Executive Summary

### What is drifting?

After a **fresh** Alembic run on an empty database:

1. **`credit_history`** is a **partitioned** table (`relkind = p`) with composite primary key **`(id, operated_at)`** and a single child partition **`credit_history_default`** (DEFAULT).
2. **`credit_history_partitioned`** is a **second** partitioned table (also `relkind = p`) with composite PK **`(id, operated_at)`** and **five monthly** child partitions (`credit_history_y2026m01` … `y2026m05`) — but **no** default partition attached to it (see §3.5).
3. **Application code** (ORM + repositories + `credit_service`) writes **only** to **`credit_history`** via `CreditHistory` with ORM PK **`id`** only.
4. **`credit_history_partitioned` is never written** by application code; row count stays **0** on fresh DB.
5. Migrations **0003** and **0007** both target partitioning but use **different table names** and both reference a partition named **`credit_history_default`** — only one physical child exists, parented by **`credit_history`** (name collision).

### Why it matters

| Risk | Impact |
|------|--------|
| Dual table design | Operators and tests disagree on which object is “the” partitioned credit log |
| ORM PK vs DB PK | SQLAlchemy model declares `id` only; DB requires partition key in PK for partitioned tables |
| Orphan `credit_history_partitioned` | Wasted objects; misleading “GAP-L1 done” signals; drift checker CRITICAL |
| Insert routing | All ORM inserts hit **`credit_history` → `credit_history_default`** only (no monthly splits on canonical table) |
| Production variance | DBs with rows before **0003** skip partitioning on `credit_history` but still get **0007** copy table — behavior differs from fresh install |
| Missing **0002** indexes on parent | On disposable DB, `idx_credit_history_chat_date` / `idx_credit_history_operator` not present (only **0003** `idx_credit_history_chat` / `idx_credit_history_invoice` on parent) |

### Blocking before production?

| Area | Blocking? |
|------|-----------|
| Bot starts / basic credit add-deduct | **Partially** — inserts may work today via default partition, but PK/ORM mismatch is a latent failure mode |
| Schema drift gate (`db_schema_drift_check.py`) | **After 2C-1:** exit **0** with **WARNING** (ORM PK + partitioned canonical table); **CRITICAL** only if orphan table returns or `credit_history` missing |
| Scale / monthly partition strategy | **Yes** — intended monthly partitions are on the **wrong** table for current code |
| Audit/compliance “partitioned credit_history” | **Yes** — documentation and tests overstate alignment |

**Recommendation (updated post 2C-1):** Orphan-table reconciliation is **done** via `0018`. Before production-scale launch, still run `alembic upgrade head` and the drift checker; treat remaining **WARNING** (ORM PK mismatch, no monthly partitions yet) as follow-up, not a launch blocker.

---

## 2. Current Schema Facts from Code

### 2.1 ORM model (`app/database/models.py`)

```python
class CreditHistory(Base):
    __tablename__ = "credit_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # ... chat_id, operated_at, etc.
```

- **Table:** `credit_history`
- **PK (ORM):** `id` only
- **No** model for `credit_history_partitioned`
- Indexes on `chat_id`, `invoice_id` (non-unique column indexes)

### 2.2 Repository / service writes

| Location | Writes to | Mechanism |
|----------|-----------|-----------|
| `app/repositories/credit_repo.py` | `CreditHistory` → `credit_history` | `add_credit`, `deduct_credit`, trial flows, `add_credit_history` |
| `app/services/credit_service.py` | `CreditHistory` → `credit_history` | Multiple `session.add(CreditHistory(...))` paths |

**No references** to `credit_history_partitioned` in `app/`.

### 2.3 Migrations (chronological)

| Revision | File | Effect on `credit_history` |
|----------|------|----------------------------|
| `fa2e02e5c03e` | `initial_schema.py` | Flat table; `PRIMARY KEY (id)`; `ix_credit_history_chat_id`, `ix_credit_history_invoice_id` |
| `0002_spec_indexes` | `0002_add_spec_indexes.py` | `idx_credit_history_chat_date`, `idx_credit_history_operator` on `credit_history` |
| `0003_credit_history_partition` | `0003_credit_history_partition_prep.py` | If **empty**: replace with partitioned `credit_history`, PK `(id, operated_at)`, child `credit_history_default`, indexes `idx_credit_history_chat`, `idx_credit_history_invoice`. If **rows > 0**: **no-op** |
| `0007_credit_history_partition` | `0007_credit_history_partition.py` | Creates **`credit_history_partitioned`** + monthly partitions + `credit_history_default` (IF NOT EXISTS) + copy from `credit_history` + `ix_chp_*` indexes. Does **not** drop or repoint `credit_history` |

### 2.4 Tests

| Test | Expectation | Conflict |
|------|-------------|----------|
| `tests/test_gap_closures.py::TestCreditHistoryPartition` | `credit_history` has `relkind = 'p'`; children under **`credit_history`** parent | Aligns with **0003** fresh path; ignores **0007** orphan |
| `tests/test_gap_batch2_3.py::test_credit_history_partitioned_exists` | `credit_history_y%` tables exist | Aligns with **0007** monthly partitions on **`credit_history_partitioned`**, not where ORM writes |
| `tests/test_db_schema_drift_check.py` | Unit tests for drift evaluator | Expects CRITICAL when dual tables + partitioned `credit_history` |

### 2.5 Drift checker (`scripts/db_schema_drift_check.py`)

- Flags **CRITICAL** when: `credit_history` is partitioned **and** `credit_history_partitioned` exists (and/or PK mismatch with ORM).
- On fresh disposable DB: **FAIL**, exit code **1** (schema present but credit drift CRITICAL).

---

## 3. Current Schema Facts from Disposable PostgreSQL

**Database:** `musicbot_disposable`  
**Introspection:** SELECT-only (2026-06-05)

### 3.1 Table kinds (`relkind`)

| Object | relkind | Meaning |
|--------|---------|---------|
| `credit_history` | `p` | Partitioned table (parent) |
| `credit_history_partitioned` | `p` | Partitioned table (parent) |
| `credit_history_default` | `r` | Regular table (partition child) |
| `credit_history_y2026m01` … `y2026m05` | `r` | Monthly partition children |

### 3.2 Partition tree

**Parent `credit_history`:**

| Child | Bound |
|-------|-------|
| `credit_history_default` | `DEFAULT` |

**No** `credit_history_y2026m*` children under `credit_history`.

**Parent `credit_history_partitioned`:**

| Child | Bound |
|-------|-------|
| `credit_history_y2026m01` | 2026-01 → 2026-02 |
| `credit_history_y2026m02` | 2026-02 → 2026-03 |
| `credit_history_y2026m03` | 2026-03 → 2026-04 |
| `credit_history_y2026m04` | 2026-04 → 2026-05 |
| `credit_history_y2026m05` | 2026-05 → 2026-06 |

**No** child named `credit_history_default` under `credit_history_partitioned` (see §3.5).

### 3.3 Primary keys

| Table | PK columns |
|-------|------------|
| `credit_history` | `id`, `operated_at` |
| `credit_history_partitioned` | `id`, `operated_at` |
| Partition children | Local PK indexes include `(id, operated_at)` per child |

### 3.4 Indexes (parent-level via `pg_indexes`)

**`credit_history` parent:**

- `credit_history_pkey1` — UNIQUE `(id, operated_at)`
- `idx_credit_history_chat`
- `idx_credit_history_invoice`

**Not observed on disposable DB:** `idx_credit_history_chat_date`, `idx_credit_history_operator` (from **0002**), `ix_credit_history_chat_id` (from initial; may have been replaced).

**`credit_history_partitioned` parent:**

- `credit_history_partitioned_pkey` — UNIQUE `(id, operated_at)`
- `ix_chp_chat_id`, `ix_chp_operated_at`, `ix_chp_invoice_id`

Monthly children carry inherited/per-partition index names (e.g. `credit_history_y2026m01_chat_id_idx`).

### 3.5 Row counts

| Table | Rows |
|-------|------|
| `credit_history` | **0** |
| `credit_history_partitioned` | **0** |

### 3.6 Column definitions

**Identical column sets** on both parents (9 columns): `id`, `chat_id`, `chat_type`, `operation`, `amount_days`, `operated_by`, `operated_at`, `note`, `invoice_id`.

**Difference:** separate sequences — `credit_history_id_seq1` vs `credit_history_partitioned_id_seq`.

### 3.7 Insert routing (inferred, no INSERT performed)

| Writer | Target | Routing on fresh DB |
|--------|--------|---------------------|
| ORM `CreditHistory` | `credit_history` | Rows go to **`credit_history_default`** (only child of canonical parent) |
| Nothing | `credit_history_partitioned` | **No inserts**; monthly partitions remain empty |

**Future dated rows** do not land in `credit_history_y2026m*` unless code is changed to write to `credit_history_partitioned` or monthly partitions are attached to `credit_history`.

### 3.8 Name collision: `credit_history_default`

- **0003** creates `credit_history_default` **PARTITION OF `credit_history`**.
- **0007** runs `CREATE TABLE IF NOT EXISTS credit_history_default PARTITION OF credit_history_partitioned DEFAULT`.
- Because the name already exists, PostgreSQL **does not** create a second partition; **0007’s default partition is skipped**.
- Evidence: `pg_inherits` shows `credit_history_default` → parent **`credit_history`** only.

This is a **root cause** of the split-brain layout.

---

## 4. Conflict Matrix

| Layer | Expected table | Expected PK | Expected partition structure | Actual behavior (fresh Alembic DB) | Conflict / risk |
|-------|----------------|-------------|------------------------------|-------------------------------------|-----------------|
| ORM model | `credit_history` | `id` | Unspecified (flat implied) | DB PK `(id, operated_at)`, partitioned | **High** — metadata drift |
| `credit_repo.py` / `credit_service.py` | `credit_history` | ORM `id` | N/A | Writes to partitioned parent → **default** child only | **Medium** — works until PK/routing edge cases |
| Migration **0003** | `credit_history` | `(id, operated_at)` | Partitioned + `credit_history_default` | Matches on empty DB | OK on fresh empty path |
| Migration **0007** | `credit_history_partitioned` | `(id, operated_at)` | Monthly + default | Orphan parent; default **not** created; copy empty | **Critical** — dead structure |
| `test_gap_closures` | `credit_history` partitioned | N/A | Children of `credit_history` | Passes on fresh DB | **Low** alone; ignores orphan |
| `test_gap_batch2_3` | `credit_history_y%` exist | N/A | Under **`credit_history_partitioned`** | Passes on fresh DB | **High** — wrong table vs ORM |
| Drift checker | Single coherent story | ORM `id` | Optional partition strategy | CRITICAL dual-table | **Gate** — FAIL |
| Production deploy docs | `alembic upgrade head` + bot | Assumes migrations = truth | Spec may describe single partitioned `credit_history` | Fresh vs legacy DB differ | **High** on legacy |

---

## 5. Fix Strategy Options

### Option A — Canonical `credit_history`; remove or detach `credit_history_partitioned` (recommended baseline)

**Idea:** Keep ORM and all writes on **`credit_history`**. Drop or archive **`credit_history_partitioned`** after verifying it is unused. Optionally add monthly partitions to **`credit_history`** (not a second table). Align model PK with `(id, operated_at)` or de-partition if scale not needed yet.

| Criterion | Assessment |
|-----------|------------|
| Files affected | New migration `0018+`, `models.py`, possibly `tests/test_gap_*`, drift checker notes, docs |
| Migration approach | New revision: preflight counts; `DROP TABLE credit_history_partitioned CASCADE` if empty; optionally `CREATE TABLE credit_history_y2026mNN PARTITION OF credit_history` with unique names; fix default partition naming (`credit_history_default` already on canonical table) |
| Data migration risk | **Low** if `credit_history_partitioned` row count = 0. **High** if > 0 — must merge into `credit_history` first |
| Test changes | Unify tests on `credit_history` children; remove/replace `test_credit_history_partitioned_exists` |
| Runtime risk | **Low** — no change to write target |
| Rollback complexity | Medium — must restore dropped table from backup |
| Production safety | **High** when preceded by row-count report and staging rehearsal |

### Option B — Move ORM/code to `credit_history_partitioned`

**Idea:** Change `CreditHistory.__tablename__` and all SQL to `credit_history_partitioned`; drop or demote old `credit_history`.

| Criterion | Assessment |
|-----------|------------|
| Files affected | `models.py`, `credit_repo.py`, `credit_service.py`, any raw SQL, all tests, new migration to swap/rename tables |
| Migration approach | Rename tables or copy data; repoint sequences; ensure default partition exists on partitioned table |
| Data migration risk | **High** — must move all rows from `credit_history` (and default child) into partitioned tree |
| Test changes | Broad |
| Runtime risk | **High** — changes every write path |
| Rollback complexity | **High** |
| Production safety | **Low** — not recommended without strong justification |

### Option C — Remove partitioning; flat `credit_history`

**Idea:** One flat table, PK `id`, matches ORM today. Downgrade partition migrations’ effect via new migration (copy from partitions to flat, drop partition trees).

| Criterion | Assessment |
|-----------|------------|
| Files affected | New migration, `models.py` (unchanged PK), tests expecting `relkind=p` removed |
| Migration approach | `CREATE TABLE credit_history_new AS SELECT ...`; swap names; drop partitioned objects |
| Data migration risk | Medium on DBs with data spread across partitions |
| Test changes | Remove partition relkind tests |
| Runtime risk | **Low** |
| Rollback complexity | Medium |
| Production safety | **High** for simplicity; **loses** partition scale path |

### Comparison summary

| Option | ORM churn | Data risk | Aligns with current writes | Keeps scale path |
|--------|----------|-----------|----------------------------|------------------|
| A | Low–medium | Low (if orphan empty) | **Yes** | Optional |
| B | **High** | **High** | After big bang | Yes |
| C | Low | Medium | Yes | No |

---

## 6. Recommended Strategy

**Choose Option A** with a phased sub-path:

1. **Phase 2C-1 (immediate):** Remove orphan **`credit_history_partitioned`** when `COUNT(*) = 0` (true on fresh disposable; verify on staging/production).
2. **Phase 2C-2:** Update **`CreditHistory` model** to composite PK `(id, operated_at)` **or** document SQLAlchemy `PrimaryKeyConstraint` in `__table_args__` matching DB (required for partitioned parent correctness).
3. **Phase 2C-3 (optional):** Attach **monthly partitions to `credit_history`** with non-colliding names (e.g. `credit_history_p_y2026m01`), plus partition maintenance job; do **not** reuse failed `credit_history_default` pattern on second parent.
4. **Defer Option C** unless product decides partitioning is unnecessary for 12+ months.

**Option B** is rejected: writes already target `credit_history`.  
**Option C** remains a fallback if partition complexity is not worth operational cost.

---

## 7. Future Migration Design (Not Implemented)

**Proposed revision:** `0018_credit_history_reconcile` (title TBD)

### 7.1 Preflight checks (run in migration or admin script)

```sql
-- Read-only pattern (run before upgrade transaction)
SELECT COUNT(*) FROM credit_history;
SELECT COUNT(*) FROM credit_history_partitioned;
SELECT relkind::text FROM pg_class WHERE relname IN ('credit_history','credit_history_partitioned');
SELECT c.relname, p.relname AS parent
FROM pg_inherits i
JOIN pg_class c ON c.oid = i.inhrelid
JOIN pg_class p ON p.oid = i.inhparent
WHERE c.relname LIKE 'credit_history%';
```

**Abort upgrade if:**

- `credit_history_partitioned` has rows **and** no approved merge plan
- Production URL / database name not in allowlist
- `musicbot_dev` or production DB name detected

### 7.2 Backup requirement

- Full logical backup (`pg_dump`) or snapshot before migration.
- Record row counts and partition list in change ticket.

### 7.3 If `credit_history_partitioned` is empty (disposable-proven case)

1. `DROP TABLE IF EXISTS credit_history_partitioned CASCADE;`
2. Verify no remaining `credit_history_y2026m*` orphans (drops cascade with parent).
3. Leave **`credit_history`** partitioned parent + **`credit_history_default`** as canonical write path **or** proceed to 7.5.

### 7.4 If `credit_history_partitioned` contains rows

1. `INSERT INTO credit_history (...) SELECT ... FROM credit_history_partitioned ON CONFLICT DO NOTHING;` (define conflict target on `(id, operated_at)`).
2. Verify counts match.
3. Then `DROP TABLE credit_history_partitioned CASCADE;`

### 7.5 Orphan table handling

- **Drop** (preferred when empty): removes confusion and fixes drift checker.
- **Rename** to `credit_history_partitioned_deprecated` only if temporary audit needed — still requires checker update.

### 7.6 Keep partitioned `credit_history`?

| Path | Action |
|------|--------|
| Keep partition scale | Add monthly children **OF `credit_history`** with unique names; add job for future months |
| Simplify | Later migration: flatten per Option C |

### 7.7 Composite PK vs ORM PK

- Update model:

```python
__table_args__ = (
    PrimaryKeyConstraint("id", "operated_at"),
)
```

- Ensure `id` remains `SERIAL` / identity on parent with partition key included in PK (PostgreSQL requirement).
- Review SQLAlchemy INSERT behavior in tests after change.

### 7.8 Model / tests / checker changes

| Artifact | Change |
|----------|--------|
| `models.py` | Composite PK; optional `__table_args__` for partition awareness |
| `test_gap_closures.py` | Keep `relkind=p` only if still partitioned |
| `test_gap_batch2_3.py` | Assert monthly partitions under **`credit_history`**, not `credit_history_partitioned` |
| `db_schema_drift_check.py` | After reconcile: expect **OK** or **WARNING** (if only single parent + planned partitions) |

### 7.9 Downgrade plan

- Downgrade of `0018` should `CREATE TABLE credit_history_partitioned` only if rollback script restores from backup — **do not** rely on naive downgrade for production.
- Document: **restore from pg_dump** is the real rollback.

### 7.10 Do not edit historical migrations

- Leave **0003** / **0007** unchanged.
- Correct state only via **new** forward migration(s).

---

## 8. Future Test Plan

| Test | Purpose |
|------|---------|
| `tests/test_credit_history_migration_disposable.py` | On disposable PG: after `0018`, `credit_history_partitioned` absent; `credit_history` exists |
| `tests/test_credit_history_orm_insert.py` | Async insert `CreditHistory` into disposable DB; row appears in `credit_history_default` or correct monthly child |
| `tests/test_credit_repo_history_write.py` | `add_credit` / `deduct_credit` create history rows on canonical table |
| Update `test_gap_closures.py` | Assert partition children parent = `credit_history` |
| Replace `test_gap_batch2_3.py::test_credit_history_partitioned_exists` | Query `credit_history_y%` **inhparent** = `credit_history` OR drop if monthly not yet implemented |
| `tests/test_db_schema_drift_check.py` | Add integration case: reconciled disposable DB → `drift_level` not CRITICAL |
| CI optional job | `alembic upgrade head` on empty disposable + drift checker exit 0 |

---

## 9. Safety Checklist Before Implementation

- [ ] Full DB backup (production/staging) with verified restore drill
- [ ] Run reconciliation on **`musicbot_disposable`** first
- [ ] Run on **staging** second with real row counts
- [ ] **Never** run on production without written row-count report
- [ ] `SELECT COUNT(*) FROM credit_history_partitioned;`
- [ ] `SELECT COUNT(*) FROM credit_history;`
- [ ] `SELECT relkind FROM pg_class WHERE relname = 'credit_history';`
- [ ] Confirm **no** code path references `credit_history_partitioned`
- [ ] `python scripts/db_schema_drift_check.py` **before** and **after**
- [ ] Do **not** touch `musicbot_dev` unless explicitly approved
- [ ] Do not reset unrelated role passwords
- [ ] Schedule maintenance window if production `credit_history` has > 0 rows and **0003** was skipped (flat table case)

---

## 10. Final Recommendation

| Item | Decision |
|------|----------|
| **Recommended option** | **Option A** — canonical `credit_history`, drop empty orphan `credit_history_partitioned`, align model PK, optionally add monthly partitions to canonical parent later |
| **Implement now?** | After **one human review** of this plan and staging row counts — do not implement in same session as index migration without review |
| **Next phase title** | **Phase 2C: `credit_history` reconciliation migration** |
| **Files likely to change** | `app/database/models.py`, new `app/database/migrations/versions/0018_*.py`, `tests/test_gap_closures.py`, `tests/test_gap_batch2_3.py`, `scripts/db_schema_drift_check.py` (expectations), `CHANGELOG.md`, docs |
| **Tests to run** | Disposable: `alembic upgrade head`, ORM insert test, `pytest tests/test_db_schema_drift_check.py`, full `pytest` credit-related modules, `python scripts/db_schema_drift_check.py` |

---

## Appendix: Commands Run (This Audit)

| Command | Result |
|---------|--------|
| `git status --short` | See § Pre-Audit |
| `git check-ignore -v app/config.env.disposable` | Ignored |
| `python -m py_compile scripts/db_schema_drift_check.py app/database/models.py app/repositories/credit_repo.py` | OK |
| SELECT introspection on `musicbot_disposable` via Python/asyncpg | See §3 |
| `python scripts/db_schema_drift_check.py` (loads `config.env.disposable`) | FAIL, credit_history CRITICAL |

**Not run:** Alembic upgrade/downgrade, DDL/DML, bot, `setup_server.sh`, production/`musicbot_dev` connections.

---

## Appendix: Legacy Production Variants

| Install path | Likely `credit_history` state | `credit_history_partitioned` |
|------------|------------------------------|------------------------------|
| Fresh Alembic (empty at 0003) | Partitioned + default child | Orphan + monthly (empty) |
| Legacy with data before 0003 | **Flat**, PK `id` | Copy from flat at 0007; may diverge over time |
| `create_all()` only (tests) | Flat, no partitions | Absent |

Reconciliation migration must branch on **preflight** relkind and row counts.
