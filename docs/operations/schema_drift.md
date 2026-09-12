# DB Schema Drift and Index Validation Report

> Last verified against repository: 2026-07-19

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)

> **Historical audit (2026-06-05).** This report documents the `0017`/`0018` migration review. **Current project Alembic head:** `0033_instance_database_ownership`. For operator steps today, see § **Current operator workflow** below and [server_native_deployment.md](../deployment/server_native_deployment.md). Production startup gates on `schema_readiness.ensure_database_schema_ready()` — `create_all()` is not the primary PostgreSQL path.

**Date:** 2026-06-05  
**Scope:** Read-only schema/index audit (original focus: `0017`; 2C-1: `0018`)  
**Migrations audited:** `0017_safe_hot_path_indexes.py`, `0018_credit_history_reconcile_orphan.py` (revision `0018_credit_hist_orphan`)

## Current operator workflow (2026-06-26)

1. From `app/`: `alembic upgrade head` → expect **`0033_instance_database_ownership`**
2. From repo root: `python scripts/db_schema_drift_check.py` — exit code **0** required before production start
3. Optional disposable validation: `app/config.env.disposable.example` + `scripts/run_disposable_alembic_check.py`
4. `scripts/db_schema_drift_check.py` discovers Alembic heads dynamically (not hardcoded to `0019`)

Critical post-0019 schema (verify via drift checker, not this historical report): `0020` default media type, `0021` now-playing flags, `0022` owner text links, `0023` helper identity profiles, `0024` call security settings, `0025` group member memberships, `0026` call report helper index, `0027` player deputy/VIP expiry, `0028` invoices, `0029` start customization, `0030`/`0031` YouTube sessions, `0032` Fast-Creat tokens, and `0033` instance ownership.

---

## 1. Executive Summary

Migration `0017_safe_hot_path_indexes` is **structurally sound** for PostgreSQL Alembic: correct revision chain, dialect guard, `autocommit_block()` for `CREATE/DROP INDEX CONCURRENTLY`, idempotent `IF NOT EXISTS`, and safe no-op on non-PostgreSQL dialects. **No runtime behavior change** is expected; only optional query-plan improvements on PostgreSQL after `alembic upgrade head`.

**Top risks (unchanged by 0017):**

| Priority | Issue | Impact |
|----------|--------|--------|
| **Critical (historical)** | At audit time, production could rely on `create_all()` | **Superseded:** `schema_readiness` now requires Alembic at head on production PostgreSQL before seeding. Still run `alembic upgrade head` explicitly on deploy. |
| **Critical (pre-2C-1)** | Dual `credit_history` + `credit_history_partitioned` after `0007` | **Mitigated by `0018`** on Alembic-fresh DBs; remaining: ORM PK mismatch (checker **WARNING**, deferred) |
| **High** | `helper_accounts.session_fingerprint`: migration-only partial **unique** index; model has non-unique `index=True` only | `create_all()` / SQLite tests do not enforce duplicate-session protection |
| **Medium** | `test_gap_closures` PG integration needs disposable URL; `test_gap_batch2_3` now guards app code (no orphan table refs) | Unrelated env failures possible without Redis/Telegram |
| **Low** | `0017` indexes absent from `models.py` | Expected for migration-only hot-path indexes; harmless if Alembic applied |

**0017 verdict:** Safe to apply on PostgreSQL production **after** normal Alembic workflow. Not represented in models/tests by design.

**Disposable PostgreSQL validation (2026-06-05):** Fresh `musicbot_disposable` → `alembic upgrade head` → `0018_credit_hist_orphan`; drift checker **exit 0**, `credit_history` **WARNING** (not CRITICAL). Uses `app/config.env.disposable` (gitignored).

**Automated follow-up:** Run the read-only checker `scripts/db_schema_drift_check.py` against a disposable or dev database after `alembic upgrade head` (see [server_native_deployment.md](../deployment/server_native_deployment.md)).

**Phase 2C-1 (2026-06-05):** Migration file `0018_credit_history_reconcile_orphan.py` (revision `0018_credit_hist_orphan`) drops empty orphan `credit_history_partitioned`. Drift checker treats canonical partitioned `credit_history` alone as WARNING (ORM PK deferred), not CRITICAL.

---

## 2. `0017_safe_hot_path_indexes` Migration Verification

### 2.1 Structural checklist

