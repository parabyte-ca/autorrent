"""Behavioural tests for update.sh, run for real against fake git/docker/sudo/id/stat.

The shims record every command they are asked to run, so the tests assert on
exactly what the upgrade would do — who runs git, which URL it fetches, and
whether the Docker image is pulled or built — without touching a network,
a Docker daemon, or a real repository.
"""
import os
import pathlib
import stat
import subprocess
import textwrap

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "update.sh"

REGISTRY_COMPOSE = textwrap.dedent("""\
    services:
      autorrent:
        image: ghcr.io/parabyte-ca/autorrent:latest
        # build: .        # uncomment to build locally instead of pulling from registry
        container_name: autorrent
""")
BUILD_COMPOSE = textwrap.dedent("""\
    services:
      autorrent:
        image: autorrent
        build: .
""")

SSH_REMOTE = "git@github.com:parabyte-ca/autorrent.git"
HTTPS = "https://github.com/parabyte-ca/autorrent.git"

_SHIMS = {
    # Records the call; behaviour is steered by FAKE_* environment variables.
    "git": r'''#!/usr/bin/env bash
echo "git $*" >> "$FAKE_LOG"
args=" $* "
if [[ "$args" == *" config --get remote.origin.url "* ]]; then echo "$FAKE_ORIGIN_URL"; exit 0; fi
if [[ "$args" == *" rev-parse "* ]]; then echo "abc1234"; exit 0; fi
if [[ "$args" == *" symbolic-ref "* && -n "${FAKE_DETACHED:-}" ]]; then exit 1; fi
if [[ "$args" == *" https://"* && -n "${FAKE_HTTPS_FAIL:-}" ]]; then echo "fatal: unable to access" >&2; exit 128; fi
if [[ "$args" == *" origin"* && -n "${FAKE_ORIGIN_FAIL:-}" ]]; then echo "fatal: Could not read from remote repository." >&2; exit 128; fi
exit 0
''',
    "docker": r'''#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
if [[ "$*" == *" config"* ]]; then
    [[ -n "${FAKE_DOCKER_CONFIG_FAIL:-}" ]] && exit 1
    cat "$FAKE_COMPOSE_FILE"; exit 0
fi
if [[ "$*" == *" ps"* ]]; then echo "autorrent   Up 3 seconds (healthy)"; fi
exit 0
''',
    # Strips "-H -u USER" and runs the rest, but records who it was run as.
    "sudo": r'''#!/usr/bin/env bash
echo "sudo $*" >> "$FAKE_LOG"
shift 3
exec "$@"
''',
    "id": r'''#!/usr/bin/env bash
echo "${FAKE_UID:-0}"
''',
    "stat": r'''#!/usr/bin/env bash
echo "${FAKE_OWNER:-root}"
''',
}


def run_update(tmp_path, *args, compose=REGISTRY_COMPOSE, uid="0", owner="amoote",
               origin=SSH_REMOTE, sudo_user=None, **fake):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "update.sh").write_text(SCRIPT.read_text())
    compose_file = repo / "docker-compose.yml"
    compose_file.write_text(compose)

    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in _SHIMS.items():
        shim = bindir / name
        shim.write_text(body)
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    log = tmp_path / "calls.log"
    log.write_text("")
    env = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "FAKE_LOG": str(log),
        "FAKE_COMPOSE_FILE": str(compose_file),
        "FAKE_ORIGIN_URL": origin,
        "FAKE_UID": uid,
        "FAKE_OWNER": owner,
    }
    if sudo_user:
        env["SUDO_USER"] = sudo_user
    env.update({k.upper(): v for k, v in fake.items()})

    proc = subprocess.run(
        ["bash", str(repo / "update.sh"), "--skip-preflight", *args],
        capture_output=True, text=True, env=env, cwd=repo, timeout=60,
    )
    calls = [line for line in log.read_text().splitlines() if line]
    return proc, calls


def docker_calls(calls):
    return [c for c in calls if c.startswith("docker ") and " config" not in c and " ps" not in c]


