# Deployment

> Last verified against repository: 2026-07-19

Canonical runbook:
[Multi-Instance Native Deployment](../MULTI_INSTANCE_DEPLOYMENT.md).

Pre-deployment checks:

1. Confirm Alembic head `0033_instance_database_ownership`.
2. Run `ruff check app/`.
3. Run `python -m compileall -q app`.
4. Run the focused and regression test suites.
5. Run `bash -n setup_server.sh deploy.sh scripts/deploy.sh`.
6. Render and inspect each unit with `setup_server.sh --render-only`.
7. Back up only the selected instance before upgrade.

Production deployment is native Ubuntu/systemd. Docker is not part of the
production multi-instance architecture.