| Check | Result |
|-------|--------|
| `revision` | `0017_safe_hot_path_indexes` |
| `down_revision` | `0016_global_bans` (single head confirmed via `alembic heads`) |
| Imports | `from alembic import op` only — sufficient |
| PostgreSQL guard | `_is_postgresql()` → early `return` on upgrade/downgrade |
| `autocommit_block()` | Used for both upgrade and downgrade — **required** for `CONCURRENTLY` |
| Upgrade DDL | `CREATE INDEX CONCURRENTLY IF NOT EXISTS` × 8 |
| Downgrade DDL | `DROP INDEX CONCURRENTLY IF EXISTS` in **reversed** `_INDEX_NAMES` order |
| SQLite / test | Skipped entirely — tests use `create_all()`; no `CONCURRENTLY`/partial-index failure |
| `py_compile` | Passes |

### 2.2 Index inventory (per-index verdict)

| Index name | Table | Columns | Predicate | Query helped | Overlapping indexes | Risk | Verdict |
|------------|-------|---------|-----------|--------------|---------------------|------|---------|
| `idx_broadcasts_pending_run_at` | `broadcasts` | `run_at` | `status = 'pending' AND run_at IS NOT NULL` | `broadcast_repo.get_pending_scheduled()` — `status == 'pending'`, `run_at IS NOT NULL`, `ORDER BY run_at` | `ix_broadcasts_status`, `ix_broadcasts_created_at` (0004) | Low | **Keep** |
| `idx_blacklist_active_entity` | `blacklist` | `entity_type`, `entity_id` | `is_active IS TRUE` | `blacklist_repo.is_blacklisted()`, `get_blacklist()` | `ix_blacklist_entity_id` (initial) | Low | **Keep** |
| `idx_group_credits_active_trial_expire` | `group_credits` | `trial_expire_at` | `is_trial IS TRUE AND status = 'active' AND trial_expire_at IS NOT NULL` | `scheduler.check_trial_expiry()` | `ix_group_credits_chat_id`, `idx_group_credits_*` (0010) — different shape | Low | **Keep** |
| `idx_fjc_active_position` | `force_join_channels` | `position`, `id` | `is_active IS TRUE` | `force_join_repo.get_active_targets*()` — `is_active`, `ORDER BY position` | `ix_fjc_position` (0004); model `index=True` on `position` | Low | **Keep** |
| `idx_global_bans_active_created` | `global_bans` | `created_at DESC`, `id` | `is_active IS TRUE` | `global_ban_repo.list_global_bans()` — active filter + `ORDER BY created_at DESC` | `ix_global_bans_user_id` UNIQUE (0016) | Low | **Keep** |
| `idx_install_logs_chat_action_time` | `install_logs` | `chat_id`, `action`, `occurred_at DESC` | none | `CreditService.auto_leave_check()` — `chat_id`, `action == 'install'`, `ORDER BY occurred_at DESC` | `idx_install_logs_chat`, `idx_install_logs_date`, `idx_install_logs_sudo_date` (0002) | Low | **Keep** (more selective than chat-only) |
| `idx_owner_sales_owner_created` | `owner_sales` | `owner_user_id`, `created_at DESC` | none | `owner_panel.own_sales_report()` | `ix_owner_sales_owner_user_id` (model `index=True`) | Low | **Keep** |
| `idx_users_not_banned_last_seen` | `users` | `last_seen DESC`, `user_id` | `is_banned IS FALSE` | `BroadcastServiceV2._get_recipients()` — `is_banned == False`, optional `last_seen >= cutoff` (no `ORDER BY`) | `ix_users_user_id` UNIQUE | Low | **Keep** (questionable if future adds `ORDER BY last_seen`; still helps filter) |

**Name conflicts:** Grep across `app/database/migrations/versions/*.py` shows **no pre-existing index** with any `0017` name.

**Redundancy:** Partial/composite indexes supersede weaker btree indexes for the listed queries; no harmful duplicate unique constraints.

**Boolean / string predicates:** Match schema (`Boolean` NOT NULL, `status` default `'active'`, `is_active` default `True`). PostgreSQL `IS TRUE` / `IS FALSE` syntax is valid.

**Non-PostgreSQL:** Upgrade/downgrade return immediately — **safe** for SQLite test paths that never run this revision’s DDL.

---

## 3. Alembic Revision Chain

### 3.1 Linear chain (base → head)

