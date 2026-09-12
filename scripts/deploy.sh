#!/usr/bin/env bash
# Compatibility wrapper for instance-scoped upgrades.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "${ROOT}/setup_server.sh" --upgrade --source "$ROOT" "$@"
