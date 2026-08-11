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
