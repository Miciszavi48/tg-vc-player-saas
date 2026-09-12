# SYSTEM_ARCHITECTURE

> Last verified against repository: 2026-07-19

## Document Metadata
- Project: Telegram Voice Chat Music & Video Player Bot (SaaS)
- Last reviewed: 2026-07-19
- Alembic head: **`0033_instance_database_ownership`**
- Authoring mode: Architecture reference derived from `app/` layout

> **Note:** This is a structural overview. For feature-level truth, prefer [features/](../features/) and [operations/](../operations/). The active i18n system is split-only under `app/resources/i18n/`, and toggle labels use `toggle_label()`.

## Scan Scope
This document is based on a deep workspace scan of:
- `app/` (handlers, services, repositories, database, utils, config, scheduler, resources, tools)
- `tests/` (unit, integration, UI contract/audit, broadcast wizard, streaming, helper management)
- root files (`README.md`, `DATABASE_AND_SCHEMA.md`, `setup_server.sh`, `deploy.sh`, `docker-compose.yml`, `Dockerfile`, env templates)
- deployment scripts and service units (`deployment/musicbot.service`, `scripts/musicbot.service`, `scripts/deploy.sh`)

## Executive Summary
The current system is a modular Python 3.12+ SaaS bot built around:
- Pyrogram/KuriGram + PyTgCalls for Telegram interaction and voice-chat streaming
- SQLAlchemy async + PostgreSQL for persistence
- Redis for caching, distributed locking, and temporary FSM/runtime state
- APScheduler for recurring operations and maintenance jobs

The architecture has clearly separated concerns (handlers -> services -> repositories -> DB) and substantial UX hardening in inline keyboards and callback flows. Key achievements requested in this review are implemented and verified, especially:
- categorized admin/group navigation trees that reduce cognitive load
- a full Redis-backed Advanced Broadcast FSM wizard with dynamic target checkboxes and back navigation
- dynamic media pipeline optimization for audio vs video streaming/transcoding
- centralized Redis key registry pattern
- production deployment automation with systemd hardening and non-root execution
- encrypted helper session handling via Fernet with key rotation fallback

---

## 1. System Topology

## 1.1 Layered Runtime Model
- **Entry point**: `app/main.py`
- **Inbound interface**: Pyrogram message/callback handlers in `app/handlers/`
- **Business orchestration**: `app/services/`
- **Data access**: `app/repositories/`
- **Persistence**: SQLAlchemy models + Alembic migrations in `app/database/`
- **Cache/locks/state**: `app/utils/cache.py` + `app/utils/redis_keys.py`
- **Presentation contract**: `app/utils/ui.py` + split i18n (`app/resources/i18n/` via `manifest.json`) — see [features/i18n.md](../features/i18n.md)
- **Schema gate**: `app/database/schema_readiness.py` — production PostgreSQL requires Alembic at head before seeding (`create_all` is not the primary production path)
- **Background jobs**: `app/scheduler.py`

