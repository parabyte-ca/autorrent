"""Tests for scripts/resolve-version.sh, which picks the APP_VERSION baked into each image.

These use real git repositories, because the behaviour that matters is what git
reports about tags and reachability.
"""
import os
import pathlib
import shutil
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "resolve-version.sh"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
        env={**os.environ, **_GIT_ENV},
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """A repo holding the script and a health.py whose stale default is 2.4.0."""
    r = tmp_path / "repo"
    (r / "scripts").mkdir(parents=True)
    (r / "backend" / "app" / "routers").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "scripts" / "resolve-version.sh")
    (r / "backend" / "app" / "routers" / "health.py").write_text('v = os.environ.get("APP_VERSION", "2.4.0")\n')
    git(r, "init", "-q")
    git(r, "symbolic-ref", "HEAD", "refs/heads/main")
    commit(r, "initial")
    return r


_n = 0


def commit(repo, message):
    global _n
    _n += 1
    (repo / f"f{_n}.txt").write_text(message)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def resolve(repo, **env):
    proc = subprocess.run(
        ["bash", str(repo / "scripts" / "resolve-version.sh")],
        capture_output=True, text=True, cwd=repo,
        env={**os.environ, **_GIT_ENV, "GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main", **env},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_main_build_after_a_release_reports_that_release_not_the_stale_default(repo):
    """The bug: merging to main after tagging made `latest` claim 2.4.0."""
    commit(repo, "release work")
    git(repo, "tag", "v2.6.0")
    commit(repo, "merge of a later PR")
    assert resolve(repo) == "2.6.0\n"


def test_tag_build_uses_the_tag_name(repo):
    git(repo, "tag", "v2.5.0")
    commit(repo, "more")
    assert resolve(repo, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v2.7.1") == "2.7.1\n"


def test_highest_version_wins_by_number_not_alphabetically(repo):
    for tag in ("v2.9.0", "v2.10.0", "v2.2.0"):
        commit(repo, tag)
        git(repo, "tag", tag)
    assert resolve(repo) == "2.10.0\n"


def test_non_release_tags_are_ignored(repo):
    commit(repo, "r")
    git(repo, "tag", "v2.6.0")
    for junk in ("v2.7.0-rc1", "v3", "v2.8", "vnext", "latest", "release-1.0.0", "v1.2.3.4"):
        commit(repo, junk)
        git(repo, "tag", junk)
    assert resolve(repo) == "2.6.0\n"


def test_a_tag_not_reachable_from_head_is_ignored(repo):
    commit(repo, "r")
    git(repo, "tag", "v2.6.0")
    git(repo, "checkout", "-q", "-b", "other")
    commit(repo, "elsewhere")
    git(repo, "tag", "v9.9.9")
    git(repo, "checkout", "-q", "main")
    assert resolve(repo) == "2.6.0\n"


def test_falls_back_to_the_source_default_when_there_are_no_release_tags(repo):
    assert resolve(repo) == "2.4.0\n"


def test_output_is_exactly_one_clean_line_for_the_workflow(repo):
    git(repo, "tag", "v2.6.0")
    out = resolve(repo)
    assert out == "2.6.0\n" and out.count("\n") == 1


def test_works_when_invoked_from_another_directory(repo, tmp_path):
    git(repo, "tag", "v2.6.0")
    proc = subprocess.run(
        ["bash", str(repo / "scripts" / "resolve-version.sh")], capture_output=True, text=True,
        cwd=tmp_path, env={**os.environ, **_GIT_ENV, "GITHUB_REF_TYPE": "branch"},
    )
    assert proc.stdout == "2.6.0\n"


def test_the_real_script_in_this_repository_runs():
    proc = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                          env={**os.environ, "GITHUB_REF_TYPE": "branch"})
    assert proc.returncode == 0, proc.stderr
    import re
    assert re.fullmatch(r"\d+\.\d+\.\d+\n", proc.stdout), proc.stdout


class TestWorkflowUsesIt:
    workflow = (SCRIPT.parents[1] / ".github" / "workflows" / "build.yml").read_text()

    def test_checkout_fetches_tags(self):
        assert "fetch-depth: 0" in self.workflow, "branch builds can't see release tags in a shallow clone"

    def test_version_comes_from_the_script_not_a_hard_coded_default(self):
        assert "scripts/resolve-version.sh" in self.workflow
        assert "health.py" not in self.workflow
