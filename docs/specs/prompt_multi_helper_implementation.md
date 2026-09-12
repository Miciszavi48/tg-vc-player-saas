# Implementation Prompt: Multi-Helper Account Management via CLI

> **Canonical References:**
> - [../DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> - [../REDIS_AND_CACHE.md](../REDIS_AND_CACHE.md)
> 
> **Historical Note:** This is an archived implementation prompt used during development.
> **Current status (2026-07-19): SUPERSEDED.** Helper-account management, OTP, pooling, and CLI behavior are implemented in current source; use [../features/helper_bot.md](../features/helper_bot.md) and `python -m app.tools.helper_pool_cli --help`. The old `docs/spec_multi_helper_cli.md` path and instructions below are preserved only as historical prompt content.
>
> ## Context

You are a senior backend engineer working in the repository `tg-vc-player-saas` (Python 3.12, kurigram Pyrogram fork, PyTgCalls, PostgreSQL 16, Redis 7). The specification for Multi-Helper CLI is at `docs/spec_multi_helper_cli.md`. Your task is to implement it.

---

## Step 0 — Repository Audit (MANDATORY FIRST STEP)

Before writing ANY code, scan the repository and produce `FEATURE_VERIFICATION_REPORT.md` with a table:

| Component | Status | Path(s) | Line(s) | Evidence |
|-----------|--------|---------|---------|----------|

For each item below, check the ACTUAL file contents. If you cannot find a file, write `NOT FOUND` — do NOT guess.

**Required audit targets:**

1. `HelperAccount` model — list every column name and type from `app/database/models.py`
2. `HelperChatBinding` model — list every column name and type
3. `HelperEvent` model — does it exist? If not, mark MISSING
4. `HelperPoolService` — list every method signature from `app/services/helper_pool_service.py`
5. `helper_pool_cli.py` — list every command name from the `_DISPATCH` dict at `app/tools/helper_pool_cli.py`
6. Fernet encryption — verify `encrypt_session` and `decrypt_session` exist, confirm key names from `app/config/settings.py`
7. `helper_event_repo.py` — does `app/repositories/helper_event_repo.py` exist?
8. Watchdog — verify `helper_health_watchdog` in `app/scheduler.py` and `app/services/watchdog.py`
9. Recovery — verify `schedule_recovery` in `app/services/recovery_service.py`
10. `mask_phone` — verify exists in `app/utils/helpers.py`
11. `NotificationService.notify_error` — verify exists in `app/services/notification_service.py`
12. Existing migrations — list all files in `app/database/migrations/versions/`, extract each `revision` and `down_revision` value
13. Redis lock functions — verify `acquire_lock` and `release_lock` in `app/utils/cache.py`

**Status values:** `PRESENT` (exists and matches spec), `PARTIAL` (exists but missing fields/methods per spec), `MISSING` (does not exist)

---

## Step 1 — Alembic Migration

Create `app/database/migrations/versions/0006_helper_extensions.py`.

**Requirements:**
- `down_revision` must equal the `revision` value from the latest existing migration (verify in Step 0 audit)
- Add to `helper_accounts`: `tg_user_id` (BigInteger, nullable, unique), `display_name` (String 255, nullable), `username` (String 128, nullable), `quarantine_count` (Integer, not null, default 0)
- Add to `helper_chat_bindings`: `bound_by` (BigInteger, nullable), `binding_state` (String 32, not null, default "bound"), `last_error` (Text, nullable)
- Create `helper_events` table with columns from spec §3.3 + indexes on `event_type`, `helper_account_id`, `created_at`
- Include `downgrade()` that reverses all changes
- Run `alembic upgrade head` and verify it succeeds on the existing DB

---

## Step 2 — Update Models

In `app/database/models.py`:
- Add the 4 new columns to `HelperAccount` class
- Add the 3 new columns to `HelperChatBinding` class
- Add the `HelperEvent` class (§3.3 of the spec)
- Update imports if needed (`Date`, `SmallInteger`, `Index` may already be imported — verify)

**Acceptance criteria:** `ruff check app/database/models.py` passes, `python -c "from app.database.models import HelperEvent, HelperAccount, HelperChatBinding"` succeeds.

---

## Step 3 — Repository Layer

Create `app/repositories/helper_event_repo.py`:
- `async def log_event(event_type, actor, helper_account_id, chat_id, metadata)` — append-only insert
- `async def get_events(helper_id=None, event_type=None, since=None, limit=100)` — query with optional filters
- Follow the session pattern from existing repos (e.g., `app/repositories/force_join_repo.py`)

---

## Step 4 — Service Layer Extensions

In `app/services/helper_pool_service.py`, add these methods (verify each does NOT already exist before adding):

```python
async def join_chat_as_helper(helper_id: int, chat_identifier: str) -> dict
async def leave_chat_as_helper(helper_id: int, chat_id: int) -> bool
async def ensure_helper_joined(helper_id: int, chat_id: int) -> bool
```

Each method must:
- Acquire the appropriate Redis lock from spec §4
- Check idempotency key where specified
- Create a temporary `pyrogram.Client` from the decrypted session
- Call the Telegram API (`join_chat`, `leave_chat`, `get_chat_member`)
- Handle `FloodWait` by storing `helper:floodwait:{id}` with dynamic TTL
- **Never log** session strings or invite links
- Emit audit event via `helper_event_repo.log_event`
- Return structured result (not print to stdout)

Also add `quarantine_count` increment to the existing `quarantine_helper` method.

---

## Step 5 — CLI Extensions

Extend `app/tools/helper_pool_cli.py` with the 13 new commands from spec §5.2.

**Hard rules:**
- Each new command gets: argparse subparser, async handler function, entry in `_DISPATCH`
- Every command that modifies state must call `helper_event_repo.log_event`
- Every command that accesses Telegram must acquire the spec-defined Redis lock and respect idempotency keys
- `export-session` must require `--confirm-danger` and must NOT log the decrypted session
- `add-batch` must validate the JSON format from spec §5.2 and skip duplicates
- `delete` must refuse if active bindings exist (unless `--force`)
- Exit codes must follow spec §5.3: 0=success, 1=error, 2=FloodWait, 3=lock conflict

**Acceptance criteria:** `python -m app.tools.helper_pool_cli --help` shows all 21 commands. Each command can be invoked with `--help` without error.

---

## Step 6 — Tests

Create `tests/test_helper_cli.py`:

**Unit tests (must pass without DB/Redis):**
1. `test_cli_parser_accepts_all_commands` — build parser, parse each command with valid args
2. `test_encrypt_decrypt_roundtrip` — encrypt then decrypt returns original
3. `test_decrypt_old_key_fallback` — encrypt with old key, set new current key, decrypt still works
4. `test_mask_phone_formats` — verify masking for various phone formats
5. `test_add_batch_rejects_invalid_json` — invalid phone format raises validation error
6. `test_quarantine_increments_count` — mock DB, verify quarantine_count += 1

**Integration tests (require DB + Redis):**
7. `test_join_chat_creates_binding_and_event` — mock Pyrogram client, verify DB binding + helper_events row
8. `test_join_chat_floodwait_returns_code_2` — mock FloodWait, verify exit code
9. `test_bind_concurrent_only_one_wins` — two concurrent bind calls, only one creates binding (lock)
10. `test_health_check_quarantines_on_failure` — mock get_me failure, verify helper status=quarantined
11. `test_rebind_picks_least_loaded` — insert 2 helpers with different loads, verify correct one selected

**i18n parity test (if any bot UX is added):**
12. Verify every key added to `fa.json` also exists in `en.json`

**Acceptance criteria:** `pytest tests/test_helper_cli.py -v` — all pass. No new failures introduced in `pytest tests/` full suite.

---

## Step 7 — CHANGELOG and Documentation

Update `CHANGELOG.md`:
- Add version entry with: files changed, features added, migration name
- Include "Known Pre-existing Test Failures" section listing the 2 existing failures:
  - `tests/integration/test_permissions_matrix.py::test_developer_id_from_config`
  - `tests/test_cache.py::test_distributed_lock_acquire_release`
  - With root cause note for each

---

## Step 8 — Privacy and Safety Verification

Before declaring done, verify these invariants:

1. **No session strings in logs**: grep the entire `app/` directory for any `logger.*session` that might leak session data — confirm zero matches or that all matches are DEBUG-level with no secret values
2. **No invite links in logs**: grep for `logger.*invite` — confirm safe
3. **track_event (if used) contains no PII**: verify that any `track_event` calls pass only feature name (string), event name (string), and numeric value — never `user_id`, `chat_id`, `broadcast_id`, `file_id`, or message text
4. **mask_phone used everywhere**: grep for `h.phone` or `helper.phone` in CLI output code — confirm all go through `mask_phone()`

---

## Output Checklist

After implementation, produce this exact output structure:

```
1) Files changed:
   - [list every file with one-line reason]

2) FEATURE_VERIFICATION_REPORT.md summary:
   - [PRESENT/PARTIAL/MISSING per audit item with file:line evidence]

3) Patch plan:
   - [exact function names created/modified per file]

4) Test results:
   - ruff check app/ → [result]
   - alembic upgrade head → [result]
   - pytest tests/test_helper_cli.py -v → [result]
   - pytest tests/ -q → [total passed / failed]

5) Privacy verification:
   - [result of each Step 8 check]

6) Remaining gaps:
   - [list anything not implemented, with reason]
```

---

## Rules (non-negotiable)

- If you cannot find a file referenced in the spec, do NOT fabricate its contents — write `FILE NOT FOUND` in the audit
- No hardcoded user-facing strings — all from i18n JSON via `t(lang, key)`
- callback_data are stable ASCII constants from the `CB` dict
- Every inline keyboard must have Back + Home buttons (if adding any bot UX)
- Private-only bot flows must show i18n warning + URL button to open PM + Back button
- Multi-instance safety via Redis `acquire_lock` / `release_lock` from `app/utils/cache.py`
- All Redis clients use `decode_responses=True`