# ── pull vs build ─────────────────────────────────────────────────────────────


class TestPullOrBuild:
    def test_registry_deployment_pulls_automatically(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git")
        assert proc.returncode == 0, proc.stderr
        d = docker_calls(calls)
        assert any(" pull" in c for c in d)
        assert not any(" build" in c for c in d)
        assert "pulling instead of building" in proc.stdout

    def test_local_build_deployment_still_builds(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", compose=BUILD_COMPOSE)
        assert proc.returncode == 0, proc.stderr
        d = docker_calls(calls)
        assert any(" build" in c for c in d) and not any(" pull" in c for c in d)
        assert "pulling instead" not in proc.stdout

    def test_explicit_pull_flag_wins(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--pull", compose=BUILD_COMPOSE)
        d = docker_calls(calls)
        assert any(" pull" in c for c in d) and not any(" build" in c for c in d)

    @pytest.mark.parametrize("compose,expect", [(REGISTRY_COMPOSE, "pull"), (BUILD_COMPOSE, "build")])
    def test_falls_back_to_reading_the_file_when_compose_config_fails(self, tmp_path, compose, expect):
        proc, calls = run_update(tmp_path, "--no-git", compose=compose, fake_docker_config_fail="1")
        assert proc.returncode == 0, proc.stderr
        d = docker_calls(calls)
        assert any(f" {expect}" in c for c in d)

    def test_the_container_is_restarted_after_the_image_is_pulled(self, tmp_path):
        _, calls = run_update(tmp_path, "--no-git")
        d = docker_calls(calls)
        pulled = next(i for i, c in enumerate(d) if " pull" in c)
        restarted = next(i for i, c in enumerate(d) if " up" in c)
        assert pulled < restarted


# ── who runs git ──────────────────────────────────────────────────────────────


class TestGitUser:
    def test_root_from_cron_runs_git_as_the_checkout_owner(self, tmp_path):
        # cron: root, no SUDO_USER, no login name
        proc, calls = run_update(tmp_path, "--pull", uid="0", owner="amoote")
        sudo_calls = [c for c in calls if c.startswith("sudo ")]
        assert sudo_calls, "git must be run through sudo as the owner"
        assert all(c.startswith("sudo -H -u amoote env GIT_TERMINAL_PROMPT=0 git -C ") for c in sudo_calls)
        # and every git call went through sudo (none ran directly as root)
        git_calls = [c for c in calls if c.startswith("git ")]
        assert len(git_calls) == len(sudo_calls)

    def test_owner_beats_sudo_user(self, tmp_path):
        _, calls = run_update(tmp_path, "--pull", uid="0", owner="amoote", sudo_user="someoneelse")
        assert any(c.startswith("sudo -H -u amoote ") for c in calls)
        assert not any("someoneelse" in c for c in calls)

    def test_sudo_user_used_when_checkout_is_owned_by_root(self, tmp_path):
        _, calls = run_update(tmp_path, "--pull", uid="0", owner="root", sudo_user="amoote")
        assert any(c.startswith("sudo -H -u amoote ") for c in calls)

    def test_no_sudo_when_owner_is_root_and_no_sudo_user(self, tmp_path):
        _, calls = run_update(tmp_path, "--pull", uid="0", owner="root")
        assert not any(c.startswith("sudo ") for c in calls)
        assert any(c.startswith("git ") for c in calls)

    def test_no_sudo_when_not_root(self, tmp_path):
        _, calls = run_update(tmp_path, "--pull", uid="1000", owner="amoote")
        assert not any(c.startswith("sudo ") for c in calls)

    def test_rollback_revision_is_read_as_the_git_user(self, tmp_path):
        _, calls = run_update(tmp_path, "--pull", uid="0", owner="amoote")
        assert any(c.startswith("sudo ") and "rev-parse" in c for c in calls)

    def test_checkout_of_a_version_runs_as_the_git_user(self, tmp_path):
        _, calls = run_update(tmp_path, "--version", "2.6.0", uid="0", owner="amoote", compose=BUILD_COMPOSE)
        checkout = [c for c in calls if " checkout " in c]
        assert checkout and checkout[0].startswith("sudo -H -u amoote ") and "v2.6.0" in checkout[0]


# ── how git fetches ───────────────────────────────────────────────────────────


class TestGitFetch:
    def test_ssh_remote_is_fetched_over_anonymous_https_first(self, tmp_path):
        proc, calls = run_update(tmp_path, uid="1000")
        assert proc.returncode == 0, proc.stderr
        pulls = [c for c in calls if " pull " in c]
        assert len(pulls) == 1 and pulls[0].endswith(f"pull {HTTPS} main")
        assert not any(" pull origin" in c for c in calls)

    def test_falls_back_to_the_configured_remote_if_https_fails(self, tmp_path):
        proc, calls = run_update(tmp_path, uid="1000", fake_https_fail="1")
        assert proc.returncode == 0, proc.stderr
        pulls = [c for c in calls if " pull " in c]
        assert pulls[0].endswith(f"pull {HTTPS} main") and pulls[1].endswith("pull origin main")
        assert "trying the configured remote" in proc.stdout

    def test_fails_when_both_attempts_fail_and_changes_nothing(self, tmp_path):
        proc, calls = run_update(tmp_path, uid="1000", fake_https_fail="1", fake_origin_fail="1")
        assert proc.returncode != 0
        assert "Could not read from remote repository" in proc.stderr
        assert not any(c.startswith("docker ") and " up" in c for c in calls)

    def test_https_remote_is_tried_once_not_twice(self, tmp_path):
        _, calls = run_update(tmp_path, uid="1000", origin=HTTPS)
        pulls = [c for c in calls if " pull " in c]
        assert len(pulls) == 1 and pulls[0].endswith("pull origin main")

    def test_ssh_url_form_is_converted_too(self, tmp_path):
        _, calls = run_update(tmp_path, uid="1000", origin="ssh://git@github.com/parabyte-ca/autorrent.git")
        assert any(c.endswith(f"pull {HTTPS} main") for c in calls)

    def test_non_github_remote_is_used_as_is(self, tmp_path):
        _, calls = run_update(tmp_path, uid="1000", origin="git@gitlab.com:me/autorrent.git")
        pulls = [c for c in calls if " pull " in c]
        assert len(pulls) == 1 and pulls[0].endswith("pull origin main")
        assert not any("github.com/" in c for c in calls)

    def test_tags_are_fetched_over_https_for_a_version_upgrade(self, tmp_path):
        proc, calls = run_update(tmp_path, "--version", "2.6.0", uid="1000", compose=BUILD_COMPOSE)
        assert proc.returncode == 0, proc.stderr
        fetch = [c for c in calls if " fetch " in c]
        assert fetch and fetch[0].endswith(f"fetch --tags {HTTPS}")
        assert any(c.endswith("checkout v2.6.0") for c in calls)

    def test_tag_fetch_falls_back_to_origin(self, tmp_path):
        _, calls = run_update(tmp_path, "--version", "2.6.0", uid="1000", fake_https_fail="1", compose=BUILD_COMPOSE)
        fetch = [c for c in calls if " fetch " in c]
        assert fetch[-1].endswith("fetch --tags origin")

    def test_no_git_skips_all_git_network_calls(self, tmp_path):
        _, calls = run_update(tmp_path, "--no-git", uid="1000")
        assert not any(" pull " in c or " fetch " in c for c in calls)

    def test_git_never_prompts_for_a_password(self, tmp_path):
        _, calls = run_update(tmp_path, uid="0", owner="amoote")
        assert all("GIT_TERMINAL_PROMPT=0" in c for c in calls if c.startswith("sudo "))


# ── the exact production scenario ─────────────────────────────────────────────


def test_cron_upgrade_to_a_release_end_to_end(tmp_path):
    """Root, from cron, registry compose, SSH remote: update the branch over HTTPS as the owner, pull the image."""
    proc, calls = run_update(
        tmp_path, "--version", "2.6.0", uid="0", owner="truenas_admin", origin=SSH_REMOTE,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Deployment complete" in proc.stdout
    ops = (" pull ", " fetch ", " checkout ")
    via_sudo = [c for c in calls if c.startswith("sudo ") and any(o in c for o in ops)]
    assert via_sudo, "git network work must go through sudo as the owner"
    assert all(c.startswith("sudo -H -u truenas_admin env GIT_TERMINAL_PROMPT=0 git -C ") for c in via_sudo)
    assert any(HTTPS in c for c in via_sudo) and not any("git@github.com" in c for c in via_sudo)
    # no git operation ran as root outside sudo: every git call has a matching sudo call
    assert len([c for c in calls if c.startswith("git ")]) == len([c for c in calls if c.startswith("sudo ")])
    d = docker_calls(calls)
    assert any(" pull" in c for c in d) and not any(" build" in c for c in d)


# ── published-image deployments stay on the branch ────────────────────────────


class TestStaysOnTheBranch:
    """Parking on a release tag rewound these very scripts to that release's copy."""

    def test_registry_deployment_never_checks_out_a_release_tag(self, tmp_path):
        proc, calls = run_update(tmp_path, "--version", "2.6.1", uid="1000")
        assert proc.returncode == 0, proc.stderr
        assert not any(" checkout " in c for c in calls), "must not move off the branch"
        assert not any(" fetch " in c for c in calls), "tags are not needed"
        assert any(c.endswith(f"pull {HTTPS} main") for c in calls)
        assert "staying on main" in proc.stdout

    def test_explicit_pull_flag_also_counts_as_a_published_image_deployment(self, tmp_path):
        _, calls = run_update(tmp_path, "--version", "2.6.1", "--pull", uid="1000", compose=BUILD_COMPOSE)
        assert not any(" checkout " in c for c in calls)

    def test_a_checkout_left_on_an_old_tag_is_returned_to_the_branch_first(self, tmp_path):
        proc, calls = run_update(tmp_path, "--version", "2.6.1", uid="1000", fake_detached="1")
        assert proc.returncode == 0, proc.stderr
        git = [c for c in calls if c.startswith("git ")]
        back = next(i for i, c in enumerate(git) if c.endswith("checkout main"))
        pulled = next(i for i, c in enumerate(git) if " pull " in c)
        assert back < pulled, "return to the branch before pulling"
        assert "returning to main" in proc.stdout

    def test_nothing_is_checked_out_when_already_on_the_branch(self, tmp_path):
        _, calls = run_update(tmp_path, uid="1000")
        assert not any(" checkout " in c for c in calls)

    def test_also_works_without_a_version(self, tmp_path):
        """A plain ./update.sh run from a checkout parked on a tag heals it too."""
        _, calls = run_update(tmp_path, uid="1000", fake_detached="1")
        assert any(c.endswith("checkout main") for c in calls)

    def test_no_git_never_touches_the_checkout(self, tmp_path):
        _, calls = run_update(tmp_path, "--no-git", uid="1000", fake_detached="1")
        assert not any(" checkout " in c for c in calls)

    def test_local_build_deployments_still_deploy_the_release_they_asked_for(self, tmp_path):
        proc, calls = run_update(tmp_path, "--version", "2.6.1", uid="1000", compose=BUILD_COMPOSE)
        assert proc.returncode == 0, proc.stderr
        assert any(c.endswith("checkout v2.6.1") for c in calls)
        assert any(c.endswith(f"fetch --tags {HTTPS}") for c in calls)
        assert "staying on main" not in proc.stdout

    def test_local_build_deployments_are_not_pulled_back_to_the_branch(self, tmp_path):
        _, calls = run_update(tmp_path, "--version", "2.6.1", uid="1000", compose=BUILD_COMPOSE, fake_detached="1")
        assert not any(c.endswith("checkout main") for c in calls)
