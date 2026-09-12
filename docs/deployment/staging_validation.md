# Staging Validation

> Last verified against repository: 2026-07-19

Use a distinct staging instance ID, folder, database, bot token, Redis
namespace, and ports:

```bash
sudo ./setup_server.sh \
  --instance musicbot-staging \
  --path /opt/musicbot-staging \
  --health-port 18110 \
  --metrics-port 19110
```

Required checks:

1. `musicbotctl migrate` reaches `0033_instance_database_ownership`.
2. The database marker contains `musicbot-staging`.
3. Redis keys begin with `musicbot:musicbot-staging:`.
4. Session, cache, downloads, temp, logs, backups, PID, and lock files remain
   inside the staging paths.
5. The staging unit starts while another instance is active.
6. Both health/metrics ports bind independently.
7. Helper login, playback, cleanup, backup, restart, and uninstall affect only
   staging.

See [Multi-Instance Native Deployment](../MULTI_INSTANCE_DEPLOYMENT.md) for the
full runbook. A code/test pass is not a substitute for this concurrent
server-side validation.
