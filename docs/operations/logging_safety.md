# Logging Safety Policy

> Last verified against repository: 2026-07-19

> **Canonical References:**
> - [TESTING_AND_SAFETY.md](../TESTING_AND_SAFETY.md)

Operational logs must help diagnose failures in `journalctl` without exposing secrets.

## Central logger

All runtime logging flows through **`app/utils/logger.py`**.

- Call **`setup_logger()` once** from `app/main.py` before handlers/services log.
- Do **not** add `logger.add()`, `logging.basicConfig()`, or file handlers elsewhere.

### Sinks (configured centrally)

| Sink | Destination |
|------|-------------|
| stderr | systemd journal → `journalctl -u musicbot-INSTANCE.service` |
| all levels | `LOG_FILE` (default `./logs/bot.log`) |
| ERROR+ only | derived error file (default `./logs/bot_error.log`) |

Log directory is created automatically when missing. Rotation: 10 MB, retention 7 days, gzip compression.

## Module usage

### Preferred (most modules)

```python
import logging

logger = logging.getLogger(__name__)
```

Stdlib records are bridged to Loguru via `InterceptHandler`.

### Allowed with caution (direct Loguru)

```python
from loguru import logger
```

Use only when the module already imports Loguru. **Use `{}` placeholders only** — never `%s` / `%d`.

Current direct-Loguru modules: `main.py`, `scheduler.py`, `helper_otp_wizard.py`, `helper_otp_pre_auth_registry.py`, `schema_readiness.py`, `i18n.py`, `media_sources.py`.

### Diagnostic helpers

`app/utils/diagnostic_logging.py` provides safe structured helpers and does **not** configure sinks:

| Helper | Purpose |
|--------|---------|
| `mask_phone()` | Phone numbers |
| `mask_secret()` | Generic secrets |
| `mask_token()` | Telegram bot tokens |
| `mask_session()` | Session strings |
| `mask_connection_url()` | PostgreSQL / Redis URLs |
| `mask_proxy_url()` | Proxy URLs |
| `mask_authorization_header()` | Bearer / Authorization headers |
| `safe_subprocess_error_summary()` | Subprocess failures without raw stderr |
| `redact_freeform_text()` | Free-form command/output redaction |
| `safe_exc_name()` | Exception class only |
| `log_handler_phase()` / `log_callback_failure()` | Structured handler diagnostics |

Static compliance scans live in `app/utils/logging_pipeline_scan.py` and are run via `scripts/check_logging_pipeline.py`.

## Subprocess stderr policy

When a subprocess fails:

- Log **exit code** and **byte counts** (`stderr_bytes`, `stdout_bytes`).
- Do **not** log raw stderr/stdout if it may contain credentials (`pg_dump`, DB CLI tools, reload commands).
- For UI-facing tool details (dev panel health probes), use `safe_subprocess_error_summary()`.
- For reload command output, use `redact_freeform_text()` with configured secrets.

## CLI script stdout policy

| Category | `print()` allowed? | Notes |
|----------|-------------------|-------|
| `scripts/*` validation tools | Yes | Human/JSON reports; use redacted URLs |
| `app/tools/helper_pool_cli.py` | Yes | JSON stdout; `export-session` requires explicit flag |
| `app/database/migrate_sqlite_to_pg.py` | Yes | Migration report only |
| Other `app/` runtime code | **No** | Use `logging.getLogger(__name__)` |

## Forbidden patterns

- Per-module `logger.add()` / `logger.remove()` in runtime code
- `logging.basicConfig()` outside `app/utils/logger.py`
- `FileHandler`, `RotatingFileHandler`, manual `open("*.log")`
- Raw `print()` for runtime diagnostics (CLI JSON output in `app/tools/` is OK)
- `traceback.print_exc()` in production paths
- Logging secret values (see below)

## Never log

- Bot tokens (`BOT_TOKEN`)
- Telegram API hash (`API_HASH`, encrypted credential blobs)
- Full phone numbers (mask with `mask_phone()`)
- OTP codes or 2FA passwords
- Session strings (plaintext or encrypted)
- Database URLs (`DATABASE_URL`) — use `mask_connection_url()` if needed
- Redis URLs (`REDIS_URL`)
- Proxy credentials (host/user/password)
- Private message bodies, link editor contents, or wizard user input text
- Raw `pg_dump` stderr (may contain connection details)

## Always log on handler failures

- Handler or phase name
- Exception class name (`type(exc).__name__`)
- Safe user/chat identifiers when useful
- Masked phone when phone context exists
- Stack trace via `logger.opt(exception=True)` or `logger.exception(...)`

## Formatting

- **Loguru** (`from loguru import logger`): use `{}` placeholders only.
- **stdlib logging** (`logging.getLogger`): `%s` / `%d` is allowed (formatted before interception).

## Change log level

Set in `app/config.env` or environment:

```env
LOG_LEVEL=DEBUG
LOG_FILE=./logs/bot.log
```

Restart the service after changes.

## Inspect logs on server

```bash
sudo journalctl -u musicbot-musicbot-a.service -f
tail -f logs/bot.log
tail -f logs/bot_error.log
sudo journalctl -u musicbot-musicbot-a.service -n 120 --no-pager
```

## Verification

```bash
python scripts/check_logging_pipeline.py
TEST_MODE=1 pytest tests/test_logging_pipeline.py -q
TEST_MODE=1 pytest tests/test_logging_safety.py -q
TEST_MODE=1 pytest tests/test_logging_visibility_and_safety.py -q
```

## Pipeline

- Loguru writes to `stderr` → systemd `StandardError=journal` → the selected instance unit.
- stdlib loggers are bridged through `InterceptHandler` in `app/utils/logger.py`.
- Startup emits one diagnostics line: level, sinks, unbuffered status, service mode.