```
<base>
  → fa2e02e5c03e (initial_schema)
  → 0002_spec_indexes
  → 0003_credit_history_partition
  → 0004_fm_broadcast
  → 0005_analytics_hourly
  → 0006_helper_extensions
  → 0007_credit_history_partition
  → 0008_broadcast_scheduling
  → 0009_helper_fingerprint_proxy
  → 0010_admin_report_indexes
  → 0011_playback_state_source_text
  → 0012_helper_session_fingerprint
  → 0013_media_events
  → 0014_sudo_permissions
  → 0015_admin_titles
  → 0016_global_bans
  → 0017_safe_hot_path_indexes
  → 0018_credit_hist_orphan (head)
```

- **Missing / duplicate revisions:** None detected (`alembic heads` → single head).
- **Suspicious `down_revision`:** None; chain is consistent.
- **Filename note:** `0003_credit_history_partition_prep.py` uses revision id `0003_credit_history_partition` (docstring says same).

### 3.2 Dialect-specific migrations

| Migration | PostgreSQL-only behavior | Risk on SQLite `alembic upgrade` |
|-----------|--------------------------|----------------------------------|
| `0003_credit_history_partition` | `PARTITION BY RANGE`, `pg_class` in downgrade | Would fail if Alembic run on SQLite |
| `0007_credit_history_partition` | Partitioned table DDL | Would fail on SQLite |
| `0012_helper_session_fingerprint` | `postgresql_where` partial unique | Partial unique skipped/degraded on SQLite |
| `0017_safe_hot_path_indexes` | Guarded skip | Safe no-op |

### 3.3 Migrations that may fail or behave oddly on non-empty production DB

| Migration | Behavior |
|-----------|----------|
| `0003` | **No-op** if `credit_history` row count > 0 (NOTICE only). Leaves flat table. |
| `0007` | Always creates `credit_history_partitioned` + copies from `credit_history`; does **not** drop/rename live table. Re-run safe via `IF NOT EXISTS` but leaves dual-table drift. |
| `0017` | `IF NOT EXISTS` + `CONCURRENTLY` — safe online; requires PostgreSQL. |

### 3.4 Schema in migrations not in `models.py`

- After full chain through **`0018`**, orphan `credit_history_partitioned` and `credit_history_y2026m*` are **not** present; canonical `credit_history` + `credit_history_default` remain.
- All `0017` partial indexes.
- `idx_group_credits_status_type_days_id`, `idx_group_credits_type_days_id` (0010).
- Partial unique `uq_helper_accounts_session_fingerprint` (0012).
- Many `0002` named indexes (spec §7) not declared in `__table_args__`.

### 3.5 Model fields / features not fully in migrations

- `ChatSettings`: no `repeat_mode` column, but `main.py` references `getattr(cs, "repeat_mode", False)` — application-level drift.
- `CreditHistory` ORM: single-column PK `id`; partitioned DB may use `PRIMARY KEY (id, operated_at)` after `0003` on empty installs.

---

## 4. `credit_history` Partition Drift (Audit Only)

### 4.1 What each layer says

| Layer | `credit_history` | `credit_history_partitioned` |
|-------|------------------|------------------------------|
| **`models.py`** | `CreditHistory.__tablename__ = "credit_history"`; PK = `id` only | **Not modeled** |
| **Initial (`fa2e02e5c03e`)** | Regular heap table; PK `id`; indexes on `chat_id`, `invoice_id` | N/A |
| **`0003`** | On **empty** DB: replace with **partitioned** `credit_history` by `operated_at` + default partition. On **non-empty**: skip. | N/A |
| **`0007`** | Unchanged; `INSERT ... SELECT` **from** `credit_history` | Creates **separate** partitioned table + monthly partitions + indexes `ix_chp_*` |
| **Repositories** | All writes via `CreditHistory` → **`credit_history`** only | **Never written** by app code |
| **Tests** | `test_gap_closures`: `relkind = 'p'` for **`credit_history`** | `test_gap_batch2_3`: `credit_history_y%` tables (children of **`credit_history_partitioned`**) |

### 4.2 Runtime write path

**Confirmed:** `app/repositories/credit_repo.py` inserts `CreditHistory` rows in `add_credit`, `deduct_credit`, trial flows, and `add_credit_history`. Target table is always **`credit_history`**.

No code references `credit_history_partitioned`.

### 4.3 Scenario matrix

