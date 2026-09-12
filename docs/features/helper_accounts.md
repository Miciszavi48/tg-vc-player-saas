# Helper Accounts

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> **Redirect (2026-06-11):** This document has been superseded by the expanded [helper_bot.md](helper_bot.md). The content below is kept for backward compatibility but [helper_bot.md](helper_bot.md) is the canonical reference.
>
> Back to [../index.md](../index.md)

---

Helper Telegram user accounts stream media into voice chats via PyTgCalls.

## Registration

- Developer panel → **مدیریت هلپرها** → OTP wizard (`helper_otp_wizard.py`).
- Group-level add flow (when enabled): `app/handlers/add_helper.py`
- CLI: `python -m app.tools.helper_pool_cli`
- Credential script: `scripts/create_helper_app_credential.py`

## OTP flow (no-silent)

- Phone → code → optional 2FA password.
- Pre-auth registry: `helper_otp_pre_auth_registry.py` (no fragile session export mid-login).
- On abort/timeout: visible Persian message via `AskResult` / `notify_ask_abort`.
- Tests: `tests/test_helper_otp_*`, `tests/test_pyromod_wizard_interception_hardening.py`

## App identity & device profiles

- Migrations `0023_helper_app_identity_profiles`.
- Repos: `helper_app_credential_repo.py`, `helper_device_profile_repo.py`
- Service: `helper_app_identity_service.py`
- Import script: `scripts/import_helper_device_profiles.py`
- Config check: `scripts/check_helper_otp_config.py`

## Pool & concurrency

- Selection/reservation: `helper_pool_service.py`
- Atomic reservation + concurrency: see [operations/helper_selection_concurrency.md](../operations/helper_selection_concurrency.md)
- Join failures now return `HelperJoinResult` internally so runtime quarantine
  logs include safe exception type, redacted message, attempt, target chat, and
  remaining availability. `ensure_helper_joined()` remains the boolean
  compatibility wrapper.

## Security

- Sessions encrypted with Fernet (`HELPER_SESSION_KEY_CURRENT`).
- Sensitive data hardening tests: `tests/test_helper_sensitive_data_hardening.py`

## Group binding

Helpers are not auto-joined at group install time. Current join/binding paths:

- First playback: `CallService._ensure_helper_in_chat()` reserves a helper, ensures it is joined, and writes `helper_chat_bindings`.
- Manual command: `/addhelper` / `افزودن هلپر` calls `ensure_helper_present_for_group()` and reuses the existing binding when present.
- Slash-free group call commands: `resolve_group_call_helper()` in `app/services/group_call_moderation_service.py` reuses the bound helper first, verifies same-group membership with the helper user client, verifies admin/call-management rights when required, and binds a successful fallback helper if the original binding is unavailable.

Persistent binding is stored in the existing `helper_chat_bindings` table (`chat_id` -> `helper_account_id`). Raw group-call functions use the helper user client selected by this binding/resolver, not the bot client. Missing helper/session/join/admin state returns a visible error instead of fake success.

Developer helper management (`hlp:home`) is read-only on open: it renders helper
counts and diagnostic notices from stored rows even when no helper is available
or every helper is quarantined. It does not attempt joins, session checks, or
group-specific helper binding just to open the panel.

Admin-required raw actions include starting, ending, title changes, mute/unmute, inviting, call link export, and live call setting toggles. The helper must be present in the same group and promoted to admin with call-management capability for those actions.

## Multi-helper CLI spec

[specs/spec_multi_helper_cli.md](../specs/spec_multi_helper_cli.md)
