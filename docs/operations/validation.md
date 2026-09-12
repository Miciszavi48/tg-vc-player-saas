# Safe Validation Commands

> Last verified against repository: 2026-07-19

> **Canonical References:**
> - [TESTING_AND_SAFETY.md](../TESTING_AND_SAFETY.md)
> - [DATABASE_AND_SCHEMA.md](../DATABASE_AND_SCHEMA.md)
> 
> Back to [../index.md](../index.md) | Related: [deployment.md](deployment.md) | [troubleshooting.md](troubleshooting.md)

The diagnostics in sections 1–7 and 9–11 are read-only or local-static checks:
they do not start the bot or make Telegram calls. `run_disposable_alembic_check.py`
and the pytest suite are explicitly separate: they may create/upgrade a disposable
database or local SQLite test file and must never target `musicbot_dev` or Redis
db 0.

---

## 1. i18n Structure Validation

```bash
# Check JSON validity, fragment existence, duplicate leaves, FA/EN parity
python scripts/split_i18n_resources.py --check

# Verbose output (shows all checks performed)
python scripts/split_i18n_resources.py --check --verbose
```

Expected: `All checks passed.` (exit 0)

What it checks:
- Valid JSON for `manifest.json` and every listed fragment
- All fragments exist for both `fa/` and `en/`
- No duplicate leaf paths across fragments in the same language
- FA/EN leaf-path parity (same keys in both languages)
- All leaves are strings (not nested objects)

---

## 2. i18n Usage Coverage Audit

```bash
# AST-scan app/**/*.py for t()/label() calls, validate against split fragments
python scripts/check_i18n_usage.py

# Show all found keys, variable call sites, stale candidates
python scripts/check_i18n_usage.py --verbose
```

Expected: `0 missing keys` (exit 0)

Current status: 856 literal `t()` keys, 19 dynamic families, 0 missing.

What it checks:
- Every literal `t(lang, "dot.key")` call resolves in FA and EN
- Every `label(lang, group, value)` pair resolves
- Dynamic f-string key families registered in `DYNAMIC_ALLOWLIST`
- Unregistered dynamic patterns → exit 1

---

## 3. DB Schema Drift Check

```bash
# Read-only SELECT checks against dev DB
python scripts/db_schema_drift_check.py

# Against a disposable DB (state-changing for that disposable database)
python scripts/run_disposable_alembic_check.py
```

Expected: Alembic head = `0033_instance_database_ownership`, all critical tables/columns/indexes present.

What it checks:
- Current Alembic revision vs expected head
- Presence of critical tables (e.g. `call_security_settings`, `group_member_memberships`)
- Critical columns (e.g. `last_daily_deducted_on`, `membership_age_days`)
- Index existence
- `credit_history` partition status

> **WARNING:** Refuses to run against non-test DB names unless `--allow-non-test-db` is passed. Safe by default.

---

## 4. Handler Map Audit

```bash
python scripts/audit_handler_map.py
```

Prints the statically discovered message handlers (87) and callback handlers
(512 in the 2026-07-19 scan) with module, function, group number, filter
pattern, and source line. Use `audit_handler_group_topology.py --check` for the
real in-memory startup total (610: 521 callback, 87 message, one raw, one
chat-member handler).

Use to verify:
- New handlers are registered at the expected priority group
- No duplicate callback patterns
- Fallback handler is last (group 1000)

---

## 5. Voice Stack Diagnostic

```bash
python scripts/check_voice_stack.py
```

Checks:
- PyTgCalls importable and version detected
- `tgcalls` native extension available
- `MediaStream` builder compatibility (v1/v2 API shim)
- Kurigram version

---

## 6. Logging Pipeline Check

```bash
python scripts/check_logging_pipeline.py
```

Checks:
- Loguru configured
- Stdlib InterceptHandler bridge in place
- APScheduler / Pyrogram log levels set
- No secrets in log format strings

---

## 7. Helper OTP Config Check

```bash
python scripts/check_helper_otp_config.py
```

Checks (no secrets printed, no Telegram calls):
- `HELPER_SESSION_KEY_CURRENT` present
- Local migration head `0033_instance_database_ownership`
- OTP config fields are set

---

## 8. Run Tests (default: SQLite + fakeredis)

```bash
# Full suite in isolated local mode; no PostgreSQL/Redis service required
TEST_MODE=1 pytest tests/ -v

# Specific areas
pytest tests/test_i18n_usage_coverage.py -v
pytest tests/test_i18n_usage_coverage.py -v
pytest tests/test_handler_command_routing.py -v
pytest tests/test_callback_routing_conflicts.py -v
pytest tests/test_playback_routing_audio_video.py -v
pytest tests/test_callback_routing_conflicts.py -v

For PostgreSQL integration, use a disposable database and set
`ALLOW_EXTERNAL_TEST_DB=1`; never use `musicbot_dev`. Tests default Redis to db
15 and refuse deployed db 0 unless explicitly overridden.
```

---

## 9. Help Callback Dispatch Check

```bash
# PowerShell
$env:TEST_MODE=1; python scripts/verify_help_callback_dispatch.py

# Bash
TEST_MODE=1 python scripts/verify_help_callback_dispatch.py
```

Expected: `FOUND group=-845 name=help_route_promote check=True` and exit 0.

---

## 10. Deploy Zip Dry Run

```bash
python scripts/build_deploy_zip.py --dry-run
```

Verifies packaging excludes secrets (`config.env`, `*.session`, backups), graphify artifacts, and venv paths. No zip written.

---

## 11. Linting

```bash
ruff check app/
```

---

## What NOT to Run

| Command | Reason |
|---------|--------|
| `python -m app.main` | Starts bot, connects Telegram |
| `bash setup_server.sh` | Installs system packages, modifies OS |
| `bash scripts/deploy.sh --fresh` | Full server setup |
| `alembic revision --autogenerate` | Creates new migration |
| Any `UPDATE` / `DELETE` on production DB | Data loss risk |
| `python scripts/validate_daily_deduct_batching.py` | Writes to `musicbot_disposable` only; do not run against production |
