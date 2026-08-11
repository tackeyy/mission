"""Issue #385 correlation and reviewer lineage contracts."""

from __future__ import annotations

import json


def _state(root):
    return json.loads(next((root / ".mission-state" / "sessions").glob("*.json")).read_text())


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
                     env_extra={"MISSION_SESSION_ID": "current"})

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