| Scenario | Likely DB state | ORM behavior | Risk |
|----------|-----------------|--------------|------|
| Fresh DB, **Alembic only**, empty at `0003` | `credit_history` partitioned (`0003`); `0007` adds empty **`credit_history_partitioned`** + copies | Writes go to partitioned **`credit_history`**; orphan partitioned copy stale | Medium — duplicate partition design, wasted storage |
| Fresh DB, **`create_all()` only** | Flat `credit_history`; no partitions; no `credit_history_partitioned` | Works with ORM PK | Tests expecting partitions **fail** |
| Old DB with data before `0003` | `0003` skipped → flat `credit_history`; `0007` still creates **`credit_history_partitioned`** with copy | Writes to flat **`credit_history`** only; copy diverges over time | **High** — dual tables, misleading “partitioned” docs |
| Production after all migrations | Depends on whether `0003` ran partition step; **`0007` always adds second structure** | Continuous writes to **`credit_history`** only | **High** before production unless reconciled |

### 4.4 Affected files / functions

- `app/repositories/credit_repo.py` — all `CreditHistory` inserts
- `app/database/models.py` — `CreditHistory`
- `app/database/migrations/versions/0003_credit_history_partition_prep.py`
- `app/database/migrations/versions/0007_credit_history_partition.py`
- `tests/test_gap_closures.py` — `TestCreditHistoryPartition`
- `tests/test_gap_batch2_3.py` — `test_credit_history_partitioned_exists`

### 4.5 Fix before production?

**Phase 2C-1 (orphan drop):** Done via `0018` on disposable. **Still deferred:** ORM PK alignment (2C-2), optional monthly partitions on canonical `credit_history`, and `create_all()` vs Alembic startup safety (Phase 2D/3).

### 4.6 Safest future fix strategy (do not implement here)

1. Pick **one** canonical table name (`credit_history` recommended — matches ORM).
2. Migration phase: backfill → attach/swap partitions OR drop orphan `credit_history_partitioned` after verified empty/unused.
3. Align ORM PK with partition key if keeping range partition (`id`, `operated_at`).
4. Update tests to assert one partition tree.
5. Document monthly partition creation job (spec mentions future months beyond 2026-05 in `0007`).

---

## 5. `Base.metadata.create_all()` Drift Risks

### 5.1 Call sites

| Location | When |
|----------|------|
| `app/database/engine.py::init_db()` | **Every production bot start** (`app/main.py` line 116) |
| `tests/conftest.py` | `TEST_MODE` SQLite bootstrap + `db_engine` fixture |
| `tests/test_streaming.py`, `test_batch1.py`, `test_batch4.py`, `test_credit_service.py`, `test_concurrency.py`, `test_ui_audit.py`, `integration/test_install_flow_trial_credit.py` | Ad hoc test DB setup |

### 5.2 Production vs tests

- **Production:** `main.py` → `init_db()` → `create_all()` + seed `bot_settings` / `install_policy_settings` with `ON CONFLICT DO NOTHING`.
- **Deploy guide:** Recommends `alembic upgrade head` **separately** ([server_native_deployment.md](../deployment/server_native_deployment.md)); runtime does **not** run Alembic.
- **`create_all(checkfirst=True)`** (default): skips existing tables but **does not add** new columns/indexes from later migrations.

### 5.3 Alembic-only features lost with `create_all()` alone

- Partial indexes (`0017`, `0012` partial unique)
- `global_bans`, `media_events`, broadcast scheduling columns (if table existed pre-migration)
- `credit_history_partitioned` tree
- `0002` / `0010` performance indexes
- Column adds from `0006`–`0015` on DBs created from stale model snapshots

### 5.4 Can this hide missing migrations?

**Yes.** If operators rely on bot startup only, DB may look “healthy” while missing columns/indexes/tables until a code path touches missing schema.

### 5.5 Safest future strategy (recommendation)

1. **Production:** Alembic-only schema; remove or gate `create_all()` behind `DEV_SKIP_ALEMBIC` / first-boot flag.
2. **Tests:** Keep `create_all()` for SQLite speed **or** run Alembic against disposable PostgreSQL in CI.
3. **Add schema drift test:** Compare `Base.metadata` reflection vs `alembic upgrade head` on disposable PG (tables, columns, critical indexes).

---

## 6. Model vs Migration Drift Table

