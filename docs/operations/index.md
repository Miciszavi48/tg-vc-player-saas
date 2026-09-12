# Operations Index

> Last verified against repository: 2026-07-19

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [TESTING_AND_SAFETY.md](../TESTING_AND_SAFETY.md)
> 
> Back to [../index.md](../index.md).

## Documents

| Document | Purpose |
|----------|---------|
| [deployment.md](deployment.md) | How to deploy (native/systemd, SFTP; Docker dev/test only) |
| [validation.md](validation.md) | Safe offline validation commands |
| [**troubleshooting.md**](troubleshooting.md) | **(Canonical)** Common issues, voice stack, proxies, helper FloodWait, and fixes |
| [schema_drift.md](schema_drift.md) | DB drift checker usage and interpretation |
| [logging_safety.md](logging_safety.md) | Logging pipeline and safety guards |
| [credit_reconciliation.md](credit_reconciliation.md) | Credit history reconciliation |
| [daily_deduct_batching.md](daily_deduct_batching.md) | Daily deduct performance |
| [daily_deduct_idempotency.md](daily_deduct_idempotency.md) | Idempotency gate |
| [helper_selection_concurrency.md](helper_selection_concurrency.md) | Atomic helper reservation |
| [ops_concurrency.md](ops_concurrency.md) | Concurrency notes |

## Current Alembic Head

**`0039_hot_seat`**

Run migrations before starting the bot:
```bash
cd app
DATABASE_URL=postgresql+asyncpg://... alembic upgrade head
```

## Safe Validation Quick Reference

```bash
python scripts/split_i18n_resources.py --check
python scripts/check_i18n_usage.py
python scripts/db_schema_drift_check.py
python scripts/audit_handler_map.py
python scripts/check_voice_stack.py
python scripts/check_logging_pipeline.py
```

None of these require a running bot, DB writes, or live Telegram connection.
