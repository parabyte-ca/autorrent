#!/usr/bin/env bash
# upgrade-agent.sh — AutoRrent host-side upgrade helper.
# Runs as a cron job on the host (as root).  Writes a heartbeat to agent.json
# so the app can confirm the helper is connected, and processes upgrade
# requests written by the app into the shared upgrade folder.
#
# Usage:
#   sudo bash upgrade-agent.sh [--install | --uninstall | --print]
#
# Setup:
#   1.  Mount the same upgrade folder into the container:
#         volumes:
#           - /opt/autorrent/upgrade:/upgrade
#       Set APP_UPGRADE_FOLDER=/upgrade in the container environment.
#   2.  Create /opt/autorrent/upgrade  (or wherever you mount it).
#   3.  Run  sudo bash upgrade-agent.sh --install
#       This installs a cron entry that fires every 5 minutes.
#   4.  Verify with  sudo bash upgrade-agent.sh --print

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$REPO_DIR/.env"

EVERY_MINUTES=5   # How often cron fires this script (must match cron entry)

# ── Helpers ────────────────────────────────────────────────────────────────────

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# ── Root check ─────────────────────────────────────────────────────────────────

[[ $EUID -eq 0 ]] || die "This script must be run as root (sudo)."

# ── Subcommands ────────────────────────────────────────────────────────────────

_install() {
    local cron_line="*/$EVERY_MINUTES * * * * root bash $SCRIPT_DIR/upgrade-agent.sh >> /var/log/upgrade-agent.log 2>&1"
    local cron_file="/etc/cron.d/autorrent-upgrade-agent"
    printf '%s\n' "$cron_line" > "$cron_file"
    chmod 644 "$cron_file"
    echo "Installed cron job: $cron_file"
    echo "  Schedule: every $EVERY_MINUTES minutes"
    echo "  Log:      /var/log/upgrade-agent.log"
    echo ""
    echo "Make sure the upgrade folder is mounted at the same path in the container."
}

_uninstall() {
    local cron_file="/etc/cron.d/autorrent-upgrade-agent"
    if [[ -f "$cron_file" ]]; then
        rm -f "$cron_file"
        echo "Removed $cron_file"
    else
        echo "No cron job found at $cron_file (nothing to remove)."
    fi
}

_print() {
    local cron_file="/etc/cron.d/autorrent-upgrade-agent"
    if [[ -f "$cron_file" ]]; then
        echo "=== $cron_file ==="
        cat "$cron_file"
    else
        echo "No cron job installed ($cron_file not found)."
    fi
}

if [[ "${1:-}" == "--install" ]];   then _install;   exit 0; fi
if [[ "${1:-}" == "--uninstall" ]]; then _uninstall; exit 0; fi
if [[ "${1:-}" == "--print" ]];     then _print;     exit 0; fi
if [[ -n "${1:-}" ]]; then
    die "Unknown argument: ${1}. Valid flags: --install, --uninstall, --print"
fi

# ── Regular run ────────────────────────────────────────────────────────────────

# Load .env if present (optional — UPGRADE_FOLDER defaults to $REPO_DIR/upgrade).
if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        key="${key// /}"
        value="${value%%#*}"          # strip inline comments
        value="${value#\"}"           # strip leading "
        value="${value%\"}"           # strip trailing "
        value="${value#\'}"
        value="${value%\'}"
        [[ -n "$key" ]] && declare "$key"="$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

UPGRADE_FOLDER="${APP_UPGRADE_FOLDER:-$REPO_DIR/upgrade}"

# Allow extra args to update.sh but ONLY --pull and --skip-preflight.
EXTRA_ARGS=""
if [[ -n "${APP_UPGRADE_ARGS:-}" ]]; then
    for arg in $APP_UPGRADE_ARGS; do
        if [[ "$arg" == "--pull" || "$arg" == "--skip-preflight" ]]; then
            EXTRA_ARGS="$EXTRA_ARGS $arg"
        fi
    done
fi

# Take an exclusive lock on the upgrade folder so only one copy runs at a time.
LOCK_FILE="$UPGRADE_FOLDER/.lock"
mkdir -p "$UPGRADE_FOLDER"
exec 9>"$LOCK_FILE"
flock -n 9 || { info "Another instance is running — exiting."; exit 0; }

