# Operations & Troubleshooting Guide

> **Status:** Canonical Operational Troubleshooting Manual  
> **Scope:** Voice Chat Streaming, Network & Proxies, Helper Accounts, Callback Routing, Database & Cache

---

## 1. Voice Chat & PyTgCalls Troubleshooting

### 1.1 "PyTgCalls is not available" / Native Extension Build Failure
- **Symptom:** Logs show `PyTgCalls is not available` or bot starts but voice commands respond with `voice_chat_unavailable`.
- **Root Cause:** 
  1. The installed package was the legacy `pytgcalls` instead of `py-tgcalls[pyrogram]`.
  2. Stock `pyrogram` overwrote the required `kurigram` fork during pip dependency resolution.
- **Resolution:**
  ```bash
  # 1. Uninstall colliding packages
  pip uninstall -y pytgcalls py-tgcalls pyrogram kurigram

  # 2. Reinstall py-tgcalls with pyrogram extras (brings ntgcalls prebuilt wheels)
  pip install "py-tgcalls[pyrogram]"

  # 3. Force-reinstall Kurigram LAST so its custom MTProto patches take precedence
  pip install --force-reinstall "https://github.com/KurimuzonAkuma/pyrogram/archive/dev.zip"
  ```

### 1.2 Helper Does Not Join Voice Chat / Stream Fails to Start
- **Symptom:** User sends `/play song`, bot confirms track, but no helper joins the voice chat or no audio plays.
- **Diagnostic Steps:**
  1. Check helper pool status:
     ```bash
     python -m app.tools.helper_pool_cli list
     ```
  2. Verify that a voice chat is actually open in the Telegram group. If the group call is closed, Telegram rejects helper join requests.
  3. Ensure the helper account has permission to speak and video chat admin rights in the group ("Manage Video Chats").
  4. Inspect logs for atomic reservation errors:
     ```bash
     grep -E "reserve_best_helper|stream-candidate" logs/bot.log
     ```
- **Resolution:**
  - If no helper is present in the group, run `افزودن کمکی موزیک` or `/addhelper` to force an immediate helper join.
  - If all helpers are in cooldown, add an additional helper account via the Developer panel (`/dev` → Helper Pool → Add Helper).

### 1.3 `yt-dlp` Extraction Failure (HTTP 403 / "Sign in to confirm you're not a bot")
- **Symptom:** YouTube downloads fail with `HTTP Error 403: Forbidden` or bot reports `error_download`.
- **Root Cause:** YouTube requires JavaScript execution for challenge solving.
- **Resolution:**
  1. Ensure Deno or Node.js is installed on the host and available on the system PATH:
     ```bash
     which deno || which node
     ```
  2. Upgrade `yt-dlp` to the latest release:
     ```bash
     pip install --upgrade yt-dlp
     ```

### 1.4 Audio Stuttering or Sudden Stream Termination
- **Symptom:** Voice chat audio stutters, drops frames, or disconnects after a few minutes.
- **Root Cause:** Network bandwidth bottleneck or FFmpeg transcoding thread pool saturation.
- **Resolution:**
  - Adjust `TRANSCODE_POOL_SIZE` in `app/config.env` (default is 4; reduce on low-spec single-core VPS).
  - Check disk space in `DOWNLOADS_PATH`. If the filesystem is 100% full, FFmpeg pipes fail.

---

## 2. Network, Proxy & MTProto Connectivity

### 2.1 Telegram MTProto Timeouts & DC Connection Drops
- **Symptom:** Bot experiences recurring `ConnectionResetError` or `TimeoutError` when calling Telegram Bot API or MTProto.
- **Root Cause:** Routing degradation between host datacenter and Telegram MTProto datacenters.
- **Resolution:**
  - Verify outbound connectivity to Telegram IP ranges:
    ```bash
    curl -I https://api.telegram.org
    ```
  - For helper accounts, configure per-account SOCKS5 / MTProto proxies using the Developer panel or CLI:
    ```bash
    python -m app.tools.helper_pool_cli set-proxy --helper-id 1 --proxy-url "socks5://user:pass@proxy-host:1080"
    ```

### 2.2 Fast-Creat Vendor API Failures (Instagram / TikTok / Spotify)
- **Symptom:** `پخش <social-link>` fails with `error_failed`.
- **Root Cause:** Fast-Creat vendor API returned 401 (invalid/expired token) or 429 (rate limit).
- **Resolution:**
  - The bot automatically rotates tokens in the pool and disables dead tokens upon encountering 401.
  - Check token pool health in the Developer panel (`/dev` → Fast-Creat Tokens). Add fresh tokens via `@Api_ManagerRoBot`.

