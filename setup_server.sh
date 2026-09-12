#!/usr/bin/env bash
# Repeatable native Ubuntu installer for independent MusicBot instances.
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC} $*"; }
ok() { echo -e "${GREEN}[ OK ]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
Usage:
  sudo ./setup_server.sh                      # install/refresh this folder
  sudo ./setup_server.sh [options]

Run it from inside the instance folder with no arguments. The install path is the
real directory holding this script, and the instance id is the folder basename
(/opt/musicbot -> musicbot), confirmed once. Missing credentials are prompted for
on a first interactive run and written to <path>/app/config.env.

Advanced (explicit flags always override inference):
  --instance ID             Stable lowercase id: ^[a-z][a-z0-9-]{0,27}$
  --path PATH               Absolute installation directory.
  -y, --yes                 Accept inferred values; never prompt.

Install options:
  --source PATH             Release source. Default: directory containing this script.
  --service-user USER       Default: mb-INSTANCE
  --db-name NAME            Default: musicbot_INSTANCE (hyphens become underscores)
  --db-user NAME            Default: musicbot_INSTANCE
  --health-port PORT        0 disables. Default: 0
  --metrics-port PORT       0 disables. Default: 0
  --redis-url URL           Default for a new env: redis://127.0.0.1:6379/0
  --no-start                Install/enable but do not start or restart the service.
  --skip-packages           Skip apt package installation.
  --adopt-legacy-unit       Stop/remove a pre-multi-instance unit (e.g. musicbot.service)
                            that targets the same --path. Refused by default.
  --render-only DIR         Render env/unit/control files without changing the server.

Scoped operations:
  --status | --restart | --stop | --logs | --uninstall
  --upgrade                 Re-run installation using --source as the new release.

Environment:
  MUSICBOT_DB_PASSWORD      Explicit password for a new/existing instance DB role.
                            Generated securely when unset.

Notes:
  Config file is always <path>/app/config.env. A root <path>/.env written by an
  older installer is migrated into it and archived.
  REDIS_URL and REDIS_NAMESPACE_ROOT are fixed for every instance; isolation
  comes from the key namespace musicbot:<INSTANCE_ID>:.

Examples:
  cd /opt/musicbot-a && sudo ./setup_server.sh
  sudo ./setup_server.sh --instance musicbot-b --path /opt/musicbot-b \
    --health-port 18101 --metrics-port 19101
USAGE
}

# Resolve through symlinks to the real script location. The install target is
# derived from where this script actually lives, never from the caller's cwd.
SCRIPT_PATH="$(realpath -e "${BASH_SOURCE[0]}" 2>/dev/null || true)"
[[ -n "$SCRIPT_PATH" ]] || fail "Cannot resolve the real path of this script"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