# ── Write heartbeat ────────────────────────────────────────────────────────────

AGENT_FILE="$UPGRADE_FOLDER/agent.json"
AGENT_TMP="$AGENT_FILE.tmp.$$"
printf '{"last_seen":"%s","every_minutes":%d}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$EVERY_MINUTES" > "$AGENT_TMP"
mv -f "$AGENT_TMP" "$AGENT_FILE"

# ── Check for an upgrade request ──────────────────────────────────────────────

REQUEST_FILE="$UPGRADE_FOLDER/request.json"
STATUS_FILE="$UPGRADE_FOLDER/status.json"
LOG_FILE="$UPGRADE_FOLDER/upgrade.log"

if [[ ! -f "$REQUEST_FILE" ]]; then
    exit 0   # nothing to do
fi

# Reject symlinks to prevent path-traversal attacks.
if [[ -L "$REQUEST_FILE" ]]; then
    info "request.json is a symlink — ignoring."
    rm -f "$REQUEST_FILE"
    exit 0
fi

# ── Read & validate request fields (no jq dependency) ─────────────────────────

# Use sed to extract JSON string values; safer than sourcing the file.
_json_str() {
    local field="$1" file="$2"
    sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -1
}

REQ_ID="$(_json_str id "$REQUEST_FILE")"
REQ_VERSION="$(_json_str version "$REQUEST_FILE")"
REQ_BY="$(_json_str requested_by "$REQUEST_FILE")"

# Consume the request immediately so the helper never double-processes it.
rm -f "$REQUEST_FILE"

# Validate id: 36 characters of hex digits and hyphens (UUID v4 shape).
if ! echo "$REQ_ID" | grep -qE '^[0-9a-f-]{36}$'; then
    info "Invalid request id ($REQ_ID) — ignoring."
    exit 0
fi

# Validate version: strict semver v?X.Y.Z
if ! echo "$REQ_VERSION" | grep -qE '^v?[0-9]+\.[0-9]+\.[0-9]+$'; then
    info "Invalid version ($REQ_VERSION) — ignoring."
    exit 0
fi

# Validate requested_by: printable ASCII chars excluding control chars and shell meta.
if ! echo "$REQ_BY" | grep -qE '^[[:print:]]{1,200}$'; then
    info "Invalid requested_by — ignoring."
    exit 0
fi

# ── Run the upgrade ────────────────────────────────────────────────────────────

info "Processing upgrade request id=$REQ_ID version=$REQ_VERSION by=$REQ_BY"

# Write "running" status.
STATUS_TMP="$STATUS_FILE.tmp.$$"
printf '{"state":"running","started_at":"%s","id":"%s","version":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$REQ_ID" "$REQ_VERSION" > "$STATUS_TMP"
mv -f "$STATUS_TMP" "$STATUS_FILE"

# Create a fresh log file.
: > "$LOG_FILE"

# Run update.sh from the repo root.
UPDATE_SH="$REPO_DIR/update.sh"
if [[ ! -f "$UPDATE_SH" ]]; then
    info "update.sh not found at $UPDATE_SH"
    printf '{"state":"failed","message":"update.sh not found","finished_at":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$STATUS_TMP"
    mv -f "$STATUS_TMP" "$STATUS_FILE"
    exit 0
fi

set +e
bash "$UPDATE_SH" --version "$REQ_VERSION" $EXTRA_ARGS >> "$LOG_FILE" 2>&1
UPDATE_EXIT=$?
set -e

FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [[ $UPDATE_EXIT -eq 0 ]]; then
    info "Upgrade to $REQ_VERSION succeeded."
    printf '{"state":"succeeded","finished_at":"%s","id":"%s","version":"%s"}\n' \
        "$FINISHED_AT" "$REQ_ID" "$REQ_VERSION" > "$STATUS_TMP"
else
    info "Upgrade to $REQ_VERSION FAILED (exit $UPDATE_EXIT)."
    printf '{"state":"failed","message":"update.sh exited %d","finished_at":"%s","id":"%s","version":"%s"}\n' \
        "$UPDATE_EXIT" "$FINISHED_AT" "$REQ_ID" "$REQ_VERSION" > "$STATUS_TMP"
fi

mv -f "$STATUS_TMP" "$STATUS_FILE"
