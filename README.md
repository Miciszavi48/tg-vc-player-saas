# Enterprise-Grade Telegram Music & Voice Streaming Engine

[![CI Test Suite](https://img.shields.io/badge/CI%20Test%20Suite-passing-brightgreen?logo=github-actions)](#testing--quality-assurance)
![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg?logo=python)
![Telegram MTProto](https://img.shields.io/badge/Telegram%20MTProto-Kurigram%20v2.x-0088cc.svg?logo=telegram)
![Voice Engine](https://img.shields.io/badge/Voice%20Engine-PyTgCalls%20%2B%20ntgcalls-orange.svg)
![Database](https://img.shields.io/badge/Database-PostgreSQL%2016%20%7C%20SQLAlchemy%202.0-336791.svg?logo=postgresql)
![Cache & Locks](https://img.shields.io/badge/Cache%20%26%20Locks-Redis%207-dc382d.svg?logo=redis)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Architecture](https://img.shields.io/badge/Architecture-Modular%20SOA%20%2F%20Clean%20Hexagonal-8A2BE2)
[![Author](https://img.shields.io/badge/Author-Ali%20Khalili-blue.svg)](https://github.com/dibbed)

An enterprise-grade, multi-tenant SaaS engine engineered for high-concurrency audio and video streaming into Telegram Voice Chats (Group Calls and Channel Live Streams). Built on an asynchronous event-driven architecture, distributed state management, resilient helper session pooling, and fault-tolerant stream recovery.

---

## Architectural Highlights

- **Service-Oriented Architecture (SOA):** Decoupled layers separating MTProto transport handlers, domain business services, repository-based data access, and asynchronous media processing pipelines.
- **Multi-Helper Session Pooling:** Load-balanced distribution of streaming calls across dedicated userbot helper accounts with encrypted session storage, active call balancing, and automatic FloodWait quarantine.
- **Resilient Stream Recovery:** Cold-start playback resumption with staggered re-joins, randomized jitter, seek-position state persistence, and automatic recovery following abrupt daemon terminations or node restarts.
- **Strict Multi-Tenancy & FSM Isolation:** Complete boundary isolation between Telegram group instances with decoupled Finite State Machine (FSM) namespaces, isolated Redis keyspaces, and independent chat configurations.
- **Real-Time Voice Diagnostics & Watchdogs:** Continuous supervision of FFmpeg processes, automated orphan call pruning, zombie process reaping, and real-time audio pipeline health metrics.
- **Enterprise Role-Based Access Control (RBAC):** Seven-tier hierarchical security model (`Developer` &rarr; `Owner` &rarr; `Sudo` &rarr; `Group Admin` &rarr; `Music Admin` &rarr; `Video Admin` &rarr; `VIP`) backed by bitmasked permission sets and cached authorization lookups.
- **Audited Financial Credit Ledger:** Subscription management engine with transactional balance modifications, daily credit deductions, low-balance warnings, and automated group service suspensions.
- **Call Security Gatekeeper (امنیت کال):** Real-time voice chat moderation gate enforcing membership age thresholds, unauthorized speaker mutes, and per-chat playback access controls.

---

## Core Engineering Features

### 1. Multi-Helper Session Pooling (`app.services.helper_pool_service`)
Streaming audio/video into Telegram voice chats requires userbot accounts. Single-account systems encounter severe rate-limiting (`FloodWait`) and connection saturation. This engine employs an active-active helper pool:
- **AES-256 (Fernet) Encryption:** Helper MTProto session strings are encrypted at rest using keys defined in `HELPER_SESSION_KEY_CURRENT`. Plaintext sessions are never written to disk.
- **Dynamic Load Balancing:** Automatically allocates voice chats to helpers based on current call counts, per-hour join quotas, and connection capacity.
- **Intelligent Quarantine & Cooldown:** Intercepts `FloodWait` and `UserRestricted` exceptions, immediately quarantines the affected helper for the required duration plus safety jitter, reassigns the affected call to a healthy peer helper, and restores the quarantined account once the cooldown expires.

### 2. Audio & Video Processing Pipeline (`app.services.media_service`)
- **Multi-Format Ingestion:** Streams local audio/video documents, direct URLs, YouTube, SoundCloud, HLS live TV channels, and Icecast/Shoutcast radio feeds via `yt-dlp` and `FFmpeg`.
- **Automated Transcoding Pool:** Offloads FFmpeg transcoding to a worker semaphore pool (`TRANSCODE_POOL_SIZE`) with automated downmixing to 48kHz stereo Opus for low-latency voice chat delivery.
- **LRU Cache & Garbage Collection:** Enforces `MEDIA_CACHE_MAX_GB` quotas with automated TTL cleanup and disk space watermarks to prevent node disk exhaustion.

### 3. Fault-Tolerant Stream Recovery
- **Continuous State Synchronization:** The audio pipeline regularly snapshots playback positions into the `playback_states` table.
- **Graceful Termination Handlers:** Catches `SIGINT` and `SIGTERM` signals to persist exact seek timestamps across all active voice sessions before releasing socket descriptors.
- **Staggered Reconnect Algorithm:** Upon daemon startup, the recovery subsystem staggers voice chat re-joins using token-bucket rate limiting (`RECOVERY_GLOBAL_PER_SECOND`), maximum concurrent connections (`RECOVERY_MAX_CONCURRENT`), and randomized millisecond jitter (`RECOVERY_JITTER_MS`) to prevent Telegram gateway connection throttling.

### 4. Enterprise RBAC & FSM State Machine
- **Hierarchical Permission Resolution:** Evaluates command authorization across 7 discrete tiers:
  1. `Developer`: System master, instance configuration, node maintenance, global broadcast, raw debugging.
  2. `Owner`: Service owner, sudo delegation, global analytics, helper inspection.
  3. `Sudo`: Delegated instance operator, group provisioning, credit allocations.
  4. `Group Admin`: Local group manager, install lifecycle, playback settings.
  5. `Music Admin`: Permitted to manage audio queue, playback speed, volume, and tracks.
  6. `Video Admin`: Authorized to initiate and control high-bandwidth video streams.
  7. `VIP`: Privileged regular user with playback priority and queue bypass rights.
- **FSM Conversation Isolation:** All interactive wizards (e.g., OTP onboarding, broadcast builders, invoice generators) utilize keyed Redis states with deterministic TTLs, preventing user prompt cross-talk and memory leaks.

---

## Tech Stack

| Domain | Technology | Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Language** | Python | `3.12+` (3.11 supported) | Asynchronous core runtime |
| **MTProto Client** | Kurigram | `dev` branch | High-throughput Telegram MTProto protocol implementation |
| **Voice Engine** | PyTgCalls + ntgcalls | `1.x` | Native WebRTC/Voice Chat streaming bridge |
| **Database** | PostgreSQL | `16+` | Relational persistence & transactional ACID guarantees |
| **Async ORM** | SQLAlchemy | `2.0+` (asyncio) | Asynchronous ORM and SQL expression builder |
| **Migrations** | Alembic | `1.13+` | Version-controlled database schema migrations |
| **Cache & Distributed Locks**| Redis | `7.x` (`redis.asyncio`) | Atomic distributed locks (`SET NX PX`), FSM state, and hot cache |
| **Media Extraction** | yt-dlp + yt-dlp-ejs | `2024.x` | Media metadata extraction & stream resolution |
| **JavaScript Engine**| Deno / Node.js | Deno `>=2.3` / Node `>=22` | EJS runtime dependency for YouTube extractor deciphering |
| **Media Transcoder** | FFmpeg | `6.x / 7.x` | Audio/video remuxing, normalization, and Opus transcoding |
| **Task Scheduling** | APScheduler | `3.10+` | Daily credit deduction cron jobs, backup runs, and watchdogs |
| **Observability** | Loguru | `0.7+` | Structured JSON and console diagnostic logging |
| **Cryptography** | Python Cryptography | `42.x` (Fernet) | Symmetric encryption for helper sessions and cookie stores |

---

## Folder Structure

```
tg-vc-player-saas/
├── .github/
│   └── workflows/
│       └── test.yml                 # Automated CI test pipeline (Python 3.11/3.12 + FFmpeg)
├── app/
│   ├── assets/                      # Curated stream feeds & satellite channel catalogs
│   │   ├── radio_stations.json      # Preconfigured internet radio stations
│   │   ├── satellite_channels.json  # Satellite live TV streaming manifests
│   │   └── tv_channels.json         # National TV HLS stream definitions
│   ├── config/                      # Strongly-typed configuration dataclasses & validators
│   │   ├── __init__.py
│   │   └── settings.py              # Environment variable loading, validation & defaults
│   ├── database/                    # Persistence engine, ORM models & migrations
│   │   ├── engine.py                # Async SQLAlchemy engine with NullPool / pool tuning
│   │   ├── models.py                # 31 normalized SQLAlchemy database tables
│   │   └── migrations/              # Alembic schema versioning scripts
│   ├── handlers/                    # Pyrogram MTProto message & callback dispatchers
│   │   ├── callbacks.py             # Central callback router with prefix demuxing
│   │   ├── playback.py              # Core voice chat playback pipeline & controllers
│   │   ├── dev_panel.py             # Developer administrative interface & metrics
│   │   ├── owner_panel.py           # Owner management interface & delegation
│   │   ├── sudo_panel.py            # Sudo operations, wallet management & audits
│   │   ├── group_panel.py           # Group administration panel & preferences
│   │   ├── helper_otp_wizard.py     # Interactive MTProto OTP helper onboarding flow
│   │   ├── tv_radio.py              # Live HLS TV and radio stream controllers
│   │   └── ...
│   ├── repositories/                # Clean Architecture asynchronous CRUD repositories
│   │   ├── admin_repo.py            # RBAC permissions & administrator metadata
│   │   ├── credit_repo.py           # Balance modifications, credit ledger & daily billing
│   │   ├── group_repo.py            # Chat metadata, membership records & install state
│   │   ├── settings_repo.py         # Per-group audio & moderation configuration
│   │   └── ...
│   ├── resources/                   # Data-driven internationalization (i18n) fragments
│   │   └── i18n/
│   │       ├── en/                  # English UI translations
│   │       └── fa/                  # Persian UI translations (optimized for RTL mobile layout)
│   ├── services/                    # Business domain logic & background orchestration
│   │   ├── call_service.py          # PyTgCalls bridge, volume, speed & queue coordination
│   │   ├── credit_service.py        # Financial accounting, daily charges & grace periods
│   │   ├── helper_pool_service.py   # Multi-helper lifecycle, reservation & load balancing
│   │   ├── media_service.py         # yt-dlp downloading, streaming & transcoding
│   │   ├── recovery_service.py      # Node recovery, seek resumption & staggered rejoin
│   │   └── ...
│   ├── tools/                       # Operational CLI entry points
│   │   └── helper_pool_cli.py       # Helper account provisioning & status inspection tool
│   ├── utils/                       # Shared utility libraries, distributed locks & formatters
│   │   ├── button_style.py          # Telegram button style abstractions
│   │   ├── cache.py                 # Redis connection manager & distributed lock decorator
│   │   ├── i18n.py                  # TextService multi-lingual string loader
│   │   └── ui.py                    # Modular inline keyboard builder & CB constants
│   ├── alembic.ini                  # Alembic environment configuration
│   ├── config.env.example           # Canonical environment configuration template
│   ├── main.py                      # Application bootstrap, DI registration & lifecycle runner
│   ├── requirements.txt             # Canonical production dependencies
│   └── scheduler.py                 # APScheduler background tasks & hourly watchdogs
├── deployment/                      # Deployment resources, systemd units & reverse-proxy assets
├── docs/                            # Deep-dive architectural, feature & operational specifications
│   ├── architecture/                # System topology, priority groups & call sequence diagrams
│   ├── deployment/                  # Multi-instance systemd guides & SFTP delivery manuals
│   ├── features/                    # Feature specifications, command lists & RBAC matrices
│   ├── operations/                  # Concurrency safety, distributed locking & runbooks
│   └── README.md                    # Documentation index
├── scripts/                         # Operational diagnostics & administrative scripts
│   ├── check_voice_stack.py         # PyTgCalls & audio engine validation utility
│   ├── check_youtube_runtime.py     # yt-dlp & JavaScript extraction runtime check
│   ├── db_schema_drift_check.py     # Schema synchronization validation against models
│   └── deploy.sh                    # Host deployment orchestration script
├── tests/                           # Comprehensive Pytest test suite
│   ├── conftest.py                  # Strict test isolation harness (SQLite + fakeredis)
│   ├── factories.py                 # Database fixture factories for mock entities
│   ├── handlers/                    # Callback routing, navigation & command dispatch tests
│   ├── integration/                 # End-to-end multi-instance isolation & stream tests
│   └── unit/                        # Domain service & repository unit tests
├── .env.example                     # Root environment compatibility stub
├── .gitignore                       # Production repository ignore definitions
├── Dockerfile                       # Multi-stage production container build definition
├── docker-compose.yml               # Container orchestration (Bot + PostgreSQL + Redis)
├── docker-compose.test.yml          # Containerized testing environment
├── musicbotctl                      # Host instance management CLI
├── pytest.ini                       # Test suite discovery configuration
├── requirements.txt                 # Root compatibility requirements pointer
└── setup_server.sh                  # Native Ubuntu multi-instance installer & provisioner
```

---

## Prerequisites

Ensure the target host meets the following minimum requirements:

- **Operating System:** Ubuntu 22.04 LTS / 24.04 LTS (recommended), Debian 12, or any Linux distribution supporting Docker.
- **Python:** Version `3.12` or `3.11` with `python3-venv` and `python3-dev`.
- **Database:** PostgreSQL `16.x` with `libpq-dev`.
- **Caching Engine:** Redis `7.x` server.
- **Audio Engine:** `ffmpeg` with Opus and AAC encoding libraries.
- **JS Runtime (for yt-dlp):** Deno `>= 2.3` (recommended) or Node.js `>= 22.0` installed and accessible via system `PATH`.
- **Telegram API Credentials:**
  - `BOT_TOKEN`: Obtained from [@BotFather](https://t.me/BotFather).
  - `API_ID` & `API_HASH`: Obtained from [my.telegram.org](https://my.telegram.org).
  - `DEVELOPER_ID`: Your numeric Telegram User ID (obtained from [@userinfobot](https://t.me/userinfobot)).

---

## Quickstart Guide

### Option A: Containerized Deployment (Docker Compose)

The fastest method to bootstrap a complete development or staging environment with isolated PostgreSQL and Redis containers.

```bash
# 1. Clone the repository
git clone https://github.com/dibbed/tg-vc-player-saas.git
cd tg-vc-player-saas

# 2. Configure the environment
cp config.env.example app/config.env
# Edit app/config.env with your Telegram credentials and generated keys:
nano app/config.env

# 3. Launch the container stack
docker compose up -d

# 4. Follow application logs
docker compose logs -f bot

# 5. Execute database migrations inside the container
docker compose exec bot alembic -c app/alembic.ini upgrade head
```

### Option B: Production Native Deployment (Ubuntu Systemd)

For production environments requiring bare-metal audio I/O performance and multi-instance process isolation:

```bash
# 1. Clone and enter the repository
git clone https://github.com/dibbed/tg-vc-player-saas.git
cd tg-vc-player-saas

# 2. Run the automated multi-instance installer
# The installer automatically installs system packages (PostgreSQL 16, Redis 7, FFmpeg, Python 3.12),
# provisions an isolated virtual environment, configures a dedicated service account,
# creates a dedicated database, and sets up a systemd unit.
sudo ./setup_server.sh --instance musicbot-a --path /opt/musicbot-a

# 3. Configure credentials
sudo nano /opt/musicbot-a/app/config.env

# 4. Apply configuration and start the service
sudo ./setup_server.sh --instance musicbot-a --path /opt/musicbot-a

# 5. Manage the instance via musicbotctl
/opt/musicbot-a/musicbotctl status
/opt/musicbot-a/musicbotctl logs
```

### Local Manual Development Setup

```bash
# 1. Install system prerequisites (Ubuntu)
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev postgresql-16 redis-server ffmpeg build-essential libpq-dev libffi-dev libssl-dev

# 2. Configure PostgreSQL & Redis
sudo -u postgres psql -c "CREATE USER musicbot_user WITH PASSWORD 'StrongPassword123';"
sudo -u postgres psql -c "CREATE DATABASE musicbot_db OWNER musicbot_user;"
sudo systemctl start redis-server

# 3. Setup Python virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 4. Install Python dependencies strictly in order to avoid fork conflicts
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install --no-deps pyromod
pip install "py-tgcalls[pyrogram]"
pip install --force-reinstall https://github.com/KurimuzonAkuma/pyrogram/archive/dev.zip

# 5. Validate the voice streaming stack & YouTube extractors
python scripts/check_voice_stack.py
python scripts/check_youtube_runtime.py --strict

# 6. Initialize environment variables
cp config.env.example app/config.env
nano app/config.env

# 7. Apply database migrations
cd app
MUSICBOT_ENV_FILE=config.env PYTHONPATH=.. alembic upgrade head
cd ..

# 8. Start the bot daemon
python -m app.main
```

---

## Configuration Reference

The canonical configuration resides at `app/config.env` (templated from [`config.env.example`](config.env.example)).

### Key Generation

Before starting the bot, generate your cryptographic Fernet keys:

```bash
# Helper session encryption key & YouTube cookie encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Cookie / token fingerprint keys
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Environment Variables Matrix

| Category | Variable | Required | Description | Default / Example |
| :--- | :--- | :---: | :--- | :--- |
| **Instance Identity** | `INSTANCE_ID` | Yes | Unique lowercase instance identifier | `musicbot-a` |
| | `INSTANCE_ROOT` | Yes | Absolute root path of the instance | `/opt/musicbot-a` |
| | `INSTANCE_DATA_DIR` | Yes | Mutable state and log directory | `/opt/musicbot-a/var` |
| | `REDIS_NAMESPACE_ROOT` | Yes | Root prefix for Redis key namespacing | `musicbot` |
| **Telegram Core** | `BOT_TOKEN` | Yes | Telegram Bot API token from @BotFather | `123456:ABC-DEF...` |
| | `API_ID` | Yes | Telegram MTProto API ID from my.telegram.org | `1234567` |
| | `API_HASH` | Yes | Telegram MTProto API Hash | `0123456789abcdef...` |
| | `DEVELOPER_ID` | Yes | Single or comma-separated Developer Telegram user IDs | `123456789,987654321` |
| | `HELPER_PHONE` | No | Initial helper account phone number for setup | `+1234567890` |
| **Database & Cache** | `DATABASE_URL` | Yes | Async PostgreSQL connection string | `postgresql+asyncpg://user:pass@127.0.0.1:5432/dbname` |
| | `REDIS_URL` | Yes | Redis connection URL | `redis://127.0.0.1:6379/0` |
| | `DB_POOL_SIZE` | No | SQLAlchemy database connection pool size | `20` |
| | `DB_MAX_OVERFLOW` | No | Maximum overflow connections beyond pool size | `40` |
| | `REDIS_SETTINGS_TTL` | No | Group settings cache TTL in seconds | `300` |
| | `REDIS_CREDIT_TTL` | No | Group credit cache TTL in seconds | `60` |
| **Cryptography** | `HELPER_SESSION_KEY_CURRENT` | Yes | Fernet key for helper MTProto session encryption | *(Generate via Fernet)* |
| | `HELPER_SESSION_KEY_OLD` | No | Previous Fernet key during key rotation | *(Empty)* |
| | `YOUTUBE_COOKIE_KEY_CURRENT` | Yes | Fernet key for YouTube cookie storage encryption | *(Generate via Fernet)* |
| | `YOUTUBE_COOKIE_FINGERPRINT_KEY`| Yes | Secret salt for duplicate cookie detection | *(Generate via secrets)* |
| | `FAST_CREAT_TOKEN_KEY_CURRENT` | Yes | Fernet key for API token credential storage | *(Generate via Fernet)* |
| **Media & Transcoding**| `VIDEO_QUALITY` | No | Default video stream resolution | `720` |
| | `MAX_DOWNLOAD_SIZE_MB` | No | Maximum download file size allowed | `200` |
| | `DOWNLOAD_SEMAPHORE` | No | Maximum concurrent active downloads | `10` |
| | `TRANSCODE_POOL_SIZE` | No | Number of parallel FFmpeg worker processes | `4` |
| | `MEDIA_CACHE_MAX_GB` | No | Maximum disk cache allocated for media files | `10` |
| **Recovery & Tuning** | `RECOVERY_GLOBAL_PER_SECOND` | No | Maximum group re-joins per second after reboot | `5` |
| | `RECOVERY_MAX_CONCURRENT` | No | Maximum concurrent re-join operations | `10` |
| | `RECOVERY_JITTER_MS` | No | Randomized jitter in milliseconds added to re-joins | `150` |
| **SaaS & Billing** | `TRIAL_DAYS` | No | Number of free trial days given upon initial install | `3` |
| | `BASE_CREDIT_RATE` | No | Base group monthly subscription cost in Toman | `50000` |
| | `MUSIC_RATE` | No | Additional fee for music playback module | `10000` |
| | `VIDEO_RATE` | No | Additional fee for video streaming module | `25000` |
| | `SECURITY_CALL_RATE` | No | Fee for voice-call security moderation module | `15000` |
| **Observability** | `LOG_CHANNEL_ID` | No | Telegram channel ID for operational notifications | `-1001234567890` |
| | `LOG_LEVEL` | No | Loguru verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`)| `INFO` |
| | `HEALTH_PORT` | No | Local HTTP healthcheck probe port (`0` disables) | `0` |
| | `METRICS_PORT` | No | Local Prometheus metrics exporter port (`0` disables)| `0` |

---

## Operational Management

### 1. Database Migrations
Migrations are strictly versioned via Alembic. Schema drift checks must be executed before production deployments.

```bash
# Apply pending migrations on native host
/opt/musicbot-a/musicbotctl migrate

# Or manually via Alembic
cd app && alembic upgrade head

# Generate a new migration following ORM model adjustments
cd app && alembic revision --autogenerate -m "describe_schema_change"

# Validate schema alignment between SQLAlchemy models and physical tables
python scripts/db_schema_drift_check.py
```

### 2. Helper Pool CLI Operations
Manage helper accounts directly via the administrative CLI without accessing the database manually:

```bash
# View CLI documentation
python -m app.tools.helper_pool_cli --help

# Onboard a new helper account interactively via phone OTP
python -m app.tools.helper_pool_cli add --phone +1234567890

# List registered helper accounts and active assignments
python -m app.tools.helper_pool_cli list

# Inspect helper pool health and connection metrics
python -m app.tools.helper_pool_cli status
```

### 3. Service Lifecycle via `musicbotctl`
On native Ubuntu installations, the `musicbotctl` control script provides unified management:

```bash
/opt/musicbot-a/musicbotctl status     # Inspect systemd unit status
/opt/musicbot-a/musicbotctl logs       # Stream journald logs
/opt/musicbot-a/musicbotctl restart    # Gracefully restart the service
/opt/musicbot-a/musicbotctl backup     # Snapshot database and encrypted state
/opt/musicbot-a/musicbotctl upgrade    # Perform atomic upgrade to new release
```

---

## Testing & Quality Assurance

The project includes an extensive test suite covering unit tests, callback dispatch integrity, UI contracts, and concurrency safety.

### Test Isolation Philosophy
Following the rules documented in [`docs/TESTING_AND_SAFETY.md`](docs/TESTING_AND_SAFETY.md):
- `TEST_MODE=1` activates complete runtime isolation.
- Databases default to SQLite in-memory / local (`aiosqlite`) using `NullPool`.
- Redis connections are transparently intercepted and mocked via `fakeredis`.
- Direct production database connections (`musicbot_dev`, `prod`) are actively blocked by connection assertions in `tests/conftest.py`.

### Running Tests

```bash
# Recommended lightweight suite (fast unit tests, used in CI)
pytest tests/unit/ -v

# Run targeted test suites
pytest tests/handlers/ -v           # Callback routing & UI interaction tests
pytest tests/integration/ -v        # Concurrency & multi-instance isolation tests

# Run single critical test modules
pytest tests/unit/test_cache.py -v
pytest tests/integration/test_multi_instance_isolation.py -v

# Run full test suite (heavy - executes complete 4,000+ test matrix)
pytest -v
```

---

## Architectural & Technical Documentation

For in-depth specifications, architectural decisions, and operational runbooks, refer to the [`docs/`](docs/) directory:

| Document | Focus Area | Description |
| :--- | :--- | :--- |
| [**`docs/architecture/system-design.md`**](docs/architecture/system-design.md) | **System Architecture** | Core engine design, priority group routing, startup sequence, and WebRTC streaming bridge. |
| [**`docs/MULTI_INSTANCE_DEPLOYMENT.md`**](docs/MULTI_INSTANCE_DEPLOYMENT.md) | **Multi-Tenancy** | Production deployment of multiple isolated bot instances on a single host. |
| [**`docs/DATABASE_AND_SCHEMA.md`**](docs/DATABASE_AND_SCHEMA.md) | **Database & Models** | Complete documentation of all 31 ORM models, relations, indices, and Alembic versioning. |
| [**`docs/REDIS_AND_CACHE.md`**](docs/REDIS_AND_CACHE.md) | **Redis & Distributed Locks** | Redis keyspace taxonomy, distributed lock semantics (`SET NX PX`), and eviction policies. |
| [**`docs/CREDIT_SOURCE_OF_TRUTH.md`**](docs/CREDIT_SOURCE_OF_TRUTH.md) | **Financial Accounting** | Transaction ledger, daily subscription deduction batching, and credit audit trail. |
| [**`docs/TESTING_AND_SAFETY.md`**](docs/TESTING_AND_SAFETY.md) | **Quality & Safety** | Guardrails preventing production state pollution during test and CI executions. |
| [**`docs/operations/troubleshooting.md`**](docs/operations/troubleshooting.md) | **Operations & Incident Response**| Runbooks for FloodWait mitigations, orphan voice session recovery, and node crashes. |
| [**`docs/operations/ops_concurrency.md`**](docs/operations/ops_concurrency.md) | **Concurrency Controls** | Comprehensive mapping of distributed lock keys, transactional boundaries, and race condition prevention. |
| [**`docs/features/commands-and-panels.md`**](docs/features/commands-and-panels.md) | **Feature Reference** | Detailed catalog of text commands, inline panels, and RBAC matrix. |
| [**`docs/features/call_security.md`**](docs/features/call_security.md) | **Voice Chat Security** | Voice-call security gatekeeper (`امنیت کال`), membership gating, and audio policy enforcement. |

---

## Author

- **Author / Creator:** Ali Khalili
- **GitHub:** [@dibbed](https://github.com/dibbed)
- **Repository:** [https://github.com/dibbed/tg-vc-player-saas](https://github.com/dibbed/tg-vc-player-saas)

---

## License
 
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.  
Copyright (c) 2026 Ali Khalili.
