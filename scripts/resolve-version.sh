#!/usr/bin/env bash
# Prints the version to bake into the Docker image (APP_VERSION).
#
#   tag build      -> the tag, without the leading "v"
#   branch build   -> the highest strict-semver release tag reachable from HEAD,
#                     so a build of main after a release reports that release
#                     instead of a stale default hard-coded in the source
#   no release tag -> the default in backend/app/routers/health.py
#
# Needs the tags to be present (the workflow checks out with fetch-depth: 0).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ "${GITHUB_REF_TYPE:-}" == "tag" ]]; then
    echo "${GITHUB_REF_NAME#v}"
    exit 0
fi

tag="$(git tag --merged HEAD --list 'v*' --sort=-v:refname 2>/dev/null \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1 || true)"
if [[ -n "$tag" ]]; then
    echo "${tag#v}"
    exit 0
fi

python3 - <<'PY'
import re
m = re.search(r"[0-9]+\.[0-9]+\.[0-9]+", open("backend/app/routers/health.py").read())
print(m.group(0) if m else "0.0.0")
PY