---

## 3. Helper Account & Session Management

### 3.1 Helper FloodWait Handling
- **Symptom:** Logs show `[FloodWait: X seconds]` for helper accounts.
- **Mechanism:**
  - Kurigram captures the `FloodWait` exception.
  - The system automatically marks the helper with `floodwait_until = now() + X` in PostgreSQL and creates a Redis cooldown key `helper:floodwait:{helper_id}`.
  - `reserve_best_helper` immediately skips this helper and routes new calls to another active helper in the pool.
- **Action Required:** None; automated recovery will re-enable the helper when the wait expires.

### 3.2 Fernet Session Decryption Failure
- **Symptom:** Helper accounts fail to boot with `InvalidToken` during session decryption.
- **Root Cause:** `HELPER_SESSION_KEY_CURRENT` in `app/config.env` was changed or does not match the key used when sessions were originally added.
- **Resolution:**
  - Restore the original `HELPER_SESSION_KEY_CURRENT` in `app/config.env`.
  - If rotating keys, place the previous key in `HELPER_SESSION_KEY_OLD` and run the session re-encryption tool.

---

## 4. UI & Callback Routing Troubleshooting

### 4.1 "Unknown Callback" (`common.errors.unknown_callback`) Alert
- **Symptom:** User taps an inline keyboard button and receives an alert: "◂ این دکمه شناخته نشده است. لطفاً منو را دوباره باز کنید."
- **Root Cause Audit Checklist:**
  1. **Route-First Rule Violation:** Did the callback handler use a deny-capable filter (e.g. `_pm_dev & filters.regex(...)`) in its registration?
     - *Fix:* Remove deny-capable filters from registration. Route on identity regex only, and perform authorization inside the handler with `@developer_only` or `_guard_private`.
  2. **RawUpdateHandler Collision:** Was a filterless `RawUpdateHandler` registered in group 0?
     - *Fix:* Filterless raw handlers must be in `CALL_SECURITY_RAW_GROUP = -900`.
  3. **Missing Prefix in `_KNOWN_CB_PREFIX`:** Is the callback family prefix registered in `app/handlers/callbacks.py::_KNOWN_CB_PREFIX`?
     - *Fix:* Add the prefix to `_KNOWN_CB_PREFIX` so the two-tier fallback handles it cleanly.
  4. **Stale Message:** If the message is old and has been deleted or expired, the user must send `/panel` or `/help` to open a fresh menu.

### 4.2 Missing i18n Key (`[missing:key.path]`)
- **Symptom:** UI displays `[missing:some.nested.key]`.
- **Resolution:**
  1. Audit missing keys:
     ```bash
     python scripts/split_i18n_resources.py --check
     ```
  2. Add the missing key with translations to both `app/resources/i18n/fa/` and `app/resources/i18n/en/`.
  3. Restart the bot process to reload the i18n in-memory cache.

### 4.3 Webhook Conflict on Polling
- **Symptom:** Bot starts but never processes any messages in polling mode.
- **Resolution:**
  ```bash
  curl "https://api.telegram.org/botYOUR_BOT_TOKEN/deleteWebhook?drop_pending_updates=true"
  ```

---

## 5. Database & Cache Troubleshooting

### 5.1 Database Schema Drift & Pending Migrations
- **Symptom:** Bot fails to boot with `SchemaReadinessError` or runtime throws missing column exceptions.
- **Resolution:**
  ```bash
  cd app
  MUSICBOT_ENV_FILE=config.env alembic upgrade head
  cd ..
  python scripts/db_schema_drift_check.py
  ```
  Expected head: `0039_hot_seat`.

### 5.2 Redis Connection Refused or Memory Exhaustion
- **Symptom:** Logs show `ConnectionRefusedError: Error 111 connecting to localhost:6379`.
- **Resolution:**
  ```bash
  # Check service status
  sudo systemctl status redis-server
  sudo systemctl restart redis-server

  # Verify memory policy in /etc/redis/redis.conf:
  # maxmemory 512mb
  # maxmemory-policy volatile-lru
  ```

### 5.3 Duplicate Daily Deductions Prevention
- **Symptom:** Group credits deducted more than once on the same date.
- **Mechanism & Fix:**
  - Idempotent daily deduction is guaranteed at the database level by the `last_daily_deducted_on` column and the unique partial index `idx_group_credits_daily_deduct_guard`.
  - In addition, Redis distributed locks `daily_deduct:lock:{date}` prevent duplicate worker executions.
