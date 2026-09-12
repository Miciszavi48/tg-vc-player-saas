# No-Git / SFTP Deployment

> Last verified against repository: 2026-07-19

Upload and extract a release directory, then use the same instance-aware native
installer. Git is not required on the target server.

```bash
cd /path/to/extracted-release
sudo ./setup_server.sh --instance musicbot-a --path /opt/musicbot-a
sudoedit /opt/musicbot-a/.env
sudo ./setup_server.sh --instance musicbot-a --path /opt/musicbot-a
```

For a second copy:

```bash
sudo ./setup_server.sh --instance musicbot-b --path /opt/musicbot-b
```

The complete isolation, update, migration, backup, rollback, and removal
procedure is in
[Multi-Instance Native Deployment](../MULTI_INSTANCE_DEPLOYMENT.md).
