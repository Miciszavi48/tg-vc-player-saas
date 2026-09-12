# Helper Bot (Helper Accounts)

> **Canonical References:**
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> Back to [../index.md](../index.md) | Related: [../architecture/playback_architecture.md](../architecture/playback_architecture.md) | [../operations/helper_selection_concurrency.md](../operations/helper_selection_concurrency.md)

## What Are Helper Accounts?

Helper accounts are regular Telegram user accounts (not bots) that:
1. Join voice chats in groups/channels.
2. Stream audio/video via PyTgCalls.
3. Handle raw MTProto calls for Call Security and slash-free group call text commands.

The bot itself cannot join voice chats; helpers are mandatory for playback.

## Registration Methods

| Method | Access | Description |
|--------|--------|-------------|
| Developer panel → مدیریت هلپرها → OTP wizard | Developer | Interactive phone+code+(2FA) wizard |
| Developer panel → Import String Session | Developer | Paste existing Pyrogram string session |
| CLI `python -m app.tools.helper_pool_cli` | Operator | Command-line pool management |
| `scripts/create_helper_app_credential.py` | Operator | Generate API credentials |

## OTP Wizard (No-Silent Design)

Handler: `app/handlers/helper_otp_wizard.py`

**Flow:**
1. Developer taps **افزودن هلپر (OTP)**.
2. Bot asks for phone number → OTP text/contact input at priority `-90`.
3. User enters phone → bot calls `send_code` via pre-auth registry.
4. Bot asks for received code.
5. Optional 2FA password step (if Telegram requires it).
6. Session saved encrypted (Fernet) to DB.

**No-silent guarantees:**
- Pre-auth registry (`helper_otp_pre_auth_registry.py`) stores the pending session in memory — no fragile mid-login session export.
- `stop_listening()` called on wizard entry to clear stale pyromod listeners.
- `/start` during `awaiting_phone` cancels OTP with `otp_cancelled_by_command`.
- Phone mismatch on session import → visible rejection (no silent success on wrong account).
- On abort/timeout: visible Persian message via `AskResult` / `notify_ask_abort`.

## Group Join Logic

Helpers do **not** auto-join when the bot is installed in a group. They join/bind through runtime paths:
- `CallService._ensure_helper_in_chat()` is called on first playback in a chat.
- `/addhelper` command (`app/handlers/add_helper.py`) triggers `ensure_helper_present_for_group()`.
- Text aliases: `افزودن هلپر` / `/addhelper`
- Returns: success / already_present / unavailable / failed / invite_failed / bot_unavailable / channel_invalid / promote_failed (all visible to user).

Successful group binding is stored in `helper_chat_bindings` (`chat_id` -> `helper_account_id`). Existing bindings are reused first; fallback helpers are selected only when the bound helper is unavailable/quarantined and an eligible pool helper exists.

### Invite-link join sequence (Pyrogram/kurigram)

Helper user accounts cannot reliably `join_chat(chat_id)` on unseen supergroups/channels (`CHANNEL_INVALID`). The runtime path is:

1. Main bot client (`get_runtime_bot()` or handler-passed `bot_client`) calls `export_chat_invite_link(chat_id)` via `resolve_group_invite_link()` (DB cache optional).
2. Invite URL is logged at INFO (`helper join invite prepared ...`).
3. **Mandatory settle delay** — `asyncio.sleep(HELPER_JOIN_INVITE_SETTLE_SECONDS)` (default **1** second).
4. Helper user client calls `join_chat(invite_link)` (never raw `chat_id`).
5. Membership is verified with `get_chat_member(chat_id, "me")`.

On `InviteHashExpired` / `InviteHashInvalid`, the bot exports a fresh link once and retries. `UserAlreadyParticipant` is treated as success. Infra failures (`invite_link_failed`, `bot_unavailable`, `channel_invalid`) do **not** quarantine the helper.

### Admin promotion after join

After a successful join (or when the helper is already a member), the **main bot** promotes the helper user account via `app/services/helper_admin_service.py`:

1. Resolve helper `tg_user_id` (DB or live `get_me()`).
2. Skip `join_chat` when membership already exists.
3. If call-management admin rights are missing, `promote_chat_member` with `can_manage_video_chats=True` and `can_manage_chat=True`.
4. `/addhelper` and manager text aliases surface `promote_failed` when the bot lacks `can_promote_members`.
5. Playback auto-join logs promote failures but does **not** block streaming.

Join failures are reported through `HelperJoinResult` from
`HelperPoolService.ensure_helper_joined_detailed()`. The existing
`ensure_helper_joined()` method remains a boolean compatibility wrapper, but
runtime paths use the detailed result to log safe exception type, redacted
message, attempt number, target chat, and remaining helper availability before
quarantining a failed helper (when quarantine applies).

## Helper Management Panel

