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

PINNABLE_COMPOSE = REGISTRY_COMPOSE.replace(
    "autorrent:latest", "autorrent:${AUTORRENT_VERSION:-latest}")

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
case "$*" in
    *" pull"*|*" up "*) echo "docker-env AUTORRENT_VERSION=${AUTORRENT_VERSION:-<unset>} $*" >> "$FAKE_LOG" ;;
esac
if [[ "$*" == "image inspect "* ]]; then
    echo "PATH=/usr/bin"
    [[ -n "${FAKE_IMAGE_VERSION:-}" ]] && echo "APP_VERSION=$FAKE_IMAGE_VERSION"
    exit 0
fi
if [[ "$*" == "inspect "* ]]; then
    echo "PATH=/usr/bin"
    [[ -n "${FAKE_RUNNING_VERSION:-}" ]] && echo "APP_VERSION=$FAKE_RUNNING_VERSION"
    exit 0
fi
if [[ "$*" == *" config --images"* ]]; then echo "ghcr.io/parabyte-ca/autorrent:${AUTORRENT_VERSION:-latest}"; exit 0; fi
if [[ "$*" == *" config"* ]]; then
    [[ -n "${FAKE_DOCKER_CONFIG_FAIL:-}" ]] && exit 1
    cat "$FAKE_COMPOSE_FILE"; exit 0
fi
if [[ "$*" == *" ps -q"* ]]; then [[ -n "${FAKE_RUNNING_VERSION:-}" ]] && echo "cid123"; exit 0; fi
if [[ "$*" == *" ps"* ]]; then
    if [[ -n "${FAKE_UNHEALTHY:-}" ]]; then echo "autorrent   Up 3 seconds (health: starting)"; else echo "autorrent   Up 3 seconds (healthy)"; fi
fi
exit 0
''',
    "sleep": r'''#!/usr/bin/env bash
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
    # Reports a stray (wrongly owned) file inside .git when FAKE_STRAY is set.
    "find": r'''#!/usr/bin/env bash
echo "find $*" >> "$FAKE_LOG"
[[ -n "${FAKE_STRAY:-}" ]] && echo "$1/FETCH_HEAD"
exit 0
''',
    "chown": r'''#!/usr/bin/env bash
echo "chown $*" >> "$FAKE_LOG"
[[ -n "${FAKE_CHOWN_FAIL:-}" ]] && exit 1
exit 0
''',
}


def run_update(tmp_path, *args, compose=REGISTRY_COMPOSE, uid="0", owner="amoote",
               origin=SSH_REMOTE, sudo_user=None, git_dir=False, **fake):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    if git_dir:
        (repo / ".git").mkdir(exist_ok=True)
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
    return [c for c in calls
            if c.startswith("docker ") and " config" not in c and " ps" not in c and " inspect" not in c]


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


# ── .git ownership repair ─────────────────────────────────────────────────────


class TestGitOwnershipRepair:
    def test_stray_files_are_handed_back_to_the_owner_before_git_runs(self, tmp_path):
        proc, calls = run_update(tmp_path, git_dir=True, fake_stray="1", sudo_user="truenas_admin")
        assert proc.returncode == 0, proc.stderr
        chowns = [c for c in calls if c.startswith("chown")]
        assert len(chowns) == 1 and chowns[0].startswith("chown -R amoote: ") and chowns[0].endswith("/.git")
        first_git = next(i for i, c in enumerate(calls) if c.startswith("sudo"))
        assert calls.index(chowns[0]) < first_git
        assert "Restoring ownership" in proc.stdout

    def test_nothing_is_changed_when_ownership_is_already_right(self, tmp_path):
        proc, calls = run_update(tmp_path, git_dir=True, sudo_user="truenas_admin")
        assert proc.returncode == 0, proc.stderr
        assert not any(c.startswith("chown") for c in calls)

    def test_not_root_never_touches_ownership(self, tmp_path):
        proc, calls = run_update(tmp_path, git_dir=True, uid="1000", fake_stray="1")
        assert not any(c.startswith(("chown", "find")) for c in calls)

    def test_root_owned_checkout_is_left_alone(self, tmp_path):
        proc, calls = run_update(tmp_path, git_dir=True, owner="root", fake_stray="1")
        assert not any(c.startswith("chown") for c in calls)

    def test_failed_repair_does_not_stop_the_update(self, tmp_path):
        proc, calls = run_update(tmp_path, git_dir=True, fake_stray="1", fake_chown_fail="1")
        assert proc.returncode == 0, proc.stderr
        assert "Could not change ownership" in proc.stdout

    def test_git_failure_explains_the_fix(self, tmp_path):
        proc, _ = run_update(tmp_path, fake_https_fail="1", fake_origin_fail="1")
        assert proc.returncode != 0
        out = proc.stdout + proc.stderr
        assert "git could not update" in out and "chown -R" in out


# ── version pinning and rollback (published image) ────────────────────────────


def _env(tmp_path):
    f = tmp_path / "repo" / ".env"
    return f.read_text() if f.exists() else None


def _pins(calls):
    """AUTORRENT_VERSION seen by each docker pull / up, in order."""
    return [(("pull" if " pull" in c else "up"), c.split()[1].split("=")[1])
            for c in calls if c.startswith("docker-env")]


def _seed_env(tmp_path, text):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / ".env").write_text(text)


