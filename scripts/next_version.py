#!/usr/bin/env python3
"""Decide which version to build and whether a new release should be published.

Runs in the build workflow and prints ``key=value`` lines for ``$GITHUB_OUTPUT``:

    version  the version baked into the image (APP_VERSION), e.g. 2.6.1
    tag      the git tag for it, e.g. v2.6.1
    minor    major.minor, for the moving image tag, e.g. 2.6
    bump     patch | minor | major | none
    release  true when a new release (tag + GitHub release) should be created

Rules
-----
* Tag build (someone pushed vX.Y.Z): build exactly that; nothing new to release.
* Branch build: look at everything merged since the latest release tag.
    - a breaking change ("type!:" or a "BREAKING CHANGE:" footer)  -> major
    - "feat:"                                                      -> minor
    - "fix:" / "perf:"                                             -> patch
    - anything else (docs, ci, test, chore, refactor, ...)         -> no release;
      the image is built and reports the latest release's version
  A PR merged with "[skip release]" in its title is never released, and only the
  default branch (DEFAULT_BRANCH) ever releases: a manual run on a feature branch
  just builds.
* Release button: a manual run ("Run workflow" in the Actions tab) that picks
  patch, minor or major (FORCE_BUMP) releases that bump regardless of the commit
  titles — for a docs/chore merge you do want shipped, or a bigger bump than the
  titles imply.  It still needs something new since the last release, and it
  ignores "[skip release]" (you asked for it by hand).

Commit titles are the PR titles (GitHub puts a merge's PR title on the line after
"Merge pull request ..."), so this follows the conventional-commit style PRs use.

Only validated integers ever reach the output, so untrusted commit text cannot
inject anything into $GITHUB_OUTPUT.
"""
import os
import re
import subprocess
import sys

SEMVER_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
CONVENTIONAL = re.compile(r"^(?P<type>[A-Za-z]+)(?:\([^)]*\))?(?P<bang>!)?:\s*\S")
MERGE_SUBJECT = re.compile(r"^Merge (?:pull request|branch) ")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
SKIP_MARKER = re.compile(r"\[skip release\]", re.IGNORECASE)

MINOR_TYPES = {"feat"}
PATCH_TYPES = {"fix", "perf"}
LEVELS = {0: None, 1: "patch", 2: "minor", 3: "major"}

Version = tuple[int, int, int]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def parse_tag(tag: str) -> Version | None:
    m = SEMVER_TAG.match(tag)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def latest_release_tag() -> str | None:
    """The highest strict-semver release tag reachable from HEAD."""
    for tag in git("tag", "--merged", "HEAD", "--list", "v*", "--sort=-v:refname").split():
        if parse_tag(tag):
            return tag
    return None


def messages_since(tag: str | None) -> list[str]:
    out = git("log", "--format=%B%x00", f"{tag}..HEAD" if tag else "HEAD")
    return [m.strip() for m in out.split("\0") if m.strip()]


def candidate_titles(message: str) -> list[str]:
    """A commit's subject, plus the PR title GitHub places after a merge subject."""
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    if not lines:
        return []
    titles = [lines[0]]
    if MERGE_SUBJECT.match(lines[0]) and len(lines) > 1:
        titles.append(lines[1])
    return titles


def classify(messages: list[str]) -> str | None:
    """The strongest change level in ``messages``: major, minor, patch or None."""
    level = 0
    for message in messages:
        if BREAKING_FOOTER.search(message):
            level = 3
        for title in candidate_titles(message):
            m = CONVENTIONAL.match(title)
            if not m:
                continue
            kind = m.group("type").lower()
            if m.group("bang"):
                level = 3
            elif kind in MINOR_TYPES:
                level = max(level, 2)
            elif kind in PATCH_TYPES:
                level = max(level, 1)
    return LEVELS[level]


def bump(version: Version, kind: str) -> Version:
    major, minor, patch = version
    if kind == "major":
        return (major + 1, 0, 0)
    if kind == "minor":
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def first_free(version: Version, taken: set[str]) -> Version:
    """Never reuse a tag that already exists, even one not reachable from HEAD."""
    major, minor, patch = version
    while f"v{major}.{minor}.{patch}" in taken:
        patch += 1
    return (major, minor, patch)


def fallback_version() -> str:
    """Before any release exists: the default recorded in the source."""
    try:
        text = open("backend/app/routers/health.py").read()
    except OSError:
        return "0.0.0"
    m = re.search(r"\d+\.\d+\.\d+", text)
    return m.group(0) if m else "0.0.0"


def fmt(v: Version) -> str:
    return ".".join(map(str, v))


FORCEABLE = {"patch", "minor", "major"}


def forced_bump(env: dict[str, str]) -> str | None:
    """The bump chosen on a manual run, or None (also for any value not in the list)."""
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        return None
    choice = env.get("FORCE_BUMP", "").strip().lower()
    return choice if choice in FORCEABLE else None


def decide(env: dict[str, str]) -> dict[str, str]:
    if env.get("GITHUB_REF_TYPE") == "tag":
        tag = env.get("GITHUB_REF_NAME", "")
        version = tag[1:] if tag.startswith("v") else tag
        parsed = parse_tag(tag)
        minor = f"{parsed[0]}.{parsed[1]}" if parsed else version
        return {"version": version, "tag": tag, "minor": minor, "bump": "none", "release": "false"}

    tag = latest_release_tag()
    if tag is None:
        version = fallback_version()
        return {"version": version, "tag": f"v{version}", "minor": ".".join(version.split(".")[:2]),
                "bump": "none", "release": "false"}

    current = parse_tag(tag)
    head_message = git("log", "-1", "--format=%B").strip()
    default_branch = env.get("DEFAULT_BRANCH")
    on_default = not default_branch or env.get("GITHUB_REF_NAME") == default_branch
    kind = None
    forced = forced_bump(env)
    if on_default and forced:
        kind = forced if messages_since(tag) else None   # nothing new: nothing to release
    elif on_default and not SKIP_MARKER.search(head_message):
        kind = classify(messages_since(tag))
    if kind is None:
        return {"version": fmt(current), "tag": tag, "minor": f"{current[0]}.{current[1]}",
                "bump": "none", "release": "false"}

    taken = set(git("tag", "--list", "v*").split())
    new = first_free(bump(current, kind), taken)
    return {"version": fmt(new), "tag": f"v{fmt(new)}", "minor": f"{new[0]}.{new[1]}",
            "bump": kind, "release": "true"}


def main() -> int:
    result = decide(dict(os.environ))
    for key in ("version", "tag", "minor", "bump", "release"):
        print(f"{key}={result[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
