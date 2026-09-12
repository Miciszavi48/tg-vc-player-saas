# Credit & Charge Commands

> **Canonical References:**
> - [CREDIT_SOURCE_OF_TRUTH.md](../CREDIT_SOURCE_OF_TRUTH.md)
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md) | Related: [../operations/credit_reconciliation.md](../operations/credit_reconciliation.md) | [../operations/daily_deduct_batching.md](../operations/daily_deduct_batching.md)

## Credit Model

- **Installed/managed source of truth:** `groups.status = 'active'` for groups
  and `channels.status = 'active'` for channels.
- **Per-chat credit balance:** `group_credits.credit_days` scoped by
  `(chat_id, chat_type)`. Credit is usable only when the matching install row is
  active.
- Credit depletes by 1 per day via scheduled daily deduct.
- History tracked in `credit_history` (partitioned table, migration `0018` reconciled orphan).

## Credit Charge Text Commands (`app/handlers/credit_commands.py`)

Registered at `PRIORITY_COMMAND_GROUP = -15` (high priority, overrides FSM listeners).

| FA | EN | Example | Notes |
|----|----|----|-------|
| `آپدیت شارژ N` | `update charge N` | `آپدیت شارژ ۳۰` | Add N music credit days |
| `آپدیت شارژ ویدیو N` | `update charge video N` | `update charge video 15` | Add N video credit days |

**Persian digit support:** `آپدیت شارژ ۳۰` works (digits normalized via `normalize_digits()`).

**Access:** Sudo+ with `can_manage_credit` permission. Group only.

**Managed-state rule:** `آپدیت شارژ N` refuses inactive/unmanaged groups. It does
not create orphan credit and does not reactivate a group. Reinstall/reactivation
is explicit through the install command.

**Filter:** `_charge_filter()` uses anchored regex on NFKC-normalized text.

## Daily Deduct (Phase 2D)

- Scheduled: `midnight_credit_deduct` in `app/scheduler.py`.
- Idempotent per chat per day: `group_credits.last_daily_deducted_on` (migration `0019`).
- DB idempotency prevents double-deduct even if scheduler fires twice.
- Batching: disabled by default (`DAILY_DEDUCT_BATCHING_ENABLED=false`); staging-only batched path exists.

### Credit Warnings

- Scheduler sends low-credit warning via `notify_credit_warning` in `notification_service.py`.
- Warning selection joins the active install table, so inactive/uninstalled
  groups with leftover credit rows are skipped.
- Redis dedup: `credit:warn:{chat_id}:{remaining_days}:{date}` (48h TTL) prevents spam.
- Warnings sent to: group, log channel, sudo DM.
- Fail-closed when Redis unavailable (no warning rather than spam).

## Call Security Gate

Enabling Call Security (`call_security_settings.enabled = True`) requires active group credit:
`call_security_service.has_active_credit(chat_id)`.

Playback, download, playlist, and Call Security runtime checks use the same
managed runtime credit state: active install row plus positive or unlimited
credit.

## Sudo Billing Bypass

`compute_install_cost` returns 0 for `sudo`, `developer`, and `owner` roles.
No wallet deduction for privileged installs.

## Reconciliation

Orphan `credit_history_partitioned` rows: migration `0018_credit_hist_orphan` + runbook at
[../operations/credit_reconciliation.md](../operations/credit_reconciliation.md).

## Related Docs

- [../operations/daily_deduct_batching.md](../operations/daily_deduct_batching.md) — batching performance plan
- [../operations/daily_deduct_idempotency.md](../operations/daily_deduct_idempotency.md) — idempotency gate
- [../operations/credit_reconciliation.md](../operations/credit_reconciliation.md) — reconciliation runbook
- [../operations/schema_drift.md](../operations/schema_drift.md) — drift checker (checks credit table)
