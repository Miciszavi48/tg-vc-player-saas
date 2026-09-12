# Deployment Index

> **Target Environment:** Python 3.12+ | Ubuntu 22.04 / 24.04 LTS | PostgreSQL 16 | Redis 7  
> **Alembic Head:** `0039_hot_seat`  
> Back to [../index.md](../index.md).

---

## Documents

| Document | Purpose |
|----------|---------|
| [**systemd-docker.md**](systemd-docker.md) | **(Canonical)** Comprehensive production deployment runbook for native Linux systemd services (`musicbot.service`), multi-instance deployments (`setup_server.sh`), and Docker Compose |
| [staging_validation.md](staging_validation.md) | Pre-deployment staging validation checklist and tests |
| [server_native_deployment.md](server_native_deployment.md) | Native server deployment pointer |
| [no_git_deployment_snapshot.md](no_git_deployment_snapshot.md) | Deployment instructions for environments without git (SFTP snapshots) |
| [../MULTI_INSTANCE_DEPLOYMENT.md](../MULTI_INSTANCE_DEPLOYMENT.md) | Multi-instance native deployment runbook |
| [../SIMPLE_MULTI_INSTANCE_GUIDE_FA.md](../SIMPLE_MULTI_INSTANCE_GUIDE_FA.md) | Persian quickstart guide for multi-instance deployments |

---

## Key Deployment Invariants

- **Instance isolation:** Every instance runs with its own dedicated database (`musicbot_<instance>`), Redis namespace (`<instance>:`), data directories, and systemd unit (`musicbot@<instance>.service`).
- **Configuration loading:** Settings are strictly loaded from instance-specific paths (`/opt/<instance>/.env` or `app/config.env`). A root `.env` file is rejected.
- **Migration requirement:** Migrations must be run to head (`0039_hot_seat`) before launching the bot. Automatic table creation (`DB_ALLOW_CREATE_ALL=true`) is strictly prohibited in production.