| Table | Model-declared indexes/constraints | Migration-declared (not in model) | Drift type | Runtime risk | Future fix |
|-------|-----------------------------------|-----------------------------------|------------|--------------|------------|
| `helper_accounts.session_fingerprint` | `index=True` (non-unique) | `uq_helper_accounts_session_fingerprint` partial **UNIQUE** (0012) | Migration-only unique | Duplicate fingerprints possible without Alembic | Add `UniqueConstraint(..., postgresql_where=...)` to model or document Alembic-only |
| `credit_history` | `index` on `chat_id`, `invoice_id` | Partitioned PK `(id, operated_at)` (0003 empty); `idx_credit_history_*` (0002); separate `credit_history_partitioned` (0007) | Table/PK/partition | Scale + test confusion | Unify partition story (§4) |
| `force_join_channels.position` | `index=True` → `ix_force_join_channels_position` | `ix_fjc_position` (0004); `idx_fjc_active_position` partial (0017) | Name + partial index | Low if Alembic applied | Optional align index names in model `__table_args__` |
| Role tables (`music_admins`, `video_admins`, `player_owners`, `player_vips`) | `UniqueConstraint(chat_id, user_id)` + column indexes | `idx_*_chat`, `idx_*_user_chat` (0002) | Extra indexes migration-only | Low | Optional declare in models |
| `blacklist` | `index` on `entity_id` | `idx_blacklist_active_entity` partial (0017) | Partial index migration-only | Low without 0017 | Apply 0017 in prod |
| `group_credits` | `uq_group_credits_chat` | `idx_group_credits_*` (0010), `idx_group_credits_active_trial_expire` (0017), many 0002 | Extra indexes | Trial scheduler scan cost without 0017 | Apply migrations |
| `chat_settings` | `uq_chat_settings_chat`, `index` on `chat_id` | Matches initial + 0004 flags | `repeat_mode` in code only | Feature silently off | Add column or remove code reference |
| `broadcasts` | Column indexes via migration table only in ORM as plain columns | `ix_broadcasts_*` (0004), `idx_broadcasts_pending_run_at` (0017) | Migration-only | Scheduled broadcast restore slower | Apply 0017 |
| `media_events` | `Index` composites in `__table_args__` | Matches 0013 closely | Aligned | Low | — |
| `analytics_hourly` | `UniqueConstraint` + 3 `Index` in model | Same names in 0005 | Aligned | Low | — |
| `global_bans` | `unique` + `index` on `user_id` | `idx_global_bans_active_created` (0017) | Partial list index migration-only | Paginated ban list slower | Apply 0017 |

---

## 7. Repository / Schema Assumptions

| File / function | Assumption | Schema guaranteed by Alembic? | `create_all()`? | Risk |
|-----------------|------------|------------------------------|-----------------|------|
| `credit_repo.py` — all `CreditHistory` writes | Table `credit_history` exists, insertable with ORM PK | Yes (flat or partitioned) | Yes (flat only) | **High** partition/PK mismatch on empty+0003 PG |
| `credit_repo.py` — `GroupCredit` locks | `group_credits` + `uq_group_credits_chat` | Yes | Yes | Low |
| `broadcast_repo.get_pending_scheduled` | `broadcasts.run_at`, `status` | After 0008 | No `run_at` if never migrated | **High** without 0008 |
| `blacklist_repo.is_blacklisted` | `blacklist` + `is_active` | Yes | Yes | Low (perf without 0017) |
| `force_join_repo.get_active_targets*` | `position`, `is_active` | After 0004 | Partial columns if stale | Medium |
| `global_ban_repo.list_global_bans` | `global_bans` table | After 0016 | **No table** | **High** without 0016 |
| `settings_repo.get_chat_settings` | `chat_settings` | Yes | Yes | Low |
| `playlist_repo.add_to_queue` | `playlists`, `idx_playlists_chat_position` helpful | 0002 | Only ORM indexes | Medium concurrency (known) |
| `helper_event_repo` / analytics | `helper_events`, `analytics_hourly` | After 0005/0006 | Missing tables if no Alembic | Medium |
| `admin_report_repo` credit reports | `idx_group_credits_status_type_days_id` | 0010 | No | Medium perf |
| `helper_pool_service` fingerprint checks | Partial **unique** on `session_fingerprint` | 0012 | **No** — only non-unique | **High** duplicate sessions in test/SQLite |
| `owner_panel.own_sales_report` | `owner_sales` | Initial | Yes | Low (perf 0017) |
| `BroadcastServiceV2._get_recipients` | `users.is_banned`, `last_seen` | Yes | Yes | Low (perf 0017) |
| `CreditService.auto_leave_check` | `install_logs` with `action`, `occurred_at` | Yes | Yes | Low (perf 0017) |
| `scheduler.check_trial_expiry` | `group_credits` trial columns | Yes | Yes | Low (perf 0017) |

