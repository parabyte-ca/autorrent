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
#   --version <semver>    Release to deploy (used by upgrade-agent), also to go
#                         back to an older one. A published-image deployment
#                         pins that exact image (AUTORRENT_VERSION in .env);
#                         without this it installs the newest release and pins it.
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

# An earlier run of git as another user (the old update script as root, a stray
# "sudo git pull") leaves files inside .git that the owner can no longer write:
#   error: cannot open '.git/FETCH_HEAD': Permission denied
# We are root here, so hand them back.
if [[ -n "$_GIT_USER" && -d "$REPO_DIR/.git" ]]; then
    if [[ -n "$(find "$REPO_DIR/.git" ! -user "$_GIT_USER" -print -quit 2>/dev/null)" ]]; then
        info "Restoring ownership of .git to $_GIT_USER (an earlier run of git as another user left files it cannot write) ..."
        chown -R "$_GIT_USER:" "$REPO_DIR/.git" || info "Could not change ownership — continuing; git may report a permission error."
    fi
fi

# A GitHub remote can be fetched anonymously over HTTPS if the repository is
# public — no SSH key needed.  Try that first, then the configured remote.
_origin_url="$(_git config --get remote.origin.url 2>/dev/null || true)"
HTTPS_URL=""
if [[ "$_origin_url" =~ ^(git@github\.com:|ssh://git@github\.com/)(.+)$ ]]; then
    HTTPS_URL="https://github.com/${BASH_REMATCH[2]}"
fi

# Git failed: say what to try instead of leaving a bare "exit 1".
git_failed() {
    die "git could not update $REPO_DIR (see the message above). If it mentions permissions, the git files may belong to another user — fix with: sudo chown -R \"\$(stat -c %U '$REPO_DIR')\": '$REPO_DIR/.git'"
}

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

# Does the compose file build the image from this checkout (an active build:
# section)?  Comment lines start with '#', so they don't match.
compose_builds_locally() {
    local cfg
    if cfg="$(docker compose -f "$COMPOSE_FILE" config 2>/dev/null)"; then
        grep -qE '^[[:space:]]+build:' <<<"$cfg"
    else
        grep -qE '^[[:space:]]+build:' "$COMPOSE_FILE"   # comment lines start with '#', so they don't match
    fi
}
# ── Version pinning (published-image deployments) ──────────────────────────────
# docker-compose.yml's image tag is ${AUTORRENT_VERSION:-latest}.  We pin it to the
# release we install, so the previous release's exact image is always known and a
# failed upgrade (or a bad release) can go back to it — "latest" cannot.

valid_semver() { [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; }

# APP_VERSION baked into an image / running container (the first arg of the
# docker command says which: "image inspect <ref>" or "inspect <container>").
_app_version() {
    sed -n 's/^APP_VERSION=//p' | head -1
}
image_version() {
    docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" 2>/dev/null | _app_version
}
running_version() {
    local cid
    cid="$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | head -1)"
    [[ -n "$cid" ]] || return 0
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$cid" 2>/dev/null | _app_version
}
pinned_version() {
    [[ -f "$ENV_FILE" ]] || return 0
    sed -n 's/^[[:space:]]*AUTORRENT_VERSION=//p' "$ENV_FILE" | tail -1 | tr -d "\"' "
}

# Forget a pin (so a stale one cannot hold the install back on an old release).
clear_pin() {
    [[ -f "$ENV_FILE" ]] || return 0
    local kept
    kept="$(grep -v '^[[:space:]]*AUTORRENT_VERSION=' "$ENV_FILE" || true)"
    if [[ -n "$kept" ]]; then printf '%s\n' "$kept" > "$ENV_FILE"; else : > "$ENV_FILE"; fi
}

# Record the version in .env (created if missing), keeping the file's owner/mode.
write_pin() {
    local version="$1" kept
    kept="$(grep -v '^[[:space:]]*AUTORRENT_VERSION=' "$ENV_FILE" 2>/dev/null || true)"
    if [[ -f "$ENV_FILE" ]]; then
        { [[ -n "$kept" ]] && printf '%s\n' "$kept"; printf 'AUTORRENT_VERSION=%s\n' "$version"; } > "$ENV_FILE"
    else
        printf 'AUTORRENT_VERSION=%s\n' "$version" > "$ENV_FILE"
        [[ -n "$_GIT_USER" ]] && chown "$_GIT_USER:" "$ENV_FILE" 2>/dev/null || true
    fi
    export AUTORRENT_VERSION="$version"
}

# ── Rollback shortcut ──────────────────────────────────────────────────────────

if [[ -n "$OPT_ROLLBACK_REV" ]]; then
    info "Rolling back to $OPT_ROLLBACK_REV ..."
    _git checkout "$OPT_ROLLBACK_REV" -- .
    OPT_NO_GIT=true
    OPT_SKIP_PREFLIGHT=true
fi

# ── Git pull ───────────────────────────────────────────────────────────────────

# Only a deployment that builds from this checkout needs the checkout to match
# the release being installed.  One that runs the published image has no reason
# to sit on a release tag — and doing so rewinds these very scripts to the older
# release's copy (including any fix to them made since), so it stays on the branch.
LOCAL_BUILD=false
if [[ "$OPT_PULL" != true ]] && compose_builds_locally; then
    LOCAL_BUILD=true
fi

if [[ "$OPT_NO_GIT" == false ]]; then
    if [[ "$LOCAL_BUILD" == false ]] && ! _git symbolic-ref -q HEAD >/dev/null 2>&1; then
        info "Checkout is parked on an old release tag — returning to $BRANCH so the update scripts stay current ..."
        _git checkout "$BRANCH"
    fi
    info "Pulling latest code (branch: $BRANCH) ..."
    git_pull_branch || git_failed
fi

if [[ -n "$OPT_VERSION" ]]; then
    if [[ "$LOCAL_BUILD" == true ]]; then
        info "Checking out tag/version: $OPT_VERSION ..."
        git_fetch_tags || git_failed
        # Try v-prefixed tag first, then bare version.
        _git checkout "v${OPT_VERSION}" 2>/dev/null || _git checkout "$OPT_VERSION"
    else
        info "Deploying the published image — staying on $BRANCH so the update scripts stay current."
    fi
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
if [[ "$OPT_PULL" != true ]] && ! compose_builds_locally; then
    info "docker-compose.yml uses a published image (no build: section) — pulling instead of building."
    OPT_PULL=true
fi

# Published image + a compose file that takes AUTORRENT_VERSION: install (and pin)
# an exact release.  Anything else keeps the old behaviour (pull "latest" / build).
PINNING=false
PREV_VERSION=""
if [[ "$OPT_PULL" == true ]] && grep -q 'AUTORRENT_VERSION' "$COMPOSE_FILE" 2>/dev/null; then
    PINNING=true
    OPT_VERSION="${OPT_VERSION#v}"
    if [[ -n "$OPT_VERSION" ]] && ! valid_semver "$OPT_VERSION"; then
        die "Invalid version '$OPT_VERSION' (expected X.Y.Z)."
    fi
    PREV_VERSION="$(running_version || true)"
    valid_semver "$PREV_VERSION" || PREV_VERSION="$(pinned_version || true)"
    valid_semver "$PREV_VERSION" || PREV_VERSION=""
fi

if [[ "$PINNING" == true ]]; then
    export AUTORRENT_VERSION="${OPT_VERSION:-latest}"
    info "Pulling Docker image (${AUTORRENT_VERSION}) ..."
    docker compose -f "$COMPOSE_FILE" pull
    if [[ -z "$OPT_VERSION" ]]; then
        # Newest release: pin to the version the pulled image says it is.
        _ref="$(docker compose -f "$COMPOSE_FILE" config --images 2>/dev/null | head -1)"
        _got="$(image_version "$_ref" || true)"
        if valid_semver "$_got"; then
            OPT_VERSION="$_got"
        else
            info "Could not read the version of the pulled image — leaving the image tag on 'latest' (no rollback pin)."
            clear_pin
        fi
    fi
    if [[ -n "$OPT_VERSION" ]]; then
        write_pin "$OPT_VERSION"
        info "Pinned image version to $OPT_VERSION${PREV_VERSION:+ (previous: $PREV_VERSION)}."
    fi
elif [[ "$OPT_PULL" == true ]]; then
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
HEALTH_TRIES="${HEALTH_TRIES:-30}"
TRIES=0
until docker compose -f "$COMPOSE_FILE" ps | grep -q "healthy" || [[ $TRIES -ge $HEALTH_TRIES ]]; do
    sleep 2
    TRIES=$((TRIES + 1))
done

if [[ $TRIES -ge $HEALTH_TRIES ]]; then
    info "Container did not become healthy in time."
    if [[ "$PINNING" == true && -n "$PREV_VERSION" ]]; then
        info "Rolling back to the previous release ($PREV_VERSION) ..."
        write_pin "$PREV_VERSION"
        docker compose -f "$COMPOSE_FILE" pull || true
        docker compose -f "$COMPOSE_FILE" up -d --remove-orphans || true
        info "Rollback complete — running $PREV_VERSION. Remove AUTORRENT_VERSION from .env to follow 'latest' again."
    elif [[ -n "$ROLLBACK_REV" ]]; then
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
