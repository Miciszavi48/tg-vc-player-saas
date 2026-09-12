# Testing

> **Canonical References:**
> - [TESTING_AND_SAFETY.md](TESTING_AND_SAFETY.md)
> - [DATABASE_AND_SCHEMA.md](DATABASE_AND_SCHEMA.md)
How to run the automated test suite safely without risking production data.

> [!TIP]
> See [TESTING_AND_SAFETY.md](TESTING_AND_SAFETY.md) for deeper details on isolation rules, `NullPool`, and test modes.

## Environment & `TEST_MODE`

By default, when running `pytest`, the codebase engages `TEST_MODE=1` which automatically:
1.  **Uses SQLite (`aiosqlite`)** instead of PostgreSQL.
2.  **Uses `fakeredis`** to mock the Redis singleton.
3.  **Bypasses `config.env`** to prevent local developer credentials from leaking into test state.

You do **NOT** need to spin up a PostgreSQL instance or run Alembic migrations to execute standard unit tests. The test runner bootstraps its own schema using `Base.metadata.create_all()`.

## Run tests

```bash
# Full suite (Safe SQLite + FakeRedis mode)
pytest tests/ -v

# Lint (application code)
ruff check app/
```

## External Database / Integration Testing

If you specifically need to test PostgreSQL syntax (e.g., `pg_try_advisory_lock` or specific `asyncpg` race conditions):

1.  Spin up a disposable PostgreSQL database container.
2.  Run Alembic migrations:
    ```bash
    DATABASE_URL="postgresql+asyncpg://user:pass@localhost/disposable_db" alembic upgrade head
    ```
3.  Run the tests targeting the external DB:
    ```bash
    DATABASE_URL="postgresql+asyncpg://user:pass@localhost/disposable_db" ALLOW_EXTERNAL_TEST_DB=1 pytest tests/ -v
    ```

> [!CAUTION]
> The test runner will aggressively refuse to run if `DATABASE_URL` contains `prod`, `production`, or `musicbot_dev`.

## Async / Redis test patterns

- All database connections in tests utilize `NullPool` to prevent async event-loop binding bugs.
- The Redis singleton is reset before every test automatically via the `_inject_cache_singleton` fixture in `conftest.py`.
- When monkey-patching DB sessions, use `sys.modules["app.database.engine"]` because `app.database.__init__` re-exports an `engine` object that shadows the `engine` submodule.

## Related docs

- [TESTING_AND_SAFETY.md](TESTING_AND_SAFETY.md) — complete safety guardrails.
- [operations/schema_drift.md](operations/schema_drift.md) — drift checker before deploy
- [deployment/staging_validation.md](deployment/staging_validation.md) — manual live Telegram matrix (not automated in CI)
