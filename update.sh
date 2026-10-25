#!/usr/bin/env bash
# update.sh — Pull latest changes and redeploy AutoRrent.
#
# Usage (manual):
#   sudo bash update.sh
#
# Usage (from upgrade-agent.sh):
#   bash update.sh --version 2.5.0 [--pull] [--skip-preflight]
#
# Options:
#   --version <semver>    Tag/version to deploy (used by upgrade-agent).
#                         Without this the script deploys whatever HEAD resolves to
#                         after the git pull.
#   --pull                Do a docker pull (for registry-based deployments) instead
#                         of a local build.  Chosen automatically when the compose
#                         file has no active build: section.
#   --skip-preflight      Skip the DB migration pre-flight check.
#   --no-git              Skip the git pull step (useful if source is already current).
#   --rollback-rev <rev>  Check out <rev> and redeploy (rollback).

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"

# ── Defaults (docker-compose based deployment) ─────────────────────────────────

COMPOSE_FILE="$REPO_DIR/docker-compose.yml"
BRANCH="main"

# Options
OPT_VERSION=""
OPT_PULL=false
OPT_SKIP_PREFLIGHT=false
OPT_NO_GIT=false
OPT_ROLLBACK_REV=""

# ── Argument parsing ───────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)         OPT_VERSION="${2:-}"; shift 2 ;;
        --pull)            OPT_PULL=true; shift ;;
        --skip-preflight)  OPT_SKIP_PREFLIGHT=true; shift ;;
        --no-git)          OPT_NO_GIT=true; shift ;;
        --rollback-rev)    OPT_ROLLBACK_REV="${2:-}"; shift 2 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

# ── Helpers ────────────────────────────────────────────────────────────────────

info() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ==> $*"; }
die()  { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) ERROR: $*" >&2; exit 1; }

cd "$REPO_DIR"

