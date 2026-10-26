"""Tests for scripts/next_version.py — which version a build gets and whether it releases.

Real git repositories are used because what matters is what git reports about
tags, reachability and commit messages.
"""
import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "next_version.py"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env={**os.environ, **_GIT_ENV}).stdout.strip()


_n = 0


def commit(repo, message):
    global _n
    _n += 1
    (repo / f"f{_n}.txt").write_text(str(_n))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture()
def repo(tmp_path):
    """A repo whose source default version is 2.4.0, tagged v2.6.0 at its first commit."""
    r = tmp_path / "repo"
    (r / "backend" / "app" / "routers").mkdir(parents=True)
    (r / "backend" / "app" / "routers" / "health.py").write_text('v = os.environ.get("APP_VERSION", "2.4.0")\n')
    git(r, "init", "-q")
    git(r, "symbolic-ref", "HEAD", "refs/heads/main")
    commit(r, "initial")
    git(r, "tag", "v2.6.0")
    return r


def decide(repo, **env):
    proc = subprocess.run(
        ["python3", str(SCRIPT)], capture_output=True, text=True, cwd=repo,
        env={**os.environ, **_GIT_ENV, "GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main", **env},
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert [ln.split("=", 1)[0] for ln in lines] == ["version", "tag", "minor", "bump", "release"], proc.stdout
    return dict(ln.split("=", 1) for ln in lines)


def after_release(repo, *messages, **env):
    for m in messages:
        commit(repo, m)
    return decide(repo, **env)


# ── what each kind of change releases ─────────────────────────────────────────


@pytest.mark.parametrize("messages,bump,version", [
    (["fix: a bug"], "patch", "2.6.1"),
    (["perf: faster scan"], "patch", "2.6.1"),
    (["feat: a feature"], "minor", "2.7.0"),
    (["fix: a", "feat: b", "fix: c"], "minor", "2.7.0"),          # strongest change wins
    (["feat!: drop an API"], "major", "3.0.0"),
    (["fix!: incompatible fix"], "major", "3.0.0"),
    (["feat: x\n\nBREAKING CHANGE: the config format changed"], "major", "3.0.0"),
    (["fix: x\n\nBREAKING-CHANGE: also accepted"], "major", "3.0.0"),
    (["fix(ui): scoped"], "patch", "2.6.1"),
    (["feat(api)!: scoped and breaking"], "major", "3.0.0"),
    (["Fix: capitalised"], "patch", "2.6.1"),
    (["FEAT: shouting"], "minor", "2.7.0"),
    (["fix:  extra spaces after the colon"], "patch", "2.6.1"),
    (["fix: posters missing after a rate limit (#52)"], "patch", "2.6.1"),   # squash-merge style
])
def test_changes_that_release(repo, messages, bump, version):
    out = after_release(repo, *messages)
    assert (out["bump"], out["version"], out["release"]) == (bump, version, "true")
    assert out["tag"] == f"v{version}" and out["minor"] == ".".join(version.split(".")[:2])


@pytest.mark.parametrize("message", [
    "docs: update the readme", "ci: tweak the workflow", "test: add coverage", "chore: bump deps",
    "refactor: tidy", "style: format", "build: update the Dockerfile",
    "update some stuff", "fixes a bug", "fixed: wrong type word", "feature: not the feat type",
    "fix", "fix:", "Merge branch 'main' into something",
])
def test_changes_that_do_not_release(repo, message):
    out = after_release(repo, message)
    assert (out["bump"], out["release"]) == ("none", "false")
    assert out["version"] == "2.6.0" and out["tag"] == "v2.6.0"      # still reports the latest release


def test_nothing_new_since_the_release(repo):
    out = decide(repo)
    assert (out["bump"], out["release"], out["version"]) == ("none", "false", "2.6.0")


# ── PR titles from GitHub merge commits ───────────────────────────────────────


def test_pr_title_after_a_merge_subject_counts(repo):
    commit(repo, "Merge pull request #5 from me/branch\n\nfeat: Trakt sync")
    assert decide(repo)["bump"] == "minor"


def test_a_real_merge_commit_uses_the_pr_title_even_if_branch_commits_are_plain(repo):
    git(repo, "checkout", "-q", "-b", "work")
    commit(repo, "wip")
    commit(repo, "more wip")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "work", "-m", "Merge pull request #9 from me/work", "-m", "fix: tidy up")
    out = decide(repo)
    assert (out["bump"], out["version"]) == ("patch", "2.6.1")


