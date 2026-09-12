# Testing and Safety

This document outlines the safety guardrails established in the Telegram Voice Chat Music & Player Bot for testing, development, and schema validation.

> [!CAUTION]
> Tests should **never** run against production databases or the production Redis instance. 

## Test Isolation Strategies

The `tests/conftest.py` orchestrates strict isolation to prevent integration tests from bleeding into live state.

### `TEST_MODE=1`
When running `pytest`, `TEST_MODE` is activated.
1.  **Database Isolation**: `DATABASE_URL` defaults to a local SQLite instance (`sqlite+aiosqlite:///./.pytest-test-mode.db`).
    *   The `NullPool` class is forcibly used to prevent async event-loop binding issues.
    *   `Base.metadata.create_all()` is executed instead of checking Alembic migration heads.
2.  **Redis Isolation**: The singleton `_redis` from `app.utils.cache` is mocked via the `fakeredis` library. 
    *   `decode_responses=True` is properly simulated.
3.  **Environment Isolation**: `MUSICBOT_DOTENV_OVERRIDE` is disabled, ensuring local `.env` variables do not overwrite explicit test secrets.

### External Testing Overrides

If you need to test against real infrastructure (e.g., to validate asyncpg race conditions or Redis script atomicity):
*   **PostgreSQL**: Provide a `DATABASE_URL` starting with `postgresql+asyncpg` AND set `ALLOW_EXTERNAL_TEST_DB=1`. 
    *   *Safety Guard*: The test suite will actively block connections to databases containing `prod`, `production`, or `musicbot_dev` in the connection string.
*   **Redis**: Set `ALLOW_EXTERNAL_REDIS=1`. Tests will attempt to connect to `redis://localhost:6379/15`.

## Safe Disposable Schema Validation

Validating Alembic migrations should be performed safely. The script `scripts/db_schema_drift_check.py` handles this.
*   Do not run `alembic upgrade head` against `musicbot_dev` or production just to see if models match.
*   Instead, developers should spin up a **disposable** PostgreSQL instance (Docker Compose in repo root is acceptable for **local dev only** — not production deploy), run `alembic upgrade head`, run `db_schema_drift_check.py` to identify missing migrations, and then destroy the container.
*   See `scripts/run_disposable_alembic_check.py` for automated wrapping.

## Operational Safety

*   **Secrets Logging**: The custom diagnostic logger (`app/utils/diagnostic_logging.py`) and standard formatters are instructed strictly *never* to print token payloads, API Hashes, session strings, or Pyrogram proxies.
*   **Alembic Head Verification**: The startup hook (`app/database/schema_readiness.py`) fails fast in production if the actual PostgreSQL schema (`alembic_version`) does not perfectly match the file-based migration head. 