# ── Git access ─────────────────────────────────────────────────────────────────
# When run as root, git must run as the user who owns the checkout:
#   * git refuses to touch a repository owned by someone else as root
#     ("detected dubious ownership"), and
#   * root has none of that user's SSH keys.
# This matters most when the upgrade helper runs us from cron, where there is
# no SUDO_USER and no login name to fall back on.
_GIT_USER=""
if [[ "$(id -u)" -eq 0 ]]; then
    _owner="$(stat -c %U "$REPO_DIR" 2>/dev/null || true)"
    if [[ -n "$_owner" && "$_owner" != "root" && "$_owner" != "UNKNOWN" ]]; then
        _GIT_USER="$_owner"
    elif [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
        _GIT_USER="$SUDO_USER"
    fi
    if [[ -n "$_GIT_USER" ]] && ! command -v sudo &>/dev/null; then
        die "sudo is required to run git as $_GIT_USER (the owner of $REPO_DIR)."
    fi
fi

# GIT_TERMINAL_PROMPT=0 makes git fail fast instead of waiting for a password
# nobody can type (cron, the upgrade helper).
_git() {
    if [[ -n "$_GIT_USER" ]]; then
        sudo -H -u "$_GIT_USER" env GIT_TERMINAL_PROMPT=0 git -C "$REPO_DIR" "$@"
    else
        GIT_TERMINAL_PROMPT=0 git -C "$REPO_DIR" "$@"
    fi
}

# A GitHub remote can be fetched anonymously over HTTPS if the repository is
# public — no SSH key needed.  Try that first, then the configured remote.
_origin_url="$(_git config --get remote.origin.url 2>/dev/null || true)"
HTTPS_URL=""
if [[ "$_origin_url" =~ ^(git@github\.com:|ssh://git@github\.com/)(.+)$ ]]; then
    HTTPS_URL="https://github.com/${BASH_REMATCH[2]}"
fi

git_pull_branch() {
    if [[ -n "$HTTPS_URL" ]]; then
        _git pull "$HTTPS_URL" "$BRANCH" && return 0
        info "Anonymous HTTPS fetch failed — trying the configured remote ..."
    fi
    _git pull origin "$BRANCH"
}

git_fetch_tags() {
    if [[ -n "$HTTPS_URL" ]]; then
        _git fetch --tags "$HTTPS_URL" && return 0
        info "Anonymous HTTPS fetch failed — trying the configured remote ..."
    fi
    _git fetch --tags origin
}

# ── Load .env for any local overrides ──────────────────────────────────────────

if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        key="${key// /}"
        value="${value%%#*}"
        value="${value#\"}"
        value="${value%\"}"
        value="${value#\'}"
        value="${value%\'}"
        [[ -n "$key" ]] && declare "$key"="$value" 2>/dev/null || true
    done < "$ENV_FILE"
fi

# ── Rollback shortcut ──────────────────────────────────────────────────────────

if [[ -n "$OPT_ROLLBACK_REV" ]]; then
    info "Rolling back to $OPT_ROLLBACK_REV ..."
    _git checkout "$OPT_ROLLBACK_REV" -- .
    OPT_NO_GIT=true
    OPT_SKIP_PREFLIGHT=true
fi

# ── Git pull ───────────────────────────────────────────────────────────────────

if [[ "$OPT_NO_GIT" == false ]]; then
    info "Pulling latest code (branch: $BRANCH) ..."
    git_pull_branch
fi

if [[ -n "$OPT_VERSION" ]]; then
    info "Checking out tag/version: $OPT_VERSION ..."
    git_fetch_tags
    # Try v-prefixed tag first, then bare version.
    _git checkout "v${OPT_VERSION}" 2>/dev/null || _git checkout "$OPT_VERSION"
fi

# ── Pre-flight DB migration test ───────────────────────────────────────────────

if [[ "$OPT_SKIP_PREFLIGHT" == false ]]; then
    info "Running pre-flight migration test ..."
    PREFLIGHT_DB="$(mktemp /tmp/autorrent-preflight-XXXXXXXX.db)"
    trap 'rm -f "$PREFLIGHT_DB"' EXIT

    if command -v python3 &>/dev/null; then
        python3 - "$PREFLIGHT_DB" <<'PYEOF'
import sys, sqlite3, pathlib

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
cur = conn.cursor()

stmts = [
    "CREATE TABLE IF NOT EXISTS watchlist (id INTEGER PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS downloads (id INTEGER PRIMARY KEY)",
    "ALTER TABLE watchlist ADD COLUMN codec TEXT DEFAULT 'x265'",
    "ALTER TABLE watchlist ADD COLUMN show_status TEXT",
    "ALTER TABLE watchlist ADD COLUMN show_status_checked_at DATETIME",
    "ALTER TABLE watchlist ADD COLUMN tvmaze_id INTEGER",
    "ALTER TABLE watchlist ADD COLUMN show_status_override INTEGER DEFAULT 0",
    "ALTER TABLE downloads ADD COLUMN completion_first_seen_at DATETIME",
    "ALTER TABLE downloads ADD COLUMN qbit_removed INTEGER DEFAULT 0",
    "ALTER TABLE watchlist ADD COLUMN max_size_mb INTEGER",
    "ALTER TABLE watchlist ADD COLUMN preferred_groups TEXT",
    "ALTER TABLE watchlist ADD COLUMN prefer_season_packs INTEGER DEFAULT 0",
]

for s in stmts:
    try:
        cur.execute(s)
    except Exception:
        pass  # column already exists — expected

conn.commit()
conn.close()
print("Pre-flight migration OK")
PYEOF
    else
        info "python3 not found — skipping migration pre-flight."
    fi
fi

# ── Capture rollback rev before building ──────────────────────────────────────

ROLLBACK_REV="$(_git rev-parse HEAD~1 2>/dev/null || true)"

# ── Build or pull ──────────────────────────────────────────────────────────────

# A deployment that runs the published image (no active build: section in the
# compose file) can only be updated by pulling; "building" would silently do
# nothing and the old image would keep running.  Decided after the git step so
# it reflects the compose file of the version being deployed.
compose_builds_locally() {
    local cfg
    if cfg="$(docker compose -f "$COMPOSE_FILE" config 2>/dev/null)"; then
        grep -qE '^[[:space:]]+build:' <<<"$cfg"
    else
        grep -qE '^[[:space:]]+build:' "$COMPOSE_FILE"   # comment lines start with '#', so they don't match
    fi
}
if [[ "$OPT_PULL" != true ]] && ! compose_builds_locally; then
    info "docker-compose.yml uses a published image (no build: section) — pulling instead of building."
    OPT_PULL=true
fi

if [[ "$OPT_PULL" == true ]]; then
    info "Pulling latest Docker image ..."
    docker compose -f "$COMPOSE_FILE" pull
else
    info "Building Docker image ..."
    docker compose -f "$COMPOSE_FILE" build
fi

# ── Deploy ─────────────────────────────────────────────────────────────────────

info "Restarting container ..."
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

info "Waiting for health check ..."
TRIES=0
until docker compose -f "$COMPOSE_FILE" ps | grep -q "healthy" || [[ $TRIES -ge 30 ]]; do
    sleep 2
    TRIES=$((TRIES + 1))
done

if [[ $TRIES -ge 30 ]]; then
    info "Container did not become healthy in time."
    if [[ -n "$ROLLBACK_REV" ]]; then
        info "Attempting automatic rollback to $ROLLBACK_REV ..."
        _git checkout "$ROLLBACK_REV" -- .
        if [[ "$OPT_PULL" == true ]]; then
            docker compose -f "$COMPOSE_FILE" pull || true
        else
            docker compose -f "$COMPOSE_FILE" build || true
        fi
        docker compose -f "$COMPOSE_FILE" up -d --remove-orphans || true
        info "Rollback complete."
    fi
    die "Upgrade failed — container health check timed out."
fi

info "Deployment complete."
docker compose -f "$COMPOSE_FILE" ps