def test_merge_subject_alone_is_not_a_conventional_title(repo):
    commit(repo, "Merge pull request #5 from me/feat-something")     # the branch name must not count
    assert decide(repo)["bump"] == "none"


# ── which commits are considered ──────────────────────────────────────────────


def test_only_commits_after_the_latest_release_count(repo):
    commit(repo, "feat: shipped in 2.6.0")
    git(repo, "tag", "-f", "v2.6.0")
    out = after_release(repo, "docs: only docs since the release")
    assert out["release"] == "false"


def test_the_highest_reachable_release_tag_is_the_base(repo):
    for tag in ("v2.9.0", "v2.10.0", "v2.2.0"):
        commit(repo, f"release {tag}")
        git(repo, "tag", tag)
    out = after_release(repo, "fix: patch on top")
    assert out["version"] == "2.10.1"                  # numeric, not alphabetical


def test_tags_that_are_not_releases_are_ignored(repo):
    for junk in ("v2.7.0-rc1", "v3", "v2.8", "latest", "release-9.9.9", "v1.2.3.4"):
        commit(repo, f"c {junk}")
        git(repo, "tag", junk)
    out = after_release(repo, "fix: x")
    assert out["version"] == "2.6.1"


def test_an_existing_tag_is_never_reused(repo):
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "elsewhere")
    git(repo, "tag", "v2.6.1")                         # exists, but not reachable from main
    git(repo, "tag", "v2.6.2")
    git(repo, "checkout", "-q", "main")
    out = after_release(repo, "fix: x")
    assert out["version"] == "2.6.3"


def test_unreachable_higher_tag_does_not_become_the_base(repo):
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "elsewhere")
    git(repo, "tag", "v9.9.9")
    git(repo, "checkout", "-q", "main")
    assert after_release(repo, "fix: x")["version"] == "2.6.1"


def test_before_any_release_exists_the_source_default_is_reported_and_nothing_releases(tmp_path):
    r = tmp_path / "fresh"
    (r / "backend" / "app" / "routers").mkdir(parents=True)
    (r / "backend" / "app" / "routers" / "health.py").write_text('"2.4.0"\n')
    git(r, "init", "-q")
    git(r, "symbolic-ref", "HEAD", "refs/heads/main")
    commit(r, "feat: first")
    out = decide(r)
    assert (out["version"], out["release"], out["bump"]) == ("2.4.0", "false", "none")


# ── opting out and where a release may come from ──────────────────────────────


def test_skip_release_in_the_merged_title_suppresses_the_release(repo):
    commit(repo, "fix: an earlier real fix")
    commit(repo, "Merge pull request #7 from me/x\n\nfix: small thing [skip release]")
    out = decide(repo)
    assert out["release"] == "false" and out["version"] == "2.6.0"


def test_skip_release_in_an_older_commit_does_not_suppress_a_later_release(repo):
    commit(repo, "docs: not releasing [skip release]")
    out = after_release(repo, "fix: this one should release")
    assert out["release"] == "true"


def test_only_the_default_branch_releases(repo):
    commit(repo, "fix: on a feature branch")
    out = decide(repo, GITHUB_REF_NAME="feature", DEFAULT_BRANCH="main")
    assert (out["release"], out["version"]) == ("false", "2.6.0")
    out = decide(repo, GITHUB_REF_NAME="main", DEFAULT_BRANCH="main")
    assert out["release"] == "true"


def test_without_a_default_branch_setting_there_is_no_restriction(repo):
    commit(repo, "fix: x")
    assert decide(repo, GITHUB_REF_NAME="anything")["release"] == "true"


# ── tag builds ────────────────────────────────────────────────────────────────


