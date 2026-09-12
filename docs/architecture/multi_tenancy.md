# Owner Scope & Single-Bot Tenancy

> Last verified against repository: 2026-07-19

**Scope of this document:** How data is scoped in the **current** deployment model (one bot process, one PostgreSQL database, one Redis). For live owner/sudo permission behavior, see [features/owner_sudo_permissions.md](../features/owner_sudo_permissions.md) and [features/text_links_and_start.md](../features/text_links_and_start.md).

## Current model (implemented)

The product is **single-bot SaaS**: one Telegram bot token per deployment. Logical multi-tenancy is **role-based** and **chat-type based**:

1. **Role-Based Isolation**:
   - **Developer** — platform operator (`DEVELOPER_ID` / `settings.DEVELOPER_IDS`)
   - **Owner** — reseller with scoped groups/channels (`owners`, `owner_scope_service`)
   - **Sudo** — delegated admin with permission matrix (`sudo_permissions`, migration `0014`)
   - **Group admins** — per-chat settings and playback control

2. **Chat-Type Identity (`chat_id`, `chat_type`)**:
   - Telegram allows a group and a channel to share the exact same numeric ID (e.g., `-100123456789`).
   - To isolate state, **`GroupCredit`** and **`ChatSettings`** models strictly use `(chat_id, chat_type)` as a composite unique identity.
   - Relationships to the `Group` and `Channel` models use `viewonly=True` with `foreign()` annotations rather than strict foreign keys, enabling robust cross-type data association without referential conflicts.

Install lineage, credit, and panels respect owner scope and chat-type identity in code; there is no shared database serving multiple unrelated bot tokens today.

---

## Bot-Maker / multi-bot database audit (not implemented)

**Verdict if building a Bot-Maker:** The schema is **not** multi-bot ready.

Zero tables have a `bot_id` column. No `bots` registry table exists. All operational tables assume a single bot instance. A true multi-tenant (many bots, one DB) upgrade would require a migration across every operational table.

---

## 1. Missing Core Table: `bots`

**Status: DOES NOT EXIST**

A Bot-Maker system requires a central registry:

