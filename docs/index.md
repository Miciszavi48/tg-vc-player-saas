# Telegram Voice Chat Music & Video Player Bot — Documentation Roadmap

> **Status:** Authoritative Documentation Index & Architecture Roadmap  
> **Target Environment:** Python 3.12+ | PostgreSQL 16 | Redis 7 | Kurigram | PyTgCalls  
> **Alembic Revision Head:** `0039_hot_seat`  
> **i18n Architecture:** Split-only fragments under `app/resources/i18n/`

---

## 1. Quick Facts & Tech Stack

| Component | Specification | Notes |
|---|---|---|
| **Language** | Python 3.12+ | Asynchronous asyncio event loop |
| **Telegram MTProto Client** | Kurigram (custom Pyrogram fork) | Required for Pyromod compatibility and custom MTProto patches |
| **Voice Chat Engine** | `py-tgcalls` | Uses native `ntgcalls` C wheel for WebRTC audio/video |
| **Primary Database** | PostgreSQL 16 | ACID persistent storage via SQLAlchemy 2.0 async + asyncpg |
| **Database Migrations** | Alembic | Canonical migration head: `0039_hot_seat` |
| **Cache & Locks** | Redis 7 | `decode_responses=True` mandatory across all clients |
| **Task Scheduling** | APScheduler 3.10+ | Async cron jobs (daily deduct, health probes, backups) |
| **Media Processing** | FFmpeg + yt-dlp | Live audio/video transcoding; requires Deno runtime |
| **Session Encryption** | Cryptography (`Fernet`) | AES-128-CBC encryption for helper session strings |

---

## 2. Core Documentation Pillars

### 🏛️ Architecture & System Design

Detailed specifications of core bot architecture, lifecycle hooks, event routing, and multi-tenancy:

- [**`architecture/system-design.md`**](architecture/system-design.md) — **(Canonical)** Comprehensive system architecture, startup sequence, session management, PyTgCalls voice stack, and handler priority group routing.
- [`architecture/handler_routing.md`](architecture/handler_routing.md) — Detailed event dispatch mechanics, priority groups, and callback propagation rules.
- [`architecture/playback_architecture.md`](architecture/playback_architecture.md) — Audio and video streaming pipeline, FFmpeg transcode flow, and media caching.
- [`architecture/multi_tenancy.md`](architecture/multi_tenancy.md) — Role hierarchy (Developer → Owner → Sudo → Group Admin → VIP) and tenant lineage.
- [`architecture/i18n_architecture.md`](architecture/i18n_architecture.md) — Split-only JSON internationalization architecture and manifest loader.

---

### 🚀 Production Deployment & Infrastructure

Guides for provisioning, containerization, multi-instance isolation, and upgrades:

- [**`deployment/systemd-docker.md`**](deployment/systemd-docker.md) — **(Canonical)** Production deployment guide covering native Linux systemd services (`musicbot.service`), multi-instance orchestration, and Docker Compose.
- [`MULTI_INSTANCE_DEPLOYMENT.md`](MULTI_INSTANCE_DEPLOYMENT.md) — Multi-instance native deployment runbook and migration procedures.
- [`SIMPLE_MULTI_INSTANCE_GUIDE_FA.md`](SIMPLE_MULTI_INSTANCE_GUIDE_FA.md) — Persian quickstart guide for multi-instance deployments.
- [`deployment/staging_validation.md`](deployment/staging_validation.md) — Pre-deployment staging validation and verification checklists.

---

### 🎮 Features, Commands & Admin Panels

Comprehensive catalog of user commands, slash-free triggers, and administrative UI:

- [**`features/commands-and-panels.md`**](features/commands-and-panels.md) — **(Canonical)** Complete reference of all slash commands, slash-free Persian/English triggers, role management, and interactive admin panels (Developer, Owner, Sudo, Group Admin).
- [`features/call_security.md`](features/call_security.md) — Per-chat Voice Call Security (**امنیت کال**) gate, membership age tracking, and auto-mute.
- [`features/playback.md`](features/playback.md) — In-depth voice streaming mechanics, queue controls, and social media downloads (Fast-Creat).
- [`features/now_playing_cover_security.md`](features/now_playing_cover_security.md) — Dynamic now-playing cover art renderer and security controls.
- [`features/admin_panels.md`](features/admin_panels.md) — Technical structure of inline keyboard administration menus.
- [`features/credit_and_charge.md`](features/credit_and_charge.md) — Subscription credit model, charge commands, and daily deduction logic.
- [`features/broadcast.md`](features/broadcast.md) — Multi-step broadcast wizard, target filtering, and cron scheduling.
- [`features/force_join.md`](features/force_join.md) — Forced channel membership verification gate.

---

### 🛠️ Operations, Maintenance & Troubleshooting

Runbooks for live operations, health monitoring, error recovery, and schema verification:

- [**`operations/troubleshooting.md`**](operations/troubleshooting.md) — **(Canonical)** Comprehensive operational troubleshooting guide: voice chat errors, PyTgCalls issues, proxy & network setup, FloodWait handling, silent callbacks, and database recovery.
- [`operations/validation.md`](operations/validation.md) — Safe offline validation commands and test execution.
- [`operations/schema_drift.md`](operations/schema_drift.md) — Database schema drift detector and migration verification.
- [`operations/daily_deduct_idempotency.md`](operations/daily_deduct_idempotency.md) — Idempotent daily deduction mechanics and safety guards.
- [`operations/helper_selection_concurrency.md`](operations/helper_selection_concurrency.md) — Concurrency protection for atomic helper account reservation.

---

### 💾 Persistence, Caching & Data Integrity

Data models, schema evolution, Redis key topologies, and billing state:

- [`DATABASE_AND_SCHEMA.md`](DATABASE_AND_SCHEMA.md) — Complete PostgreSQL table catalog, SQLAlchemy ORM models, and index map.
- [`REDIS_AND_CACHE.md`](REDIS_AND_CACHE.md) — Redis key namespaces, TTL policies, distributed locks (`SET NX PX`), and FSM keys.
- [`CREDIT_SOURCE_OF_TRUTH.md`](CREDIT_SOURCE_OF_TRUTH.md) — Canonical group credit state vs transient heuristic caches.

---

### 🧪 Testing & Quality Assurance

Test runner instructions, safety invariants, and isolation requirements:

- [`TESTING_AND_SAFETY.md`](TESTING_AND_SAFETY.md) — Safety rules: database isolation, fake Redis, avoiding DB 0.
- [`testing.md`](testing.md) — Guide to running automated pytest suites.

---

### 📊 Reports & Topology Inventories

Generated topologies and architectural audit records:

- [`reports/index.md`](reports/index.md) — Reports index.
- [`reports/current/handler_group_inventory.md`](reports/current/handler_group_inventory.md) — Machine-audited startup handler topology.
- [`reports/current/callback_dispatch_matrix.md`](reports/current/callback_dispatch_matrix.md) — Replayed callback query dispatch matrix and route reachability analysis.
