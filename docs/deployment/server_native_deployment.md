# Native Server Deployment

> Last verified against repository: 2026-07-19

Use the canonical
[multi-instance native deployment runbook](../MULTI_INSTANCE_DEPLOYMENT.md).

```bash
sudo ./setup_server.sh --instance musicbot-a --path /opt/musicbot-a
sudo ./setup_server.sh --instance musicbot-b --path /opt/musicbot-b
```

The installer generates each `.env`, service account, PostgreSQL database,
Redis namespace, runtime tree, control script, and systemd unit. Do not copy a
fixed service template or run migrations without the selected instance `.env`.
