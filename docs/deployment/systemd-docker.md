# Production Deployment Guide: Systemd & Docker

> **Status:** Canonical Production Deployment Runbook  
> **Supported OS:** Ubuntu 22.04 LTS / 24.04 LTS / Debian 12  
> **Database:** PostgreSQL 16 | **Cache:** Redis 7 | **Alembic Head:** `0039_hot_seat`

---

## 1. Prerequisites & Host Environment

### System Dependencies
The host operating system requires Python 3.12+, PostgreSQL 16, Redis 7, FFmpeg, and a JavaScript runtime (Deno or Node.js) for `yt-dlp`:

```bash
# Update and install base packages
sudo apt update && sudo apt install -y \
    python3.12 \
    python3.12-venv \
    python3.12-dev \
    postgresql-16 \
    redis-server \
    ffmpeg \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    curl \
    git

# Install Deno (required by yt-dlp for extraction)
curl -fsSL https://deno.land/install.sh | sh
sudo mv /root/.deno/bin/deno /usr/local/bin/
```

### PostgreSQL 16 Setup
```bash
sudo -u postgres psql -c "CREATE USER musicbot WITH PASSWORD 'your_strong_password';"
sudo -u postgres psql -c "CREATE DATABASE musicbot_prod OWNER musicbot;"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE musicbot_prod TO musicbot;"
```

### Redis 7 Setup
Ensure Redis is running with `decode_responses=True` support:
```bash
sudo systemctl enable --now redis-server
redis-cli ping  # Expects PONG
```

---

## 2. Configuration Management (`app/config.env`)

Configuration is strictly loaded from `app/config.env`. A root `.env` file is **not** an approved configuration candidate.

Create `/opt/musicbot/app/config.env`:

```env
# --- Telegram Bot Credentials ---
BOT_TOKEN=123456789:YOUR_TELEGRAM_BOT_TOKEN
API_ID=12345678
API_HASH=abcdef1234567890abcdef1234567890
DEVELOPER_ID=123456789
DEVELOPER_IDS=123456789

# --- Database & Persistence ---
DATABASE_URL=postgresql+asyncpg://musicbot:your_strong_password@localhost:5432/musicbot_prod
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=10
DB_POOL_TIMEOUT=30
DB_POOL_RECYCLE=1800
DB_ALLOW_CREATE_ALL=false

# --- Redis Cache & Distributed Locks ---
REDIS_URL=redis://localhost:6379/0
REDIS_SETTINGS_TTL=300
REDIS_CREDIT_TTL=300

# --- Helper Pool & Encryption ---
HELPER_SESSION_KEY_CURRENT=YOUR_FERNET_KEY_GENERATE_WITH_PYTHON_CRYPTOGRAPHY
HELPER_SESSION_KEY_OLD=
HELPER_DEFAULT_MAX_CALLS=5
HELPER_DEFAULT_MAX_JOINS_PER_HOUR=20

# --- Fast-Creat Vendor Credentials ---
FAST_CREAT_TOKEN_KEY_CURRENT=YOUR_FERNET_KEY_GENERATE_WITH_PYTHON_CRYPTOGRAPHY
FAST_CREAT_TOKEN_FINGERPRINT_KEY=YOUR_32_BYTE_HEX_OR_HMAC_KEY

# --- Storage & Media Paths ---
DOWNLOADS_PATH=/opt/musicbot/downloads
SESSION_PATH=/opt/musicbot/sessions
LOG_FILE=/opt/musicbot/logs/bot.log
LOG_LEVEL=INFO
MEDIA_CACHE_MAX_GB=20
MEDIA_CACHE_TARGET_GB=15
```

> **Generating Encryption Keys:**
> ```bash
> python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

---

## 3. Deployment Model A: Native Linux Service (`systemd`)

### 1. File Placement & Virtual Environment
```bash
sudo mkdir -p /opt/musicbot /opt/musicbot/logs /opt/musicbot/downloads /opt/musicbot/sessions
sudo chown -R musicbot:musicbot /opt/musicbot

