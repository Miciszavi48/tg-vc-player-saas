# Force Join (Forced Membership)

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> Back to [../index.md](../index.md)

Requires users to join configured channels before using the bot.

## Configuration

- Developer panel → forced membership / `force_join_panel.py`
- Global toggle: `force_join_enabled` in `bot_settings`
- Redis: `fm:targets`, `fm:ok:{user_id}`, rate-limit keys (`redis_keys.py`)

## Runtime

- `force_join.py` checks membership on `/start` and gated actions.
- Sudo bypass when `can_use_sudo_admin_bypass` applies.

## Spec

[specs/spec_forced_membership_broadcast_security.md](../specs/spec_forced_membership_broadcast_security.md)

## Tests

- `tests/test_force_join_ask_abort_phase_nosilent2.py`
- Integration coverage in permissions matrix tests