```sql
CREATE TABLE bots (
    id          SERIAL PRIMARY KEY,
    bot_token   TEXT NOT NULL UNIQUE,
    api_id      BIGINT NOT NULL,
    api_hash    TEXT NOT NULL,
    developer_id BIGINT NOT NULL,
    plan_type   TEXT NOT NULL DEFAULT 'free',  -- free, paid, premium
    display_name TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Impact**: Without this table, there is no way to register multiple bots or scope any data to a specific bot instance.

---

## 2. Data Isolation: `bot_id` Foreign Key Missing from ALL Tables

**Status: 0 of 34 tables have `bot_id`**

Every single operational table is globally scoped. If two bots share the same database:
- A user's favorites from Bot A would appear in Bot B
- Group settings from Bot A would apply to Bot B
- Credit charged via Bot A's sudo would affect Bot B's groups

### Tables Requiring `bot_id` (categorized by urgency)

#### Tier 1 — CRITICAL (core business isolation)

| Table | Current Unique Key | Required Change |
|-------|-------------------|-----------------|
| `groups` | `UNIQUE(chat_id)` | `UNIQUE(bot_id, chat_id)` |
| `channels` | `UNIQUE(chat_id)` | `UNIQUE(bot_id, chat_id)` |
| `users` | `UNIQUE(user_id)` | `UNIQUE(bot_id, user_id)` |
| `group_credits` | `UNIQUE(chat_id, chat_type)` | `UNIQUE(bot_id, chat_id, chat_type)` |
| `chat_settings` | `UNIQUE(chat_id, chat_type)` | `UNIQUE(bot_id, chat_id, chat_type)` |
| `bot_settings` | `PK(key)` | `PK(bot_id, key)` |
| `install_policy_settings` | `PK(id=1)` | `PK(bot_id)` |
| `owners` | `UNIQUE(user_id)` | `UNIQUE(bot_id, user_id)` |
| `sudos` | `UNIQUE(user_id)` | `UNIQUE(bot_id, user_id)` |

#### Tier 2 — HIGH (operational isolation)

| Table | Required Change |
|-------|-----------------|
| `credit_history` | Add `bot_id`, index on `(bot_id, chat_id)` |
| `install_logs` | Add `bot_id` |
| `invoices` | Add `bot_id` |
| `invoice_items` | Inherits via `invoice_id` FK — OK if invoices gets `bot_id` |
| `blacklist` | Add `bot_id` — each bot has its own blacklist |
| `filter_words` | Add `bot_id` — each bot has its own filters |
| `force_join_channels` | Add `bot_id` |
| `broadcasts` | Add `bot_id` |
| `favorites` | Add `bot_id` — user favorites are per-bot |
| `playlists` | Add `bot_id` |
| `playback_states` | `PK(chat_id)` → `PK(bot_id, chat_id)` |

#### Tier 3 — MEDIUM (admin/role isolation)

| Table | Required Change |
|-------|-----------------|
| `music_admins` | `UNIQUE(bot_id, chat_id, user_id)` |
| `video_admins` | `UNIQUE(bot_id, chat_id, user_id)` |
| `player_owners` | `UNIQUE(bot_id, chat_id, user_id)` |
| `player_vips` | `UNIQUE(bot_id, chat_id, user_id)` |
| `sudo_wallets` | `PK(bot_id, sudo_user_id)` |
| `sudo_wallet_transactions` | Add `bot_id` |
| `sudo_bulk_invoices` | Add `bot_id` |
| `owner_sales` | Add `bot_id` |

#### Tier 4 — LOW (infrastructure, can be shared or scoped)

| Table | Notes |
|-------|-------|
| `helper_accounts` | Could be shared across bots OR scoped per bot. Decision needed. |
| `helper_chat_bindings` | If helpers are shared: add `bot_id`. If per-bot: inherits from `helper_accounts.bot_id`. |
| `helper_events` | Add `bot_id` for audit scoping |
| `analytics_hourly` | Add `bot_id` to scope metrics per bot |
| `call_reports` | Add `bot_id` |
| `free_install_whitelist` | Add `bot_id` |

---

## 3. Sudo/Owner Scoping

**Status: NOT SCOPED TO BOT**

Currently:
- `owners.user_id` is globally unique — a user can only be owner of ONE bot
- `sudos.user_id` is globally unique — same issue

In a multi-tenant system:
- User 123456 might be Owner of Bot A and Sudo of Bot B
- The unique constraint on `user_id` alone would prevent this

**Required**: Change `UNIQUE(user_id)` → `UNIQUE(bot_id, user_id)` on both `owners` and `sudos`.

---

## 4. Unique Constraint Conflicts

**Status: 18 tables have constraints that will break under multi-tenancy**

| Constraint | Current | Required |
|------------|---------|----------|
| `groups.chat_id` | `UNIQUE` | `UNIQUE(bot_id, chat_id)` |
| `channels.chat_id` | `UNIQUE` | `UNIQUE(bot_id, chat_id)` |
| `users.user_id` | `UNIQUE` | `UNIQUE(bot_id, user_id)` |
| `helper_accounts.phone` | `UNIQUE` | May remain global if helpers are shared |
| `helper_accounts.tg_user_id` | `UNIQUE` | Same decision as above |
| `music_admins(chat_id, user_id)` | `UNIQUE` | `UNIQUE(bot_id, chat_id, user_id)` |
| `video_admins(chat_id, user_id)` | `UNIQUE` | Same |
| `player_owners(chat_id, user_id)` | `UNIQUE` | Same |
| `player_vips(chat_id, user_id)` | `UNIQUE` | Same |
| `playback_states.chat_id` | `PK` | `PK(bot_id, chat_id)` |
| `helper_chat_bindings.chat_id` | `PK` | `PK(bot_id, chat_id)` |
| `bot_settings.key` | `PK` | `PK(bot_id, key)` |
| `install_policy_settings.id` | `PK(1)` | `PK(bot_id)` |

---

## 5. Migration Complexity Estimate

| Aspect | Estimate |
|--------|----------|
| Tables needing `bot_id` column | **31 of 34** |
| Unique constraints to alter | **13** |
| Primary keys to alter | **4** (bot_settings, install_policy, playback_states, helper_chat_bindings) |
| Foreign keys to add | **31** (all → `bots.id`) |
| Application code changes | Every query needs `WHERE bot_id = :current_bot` filter |
| Service layer changes | `settings.BOT_ID` or context-injected bot_id in every service method |

**Estimated effort**: 3–5 days for an experienced engineer, assuming automated migration generation.

---

## 6. Recommendation

**Do NOT add `bot_id` to the current codebase now.**

Rationale:
1. Adding `bot_id` to 31 tables without the runtime context-injection system would break the entire application.
2. The current system works perfectly for single-bot deployment.
3. The multi-tenant upgrade should be a dedicated project phase with:
   - A `bots` registry table
   - A middleware/context system that injects `bot_id` into every DB query
   - A migration script that adds `bot_id` columns, backfills them with a default bot ID, and alters constraints
   - Full regression testing

**What to do NOW to minimize future pain**:
- Keep the schema clean (no cross-table denormalization)
- Keep all queries through the repository/service layer (never raw SQL in handlers) — this is already the case ✅
- Keep unique constraints explicit (not implicit) — already the case ✅
- Document the multi-tenancy plan (this report)

The current architecture is **well-positioned** for the upgrade because:
- All DB access goes through `async_session` + repository pattern
- No raw SQL in handlers
- Clean model separation
- The `bot_id` column can be added via a single Alembic migration + a grep-and-replace of all repository queries
