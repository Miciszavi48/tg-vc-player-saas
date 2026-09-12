# Broadcast

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md)

Bulk messaging to groups, channels, and private users.

## Surfaces

- Legacy: `broadcast.py` (developer commands)
- Panel: `broadcast_panel.py`
- Wizard v2: `broadcast_wizard.py` + `broadcast_service_v2.py`

## Batching

Recipient batching for large sends: `tests/test_broadcast_recipient_batching.py`

## No-silent

Wizard ask flows use `safe_ask` / `AskResult`; abort paths must reply. Tests: `tests/test_broadcast_wizard_no_silent_phase_nosilent3.py`

## Spec

[specs/spec_forced_membership_broadcast_security.md](../specs/spec_forced_membership_broadcast_security.md) (broadcast security sections)
