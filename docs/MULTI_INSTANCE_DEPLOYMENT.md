# Multi-Instance Native Deployment

This is the canonical Ubuntu/systemd runbook for running independent MusicBot
instances without Docker.

## Isolation Model

Each instance has one required stable `INSTANCE_ID` matching:

```text
^[a-z][a-z0-9-]{0,27}$
```

`BOT_INSTANCE_ID` is accepted as an alias for manual compatibility. If both are
set, they must match. Production startup fails when neither is set.

The supported database model is **one PostgreSQL database per instance**.
Shared PostgreSQL servers are supported; shared databases/schemas are not.
Migration `0033_instance_database_ownership` adds a singleton ownership marker.
Startup atomically claims an unclaimed database and rejects a database claimed
by another `INSTANCE_ID`.

One Redis server/database may be shared. Every application key, lock, cache,
FSM state, rate limit, scheduler coordination key, and scan pattern is prefixed:

```text
${REDIS_NAMESPACE_ROOT}:${INSTANCE_ID}:
```

The default root is `musicbot`, for example `musicbot:musicbot-a:`.

Additional ownership boundaries:

- Main and helper Telegram client names include the instance ID.
- Session, download, cache, temporary, backup, log, PID, lock, and reload files
  use instance-owned paths.
- A filesystem lock, PostgreSQL advisory lease, and renewable Redis lease
  prevent duplicate processes for the same instance state.
- APScheduler job IDs and backup filenames include the instance ID.
- Cleanup is limited to configured instance directories. FFmpeg cleanup only
  examines descendants of the current process.
- Health and metrics listeners have independent configurable ports. Port `0`
  disables a listener.
- `BOT_RELOAD_COMMAND` is rejected. Use the instance sentinel or generated
  `musicbotctl restart`, which targets only the selected unit.

## Folder Layout

For `musicbot-a`:

```text
/opt/musicbot-a/
├── app/
│   └── config.env          <- the only runtime env file (0600)
├── venv/
├── musicbotctl
└── var/
    ├── backups/
    ├── cache/
    ├── downloads/
    ├── logs/
    │   ├── bot.log
    │   └── bot_error.log
    ├── sessions/
    └── tmp/

/run/musicbot-musicbot-a/
├── musicbot.lock
├── musicbot.pid
└── reload.request

/etc/systemd/system/musicbot-musicbot-a.service
/etc/musicbot/instances/musicbot-a.conf
```

The second instance uses `/opt/musicbot-b`, `/run/musicbot-musicbot-b`, a
different service account, unit, database, Redis prefix, and optional ports.

## Environment Variables

Required:

| Variable | Purpose |
| --- | --- |
| `INSTANCE_ID` | Stable instance identity |
| `BOT_TOKEN` | BotFather token unique to this instance |
| `API_ID`, `API_HASH` | Telegram application credentials |
| `DEVELOPER_ID` | Instance owner/developer IDs |
| `DATABASE_URL` | Dedicated PostgreSQL database URL |
| `REDIS_URL` | Fixed `redis://127.0.0.1:6379/0`; shared, isolated by namespace |

Isolation variables normally managed by the installer:

| Variable | Default/contract |
| --- | --- |
| `INSTANCE_ROOT` | Selected `--path`, or the folder holding `setup_server.sh` |
| `INSTANCE_DATA_DIR` | `${INSTANCE_ROOT}/var` |
| `DATABASE_ISOLATION_MODE` | Must be `database` |
| `REDIS_NAMESPACE_ROOT` | Fixed `musicbot`; never prompted for |
| `DOWNLOADS_PATH`, `SESSION_PATH` | Separate directories |
| `LOG_FILE`, `MEDIA_CACHE_PATH`, `TEMP_PATH`, `BACKUP_DIR` | Separate directories |
| `RUNTIME_PATH`, `PID_FILE`, `LOCK_FILE` | Selected `/run/musicbot-ID` |
| `HEALTH_HOST`, `HEALTH_PORT` | Local health listener; `0` disables |
| `METRICS_HOST`, `METRICS_PORT` | Prometheus text listener; `0` disables |
| `BOT_RELOAD_SENTINEL_PATH` | Must be under `RUNTIME_PATH` |