Developer panel button **مدیریت هلپرها** uses stable callback data `hlp:home`.
It is private-chat and developer-only, but opening the panel is read-only and
does not require an available helper. The panel renders from stored helper rows
only and does not call join/session/group binding paths.

`hlp:home` shows total, active, available, disabled, and quarantined counts. It
still opens when no helper exists, all helpers are quarantined, or active
helpers are at capacity/cooldown. Recovery and add/manage buttons remain
available in those states.

## Pool & Concurrency

| Module | Role |
|--------|------|
| `app/services/helper_pool_service.py` | Selection, reservation, capacity |
| `app/repositories/helper_pool_repo.py` | DB queries |

**Atomic reservation** (Phase 2D-5B):
- `reserve_best_helper()` uses `FOR UPDATE SKIP LOCKED` — concurrent requests don't race.
- Call path reserves before Telegram/PyTgCalls joins, releases on setup failure.
- See [../operations/helper_selection_concurrency.md](../operations/helper_selection_concurrency.md).

## App Identity & Device Profiles

| Module | Role |
|--------|------|
| `app/services/helper_app_identity_service.py` | API credential management |
| `app/repositories/helper_app_credential_repo.py` | Stored API ID/hash/device |
| `app/repositories/helper_device_profile_repo.py` | Device profile templates |
| `scripts/import_helper_device_profiles.py` | Import device profile JSON |
| Migration `0023_helper_app_identity_profiles` | Schema for credential storage |

## Session Encryption and Uniqueness

- Algorithm: `cryptography.fernet.Fernet`
- Config keys: `HELPER_SESSION_KEY_CURRENT`, optional `HELPER_SESSION_KEY_OLD`
- `encrypt_session` / `decrypt_session` in `HelperPoolService`
- Key rotation: `helper_pool_cli rotate-key` re-encrypts all sessions

### `session_fingerprint`
The `HelperAccount` model utilizes a `session_fingerprint` column to detect identical sessions. To prevent conflicts when fingerprints are unknown (NULL), the database applies a **partial unique index** (`uq_helper_accounts_session_fingerprint`) using PostgreSQL syntax: `WHERE session_fingerprint IS NOT NULL`. This guarantees uniqueness for resolved sessions while safely allowing multiple pending or imported accounts.

## Call Security Integration

When Kurigram raw MTProto is available, helpers can:
- Mute new/unknown-age voice chat joiners via `phone.EditGroupCallParticipant`.
- Managed by `app/services/group_call_moderation_service.py`.
- Requires active call + eligible helper bound to the chat.
- Panel shows honest status: unsupported / no active call / no helper / permission denied.

## Slash-Free Group Call Commands

Raw group-call actions from commands such as `شروع کال`, `پایان کال`, `عنوان کال`, `لینک کال`, `بیصدا کال`, and `دعوت کال` use `resolve_group_call_helper()` before invoking `phone.*` functions. The resolver:

1. Prefers the helper bound to the same group.
2. Ensures the helper is joined and then verifies membership with `get_chat_member(chat_id, "me")`.
3. Verifies helper admin/call-management rights for create, discard, title, invite, mute/unmute, link export, and live setting toggles.
4. Starts the helper user client and uses that client for raw MTProto calls.
5. Returns visible no-helper/session-unavailable/join-failed/not-in-group/not-admin/API errors instead of reporting fake success.

The bot client is not used for helper-only raw phone operations, and scheduled call-end jobs re-run helper resolution at execution time instead of storing helper session secrets.

## Diagnostics

```bash
python scripts/check_helper_otp_config.py   # API/session-key/migration checks (read-only)
python -m app.tools.helper_pool_cli --help  # List, add, disable, rotate-key
journalctl -u musicbot --since "30 min ago" | grep -E "callback\\.|hlp:home|helper|quarantine|No available helper"
```

## Multi-Helper CLI Spec

[specs/spec_multi_helper_cli.md](../specs/spec_multi_helper_cli.md)

## Related Tests

| Test file | Coverage |
|-----------|---------|
| `tests/test_helper_otp_*` | OTP wizard flows |
| `tests/test_helper_otp_live_silence_regression.py` | No-silent regression |
| `tests/test_helper_atomic_reservation_phase2d5b.py` | Atomic reservation |
| `tests/test_helper_atomic_reservation_disposable_phase2d5c.py` | Disposable DB integration |
| `tests/test_helper_sensitive_data_hardening.py` | Session string / OTP / 2FA cleanup |
| `tests/test_helper_management.py` | Roundtrip encryption, old-key fallback |
| `tests/test_helper_group_binding_for_call_commands.py` | Same-group helper resolver and raw command client identity |
| `tests/test_helper_panel_hlp_home_routing.py` | `hlp:home` registration, early answer, no join/session dependency |
| `tests/test_helper_panel_no_available_helper.py` | Helper panel opens with no/quarantined/unavailable helpers |
| `tests/test_helper_join_failure_observability.py` | Safe helper join failure logs and quarantine metadata |
