"""Issue #385 correlation and reviewer lineage contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from .conftest import MISSION_STATE_PY


@pytest.fixture
def run_cli(legacy_run_cli):
    """Supersede/correlation admin commands remain v4-owned until #543."""
    return legacy_run_cli


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.5,
    "completeness": 4.0,
    "usability": 4.0,
}


def _state(root):
    return json.loads(next((root / ".mission-state" / "sessions").glob("*.json")).read_text())


def _git_review_scope(root):
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, check=True, text=True,
                              capture_output=True).stdout.strip()

    git("init")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "fixture")
    tracked = root / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    git("add", "tracked.txt")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    tracked.write_text("head\n", encoding="utf-8")
    git("commit", "-am", "head")
    return base, git("rev-parse", "HEAD")


def _review(path):
    path.write_text(json.dumps({
        "schema": "mission-review/1", "perspective": "neutral", "iteration": 1,
        "scores": ITEMS, "findings": [], "same_score_note": None,
        "notes": "neutral fixture",
    }), encoding="utf-8")
    return path


def test_init_records_typed_correlation_and_reviewer_generation(run_cli, tmp_path):
    result = run_cli(
        "init", "review an issue", "--force-mission", "--issue-ref", "385",
        "--host-run-id", "host-run-1", "--review-group-id", "issue-385-head-a",
        "--review-perspective", "quality", "--base-sha", "a" * 40, "--head-sha", "b" * 40,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    state = _state(tmp_path)
    assert state["host_run_id"] == "host-run-1"
    assert state["root_run_id"] == "host-run-1"
    assert state["parent_run_id"] is None
    assert state["child_run_id"] is None
    assert state["review_group_id"] == "issue-385-head-a"
    assert state["review_generation"] == 1
    assert state["review_perspective"] == "quality"
    assert state["base_sha"] == "a" * 40
    assert state["head_sha"] == "b" * 40
    assert state["supersedes"] == []


def test_init_uses_local_correlation_when_provider_does_not_supply_one(run_cli, tmp_path):
    result = run_cli("init", "review an issue", "--force-mission", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    state = _state(tmp_path)
    assert state["host_run_id"].startswith("mission-local-")
    assert state["root_run_id"] == state["host_run_id"]


def test_init_allocates_unique_review_generations_under_one_project_lock(tmp_path):
    """Concurrent init must allocate one current generation, not race on max + 1."""
    common = [
        sys.executable, str(MISSION_STATE_PY), "init", "review", "--force-mission",
        "--review-group-id", "issue-385-lock",
    ]
    processes = []
    for index in range(8):
        env = dict(os.environ)
        env["MISSION_SESSION_ID"] = f"concurrent-{index}"
        processes.append(subprocess.Popen(
            common, cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ))
    results = [process.communicate() for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    from mission_persistence.authoritative_reader import read_authoritative_snapshot

    states = [
        read_authoritative_snapshot(
            path, expected_session_id=path.stem
        ).document_copy()
        for path in (tmp_path / ".mission-state" / "sessions").glob("*.json")
    ]
    generations = sorted(state["review_generation"] for state in states)
    assert generations == list(range(1, 9))
    assert sum(state["review_generation"] == max(generations) and state["loop_active"] for state in states) == 1


def test_init_rejects_malformed_group_tokens_before_state_write(run_cli, tmp_path):
    for option in ("--review-group-id", "--logical-group-id"):
        result = run_cli("init", "review", "--force-mission", option, "invalid token\n", cwd=tmp_path)
        assert result.returncode == 2
        assert "opaque token" in result.stderr
        assert not list((tmp_path / ".mission-state" / "sessions").glob("*.json"))


def test_supersede_reviews_terminals_only_old_generation_and_keeps_raw_records(run_cli, tmp_path):
    common = [
        "init", "review issue", "--force-mission", "--issue-ref", "385",
        "--review-group-id", "issue-385", "--review-perspective", "quality",
    ]
    old = run_cli(*common, "--base-sha", "a" * 40, "--head-sha", "b" * 40,
                  cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "old"})
    assert old.returncode == 0, old.stderr
    current = run_cli(*common, "--base-sha", "c" * 40, "--head-sha", "d" * 40,
                      cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "current"})
    assert current.returncode == 0, current.stderr

    result = run_cli("supersede-reviews", "--group", "issue-385", cwd=tmp_path,
                     env_extra={
                         "MISSION_SESSION_ID": "current",
                         "MISSION_OPERATION_ID": "supersede-issue-385",
                     })

    assert result.returncode == 0, result.stderr
    old_state = json.loads((tmp_path / ".mission-state" / "sessions" / "old.json").read_text())
    current_state = json.loads((tmp_path / ".mission-state" / "sessions" / "current.json").read_text())
    assert old_state["review_generation"] == 1
    assert old_state["terminal_outcome"] == "stale_superseded"
    assert old_state["passes"] is False and old_state["loop_active"] is False
    assert current_state["review_generation"] == 2
    assert current_state["loop_active"] is True
    assert current_state["supersedes"] == ["old"]


def test_supersede_rejects_duplicate_current_generation_without_writing(run_cli, tmp_path):
    common = ["init", "review", "--force-mission", "--review-group-id", "group"]
    for sid in ("one", "two"):
        result = run_cli(*common, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": sid})
        assert result.returncode == 0, result.stderr
    sessions = tmp_path / ".mission-state" / "sessions"
    first, second = sessions / "one.json", sessions / "two.json"
    duplicate = json.loads(first.read_text())
    duplicate["review_generation"] = json.loads(second.read_text())["review_generation"]
    first.write_text(json.dumps(duplicate), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in (first, second)}

    result = run_cli("supersede-reviews", "--group", "group", cwd=tmp_path,
                     env_extra={"MISSION_SESSION_ID": "two"})

    assert result.returncode == 2
    assert {path.name: path.read_bytes() for path in (first, second)} == before


def test_review_provenance_binds_current_generation_and_rejects_old_aggregate_replay(run_cli, tmp_path):
    """A previous reviewer generation cannot supply a new generation's score."""
    base, head = _git_review_scope(tmp_path)
    common = [
        "init", "review issue", "--force-mission", "--issue-ref", "385",
        "--artifact-applicability", "not-applicable", "--review-group-id", "issue-385",
        "--review-perspective", "quality", "--base-sha", base, "--head-sha", head,
    ]
    assert run_cli(*common, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "old"}).returncode == 0
    review = _review(tmp_path / "review.json")
    old_score = tmp_path / "old-score.json"
    aggregate = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review), "--out", str(old_score),
        "--base-sha", base, "--head-sha", head, cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "old"},
    )
    assert aggregate.returncode == 0, aggregate.stderr
    ref = json.loads(old_score.read_text(encoding="utf-8"))["score_provenance"]["review_evidence_ref"]
    assert ref["review_group_id"] == "issue-385"
    assert ref["review_generation"] == 1
    assert ref["base_sha"] == base
    assert ref["head_sha"] == head

    assert run_cli(*common, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "current"}).returncode == 0
    replay = run_cli(
        "push-score", "--iteration", "1", "--scoring-json", str(old_score), cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "current"},
    )
    assert replay.returncode == 2
    assert "review lineage" in replay.stderr


def test_mark_passes_revalidates_review_generation_without_writing(run_cli, tmp_path):
    """The terminal gate cannot use a provenance ref from another generation."""
    base, head = _git_review_scope(tmp_path)
    common = [
        "init", "review issue", "--force-mission", "--issue-ref", "385",
        "--artifact-applicability", "not-applicable", "--review-group-id", "issue-385",
        "--review-perspective", "quality", "--base-sha", base, "--head-sha", head,
    ]
    assert run_cli(*common, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "old"}).returncode == 0
    assert run_cli(*common, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "current"}).returncode == 0
    review = _review(tmp_path / "review.json")
    finalized = run_cli(
        "review-finalize", "--iteration", "1", "--input", str(review),
        "--base-sha", base, "--head-sha", head, cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "current"},
    )
    assert finalized.returncode == 0, finalized.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / "current.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["score_history"][-1]["score_provenance"]["review_evidence_ref"]["review_generation"] = 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    rejected = run_cli("mark-passes", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "current"})

    assert rejected.returncode == 2
    assert "review lineage" in rejected.stderr
    assert state_path.read_bytes() == before
