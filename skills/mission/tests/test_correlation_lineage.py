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