# Create virtualenv and install dependencies
cd /opt/musicbot
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
pip install "py-tgcalls[pyrogram]"
pip install --force-reinstall "https://github.com/KurimuzonAkuma/pyrogram/archive/dev.zip"
```

### 2. Alembic Database Migration
Before starting the process, always upgrade database schema to the latest Alembic revision:
```bash
cd /opt/musicbot/app
MUSICBOT_ENV_FILE=config.env ../.venv/bin/alembic upgrade head
cd /opt/musicbot
../.venv/bin/python scripts/db_schema_drift_check.py
```

### 3. Systemd Service Unit (`/etc/systemd/system/musicbot.service`)
```ini
[Unit]
Description=Telegram Voice Chat Music & Video Player Bot (SaaS)
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
Type=simple
User=musicbot
Group=musicbot
WorkingDirectory=/opt/musicbot
EnvironmentFile=/opt/musicbot/app/config.env
ExecStart=/opt/musicbot/.venv/bin/python -m app.main
Restart=always
RestartSec=5
TimeoutStopSec=30
KillMode=mixed
LimitNOFILE=65536

# Security Hardening
PrivateTmp=true
ProtectSystem=full
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

### 4. Service Activation & Control
```bash
sudo systemctl daemon-reload
sudo systemctl enable musicbot
sudo systemctl start musicbot

# Inspect status and live logs
sudo systemctl status musicbot
sudo journalctl -u musicbot -f
```

---

## 4. Multi-Instance Orchestration (`setup_server.sh`)

For SaaS deployments operating multiple client instances on a single physical host, use the automated instance installer:

```bash
# Deploy an isolated instance named musicbot-customer1
sudo ./setup_server.sh --instance customer1 --path /opt/musicbot-customer1

# Deploy an isolated instance named musicbot-customer2
sudo ./setup_server.sh --instance customer2 --path /opt/musicbot-customer2
```

`setup_server.sh` automatically provisions:
- Dedicated systemd unit: `musicbot-customer1.service`
- Dedicated PostgreSQL database: `musicbot_customer1`
- Dedicated Redis namespace
- Dedicated control utility: `/opt/musicbot-customer1/musicbotctl {status|logs|restart|upgrade}`

---

## 5. Deployment Model B: Docker & Docker Compose

### 1. Production Dockerfile
Create `Dockerfile` in the repository root:

```dockerfile
# Multi-stage production build
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    libssl-dev \
    git \
    curl

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt \
    && pip install --no-cache-dir --prefix=/install "py-tgcalls[pyrogram]" \
    && pip install --no-cache-dir --prefix=/install --force-reinstall "https://github.com/KurimuzonAkuma/pyrogram/archive/dev.zip"

# Final runtime image
FROM python:3.12-slim

WORKDIR /opt/musicbot

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Deno for yt-dlp JavaScript extraction
RUN curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/

COPY --from=builder /install /usr/local
COPY . /opt/musicbot

RUN useradd -m -u 1000 musicbot \
    && mkdir -p /opt/musicbot/logs /opt/musicbot/downloads /opt/musicbot/sessions \
    && chown -R musicbot:musicbot /opt/musicbot

USER musicbot

ENTRYPOINT ["python", "-m", "app.main"]
```

### 2. Docker Compose File (`docker-compose.yml`)
```yaml
version: "3.8"

services:
  postgres:
    image: postgres:16-alpine
    container_name: musicbot_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: musicbot
      POSTGRES_PASSWORD: your_strong_password
      POSTGRES_DB: musicbot_prod
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U musicbot -d musicbot_prod"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: musicbot_redis
    restart: unless-stopped
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  bot:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: musicbot_app
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app/config.env:/opt/musicbot/app/config.env:ro
      - ./downloads:/opt/musicbot/downloads
      - ./logs:/opt/musicbot/logs
      - ./sessions:/opt/musicbot/sessions
    network_mode: host

volumes:
  postgres_data:
  redis_data:
```

### 3. Launching Containers
```bash
# Run database migrations
docker-compose run --rm bot bash -c "cd app && alembic upgrade head"

# Start the services
docker-compose up -d

# Check logs
docker-compose logs -f bot
```

---

## 6. Post-Deployment Verification & Health Checklist

1. **Delete Existing Webhooks:**
   Ensure Telegram long-polling functions without conflicts:
   ```bash
   curl "https://api.telegram.org/botYOUR_BOT_TOKEN/deleteWebhook?drop_pending_updates=true"
   ```

2. **Verify Process State:**
   ```bash
   # Systemd
   systemctl is-active musicbot
   # Docker
   docker-compose ps
   ```

3. **Verify Helper Pool Status:**
   ```bash
   python -m app.tools.helper_pool_cli list
   ```

4. **Verify Database Drift:**
   ```bash
   python scripts/db_schema_drift_check.py
   ```
