# Database and Schema Architecture

This document describes the canonical database architecture for the Telegram Voice Chat Music & Player Bot. The database layer uses PostgreSQL 16 (production), `asyncpg`, SQLAlchemy 2.0, and Alembic for migrations.

## Core Setup

- **Engine URL**: Sourced from `settings.DATABASE_URL` (default: `postgresql+asyncpg://...`).
- **Test Mode Engine**: When `TEST_MODE=1` is set, the application uses SQLite via `aiosqlite` utilizing a `NullPool` to avoid cross-event-loop contamination (`sqlite+aiosqlite:///./.pytest-test-mode.db`).
- **Connection Pool**: Production uses a `QueuePool` with size 20, max overflow 40, and pool recycle at 1800s. Prepared statement caching is disabled at the `asyncpg` driver level to prevent parameterized statement conflicts.

## Alembic Migrations

Migrations follow a strictly linear chain inside `app/database/migrations/versions`.
> [!IMPORTANT]
> The current migration head is **`0033_instance_database_ownership`**.
> Production runs must explicitly pass Alembic schema checks at startup (`ensure_database_schema_ready()`), otherwise the application fails fast.

## Models Overview (51 Total)

All models inherit from the `Base` declarative base class in `app/database/models.py`.

### Identity Models

*   **`BotInstanceMetadata`**: Singleton database ownership marker. Startup rejects an `INSTANCE_ID` mismatch; shared-schema operation is unsupported.
*   **`Group` & `Channel`**: Primary records for chats. Note that business relationships do *not* strictly enforce foreign keys to these tables for metadata like credits. Instead, relationships use `viewonly=True` with `foreign()` annotations to support cross-type context.
*   **`User`**: Tracks normal bot users and ban state.
*   **`Owner` & `Sudo`**: Global permission tables mapping unique `user_id` to operational rights and sales metrics.
*   **`HelperAccount`**: Stores pyrogram helper session info, `session_string_enc`, proxy routing, and concurrent limits. Contains a critical partial unique index on `session_fingerprint` to enforce uniqueness only for non-null fingerprints.

### Canonical State Models

*   **`GroupCredit`**: **The absolute canonical source of truth for all chat credits.**
    *   Composite identity: `(chat_id, chat_type)`
    *   Row-level locking (`with_for_update()`) is strictly used in repositories for concurrency safety.
*   **`ChatSettings`**: Tracks enabled/disabled states for features per chat.
    *   Composite identity: `(chat_id, chat_type)`
*   **`CallSecuritySettings`**: Holds boolean flags for security features. PK is `chat_id`.
*   **`GroupMemberMembership`**: Tracks chat member lifecycle. Composite PK `(chat_id, user_id)`.

### Operational Models

*   **`CreditHistory`**: Audit log for every credit transaction. Do not "casually repair" these rows; they are historically immutable.
*   **`Invoices` & `InvoiceItem`**: Tracks generated and paid manual invoices.
*   **`SudoWallet` & `SudoWalletTransaction`**: Financial ledgers for sudo owners.
*   **`HelperEvent` & `MediaEvent`**: Append-only analytics logs mapping helper utilization and media resolution URLs.
*   **`PlaybackState`**: Single-column PK on `chat_id` recording the last known playback progress.

### Administrative Models

*   **`MusicAdmin`, `VideoAdmin`, `PlayerOwner`, `PlayerVip`**: Track per-chat elevated permissions. Unique composite constraints exist on `(chat_id, user_id)`.
*   **`BotSetting`**: String-based PK key-value store for global remote configurations.
*   **`FilterWord`, `Blacklist`, `GlobalBan`**: Content and access gating layers.
*   **`Broadcast`**: Stores background job payloads and target audience queries.
*   **`CallReport`**: Append-only voice-call participation analytics. Migration **`0026_call_reports_helper_idx`** adds non-unique index `idx_call_reports_helper_account_id` on `helper_account_id` for helper-scoped report queries.
*   **`PlayerDeputy` / `PlayerVip`**: Migration **`0027_player_deputies_vip_expiry`** adds the explicit `player_deputies` role table and nullable `player_vips.expires_at` for permanent/timed VIP support.
*   **Start customization**: Migration **`0029_start_customization_schema`** adds scoped start-message pools, semantic button settings, and customization audit records.
*   **YouTube cookie sessions**: Migrations **`0030_youtube_cookie_session_pool`** and **`0031_youtube_cookie_hardening`** add the session pool and harden its persisted state.
*   **Fast-Creat token pools**: Migration **`0032_fast_creat_token_pool`** adds encrypted provider-separated token storage and secret-free audit events.

### BotSetting key families (selected)

Per-chat feature toggles that do not warrant dedicated tables are stored in `bot_settings`:

| Key pattern | Purpose |
| --- | --- |
| `call_stats:{chat_id}:enabled` | Call-stats panel and `آمار کال` text command gate |
| `id_command:{chat_id}:output_mode` | Id command output: `simple` or `photo` |
| `id_command:{chat_id}:show_call_stats` | Whether Id output includes call-stats block |
| `group_text_call:{chat_id}:*` | Scheduled end, auto-stats, mute/comment, title for slash-free call commands |

## Repositories (`app/repositories/`)
Database interaction avoids raw `session.execute()` calls in services and handlers. Instead, discrete async functions inside repository modules accept the standard dependencies. All queries use SQLAlchemy 2.0 style syntax (`select()`, `update()`, etc.).
