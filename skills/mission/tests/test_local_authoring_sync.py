"""Local authoring source freshness contract for the mission skill."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SYNC_SCRIPT = REPO_ROOT / "scripts" / "mission-local-authoring-sync.sh"


def _git(*args, cwd=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture(tmp_path):
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    runtime = tmp_path / "runtime"

    _git("init", "--bare", "--initial-branch=main", str(remote))
    _git("clone", str(remote), str(source))
    _git("config", "user.name", "Test User", cwd=source)
    _git("config", "user.email", "test@example.invalid", cwd=source)
    (source / "skills").mkdir()
    (source / "skills" / "marker.txt").write_text("initial\n", encoding="utf-8")
    _git("add", "skills/marker.txt", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    _git("push", "origin", "main", cwd=source)
    _git("clone", str(remote), str(runtime))
    return remote, source, runtime


def _run(runtime, extra_env=None):
    env = os.environ.copy()
    env["MISSION_PLUGIN_ROOT"] = str(runtime)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SYNC_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def _advance_source(source, marker="next\n"):
    (source / "skills" / "marker.txt").write_text(marker, encoding="utf-8")
    _git("add", "skills/marker.txt", cwd=source)
    _git("commit", "-m", "advance", cwd=source)
    _git("push", "origin", "main", cwd=source)


def _commit_runtime(runtime, marker):
    _git("config", "user.name", "Test User", cwd=runtime)
    _git("config", "user.email", "test@example.invalid", cwd=runtime)
    (runtime / "skills" / "marker.txt").write_text(marker, encoding="utf-8")
    _git("add", "skills/marker.txt", cwd=runtime)
    _git("commit", "-m", "local change", cwd=runtime)


def test_clean_main_already_at_remote_head_is_ready(tmp_path):
    _, _, runtime = _fixture(tmp_path)

    result = _run(runtime)

    assert result.returncode == 0, result.stderr
    assert "status=ready" in result.stdout
    assert _git("rev-parse", "HEAD", cwd=runtime).stdout == _git(
        "rev-parse", "refs/remotes/origin/main", cwd=runtime
    ).stdout


def test_sync_target_cannot_be_overridden_away_from_origin_main(tmp_path):
    _, _, runtime = _fixture(tmp_path)

    result = _run(
        runtime,
        {
            "MISSION_LOCAL_REMOTE": "missing-remote",
            "MISSION_LOCAL_BRANCH": "missing-branch",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "branch=main" in result.stdout


def test_checkout_change_during_sync_is_rejected_before_ready(tmp_path):
    _, source, runtime = _fixture(tmp_path)
    _advance_source(source)
    wrapper_dir = tmp_path / "bin"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "git"
    wrapper.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *" status --porcelain --untracked-files=all" ]]; then
  count=0
  if [ -f "$GIT_WRAPPER_COUNTER" ]; then
    count="$(cat "$GIT_WRAPPER_COUNTER")"
  fi
  count=$((count + 1))
  printf '%s\n' "$count" >"$GIT_WRAPPER_COUNTER"
  if [ "$count" -eq 2 ]; then
    "$REAL_GIT" -C "$MISSION_PLUGIN_ROOT" switch -c raced >/dev/null
  fi
fi
exec "$REAL_GIT" "$@"
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = _run(
        runtime,
        {
            "PATH": f"{wrapper_dir}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": shutil.which("git"),
            "GIT_WRAPPER_COUNTER": str(tmp_path / "git-status-count"),
        },
    )

    assert result.returncode != 0
    assert "status=ready" not in result.stdout
    assert _git("branch", "--show-current", cwd=runtime).stdout.strip() == "raced"


def test_clean_main_behind_remote_is_fast_forwarded(tmp_path):
    _, source, runtime = _fixture(tmp_path)
    _advance_source(source)

    result = _run(runtime)

    assert result.returncode == 0, result.stderr
    assert (runtime / "skills" / "marker.txt").read_text(encoding="utf-8") == "next\n"
    assert _git("rev-parse", "HEAD", cwd=runtime).stdout == _git(
        "rev-parse", "refs/remotes/origin/main", cwd=runtime
    ).stdout


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_dirty_main_is_rejected_without_changing_files(tmp_path, dirty_kind):
    _, _, runtime = _fixture(tmp_path)
    if dirty_kind == "tracked":
        dirty_path = runtime / "skills" / "marker.txt"
    else:
        dirty_path = runtime / "local-note.txt"
    dirty_path.write_text("keep me\n", encoding="utf-8")

    result = _run(runtime)

    assert result.returncode != 0
    assert "clean" in result.stderr
    assert dirty_path.read_text(encoding="utf-8") == "keep me\n"


@pytest.mark.parametrize("checkout_kind", ["feature", "detached"])
def test_non_main_checkout_is_rejected_without_switching(tmp_path, checkout_kind):
    _, _, runtime = _fixture(tmp_path)
    if checkout_kind == "feature":
        _git("switch", "-c", "feature", cwd=runtime)
    else:
        _git("checkout", "--detach", cwd=runtime)
    before = _git("rev-parse", "HEAD", cwd=runtime).stdout

    result = _run(runtime)

    assert result.returncode != 0
    assert _git("rev-parse", "HEAD", cwd=runtime).stdout == before
    if checkout_kind == "feature":
        assert "main" in result.stderr
        assert _git("branch", "--show-current", cwd=runtime).stdout.strip() == "feature"
    else:
        assert "detached" in result.stderr


@pytest.mark.parametrize("history_kind", ["ahead", "diverged"])
def test_non_fast_forward_main_is_rejected_without_rewriting_history(tmp_path, history_kind):
    _, source, runtime = _fixture(tmp_path)
    _commit_runtime(runtime, "local\n")
    if history_kind == "diverged":
        _advance_source(source, "remote\n")
    before = _git("rev-parse", "HEAD", cwd=runtime).stdout

    result = _run(runtime)

    assert result.returncode != 0
    assert "cannot fast-forward" in result.stderr
    assert _git("rev-parse", "HEAD", cwd=runtime).stdout == before
    assert (runtime / "skills" / "marker.txt").read_text(encoding="utf-8") == "local\n"


def test_fetch_failure_does_not_fall_back_to_stale_checkout(tmp_path):
    _, _, runtime = _fixture(tmp_path)
    _git("remote", "set-url", "origin", str(tmp_path / "missing.git"), cwd=runtime)
    before = _git("rev-parse", "HEAD", cwd=runtime).stdout

    result = _run(runtime)

    assert result.returncode != 0
    assert _git("rev-parse", "HEAD", cwd=runtime).stdout == before
    assert "status=ready" not in result.stdout


def test_missing_remote_main_is_rejected(tmp_path):
    remote, _, runtime = _fixture(tmp_path)
    _git("--git-dir", str(remote), "update-ref", "-d", "refs/heads/main")

    result = _run(runtime)

    assert result.returncode != 0
    assert "status=ready" not in result.stdout


def test_skill_bootstraps_local_source_before_init_and_rereads_itself():
    skill = (REPO_ROOT / "skills" / "mission" / "SKILL.md").read_text(encoding="utf-8")

    bootstrap = skill.index("## Local authoring source bootstrap")
    compact = skill.index("## Compact Instructions")
    init = skill.index("mission-state.py init", compact)

    assert bootstrap < compact < init
    assert "mission-local-authoring-sync.sh" in skill[bootstrap:compact]
    assert "SKILL.md" in skill[bootstrap:compact]
    assert "読み直" in skill[bootstrap:compact]
    assert "fail-closed" in skill[bootstrap:compact]
    assert "fallback" in skill[bootstrap:compact]