class TestVersionPinning:
    def test_explicit_version_installs_and_pins_that_exact_image(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.7.0",
                                 compose=PINNABLE_COMPOSE, fake_running_version="2.6.1")
        assert proc.returncode == 0, proc.stderr
        assert _pins(calls) == [("pull", "2.7.0"), ("up", "2.7.0")]
        assert _env(tmp_path) == "AUTORRENT_VERSION=2.7.0\n"
        assert "previous: 2.6.1" in proc.stdout

    def test_a_v_prefix_is_accepted(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "v2.7.0", compose=PINNABLE_COMPOSE)
        assert proc.returncode == 0, proc.stderr
        assert _pins(calls)[0] == ("pull", "2.7.0")

    def test_going_back_to_an_older_release_works_the_same_way(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.5.0",
                                 compose=PINNABLE_COMPOSE, fake_running_version="2.6.1")
        assert proc.returncode == 0, proc.stderr
        assert _pins(calls) == [("pull", "2.5.0"), ("up", "2.5.0")]
        assert _env(tmp_path) == "AUTORRENT_VERSION=2.5.0\n"

    def test_no_version_pulls_latest_then_pins_what_it_turned_out_to_be(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", compose=PINNABLE_COMPOSE,
                                 fake_image_version="2.6.2", fake_running_version="2.6.1")
        assert proc.returncode == 0, proc.stderr
        assert _pins(calls) == [("pull", "latest"), ("up", "2.6.2")]
        assert _env(tmp_path) == "AUTORRENT_VERSION=2.6.2\n"

    def test_a_stale_pin_does_not_hold_back_a_plain_update(self, tmp_path):
        _seed_env(tmp_path, "AUTORRENT_VERSION=2.4.0\n")
        proc, calls = run_update(tmp_path, "--no-git", compose=PINNABLE_COMPOSE, fake_image_version="2.6.2")
        assert _pins(calls)[0] == ("pull", "latest")
        assert _env(tmp_path) == "AUTORRENT_VERSION=2.6.2\n"

    def test_unreadable_image_version_means_no_pin_and_clears_the_old_one(self, tmp_path):
        _seed_env(tmp_path, "FOO=bar\nAUTORRENT_VERSION=2.4.0\n")
        proc, calls = run_update(tmp_path, "--no-git", compose=PINNABLE_COMPOSE)
        assert proc.returncode == 0, proc.stderr
        assert _pins(calls) == [("pull", "latest"), ("up", "latest")]
        assert _env(tmp_path) == "FOO=bar\n"

    def test_other_env_lines_survive_and_there_is_only_one_pin(self, tmp_path):
        _seed_env(tmp_path, "APP_UPGRADE_FOLDER=/up\nAUTORRENT_VERSION=2.4.0\n# note\n")
        run_update(tmp_path, "--no-git", "--version", "2.7.0", compose=PINNABLE_COMPOSE)
        text = _env(tmp_path)
        assert text.count("AUTORRENT_VERSION") == 1 and "AUTORRENT_VERSION=2.7.0" in text
        assert "APP_UPGRADE_FOLDER=/up" in text and "# note" in text

    def test_a_new_env_file_belongs_to_the_checkout_owner(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.7.0",
                                 compose=PINNABLE_COMPOSE, sudo_user="truenas_admin")
        assert any(c.startswith("chown amoote: ") and c.endswith("/.env") for c in calls)

    @pytest.mark.parametrize("bad", ["latest", "2.7", "2.7.0;rm -rf /", "../2.7.0", "2.7.0-rc1"])
    def test_a_bad_version_is_refused_before_anything_is_pulled(self, tmp_path, bad):
        proc, calls = run_update(tmp_path, "--no-git", "--version", bad, compose=PINNABLE_COMPOSE)
        assert proc.returncode != 0
        assert not docker_calls(calls) and _env(tmp_path) is None

    def test_a_compose_file_without_the_variable_keeps_the_old_behaviour(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.7.0")   # REGISTRY_COMPOSE
        assert proc.returncode == 0, proc.stderr
        assert _env(tmp_path) is None
        assert not any(c.startswith("docker-env") and "<unset>" not in c for c in calls)

    def test_local_build_deployments_are_never_pinned(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", compose=BUILD_COMPOSE + "# AUTORRENT_VERSION\n")
        assert proc.returncode == 0, proc.stderr
        assert _env(tmp_path) is None


class TestRollback:
    def test_unhealthy_upgrade_goes_back_to_the_previous_release(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.7.0", compose=PINNABLE_COMPOSE,
                                 fake_running_version="2.6.1", fake_unhealthy="1", health_tries="2")
        assert proc.returncode != 0
        assert _pins(calls)[-2:] == [("pull", "2.6.1"), ("up", "2.6.1")]
        assert _env(tmp_path) == "AUTORRENT_VERSION=2.6.1\n"
        assert "Rollback complete" in proc.stdout

    def test_previous_release_falls_back_to_the_pin_when_nothing_is_running(self, tmp_path):
        _seed_env(tmp_path, "AUTORRENT_VERSION=2.6.0\n")
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.7.0", compose=PINNABLE_COMPOSE,
                                 fake_unhealthy="1", health_tries="2")
        assert proc.returncode != 0
        assert _pins(calls)[-1] == ("up", "2.6.0")
        assert _env(tmp_path) == "AUTORRENT_VERSION=2.6.0\n"

    def test_healthy_upgrade_does_not_roll_back(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.7.0", compose=PINNABLE_COMPOSE,
                                 fake_running_version="2.6.1")
        assert "Rolling back" not in proc.stdout
        assert _pins(calls)[-1] == ("up", "2.7.0")

    def test_without_a_known_previous_release_nothing_is_guessed(self, tmp_path):
        proc, calls = run_update(tmp_path, "--no-git", "--version", "2.7.0", compose=PINNABLE_COMPOSE,
                                 fake_unhealthy="1", health_tries="2")
        assert proc.returncode != 0
        assert "Rolling back to the previous release" not in proc.stdout
        assert _env(tmp_path) == "AUTORRENT_VERSION=2.7.0\n"
