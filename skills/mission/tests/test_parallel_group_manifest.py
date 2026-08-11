"""Issue #388 parallel group manifest admission."""

import json


def test_parallel_init_writes_versioned_manifest_with_unique_children(run_cli, tmp_path):
    result = run_cli(
        "parallel-init", "--group-id", "group-388", "--issue-ref", "388", "--issue-ref", "389",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / ".mission-state" / "sessions" / "group-388.group.json").read_text())
    assert manifest["schema"] == "mission-parallel-group/1"
    assert manifest["group_id"] == "group-388"
    assert manifest["planned_children"] == [{"issue_ref": "388"}, {"issue_ref": "389"}]
    assert manifest["status"] == "running"


def test_parallel_init_rejects_duplicate_issue_ref_without_manifest(run_cli, tmp_path):
    result = run_cli(
        "parallel-init", "--group-id", "group-388", "--issue-ref", "388", "--issue-ref", "388",
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert not (tmp_path / ".mission-state" / "sessions" / "group-388.group.json").exists()


def test_parallel_closeout_rejects_incomplete_child_without_writing_manifest(run_cli, tmp_path):
    run_cli("parallel-init", "--group-id", "group-388", "--issue-ref", "388", "--issue-ref", "389", cwd=tmp_path, check=True)
    for sid, issue, passed in (("one", "388", True), ("two", "389", False)):
        result = run_cli("init", "child", "--force-mission", "--issue-ref", issue,
                         "--logical-group-id", "group-388", cwd=tmp_path,
                         env_extra={"MISSION_SESSION_ID": sid})
        assert result.returncode == 0
        path = tmp_path / ".mission-state" / "sessions" / f"{sid}.json"
        state = json.loads(path.read_text())
        state.update({"passes": passed, "loop_active": not passed, "halt_reason": ""})
        path.write_text(json.dumps(state))
    manifest_path = tmp_path / ".mission-state" / "sessions" / "group-388.group.json"
    before = manifest_path.read_bytes()
    rejected = run_cli("parallel-closeout", "--group-id", "group-388", cwd=tmp_path)
    assert rejected.returncode == 2
    assert manifest_path.read_bytes() == before
    assert json.loads(run_cli("parallel-status", "--group-id", "group-388", cwd=tmp_path, check=True).stdout)["incomplete"] == ["389"]


def test_parallel_status_classifies_active_lease_waiting_and_expired_child_incomplete(run_cli, tmp_path):
    run_cli("parallel-init", "--group-id", "lease-388", "--issue-ref", "388", cwd=tmp_path, check=True)
    run_cli("init", "child", "--force-mission", "--issue-ref", "388", "--logical-group-id", "lease-388", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "lease"}, check=True)
    status = json.loads(run_cli("parallel-status", "--group-id", "lease-388", cwd=tmp_path, check=True).stdout)
    assert status["incomplete"] == ["388"] and status["active_leases"] == ["388"]


def test_parallel_closeout_reports_coverage_and_requires_all_terminal_leases_released(run_cli, tmp_path):
    refs = [str(400 + n) for n in range(10)]
    args = ["parallel-init", "--group-id", "coverage-388"] + sum((["--issue-ref", ref] for ref in refs), [])
    run_cli(*args, cwd=tmp_path, check=True)
    for ref in refs:
        run_cli("init", "child", "--force-mission", "--issue-ref", ref, "--logical-group-id", "coverage-388", "--artifact-applicability", "not-applicable", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": f"s{ref}"}, check=True)
        path = tmp_path / ".mission-state" / "sessions" / f"s{ref}.json"; state=json.loads(path.read_text())
        state.update({"loop_active": False, "passes": True, "lease_expires_at": "2000-01-01T00:00:00Z", "activity_segments": [], "score_history": [{"score_provenance": {}}]}); path.write_text(json.dumps(state))
    status=json.loads(run_cli("parallel-status", "--group-id", "coverage-388", cwd=tmp_path, check=True).stdout)
    assert status["coverage"]["ratio"] >= .9
    assert run_cli("parallel-closeout", "--group-id", "coverage-388", cwd=tmp_path).returncode == 0
