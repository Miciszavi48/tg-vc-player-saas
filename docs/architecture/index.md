# Architecture Index

> Last verified against repository: 2026-07-19

Structural documentation for the Telegram Voice Chat Music & Video Player Bot.

> For feature behavior, see [../features/index.md](../features/index.md).  
> For deployment, see [../operations/deployment.md](../operations/deployment.md).  
> Back to [../index.md](../index.md).

## Documents

| Document | What it covers |
|----------|---------------|
| [**system-design.md**](system-design.md) | **(Canonical)** Comprehensive bot architecture, startup sequence, session management, PyTgCalls stack, handler priority routing |
| [system_architecture.md](system_architecture.md) | Structural layers, data flow, media pipeline, Redis/DB, scheduler (Reference) |
| [i18n_architecture.md](i18n_architecture.md) | Split-only loader, fragment layout, manifest, TextService API |
| [handler_routing.md](handler_routing.md) | Handler registration order, priority groups, callback routing model |
| [playback_architecture.md](playback_architecture.md) | Playback pipeline, voice stack, audio vs video routing |
| [multi_tenancy.md](multi_tenancy.md) | Role hierarchy (developer → owner → sudo → group admin → VIP), install lineage |
| [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md) | Maps PostgreSQL tables, ORM models, and Alembic state |
| [../REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md) | Maps Redis keys, rate limits, distributed locks, and TTLs |
| [../CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md) | Explains canonical group credit state vs heuristic hints |
| [../TESTING_AND_SAFETY.md](../TESTING_AND_SAFETY.md) | Explains SQLite/fakeredis test isolation and safety limits |

## Key architectural invariants

- **Layered:** Handlers → Services → Repositories → DB. No handler talks directly to DB.
- **i18n split-only:** No monolith `fa.json`/`en.json`. Split fragments under `app/resources/i18n/`.
- **Redis required:** FSM state, locks, caches all use Redis. `decode_responses=True` everywhere.
- **Schema gate:** PostgreSQL startup requires Alembic head; `create_all()` only for SQLite/test mode.
- **Helper bot required for voice:** PyTgCalls streams media via helper Telegram user accounts.
