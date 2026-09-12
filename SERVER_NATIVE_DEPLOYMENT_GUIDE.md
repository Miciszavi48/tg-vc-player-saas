# Native Ubuntu Deployment

The authoritative runbook is
[`docs/MULTI_INSTANCE_DEPLOYMENT.md`](docs/MULTI_INSTANCE_DEPLOYMENT.md).

```bash
sudo ./setup_server.sh --instance musicbot-a --path /opt/musicbot-a
sudo ./setup_server.sh --instance musicbot-b --path /opt/musicbot-b
```

Do not install `deployment/musicbot.service` unchanged. `setup_server.sh`
generates the unit, `.env`, database target, paths, ports, and control script
for the selected instance.
