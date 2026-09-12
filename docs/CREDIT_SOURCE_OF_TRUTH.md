# Credit Source of Truth

This document defines the canonical flow for subscription credits and clarifies how derived metrics and cache heuristics are processed. 

> [!CAUTION]
> Recent bugs involved conflicting credit sources. Always treat `GroupCredit` as the absolute source of truth. Redis sets, derived fields, or old expiration displays should **never** be used to execute destructive actions (such as removing the bot from a group or disabling features).

## Source of Truth Mapping

| State | Canonical Source (`GroupCredit`) | Derived/Cache (Redis) | Who Writes It | Who Reads It | Invalidation |
|---|---|---|---|---|---|
| **Trial** | `is_trial=True`, `trial_started_at`, `trial_expire_at` | `credit:{type}:{id}` | `install.py`, `credit_service.py` | UI Panels, Scheduler | Transitions clear trial fields, set `is_trial=False` |
| **Paid/Manual** | `credit_days > 0`, `is_trial=False` | `credit:{type}:{id}` | `credit_service.py`, `dev_panel.py` | UI Panels, Scheduler | Daily deduction, Manual add/deduct |
| **Unlimited** | `status="unlimited"`, `credit_days = MAX_CREDIT_DAYS` (36500), `is_trial=False` | `credit:{type}:{id}` | Dev/Sudo via `credit_service` (`mode="unlimited"`) | Validation hooks, `media_capability_service` | — |
| **Expired** | `credit_days <= 0`, `is_trial=False`, `expire_at` < Now | `credit:expired_pending_leave` | `credit_service` (via cron) | Scheduler (Auto-leave) | Cleared immediately on recharge |

## State Transitions

### Trial to Paid
When a chat receives its first manual credit charge:
1. `credit_days` is incremented.
2. `is_trial` is explicitly set to `False`.
3. Legacy/display metadata (`trial_started_at`, `trial_expire_at`) is **nullified** to prevent UI confusion.
4. Total charged counters begin tracking.

### Paid to Unlimited
If a developer/sudo assigns unlimited credit (`mode="unlimited"`):
1. `status` is set to `"unlimited"` and `credit_days` is set to `MAX_CREDIT_DAYS` (36500) — not `-1`.
2. The UI must recognize `status="unlimited"` and stop calculating/displaying expiration warnings.
3. Daily deductions must skip this chat (`credit_service` checks `status`).

### Expiration and Auto-Leave
When `credit_days` drops to 0 during the daily deduction cron:
1. The chat's `(chat_id, chat_type)` is pushed to the Redis set `credit:expired_pending_leave`.
2. **This set is a hint.** The auto-leave background task polls this set, but **must** re-query `GroupCredit` to verify `credit_days <= 0` right before attempting to leave the chat.
3. If the chat has been recharged in the interim, the task simply discards the Redis hint.

## The `CreditHistory` Log
Every modification to `GroupCredit` (add, deduct, trial, daily_deduct) must insert a corresponding row into `CreditHistory`.
*   **Verification Risk**: `CreditHistory` is an audit log, not a derived view. It should never be "casually repaired" or recalculated to match `GroupCredit`. Any discrepancy between `CreditHistory` sums and `GroupCredit` balances indicates a logical fault that requires developer investigation, not automated fixing.

## Reporting and Orphan Rows
Reports querying for "Chats with no credit" or "Chats due for renewal" must join against active `Group` or `Channel` records. Relying purely on `GroupCredit` rows with `credit_days=0` may return orphan rows for chats where the bot was manually kicked. 

**Rule**: No-credit/renewal reports require an `active` install state.