def test_a_tag_build_uses_exactly_that_version_and_never_releases_again(repo):
    commit(repo, "feat: would otherwise bump")
    out = decide(repo, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v2.7.1")
    assert out == {"version": "2.7.1", "tag": "v2.7.1", "minor": "2.7", "bump": "none", "release": "false"}


def test_a_prerelease_tag_build_does_not_crash(repo):
    out = decide(repo, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v2.7.0-rc1")
    assert (out["version"], out["tag"], out["release"]) == ("2.7.0-rc1", "v2.7.0-rc1", "false")
    assert out["minor"]                                  # never empty: it feeds a Docker tag


# ── output safety ─────────────────────────────────────────────────────────────


def test_hostile_commit_text_cannot_inject_outputs(repo):
    evil = "fix: x\n\nversion=9.9.9\nrelease=true\nbump=major\n::set-output name=version::6.6.6"
    out = after_release(repo, evil)
    assert (out["version"], out["bump"]) == ("2.6.1", "patch")      # decided by the rules, not the text


def test_every_value_is_a_plain_token(repo):
    out = after_release(repo, "feat: x")
    assert all(re.fullmatch(r"[0-9A-Za-z.\-]+", v) for v in out.values()), out


def test_the_script_never_modifies_the_repository(repo):
    commit(repo, "fix: x")
    before = (git(repo, "tag"), git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain"))
    decide(repo)
    decide(repo)
    assert (git(repo, "tag"), git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")) == before


def test_the_real_repository_history_runs():
    proc = subprocess.run(["python3", str(SCRIPT)], capture_output=True, text=True, cwd=ROOT,
                          env={**os.environ, "GITHUB_REF_TYPE": "branch"})
    assert proc.returncode == 0, proc.stderr
    assert re.search(r"^version=\d+\.\d+\.\d+$", proc.stdout, re.M)


# ── the release button (manual run with a chosen bump) ────────────────────────

BUTTON = {"GITHUB_EVENT_NAME": "workflow_dispatch"}


class TestReleaseButton:
    @pytest.mark.parametrize("choice,version", [("patch", "2.6.1"), ("minor", "2.7.0"), ("major", "3.0.0")])
    def test_the_chosen_bump_releases_even_for_a_docs_merge(self, repo, choice, version):
        r = after_release(repo, "docs: reword the readme", **BUTTON, FORCE_BUMP=choice)
        assert (r["release"], r["bump"], r["version"], r["tag"]) == ("true", choice, version, f"v{version}")

    def test_the_chosen_bump_wins_over_what_the_titles_imply(self, repo):
        r = after_release(repo, "feat: big thing", **BUTTON, FORCE_BUMP="patch")
        assert (r["bump"], r["version"]) == ("patch", "2.6.1")

    def test_none_leaves_the_normal_rules_alone(self, repo):
        assert after_release(repo, "docs: x", **BUTTON, FORCE_BUMP="none")["release"] == "false"
        assert decide(repo, **BUTTON, FORCE_BUMP="none")["release"] == "false"
        commit(repo, "fix: y")
        assert decide(repo, **BUTTON, FORCE_BUMP="none")["version"] == "2.6.1"

    def test_nothing_new_since_the_last_release_means_nothing_to_release(self, repo):
        r = decide(repo, **BUTTON, FORCE_BUMP="minor")
        assert (r["release"], r["version"]) == ("false", "2.6.0")

    def test_it_ignores_the_skip_marker_because_you_asked_by_hand(self, repo):
        r = after_release(repo, "chore: tidy [skip release]", **BUTTON, FORCE_BUMP="patch")
        assert r["release"] == "true" and r["version"] == "2.6.1"

    def test_only_the_default_branch_can_be_released_this_way(self, repo):
        r = after_release(repo, "fix: x", **BUTTON, FORCE_BUMP="patch",
                          GITHUB_REF_NAME="feature", DEFAULT_BRANCH="main")
        assert r["release"] == "false"

    def test_a_forced_bump_still_never_reuses_a_tag(self, repo):
        git(repo, "tag", "v2.6.1")          # exists but is not on this history
        commit(repo, "docs: y")
        r = decide(repo, **BUTTON, FORCE_BUMP="patch")
        assert r["version"] == "2.6.2"

    @pytest.mark.parametrize("junk", ["", "PATCH ", "v2.9.9", "patch; rm -rf /", "$(id)", "minor\n::set-output x"])
    def test_only_known_choices_count(self, repo, junk):
        commit(repo, "docs: z")
        r = decide(repo, **BUTTON, FORCE_BUMP=junk)
        if junk.strip().lower() in ("patch",):
            assert r["bump"] == "patch"
        else:
            assert r["release"] == "false"

    def test_a_push_to_main_ignores_force_bump(self, repo):
        r = after_release(repo, "docs: z", GITHUB_EVENT_NAME="push", FORCE_BUMP="major")
        assert r["release"] == "false"


# ── the workflow wires it up correctly ────────────────────────────────────────


class TestWorkflow:
    @pytest.fixture()
    def wf(self):
        yaml = pytest.importorskip("yaml")
        return yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())

    @staticmethod
    def step(wf, name_part):
        return next(s for s in wf["jobs"]["build"]["steps"] if name_part in s.get("name", ""))

    def test_checkout_has_full_history_for_tags_and_commit_range(self, wf):
        assert self.step(wf, "Checkout")["with"]["fetch-depth"] == 0

    def test_version_is_decided_by_the_script_with_the_default_branch(self, wf):
        s = self.step(wf, "Decide version")
        assert "scripts/next_version.py" in s["run"] and s["id"] == "ver"
        assert "default_branch" in s["env"]["DEFAULT_BRANCH"]

    def test_image_is_built_with_the_decided_version(self, wf):
        build = self.step(wf, "Build and push")
        assert "steps.ver.outputs.version" in build["with"]["build-args"]

    def test_release_step_runs_after_the_image_push_and_only_when_needed(self, wf):
        names = [s.get("name", "") for s in wf["jobs"]["build"]["steps"]]
        assert names.index(self.step(wf, "Create tag")["name"]) > names.index(self.step(wf, "Build and push")["name"])
        cond = self.step(wf, "Create tag")["if"]
        assert "github.ref_type == 'tag'" in cond and "steps.ver.outputs.release == 'true'" in cond

    def test_release_is_created_for_the_decided_tag_at_this_commit(self, wf):
        w = self.step(wf, "Create tag")["with"]
        assert w["tag_name"] == "${{ steps.ver.outputs.tag }}"
        assert w["target_commitish"] == "${{ github.sha }}"

    def test_versioned_image_tags_are_added_only_for_releases(self, wf):
        tags = self.step(wf, "Extract metadata")["with"]["tags"]
        for out in ("version", "minor"):
            line = next(ln for ln in tags.splitlines() if f"steps.ver.outputs.{out}" in ln)
            assert "steps.ver.outputs.release == 'true'" in line

    def test_latest_is_only_moved_by_tags_and_the_default_branch(self, wf):
        line = next(ln for ln in self.step(wf, "Extract metadata")["with"]["tags"].splitlines() if "value=latest" in ln)
        assert "github.ref_type == 'tag'" in line and "default_branch" in line

    def test_builds_are_serialised_so_two_merges_cannot_pick_the_same_version(self, wf):
        c = wf["concurrency"]
        assert c["cancel-in-progress"] is False
        assert "github.ref_type == 'tag'" in c["group"] and "'main'" in c["group"]

    def test_workflow_can_create_tags_and_releases_and_push_packages(self, wf):
        assert wf["jobs"]["build"]["permissions"] == {"contents": "write", "packages": "write"}

    def test_the_release_button_is_a_choice_that_reaches_the_script(self, wf):
        on = wf.get("on", wf.get(True))
        bump = on["workflow_dispatch"]["inputs"]["bump"]
        assert bump["type"] == "choice" and bump["default"] == "none"
        assert bump["options"] == ["none", "patch", "minor", "major"]
        assert self.step(wf, "Decide version")["env"]["FORCE_BUMP"] == "${{ inputs.bump }}"

    def test_manual_tag_pushes_and_manual_runs_still_trigger_it(self, wf):
        on = wf.get("on", wf.get(True))
        assert on["push"]["tags"] == ["v*.*.*"] and on["push"]["branches"] == ["main"] and "workflow_dispatch" in on