INSTANCE_ID=""
INSTALL_DIR=""
EXPLICIT_PATH=false
SOURCE_DIR="$SCRIPT_DIR"
ASSUME_YES=false
SERVICE_USER=""
DB_NAME=""
DB_USER=""
DB_NAME_EXPLICIT=""
DB_USER_EXPLICIT=""
HEALTH_PORT_EXPLICIT=""
METRICS_PORT_EXPLICIT=""
HEALTH_PORT=0
METRICS_PORT=0
REDIS_URL_DEFAULT="redis://127.0.0.1:6379/0"
START_SERVICE=true
SKIP_PACKAGES=false
ADOPT_LEGACY_UNIT=false
RENDER_ONLY=""
ACTION="install"
EXPLICIT_DB_PASSWORD="${MUSICBOT_DB_PASSWORD:-}"
REGISTRY_ROOT="${MUSICBOT_INSTANCE_REGISTRY:-/etc/musicbot/instances}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --instance)
            [[ $# -ge 2 ]] || fail "--instance requires a value"
            INSTANCE_ID="$2"
            shift 2
            ;;
        --path|--install-dir)
            [[ $# -ge 2 ]] || fail "$1 requires a path"
            INSTALL_DIR="$2"
            EXPLICIT_PATH=true
            shift 2
            ;;
        --source)
            [[ $# -ge 2 ]] || fail "--source requires a path"
            SOURCE_DIR="$2"
            shift 2
            ;;
        --service-user)
            [[ $# -ge 2 ]] || fail "--service-user requires a value"
            SERVICE_USER="$2"
            shift 2
            ;;
        --db-name)
            [[ $# -ge 2 ]] || fail "--db-name requires a value"
            DB_NAME="$2"
            DB_NAME_EXPLICIT=1
            shift 2
            ;;
        --db-user)
            [[ $# -ge 2 ]] || fail "--db-user requires a value"
            DB_USER="$2"
            DB_USER_EXPLICIT=1
            shift 2
            ;;
        --health-port)
            [[ $# -ge 2 ]] || fail "--health-port requires a value"
            HEALTH_PORT="$2"
            HEALTH_PORT_EXPLICIT=1
            shift 2
            ;;
        --metrics-port)
            [[ $# -ge 2 ]] || fail "--metrics-port requires a value"
            METRICS_PORT="$2"
            METRICS_PORT_EXPLICIT=1
            shift 2
            ;;
        --redis-url)
            [[ $# -ge 2 ]] || fail "--redis-url requires a value"
            REDIS_URL_DEFAULT="$2"
            shift 2
            ;;
        --no-start)
            START_SERVICE=false
            shift
            ;;
        --skip-packages)
            SKIP_PACKAGES=true
            shift
            ;;
        --adopt-legacy-unit)
            ADOPT_LEGACY_UNIT=true
            shift
            ;;
        --yes|-y)
            ASSUME_YES=true
            shift
            ;;
        --render-only)
            [[ $# -ge 2 ]] || fail "--render-only requires a directory"
            RENDER_ONLY="$2"
            shift 2
            ;;
        --status|--restart|--stop|--logs|--uninstall|--upgrade)
            ACTION="${1#--}"
            shift
            ;;
        --start)
            START_SERVICE=true
            shift
            ;;
        --service-name)
            fail "--service-name is no longer supported; the unit is derived from --instance"
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1"
            ;;
    esac
done

_registry_read() {
    [[ -f "$1" ]] || return 1
    sed -n "s/^${2}=//p" "$1" | tail -n 1
}

interactive() {
    [[ -t 0 && -t 1 ]]
}

# ── Target resolution: explicit flags always win over inference ─────────────
if [[ "$ACTION" == "upgrade" && "$EXPLICIT_PATH" != true ]]; then
    # An upgrade is normally run FROM a new release folder. That folder is never
    # the install target, so never fall back to this script's directory here:
    # resolve the real installation from the registry, or fail clearly.
    _resolved=""
    if [[ -n "$INSTANCE_ID" ]]; then
        _resolved="$(_registry_read "${REGISTRY_ROOT}/${INSTANCE_ID}.conf" INSTALL_DIR || true)"
        [[ -n "$_resolved" ]] \
            || fail "Instance ${INSTANCE_ID} is not registered under ${REGISTRY_ROOT}. Pass --path to name the installation to upgrade."
    else
        _entries=()
        for _entry in "$REGISTRY_ROOT"/*.conf; do
            [[ -e "$_entry" ]] && _entries+=("$_entry")
        done
        [[ ${#_entries[@]} -eq 1 ]] \
            || fail "Cannot infer which installation to upgrade (${#_entries[@]} registered under ${REGISTRY_ROOT}). Pass --instance ID or --path PATH."
        _resolved="$(_registry_read "${_entries[0]}" INSTALL_DIR || true)"
        INSTANCE_ID="$(_registry_read "${_entries[0]}" INSTANCE_ID || true)"
        [[ -n "$_resolved" && -n "$INSTANCE_ID" ]] \
            || fail "Registry entry ${_entries[0]} is incomplete; pass --instance and --path explicitly."
    fi
    INSTALL_DIR="$_resolved"
    info "Upgrading registered instance ${INSTANCE_ID} at ${INSTALL_DIR}"
fi

# Default install target: the real directory holding this script, never the
# caller's cwd.
[[ -n "$INSTALL_DIR" ]] || INSTALL_DIR="$SCRIPT_DIR"
[[ "$INSTALL_DIR" = /* ]] || fail "--path must be absolute"

# Derive the instance id from the folder name when not given.
if [[ -z "$INSTANCE_ID" ]]; then
    _derived="$(basename "$INSTALL_DIR")"
    [[ "$_derived" =~ ^[a-z][a-z0-9-]{0,27}$ ]] \
        || fail "Cannot derive an instance id from folder name '${_derived}'. Pass --instance ID matching ^[a-z][a-z0-9-]{0,27}$"
    INSTANCE_ID="$_derived"
    if [[ -z "$RENDER_ONLY" && "$ASSUME_YES" != true ]] && interactive; then
        read -r -p "Use instance id '${INSTANCE_ID}' for ${INSTALL_DIR}? [Y/n] " _reply
        case "${_reply:-y}" in
            [Yy]|[Yy][Ee][Ss]|"") ;;
            *) fail "Aborted. Re-run with --instance ID to choose a different id." ;;
        esac
    else
        info "Using inferred instance id '${INSTANCE_ID}' from ${INSTALL_DIR}"
    fi
fi

[[ "$INSTANCE_ID" =~ ^[a-z][a-z0-9-]{0,27}$ ]] \
    || fail "Invalid instance id. Expected ^[a-z][a-z0-9-]{0,27}$"

INSTALL_DIR="$(realpath -m "$INSTALL_DIR")"
SOURCE_DIR="$(realpath -m "$SOURCE_DIR")"
[[ "$INSTALL_DIR" != "/" && "$INSTALL_DIR" != "/opt" && "$INSTALL_DIR" != "/usr" ]] \
    || fail "Refusing unsafe installation path: $INSTALL_DIR"
[[ "$INSTALL_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] \
    || fail "--path may contain only ASCII letters, digits, dot, underscore, slash, and hyphen"
[[ -d "$SOURCE_DIR/app" && -f "$SOURCE_DIR/requirements.txt" ]] \
    || fail "Source does not contain app/ and requirements.txt: $SOURCE_DIR"
[[ "$REDIS_URL_DEFAULT" =~ ^rediss?://[^[:space:]]+$ ]] \
    || fail "--redis-url must be a redis:// or rediss:// URL without whitespace"

# An already-registered instance keeps the identity it was installed with. Without
# this, a bare re-run would fall back to the instance-derived defaults and try to
# switch service user, database, and ports out from under a working install.
# Explicit flags still win.
_registry_defaults="${REGISTRY_ROOT}/${INSTANCE_ID}.conf"
if [[ -f "$_registry_defaults" ]] \
   && [[ "$(_registry_read "$_registry_defaults" INSTALL_DIR || true)" == "$INSTALL_DIR" ]]; then
    [[ -n "$SERVICE_USER" ]] \
        || SERVICE_USER="$(_registry_read "$_registry_defaults" SERVICE_USER || true)"
    [[ -n "$DB_NAME" ]] \
        || DB_NAME="$(_registry_read "$_registry_defaults" DB_NAME || true)"
    [[ -n "$DB_USER" ]] \
        || DB_USER="$(_registry_read "$_registry_defaults" DB_USER || true)"
    if [[ -z "$HEALTH_PORT_EXPLICIT" ]]; then
        _reg_port="$(_registry_read "$_registry_defaults" HEALTH_PORT || true)"
        [[ -z "$_reg_port" ]] || HEALTH_PORT="$_reg_port"
    fi
    if [[ -z "$METRICS_PORT_EXPLICIT" ]]; then
        _reg_port="$(_registry_read "$_registry_defaults" METRICS_PORT || true)"
        [[ -z "$_reg_port" ]] || METRICS_PORT="$_reg_port"
    fi
    info "Reusing registered settings for ${INSTANCE_ID} (user=${SERVICE_USER}, db=${DB_NAME}, ports=${HEALTH_PORT}/${METRICS_PORT})"
fi

DB_SUFFIX="${INSTANCE_ID//-/_}"
SERVICE_USER="${SERVICE_USER:-mb-${INSTANCE_ID}}"
DB_NAME="${DB_NAME:-musicbot_${DB_SUFFIX}}"
DB_USER="${DB_USER:-musicbot_${DB_SUFFIX}}"
SERVICE_NAME="musicbot-${INSTANCE_ID}"
UNIT_NAME="${SERVICE_NAME}.service"
SYSTEMD_DIR="${MUSICBOT_SYSTEMD_DIR:-/etc/systemd/system}"
UNIT_FILE="${SYSTEMD_DIR}/${UNIT_NAME}"
REGISTRY_FILE="${REGISTRY_ROOT}/${INSTANCE_ID}.conf"
# Canonical runtime env file. <INSTANCE_ROOT>/.env is never created or used;
# a root .env left by an older installer is migrated into this file.
ENV_FILE="${INSTALL_DIR}/app/config.env"
LEGACY_ROOT_ENV="${INSTALL_DIR}/.env"
VENV_DIR="${INSTALL_DIR}/venv"
DATA_DIR="${INSTALL_DIR}/var"
DOWNLOADS_DIR="${DATA_DIR}/downloads"
SESSIONS_DIR="${DATA_DIR}/sessions"
LOGS_DIR="${DATA_DIR}/logs"
CACHE_DIR="${DATA_DIR}/cache"
TEMP_DIR="${DATA_DIR}/tmp"
BACKUP_DIR="${DATA_DIR}/backups"
RUNTIME_DIR="/run/${SERVICE_NAME}"
PID_FILE="${RUNTIME_DIR}/musicbot.pid"
LOCK_FILE="${RUNTIME_DIR}/musicbot.lock"
CONTROL_FILE="${INSTALL_DIR}/musicbotctl"

# ── First-run prompts for values needed before any validation ───────────────
# Only on a genuinely new install, only interactively, and only for values the
# caller did not pass as flags. Everything here is validated below.
first_run() {
    [[ -f "$ENV_FILE" ]] || return 0
    case "$(sed -n 's/^BOT_TOKEN=//p' "$ENV_FILE" | tail -n 1)" in
        ""|*CHANGE_ME*|*REPLACE_WITH*) return 0 ;;
        *) return 1 ;;
    esac
}

if [[ "$ACTION" == "install" && -z "$RENDER_ONLY" && "$ASSUME_YES" != true ]] \
   && interactive && first_run; then
    info "Setting up a new instance in ${INSTALL_DIR} (press Enter to accept defaults)"
    if [[ -z "$DB_NAME_EXPLICIT" ]]; then
        read -r -p "  PostgreSQL database name [${DB_NAME}]: " _reply
        DB_NAME="${_reply:-$DB_NAME}"
    fi
    if [[ -z "$DB_USER_EXPLICIT" ]]; then
        read -r -p "  PostgreSQL database user [${DB_USER}]: " _reply
        DB_USER="${_reply:-$DB_USER}"
    fi
    if [[ -z "$HEALTH_PORT_EXPLICIT" ]]; then
        read -r -p "  Health endpoint port (0 disables) [${HEALTH_PORT}]: " _reply
        HEALTH_PORT="${_reply:-$HEALTH_PORT}"
    fi
    if [[ -z "$METRICS_PORT_EXPLICIT" ]]; then
        read -r -p "  Metrics endpoint port (0 disables) [${METRICS_PORT}]: " _reply
        METRICS_PORT="${_reply:-$METRICS_PORT}"
    fi
    info "The database password is generated securely unless MUSICBOT_DB_PASSWORD is set."
fi

[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] \
    || fail "Invalid service user: $SERVICE_USER"
[[ "$DB_NAME" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] \
    || fail "Invalid database name: $DB_NAME"
[[ "$DB_USER" =~ ^[a-z_][a-z0-9_]{0,62}$ ]] \
    || fail "Invalid database user: $DB_USER"
[[ "$HEALTH_PORT" =~ ^[0-9]+$ && "$HEALTH_PORT" -le 65535 ]] \
    || fail "Invalid health port: $HEALTH_PORT"
[[ "$METRICS_PORT" =~ ^[0-9]+$ && "$METRICS_PORT" -le 65535 ]] \
    || fail "Invalid metrics port: $METRICS_PORT"
if [[ "$HEALTH_PORT" -ne 0 && "$HEALTH_PORT" -eq "$METRICS_PORT" ]]; then
    fail "Health and metrics ports must be different"
fi

if [[ -z "$RENDER_ONLY" && $EUID -ne 0 ]]; then
    fail "Run as root, or use --render-only for validation"
fi

registry_value() {
    local file="$1"
    local key="$2"
    [[ -f "$file" ]] || return 1
    sed -n "s/^${key}=//p" "$file" | tail -n 1
}

assert_registry_safety() {
    local existing_path=""
    if [[ -f "$REGISTRY_FILE" ]]; then
        existing_path="$(registry_value "$REGISTRY_FILE" INSTALL_DIR || true)"
        [[ "$existing_path" == "$INSTALL_DIR" ]] \
            || fail "Instance $INSTANCE_ID is already registered at $existing_path"
    fi

    local file other_id other_path other_db other_health other_metrics
    if [[ -d "$REGISTRY_ROOT" ]]; then
        for file in "$REGISTRY_ROOT"/*.conf; do
            [[ -e "$file" && "$file" != "$REGISTRY_FILE" ]] || continue
            other_id="$(registry_value "$file" INSTANCE_ID || true)"
            other_path="$(registry_value "$file" INSTALL_DIR || true)"
            other_db="$(registry_value "$file" DB_NAME || true)"
            other_health="$(registry_value "$file" HEALTH_PORT || true)"
            other_metrics="$(registry_value "$file" METRICS_PORT || true)"
            [[ "$other_path" != "$INSTALL_DIR" ]] \
                || fail "Path $INSTALL_DIR is already used by instance $other_id"
            [[ "$other_db" != "$DB_NAME" ]] \
                || fail "Database $DB_NAME is already used by instance $other_id"
            for requested in "$HEALTH_PORT" "$METRICS_PORT"; do
                [[ "$requested" -eq 0 ]] && continue
                [[ "$requested" != "$other_health" && "$requested" != "$other_metrics" ]] \
                    || fail "Port $requested is already registered to instance $other_id"
            done
        done
    fi

    if [[ -f "$UNIT_FILE" && ! -f "$REGISTRY_FILE" ]]; then
        local unit_path
        unit_path="$(sed -n 's/^WorkingDirectory=//p' "$UNIT_FILE" | tail -n 1)"
        [[ "$unit_path" == "$INSTALL_DIR" ]] \
            || fail "$UNIT_FILE already exists for another path: $unit_path"
    fi
}

conflicting_units() {
    # Units other than ours that run this project from the same directory.
    # Catches pre-multi-instance installs (musicbot.service) that would fight
    # ${UNIT_NAME} for the same path, sessions, and runtime state.
    local file base workdir execstart
    for file in "$SYSTEMD_DIR"/*.service; do
        [[ -e "$file" ]] || continue
        base="$(basename "$file")"
        [[ "$base" != "$UNIT_NAME" ]] || continue
        workdir="$(sed -n 's/^WorkingDirectory=//p' "$file" | tail -n 1)"
        [[ "$workdir" == "$INSTALL_DIR" ]] || continue
        execstart="$(sed -n 's/^ExecStart=//p' "$file" | tail -n 1)"
        [[ "$execstart" == *app.main* ]] || continue
        printf '%s\n' "$base"
    done
}

# Detection only — never mutates. Runs early so a conflict fails fast, before
# any packages, venv, database, or migration work.
assert_no_conflicting_units() {
    local units unit
    units="$(conflicting_units)"
    [[ -n "$units" ]] || return 0

    if [[ "$ADOPT_LEGACY_UNIT" != true ]]; then
        warn "Another unit already runs ${INSTALL_DIR}:"
        while read -r unit; do
            [[ -n "$unit" ]] || continue
            warn "  - ${unit}"
        done <<< "$units"
        fail "Refusing to install ${UNIT_NAME} alongside it. Re-run with --adopt-legacy-unit to stop and replace it, or choose a different --path."
    fi

    while read -r unit; do
        [[ -n "$unit" ]] || continue
        if [[ -n "$RENDER_ONLY" ]]; then
            warn "--render-only: ${unit} would be stopped and removed by --adopt-legacy-unit"
        else
            warn "Will adopt ${unit} once the new unit is ready to be written"
        fi
    done <<< "$units"
}

# Destructive — stops the old service. Deliberately deferred until every fallible
# step (packages, venv, env, database, migrations) has already succeeded, so a
# failure never strands the host with the old unit deleted and no replacement.
# This is also what keeps the adoption downtime to seconds.
adopt_conflicting_units() {
    [[ "$ADOPT_LEGACY_UNIT" == true ]] || return 0
    local units unit
    units="$(conflicting_units)"
    [[ -n "$units" ]] || return 0

    while read -r unit; do
        [[ -n "$unit" ]] || continue
        warn "Adopting legacy unit ${unit}: stopping and removing it in favour of ${UNIT_NAME}"
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
        rm -f "${SYSTEMD_DIR}/${unit}"
    done <<< "$units"
    systemctl daemon-reload
}

check_live_port() {
    local port="$1"
    [[ "$port" -ne 0 ]] || return 0
    if [[ -f "$REGISTRY_FILE" ]] && {
        [[ "$(registry_value "$REGISTRY_FILE" HEALTH_PORT || true)" == "$port" ]] ||
        [[ "$(registry_value "$REGISTRY_FILE" METRICS_PORT || true)" == "$port" ]];
    }; then
        return 0
    fi
    if ss -ltnH "sport = :${port}" 2>/dev/null | grep -q .; then
        fail "TCP port $port is already listening"
    fi
}

render_env_preview() {
    cat <<ENV
INSTANCE_ID=${INSTANCE_ID}
INSTANCE_ROOT=${INSTALL_DIR}
INSTANCE_DATA_DIR=${DATA_DIR}
DATABASE_ISOLATION_MODE=database
DATABASE_URL=postgresql+asyncpg://${DB_USER}:REDACTED@127.0.0.1:5432/${DB_NAME}
REDIS_URL=${REDIS_URL_DEFAULT}
REDIS_NAMESPACE_ROOT=musicbot
DOWNLOADS_PATH=${DOWNLOADS_DIR}
SESSION_PATH=${SESSIONS_DIR}
LOG_FILE=${LOGS_DIR}/bot.log
MEDIA_CACHE_PATH=${CACHE_DIR}
TEMP_PATH=${TEMP_DIR}
BACKUP_DIR=${BACKUP_DIR}
RUNTIME_PATH=${RUNTIME_DIR}
PID_FILE=${PID_FILE}
LOCK_FILE=${LOCK_FILE}
HEALTH_HOST=127.0.0.1
HEALTH_PORT=${HEALTH_PORT}
METRICS_HOST=127.0.0.1
METRICS_PORT=${METRICS_PORT}
BOT_RELOAD_SENTINEL_PATH=${RUNTIME_DIR}/reload.request
ENV
}

render_unit() {
    cat <<UNIT
[Unit]
Description=MusicBot instance ${INSTANCE_ID}
After=network-online.target postgresql.service redis-server.service
Wants=network-online.target postgresql.service redis-server.service
# The restart budget must outlast the Redis runtime lease TTL. After SIGKILL/OOM
# the lease is not released and survives up to _REDIS_LEASE_TTL_SECONDS (90s), so
# early restarts fail with "Redis lease is already held". RestartSec x
# StartLimitBurst (15 x 10 = 150s) leaves room to retry past expiry instead of
# parking the unit in failed state. See test_restart_budget_outlasts_redis_lease.
StartLimitIntervalSec=600
StartLimitBurst=10

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
Environment="PYTHONPATH=${INSTALL_DIR}"
Environment="PYTHONUNBUFFERED=1"
Environment="MUSICBOT_ENV_FILE=${ENV_FILE}"
Environment="PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=${VENV_DIR}/bin/python -m app.main
Restart=on-failure
RestartSec=15
TimeoutStopSec=45
KillSignal=SIGTERM
LimitNOFILE=100000
LimitNPROC=32768
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
RuntimeDirectory=${SERVICE_NAME}
RuntimeDirectoryMode=0750
ReadWritePaths=${DATA_DIR} ${RUNTIME_DIR}

[Install]
WantedBy=multi-user.target
UNIT
}

render_control_script() {
    cat <<CONTROL
#!/usr/bin/env bash
set -euo pipefail
UNIT=${UNIT_NAME@Q}
ROOT=${INSTALL_DIR@Q}
ENV_FILE=${ENV_FILE@Q}
VENV=${VENV_DIR@Q}
INSTANCE=${INSTANCE_ID@Q}
REGISTRY=${REGISTRY_FILE@Q}
BACKUPS=${BACKUP_DIR@Q}
UNIT_FILE=${UNIT_FILE@Q}

env_value() {
    sudo -u ${SERVICE_USER@Q} sed -n "s/^\$1=//p" "\$ENV_FILE" | tail -n 1
}

case "\${1:-status}" in
    status) sudo systemctl status "\$UNIT" --no-pager ;;
    start) sudo systemctl start "\$UNIT" ;;
    restart) sudo systemctl restart "\$UNIT" ;;
    stop) sudo systemctl stop "\$UNIT" ;;
    logs) sudo journalctl -u "\$UNIT" -f ;;
    migrate)
        cd "\$ROOT/app"
        sudo -u ${SERVICE_USER@Q} env MUSICBOT_ENV_FILE="\$ENV_FILE" \
            PYTHONPATH="\$ROOT" "\$VENV/bin/alembic" upgrade head
        ;;
    backup)
        sudo -u ${SERVICE_USER@Q} mkdir -p "\$BACKUPS"
        stamp="\$(date +%Y%m%d_%H%M%S)"
        db_url="\$(env_value DATABASE_URL | sed 's/+asyncpg//')"
        output="\$BACKUPS/\${INSTANCE}_manual_\${stamp}.sql.gz"
        sudo -u ${SERVICE_USER@Q} bash -c \
            'pg_dump "\$1" | gzip -9 > "\$2"' _ "\$db_url" "\$output"
        ;;
    restore)
        [[ \$# -eq 2 && -f "\$2" ]] || {
            echo "Usage: \$0 restore /path/to/backup.sql.gz" >&2
            exit 2
        }
        sudo systemctl is-active --quiet "\$UNIT" && {
            echo "Stop \$UNIT before restore" >&2
            exit 1
        }
        db_url="\$(env_value DATABASE_URL | sed 's/+asyncpg//')"
        sudo -u ${SERVICE_USER@Q} bash -c \
            'gzip -dc "\$1" | psql "\$2"' _ "\$2" "\$db_url"
        ;;
    upgrade)
        release="\${2:-\$ROOT}"
        sudo "\$release/setup_server.sh" --upgrade \
            --instance "\$INSTANCE" --path "\$ROOT" --source "\$release"
        ;;
    uninstall)
        sudo systemctl disable --now "\$UNIT" 2>/dev/null || true
        sudo rm -f "\$UNIT_FILE" "\$REGISTRY"
        sudo systemctl daemon-reload
        echo "Unit removed. Database, app/config.env, backups, sessions, and \$ROOT were preserved."
        ;;
    *)
        echo "Usage: \$0 {status|start|restart|stop|logs|migrate|backup|restore|upgrade|uninstall}" >&2
        exit 2
        ;;
esac
CONTROL
}

if [[ -z "$RENDER_ONLY" ]]; then
    mkdir -p /run/lock
    exec 9>/run/lock/musicbot-instance-installer.lock
    flock -x 9
fi

assert_registry_safety

# Only install/upgrade own the unit; scoped actions (status/logs/...) must not be
# blocked by an unrelated legacy unit.
if [[ "$ACTION" == "install" || "$ACTION" == "upgrade" ]]; then
    assert_no_conflicting_units
fi

if [[ -n "$RENDER_ONLY" ]]; then
    RENDER_ONLY="$(realpath -m "$RENDER_ONLY")"
    mkdir -p "$RENDER_ONLY"
    render_env_preview > "${RENDER_ONLY}/${INSTANCE_ID}.config.env"
    render_unit > "${RENDER_ONLY}/${UNIT_NAME}"
    render_control_script > "${RENDER_ONLY}/musicbotctl-${INSTANCE_ID}"
    chmod 0755 "${RENDER_ONLY}/musicbotctl-${INSTANCE_ID}"
    ok "Rendered instance files in $RENDER_ONLY"
    exit 0
fi

run_scoped_action() {
    [[ -f "$REGISTRY_FILE" ]] \
        || fail "Instance $INSTANCE_ID is not registered at $REGISTRY_FILE"
    [[ "$(registry_value "$REGISTRY_FILE" INSTALL_DIR || true)" == "$INSTALL_DIR" ]] \
        || fail "Registered path does not match --path"
    case "$ACTION" in
        status) systemctl status "$UNIT_NAME" --no-pager ;;
        restart) systemctl restart "$UNIT_NAME" ;;
        stop) systemctl stop "$UNIT_NAME" ;;
        logs) journalctl -u "$UNIT_NAME" -f ;;
        uninstall)
            systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
            rm -f "$UNIT_FILE" "$REGISTRY_FILE"
            systemctl daemon-reload
            ok "Removed only ${UNIT_NAME}; files and database were preserved."
            ;;
        *) fail "Unsupported scoped action: $ACTION" ;;
    esac
}

if [[ "$ACTION" != "install" && "$ACTION" != "upgrade" ]]; then
    run_scoped_action
    exit 0
fi

check_live_port "$HEALTH_PORT"
check_live_port "$METRICS_PORT"

install_packages() {
    [[ "$SKIP_PACKAGES" == false ]] || {
        info "Skipping apt packages by request."
        return
    }
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y \
        ca-certificates curl gnupg iproute2 lsb-release software-properties-common

    if ! command -v python3.12 >/dev/null 2>&1; then
        add-apt-repository -y ppa:deadsnakes/ppa
        apt-get update
    fi

    if ! apt-cache show postgresql-16 >/dev/null 2>&1; then
        install -d /usr/share/postgresql-common/pgdg
        curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
            | gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
        echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
            > /etc/apt/sources.list.d/pgdg.list
        apt-get update
    fi

    apt-get install -y \
        ffmpeg git libffi-dev libpq-dev libssl-dev openssl postgresql-16 \
        postgresql-client-16 python3.12 python3.12-dev python3.12-venv \
        redis-server rsync shellcheck util-linux
    systemctl enable --now postgresql
    if systemctl list-unit-files --type=service | grep -q '^redis-server\.service'; then
        systemctl enable --now redis-server
    else
        systemctl enable --now redis
    fi
}

copy_release() {
    if ! id "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
    fi
    mkdir -p "$INSTALL_DIR"
    if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
        rsync -a --delete \
            --exclude='.git/' \
            --exclude='.env' \
            --exclude='app/config.env' \
            --exclude='config.env' \
            --exclude='venv/' \
            --exclude='.venv/' \
            --exclude='var/' \
            --exclude='__pycache__/' \
            --exclude='.pytest_cache/' \
            --exclude='.ruff_cache/' \
            "$SOURCE_DIR/" "$INSTALL_DIR/"
    fi
    mkdir -p \
        "$DOWNLOADS_DIR" "$SESSIONS_DIR" "$LOGS_DIR" "$CACHE_DIR" \
        "$TEMP_DIR" "$BACKUP_DIR"
}

install_venv() {
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        command -v python3.12 >/dev/null 2>&1 \
            || fail "Python 3.12 is required; rerun without --skip-packages"
        python3.12 -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
    "$VENV_DIR/bin/pip" install --no-deps pyromod
    # Voice chat: the PyPI name is py-tgcalls, the import name is pytgcalls, and
    # it must be installed WITH deps so the ntgcalls native wheel comes along.
    # The similarly named legacy "pytgcalls" distribution shadows the same import
    # with a build that needs the unavailable tgcalls C extension, which silently
    # disables voice ("pytgcalls is not available"). See requirements.txt.
    "$VENV_DIR/bin/pip" uninstall -y pytgcalls >/dev/null 2>&1 || true
    "$VENV_DIR/bin/pip" install "py-tgcalls[pyrogram]"
    # Kurigram must be reinstalled last: pyromod/py-tgcalls pull stock pyrogram,
    # which would otherwise overwrite the required fork.
    "$VENV_DIR/bin/pip" install --force-reinstall \
        https://github.com/KurimuzonAkuma/pyrogram/archive/dev.zip
    "$VENV_DIR/bin/pip" install --upgrade yt-dlp
}

sql_ident() {
    printf '"%s"' "${1//\"/\"\"}"
}

sql_literal() {
    printf "'%s'" "${1//\'/\'\'}"
}

url_database_name() {
    "$VENV_DIR/bin/python" - "$1" <<'PY'
import sys
from sqlalchemy.engine import make_url
print(make_url(sys.argv[1]).database or "")
PY
}

url_database_user() {
    "$VENV_DIR/bin/python" - "$1" <<'PY'
import sys
from sqlalchemy.engine import make_url
print(make_url(sys.argv[1]).username or "")
PY
}

url_database_password() {
    "$VENV_DIR/bin/python" - "$1" <<'PY'
import sys
from sqlalchemy.engine import make_url
print(make_url(sys.argv[1]).password or "")
PY
}

get_env() {
    local key="$1"
    [[ -f "$ENV_FILE" ]] || return 1
    sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1
}

set_env() {
    local key="$1"
    local value="$2"
    local escaped="${value//\\/\\\\}"
    escaped="${escaped//&/\\&}"
    escaped="${escaped//|/\\|}"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

migrate_root_env() {
    # Older installers wrote <root>/.env. That file must not survive: settings
    # discovery no longer reads it, so leaving it behind would silently strand
    # edits made to it. Migrate it into app/config.env only when it belongs to
    # this instance, keeping its values and permissions.
    [[ -f "$LEGACY_ROOT_ENV" ]] || return 0

    local legacy_instance
    legacy_instance="$(sed -n 's/^INSTANCE_ID=//p' "$LEGACY_ROOT_ENV" | tail -n 1)"
    [[ -z "$legacy_instance" || "$legacy_instance" == "$INSTANCE_ID" ]] \
        || fail "${LEGACY_ROOT_ENV} belongs to instance ${legacy_instance}, not ${INSTANCE_ID}. Refusing to migrate it."

    local stamp archived
    stamp="$(date +%Y%m%d_%H%M%S)"
    archived="${LEGACY_ROOT_ENV}.migrated.${stamp}"

    if [[ -f "$ENV_FILE" ]]; then
        cp -a "$ENV_FILE" "${ENV_FILE}.superseded.${stamp}"
        chmod 0600 "${ENV_FILE}.superseded.${stamp}"
        warn "Kept previous ${ENV_FILE} as ${ENV_FILE}.superseded.${stamp}"
    fi

    # The root .env was the live runtime file, so its values win.
    mkdir -p "$(dirname "$ENV_FILE")"
    cp -a "$LEGACY_ROOT_ENV" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"

    local key expected actual
    for key in INSTANCE_ID BOT_TOKEN API_ID API_HASH DEVELOPER_ID DATABASE_URL; do
        expected="$(sed -n "s/^${key}=//p" "$LEGACY_ROOT_ENV" | tail -n 1)"
        actual="$(sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1)"
        [[ "$expected" == "$actual" ]] \
            || fail "Migration of ${LEGACY_ROOT_ENV} lost ${key}; aborting before removing it."
    done

    mv "$LEGACY_ROOT_ENV" "$archived"
    chmod 0600 "$archived"
    warn "Migrated ${LEGACY_ROOT_ENV} to ${ENV_FILE} (archived as ${archived})"
}

prompt_value() {
    # prompt_value KEY PROMPT [DEFAULT] [secret]
    local key="$1" prompt="$2" default="${3:-}" secret="${4:-}" current reply
    current="$(get_env "$key" || true)"
    case "$current" in
        ""|0|*CHANGE_ME*|*REPLACE_WITH*) ;;
        *) return 0 ;;   # already configured: never re-prompt, never print it
    esac

    if ! interactive; then
        fail "${key} is not set in ${ENV_FILE} and this run is not interactive. Set it in the file or pass the matching flag."
    fi

    while :; do
        if [[ -n "$secret" ]]; then
            read -r -s -p "  ${prompt}: " reply; echo
        elif [[ -n "$default" ]]; then
            read -r -p "  ${prompt} [${default}]: " reply
            reply="${reply:-$default}"
        else
            read -r -p "  ${prompt}: " reply
        fi
        [[ -n "$reply" ]] && break
        warn "  A value is required."
    done
    set_env "$key" "$reply"
}

run_first_run_wizard() {
    local need=false key
    for key in BOT_TOKEN API_ID API_HASH DEVELOPER_ID; do
        case "$(get_env "$key" || true)" in
            ""|0|*CHANGE_ME*|*REPLACE_WITH*) need=true ;;
        esac
    done
    [[ "$need" == true ]] || return 0

    if ! interactive; then
        return 0   # service_env_valid reports the missing keys and blocks start
    fi

    info "First-run setup for instance ${INSTANCE_ID} (values are written to ${ENV_FILE})"
    prompt_value BOT_TOKEN "Bot token from @BotFather"
    prompt_value API_ID "API_ID from my.telegram.org"
    prompt_value API_HASH "API_HASH from my.telegram.org"
    prompt_value DEVELOPER_ID "Developer/Owner Telegram user id"

    local token api_id dev_id
    token="$(get_env BOT_TOKEN || true)"
    [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] \
        || fail "BOT_TOKEN does not look like a Telegram bot token (expected 123456:ABC-DEF...)"
    api_id="$(get_env API_ID || true)"
    [[ "$api_id" =~ ^[0-9]+$ ]] || fail "API_ID must be numeric"
    dev_id="$(get_env DEVELOPER_ID || true)"
    [[ "$dev_id" =~ ^-?[0-9]+(,-?[0-9]+)*$ ]] \
        || fail "DEVELOPER_ID must be one or more comma-separated Telegram user ids"
    ok "Credentials stored in ${ENV_FILE}"
}

prepare_env_base() {
    migrate_root_env

    mkdir -p "$(dirname "$ENV_FILE")"
    if [[ ! -f "$ENV_FILE" ]]; then
        if [[ -f "$INSTALL_DIR/app/config.env.example" ]]; then
            cp "$INSTALL_DIR/app/config.env.example" "$ENV_FILE"
        elif [[ -f "$INSTALL_DIR/config.env.example" ]]; then
            cp "$INSTALL_DIR/config.env.example" "$ENV_FILE"
        elif [[ -f "$INSTALL_DIR/.env.example" ]]; then
            cp "$INSTALL_DIR/.env.example" "$ENV_FILE"
        else
            fail "No app/config.env.example/config.env.example found in $INSTALL_DIR"
        fi
    fi
    chmod 0600 "$ENV_FILE"

    local existing_instance
    existing_instance="$(get_env INSTANCE_ID || true)"
    [[ -z "$existing_instance" || "$existing_instance" == "$INSTANCE_ID" ]] \
        || fail "$ENV_FILE belongs to instance $existing_instance"

    set_env INSTANCE_ID "$INSTANCE_ID"
    set_env INSTANCE_ROOT "$INSTALL_DIR"
    set_env INSTANCE_DATA_DIR "$DATA_DIR"
    set_env DATABASE_ISOLATION_MODE "database"
    set_env REDIS_NAMESPACE_ROOT "musicbot"
    set_env DOWNLOADS_PATH "$DOWNLOADS_DIR"
    set_env SESSION_PATH "$SESSIONS_DIR"
    set_env LOG_FILE "$LOGS_DIR/bot.log"
    set_env MEDIA_CACHE_PATH "$CACHE_DIR"
    set_env TEMP_PATH "$TEMP_DIR"
    set_env BACKUP_DIR "$BACKUP_DIR"
    set_env RUNTIME_PATH "$RUNTIME_DIR"
    set_env PID_FILE "$PID_FILE"
    set_env LOCK_FILE "$LOCK_FILE"
    set_env HEALTH_HOST "127.0.0.1"
    set_env HEALTH_PORT "$HEALTH_PORT"
    set_env METRICS_HOST "127.0.0.1"
    set_env METRICS_PORT "$METRICS_PORT"
    set_env BOT_RELOAD_SENTINEL_PATH "$RUNTIME_DIR/reload.request"
    # Shared infrastructure: fixed for every instance and never prompted for.
    # Isolation comes from the namespace musicbot:<INSTANCE_ID>:, not from
    # separate Redis servers or databases.
    set_env REDIS_URL "$REDIS_URL_DEFAULT"
    set_env REDIS_NAMESPACE_ROOT "musicbot"
}

prepare_database() {
    local role_lit db_lit role_ident db_ident
    role_lit="$(sql_literal "$DB_USER")"
    db_lit="$(sql_literal "$DB_NAME")"
    role_ident="$(sql_ident "$DB_USER")"
    db_ident="$(sql_ident "$DB_NAME")"

    local role_exists db_exists db_owner password db_url existing_url
    role_exists="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname=${role_lit}" | tr -d '[:space:]')"
    db_exists="$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname=${db_lit}" | tr -d '[:space:]')"
    existing_url="$(get_env DATABASE_URL || true)"
    db_url=""

    if [[ -n "$existing_url" && "$existing_url" != *CHANGE_ME* && "$existing_url" != *REPLACE_WITH* ]]; then
        [[ "$(url_database_name "$existing_url")" == "$DB_NAME" ]] \
            || fail "DATABASE_URL in $ENV_FILE targets another database"
        [[ "$(url_database_user "$existing_url")" == "$DB_USER" ]] \
            || fail "DATABASE_URL in $ENV_FILE uses another database role"
        db_url="$existing_url"
    fi

    password="$EXPLICIT_DB_PASSWORD"
    if [[ -z "$password" && -n "$db_url" ]]; then
        password="$(url_database_password "$db_url")"
    fi
    if [[ "$role_exists" != "1" ]]; then
        [[ -n "$password" ]] || password="$(openssl rand -hex 24)"
        sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
            "CREATE ROLE ${role_ident} LOGIN PASSWORD $(sql_literal "$password");" >/dev/null
    elif [[ -n "$password" ]]; then
        sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
            "ALTER ROLE ${role_ident} LOGIN PASSWORD $(sql_literal "$password");" >/dev/null
    fi

    if [[ "$db_exists" != "1" ]]; then
        sudo -u postgres psql -v ON_ERROR_STOP=1 -c \
            "CREATE DATABASE ${db_ident} OWNER ${role_ident} ENCODING 'UTF8';" >/dev/null
    else
        db_owner="$(sudo -u postgres psql -tAc \
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=${db_lit}" \
            | tr -d '[:space:]')"
        [[ "$db_owner" == "$DB_USER" ]] \
            || fail "Existing database $DB_NAME is owned by $db_owner, not $DB_USER"
    fi

    if [[ -z "$db_url" ]]; then
        [[ -n "$password" ]] \
            || fail "Existing DB role password is unknown; set MUSICBOT_DB_PASSWORD"
        local quoted
        quoted="$("$VENV_DIR/bin/python" -c \
            'from urllib.parse import quote; import sys; print(quote(sys.argv[1], safe=""))' \
            "$password")"
        db_url="postgresql+asyncpg://${DB_USER}:${quoted}@127.0.0.1:5432/${DB_NAME}"
        set_env DATABASE_URL "$db_url"
    fi

    local claimed
    claimed="$(sudo -u postgres psql -d "$DB_NAME" -tAc \
        "SELECT to_regclass('public.bot_instance_metadata')" | tr -d '[:space:]')"
    if [[ "$claimed" == "bot_instance_metadata" ]]; then
        claimed="$(sudo -u postgres psql -d "$DB_NAME" -tAc \
            "SELECT instance_id FROM bot_instance_metadata WHERE id=1" | tr -d '[:space:]')"
        [[ -z "$claimed" || "$claimed" == "$INSTANCE_ID" ]] \
            || fail "Database $DB_NAME is claimed by instance $claimed"
    fi
}

generate_helper_key_if_needed() {
    local current
    current="$(get_env HELPER_SESSION_KEY_CURRENT || true)"
    if [[ -z "$current" || "$current" == CHANGE_ME_* || "$current" == REPLACE_WITH_* ]]; then
        set_env HELPER_SESSION_KEY_CURRENT \
            "$("$VENV_DIR/bin/python" -c \
                'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    fi
}

run_migrations() {
    (
        cd "$INSTALL_DIR/app"
        sudo -u "$SERVICE_USER" env \
            MUSICBOT_ENV_FILE="$ENV_FILE" \
            MUSICBOT_DOTENV_OVERRIDE=false \
            PYTHONPATH="$INSTALL_DIR" \
            "$VENV_DIR/bin/alembic" upgrade head
    )
}

write_deployment_files() {
    render_unit > "$UNIT_FILE"
    chmod 0644 "$UNIT_FILE"
    render_control_script > "$CONTROL_FILE"
    chmod 0755 "$CONTROL_FILE"
    mkdir -p "$REGISTRY_ROOT"
    cat > "$REGISTRY_FILE" <<REGISTRY
INSTANCE_ID=${INSTANCE_ID}
INSTALL_DIR=${INSTALL_DIR}
UNIT_NAME=${UNIT_NAME}
SERVICE_USER=${SERVICE_USER}
DB_NAME=${DB_NAME}
DB_USER=${DB_USER}
HEALTH_PORT=${HEALTH_PORT}
METRICS_PORT=${METRICS_PORT}
REGISTRY
    chmod 0600 "$REGISTRY_FILE"
    systemctl daemon-reload
    systemctl enable "$UNIT_NAME" >/dev/null
}

service_env_valid() {
    local key value
    for key in INSTANCE_ID BOT_TOKEN API_ID API_HASH DEVELOPER_ID DATABASE_URL REDIS_URL; do
        value="$(get_env "$key" || true)"
        if [[ -z "$value" || "$value" == "0" || "$value" == *CHANGE_ME* || "$value" == *REPLACE_WITH* ]]; then
            warn "Service start blocked: configure $key in $ENV_FILE"
            return 1
        fi
    done
}

apply_permissions() {
    chown -R root:root "$INSTALL_DIR"
    chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
    chown root:"$SERVICE_USER" "$ENV_FILE"
    chmod 0700 "$SESSIONS_DIR" "$TEMP_DIR" "$BACKUP_DIR"
    chmod 0750 "$DOWNLOADS_DIR" "$LOGS_DIR" "$CACHE_DIR"
    chmod 0640 "$ENV_FILE"
}

install_packages
copy_release
install_venv
prepare_env_base
run_first_run_wizard
prepare_database
generate_helper_key_if_needed
apply_permissions
run_migrations
adopt_conflicting_units
write_deployment_files

if [[ "$START_SERVICE" == true ]] && service_env_valid; then
    systemctl restart "$UNIT_NAME"
    ok "Started/restarted only $UNIT_NAME"
else
    info "Installed and enabled $UNIT_NAME without starting it"
fi

cat <<SUMMARY

Instance:   ${INSTANCE_ID}
Path:       ${INSTALL_DIR}
Env:        ${ENV_FILE}
Service:    ${UNIT_NAME}
Database:   ${DB_NAME}
Redis ns:   musicbot:${INSTANCE_ID}:
Control:    ${CONTROL_FILE}

Commands:
  ${CONTROL_FILE} status
  ${CONTROL_FILE} restart
  ${CONTROL_FILE} logs
  ${CONTROL_FILE} stop
  ${CONTROL_FILE} migrate
  ${CONTROL_FILE} backup
  ${CONTROL_FILE} upgrade /path/to/new-release
  ${CONTROL_FILE} uninstall
SUMMARY
