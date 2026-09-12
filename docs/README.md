# Documentation Overview

> **Telegram Voice Chat Music & Video Player Bot** — SaaS Platform Documentation

Full documentation roadmap is available at [**`index.md`**](index.md).

---

## Canonical Guides

| Area | Canonical Document | Description |
|---|---|---|
| **Architecture** | [**`architecture/system-design.md`**](architecture/system-design.md) | Core bot architecture, startup sequence, session management, PyTgCalls voice stack, and priority group routing. |
| **Deployment** | [**`deployment/systemd-docker.md`**](deployment/systemd-docker.md) | Linux systemd service configuration, multi-instance deployment (`setup_server.sh`), and Docker Compose setup. |
| **Features** | [**`features/commands-and-panels.md`**](features/commands-and-panels.md) | Complete reference of slash commands, Persian & English text commands, role hierarchy, and admin panels. |
| **Operations** | [**`operations/troubleshooting.md`**](operations/troubleshooting.md) | Voice streaming troubleshooting, network/proxies, helper accounts, callbacks, and database recovery. |

---

## Infrastructure & Multi-Tenancy

- [**Multi-Instance Deployment Guide**](MULTI_INSTANCE_DEPLOYMENT.md) — Comprehensive guide to deploying multiple isolated instances on one host.
- [**Database Schema & ORM Models**](DATABASE_AND_SCHEMA.md) — PostgreSQL tables, models, and Alembic migration tracking (`0039_hot_seat`).
- [**Redis & Caching Architecture**](REDIS_AND_CACHE.md) — Redis key spaces, TTLs, and distributed locking (`SET NX PX`).
- [**Credit Source of Truth**](CREDIT_SOURCE_OF_TRUTH.md) — Group credit persistence and daily deduction mechanics.
- [**Testing & Safety Rules**](TESTING_AND_SAFETY.md) — Test isolation rules and test execution guide.

---

## Command Line Entry Points

```bash
# Start bot in polling mode
python -m app.main

# Helper account pool management CLI
python -m app.tools.helper_pool_cli --help

# Run database migrations
cd app && alembic upgrade head

# Run database schema drift check
python scripts/db_schema_drift_check.py
```
