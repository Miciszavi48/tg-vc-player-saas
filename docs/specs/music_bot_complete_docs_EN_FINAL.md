# 🎵 Telegram Music Player Bot - Complete Technical Documentation

> **Canonical References:**
> - [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> **Version:** `2.0.0` | **Language:** Persian (FA) | **Database:** PostgreSQL 16 | **Framework:** Pyrogram + PyTgCalls
>
> *Refactored & redesigned from the original single-file SQLite codebase into a production-grade, modular, scalable architecture capable of serving 1M+ groups and channels simultaneously.*

### Implementation status (2026-07-19)

This file is the **client requirements spec**. For shipped behavior, prefer:

- [docs/README.md](../README.md) — documentation index
- [features/call_security.md](../features/call_security.md) — **امنیت کال** (not «امنیت کانال»)
- [features/playback.md](../features/playback.md), [features/group_settings.md](../features/group_settings.md)
- Alembic head: **`0033_instance_database_ownership`**

Status: **PARTIAL / SUPERSEDED IN PART.** This file preserves the client baseline. Current multi-instance deployment, split i18n, role boundaries, callbacks, and schema behavior are documented by the canonical architecture, feature, and operations guides linked above; examples below are not proof of current implementation.

Obsolete terms in older sections: global «Channel Security» / `channel_security_enabled` → removed from UI; per-chat Call Security replaces it.

---

## 📋 Table of Contents

1. [Project Overview](#1-project-overview)
2. [System Architecture](#2-system-architecture)
3. [Technology Stack](#3-technology-stack)
4. [Project File Structure](#4-project-file-structure)
5. [Configuration File (config.env)](#5-configuration-file-configenv)
6. [PostgreSQL Database - Full Schema](#6-postgresql-database--full-schema)
7. [Database Indexes & Optimization](#7-database-indexes--optimization)
8. [ORM Models (SQLAlchemy)](#8-orm-models-sqlalchemy)
9. [Role Hierarchy & Access Control](#9-role-hierarchy--access-control)
10. [Developer Panel - Full Feature List](#10-developer-panel--full-feature-list)
11. [Owner Panel - Full Feature List](#11-owner-panel--full-feature-list)
12. [Sudo Panel - Full Feature List](#12-sudo-panel--full-feature-list)
13. [Group Panel - Full Feature List](#13-group-panel--full-feature-list)
14. [Start Menu](#14-start-menu)
15. [Playback System - Full Feature List](#15-playback-system--full-feature-list)
16. [Playlist / Queue System](#16-playlist--queue-system)
17. [Media Types & Sources](#17-media-types--sources)
18. [Credit System (Subscription Engine)](#18-credit-system-subscription-engine)
19. [Installation Flow & Trial Period](#19-installation-flow--trial-period)
20. [Promotion & Demotion System](#20-promotion--demotion-system)
21. [Broadcast System](#21-broadcast-system)
22. [Force Join System](#22-force-join-system)
23. [Filter Words System](#23-filter-words-system)
24. [Blacklist System](#24-blacklist-system)
25. [Auto-Leave System](#25-auto-leave-system)
26. [Reporting & Logging System](#26-reporting--logging-system)
27. [Cron Jobs & Scheduled Tasks](#27-cron-jobs--scheduled-tasks)
28. [Handler Modules (Full Structure)](#28-handler-modules-full-structure)
29. [Custom Filters & Decorators](#29-custom-filters--decorators)
30. [Redis Caching Layer](#30-redis-caching-layer)
31. [Media Download Service](#31-media-download-service)
32. [Installation & Deployment Guide](#32-installation--deployment-guide)
33. [Server Requirements & Tuning](#33-server-requirements--tuning)
34. [PostgreSQL Tuning](#34-postgresql-tuning)
35. [PgBouncer Connection Pooling](#35-pgbouncer-connection-pooling)
36. [Migration from SQLite to PostgreSQL](#36-migration-from-sqlite-to-postgresql)
37. [Future Update Roadmap](#37-future-update-roadmap)
38. [Quick Reference Tables](#38-quick-reference-tables)

---

## 1. Project Overview

The **Telegram Music Player Bot** is an advanced, subscription-based Telegram bot that enables playing music and video inside **Voice Chats (Group Calls)** of both groups and channels. It is designed as a **SaaS (Software as a Service)** product with a strict hierarchical role system.

### Business Model

The bot is sold as a **subscription service** by the Developer (bot builder) through Owners and Sudos (sales representatives) to group/channel administrators. Each group or channel pays a credit-based subscription (per day) to keep the player active.

```
Developer → sells to → Owner(s)
Owner(s)  → assigns  → Sudo(s) (sales reps)
Sudo(s)   → installs → Groups / Channels (customers)
```

### Core Capabilities

- 🎵 Audio playback in Telegram Voice Chats
- 🎬 Video playback in Telegram Video Chats
- 📺 Live TV streaming (Iranian domestic + satellite channels)
- 📻 Internet radio streaming
- 🔗 YouTube/link stream playback (via yt-dlp)
- 📋 Full playlist/queue management
- 💳 Per-group/channel credit subscription system
- 👑 4-tier admin hierarchy (Developer / Owner / Sudo / Group-level)
- 📢 Broadcast messaging (private, group, channel)
- 🔒 Force-join membership gate
- 🚫 Blacklist, word filter, and rate limiting
- 📊 Reporting, logging, and audit trail
- 🤖 Auto-leave on credit expiry
- ⏱ 3-day free trial on first installation

---

## 2. System Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Telegram MTProto API                        │
└───────────────────────┬──────────────────────────────────────────────┘
                        │
         ┌──────────────┼───────────────┐
         ▼              ▼               ▼
   ┌──────────┐   ┌──────────┐   ┌───────────────┐
   │  Bot     │   │  Helper  │   │  PyTgCalls    │
   │ (Pyrogram│   │ Account  │   │  (VoiceChat   │
   │  Bot API)│   │(Pyrogram │   │   Streaming)  │
   └────┬─────┘   │  User)   │   └──────┬────────┘
        │         └────┬─────┘          │
        │              │                │
        └──────┬────────┘               │
               ▼                        │
       ┌───────────────────┐            │
       │   Handler Layer   │◄───────────┘
       │  (Modular Files)  │
       └────────┬──────────┘
                │
   ┌────────────▼────────────┐
   │     Service Layer       │
   │  (Business Logic)       │
   │  - CreditService        │
   │  - MediaService         │
   │  - BroadcastService     │
   │  - NotificationService  │
   │  - CallService          │
   └────────────┬────────────┘
                │
   ┌────────────▼────────────┐      ┌──────────────────┐
   │    Repository Layer     │      │   Redis Cache    │
   │  (Data Access Objects)  │◄────►│  (Settings,      │
   └────────────┬────────────┘      │   Credits, etc.) │
                │                   └──────────────────┘
   ┌────────────▼────────────┐
   │   PostgreSQL Database   │
   │  (asyncpg + SQLAlchemy) │
   └─────────────────────────┘
```

### Data Flow - Playback Request

```
User sends "پخش" (Play) in group
        │
        ▼
Handler checks: Is bot admin in group?
        │ YES
        ▼
Check: Group has credit? (Redis → DB fallback)
        │ YES
        ▼
Check: Is user a music_admin or above?
        │ YES
        ▼
Check: Is message a reply to an audio file?
        │ YES
        ▼
MediaService: Download / get stream URL
        │
        ▼
CallService: Join VC or change stream
        │
        ▼
Send playback message with inline controls
        │
        ▼
Log to call_reports table
```

---

## 3. Technology Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| Language | Python | 3.11+ | Core runtime |
| Bot Framework | Pyrogram | 2.0.x | Telegram Bot & User API |
| Voice Chat | PyTgCalls | latest | Group Call streaming |
| Interactive Input | Pyromod | 0.1.4 | `await c.ask()` conversations |
| Database | PostgreSQL | 16 | Primary data store |
| ORM | SQLAlchemy | 2.0 async | Object-relational mapping |
| DB Driver | asyncpg | 0.29 | Async PostgreSQL driver |
| Connection Pooler | PgBouncer | latest | DB connection pooling |
| Cache | Redis | 7 | Settings & credit cache |
| Redis Client | redis-py | 5.x | Async Redis client |
| Scheduler | APScheduler | 3.10 | Cron jobs (credit check, cleanup) |
| Media Download | yt-dlp | latest | YouTube & link downloads |
| Media Processing | FFmpeg | 6.x | Audio/video transcoding |
| Migrations | Alembic | 1.13 | Database schema versioning |
| Logging | Loguru | 0.7 | Structured file + channel logging |
| Date (Persian) | jdatetime | 4.1 | Persian (Jalali) calendar |
| Env Config | python-dotenv | 1.0 | Load `.env` file |
| HTTP Client | aiohttp | 3.9 | Async HTTP requests |
| Process Manager | systemd | - | Keep bot alive on server |

---

## 4. Project File Structure

```
music_bot/
│
├── main.py                        # Entry point - wires everything together
├── config.py                      # Reads config/config.env → settings object
├── requirements.txt               # All Python dependencies
├── Dockerfile                     # Docker build (optional)
├── docker-compose.yml             # Full stack (bot + db + redis)
├── alembic.ini                    # Alembic migration config
├── .env.example                   # Template for config.env
│
├── config/
│   └── config.env                 # ★ THE ONLY FILE YOU EDIT TO CONFIGURE THE BOT
│
├── database/
│   ├── __init__.py
│   ├── engine.py                  # Async SQLAlchemy engine + session factory
│   ├── models.py                  # All SQLAlchemy table models
│   ├── create_db.sql              # Raw SQL to create DB and user (run once)
│   ├── migrate_sqlite_to_pg.py    # One-time migration script from old SQLite
│   └── migrations/                # Alembic auto-generated migration files
│       ├── env.py
│       ├── script.py.mako
│       └── versions/
│           ├── 0001_initial_schema.py
│           └── 0002_add_favorites.py
│
├── repositories/                  # Data Access Layer - all DB queries live here
│   ├── __init__.py
│   ├── group_repo.py              # CRUD for groups & channels
│   ├── credit_repo.py             # Credit read/write, expiry checks
│   ├── user_repo.py               # User, Owner, Sudo management
│   ├── settings_repo.py           # Bot settings & chat settings
│   ├── admin_repo.py              # Music/video/player admins
│   ├── playlist_repo.py           # Queue / playlist management
│   ├── blacklist_repo.py          # Blacklist management
│   ├── filter_repo.py             # Word filter management
│   └── log_repo.py                # Install logs, call reports
│
├── services/                      # Business Logic Layer
│   ├── __init__.py
│   ├── call_service.py            # PyTgCalls management (join, leave, stream)
│   ├── credit_service.py          # Charge, deduct, trial activation logic
│   ├── media_service.py           # yt-dlp download, FFmpeg conversion
│   ├── broadcast_service.py       # Mass send/forward to groups/users/channels
│   └── notification_service.py    # Sends reports to log channel & developer
│
├── handlers/                      # Telegram message & callback handlers
│   ├── __init__.py                # register_all() function
│   ├── dev_panel.py               # Developer panel commands
│   ├── owner_panel.py             # Owner panel commands
│   ├── sudo_panel.py              # Sudo panel commands
│   ├── group_panel.py             # In-group settings & management
│   ├── start.py                   # /start menu handler
│   ├── playback.py                # Play, Stop, Pause, Resume, Volume, Speed
│   ├── playlist.py                # Queue management commands
│   ├── tv_radio.py                # TV channel & radio playback
│   ├── search.py                  # YouTube search and inline results
│   ├── download.py                # Media download handler
│   ├── promotion.py               # Promote/demote music & video admins
│   ├── broadcast.py               # Mass broadcast handlers
│   ├── force_join.py              # Force-join membership gate
│   └── callbacks.py               # All InlineKeyboard callback_data handlers
│
├── utils/
│   ├── __init__.py
│   ├── filters.py                 # Custom Pyrogram filters
│   ├── decorators.py              # Access-control decorators
│   ├── keyboards.py               # All InlineKeyboardMarkup builders
│   ├── messages.py                # All message text templates (centralized)
│   ├── helpers.py                 # Misc helpers (time formatting, ID parsing)
│   └── logger.py                  # Loguru setup + Telegram log channel sink
│
├── assets/
│   ├── tv_channels.json           # List of TV channel stream URLs
│   └── radio_stations.json        # List of radio station stream URLs
│
├── sessions/                      # Pyrogram session files (.session)
│   └── .gitkeep
│
├── downloads/                     # Temporary media download directory
│   └── .gitkeep
│
└── logs/
    ├── bot.log                    # Main application log
    └── bot_error.log              # Error-only log
```

---

## 5. Configuration File (config.env)

This is **the single file** that needs to be edited to deploy the bot. All other settings are managed via bot commands.

```env
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                TELEGRAM CREDENTIALS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Bot token from @BotFather
BOT_TOKEN=123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# API credentials from https://my.telegram.org
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890

# Helper userbot phone number (with country code)
# This account streams audio/video into voice chats
HELPER_PHONE=+989123456789

# Numeric Telegram user ID of the Developer (bot builder)
# This ID has the highest privilege and is set at code level
DEVELOPER_ID=123456789

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                DATABASE CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DATABASE_URL=postgresql+asyncpg://botuser:StrongPassword123@localhost:5432/musicbot_db

# Connection pool settings (tune based on server RAM)
DB_POOL_SIZE=20          # base pool connections
DB_MAX_OVERFLOW=40       # extra connections allowed above pool_size
DB_POOL_TIMEOUT=30       # seconds to wait for a connection before error
DB_POOL_RECYCLE=1800     # recycle connections every 30 minutes

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                REDIS CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REDIS_URL=redis://localhost:6379/0
REDIS_SETTINGS_TTL=300   # cache settings for 5 minutes
REDIS_CREDIT_TTL=60      # cache credit balance for 1 minute

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                FILE PATHS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DOWNLOADS_PATH=/root/music_bot/downloads/
SESSION_PATH=/root/music_bot/sessions/
LOG_FILE=/root/music_bot/logs/bot.log

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                CREDIT & PRICING DEFAULTS
#         (All can be changed later via bot commands)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TRIAL_DAYS=3                    # free trial duration in days
BASE_CREDIT_RATE=50000          # base rate in Tomans
MUSIC_RATE=10000                # music credit per day (Tomans)
VIDEO_RATE=25000                # video credit per day (Tomans)
SECURITY_CALL_RATE=15000        # call security per day (Tomans)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                INSTALLATION LIMITS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Minimum member count required to install in a group (0 = no limit)
MAX_GROUP_MEMBERS=0

# Minimum admin count required to install in a channel (0 = no limit)
MAX_CHANNEL_ADMINS=0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                DEFAULT LINKS & TEXTS
#         (All can be overridden via bot commands)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LOG_CHANNEL_ID=-100123456789    # Telegram channel ID for install/error logs
START_TEXT=سلام MENTION عزیز! 🎵 به ربات موزیک پلیر خوش آمدی
DEVELOPER_LINK=https://t.me/developer_username
BOT_CHANNEL_LINK=https://t.me/bot_channel
SUPPORT_GROUP_LINK=https://t.me/support_group
GUIDE_CHANNEL_LINK=https://t.me/guide_channel

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                MEDIA SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VIDEO_QUALITY=720               # max video height for yt-dlp downloads
MAX_DOWNLOAD_SIZE_MB=200        # reject downloads above this size
DOWNLOAD_SEMAPHORE=10           # max concurrent downloads across all chats

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#                LOGGING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

LOG_LEVEL=INFO                  # DEBUG / INFO / WARNING / ERROR
```

---



### 5.X Addendum env variables (required by Addendum A)

Append these variables to `config.env`:

```env
# ---------- Helper pool session encryption ----------
HELPER_SESSION_KEY_CURRENT=base64_32_bytes_key
# Optional old key for rotation reads
# HELPER_SESSION_KEY_OLD=base64_32_bytes_key

HELPER_DEFAULT_MAX_CALLS=50
HELPER_DEFAULT_MAX_JOINS_PER_HOUR=300

# ---------- Recovery rate limits (startup resume) ----------
RECOVERY_GLOBAL_PER_SECOND=5
RECOVERY_MAX_CONCURRENT=10
RECOVERY_JITTER_MS=150
RECOVERY_HELPER_PER_MINUTE=120

# ---------- Backups ----------
BACKUP_DIR=/var/backups/musicbot
BACKUP_RETENTION_DAYS=7

# ---------- Sudo wallet alerts ----------
SUDO_WALLET_LOW_THRESHOLD_DAYS=7
```
## 6. PostgreSQL Database - Full Schema

### Initial Setup

```sql
-- Run as PostgreSQL superuser
CREATE USER botuser WITH PASSWORD 'StrongPassword123';
CREATE DATABASE musicbot_db OWNER botuser ENCODING 'UTF8' LC_COLLATE 'en_US.UTF-8';
GRANT ALL PRIVILEGES ON DATABASE musicbot_db TO botuser;

-- Required extensions
\c musicbot_db
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- fuzzy text search
CREATE EXTENSION IF NOT EXISTS btree_gin;   -- multi-column GIN indexes
CREATE EXTENSION IF NOT EXISTS pg_partman;  -- table partitioning (for large tables)
```

---

### Table: `groups`
Stores every group where the bot has been installed.

```sql
CREATE TABLE groups (
    id             BIGSERIAL        PRIMARY KEY,
    chat_id        BIGINT           NOT NULL UNIQUE,
    chat_title     VARCHAR(255),
    invite_link    TEXT,
    -- 0 = bot removed/inactive, 1 = active
    status         SMALLINT         NOT NULL DEFAULT 1,
    installed_at   TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    -- references sudos.user_id - the sudo who installed the player
    installed_by   BIGINT,
    last_activity  TIMESTAMPTZ,
    member_count   INT              DEFAULT 0,

    CONSTRAINT fk_groups_sudo
        FOREIGN KEY (installed_by)
        REFERENCES sudos(user_id)
        ON DELETE SET NULL
);
COMMENT ON TABLE groups IS 'All Telegram groups where the music player has been installed';
COMMENT ON COLUMN groups.status IS '0=inactive/removed, 1=active';
```

---

### Table: `channels`
Stores every channel where the bot has been installed.

```sql
CREATE TABLE channels (
    id             BIGSERIAL        PRIMARY KEY,
    chat_id        BIGINT           NOT NULL UNIQUE,
    chat_title     VARCHAR(255),
    invite_link    TEXT,
    status         SMALLINT         NOT NULL DEFAULT 1,
    installed_at   TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    installed_by   BIGINT,
    last_activity  TIMESTAMPTZ,
    admin_count    INT              DEFAULT 0,

    CONSTRAINT fk_channels_sudo
        FOREIGN KEY (installed_by)
        REFERENCES sudos(user_id)
        ON DELETE SET NULL
);
COMMENT ON TABLE channels IS 'All Telegram channels where the music player has been installed';
```

---

### Table: `group_credits`
The central billing table. Every group/channel has exactly one row here.

```sql
CREATE TABLE group_credits (
    id               BIGSERIAL    PRIMARY KEY,
    chat_id          BIGINT       NOT NULL UNIQUE,
    -- 'group' or 'channel'
    chat_type        VARCHAR(10)  NOT NULL DEFAULT 'group',
    -- days of credit remaining (decremented daily by cron)
    credit_days      INT          NOT NULL DEFAULT 0,
    -- computed expiry timestamp (set on each charge)
    expire_at        TIMESTAMPTZ,
    -- user_id who last charged this group/channel
    charged_by       BIGINT,
    -- cumulative total days ever charged
    total_charged    INT          NOT NULL DEFAULT 0,
    -- whether this group is currently on the free trial
    is_trial         BOOLEAN      NOT NULL DEFAULT FALSE,
    trial_started_at TIMESTAMPTZ,
    trial_expire_at  TIMESTAMPTZ,
    -- 0=no credit/expired, 1=active, 2=trial_active
    status           SMALLINT     NOT NULL DEFAULT 1
);
COMMENT ON TABLE group_credits IS
    'Subscription credits for each group/channel. '
    'credit_days is decremented by 1 daily via cron. '
    'status=0 triggers auto-leave if enabled.';
```

---

### Table: `credit_history`
Full audit log of every credit add/deduct operation.

```sql
CREATE TABLE credit_history (
    id           BIGSERIAL    PRIMARY KEY,
    chat_id      BIGINT       NOT NULL,
    chat_type    VARCHAR(10)  NOT NULL DEFAULT 'group',
    -- 'add' or 'deduct'
    operation    VARCHAR(10)  NOT NULL,
    amount_days  INT          NOT NULL,
    operated_by  BIGINT       NOT NULL,
    operated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- optional note e.g. "trial", "manual deduct", "renewal"
    note         TEXT,
    invoice_id   BIGINT
)
-- Partitioned by month for efficient querying on large datasets
PARTITION BY RANGE (operated_at);

-- Create first partition (extend monthly with pg_partman)
CREATE TABLE credit_history_2026_01
    PARTITION OF credit_history
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');

CREATE TABLE credit_history_2026_02
    PARTITION OF credit_history
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');

-- Set up pg_partman for automatic monthly partition creation
SELECT partman.create_parent(
    p_parent_table  := 'public.credit_history',
    p_control       := 'operated_at',
    p_type          := 'range',
    p_interval      := 'monthly',
    p_premake       := 3
);
```

---

### Table: `invoices`
Tracks payment invoices issued by sudo/owner to groups/channels.

```sql
CREATE TABLE invoices (
    id           BIGSERIAL    PRIMARY KEY,
    chat_id      BIGINT       NOT NULL,
    chat_type    VARCHAR(10)  NOT NULL DEFAULT 'group',
    -- invoice amount in Tomans
    amount       BIGINT       NOT NULL,
    -- days of credit this invoice covers
    days         INT          NOT NULL,
    -- user_id of sudo/owner who issued it
    issued_by    BIGINT       NOT NULL,
    issued_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    paid_at      TIMESTAMPTZ,
    -- 'pending', 'paid', 'cancelled'
    status       VARCHAR(20)  NOT NULL DEFAULT 'pending'
);
COMMENT ON TABLE invoices IS 'Payment invoices for group/channel credit purchases';
```

---

### Table: `users`
All users who have ever interacted with the bot in private.

```sql
CREATE TABLE users (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     BIGINT       NOT NULL UNIQUE,
    username    VARCHAR(100),
    first_name  VARCHAR(255),
    is_banned   BOOLEAN      NOT NULL DEFAULT FALSE,
    banned_at   TIMESTAMPTZ,
    banned_by   BIGINT,
    joined_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen   TIMESTAMPTZ
);
COMMENT ON TABLE users IS 'All users who have started the bot in private';
```

---

### Table: `owners`
Bot owners (مالک ربات). Added by Developer only.

```sql
CREATE TABLE owners (
    id              BIGSERIAL    PRIMARY KEY,
    user_id         BIGINT       NOT NULL UNIQUE,
    username        VARCHAR(100),
    display_name    VARCHAR(255),
    -- always the developer ID
    added_by        BIGINT       NOT NULL,
    added_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    deactivated_at  TIMESTAMPTZ
);
COMMENT ON TABLE owners IS 'Bot owners - second highest privilege tier';
```

---

### Table: `sudos`
Sales representatives / resellers. Added by Owner or Developer.

```sql
CREATE TABLE sudos (
    id             BIGSERIAL    PRIMARY KEY,
    user_id        BIGINT       NOT NULL UNIQUE,
    username       VARCHAR(100),
    display_name   VARCHAR(255),
    -- owner_id or developer_id who added this sudo
    added_by       BIGINT       NOT NULL,
    added_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- the sudo's custom purchase link (shown in start menu)
    sudo_link      TEXT,
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    deactivated_at TIMESTAMPTZ,
    -- total groups + channels installed by this sudo
    total_installs INT          NOT NULL DEFAULT 0
);
COMMENT ON TABLE sudos IS
    'Sudos are sales representatives who install players in groups/channels '
    'and manage their own customer base';
```

---

### Table: `music_admins`
Per-group music administrators (can control music playback).

```sql
CREATE TABLE music_admins (
    id           BIGSERIAL    PRIMARY KEY,
    chat_id      BIGINT       NOT NULL,
    user_id      BIGINT       NOT NULL,
    username     VARCHAR(100),
    display_name VARCHAR(255),
    promoted_by  BIGINT,
    promoted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (chat_id, user_id)
);
COMMENT ON TABLE music_admins IS
    'Users promoted to music admin in a specific group. '
    'Can control audio playback (play, stop, pause, queue).';
```

---

### Table: `video_admins`
Per-group video administrators (can control video playback).

```sql
CREATE TABLE video_admins (
    id           BIGSERIAL    PRIMARY KEY,
    chat_id      BIGINT       NOT NULL,
    user_id      BIGINT       NOT NULL,
    username     VARCHAR(100),
    display_name VARCHAR(255),
    promoted_by  BIGINT,
    promoted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (chat_id, user_id)
);
COMMENT ON TABLE video_admins IS
    'Users promoted to video admin in a specific group. '
    'Can control video playback.';
```

---

### Table: `player_owners`
Per-group player owners (مالکان پلیر). Highest admin level within a group.

```sql
CREATE TABLE player_owners (
    id           BIGSERIAL    PRIMARY KEY,
    chat_id      BIGINT       NOT NULL,
    user_id      BIGINT       NOT NULL,
    username     VARCHAR(100),
    display_name VARCHAR(255),
    promoted_by  BIGINT,
    promoted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (chat_id, user_id)
);
COMMENT ON TABLE player_owners IS
    'Player owners within a group - highest in-group admin tier. '
    'Can manage group settings, promote/demote other admins.';
```

---

### Table: `player_vips`
Per-group VIP users (ویژه‌ها). Special privileges below admin.

```sql
CREATE TABLE player_vips (
    id           BIGSERIAL    PRIMARY KEY,
    chat_id      BIGINT       NOT NULL,
    user_id      BIGINT       NOT NULL,
    username     VARCHAR(100),
    display_name VARCHAR(255),
    promoted_by  BIGINT,
    promoted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (chat_id, user_id)
);
COMMENT ON TABLE player_vips IS
    'VIP users in a group - can use player features not available to regular users';
```

---

### Table: `chat_settings`
All configurable settings for each group or channel.

```sql
CREATE TABLE chat_settings (
    id                         BIGSERIAL    PRIMARY KEY,
    chat_id                    BIGINT       NOT NULL UNIQUE,
    -- 'group' or 'channel'
    chat_type                  VARCHAR(10)  NOT NULL DEFAULT 'group',

    -- ── Media type permissions ────────────────────────────────────
    music_enabled              BOOLEAN      NOT NULL DEFAULT TRUE,
    video_enabled              BOOLEAN      NOT NULL DEFAULT TRUE,
    -- Allow regular users (non-admins) to download streamed media
    download_for_users         BOOLEAN      NOT NULL DEFAULT FALSE,

    -- ── Playback behavior ────────────────────────────────────────
    repeat_mode                BOOLEAN      NOT NULL DEFAULT FALSE,
    auto_next                  BOOLEAN      NOT NULL DEFAULT TRUE,
    queue_enabled              BOOLEAN      NOT NULL DEFAULT TRUE,
    -- Automatically start a voice call when the group becomes active
    auto_ready_call            BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Default media type when user just says "پخش" without specifying
    default_media_type         VARCHAR(10)  NOT NULL DEFAULT 'audio',

    -- ── Security ─────────────────────────────────────────────────
    call_security_enabled      BOOLEAN      NOT NULL DEFAULT FALSE,

    -- ── Messaging ────────────────────────────────────────────────
    -- Show the "Now Playing" message in group chat
    call_message_enabled       BOOLEAN      NOT NULL DEFAULT TRUE,
    -- Delete "Now Playing" messages when track ends
    auto_clean                 BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Forward call start/end reports to log channel
    call_report_enabled        BOOLEAN      NOT NULL DEFAULT FALSE,

    -- ── Recording ────────────────────────────────────────────────
    record_call_enabled        BOOLEAN      NOT NULL DEFAULT FALSE,

    -- ── Display options on Now Playing card ──────────────────────
    show_id                    BOOLEAN      NOT NULL DEFAULT TRUE,
    show_photo                 BOOLEAN      NOT NULL DEFAULT TRUE,
    show_text                  BOOLEAN      NOT NULL DEFAULT TRUE,

    -- ── Access control ───────────────────────────────────────────
    force_join_enabled         BOOLEAN      NOT NULL DEFAULT FALSE,
    -- Automatically leave group if credit expires
    auto_leave_no_credit       BOOLEAN      NOT NULL DEFAULT TRUE,

    updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE chat_settings IS 'Per-group or per-channel configurable settings';
```

---

### Table: `playlists`
The playback queue for each active group/channel.

```sql
CREATE TABLE playlists (
    id               BIGSERIAL    PRIMARY KEY,
    chat_id          BIGINT       NOT NULL,
    -- position in the queue (0 = currently playing, 1 = next, etc.)
    position         SMALLINT     NOT NULL DEFAULT 0,
    -- local filesystem path (null if stream_url is set)
    file_path        TEXT,
    -- remote stream URL (null if file_path is set)
    stream_url       TEXT,
    title            VARCHAR(500),
    duration_seconds INT,
    -- 'audio', 'video', 'tv', 'radio', 'satellite'
    media_type       VARCHAR(10)  NOT NULL DEFAULT 'audio',
    added_by         BIGINT,
    added_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (chat_id, position)
);
COMMENT ON TABLE playlists IS 'Active playback queue for each group/channel';
```

---

### Table: `favorites`
Users' saved favorite tracks per group.

```sql
CREATE TABLE favorites (
    id               BIGSERIAL    PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    chat_id          BIGINT       NOT NULL,
    title            VARCHAR(500),
    stream_url       TEXT,
    duration_seconds INT,
    -- 'audio', 'video'
    media_type       VARCHAR(10)  NOT NULL DEFAULT 'audio',
    added_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, chat_id, stream_url)
);
COMMENT ON TABLE favorites IS 'User-saved favorite tracks per group';
```

---

### Table: `bot_settings`
Global key-value store for all bot-wide settings (editable via bot commands).

```sql
CREATE TABLE bot_settings (
    key         VARCHAR(100)  PRIMARY KEY,
    value       TEXT          NOT NULL,
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    -- user_id who last changed this setting
    updated_by  BIGINT
);
COMMENT ON TABLE bot_settings IS
    'Global bot configuration. All values editable via admin commands. '
    'Acts as a database-backed config system (no restarts needed).';

-- Default values - inserted on first run
INSERT INTO bot_settings (key, value) VALUES
    -- ── Text / Content ─────────────────────────────────────────
    ('start_text',              'سلام MENTION عزیز! 🎵'),
    ('helper_start_text',       'Hi MENTION'),
    ('about_text',              'متن درباره ما'),
    ('helper_text',             'متن راهنما'),
    ('tariff_text',             'متن تعرفه'),
    -- ── Links ──────────────────────────────────────────────────
    ('developer_link',          'https://t.me/developer'),
    ('developer_pv_link',       'developer_username'),
    ('bot_channel_link',        'https://t.me/bot_channel'),
    ('support_group_link',      'https://t.me/support'),
    ('guide_channel_link',      'https://t.me/guide'),
    ('broadcast_channel_link',  'https://t.me/payamresan'),
    ('custom_link',             ''),
    -- ── Sudo purchase links (JSON array of {id, link}) ─────────
    ('sudo_links',              '[]'),
    -- ── Feature Toggles ────────────────────────────────────────
    ('trial_enabled',           'true'),
    ('trial_days',              '3'),
    ('auto_leave_enabled',      'true'),
    ('force_join_enabled',      'false'),
    ('install_limit_enabled',   'false'),
    -- ── Pricing ────────────────────────────────────────────────
    ('base_rate',               '50000'),
    ('music_rate',              '10000'),
    ('music_sell_rate',         '10000'),
    ('video_rate',              '25000'),
    ('video_sell_rate',         '25000'),
    ('security_call_rate',      '15000'),
    -- ── Installation Limits ────────────────────────────────────
    ('max_group_members',       '0'),
    ('max_channel_admins',      '0'),
    -- ── Channels ───────────────────────────────────────────────
    ('log_channel_id',          '0'),
    ('force_join_channel_id',   '0'),
    ('force_join_channel_link', ''),
    -- ── Filters ────────────────────────────────────────────────
    ('filter_words',            '[]'),
    ('blocked_chats',           '[]'),
    ('blocked_users',           '[]');
```

---

### Table: `force_join_channels`
Channels users must join before using the bot.

```sql
CREATE TABLE force_join_channels (
    id               BIGSERIAL    PRIMARY KEY,
    channel_id       BIGINT       NOT NULL UNIQUE,
    channel_username VARCHAR(100),
    invite_link      TEXT,
    added_by         BIGINT,
    added_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE
);
```

---

### Table: `filter_words`
Words that trigger auto-deletion of messages containing them.

```sql
CREATE TABLE filter_words (
    id        BIGSERIAL    PRIMARY KEY,
    word      VARCHAR(200) NOT NULL UNIQUE,
    added_by  BIGINT,
    added_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
```

---

### Table: `blacklist`
Blocked groups, channels, and users.

```sql
CREATE TABLE blacklist (
    id           BIGSERIAL    PRIMARY KEY,
    entity_id    BIGINT       NOT NULL,
    -- 'group', 'channel', 'user'
    entity_type  VARCHAR(10)  NOT NULL,
    reason       TEXT,
    blocked_by   BIGINT,
    blocked_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    unblocked_at TIMESTAMPTZ,
    is_active    BOOLEAN      NOT NULL DEFAULT TRUE,

    UNIQUE (entity_id, entity_type)
);
COMMENT ON TABLE blacklist IS 'Blocked entities - bot refuses service to blacklisted groups, channels, and users';
```

---

### Table: `install_logs`
Audit log for every installation and uninstallation event.

```sql
CREATE TABLE install_logs (
    id          BIGSERIAL    PRIMARY KEY,
    chat_id     BIGINT       NOT NULL,
    chat_title  VARCHAR(255),
    -- 'group' or 'channel'
    chat_type   VARCHAR(10)  NOT NULL DEFAULT 'group',
    -- Telegram user who caused the event (added/removed bot)
    triggered_by BIGINT,
    -- sudo_id responsible for this chat (if any)
    sudo_id      BIGINT,
    -- 'install' or 'uninstall'
    action       VARCHAR(20)  NOT NULL,
    occurred_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE install_logs IS 'Full audit log of bot install/uninstall events';
```

---

### Table: `call_reports`
Log of every voice/video call playback session.

```sql
CREATE TABLE call_reports (
    id               BIGSERIAL    PRIMARY KEY,
    chat_id          BIGINT       NOT NULL,
    chat_title       VARCHAR(255),
    title            VARCHAR(500),
    -- 'audio', 'video', 'tv', 'radio', 'satellite'
    media_type       VARCHAR(10),
    -- user_id who triggered the play
    played_by        BIGINT,
    duration_seconds INT,
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    ended_at         TIMESTAMPTZ
);
COMMENT ON TABLE call_reports IS 'Log of every playback session per group/channel';
```

---

## 7. Database Indexes & Optimization

```sql
-- ── groups ───────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_groups_status
    ON groups(status);
CREATE INDEX CONCURRENTLY idx_groups_installed_by
    ON groups(installed_by);
CREATE INDEX CONCURRENTLY idx_groups_installed_at
    ON groups(installed_at DESC);

-- ── channels ─────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_channels_status
    ON channels(status);
CREATE INDEX CONCURRENTLY idx_channels_installed_by
    ON channels(installed_by);

-- ── group_credits (most critical table for cron performance) ─────────
-- Primary lookup: find credit by chat_id
CREATE INDEX CONCURRENTLY idx_group_credits_chat_id
    ON group_credits(chat_id);

-- Partial index: find ACTIVE records nearing expiry (cron warning job)
CREATE INDEX CONCURRENTLY idx_group_credits_expiring_soon
    ON group_credits(expire_at ASC)
    WHERE status = 1 AND expire_at IS NOT NULL;

-- Partial index: find all expired / zero-credit records (auto-leave cron)
CREATE INDEX CONCURRENTLY idx_group_credits_zero
    ON group_credits(chat_id)
    WHERE credit_days <= 0;

-- Partial index: find trial records (trial expiry cron)
CREATE INDEX CONCURRENTLY idx_group_credits_trial
    ON group_credits(trial_expire_at ASC)
    WHERE is_trial = TRUE;

CREATE INDEX CONCURRENTLY idx_group_credits_charged_by
    ON group_credits(charged_by);

-- ── credit_history ───────────────────────────────────────────────────
-- Paginated history per group/channel
CREATE INDEX CONCURRENTLY idx_credit_history_chat_date
    ON credit_history(chat_id, operated_at DESC);

-- Sudo's own operation history
CREATE INDEX CONCURRENTLY idx_credit_history_operator
    ON credit_history(operated_by, operated_at DESC);

-- ── users ────────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_users_banned
    ON users(is_banned)
    WHERE is_banned = TRUE;

CREATE INDEX CONCURRENTLY idx_users_joined
    ON users(joined_at DESC);

-- ── sudos ────────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_sudos_active
    ON sudos(is_active)
    WHERE is_active = TRUE;

-- ── music_admins / video_admins ───────────────────────────────────────
-- Used on every playback permission check (very hot path)
CREATE INDEX CONCURRENTLY idx_music_admins_chat
    ON music_admins(chat_id);
CREATE INDEX CONCURRENTLY idx_music_admins_user_chat
    ON music_admins(user_id, chat_id);

CREATE INDEX CONCURRENTLY idx_video_admins_chat
    ON video_admins(chat_id);
CREATE INDEX CONCURRENTLY idx_video_admins_user_chat
    ON video_admins(user_id, chat_id);

-- ── player_owners / player_vips ──────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_player_owners_chat
    ON player_owners(chat_id);
CREATE INDEX CONCURRENTLY idx_player_owners_user_chat
    ON player_owners(user_id, chat_id);

CREATE INDEX CONCURRENTLY idx_player_vips_chat
    ON player_vips(chat_id);
CREATE INDEX CONCURRENTLY idx_player_vips_user_chat
    ON player_vips(user_id, chat_id);

-- ── chat_settings ────────────────────────────────────────────────────
-- Very hot read path - almost every message triggers a settings lookup
CREATE INDEX CONCURRENTLY idx_chat_settings_chat_id
    ON chat_settings(chat_id);  -- already unique, but explicit for clarity

-- ── playlists ────────────────────────────────────────────────────────
-- Core queue lookup: "give me the next track for chat_id"
CREATE INDEX CONCURRENTLY idx_playlists_chat_position
    ON playlists(chat_id, position ASC);

CREATE INDEX CONCURRENTLY idx_playlists_added_by
    ON playlists(added_by);

-- ── favorites ────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_favorites_user_chat
    ON favorites(user_id, chat_id);

-- ── blacklist ────────────────────────────────────────────────────────
-- Checked on every group message (ensure not blocked)
CREATE INDEX CONCURRENTLY idx_blacklist_entity_active
    ON blacklist(entity_id, entity_type)
    WHERE is_active = TRUE;

-- ── install_logs ─────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_install_logs_sudo_date
    ON install_logs(sudo_id, occurred_at DESC);
CREATE INDEX CONCURRENTLY idx_install_logs_chat
    ON install_logs(chat_id);
CREATE INDEX CONCURRENTLY idx_install_logs_date
    ON install_logs(occurred_at DESC);

-- ── call_reports ─────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_call_reports_chat_date
    ON call_reports(chat_id, started_at DESC);
CREATE INDEX CONCURRENTLY idx_call_reports_date
    ON call_reports(started_at DESC);

-- ── invoices ─────────────────────────────────────────────────────────
CREATE INDEX CONCURRENTLY idx_invoices_chat
    ON invoices(chat_id, issued_at DESC);
CREATE INDEX CONCURRENTLY idx_invoices_issued_by
    ON invoices(issued_by);
CREATE INDEX CONCURRENTLY idx_invoices_status
    ON invoices(status)
    WHERE status = 'pending';
```

---

## 8. ORM Models (SQLAlchemy)

```python
# database/models.py

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, SmallInteger, String, Text, UniqueConstraint, func
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all models"""
    pass


class Group(Base):
    __tablename__ = "groups"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id       = Column(BigInteger, nullable=False, unique=True, index=True)
    chat_title    = Column(String(255))
    invite_link   = Column(Text)
    status        = Column(SmallInteger, nullable=False, default=1)
    installed_at  = Column(DateTime(timezone=True), server_default=func.now())
    installed_by  = Column(BigInteger)
    last_activity = Column(DateTime(timezone=True))
    member_count  = Column(Integer, default=0)

    credit   = relationship(
        "GroupCredit",
        primaryjoin="Group.chat_id == GroupCredit.chat_id",
        foreign_keys="GroupCredit.chat_id",
        uselist=False,
        lazy="selectin"
    )
    settings = relationship(
        "ChatSettings",
        primaryjoin="Group.chat_id == ChatSettings.chat_id",
        foreign_keys="ChatSettings.chat_id",
        uselist=False,
        lazy="selectin"
    )


class GroupCredit(Base):
    __tablename__ = "group_credits"

    id               = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id          = Column(BigInteger, nullable=False, unique=True, index=True)
    chat_type        = Column(String(10), nullable=False, default='group')
    credit_days      = Column(Integer, nullable=False, default=0)
    expire_at        = Column(DateTime(timezone=True))
    charged_by       = Column(BigInteger)
    total_charged    = Column(Integer, nullable=False, default=0)
    is_trial         = Column(Boolean, nullable=False, default=False)
    trial_started_at = Column(DateTime(timezone=True))
    trial_expire_at  = Column(DateTime(timezone=True))
    status           = Column(SmallInteger, nullable=False, default=1)


class ChatSettings(Base):
    __tablename__ = "chat_settings"

    id                    = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id               = Column(BigInteger, nullable=False, unique=True, index=True)
    chat_type             = Column(String(10), nullable=False, default='group')
    music_enabled         = Column(Boolean, nullable=False, default=True)
    video_enabled         = Column(Boolean, nullable=False, default=True)
    download_for_users    = Column(Boolean, nullable=False, default=False)
    repeat_mode           = Column(Boolean, nullable=False, default=False)
    auto_next             = Column(Boolean, nullable=False, default=True)
    queue_enabled         = Column(Boolean, nullable=False, default=True)
    auto_ready_call       = Column(Boolean, nullable=False, default=False)
    default_media_type    = Column(String(10), nullable=False, default='audio')
    call_security_enabled = Column(Boolean, nullable=False, default=False)
    call_message_enabled  = Column(Boolean, nullable=False, default=True)
    auto_clean            = Column(Boolean, nullable=False, default=False)
    call_report_enabled   = Column(Boolean, nullable=False, default=False)
    record_call_enabled   = Column(Boolean, nullable=False, default=False)
    show_id               = Column(Boolean, nullable=False, default=True)
    show_photo            = Column(Boolean, nullable=False, default=True)
    show_text             = Column(Boolean, nullable=False, default=True)
    force_join_enabled    = Column(Boolean, nullable=False, default=False)
    auto_leave_no_credit  = Column(Boolean, nullable=False, default=True)
    updated_at            = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )


class Playlist(Base):
    __tablename__ = "playlists"

    id               = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id          = Column(BigInteger, nullable=False, index=True)
    position         = Column(SmallInteger, nullable=False, default=0)
    file_path        = Column(Text)
    stream_url       = Column(Text)
    title            = Column(String(500))
    duration_seconds = Column(Integer)
    media_type       = Column(String(10), nullable=False, default='audio')
    added_by         = Column(BigInteger)
    added_at         = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint('chat_id', 'position', name='uq_playlist_chat_pos'),
    )


class User(Base):
    __tablename__ = "users"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id    = Column(BigInteger, nullable=False, unique=True, index=True)
    username   = Column(String(100))
    first_name = Column(String(255))
    is_banned  = Column(Boolean, nullable=False, default=False)
    banned_at  = Column(DateTime(timezone=True))
    banned_by  = Column(BigInteger)
    joined_at  = Column(DateTime(timezone=True), server_default=func.now())
    last_seen  = Column(DateTime(timezone=True))


class Sudo(Base):
    __tablename__ = "sudos"

    id             = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id        = Column(BigInteger, nullable=False, unique=True, index=True)
    username       = Column(String(100))
    display_name   = Column(String(255))
    added_by       = Column(BigInteger, nullable=False)
    added_at       = Column(DateTime(timezone=True), server_default=func.now())
    sudo_link      = Column(Text)
    is_active      = Column(Boolean, nullable=False, default=True)
    deactivated_at = Column(DateTime(timezone=True))
    total_installs = Column(Integer, nullable=False, default=0)
```

---

## 9. Role Hierarchy & Access Control

```
╔══════════════════════════════════════════════════════════════════╗
║  DEVELOPER  (bot builder - set in config.env, never in DB)      ║
║  • Full access to everything                                     ║
║  • Add/remove Owners                                             ║
║  • Set pricing, limits, and global settings                      ║
╠══════════════════════════════════════════════════════════════════╣
║  OWNER  (مالک ربات - added by Developer)                         ║
║  • All Developer access except adding other Owners               ║
║  • Full broadcast to all groups/channels/users                   ║
║  • Manage Sudos                                                  ║
║  • See reports from all Sudos                                    ║
╠══════════════════════════════════════════════════════════════════╣
║  SUDO  (نماینده - added by Owner or Developer)                   ║
║  • Install/uninstall player in groups and channels               ║
║  • Charge/deduct credit for their installed chats                ║
║  • View reports only for their own installs                      ║
║  • Has their own purchase link                                   ║
╠══════════════════════════════════════════════════════════════════╣
║  PLAYER OWNER  (مالک پلیر - per-group, set by Sudo/Owner)        ║
║  • Highest in-group privilege                                    ║
║  • Configure all group settings                                  ║
║  • Promote/demote Music Admins, Video Admins, VIPs               ║
╠══════════════════════════════════════════════════════════════════╣
║  MUSIC ADMIN / VIDEO ADMIN  (per-group)                          ║
║  • Music Admin: full control over audio playback                 ║
║  • Video Admin: full control over video playback                 ║
╠══════════════════════════════════════════════════════════════════╣
║  VIP USER  (per-group)                                           ║
║  • Access to features above regular users                        ║
╠══════════════════════════════════════════════════════════════════╣
║  REGULAR USER                                                    ║
║  • View playback / download (if enabled)                         ║
╚══════════════════════════════════════════════════════════════════╝
```

### Access Control Decorators

```python
# utils/decorators.py

from functools import wraps
from config import settings


def developer_only(func):
    """Allows only the developer (from config)"""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        if message.from_user.id == settings.DEVELOPER_ID:
            return await func(client, message, *args, **kwargs)
    return wrapper


def owner_or_above(func):
    """Allows owners + developer"""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        uid = message.from_user.id
        if uid == settings.DEVELOPER_ID:
            return await func(client, message, *args, **kwargs)
        from repositories.user_repo import is_owner
        if await is_owner(uid):
            return await func(client, message, *args, **kwargs)
    return wrapper


def sudo_or_above(func):
    """Allows sudos + owners + developer"""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        uid = message.from_user.id
        if uid == settings.DEVELOPER_ID:
            return await func(client, message, *args, **kwargs)
        from repositories.user_repo import is_sudo_or_above
        if await is_sudo_or_above(uid):
            return await func(client, message, *args, **kwargs)
    return wrapper


def group_music_admin(func):
    """Allows music admins + player owners + sudo + owner + developer in a group"""
    @wraps(func)
    async def wrapper(client, message, *args, **kwargs):
        uid = message.from_user.id
        cid = message.chat.id
        if uid == settings.DEVELOPER_ID:
            return await func(client, message, *args, **kwargs)
        from repositories.admin_repo import is_music_admin_or_above
        if await is_music_admin_or_above(uid, cid):
            return await func(client, message, *args, **kwargs)
    return wrapper
```

---

## 10. Developer Panel - Full Feature List

Accessible via `/start` in private chat (only for DEVELOPER_ID).

```
╔══════════════════════════════════════════════════════════╗
║              DEVELOPER PANEL (پنل برنامه‌نویس)            ║
╠══════════════════════════════════════════════════════════╣
║  📊 Status & Statistics                                  ║
║     • Bot status (online, helper status, call count)     ║
║     • Total groups installed (music / video)             ║
║     • Total channels installed                           ║
║     • Total registered users                             ║
║     • Total sudos active                                 ║
║     • Server CPU / RAM / Disk usage                      ║
╠══════════════════════════════════════════════════════════╣
║  💳 Credit Management                                    ║
║     • Add credit → chat_id + days                        ║
║     • Deduct credit → chat_id + days                     ║
║     • Issue invoice → chat_id + amount + days            ║
║     • View invoice history → per chat_id                 ║
╠══════════════════════════════════════════════════════════╣
║  💰 Rate Settings                                        ║
║     • Set base rate (نرخ پایه)                           ║
║     • Set music rate (نرخ موزیک)                         ║
║     • Set video rate (نرخ ویدیو)                         ║
║     • Set security call rate (نرخ امنیت کال)            ║
║     • Set music sell rate (نرخ فروش موزیک)               ║
║     • Set video sell rate (نرخ فروش ویدیو)               ║
╠══════════════════════════════════════════════════════════╣
║  📢 Broadcast                                            ║
║     • Send message to all groups                         ║
║     • Forward message to all groups                      ║
║     • Send message to all users (private)                ║
║     • Forward message to all users (private)             ║
║     • Send message to all channels                       ║
║     • Forward message to all channels                    ║
║     • Send message to specific sudo                      ║
╠══════════════════════════════════════════════════════════╣
║  📋 Group & Channel Lists                                ║
║     • List of active music groups (with credit + link)   ║
║     • List of renewal-needed music groups (< 24h left)   ║
║     • List of active video groups (with credit + link)   ║
║     • List of renewal-needed video groups                ║
║     • List of active channels                            ║
║     • List of zero-credit groups & channels              ║
║     • Remove bot from specific group (music)             ║
║     • Remove bot from specific group (video)             ║
╠══════════════════════════════════════════════════════════╣
║  🔒 Force Join                                           ║
║     • Enable force-join globally                         ║
║     • Disable force-join globally                        ║
║     • Set force-join channel                             ║
╠══════════════════════════════════════════════════════════╣
║  🚫 Filter Words                                         ║
║     • Add filter word                                    ║
║     • Remove filter word                                 ║
║     • Show all filter words                              ║
╠══════════════════════════════════════════════════════════╣
║  ✅ Force Membership                                     ║
║     • Add required membership channel                    ║
║     • Remove required membership channel                 ║
║     • List all required channels                         ║
╠══════════════════════════════════════════════════════════╣
║  👥 Sudo Management                                      ║
║     • Add sudo (by numeric user ID)                      ║
║     • Remove sudo                                        ║
║     • List all sudos (name + ID + username)              ║
║     • Set bot owner (مالک ربات)                          ║
║     • Set sudo purchase link (لینک سودو)                 ║
║     • Remove sudo purchase link                          ║
║     • List all sudo purchase links                       ║
╠══════════════════════════════════════════════════════════╣
║  ✏️ Text & Link Settings                                 ║
║     • Set start text (supports MENTION, BOLD, USERID)    ║
║     • Set helper start text                              ║
║     • Set about-us text                                  ║
║     • Set developer link (لینک سازنده ربات)              ║
║     • Set developer private link (لینک پی‌وی ادمین)      ║
║     • Set bot channel link                               ║
║     • Set guide channel                                  ║
║     • Set support group                                  ║
║     • Set broadcast channel (پیامرسان)                   ║
║     • Set helper text                                    ║
║     • Set custom link (لینک دلخواه)                      ║
║     • Set tariff text (متن تعرفه)                        ║
╠══════════════════════════════════════════════════════════╣
║  ⚙️ Installation Limits                                  ║
║     • Set minimum member count for group install         ║
║     • Set minimum admin count for channel install        ║
║     • Enable installation limit                          ║
║     • Disable installation limit                         ║
╠══════════════════════════════════════════════════════════╣
║  🔐 Blacklist (Block/Unblock)                            ║
║     • Block group/channel by ID                          ║
║     • Block user by ID                                   ║
║     • Unblock group/channel by ID                        ║
║     • Unblock user by ID                                 ║
╠══════════════════════════════════════════════════════════╣
║  🚪 Auto-Leave                                           ║
║     • Enable auto-leave (when credit = 0)                ║
║     • Disable auto-leave                                 ║
╠══════════════════════════════════════════════════════════╣
║  📣 Log Channel                                          ║
║     • Set log & reports channel                          ║
╚══════════════════════════════════════════════════════════╝
```

---

## 11. Owner Panel - Full Feature List

Accessible via `/start` in private (for users in `owners` table).

Identical to Developer panel **except**:
- Cannot add/remove other Owners
- Cannot set `DEVELOPER_ID` (hardcoded in config)
- Has additional reports section for their sudo network
- Has their own credit balance display and top-up view

```
Extra sections for Owner:
  • View own credit balance
  • Top up bot credit
  • View own invoices
  • Receive install reports from all sudos
  • Receive zero-credit alerts for all groups/channels
```

---

## 12. Sudo Panel - Full Feature List

Accessible via `/start` in private (for users in `sudos` table).

```
╔══════════════════════════════════════════════════════════╗
║              SUDO PANEL (پنل سودو)                       ║
╠══════════════════════════════════════════════════════════╣
║  📊 Statistics                                           ║
║     • Total groups installed by this sudo                ║
║     • Total channels installed by this sudo              ║
║     • List of active installs                            ║
╠══════════════════════════════════════════════════════════╣
║  📋 Reports (own installs only)                          ║
║     • Install reports (new group/channel installs)       ║
║     • Low-credit / zero-credit alerts for their chats    ║
║     • Remaining credit per group/channel                 ║
╠══════════════════════════════════════════════════════════╣
║  🚪 Management                                           ║
║     • Remove player from any of their installed groups   ║
║     • Remove player from any of their installed channels ║
╠══════════════════════════════════════════════════════════╣
║  💳 Credit Operations (on their chats)                   ║
║     • Update charge for music group (آپدیت شارژ)         ║
║     • Update charge for video group (آپدیت شارژ ویدیو)   ║
║     • Update charge in group chat (inline command)       ║
╚══════════════════════════════════════════════════════════╝
```

### Credit Update Commands (in group chat)

```
آپدیت شارژ {days}         → update music charge for this group
آپدیت شارژ ویدیو {days}    → update video charge for this group
```
Both commands send an automatic notification to Owner and Developer with full details:
- Group name + ID + invite link
- New credit amount
- Sudo's name + username + ID
- Timestamp (Persian calendar)

---

## 13. Group Panel - Full Feature List

Accessible via `/settings` or the settings button in groups.

```
╔══════════════════════════════════════════════════════════╗
║              GROUP PANEL (پنل گروه)                      ║
╠══════════════════════════════════════════════════════════╣
║  ⚙️ Settings                                             ║
║     • Music playback: Enable / Disable                   ║
║     • Video playback: Enable / Disable                   ║
║     • Call security: Enable / Disable                    ║
║     • Repeat mode: Enable / Disable                      ║
║     • User downloads: Enable / Disable                   ║
║     • Now Playing message: Enable / Disable              ║
║     • Auto-clean messages: Enable / Disable              ║
║     • Queue (waiting list): Enable / Disable             ║
║     • Auto-ready call: Enable / Disable                  ║
║     • Call reports: Enable / Disable                     ║
║     • Call recording: Enable / Disable                   ║
║     • Display mode: ID / Photo / Text (on/off each)      ║
║     • Default media type (audio / video)                 ║
╠══════════════════════════════════════════════════════════╣
║  👥 Management                                           ║
║     • Show list of Player Owners + Clear all             ║
║     • Show list of Music Admins + Clear all              ║
║     • Show list of Video Admins + Clear all              ║
║     • Show list of VIP Users + Clear all                 ║
╠══════════════════════════════════════════════════════════╣
║  📖 Player Help                                          ║
║     • Promotion & demotion commands                      ║
║     • Playback commands                                  ║
║     • General commands                                   ║
║     • Request support                                    ║
╠══════════════════════════════════════════════════════════╣
║  🆘 Support                                              ║
║     • Contact bot builder (developer)                    ║
║     • Contact sudo (sales rep)                           ║
║     • Guide channel                                      ║
║     • Support group                                      ║
╚══════════════════════════════════════════════════════════╝
```

---

## 14. Start Menu

Sent to any user opening the bot in private (`/start`).

```
┌──────────────────────────────────────────────────┐
│  [Start Text - customizable, with MENTION macro] │
├──────────────────────────────────────────────────┤
│  [📚 About Us]                                   │
│  [💻 Buy directly from developer]                │
│  [▪️ Bot Channel]  [▪️ Support Group]             │
│  [📮 Buy via reseller (sudo)]                    │
│  [📖 Guide Channel]  [🔗 Custom Link]            │
│  [➕ Add to Group]   [📢 Add to Channel]          │
└──────────────────────────────────────────────────┘

After installation (sent by Owner or Sudo):
┌──────────────────────────────────────────────────┐
│  ✅ Player Installed                              │
│  💎 Current credit: {N} days                     │
│  📅 Expires: {date}                              │
│  💰 Charged by: {sudo_name}                      │
│  ━━━━━━━━━━━━━━━━━━━━━━━━                        │
│  [➕ Add Credit]                                  │
│  [➖ Deduct Credit]                               │
│  [👤 Add Helper]                                 │
│  [⚙️ Player Panel]                               │
│  [📖 Player Guide]                               │
│  [📺 Guide Channel]                              │
└──────────────────────────────────────────────────┘
```

---

## 15. Playback System - Full Feature List

### Playback Commands

| Persian Command | English Command | Access Level | Description |
|----------------|----------------|--------------|-------------|
| `پخش` | `Play` | Music Admin+ | Play audio (reply to file) |
| `پخش {username/id}` | `Play {target}` | Music Admin+ | Dedicate track to a user |
| `پخش ویدیو` | `PlayVideo` | Video Admin+ | Play video (reply to file) |
| `پخش ویدیو {username/id}` | `PlayVideo {target}` | Video Admin+ | Dedicate video to user |
| `توقف پخش` | `StopMusic` | Music Admin+ | Stop audio playback |
| `توقف ویدیو` | `StopVideo` | Video Admin+ | Stop video playback |
| `مکث` | `Pause` | Music/Video Admin+ | Pause current playback |
| `ازسرگیری` | `Resume` | Music/Video Admin+ | Resume paused playback |
| `بیصدا` | `silent` | Music/Video Admin+ | Mute the stream |
| `باصدا` | `unsilent` | Music/Video Admin+ | Unmute the stream |
| `صدای موزیک {n}` | `MusicSound {n}` | Music Admin+ | Set audio volume (0-200) |
| `صدای ویدیو {n}` | `VideoSound {n}` | Video Admin+ | Set video volume (0-200) |
| `پخش تیوی` | `PlayTv` | Music/Video Admin+ | Start TV channel stream |
| `توقف تیوی` | `StopTv` | Music/Video Admin+ | Stop TV stream |
| `پینگ` | `Ping` | All | Check bot latency |
| `ربات` / `bot` / `robot` | same | All | Check helper & bot status |

### Now Playing Inline Keyboard

```
┌─────────────────────────────────────────────────────────────┐
│  🎵  {Track Title}                                          │
│  🎤  {Artist Name}           ⏱ {Duration}                  │
├─────────────────────────────────────────────────────────────┤
│  [🔉 Vol-]                              [🔊 Vol+]           │
├─────────────────────────────────────────────────────────────┤
│  [⏮ Prev]    [⏸ Pause / ▶️ Resume]    [⏭ Next]            │
├─────────────────────────────────────────────────────────────┤
│  [❤️ Add to Favorites]                                      │
├─────────────────────────────────────────────────────────────┤
│  [🐢 Speed-]    [🔁 Repeat: ON/OFF]    [🐇 Speed+]         │
├─────────────────────────────────────────────────────────────┤
│  [📥 Download]              [📡 Bot Channel]                │
├─────────────────────────────────────────────────────────────┤
│  [🤖 Bot ID]                [📢 Channel Link]               │
└─────────────────────────────────────────────────────────────┘
```

---

## 16. Playlist / Queue System

### Queue Commands

| Persian | English | Description |
|---------|---------|-------------|
| `افزودن به لیست` | `AddToPlayList` | Add replied-to file to queue |
| `پخش لیست` | `PlayList` | Start playing from queue |
| `توقف لیست` | `StopList` | Stop queue playback |
| `لیست پخش` | `ListPlayList` | Show current queue |
| `حذف از لیست` | `delfromPlaylist` | Remove item from queue |
| `پاکسازی لیست پخش` | `CleanPlayList` | Clear entire queue |

### Auto-Play Logic

```
Track ends (on_stream_end event)
         │
         ▼
Check: is repeat_mode ON?
         │ YES                 │ NO
         ▼                     ▼
 Replay same track      Check: queue has next item?
                                │ YES              │ NO
                                ▼                  ▼
                        Play next item     Leave voice call
                        Delete position 1  (or stay if
                        Shift all others   auto_ready ON)
                        down by 1
```

---

## 17. Media Types & Sources

### Playback Menu (Inline UI)

```
[▶️ Audio]   [🎬 Video]
[📡 Satellite TV]
[📺 Television (domestic)]
[📥 Download Media]
[🌍 Select Country → Radio]
[🔄 Replay (on reply/link)]
```

### Media Sources Supported

| Source | How | Command/Flow |
|--------|-----|-------------|
| Telegram File (audio) | Reply to audio message | `پخش` |
| Telegram File (video) | Reply to video message | `پخش ویدیو` |
| YouTube URL | yt-dlp download | `پخش {url}` |
| YouTube Search | Search + select result | Search inline |
| Direct stream URL | Pass URL directly | `پخش {url}` |
| TV (صداوسیما) | Predefined IPTV URLs | `پخش تیوی` |
| Satellite TV | Predefined stream URLs | `پخش ماهواره` |
| Internet Radio | Predefined + country selector | `رادیو` |
| Replay on reply | Extract media from replied msg | `پخش ریپلای` |

### TV Channels (assets/tv_channels.json)

```json
[
  { "name": "شبکه یک",     "url": "https://livestream.irib.ir/live/tv1.m3u8" },
  { "name": "شبکه دو",     "url": "https://livestream.irib.ir/live/tv2.m3u8" },
  { "name": "شبکه سه",     "url": "https://livestream.irib.ir/live/tv3.m3u8" },
  { "name": "شبکه خبر",   "url": "https://livestream.irib.ir/live/news.m3u8" },
  { "name": "شبکه ورزش",  "url": "https://livestream.irib.ir/live/sport.m3u8" },
  { "name": "شبکه تماشا", "url": "https://livestream.irib.ir/live/tamasha.m3u8" },
  { "name": "شبکه نسیم",  "url": "https://livestream.irib.ir/live/nasim.m3u8" }
]
```

---

## 18. Credit System (Subscription Engine)

### Credit Lifecycle

```
Install bot in group
         │
         ▼
Create group_credits row (credit_days = 0, status = 0)
         │
         ▼
Auto-activate 3-day trial (is_trial = TRUE, status = 2)
         │ (trial lasts 3 days)
         ▼
Trial expires → status = 0 (no credit)
         │ IF auto_leave_no_credit = TRUE
         ▼
Bot leaves group call (but stays in group)
         │
         ▼ (Sudo charges the group)
         │
credit_days += N, expire_at = NOW + N days, status = 1
         │
         ▼ (Daily cron runs at midnight)
         │
credit_days -= 1
         │
         ▼ (At 24h before expiry → warning message)
         │
         ▼ (At credit_days = 0)
         │
status = 0 → auto_leave if enabled
```

### Credit Service

```python
# services/credit_service.py

class CreditService:

    async def charge(
        self,
        chat_id: int,
        chat_type: str,
        days: int,
        operated_by: int,
        note: str = "manual"
    ) -> bool:
        """
        Adds credit days to a group or channel.
        Updates credit_days, recomputes expire_at, sets status=1.
        Writes to credit_history. Invalidates Redis cache.
        Sends notification to Developer + Owner about the charge.
        """

    async def deduct(
        self,
        chat_id: int,
        days: int,
        operated_by: int,
        note: str = "manual"
    ) -> bool:
        """
        Deducts credit days.
        If credit_days drops to 0 or below, sets status=0.
        If auto_leave is enabled, triggers bot leave from voice call.
        """

    async def activate_trial(self, chat_id: int, chat_type: str) -> bool:
        """
        Activates the 3-day free trial on first installation.
        Sets is_trial=TRUE, credit_days=3, status=2.
        Trial can only be activated once per chat_id.
        """

    async def get_balance(self, chat_id: int) -> int:
        """Returns remaining credit days (from Redis cache or DB)"""

    async def get_expiring_chats(self, hours_ahead: int = 24) -> list[dict]:
        """
        Returns all groups/channels whose credit expires
        within the next N hours. Used by warning cron job.
        """

    async def get_zero_credit_chats(self) -> list[dict]:
        """Returns all groups/channels with credit_days <= 0"""

    async def daily_deduct_all(self) -> int:
        """
        Called by cron at midnight.
        Decrements credit_days by 1 for all active groups/channels.
        Updates expire_at. Returns count of updated rows.
        SQL: UPDATE group_credits SET credit_days = credit_days - 1
             WHERE status = 1 AND credit_days > 0
        """
```

---

## 19. Installation Flow & Trial Period

### When bot is added as admin to a group

```python
@bot.on_message(filters.new_chat_members)
async def on_new_member(client, message):
    new_members = message.new_chat_members
    bot_user = await client.get_me()

    if any(m.id == bot_user.id for m in new_members):
        chat_id = message.chat.id

        # 1. Check blacklist
        if await is_blacklisted(chat_id, 'group'):
            await client.leave_chat(chat_id)
            return

        # 2. Check install limits (member count)
        settings = await get_bot_setting('max_group_members')
        if int(settings) > 0:
            member_count = await client.get_chat_members_count(chat_id)
            if member_count < int(settings):
                await client.leave_chat(chat_id)
                return

        # 3. Create group record
        await group_repo.upsert(chat_id, ...)

        # 4. Create credit record + activate trial
        await credit_service.activate_trial(chat_id, 'group')

        # 5. Create default settings
        await settings_repo.create_defaults(chat_id, 'group')

        # 6. Log install
        await log_repo.log_install(chat_id, 'group', 'install', ...)

        # 7. Notify sudo + developer + owner
        await notification_service.notify_install(chat_id, ...)
```

---

## 20. Promotion & Demotion System

### In-Group Commands

| Persian | English | Target | Effect |
|---------|---------|--------|--------|
| `ترفیع موزیک` | `PromotMusic` | Reply / @username / numeric ID | Add to music_admins |
| `عزل موزیک` | `DemoteMusic` | Reply / @username / numeric ID | Remove from music_admins |
| `ترفیع ویدیو` | `PromotVideo` | Reply / @username / numeric ID | Add to video_admins |
| `عزل ویدیو` | `DemoteVideo` | Reply / @username / numeric ID | Remove from video_admins |
| `ترفیع مالک` | `SetCreator` | Reply / @username / numeric ID | Add to player_owners |
| `عزل مالک` | `DelCreator` | Reply / @username / numeric ID | Remove from player_owners |
| `پیکربندی موزیک` | `ConfigMusic` | - | Promote all current group admins to music_admins |
| `پاکسازی مدیران موزیک` | `DelConfigMusic` | - | Clear entire music_admins list for this group |
| `لیست مدیران موزیک` | `ListMusic` | - | List all music_admins in this group |
| `پیکربندی ویدیو` | `ConfigVideo` | - | Promote all current group admins to video_admins |
| `پاکسازی مدیران ویدیو` | `DelConfigVideo` | - | Clear entire video_admins list for this group |
| `لیست مدیران ویدیو` | `ListVideo` | - | List all video_admins in this group |
| `لیست مالکان` | `CreatorsList` | - | List all player_owners in this group |

---

## 21. Broadcast System

Supports three broadcast modes for three target audiences, each with send and forward options.

```python
# services/broadcast_service.py

class BroadcastService:

    async def broadcast_to_groups(
        self,
        message_id: int,
        from_chat_id: int,
        mode: str = 'copy'   # 'copy' or 'forward'
    ) -> dict:
        """
        Sends/forwards a message to all installed groups.
        Iterates groups table. Rate-limited to avoid flood wait.
        Returns: { 'sent': n, 'failed': m }
        """

    async def broadcast_to_channels(
        self,
        message_id: int,
        from_chat_id: int,
        mode: str = 'copy'
    ) -> dict:
        """Same as above but for channels table."""

    async def broadcast_to_users(
        self,
        message_id: int,
        from_chat_id: int,
        mode: str = 'copy'
    ) -> dict:
        """
        Sends/forwards to all users in the users table.
        (Only private chats - users who have /started the bot)
        """
```

---

## 22. Force Join System

```python
# handlers/force_join.py
# Checked on every group command from a regular user

async def check_force_join(client, message, user_id: int) -> bool:
    """
    Returns True if user is a member of all required channels.
    Returns False (and sends join prompt) if not.

    Exemptions (bypass force join):
    - Telegram admins in force_join_channels (ChatAdminRequired → skip silently)
    - Users in music_admins / video_admins / player_owners tables
    - Developer / Owner / Sudo

    Error handling:
    - UserNotParticipant → show join button
    - ChatAdminRequired  → bot lacks admin in channel, show re-admin message
    - ChannelInvalid     → same
    """
```

---

## 23. Filter Words System

```python
# Checked on every incoming group message

@bot.on_message(filters.group & filters.text)
async def check_filter(client, message):
    words = await get_filter_words()  # Redis-cached
    text = message.text.lower()
    for word in words:
        if word.lower() in text:
            try:
                await message.delete()
            except:
                pass
            return
```

---

## 24. Blacklist System

Any group, channel, or user in the blacklist is completely rejected:
- Bot auto-leaves blacklisted groups immediately on joining
- Bot ignores all commands from blacklisted users
- Admin attempts to install in blacklisted channels are rejected

---

## 25. Auto-Leave System

```python
# Triggered by cron and by real-time credit checks

async def auto_leave_check(chat_id: int, call_py: PyTgCalls, bot: Client):
    """
    Called when a group's credit drops to 0.
    Steps:
    1. Check chat_settings.auto_leave_no_credit
    2. If True: leave voice call via call_py.leave_group_call(chat_id)
    3. Send notification to group (if call_message_enabled)
    4. Notify sudo responsible for this group
    5. Update group_credits.status = 0
    """
```

---

## 26. Reporting & Logging System

### Automatic Notifications Sent to Developer + Owner

| Event | Triggered By | Contains |
|-------|-------------|----------|
| New group install | Bot added to group | Group name, ID, link, member count, sudo name |
| Group uninstall | Bot removed from group | Same |
| Credit charged | Sudo charges a group | Group details + sudo details + amount |
| Credit at 24h | Daily cron | Group name + link + remaining hours |
| Credit expired | Daily cron | Group name + link |
| Bot error | Exception handler | Error message + traceback |

### Sudo-Only Notifications

| Event | What Sudo Sees |
|-------|---------------|
| New install by this sudo | Group/channel name + ID + invite link |
| Credit warning for their chat | Remaining credit + group info |
| Zero credit for their chat | Group info + auto-leave status |

### Log Channel Format

```
⇐ New Group Install

📅 Date: {Persian date}
━━━| Group Info |━━━
• Name: {chat_title}
• ID: {chat_id}
• Link: click to join
• Members: {count}
━━━| Sudo Info |━━━
• Name: {sudo_name}
• Username: @{sudo_username}
• ID: {sudo_id}
```

---

## 27. Cron Jobs & Scheduled Tasks

```python
# scheduler.py - APScheduler

from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler(timezone="Asia/Tehran")


@scheduler.scheduled_job('cron', hour=0, minute=0)
async def midnight_credit_deduct():
    """
    Runs every midnight (Tehran time).
    Deducts 1 day of credit from all active groups & channels.
    SQL: UPDATE group_credits
         SET credit_days = credit_days - 1,
             status = CASE WHEN credit_days - 1 <= 0 THEN 0 ELSE status END
         WHERE status = 1
    Returns count of updated rows.
    Sends summary to log channel.
    """


@scheduler.scheduled_job('interval', minutes=10)
async def check_group_credit_warnings():
    """
    Finds groups expiring within next 24 hours.
    Sends warning message to each group.
    Notifies responsible sudo.
    """


@scheduler.scheduled_job('interval', minutes=13)
async def check_channel_credit_warnings():
    """Same as above for channels."""


@scheduler.scheduled_job('interval', minutes=20)
async def cleanup_downloads():
    """
    Deletes downloaded media files older than 30 minutes.
    Frees disk space to prevent disk-full scenarios.
    """


@scheduler.scheduled_job('interval', minutes=25)
async def health_check():
    """
    - Sends ping to Telegram API
    - Checks PostgreSQL connection health (SELECT 1)
    - Checks Redis connection
    - Logs CPU / RAM / disk usage
    - Sends health report to log channel if anomaly detected
    """


@scheduler.scheduled_job('interval', minutes=18)
async def check_trial_expiry():
    """
    Finds trial groups whose trial_expire_at has passed.
    Sets status = 0, is_trial = FALSE.
    Triggers auto_leave_check for each.
    Notifies sudo responsible for each expired trial group.
    """


@scheduler.scheduled_job('cron', hour=4, minute=0)
async def nightly_db_backup():
    """
    Runs daily at 04:00 (Tehran time).

    Creates a compressed `pg_dump` backup and keeps the last N backups.
    This is required for disaster recovery and safe upgrades.

    - Output path: BACKUP_DIR/pg_{YYYYMMDD_HHMM}.dump.gz
    - Retention: BACKUP_RETENTION_DAYS (default 7)
    - On success: logs to log channel (Persian via JSON key)
    - On failure: logs error + notifies Developer/Owner
    """
    ...

@scheduler.scheduled_job('interval', minutes=30)
async def check_sudo_wallet_alerts():
    """
    Checks sudo wallet balances and notifies when low.

    Rules:
    - If sudo balance <= SUDO_WALLET_LOW_THRESHOLD_DAYS:
        - notify the sudo (Persian)
        - notify Owner (summary)
    - Helps prevent sudden service interruption for sudo clients.
    """
    ...

@scheduler.scheduled_job('interval', minutes=15)
async def helper_health_watchdog():
    """
    Probes helper pool health and auto-quarantines broken helpers.

    - Tries `get_me()` for helpers that are active or exiting cooldown.
    - Resets cooldown -> active when safe.
    - If a helper fails probe repeatedly:
        - set status='error'
        - notify Owner/Developer (Persian)
    """
    ...


@scheduler.scheduled_job('interval', hours=1)
async def gc_and_memory_check():
    """
    Runs a lightweight garbage-collection cycle and kills zombie FFmpeg processes.

    - Calls Python `gc.collect()`
    - Terminates orphan FFmpeg processes registered in A8.2
    - Logs anomalies to log channel (Persian via JSON key)
    """
    ...
```

---

## 28. Handler Modules (Full Structure)

### main.py Entry Point

```python
# main.py

import asyncio
from pyrogram import Client, idle
from pytgcalls import PyTgCalls
from config import settings
from database.engine import init_db, engine
from handlers import register_all
from scheduler import scheduler
from utils.logger import setup_logger


bot = Client(
    name="MusicBot",
    bot_token=settings.BOT_TOKEN,
    api_id=settings.API_ID,
    api_hash=settings.API_HASH,
    workers=200,               # high worker count for 1M+ scale
    sleep_threshold=60,
    max_concurrent_transmissions=10,
)

helper = Client(
    name="Helper",
    api_id=settings.API_ID,
    api_hash=settings.API_HASH,
    phone_number=settings.HELPER_PHONE,
    device_model="MusicPlayerHelper",
    workers=100,
    sleep_threshold=60,
)

call_py = PyTgCalls(helper, overload_quiet_mode=True)


async def main():
    setup_logger()
    await init_db()

    register_all(bot, call_py)

    scheduler.start()

    async with bot, helper:
        await call_py.start()
        await idle()
        await call_py.stop()

    scheduler.shutdown()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

### handlers/__init__.py

```python
# handlers/__init__.py

from pyrogram import Client
from pytgcalls import PyTgCalls

from . import (
    dev_panel, owner_panel, sudo_panel,
    group_panel, start, playback, playlist,
    tv_radio, search, download, promotion,
    broadcast, force_join, callbacks
)


def register_all(bot: Client, call_py: PyTgCalls):
    """Register all handler modules"""
    modules = [
        dev_panel, owner_panel, sudo_panel,
        group_panel, start, playback, playlist,
        tv_radio, search, download, promotion,
        broadcast, force_join, callbacks
    ]
    for module in modules:
        module.register(bot, call_py)
```

---

## 29. Custom Filters & Decorators

```python
# utils/filters.py

from pyrogram import filters
from config import settings


def dev_filter():
    async def func(flt, client, message):
        return message.from_user and message.from_user.id == settings.DEVELOPER_ID
    return filters.create(func, "DevFilter")


def sudo_filter():
    async def func(flt, client, message):
        if not message.from_user:
            return False
        from repositories.user_repo import is_sudo_or_above
        return await is_sudo_or_above(message.from_user.id)
    return filters.create(func, "SudoFilter")


def credit_filter():
    """
    Passes only if the group/channel has active credit.
    Uses Redis cache → DB fallback.
    """
    async def func(flt, client, message):
        if not message.chat:
            return False
        from services.credit_service import CreditService
        return await CreditService().get_balance(message.chat.id) > 0
    return filters.create(func, "CreditFilter")


def music_admin_filter():
    """Passes if user is music admin or above in this chat"""
    async def func(flt, client, message):
        if not message.from_user or not message.chat:
            return False
        if message.from_user.id == settings.DEVELOPER_ID:
            return True
        from repositories.admin_repo import is_music_admin_or_above
        return await is_music_admin_or_above(
            message.from_user.id, message.chat.id
        )
    return filters.create(func, "MusicAdminFilter")


def not_blacklisted():
    """Rejects messages from blacklisted users, groups, or channels"""
    async def func(flt, client, message):
        from repositories.blacklist_repo import is_blacklisted
        if message.from_user and await is_blacklisted(message.from_user.id, 'user'):
            return False
        if message.chat and await is_blacklisted(message.chat.id, 'group'):
            return False
        return True
    return filters.create(func, "NotBlacklistedFilter")
```

---

## 30. Redis Caching Layer

```python
# utils/cache.py

import redis.asyncio as aioredis
import json
from config import settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


# ── Cache key namespacing ──────────────────────────────────────────────────
# settings:{chat_id}       → ChatSettings dict      TTL: 5 min
# credit:{chat_id}         → credit_days (int)      TTL: 1 min
# role:{user_id}           → role string            TTL: 10 min
# botset:{key}             → bot_settings value     TTL: 5 min
# blacklist:group:{id}     → '1' if blocked         TTL: 5 min
# blacklist:user:{id}      → '1' if blocked         TTL: 5 min
# filterwords              → JSON array             TTL: 5 min
# sudolist                 → JSON array of IDs      TTL: 5 min
# ownerlist                → JSON array of IDs      TTL: 5 min


async def get_chat_settings(chat_id: int) -> dict | None:
    r = await get_redis()
    key = f"settings:{chat_id}"
    cached = await r.get(key)
    if cached:
        return json.loads(cached)
    from repositories.settings_repo import fetch_chat_settings
    data = await fetch_chat_settings(chat_id)
    if data:
        await r.setex(key, settings.REDIS_SETTINGS_TTL, json.dumps(data))
    return data


async def invalidate_chat_settings(chat_id: int):
    r = await get_redis()
    await r.delete(f"settings:{chat_id}")


async def get_credit(chat_id: int) -> int:
    r = await get_redis()
    key = f"credit:{chat_id}"
    cached = await r.get(key)
    if cached is not None:
        return int(cached)
    from repositories.credit_repo import fetch_credit_days
    days = await fetch_credit_days(chat_id)
    await r.setex(key, settings.REDIS_CREDIT_TTL, str(days))
    return days


async def invalidate_credit(chat_id: int):
    r = await get_redis()
    await r.delete(f"credit:{chat_id}")
```

---

## 31. Media Download Service

```python
# services/media_service.py

import asyncio
import os
import yt_dlp
from pathlib import Path
from config import settings

# Semaphore prevents too many simultaneous downloads
_semaphore = asyncio.Semaphore(settings.DOWNLOAD_SEMAPHORE)


async def download_audio(url: str, chat_id: int) -> str | None:
    """
    Downloads audio from URL using yt-dlp.
    Converts to MP3 via FFmpeg.
    Returns path to downloaded file, or None on failure.
    """
    async with _semaphore:
        output = f"{settings.DOWNLOADS_PATH}{chat_id}_{int(asyncio.get_event_loop().time())}"
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': f'{output}.%(ext)s',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
            'quiet': True,
            'no_warnings': True,
            'max_filesize': settings.MAX_DOWNLOAD_SIZE_MB * 1024 * 1024,
        }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_download, url, opts, 'audio')


async def download_video(url: str, chat_id: int) -> str | None:
    """
    Downloads video at max 720p resolution.
    Returns path to downloaded file, or None on failure.
    """
    async with _semaphore:
        output = f"{settings.DOWNLOADS_PATH}{chat_id}_{int(asyncio.get_event_loop().time())}"
        opts = {
            'format': f'best[height<={settings.VIDEO_QUALITY}][width<=1280]',
            'outtmpl': f'{output}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'max_filesize': settings.MAX_DOWNLOAD_SIZE_MB * 1024 * 1024,
        }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _do_download, url, opts, 'video')


async def get_stream_url(url: str) -> str | None:
    """
    Extracts direct stream URL without downloading.
    Used for live streams, TV channels, radio.
    Equivalent to: yt-dlp -g -f best[height<=720] {url}
    """
    async with _semaphore:
        opts = {
            'format': f'best[height<=720][width<=1280]',
            'quiet': True,
            'no_warnings': True,
        }
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _extract_url, url, opts)


def _do_download(url: str, opts: dict, kind: str) -> str | None:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if kind == 'audio':
                # Return the .mp3 path after postprocessing
                return filename.rsplit('.', 1)[0] + '.mp3'
            return filename
    except Exception as e:
        return None


def _extract_url(url: str, opts: dict) -> str | None:
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get('url') or (info.get('formats') or [{}])[-1].get('url')
    except Exception:
        return None


async def cleanup_old_downloads(max_age_minutes: int = 30):
    """Deletes files older than max_age_minutes in the downloads directory"""
    import time
    now = time.time()
    for f in Path(settings.DOWNLOADS_PATH).iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > max_age_minutes * 60:
            try:
                f.unlink()
            except Exception:
                pass
```

---

## 32. Installation & Deployment Guide

### Step 1 - System Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Python 3.11
sudo apt install -y python3.11 python3.11-venv python3.11-dev build-essential

# FFmpeg (required for audio/video processing)
sudo apt install -y ffmpeg

# yt-dlp (replaces outdated youtube-dl)
sudo curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp
sudo chmod a+rx /usr/local/bin/yt-dlp

# PostgreSQL 16
sudo apt install -y postgresql-16 postgresql-client-16

# Redis
sudo apt install -y redis-server
sudo systemctl enable redis-server && sudo systemctl start redis-server

# PgBouncer
sudo apt install -y pgbouncer
```

### Step 2 - Database Setup

```bash
sudo -u postgres psql << 'EOF'
CREATE USER botuser WITH PASSWORD 'StrongPassword123';
CREATE DATABASE musicbot_db OWNER botuser ENCODING 'UTF8';
GRANT ALL PRIVILEGES ON DATABASE musicbot_db TO botuser;
\c musicbot_db
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;
EOF
```

### Step 3 - Project Setup

```bash
# Clone / copy project
git clone https://github.com/dibbed/tg-vc-player-saas.git /root/music_bot
cd /root/music_bot

# Virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure the bot
cp .env.example config/config.env
nano config/config.env        # ← Edit ONLY this file

# Run migrations
alembic upgrade head

# First run (will prompt for helper phone OTP)
python main.py
```

### Step 4 - Systemd Service

```ini
# /etc/systemd/system/musicbot.service

[Unit]
Description=Telegram Music Player Bot
After=network-online.target postgresql.service redis.service
Wants=postgresql.service redis.service
StartLimitIntervalSec=500
StartLimitBurst=5

[Service]
Type=simple
User=root
WorkingDirectory=/root/music_bot
Environment="PATH=/root/music_bot/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=/root/music_bot/venv/bin/python main.py
Restart=on-failure
RestartSec=15s
StandardOutput=append:/root/music_bot/logs/bot.log
StandardError=append:/root/music_bot/logs/bot_error.log
LimitNOFILE=65536
LimitNPROC=32768

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable musicbot
sudo systemctl start musicbot

# Monitor
sudo journalctl -u musicbot -f
tail -f /root/music_bot/logs/bot.log
```

### requirements.txt

```text
# Telegram
pyrogram==2.0.106
pyromod==0.1.4
pytgcalls

# Database
sqlalchemy[asyncio]==2.0.23
asyncpg==0.29.0
alembic==1.13.1

# Cache
redis==5.0.1

# Scheduler
apscheduler==3.10.4

# Media
yt-dlp

# Utilities
python-dotenv==1.0.0
loguru==0.7.2
jdatetime==4.1.1
aiofiles==23.2.1
aiohttp==3.9.1
```

---

## 33. Server Requirements & Tuning

### Recommended Specs (for 1M+ chats)

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 8 GB | 16-32 GB |
| Disk | 50 GB SSD | 200 GB NVMe |
| Network | 100 Mbps | 1 Gbps |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

### Kernel Tuning (/etc/sysctl.conf)

```bash
# Network performance
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.core.netdev_max_backlog = 65535
net.ipv4.tcp_tw_reuse = 1

# File descriptors
fs.file-max = 2097152

# Memory
vm.swappiness = 10
vm.dirty_ratio = 15
vm.overcommit_memory = 1
```

```bash
# Apply without reboot
sudo sysctl -p

# Increase file descriptor limits
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf
```

---

## 34. PostgreSQL Tuning

Add to `/etc/postgresql/16/main/postgresql.conf`:

```ini
# Memory (tune to 25% of total RAM for shared_buffers)
shared_buffers = 4GB
effective_cache_size = 12GB
work_mem = 64MB
maintenance_work_mem = 1GB

# Connections
max_connections = 100           # PgBouncer handles the pool

# WAL
wal_buffers = 64MB
checkpoint_completion_target = 0.9
max_wal_size = 4GB

# Write performance (safe to disable for bot workloads)
synchronous_commit = off

# Autovacuum (tune for tables with many writes like playlists)
autovacuum_vacuum_cost_delay = 2ms
autovacuum_vacuum_scale_factor = 0.01
autovacuum_analyze_scale_factor = 0.005

# Logging
log_min_duration_statement = 500    # log queries slower than 500ms
log_checkpoints = on
log_connections = off
log_disconnections = off

# Stats
track_activity_query_size = 4096
```

---

## 35. PgBouncer Connection Pooling

```ini
# /etc/pgbouncer/pgbouncer.ini

[databases]
musicbot_db = host=127.0.0.1 port=5432 dbname=musicbot_db

[pgbouncer]
listen_port = 6432
listen_addr = 127.0.0.1
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt

# Transaction mode is best for async Python (asyncpg)
pool_mode = transaction

max_client_conn = 2000
default_pool_size = 50
min_pool_size = 10
reserve_pool_size = 10
reserve_pool_timeout = 5

server_idle_timeout = 600
client_idle_timeout = 0
max_db_connections = 100

# Logging (disable for production performance)
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1

# Stats
stats_period = 60
```

```bash
# userlist.txt
"botuser" "md5{md5hash_of_password}"
```

---

## 36. Migration from SQLite to PostgreSQL

Run this script **once** to migrate all existing data from the old SQLite database.

```python
# database/migrate_sqlite_to_pg.py

import sqlite3
import asyncio
import asyncpg
from datetime import datetime, timezone


SQLITE_PATH = "database.sqlite"   # path to old database
PG_DSN = "postgresql://botuser:StrongPassword123@localhost/musicbot_db"


async def migrate():
    print("🚀 Starting SQLite → PostgreSQL migration...\n")
    sq = sqlite3.connect(SQLITE_PATH)
    sq_cur = sq.cursor()
    pg = await asyncpg.connect(PG_DSN)

    # ── 1. Groups ──────────────────────────────────────────────────────────
    sq_cur.execute("SELECT namegp, idgp, linkgp, status FROM gp")
    rows = sq_cur.fetchall()
    count = 0
    for r in rows:
        await pg.execute(
            "INSERT INTO groups (chat_title, chat_id, invite_link, status) "
            "VALUES ($1, $2, $3, $4) ON CONFLICT (chat_id) DO NOTHING",
            r[0], r[1], r[2], r[3]
        )
        count += 1
    print(f"✅ groups: {count} rows migrated")

    # ── 2. Music Credits (charge table → group_credits) ────────────────────
    sq_cur.execute("SELECT idgp, idadmin, day, start, end, status, link, name FROM charge")
    rows = sq_cur.fetchall()
    count = 0
    for r in rows:
        credit_days = max(0, int((r[4] - __import__('time').time()) / 86400))
        expire_at = datetime.fromtimestamp(r[4], tz=timezone.utc) if r[4] else None
        await pg.execute(
            "INSERT INTO group_credits "
            "(chat_id, chat_type, credit_days, expire_at, charged_by, status) "
            "VALUES ($1, 'group', $2, $3, $4, $5) ON CONFLICT (chat_id) DO NOTHING",
            r[0], credit_days, expire_at, r[1], r[5]
        )
        count += 1
    print(f"✅ group_credits (music): {count} rows migrated")

    # ── 3. Users ───────────────────────────────────────────────────────────
    sq_cur.execute("SELECT iduser FROM users")
    rows = sq_cur.fetchall()
    count = 0
    for r in rows:
        await pg.execute(
            "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
            r[0]
        )
        count += 1
    print(f"✅ users: {count} rows migrated")

    # ── 4. Music Admins ────────────────────────────────────────────────────
    sq_cur.execute("SELECT idgp, idadmin, nameadmin FROM musicadmin")
    rows = sq_cur.fetchall()
    count = 0
    for r in rows:
        await pg.execute(
            "INSERT INTO music_admins (chat_id, user_id, display_name) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            r[0], r[1], r[2]
        )
        count += 1
    print(f"✅ music_admins: {count} rows migrated")

    # ── 5. Video Admins ────────────────────────────────────────────────────
    sq_cur.execute("SELECT idgp, idadmin, nameadmin FROM videoadmins")
    rows = sq_cur.fetchall()
    count = 0
    for r in rows:
        await pg.execute(
            "INSERT INTO video_admins (chat_id, user_id, display_name) "
            "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            r[0], r[1], r[2]
        )
        count += 1
    print(f"✅ video_admins: {count} rows migrated")

    # ── 6. Player Owners (creators) ────────────────────────────────────────
    sq_cur.execute("SELECT idgp, creator FROM creators")
    rows = sq_cur.fetchall()
    count = 0
    for r in rows:
        await pg.execute(
            "INSERT INTO player_owners (chat_id, user_id) "
            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
            r[0], r[1]
        )
        count += 1
    print(f"✅ player_owners: {count} rows migrated")

    # ── 7. Sudos ───────────────────────────────────────────────────────────
    sq_cur.execute("SELECT idsudo, namesudo FROM sudo")
    rows = sq_cur.fetchall()
    count = 0
    for r in rows:
        await pg.execute(
            "INSERT INTO sudos (user_id, display_name, added_by) "
            "VALUES ($1, $2, 0) ON CONFLICT DO NOTHING",
            r[0], r[1]
        )
        count += 1
    print(f"✅ sudos: {count} rows migrated")

    # ── 8. Bot Settings (information table) ────────────────────────────────
    sq_cur.execute("SELECT start, about, groupp, adminpv, payamresan FROM information WHERE id=1")
    row = sq_cur.fetchone()
    if row:
        await pg.execute(
            "UPDATE bot_settings SET value=$1, updated_at=NOW() WHERE key='start_text'",
            row[0]
        )
        await pg.execute(
            "UPDATE bot_settings SET value=$1, updated_at=NOW() WHERE key='about_text'",
            row[1]
        )
        await pg.execute(
            "UPDATE bot_settings SET value=$1, updated_at=NOW() WHERE key='support_group_link'",
            f"https://t.me/{row[2]}"
        )
        await pg.execute(
            "UPDATE bot_settings SET value=$1, updated_at=NOW() WHERE key='developer_pv_link'",
            row[3]
        )
        await pg.execute(
            "UPDATE bot_settings SET value=$1, updated_at=NOW() WHERE key='broadcast_channel_link'",
            f"https://t.me/{row[4]}"
        )
        print("✅ bot_settings: migrated from information table")

    # ── 9. Pricing (money1 + paye tables) ──────────────────────────────────
    sq_cur.execute("SELECT nerkh1, nerkh2 FROM money1 WHERE kos=1")
    row = sq_cur.fetchone()
    if row:
        await pg.execute(
            "UPDATE bot_settings SET value=$1 WHERE key='music_rate'", str(row[0])
        )
        await pg.execute(
            "UPDATE bot_settings SET value=$1 WHERE key='video_rate'", str(row[1])
        )
        print("✅ pricing: migrated from money1 table")

    await pg.close()
    sq.close()
    print("\n🎉 Migration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
```

---

## 37. Future Update Roadmap

The codebase is designed to be fully extensible - adding a new feature requires only creating a new file in `handlers/` and `services/`, with no changes to existing code.

### How to Add a New Feature

```bash
# Step 1: Create the handler module
touch handlers/new_feature.py

# Step 2: Write the handler with standard structure
```

```python
# handlers/new_feature.py

from pyrogram import Client, filters
from pyrogram.types import Message
from utils.filters import credit_filter, music_admin_filter


def register(bot: Client, call_py):

    @bot.on_message(
        filters.command("newcommand") &
        filters.group &
        credit_filter() &
        music_admin_filter()
    )
    async def handle_new_command(client: Client, message: Message):
        # your feature logic here
        ...
```

```python
# Step 3: Add to handlers/__init__.py
from . import new_feature
# add to modules list in register_all()
```

```bash
# Step 4: If new DB columns needed:
alembic revision --autogenerate -m "add new_feature_column"
alembic upgrade head
```

### Planned Future Features (by priority)

| Priority | Feature | Description |
|----------|---------|-------------|
| 🔴 High | Multi-instance bot builder | Automated management of multiple bot tokens from one control panel |
| 🔴 High | Online payment gateway | Automatic credit purchase via Iranian payment gateway |
| 🟡 Medium | Web admin dashboard | Browser-based panel for Developer/Owner statistics |
| 🟡 Medium | Sudo referral system | Reward tracking for sudo sales network |
| 🟡 Medium | Spotify link support | Convert Spotify URLs to YouTube playback |
| 🟢 Low | Analytics charts | Visual growth charts for installs and revenue |
| 🟢 Low | Scheduled playback | Schedule tracks to play at specific times |
| 🟢 Low | Voice call recording | Save call recordings to cloud storage |

---

## 38. Quick Reference Tables

### All Database Tables

| Table | Purpose | Key Columns | Hot Indexes |
|-------|---------|-------------|-------------|
| `groups` | Installed groups | `chat_id`, `status`, `installed_by` | `status`, `installed_by` |
| `channels` | Installed channels | `chat_id`, `status` | `status` |
| `group_credits` | Subscription credits | `chat_id`, `credit_days`, `expire_at` | `chat_id`, partial on expiry/zero |
| `credit_history` | Credit audit log | `chat_id`, `operated_at` | `chat_id+date` (partitioned) |
| `invoices` | Payment invoices | `chat_id`, `status`, `issued_by` | `chat_id`, `status=pending` |
| `users` | Bot users | `user_id`, `is_banned` | `user_id`, `is_banned=true` |
| `owners` | Bot owners | `user_id`, `is_active` | `user_id` |
| `sudos` | Sales reps | `user_id`, `is_active`, `total_installs` | `user_id`, `is_active=true` |
| `music_admins` | Music admins per group | `chat_id`, `user_id` | `chat_id`, `user_id+chat_id` |
| `video_admins` | Video admins per group | `chat_id`, `user_id` | `chat_id`, `user_id+chat_id` |
| `player_owners` | Player owners per group | `chat_id`, `user_id` | `chat_id`, `user_id+chat_id` |
| `player_vips` | VIP users per group | `chat_id`, `user_id` | `chat_id`, `user_id+chat_id` |
| `chat_settings` | Per-chat config | `chat_id`, all boolean settings | `chat_id` |
| `playlists` | Playback queue | `chat_id`, `position` | `chat_id+position` |
| `favorites` | User saved tracks | `user_id`, `chat_id` | `user_id+chat_id` |
| `bot_settings` | Global key-value config | `key` (PK) | - |
| `force_join_channels` | Required join channels | `channel_id`, `is_active` | `channel_id` |
| `filter_words` | Word blacklist | `word` (unique) | - |
| `blacklist` | Blocked entities | `entity_id`, `entity_type` | `entity_id+type+active` |
| `install_logs` | Install audit | `sudo_id`, `chat_id`, `occurred_at` | `sudo_id+date`, `date` |
| `call_reports` | Playback logs | `chat_id`, `started_at` | `chat_id+date` |

### All Commands Summary

| Category | Persian | English | Access |
|----------|---------|---------|--------|
| Playback | `پخش` | `Play` | Music Admin+ |
| Playback | `پخش ویدیو` | `PlayVideo` | Video Admin+ |
| Playback | `توقف پخش` | `StopMusic` | Music Admin+ |
| Playback | `توقف ویدیو` | `StopVideo` | Video Admin+ |
| Playback | `مکث` | `Pause` | Music/Video Admin+ |
| Playback | `ازسرگیری` | `Resume` | Music/Video Admin+ |
| Playback | `بیصدا` / `باصدا` | `silent` / `unsilent` | Music Admin+ |
| Playback | `صدای موزیک {n}` | `MusicSound {n}` | Music Admin+ |
| Playback | `صدای ویدیو {n}` | `VideoSound {n}` | Video Admin+ |
| Playback | `پخش تیوی` | `PlayTv` | Music/Video Admin+ |
| Playback | `توقف تیوی` | `StopTv` | Music/Video Admin+ |
| Queue | `افزودن به لیست` | `AddToPlayList` | Music Admin+ |
| Queue | `پخش لیست` | `PlayList` | Music Admin+ |
| Queue | `توقف لیست` | `StopList` | Music Admin+ |
| Queue | `لیست پخش` | `ListPlayList` | All |
| Queue | `حذف از لیست` | `delfromPlaylist` | Music Admin+ |
| Queue | `پاکسازی لیست پخش` | `CleanPlayList` | Music Admin+ |
| Promotion | `ترفیع موزیک` | `PromotMusic` | Player Owner+ |
| Promotion | `عزل موزیک` | `DemoteMusic` | Player Owner+ |
| Promotion | `ترفیع ویدیو` | `PromotVideo` | Player Owner+ |
| Promotion | `عزل ویدیو` | `DemoteVideo` | Player Owner+ |
| Promotion | `ترفیع مالک` | `SetCreator` | Sudo+ |
| Promotion | `عزل مالک` | `DelCreator` | Sudo+ |
| Promotion | `پیکربندی موزیک` | `ConfigMusic` | Player Owner+ |
| Promotion | `پاکسازی مدیران موزیک` | `DelConfigMusic` | Player Owner+ |
| Promotion | `لیست مدیران موزیک` | `ListMusic` | Player Owner+ |
| Promotion | `پیکربندی ویدیو` | `ConfigVideo` | Player Owner+ |
| Promotion | `پاکسازی مدیران ویدیو` | `DelConfigVideo` | Player Owner+ |
| Promotion | `لیست مدیران ویدیو` | `ListVideo` | All |
| Promotion | `لیست مالکان` | `CreatorsList` | All |
| Credit | `آپدیت شارژ {days}` | - | Sudo+ |
| Credit | `آپدیت شارژ ویدیو {days}` | - | Sudo+ |
| Utility | `پینگ` | `Ping` | All |
| Utility | `ربات` | `bot` / `robot` | All |

---

*Documentation Version: 2.0.0 - Based on full source code analysis and client requirements*
*Last updated: 2026-02-24*
*Ready for Phase 1 development - all features mapped, database fully designed*


---

# Addendum A: Missing or Under-Specified Areas (Mandatory Patch for v2.1)

This addendum **does not remove or change** any earlier section.
It only specifies what must be added for correctness, safety, and long-term stability.

Contents:
- A1. Persian-only text system (JSON driven)
- A2. Multi-helper account pool (Helper Ban Risk mitigation)
- A3. CLI for helper pool management (add, remove, list, test, rotate)
- A4. Financial anti-abuse: Sudo Wallets (bulk credit, audited deductions)
- A5. State recovery after restart (resume active calls)
- A6. Concurrency hardening: distributed locks for queue and credit operations
- A7. Database schema additions and migrations (chat_settings fields, helper tables, sudo wallets, playback state)
- A8. Memory and process garbage collection for PyTgCalls and FFmpeg (long-running stability)

---

## A1. Persian-only Text System (JSON Driven, No Hardcoded UI Text)

### A1.1 Goal and hard requirements

All user-facing texts must be **Persian only**, including:
- Message bodies
- Inline keyboard button titles
- Reply keyboard titles
- Panel headings and descriptions
- Error texts and warnings
- Help texts and tariffs
- Confirmation prompts

Hard rule:
- No text strings are hardcoded inside handler code.
- All texts are loaded via a **single JSON file** and referenced by key.

This requirement is aligned with existing Persian phrases already present in the legacy `main.py` source. For example, the legacy file currently uses Persian button titles like `• شبکه 2` and messages like `• جهت پخش، یکی از شبکه های زیر را انتخاب کنید :`.

### A1.2 File layout for texts

Add a new folder:
- `resources/strings/`
  - `fa.json`  (the only language file in Phase 1)

Optional future:
- `en.json`, `ar.json` can be added later, but Phase 1 stays Persian only.

### A1.3 JSON structure rules (nested and clean)

Use a strict nested hierarchy to keep it maintainable.
Keys must be stable.
Callback data values must not be localized.
Only visible texts are localized.

Example skeleton (`resources/strings/fa.json`):

```json
{
  "meta": {
    "language": "fa",
    "fallback": "fa",
    "version": "1.0.0"
  },

  "common": {
    "buttons": {
      "back": "بازگشت",
      "close": "بستن",
      "cancel": "لغو",
      "confirm": "تایید",
      "next": "بعدی",
      "prev": "قبلی",
      "stop": "توقف",
      "pause": "مکث",
      "resume": "ازسرگیری"
    },
    "errors": {
      "no_access": "شما دسترسی ندارید.",
      "not_installed": "پلیر در این چت نصب نیست.",
      "invalid_number": "عدد وارد شده معتبر نیست.",
      "try_later": "لطفا چند ثانیه بعد دوباره تلاش کنید."
    }
  },

  "start": {
    "welcome": "سلام {mention} عزیز! 🎵 به ربات موزیک پلیر خوش آمدی",
    "menu_title": "منوی اصلی",
    "menu": {
      "force_join": "نمایش عضویت اجبار",
      "pricing": "تعرفه ربات",
      "buy_from_creator": "خرید از سازنده",
      "buy_from_sudo_1": "خرید از سودو اول",
      "buy_from_sudo_2": "خرید از سودو دوم",
      "guide_channel": "کانال راهنما",
      "bot_channel": "کانال ربات",
      "support_group": "گروه پشتیبانی",
      "custom_link": "لینک دلخواه",
      "add_to_group": "افزودن به گروه",
      "add_to_channel": "افزودن به کانال"
    }
  },

  "playback": {
    "choose_source": "• جهت پخش، یکی از گزینه های زیر را انتخاب کنید:",
    "choose_tv": "• جهت پخش، یکی از شبکه های زیر را انتخاب کنید :",
    "now_playing": "⌯ در حال پخش میباشد 🔊\n⊹ نام درخواست کننده : {user}\n⊹ ساعت : `{datetime}` 📅",
    "types": {
      "audio": "صوتی",
      "video": "تصویری",
      "tv": "تلویزیون",
      "satellite": "ماهواره",
      "radio": "رادیو",
      "download": "دانلود رسانه"
    }
  },

  "panels": {
    "developer": {
      "title": "پنل برنامه نویس",
      "status": "وضعیت ربات",
      "increase_credit": "افزایش اعتبار",
      "decrease_credit": "کسر اعتبار"
    },
    "owner": {
      "title": "پنل مالک ربات",
      "stats": "نمایش امار",
      "bots_credit": "نمایش اعتبار ربات"
    },
    "sudo": {
      "title": "پنل سودو",
      "installs_report": "گزارش نصب",
      "credit_report": "گزارش اعتبار"
    },
    "group": {
      "title": "پنل گروه",
      "settings": {
        "music_video": "فعال سازی موزیک ویدیو",
        "security_call": "فعالسازی امنیت کال",
        "repeat": "تکرار",
        "queue": "لیست انتظار"
      }
    }
  }
}
```

Notes:
- Use `{placeholders}` for dynamic data.
- Keep the Persian punctuation and spacing consistent.
- Persian numerals are optional. Use standard digits `0-9` unless explicitly requested.

### A1.4 Runtime loader and formatter contract

Add a minimal translation utility:

- `utils/i18n.py`
  - `load_strings(language="fa")`
  - `t(key: str, **kwargs) -> str`

Rules:
- Dot-path lookup: `t("common.buttons.back")`
- Missing key should return a safe placeholder like: `"[missing:common.buttons.back]"` and log to log channel.
- Strings are formatted with `.format(**kwargs)`.

### A1.5 Removing hardcoded env texts (deprecation only)

The existing `config.env` currently contains text values like:

- `START_TEXT=...` (Persian content)
- `DEVELOPER_LINK=...`
- `SUPPORT_GROUP_LINK=...`

These can remain for backward compatibility, but the v2.1 rule is:
- All message texts and button titles come from JSON.
- Env and DB settings hold only IDs, links, and numeric rates.

The presence of `HELPER_PHONE` as a single account is also deprecated and replaced by the helper pool described in A2. The legacy single helper setting appears in `config.env` and the example `main.py` entry point.

---

## A2. Multi-Helper Account Pool (Account Pool Design)

### A2.1 Why this is mandatory

Telegram is sensitive to user accounts that join many groups and start voice chats.
Using only one helper account creates a single point of failure.

In the current documentation, only one helper phone is defined (`HELPER_PHONE`) and used to create a single userbot client.

This must be replaced with a pool of helper accounts with load balancing.

### A2.2 What "multi-account" means in this project

Multi-account refers to:
- Multiple helper userbot accounts (MTProto) used for streaming into voice chats.
- The bot token remains single.
- Each chat (group/channel) is assigned to one helper account at a time.
- The assignment is stored in DB so it survives restarts.

### A2.3 Helper account states

Each helper account has a state machine:

- `active`: usable for joining and streaming
- `cooldown`: temporarily paused due to errors or rate limits
- `disabled`: manually disabled by Developer or Owner
- `banned`: Telegram ban, cannot be used until recovered
- `error`: needs manual inspection

### A2.4 Capacity constraints

Add per-account limits:

- `max_concurrent_calls` (default 50)
- `max_joins_per_hour` (default 300)
- `max_downloads_per_hour` (optional)
- `cooldown_seconds` after specific errors

The scheduler chooses the least-loaded eligible account.

### A2.5 Persistent assignment rule

To reduce rejoin churn and reduce spam signals:
- Once a chat is bound to a helper, it stays bound unless:
  - The helper becomes banned/disabled/error
  - The chat is explicitly rotated to another helper (CLI)
  - The chat had no activity for a long time (optional policy)


### A2.6 Session Security (Encrypted Sessions at Rest)

**Problem:** storing MTProto session strings as plaintext in the database would allow full helper account takeover if the DB is leaked.

**Mandatory rule in this project:**
- Helper sessions are **never** stored plaintext.
- Database column must be encrypted at rest.
- The encryption key is stored **only** in `config.env`.

Implementation contract:
- DB column name: `session_string_enc` (already defined in A7.2).
- Algorithm: `cryptography.fernet.Fernet` (symmetric AEAD).
- Key: `HELPER_SESSION_KEY` (32-byte urlsafe base64 key).

Storage rules:
- Before INSERT/UPDATE: `session_string_enc = fernet.encrypt(session_string.encode("utf-8")).decode("utf-8")`
- Before use: `session_string = fernet.decrypt(session_string_enc.encode("utf-8")).decode("utf-8")`
- Do not print session strings in logs (even in debug).

Key rotation policy (recommended, safe for production):
- Support two keys in env:
  - `HELPER_SESSION_KEY_CURRENT`
  - `HELPER_SESSION_KEY_OLD` (optional)
- On read: try CURRENT, then OLD.
- Provide CLI command to re-encrypt all sessions using CURRENT and clear OLD.

CLI additions (extends A3):
- `python -m tools.helper_pool_cli rotate-key`
  - Reads all helper sessions using CURRENT/OLD
  - Re-encrypts with CURRENT
  - Writes back
  - Emits a Persian log message to log channel (via JSON): `t("helper.security.key_rotated")`

### A2.7 Auto-Ban Detection, Quarantine, and Owner Alerts

**Problem:** if a helper becomes restricted, kicked, or flagged, repeated retries using the same helper causes failures and can spread the issue to more chats.

Mandatory behavior:
1) Detect ban/limit signals in the playback/join pipeline.
2) Quarantine the helper immediately.
3) Reassign the chat to a different helper.
4) Notify Owner and Developer in Persian (log channel + optional DM).

Errors that trigger quarantine (examples):
- `UserIsRestricted`
- `UserDeactivatedBan`
- `PeerFlood`
- `FloodWait`
- `ChatAdminRequired` (kicked or lost admin rights in a channel)
- `YouBlockedUser` (rare but possible)
- `InviteHashExpired` / `UserAlreadyParticipant` should **not** quarantine, handle separately.

DB changes (minimal, aligned with A7.2):
- Add fields to `helper_accounts`:

```sql
ALTER TABLE helper_accounts
ADD COLUMN cooldown_until TIMESTAMPTZ,
ADD COLUMN banned_until    TIMESTAMPTZ,
ADD COLUMN last_error      TEXT,
ADD COLUMN last_error_at   TIMESTAMPTZ;
```

Runtime rules:
- On `FloodWait(seconds)`:
  - set `status='cooldown'`
  - set `cooldown_until = now() + interval '{seconds} seconds'`
  - do **not** use this helper for new joins until cooldown ends
- On hard restriction/ban:
  - set `status='banned'` (or `disabled` if manual action required)
  - set `banned_until = now() + interval '24 hours'` (configurable) unless Telegram provides a duration
- On admin loss or forced leave:
  - set `status='cooldown'` for a short time
  - notify Owner to re-check permissions

Notification contract (Persian-only, JSON keys):
- Log channel: `t("helper.health.quarantined")` with helper id, masked phone, reason, affected chat_id
- Owner DM (optional): `t("helper.health.owner_alert")`
- Developer DM (optional): `t("helper.health.dev_alert")`

Helper selection must ignore non-eligible helpers:
- eligible if:
  - `status='active'`
  - `cooldown_until IS NULL OR cooldown_until < now()`
  - `banned_until IS NULL OR banned_until < now()`
  - `current_active_calls < max_concurrent_calls`

A scheduled watchdog (recommended, ties into Section 27):
- `helper_health_watchdog()` checks for:
  - helpers stuck in cooldown past cooldown_until
  - helpers that fail `get_me()` in a health probe
  - and auto-restores status to `active` when safe

---

## A3. CLI for Helper Pool Management (Mandatory)

### A3.1 CLI goals

- Add helper accounts safely (login, OTP, 2FA support)
- Store their session strings securely
- Enable, disable, list, and test helpers
- Rotate a specific chat to another helper
- Validate pool health before large installs

### A3.2 CLI entry point and commands

Add a CLI module:
- `tools/helper_pool_cli.py`

Command name:
- `python -m tools.helper_pool_cli ...`

Commands:

1) Add helper account

```bash
python -m tools.helper_pool_cli add --phone "+989123456789"
```

Behavior:
- Prompts OTP code
- If 2FA enabled, prompts password
- Creates a Pyrogram session string
- Encrypts and stores in DB

2) List helper accounts

```bash
python -m tools.helper_pool_cli list
python -m tools.helper_pool_cli list --all
```

Outputs:
- id
- masked phone
- status
- max_concurrent_calls
- current_active_calls
- last_used_at

3) Disable or enable

```bash
python -m tools.helper_pool_cli disable --id 3
python -m tools.helper_pool_cli enable  --id 3
```

4) Set capacity

```bash
python -m tools.helper_pool_cli set-capacity --id 3 --max-calls 40
```

5) Test login

```bash
python -m tools.helper_pool_cli test --id 3
```

Behavior:
- Connects using stored session
- Calls `get_me()`
- Sends a short health message to `LOG_CHANNEL_ID` (optional)

6) Rotate chat binding

```bash
python -m tools.helper_pool_cli rotate-chat --chat-id -100123456789 --target-helper-id 5
```

Behavior:
- Updates binding table
- Marks old helper call count minus one after leaving (if active)
- Triggers resume workflow (A5)

### A3.3 Secure session storage

Store session strings encrypted at rest.

Config additions:

```env
# Encrypt helper sessions stored in DB
HELPER_SESSION_KEY=base64_32_bytes_key

# Default helper limits
HELPER_DEFAULT_MAX_CALLS=50
HELPER_DEFAULT_MAX_JOINS_PER_HOUR=300
```

Production note:
- Prefer `HELPER_SESSION_KEY_CURRENT` (+ optional `HELPER_SESSION_KEY_OLD`) for key rotation support.
- `HELPER_SESSION_KEY` may be treated as an alias of `HELPER_SESSION_KEY_CURRENT` for backward compatibility.

Encryption rule:
- Use symmetric encryption (Fernet) with a 32-byte key
- Never print session strings to logs

### A3.4 Helper pool selection algorithm

Pseudo logic:

- Query helpers where `status='active'`
- Filter: `current_active_calls < max_concurrent_calls`
- Sort by:
  1) `current_active_calls` asc
  2) `last_used_at` asc
- Choose first

If none available:
- return a clear Persian error to the requester:
  - `t("common.errors.try_later")`

---

## A4. Financial Anti-Abuse: Sudo Wallets (Bulk Credit)

### A4.1 Problem statement

In the current feature set, Sudos can charge groups with commands like `آپدیت شارژ {days}`.
This is present in the command summary.

Without a wallet limit, a malicious sudo can give unlimited free credit.

### A4.2 Mandatory fix

Introduce a sudo wallet system:

- Sudos can only charge credit if their sudo wallet has enough balance.
- Only Developer or Owner can sell "bulk balance" to a sudo.
- Every credit charge creates an immutable transaction record.

### A4.3 Data model

Add these tables:

#### Table: `sudo_wallets`

```sql
CREATE TABLE sudo_wallets (
  sudo_user_id        BIGINT PRIMARY KEY REFERENCES sudos(user_id) ON DELETE CASCADE,
  balance_toman       BIGINT NOT NULL DEFAULT 0,
  locked_toman        BIGINT NOT NULL DEFAULT 0,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sudo_wallets_balance ON sudo_wallets(balance_toman);
```

#### Table: `sudo_wallet_transactions`

```sql
CREATE TABLE sudo_wallet_transactions (
  id                 BIGSERIAL PRIMARY KEY,
  sudo_user_id        BIGINT NOT NULL REFERENCES sudos(user_id) ON DELETE CASCADE,
  amount_toman        BIGINT NOT NULL,              -- positive or negative
  reason              TEXT NOT NULL,                -- e.g. "bulk_topup", "group_charge", "refund"
  ref_type            TEXT,                         -- e.g. "invoice", "charge_log"
  ref_id              BIGINT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sudo_wallet_tx_sudo_time ON sudo_wallet_transactions(sudo_user_id, created_at DESC);
CREATE INDEX idx_sudo_wallet_tx_ref ON sudo_wallet_transactions(ref_type, ref_id);
```

#### Table: `sudo_bulk_invoices` (optional but recommended)

```sql
CREATE TABLE sudo_bulk_invoices (
  id                 BIGSERIAL PRIMARY KEY,
  sudo_user_id        BIGINT NOT NULL REFERENCES sudos(user_id) ON DELETE CASCADE,
  amount_toman        BIGINT NOT NULL,
  issued_by           BIGINT NOT NULL,          -- owner/developer user_id
  issued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  paid_at             TIMESTAMPTZ,
  status              TEXT NOT NULL DEFAULT 'issued'  -- issued, paid, void
);

CREATE INDEX idx_sudo_bulk_invoices_sudo ON sudo_bulk_invoices(sudo_user_id, status);
```

### A4.4 Charging flow (atomic, non-abusable)

When sudo runs `آپدیت شارژ {days}`:

1) Compute cost:
- `cost = days * MUSIC_RATE` for music
- `cost = days * VIDEO_RATE` for video
- add `BASE_CREDIT_RATE` when first activation or install (policy controlled)

2) Start DB transaction
3) Lock sudo wallet row `FOR UPDATE`
4) Verify `balance_toman - locked_toman >= cost`
5) Deduct wallet
6) Extend group credit (or set expire_at)
7) Insert transaction record
8) Commit

If insufficient wallet:
- Return Persian error message:
  - "موجودی کیف پول سودو کافی نیست."

### A4.5 Owner top-up commands (only Owner or Developer)

Add Owner or Developer panel actions to:
- Create invoice
- Mark invoice paid
- Increase sudo wallet

All outputs must be Persian via JSON keys.
### A4.6 Wallet Audit: Owner-to-Sudo Sales Ledger (Mandatory)

**Problem:** Sudos spend from `sudo_wallets`, but the system must also record *how* that wallet was funded, especially when the Owner sells bulk credit to a Sudo. This is required for accounting and dispute resolution.

Mandatory solution:
- Every time the Owner sells bulk credit to a Sudo, create an immutable ledger row.

Add table: `owner_sales`

```sql
CREATE TABLE owner_sales (
  id                 BIGSERIAL PRIMARY KEY,
  owner_user_id      BIGINT NOT NULL REFERENCES users(id),
  sudo_user_id       BIGINT NOT NULL REFERENCES users(id),
  chat_scope         TEXT NOT NULL DEFAULT 'bulk',     -- bulk, per_chat (future)
  units              INT NOT NULL,                     -- example: days or credits
  price_irr          BIGINT NOT NULL,                  -- price paid (IRR)
  payment_ref        TEXT,                             -- card tx ref / tracking code
  note               TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_owner_sales_owner ON owner_sales(owner_user_id, created_at DESC);
CREATE INDEX idx_owner_sales_sudo  ON owner_sales(sudo_user_id, created_at DESC);
```

Linking sales to wallet top-ups:
- When a sale happens:
  1) Insert into `owner_sales`
  2) Insert into `sudo_wallet_transactions` with:
     - `type='topup'`
     - `source='owner_sale'`
     - `ref_id = owner_sales.id`
  3) Update `sudo_wallets.balance_days += units` (atomic transaction)

Owner panel reporting (add commands):
- `گزارش فروش به سودو`:
  - filters by date range
  - shows totals per sudo
- Export option (CSV) in developer environment is allowed, but the bot reply remains Persian.

Sudo transparency (optional but recommended):
- Allow Sudo to see their own purchase history (only their rows):
  - `سوابق خرید اعتبار عمده`

Anti-tamper:
- Owner cannot delete sales rows via bot.
- Only DB admin can delete, and it must be treated as an incident.

---

## A5. State Recovery After Restart (Resume Active Calls)

### A5.1 Why this is required

If the server restarts or crashes, active voice chats are interrupted.
The documentation currently defines `call_reports` with `ended_at` but has no restart recovery plan.

Required behavior:
- On startup, detect unfinished call sessions and attempt to resume.

### A5.2 New table: `playback_states` (persistent resume data)

```sql
CREATE TABLE playback_states (
  chat_id             BIGINT PRIMARY KEY,
  helper_account_id   BIGINT REFERENCES helper_accounts(id),
  media_type          TEXT NOT NULL,                 -- audio, video, tv, radio, link
  source              TEXT NOT NULL,                 -- url, file_id, local_path, ytdlp_id
  title               TEXT,
  duration_sec        INT,
  queue_position      INT,
  seek_sec            INT NOT NULL DEFAULT 0,
  is_paused           BOOLEAN NOT NULL DEFAULT FALSE,
  last_update_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_playback_states_helper ON playback_states(helper_account_id);
CREATE INDEX idx_playback_states_last_update ON playback_states(last_update_at DESC);
```

### A5.3 Update `call_reports` to store helper assignment

Add column:

```sql
ALTER TABLE call_reports
ADD COLUMN helper_account_id BIGINT REFERENCES helper_accounts(id);
```

### A5.4 Resume algorithm (startup routine)

At application startup:

1) Query all active sessions:
- `SELECT chat_id FROM call_reports WHERE ended_at IS NULL`

2) For each chat:
- Load `playback_states` row
- Validate chat is still installed and active
- Pick helper:
  - If stored helper is active, use it
  - Else choose from pool (A3.4)

3) Join voice chat
4) Resume media:
- If source is a file, use FFmpeg with `-ss {seek_sec}`
- If source is a stream, best-effort restart from live point
- If source is YouTube, re-run yt-dlp to get stream URL and seek if supported

5) Update logs:
- Write a Persian notification message to log channel:
  - "ریکاوری پخش پس از ری استارت انجام شد."

6) If resume fails due to closed voice chat:
- Mark `ended_at` now and clear state.

### A5.4.1 Staggered Recovery (FloodWait-safe Startup Resume)

**Problem:** if the server restarts while many calls are active (for example 1000 chats), attempting to recover all of them instantly will send a burst of join requests and trigger `FloodWait`, risking temporary blocks or helper bans.

**Mandatory behavior:**
- Recovery must be rate-limited and queued.
- At most **N** resume attempts per second globally, and at most **M** per helper per minute.

Config additions (example defaults):
```env
RECOVERY_GLOBAL_PER_SECOND=5
RECOVERY_MAX_CONCURRENT=10
RECOVERY_JITTER_MS=150
RECOVERY_HELPER_PER_MINUTE=120
```

Queue model:
- Build a list of `chat_id` from `call_reports WHERE ended_at IS NULL`.
- Enqueue into an in-memory async queue at startup.
- Start a worker pool with concurrency = `RECOVERY_MAX_CONCURRENT`.
- Each worker:
  1) waits for a token from a global rate limiter (`RECOVERY_GLOBAL_PER_SECOND`)
  2) waits for helper-specific token bucket (`RECOVERY_HELPER_PER_MINUTE`)
  3) adds small jitter sleep (`0..RECOVERY_JITTER_MS`) to avoid synchronized spikes
  4) executes the resume routine (A5.4)

FloodWait handling:
- If Telegram returns `FloodWait(seconds)`:
  - immediately stop using that helper
  - update `helper_accounts`:
    - `status='cooldown'`
    - `cooldown_until = now() + seconds`
    - `last_error='FloodWait'`
  - re-enqueue the chat with a delay (sleep `seconds + random(1..3)`)

Recovery scheduling (recommended):
- Start recovery workers **after** bot login and DB/Redis readiness checks.
- Log startup summary in Persian:
  - `t("recovery.start", count={pending_count})`
- Log progress every 100 recovered chats:
  - `t("recovery.progress", done={done}, pending={pending})`

Important:
- If a voice chat no longer exists, mark call ended and clear `playback_states` (same as A5.4 step 6).
- Never spam groups during recovery; user-visible message should be optional and minimal.

### A5.5 Seek update policy

To keep seek state accurate without high DB write load:

- Update `seek_sec` every 5 seconds per active call in Redis
- Persist to DB every 30 seconds
- Persist immediately on stop/pause/next/prev

---

## A6. Concurrency Hardening (Distributed Locks + DB Constraints)

### A6.1 Problem statement

Even with DB unique constraints, concurrent operations can create conflicts.

Example risk:
- Two admins add items at the same time
- Both compute `position=1` before insert
- Unique constraint can reject one insert, but the user will see inconsistent behavior

The model already has a unique constraint on playlist positions per chat.

We must add a distributed lock so that the queue and credit operations become deterministic.

### A6.2 Redis lock contract

Lock key:
- `lock:chat:{chat_id}`

Acquire:
- `SET lock:chat:{chat_id} {token} NX PX 5000`

Release:
- Lua script that deletes only if token matches

Rules:
- Lock TTL: 3 to 5 seconds
- Retry: 10 to 20 attempts with small jitter (20 to 50 ms)

### A6.3 Operations that must be locked

Per-chat lock is mandatory for:

- Playlist changes:
  - add to queue
  - remove from queue
  - reorder
  - next, prev (when they modify queue)
  - clear queue

- Credit changes:
  - sudo charge
  - owner charge
  - deduct
  - trial activation
  - expiry tasks

- Chat settings toggles that influence playback:
  - repeat
  - queue enabled
  - smart radio enabled
  - vote skip enabled

### A6.4 DB transaction discipline

Inside the lock:
- Always use a DB transaction.
- Use `SELECT ... FOR UPDATE` for:
  - wallet rows
  - credit rows
  - playback state rows
  - the "queue head" selection when needed

If Redis is down:
- Fall back to Postgres advisory locks:
  - `SELECT pg_advisory_lock(chat_id)`
  - `SELECT pg_advisory_unlock(chat_id)`

---

## A7. Database Schema Additions and Migrations

### A7.1 chat_settings missing fields

Add these fields to `chat_settings`:

- `vote_skip_enabled` BOOLEAN NOT NULL DEFAULT FALSE
- `smart_radio_enabled` BOOLEAN NOT NULL DEFAULT FALSE
- `language` VARCHAR(8) NOT NULL DEFAULT 'fa'

Migration:

```sql
ALTER TABLE chat_settings
ADD COLUMN vote_skip_enabled BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN smart_radio_enabled BOOLEAN NOT NULL DEFAULT FALSE,
ADD COLUMN language VARCHAR(8) NOT NULL DEFAULT 'fa';
```

### A7.2 Helper pool tables

Add:

- `helper_accounts`
- `helper_chat_bindings`

```sql
CREATE TABLE helper_accounts (
  id                      BIGSERIAL PRIMARY KEY,
  phone                   VARCHAR(20) UNIQUE NOT NULL,
  session_string_enc      TEXT NOT NULL,
  status                  TEXT NOT NULL DEFAULT 'active',
  max_concurrent_calls    INT NOT NULL DEFAULT 50,
  current_active_calls    INT NOT NULL DEFAULT 0,
  max_joins_per_hour      INT NOT NULL DEFAULT 300,
  joins_last_hour         INT NOT NULL DEFAULT 0,
  last_login_at           TIMESTAMPTZ,
  last_used_at            TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  notes                   TEXT
);

CREATE INDEX idx_helper_accounts_status_calls ON helper_accounts(status, current_active_calls);
CREATE INDEX idx_helper_accounts_last_used ON helper_accounts(last_used_at);

CREATE TABLE helper_chat_bindings (
  chat_id                 BIGINT PRIMARY KEY,
  helper_account_id       BIGINT NOT NULL REFERENCES helper_accounts(id),
  bound_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_helper_chat_bindings_helper ON helper_chat_bindings(helper_account_id);
```

### A7.2.1 Helper quarantine fields (required by A2.7)

If you created the table from the A7.2 CREATE statement and want to enable quarantine and cooldown logic, add:

```sql
ALTER TABLE helper_accounts
ADD COLUMN cooldown_until TIMESTAMPTZ,
ADD COLUMN banned_until    TIMESTAMPTZ,
ADD COLUMN last_error      TEXT,
ADD COLUMN last_error_at   TIMESTAMPTZ;
```

Indexes (recommended):
```sql
CREATE INDEX idx_helper_accounts_cooldown_until
  ON helper_accounts(cooldown_until)
  WHERE cooldown_until IS NOT NULL;

CREATE INDEX idx_helper_accounts_banned_until
  ON helper_accounts(banned_until)
  WHERE banned_until IS NOT NULL;

CREATE INDEX idx_helper_accounts_status_cooldown
  ON helper_accounts(status, cooldown_until);

CREATE INDEX idx_helper_accounts_status_ban
  ON helper_accounts(status, banned_until);
```

Note:
- If you re-generate the schema from scratch, you may include these columns directly in the CREATE TABLE statement.
### A7.3 Playback resume tables

Add `playback_states` (see A5.2).

### A7.4 Sudo wallet tables

Add `sudo_wallets`, `sudo_wallet_transactions`, optionally `sudo_bulk_invoices` (see A4.3).

### A7.5 Index hygiene

The documentation already highlights important indexes for core tables such as `users`, `sudos`, `chat_settings`, `playlists`, and `call_reports`.

For the new tables, indexes in A7.2 and A4.3 are mandatory.

---

## A8. Memory and Process Garbage Collection (PyTgCalls, FFmpeg)

### A8.1 Why this is required

The current scheduled job `cleanup_downloads` only describes disk cleanup. It deletes files older than 30 minutes.

This does not address:
- in-memory caches held by the streaming layer
- FFmpeg processes that can remain alive if not handled carefully
- long-running memory growth that leads to instability

### A8.2 Mandatory process registry

Maintain an in-memory registry:
- per chat_id:
  - current ffmpeg pid
  - current download task
  - current stream source
  - last activity time

On stop, next, and leave:
- terminate the ffmpeg process if it exists
- await task cancellations
- clear references so Python GC can reclaim memory

### A8.3 Periodic streaming engine recycle

Add a scheduler job:

- Every 6 hours:
  - Stop all PyTgCalls instances safely
  - Recreate them from helper pool sessions
  - Run the state recovery routine (A5) to resume unfinished calls

This pattern prevents memory leaks from accumulating for weeks.

### A8.3.1 Graceful Recycle (Idle-only, No User-Facing Interruptions)

**Problem:** recycling *all* PyTgCalls instances every 6 hours can interrupt active listeners and creates a bad user experience.

**Correct implementation (mandatory):**
- Only recycle helpers that are currently **idle**.
- If a helper is busy (streaming), postpone recycle until it becomes idle.
- The "memory threshold safeguard" (A8.4) may still force an immediate recycle when the process is in danger, but the default periodic recycle must be graceful.

Definitions:
- **Idle helper**: `current_active_calls == 0` and no active FFmpeg process in the registry (A8.2).
- **Busy helper**: any active call or running FFmpeg.

Algorithm:
1) Every 6 hours, run `graceful_recycle_tick()`:
   - for each helper:
     - if idle: recycle now
     - if busy: mark `recycle_pending=True` in memory (or store in a small table if you want persistence)
2) When a busy helper finishes playback (stop/ended/leave):
   - if `recycle_pending=True`:
     - recycle the helper immediately (before accepting a new chat)
     - clear the flag

Recycle procedure (idle helper):
- Stop PyTgCalls instance safely
- Ensure all FFmpeg processes are terminated (registry)
- Drop references
- Recreate the PyTgCalls instance using the decrypted session
- Run a lightweight `get_me()` probe
- Set `last_used_at = now()`

User-facing behavior:
- No user message is required.
- Only log to log channel in Persian via JSON:
  - `t("gc.recycle_idle_done", helper_id=...)`

Edge cases:
- If a helper stays busy for too long (example > 12 hours nonstop):
  - keep postponing periodic recycle
  - but if memory rises near threshold, allow A8.4 to force recycle.

Implementation note:
- Because helpers are pooled, recycling one helper does not disrupt other helpers.

### A8.4 Memory threshold safeguard

Add a health check extension:
- If RSS memory exceeds threshold (example 70 percent of RAM):
  - Trigger an immediate recycle (A8.3)
  - Log to log channel in Persian:
    - "مصرف رم بالا است، ریست ایمن انجام شد."

### A8.5 Download pipeline memory discipline

Rules:
- Never load full media bytes into memory.
- Always stream or use file paths.
- Enforce `MAX_DOWNLOAD_SIZE_MB` and `DOWNLOAD_SEMAPHORE` already present in config to prevent overload.

---

# End of Addendum A