---

## 8. Disposable PostgreSQL Validation Status

**Disposable PostgreSQL validation was not run because no safe disposable DB was available.**

Reasoning:

- Default test/dev URL in `tests/conftest.py` is `postgresql+asyncpg://musicbot:devpassword@localhost/musicbot_dev`.
- No evidence of an isolated empty database or `TEST_DATABASE_URL` pointing to a dedicated throwaway instance on this machine.
- Instructions forbid touching production or running destructive/upgrading commands without a confirmed disposable target.

---

## 9. Recommended Next Implementation Phases

| Phase | Focus | Risk | Main files | Tests needed | Rollback | Before production? |
|-------|--------|------|------------|--------------|----------|------------------|
| **2A** | Migration/model metadata drift (indexes in models, document Alembic-only, `repeat_mode`) | Low–Medium | `models.py`, docs | Static + optional PG reflect | Revert model DDL declarations | **Yes** |
| **2B** | Schema drift test (Alembic head vs `create_all()` reflect) | Low | `tests/`, CI | New pytest on disposable PG | Delete test | **Yes** |
| **2C** | Resolve `credit_history` partitioning (single table, drop orphan) | **High** | `0003`/`0007` successors, `models.py`, `credit_repo` | Partition + insert tests; fix `test_gap_*` | Restore from backup; downgrade carefully | **Yes — blocking for scale** |
| **2D** | Owner panel N+1 / query batching | Medium | `admin_report_repo.py`, handlers | Load/latency tests | Code revert | Recommended |
| **2E** | Scheduler/broadcast batching (`_get_recipients`, daily deduct) | Medium | `broadcast_service_v2.py`, `scheduler.py`, `credit_service.py` | Integration tests | Code revert | Recommended |

**Apply `0017` in production via:** `alembic upgrade head` (from `app/` with correct `DATABASE_URL` and `PYTHONPATH`), not via `init_db()`.

---

## 10. Commands Run

```text
git status --short
python -m py_compile app/database/migrations/versions/0017_safe_hot_path_indexes.py app/database/models.py app/database/engine.py app/database/migrations/env.py
cd app; DATABASE_URL=postgresql+asyncpg://invalid:invalid@127.0.0.1:1/disposable_audit_only alembic history
cd app; DATABASE_URL=postgresql+asyncpg://invalid:invalid@127.0.0.1:1/disposable_audit_only alembic heads
```

Repository searches: `rg` / codebase grep for `create_all`, `credit_history`, index names, migration revisions.

**Not run:** `alembic upgrade head`, `psql`, any connection to `localhost/musicbot_dev`, bot startup, `setup_server.sh`.

---

## 11. Files Inspected

- `app/database/models.py`
- `app/database/engine.py`
- `app/database/migrations/env.py`
- `app/alembic.ini`
- `app/database/migrations/versions/*.py` (all 17 revisions)
- `app/database/migrations/versions/0017_safe_hot_path_indexes.py`
- `app/repositories/*.py` (15 modules)
- `app/services/credit_service.py`, `broadcast_service.py`, `broadcast_service_v2.py`, `helper_pool_service.py`, `media_event_service.py`, `recovery_service.py`, `watchdog.py`, `install_policy_service.py`
- `app/scheduler.py`
- `app/main.py`, `app/bootstrap.py`
- `tests/conftest.py`, `tests/test_gap_closures.py`, `tests/test_gap_batch2_3.py`, `tests/test_broadcast_wizard.py`, and grep hits for migration/credit/broadcast/helper/global ban tests
- `AGENTS.md`, [server_native_deployment.md](../deployment/server_native_deployment.md), `app/config.env.example`

---

## 12. Change Confirmation

- **Only file created/updated by this audit:** `DB_SCHEMA_DRIFT_AND_INDEX_VALIDATION_REPORT.md`
- **No** changes to application code, migrations (including `0017`), models, repositories, services, or runtime configuration.
- **No** database connections or mutations were performed.