Encryption and fingerprint keys should also be different secrets per instance.

## Install Two Instances

The primary command takes no arguments: run it from inside the instance folder.
The install path is the real directory holding `setup_server.sh` (never the
caller's cwd, symlinks resolved) and the instance id is the folder basename,
confirmed once. Explicit flags below are advanced options that override
inference.

```bash
cd /opt/musicbot-a && sudo ./setup_server.sh --health-port 18101 --metrics-port 19101
cd /opt/musicbot-b && sudo ./setup_server.sh --health-port 18102 --metrics-port 19102
```

On a first interactive run the installer prompts once for anything missing
(`BOT_TOKEN`, `API_ID`, `API_HASH`, `DEVELOPER_ID`, database name/user, ports)
and writes them to `<path>/app/config.env` at 0600 without echoing secrets. The
database password is generated securely unless `MUSICBOT_DB_PASSWORD` is set. A
non-interactive run never hangs: it fails clearly, or leaves the service enabled
but unstarted until the values exist.

Equivalent fully-explicit form for automation:

```bash
sudo ./setup_server.sh --instance musicbot-a --path /opt/musicbot-a \
  --health-port 18101 --metrics-port 19101 --yes
```

### The config file

`<INSTANCE_ROOT>/app/config.env` is the only runtime env file — it is what the
unit's `EnvironmentFile`/`MUSICBOT_ENV_FILE`, Alembic, `musicbotctl`, backups and
settings discovery all use. `<INSTANCE_ROOT>/.env` is never created and is not a
discovery candidate. An older install's root `.env` is migrated on the next run:
its values win, it is verified key-by-key, archived as `.env.migrated.<stamp>`,
and any replaced `app/config.env` is kept as `config.env.superseded.<stamp>`. A
root `.env` naming a different instance is refused, never migrated.

`REDIS_URL=redis://127.0.0.1:6379/0` and `REDIS_NAMESPACE_ROOT=musicbot` are
fixed for every instance and never prompted for; isolation comes from the
`musicbot:<INSTANCE_ID>:` key namespace.

The installer is idempotent. Re-running `sudo ./setup_server.sh` inside an
installed folder preserves every existing secret, prompts only for values that
are still missing, creates/reuses the folder and venv, creates a dedicated Linux
account/database/role, runs Alembic only with that instance's `app/config.env`,
writes one unit, and enables/restarts only that unit. It rejects invalid IDs,
path/database/port reuse recorded under `/etc/musicbot/instances`, mismatched
existing units, and live port conflicts.

To edit credentials by hand instead of using the prompts:

```bash
sudoedit /opt/musicbot-a/app/config.env
cd /opt/musicbot-a && sudo ./setup_server.sh
```

Do not reuse bot tokens, databases, encryption keys, or endpoint ports.

### Validating two instances concurrently

Health/metrics default to `0` (disabled); the ports above enable them and give
each instance an independent probe. After both units are started:

```bash
# Both units up, each with its own PID and journal
systemctl is-active musicbot-musicbot-a.service musicbot-musicbot-b.service
systemctl show -p MainPID --value musicbot-musicbot-a.service
systemctl show -p MainPID --value musicbot-musicbot-b.service

# Endpoints answer independently and report their own identity
curl -s http://127.0.0.1:18101/ ; echo    # {"status":"ok","instance_id":"musicbot-a"}
curl -s http://127.0.0.1:18102/ ; echo    # {"status":"ok","instance_id":"musicbot-b"}
curl -s http://127.0.0.1:19101/ | grep musicbot_up
curl -s http://127.0.0.1:19102/ | grep musicbot_up

# Redis keys stay inside each namespace and never overlap
redis-cli --scan --pattern 'musicbot:musicbot-a:*' | head
redis-cli --scan --pattern 'musicbot:musicbot-b:*' | head
redis-cli --scan --pattern '*' | grep -vE '^musicbot:musicbot-(a|b):' || echo "no unnamespaced keys"

# One claim row per database, each naming its own instance
sudo -u postgres psql -d musicbot_musicbot_a -tAc \
  'SELECT instance_id FROM bot_instance_metadata WHERE id=1'
sudo -u postgres psql -d musicbot_musicbot_b -tAc \
  'SELECT instance_id FROM bot_instance_metadata WHERE id=1'

# Runtime state is per instance
ls -l /run/musicbot-musicbot-a/musicbot.pid /run/musicbot-musicbot-b/musicbot.pid
sudo journalctl -u musicbot-musicbot-a.service -n 20 --no-pager

# Stopping one must not disturb the other
sudo systemctl stop musicbot-musicbot-a.service
curl -s http://127.0.0.1:18102/ ; echo                      # b still ok
redis-cli exists musicbot:musicbot-a:runtime:owner          # 0 - lease released
sudo systemctl start musicbot-musicbot-a.service
```

Each instance needs its own bot token, `DEVELOPER_ID` (Owner), and helper
accounts in `.env`. Two instances sharing a token will fight over Telegram
`getUpdates` regardless of this isolation.

## Migrating A Pre-Multi-Instance Install

Installs made before this architecture run a single fixed `musicbot.service`
unit. That unit and a generated `musicbot-<id>.service` must never both target
the same folder: they would compete for the same `.env`, sessions, and runtime
state. The installer therefore scans `/etc/systemd/system` for any other unit
whose `WorkingDirectory` equals `--path` and whose `ExecStart` runs `app.main`,
and refuses to install alongside it:

```
[WARN] Another unit already runs /opt/musicbot:
[WARN]   - musicbot.service
[FAIL] Refusing to install musicbot-musicbot.service alongside it. ...
```

Pass `--adopt-legacy-unit` to stop, disable, and remove the old unit and hand
the folder to the generated one:

```bash
sudo ./setup_server.sh \
  --instance musicbot \
  --path /opt/musicbot \
  --db-name musicbot_db \
  --db-user musicbot_user \
  --adopt-legacy-unit
```

This stops the bot briefly. `--db-name`/`--db-user` keep the existing database
instead of creating `musicbot_musicbot`. The legacy `app/config.env` is copied
to `.env` on first run (in-place installs only, i.e. `--source` = `--path`), and
instance-owned paths are rewritten to the `var/` layout.

`--render-only` reports the conflict without changing anything, so the migration
can be reviewed first:

```bash
sudo ./setup_server.sh --instance musicbot --path /opt/musicbot \
  --adopt-legacy-unit --render-only /tmp/preview
```

### Operator runbook: migrating the live single instance

Adoption stops the running bot, so it needs a deliberate maintenance window.
Downtime is the gap between step 5 and step 6 (seconds). Every fallible step —
packages, venv, `.env`, database, migrations — runs *before* the old unit is
stopped, so a failure leaves the old unit running and rollback untouched.

> A legacy install whose code has already been updated in place **cannot restart
> on the old unit**: the new code needs `RUNTIME_PATH` write access the legacy
> unit's `ReadWritePaths` does not grant, and it requires Alembic head `0033`.
> Verify both blockers before assuming the old unit is a working fallback.

**1. Backup and preflight**

```bash
cd /opt/musicbot
STAMP=$(date +%Y%m%d_%H%M%S)
sudo mkdir -p /var/backups/musicbot-migration

# Database (full dump, not schema-only)
sudo -u postgres pg_dump musicbot_db | gzip -9 \
  | sudo tee /var/backups/musicbot-migration/musicbot_db_$STAMP.sql.gz >/dev/null

# Rollback targets: the unit, the env, and the code tree as it is today
sudo cp /etc/systemd/system/musicbot.service \
        /var/backups/musicbot-migration/musicbot.service.$STAMP
sudo cp /opt/musicbot/app/config.env \
        /var/backups/musicbot-migration/config.env.$STAMP
sudo tar czf /var/backups/musicbot-migration/opt_musicbot_$STAMP.tar.gz \
  --exclude=venv --exclude=var --exclude=.git -C /opt musicbot

# Preflight (all read-only)
sudo grep -c '^INSTANCE_ID=musicbot$' app/config.env      # expect 1
sudo grep -E '^BOT_RELOAD_COMMAND=$' app/config.env       # must be empty
sudo -u postgres psql -d musicbot_db -tAc 'SELECT version_num FROM alembic_version'
redis-cli ping                                            # expect PONG
df -h /opt | awk 'NR==2{print $4}'                        # need room for the venv
```

**2. Preview the adoption (changes nothing)**

```bash
sudo ./setup_server.sh --instance musicbot --path /opt/musicbot \
  --service-user musicbot --db-name musicbot_db --db-user musicbot_user \
  --adopt-legacy-unit --render-only /tmp/preview
cat /tmp/preview/musicbot-musicbot.service
```

Confirm `User=musicbot`, `EnvironmentFile=/opt/musicbot/app/config.env`, and
`ReadWritePaths=/opt/musicbot/var /run/musicbot-musicbot`. The preview's
`DATABASE_URL` line is a redacted placeholder; the real run keeps the existing
URL and password from `app/config.env`.

**3–5. Adopt, migrate, and restart**

```bash
sudo ./setup_server.sh --instance musicbot --path /opt/musicbot \
  --service-user musicbot --db-name musicbot_db --db-user musicbot_user \
  --adopt-legacy-unit
```

One command performs, in order: migrate the legacy root `.env` into
`app/config.env` (preserving an archive) and rewrite the instance-owned paths;
verify the database name/role match; run `alembic upgrade head` using **only**
this instance's `app/config.env` (`0032` -> `0033`); stop and remove
`musicbot.service`; write and enable `musicbot-musicbot.service`; restart it.
Run it from `/opt/musicbot` so `--source` equals `--path`; that preserves the
in-place install while `setup_server.sh` performs the legacy env migration.

**6. Validate**

```bash
systemctl is-active musicbot-musicbot.service
sudo journalctl -u musicbot-musicbot.service -n 50 --no-pager

# Writable paths and env
sudo -u musicbot test -w /opt/musicbot/var && echo "var writable"
ls -ld /run/musicbot-musicbot
sudo grep -E '^(INSTANCE_ID|RUNTIME_PATH|LOCK_FILE)=' /opt/musicbot/app/config.env

# Database: at head and claimed by this instance
sudo -u postgres psql -d musicbot_db -tAc 'SELECT version_num FROM alembic_version'
sudo -u postgres psql -d musicbot_db -tAc \
  'SELECT instance_id FROM bot_instance_metadata WHERE id=1'   # expect musicbot

# Redis: keys now namespaced, lease held
redis-cli --scan --pattern 'musicbot:musicbot:*' | head
redis-cli ttl musicbot:musicbot:runtime:owner                  # expect <= 90

# Telegram / helpers / playback
sudo -u musicbot grep -m1 '^BOT_TOKEN=' /opt/musicbot/app/config.env | cut -d= -f2 \
  | xargs -I{} curl -s "https://api.telegram.org/bot{}/getMe"   # expect ok:true
sudo journalctl -u musicbot-musicbot.service | grep -E 'Bot started as|handlers registered'
/opt/musicbot/musicbotctl status
```

Then exercise one real chat: `/play` a track, confirm audio joins the voice
chat, and confirm a helper account joins. Nothing below the Telegram line is
covered by automated tests.

**7. Rollback (startup fails and is not a quick fix)**

Because the code tree on disk is already the multi-instance version, reverting
the unit alone is not enough — restore the code snapshot and the schema together:

```bash
STAMP=<the stamp used above>
sudo systemctl disable --now musicbot-musicbot.service
sudo rm -f /etc/systemd/system/musicbot-musicbot.service /etc/musicbot/instances/musicbot.conf

# Schema back to 0032 (0033 only adds bot_instance_metadata; downgrade drops it
# and touches no existing data)
cd /opt/musicbot/app
sudo -u musicbot env MUSICBOT_ENV_FILE=/opt/musicbot/app/config.env PYTHONPATH=/opt/musicbot \
  /opt/musicbot/venv/bin/alembic downgrade 0032_fast_creat_token_pool

# Code and unit back to the pre-migration snapshot
sudo tar xzf /var/backups/musicbot-migration/opt_musicbot_$STAMP.tar.gz -C /opt
sudo cp /var/backups/musicbot-migration/musicbot.service.$STAMP \
        /etc/systemd/system/musicbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now musicbot.service
systemctl is-active musicbot.service
```

Restore the database dump only if the schema is actually damaged — the migration
adds a table and does not modify existing rows:

```bash
sudo systemctl stop musicbot.service
gzip -dc /var/backups/musicbot-migration/musicbot_db_$STAMP.sql.gz \
  | sudo -u postgres psql -d musicbot_db
```

A legacy unit also predates the required `RUNTIME_PATH`. Its `ReadWritePaths`
does not include the folder holding the default PID/lock directory
(`INSTANCE_DATA_DIR/run`), so under `ProtectSystem=strict` a legacy unit
restarted on current code fails with `mkdir: Read-only file system`. The
generated unit sets `RUNTIME_PATH=/run/<service>` and lists it in
`ReadWritePaths`; migrating with `--adopt-legacy-unit` resolves this.

## Per-Instance Operations

```bash
/opt/musicbot-a/musicbotctl status
/opt/musicbot-a/musicbotctl restart
/opt/musicbot-a/musicbotctl logs
/opt/musicbot-a/musicbotctl stop

/opt/musicbot-b/musicbotctl status
/opt/musicbot-b/musicbotctl restart
/opt/musicbot-b/musicbotctl logs
/opt/musicbot-b/musicbotctl stop
```

Equivalent scoped installer operations:

```bash
sudo ./setup_server.sh --status --instance musicbot-a --path /opt/musicbot-a
sudo ./setup_server.sh --restart --instance musicbot-a --path /opt/musicbot-a
sudo ./setup_server.sh --logs --instance musicbot-a --path /opt/musicbot-a
sudo ./setup_server.sh --stop --instance musicbot-a --path /opt/musicbot-a
```

File logs are under the instance `var/logs`; journal logs use the unique unit.

## Updates And Migrations

```bash
/opt/musicbot-a/musicbotctl backup
/opt/musicbot-a/musicbotctl upgrade /path/to/new-release
/opt/musicbot-a/musicbotctl status
```

The upgrade preserves `.env`, `var/`, and `venv/`, installs dependencies, runs
Alembic against only `musicbot-a`'s `DATABASE_URL`, and restarts only its unit.

Migration-only:

```bash
/opt/musicbot-a/musicbotctl migrate
/opt/musicbot-b/musicbotctl migrate
```

Current Alembic head is `0033_instance_database_ownership`.

## Backup And Restore

```bash
/opt/musicbot-a/musicbotctl backup
/opt/musicbot-a/musicbotctl stop
/opt/musicbot-a/musicbotctl restore /opt/musicbot-a/var/backups/FILE.sql.gz
/opt/musicbot-a/musicbotctl start
```

Scheduled and manual backups include the instance ID. Restore only into the
database named in the same `.env`; a different database ownership marker is
rejected at startup.

## Rollback

1. Stop only the selected unit.
2. Restore that instance's previous source release.
3. Restore its database backup if the migration is not backward compatible.
4. Run `musicbotctl migrate` at the release's expected revision if required.
5. Start and inspect only that instance.

Never copy another instance's `.env`, sessions, Redis namespace, or database
backup over the target.

## Removal

```bash
/opt/musicbot-a/musicbotctl uninstall
```

Removal deletes only the selected unit and registry entry. It deliberately
preserves `.env`, sessions, backups, source, and PostgreSQL data. After
verifying backups, remove those resources manually by their exact instance
names.

## Validation Status And Remaining Risks

Automated tests cover two-ID Redis keys, session names, runtime paths, file/PID
locks, stale locks, scheduler IDs, database ownership, endpoint conflicts,
cleanup boundaries, installer reruns, invalid IDs, registry path/port
conflicts, legacy-unit adoption, generated unit structure, compile/static
checks, and shell syntax.

### Crash recovery and the Redis lease

Each instance holds a Redis lease at `musicbot:<id>:runtime:owner` (TTL 90s,
renewed every 30s) so a second copy of the same instance cannot start. A clean
`SIGTERM` releases it immediately, but `SIGKILL`, an OOM kill, or a power loss
does not: the lease then lingers until its TTL expires, and restarts inside that
window fail with `Redis lease is already held`.

The generated unit therefore retries for longer than the TTL
(`RestartSec=15` x `StartLimitBurst=10` = 150s > 90s). With the earlier
`10 x 5 = 50s` budget, systemd exhausted its retries before the lease expired and
left the unit permanently `failed` after an OOM. If you tune either value, keep
the product above the TTL — `test_restart_budget_outlasts_redis_lease` enforces
this against the constant in `app/runtime/instance.py`.

To recover a unit that has already given up:

```bash
sudo systemctl reset-failed musicbot-<id>.service
sudo systemctl start musicbot-<id>.service
```

Two guards protect a shared Redis and the migration head:

- `conftest.py` refuses to run tests against Redis db 0. Deployed instances use
  it, and suite fixtures call `flushdb()`, which would wipe every instance's
  keys and leases at once. Override only with `ALLOW_EXTERNAL_REDIS=1`.
- `test_expected_alembic_head_constants_match_the_real_head` pins every
  `EXPECTED_*_HEAD` constant under `scripts/` to the migration tree, so a new
  migration cannot silently leave deploy/validation scripts reporting a correct
  database as stale.

### Verified by concurrent execution

Two instance processes (`musicbot-a`, `musicbot-b`) were started concurrently
against live PostgreSQL and Redis, each with its own `.env`, database, ports,
and runtime directory. Observed while both were running:

- separate health/metrics endpoints reporting their own `instance_id`;
- Redis keys only under `musicbot:musicbot-a:` / `musicbot:musicbot-b:`;
- one `bot_instance_metadata` claim row per database;
- a duplicate `INSTANCE_ID` rejected (`InstanceAlreadyRunningError`);
- a foreign database rejected (`Database instance ownership mismatch`);
- a taken port rejected (`OSError: [Errno 98] address already in use`);
- SIGTERM to one instance released only its own PID file, Redis lease, and
  ports, leaving the other untouched; its stale lock file was safely reacquired
  on restart.

A second concurrent run (`musicbot-p`, `musicbot-q`) additionally covered crash
behaviour: `SIGKILL` on one instance left the other's endpoint, lease, and PID
untouched, and the killed instance recovered over its stale lock and PID file —
but only once the Redis lease TTL expired, which is what the restart budget above
is sized for.

Alembic ran independently against each database, reaching
`0033_instance_database_ownership`. The `0033` downgrade was exercised too: it
drops only `bot_instance_metadata`, returns the revision to `0032`, leaves
existing rows untouched, and re-upgrades cleanly.

### Not verified

The Telegram connection was **not** exercised: no bot tokens were available, so
`bot.start()`, playback, helper sessions, and PyTgCalls were never driven, and
the two instances were not started as concurrent systemd units. Native
`tgcalls`, FFmpeg, Telegram flood limits, capacity, firewall policy, and backup
restore drills remain server risks. Before production readiness, an operator
must start both units concurrently with real credentials and exercise
playback/helpers.

The adoption path itself is verified only up to the point of stopping a live
service: the refusal, the render-only preview, the ordering, and the idempotent
removal were all exercised, but no legacy unit that was actually running has been
adopted. Memory is also a real constraint — the full test suite OOMs on a 3.8 GB
host and must be run in chunks; two instances plus PostgreSQL and Redis on one
small box will be tight.