See canonical references:
- [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
- [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
- [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
- [TESTING_AND_SAFETY.md](../TESTING_AND_SAFETY.md)

## 1.2 Core Data Flow Pattern
Typical flow is:
1. Handler validates chat/role/filter conditions.
2. Handler invokes service/repository functions.
3. Services coordinate Redis locks/cache + repository calls + external APIs.
4. Repositories execute SQLAlchemy async queries/transactions.
5. Handler returns UX response using keyboard factory and i18n text keys.

This pattern is consistent across playback, install, broadcast, helper, and panel management paths.

---

## 2. UX/UI & Cognitive Load Optimization

## 2.1 Navigation Tree: From Flat Button Walls to Structured Branches

### Developer Panel Tree
Implemented in `app/utils/ui.py` and `app/handlers/dev_panel.py`.

Main menu now exposes category nodes (`DEV_CAT_*`) instead of a long mixed control list:
- `DEV_CAT_CREDIT`
- `DEV_CAT_RATES`
- `DEV_CAT_BROADCAST`
- `DEV_CAT_LISTS`
- `DEV_CAT_SETTINGS`
- `DEV_CAT_USERS`
- `DEV_CAT_TEXTS`

Each category routes to focused sub-keyboards:
- `KeyboardFactory.dev_sub_credit`
- `KeyboardFactory.dev_sub_rates`
- `KeyboardFactory.dev_sub_broadcast`
- `KeyboardFactory.dev_sub_lists`
- `KeyboardFactory.dev_sub_settings`
- `KeyboardFactory.dev_sub_users`
- `KeyboardFactory.dev_sub_texts`

Handler-level category transitions are explicit (`dev_cat_credit`, `dev_cat_rates`, etc.) and each opens a section title screen with bounded controls.

### Group Panel Tree
Group UX is also explicitly hierarchical:
- Root nodes: `Settings`, `Management`, `Help`, `Support`
- Submenus:
  - settings toggles (`group_settings`)
  - management (`group_management_menu`)
  - help (`group_help_menu`)
  - support (`group_support_menu`)

### Context-Aware Back Navigation
`NAV_BACK` in `app/handlers/callbacks.py` is context-sensitive:
- group context -> returns to Group Panel
- PM context -> role-based return (Developer / Owner / Sudo / regular start menu)

This eliminates dead-end navigation and lowers interaction friction.

### Evidence from tests
- `tests/test_panels.py`
- `tests/handlers/panels/test_developer_panel.py`
- `tests/handlers/panels/test_group_panel.py`
- `tests/test_ui_audit.py`

These validate category presence, callback coverage, and back/close consistency.

## 2.2 Label Simplification & Human-Centric Persian UI

User-facing labels are sourced from manifest-registered FA/EN fragments under `app/resources/i18n/`, not hardcoded in handlers.

Observed UX traits:
- concise, task-oriented labels (`افزایش اعتبار`, `کسر اعتبار`, `مدیریت سودوها`, `متون و لینک‌ها`)
- category names in plain Persian with recognizable semantics (`مدیریت اعتبار`, `تنظیم نرخ‌ها`, `ارسال همگانی`, `تنظیمات عمومی`)
- callback payloads remain stable ASCII constants while display text is localized

Contract enforcement:
- `tests/test_ui_contract.py` enforces no hardcoded user-visible strings in handlers and key parity between fa/en.
- `tests/test_ui_audit.py` + `tests/test_ui_contract.py` enforce callback-data stability (`CB` values ASCII, no localization drift in callback protocol).

## 2.3 Advanced Broadcast FSM Wizard (Interactive, Stateful, Reversible)

Implemented in `app/handlers/broadcast_wizard.py`.

### State model
- Redis-backed FSM state:
  - `_set_state`, `_get_state`, `_clear_state`
  - key factory: `bcw_state_key(user_id)`
  - TTL: `TTL_BCW_STATE`

### Step flow
1. payload capture (`awaiting_payload`)
2. mode selection (`send` vs `forward`)
3. target selection with checkboxes (users/groups/channels, multi-select)
4. audience filter (`all`, `7d`, `30d`)
5. schedule mode (`now`, Jalali datetime, delay hours, recurring)
6. confirmation + execution

### Dynamic checkbox UX
- `_targets_keyboard` shows `tgt_selected` (`✅`) / `tgt_unselected` (`◻️`) state dynamically.
- callbacks toggle selection in-place via `edit_reply_markup`.

### Back navigation
Dedicated back callbacks per step:
- `BCW_BACK_MODE`
- `BCW_BACK_TGT`
- `BCW_BACK_FILTER`
- `BCW_BACK_SCHED`

This preserves state while allowing non-destructive step rollback.

### Scheduling capabilities
- immediate send
- run at Jalali datetime (`%Y/%m/%d %H:%M`, converted to Gregorian UTC)
- run after N hours
- recurring every N hours (scheduler interval job)

### Persistence/execution bridge
On confirm, wizard writes full broadcast metadata into `broadcasts` table (`run_at`, `interval_hours`, `target_types_json`, `filter_type`, source admin context) and dispatches through `BroadcastServiceV2`.

### Evidence from tests
- `tests/test_broadcast_wizard.py`
- `tests/test_broadcast_e2e.py`
- migration coverage: `app/database/migrations/versions/0008_broadcast_scheduling.py`

## 2.4 Sudo Onboarding Pipeline

When sudo is added from Developer management flow (`app/handlers/dev_panel.py`):
- user is inserted/activated
- sudo cache invalidated
- success confirmation sent
- onboarding DM sent via `safe_send_message(..., t("sudo_mgmt.welcome_dm"))`

The onboarding DM (`sudo_mgmt.welcome_dm` in both FA and EN split fragments) is structured and actionable:
- role scope
- operational duties
- quick access commands (`/start`, charge command examples)

Related onboarding UX for installers is also implemented via post-install panel:
- `KeyboardFactory.post_install_panel` in `app/utils/ui.py`
- wired in `app/handlers/install.py`
- includes immediate actions: increase/decrease credit hints, player panel button, help button, guide channel link.

---

## 3. Core Backend & Performance Architecture

## 3.1 Media Pipeline (Audio vs Video Optimized for Voice Chats)

### Invocation path
- Playback handlers route media_type (`audio`/`video`) to:
  - `CallService.join_voice_chat`
  - `MediaService.download_audio` / `download_video`
  - `transcode_pool.pre_transcode`

### Audio profile
Audio transcoding config (pre-transcode and yt-dlp postprocess) uses Opus-centric low-overhead profile:
- `-vn`
- `-c:a libopus`
- `-b:a 48k`
- `-ar 48000`
- `-ac 2`
- `-threads 1`

### Video profile
Video path applies Telegram-friendly, low-latency profile:
- `-c:v libx264`
- `-preset ultrafast`
- `-maxrate 1500k`
- `-bufsize 3000k`
- `-vf scale=-2:480` (or capped by configured video quality in download stage)
- `-c:a aac`
- `-b:a 128k`
- `-threads 2`

### Dynamic selection
`media_type` controls runtime stream object creation:
- audio -> `AudioPiped`-compatible stream builders
- video -> `AudioVideoPiped`-compatible builders

Compatibility fallbacks for multiple PyTgCalls API versions are implemented in `call_service.py`.

### Performance and resilience features
- bounded transcode concurrency (`TRANSCODE_POOL_SIZE` semaphore)
- prefetch pipeline for next queue item (`_prefetch_next`) with transcode cache hit path
- graceful passthrough fallback when transcode fails/timeouts
- cleanup of source/transcoded files on leave
- watchdog cleanup for orphan calls/zombie ffmpeg (`app/services/watchdog.py`)

## 3.2 Redis Key Registry Architecture

Central registry: `app/utils/redis_keys.py`

Contains:
- key factories per domain (settings, credit, role, VIP, FM, broadcast, helper, install, wallet, prefetch, locks)
- shared lock-key wrapper
- all major TTL constants (`TTL_*`)

Cache/lock implementation in `app/utils/cache.py` consumes registry keys and exposes:
- typed cache helpers
- broadcast progress state management
- distributed lock acquire/release with Lua-guarded token semantics
- Postgres advisory lock fallback when Redis is unavailable

Critical operational usage validated in services/handlers:
- install locks (`install_lock_key`)
- credit locks (`credit_lock_key`)
- broadcast execution lock (`bc_lock_key`)
- helper join locks/idempotency keys
- wizard temporary state keys

Redis client is initialized with `decode_responses=True`, matching project-level requirement.

## 3.3 Database Architecture (Repository + SQLAlchemy Async)

### Engine/session
`app/database/engine.py`:
- async SQLAlchemy engine (`create_async_engine`)
- tunable pool settings from config
- `statement_cache_size` / `prepared_statement_cache_size` disabled for asyncpg stability
- central `async_session` factory

### Repository pattern
Repository modules in `app/repositories/` encapsulate DB operations by aggregate/domain:
- `user_repo`, `group_repo`, `channel_repo`
- `settings_repo`, `credit_repo`, `playlist_repo`
- `broadcast_repo`, `blacklist_repo`, `admin_repo`, etc.

This keeps most read/write logic out of handlers and supports isolation/testing via session patching in test suites.

### Schema status: single-tenant
Current schema is globally scoped (no `bot_id` columns), with uniqueness keyed by global chat/user identifiers.
Confirmed by:
- ORM model constraints in `app/database/models.py`
- explicit audit in `DATABASE_AND_SCHEMA.md` (`strictly SINGLE-TENANT`)

### Multi-tenant upgrade preparedness
While currently single-tenant, structure is prepped for a controlled Bot-Maker migration path because:
- data access is largely centralized through repository/service boundaries
- constraints are explicit and migration-friendly
- Alembic migration pipeline is active and versioned
- dedicated audit document already maps exact `bot_id` migration requirements

Important nuance from audit:
- multi-tenant conversion requires broad schema updates (`bot_id` propagation, PK/unique rewrites, context injection).

---

## 4. Deployment & Infrastructure

## 4.1 Automated Bare-Metal Ubuntu Deployment (`setup_server.sh`)

`setup_server.sh` is a full bootstrap workflow (0/7 through 7/7):
1. root pre-flight validation
2. system package installation (Python toolchain, ffmpeg, Redis, PostgreSQL)
3. Redis enable/start + health check
4. PostgreSQL enable/start + user/db provisioning + grants
5. project copy/sync into `/opt/musicbot`
6. venv creation + dependency install (KuriGram first, pyromod/pytgcalls handling)
7. config generation/update, Alembic migrations, systemd unit generation, ownership hardening

This is an end-to-end automated path from clean host to running service.

## 4.2 Runtime Security & Process Isolation

### Non-root execution
Deployment creates dedicated system user (`musicbot`) and runs service as:
- `User=musicbot`
- `Group=musicbot`

### systemd hardening
From generated unit and templates:
- `NoNewPrivileges=true`
- `ProtectSystem=strict`
- `PrivateTmp=true` (in deployment template)
- constrained writable paths via `ReadWritePaths=...`
- resource limits (`LimitNOFILE`, `LimitNPROC`)
- restart policy (`Restart=always` or template-specific restart rules)

### Filesystem permissioning
Setup applies strict ownership (`chown -R musicbot:musicbot /opt/musicbot`) and backup dir ownership constraints.

## 4.3 Helper Session Encryption (Fernet)

Session encryption stack:
- config keys: `HELPER_SESSION_KEY_CURRENT`, optional `HELPER_SESSION_KEY_OLD`
- implementation: `HelperPoolService.encrypt_session` / `decrypt_session`
- algorithm: `cryptography.fernet.Fernet`
- decrypt supports current key then old-key fallback (rotation-safe)

Operational tooling:
- `helper_pool_cli import-session` encrypts on write
- `helper_pool_cli rotate-key` re-encrypts all helper sessions with current key

Validation:
- `tests/test_helper_management.py` covers roundtrip encryption + old-key fallback

---

## 5. Quality Controls & Regression Guardrails

Architecture-level quality signals identified in `tests/`:
- callback/UI contract enforcement (coverage, back/close presence, no localized callback payloads)
- broadcast wizard FSM/state/scheduling tests
- streaming/watchdog/pre-transcode tests
- concurrency/locking tests for critical operations
- helper encryption and CLI command coverage tests
- i18n parity and missing-key safety tests

This test structure acts as a practical anti-regression layer for both UX and backend invariants.

---

## 6. Observed Constraints and Forward Path

- Current DB remains single-tenant by design; multi-tenant upgrade is intentionally deferred and documented.
- Voice-chat availability still depends on environment-compatible `pytgcalls/tgcalls` native stack.
- Redis key centralization pattern is firmly established and broadly used across cache/lock/FSM paths.
- The UX system is now stateful, hierarchical, and i18n-driven, providing a stable foundation for future feature expansion without returning to callback chaos.

---

## 7. Evidence Index (Key Files)

### UX/UI
- `app/utils/ui.py`
- `app/handlers/dev_panel.py`
- `app/handlers/group_panel.py`
- `app/handlers/callbacks.py`
- `app/handlers/broadcast_wizard.py`
- `app/resources/i18n/manifest.json`
- `app/resources/i18n/fa/` and `app/resources/i18n/en/`
- `tests/test_ui_audit.py`
- `tests/test_ui_contract.py`
- `tests/test_panels.py`
- `tests/test_broadcast_wizard.py`

### Backend/Performance
- `app/services/call_service.py`
- `app/services/media_service.py`
- `app/services/transcode_pool.py`
- `app/services/watchdog.py`
- `app/services/broadcast_service_v2.py`
- `app/utils/cache.py`
- `app/utils/redis_keys.py`
- `app/database/engine.py`
- `app/database/models.py`
- `app/repositories/*.py`
- `DATABASE_AND_SCHEMA.md`

### Deployment/Security
- `setup_server.sh`
- `deployment/musicbot.service`
- `scripts/musicbot.service`
- `scripts/deploy.sh`
- `deploy.sh`
- `README.md`
- `app/services/helper_pool_service.py`
- `app/tools/helper_pool_cli.py`
- `tests/test_helper_management.py`
