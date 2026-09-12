# Credit & Daily Deduct

> **Redirect (2026-06-11):** This document has been superseded by the expanded [credit_and_charge.md](credit_and_charge.md). The content below is kept for backward compatibility.
>
> Back to [../index.md](../index.md)

---

## Model

- Per-chat `group_credits.credit_days` decremented by scheduler.
- History: `credit_history` (partitioned; see operations docs).

## Daily deduct (Phase 2D)

- Idempotent per chat per day: migration `0019_group_credit_daily_deduct_idempotency`
- Batching plan: [operations/daily_deduct_batching.md](../operations/daily_deduct_batching.md)
- Idempotency decision: [operations/daily_deduct_idempotency.md](../operations/daily_deduct_idempotency.md)
- Validator: `python scripts/validate_daily_deduct_batching.py`

## Credit warnings

Scheduler sends low-credit warnings with Redis dedup (`credit_warning_sent_key`). Tests: `tests/test_scheduler_credit_warning_queries.py`, `tests/test_notification_credit_warning_dedup.py`.

## Reconciliation

Orphan credit_history rows: migration `0018` + plan [operations/credit_reconciliation.md](../operations/credit_reconciliation.md).

## Call Security gate

Enabling Call Security `enabled=True` requires active group credit (`call_security_service.has_active_credit`).

## Alembic head

**`0033_instance_database_ownership`** (credit/deduct remains implemented by the earlier `0018`/`0019` revisions; the current head includes all prior migrations).
