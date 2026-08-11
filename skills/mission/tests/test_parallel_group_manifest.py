"""Issue #388 parallel group manifest lifecycle contracts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


def _manifest_path(root, group_id):
    return root / ".mission-state" / "sessions" / f"{group_id}.group.json"


def _state_path(root, session_id):
    return root / ".mission-state" / "sessions" / f"{session_id}.json"


def _init_child(run_cli, root, *, group_id, issue_ref, session_id):
    result = run_cli(
        "init",
        "child",
        "--force-mission",
        "--issue-ref",
        str(issue_ref),
        "--logical-group-id",
        group_id,
        cwd=root,
        env_extra={"MISSION_SESSION_ID": session_id},
    )
    assert result.returncode == 0, result.stderr
    return _state_path(root, session_id)


def _update_state(path, **changes):
    state = json.loads(path.read_text())
    state.update(changes)
    path.write_text(json.dumps(state))


def _status(run_cli, root, group_id):
    result = run_cli("parallel-status", "--group-id", group_id, cwd=root)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_parallel_init_writes_versioned_manifest_with_unique_children(run_cli, tmp_path):
    result = run_cli(
        "parallel-init",
        "--group-id",
        "group-388",
        "--issue-ref",
        "388",
        "--issue-ref",
        "389",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads(_manifest_path(tmp_path, "group-388").read_text())
    assert manifest["schema"] == "mission-parallel-group/1"
    assert manifest["group_id"] == "group-388"
    assert manifest["planned_children"] == [{"issue_ref": "388"}, {"issue_ref": "389"}]
    assert manifest["status"] == "running"


def test_parallel_init_rejects_duplicate_issue_ref_without_manifest(run_cli, tmp_path):
    result = run_cli(
        "parallel-init",
        "--group-id",
        "group-388",
        "--issue-ref",
        "388",
        "--issue-ref",
        "#388",
        cwd=tmp_path,
    )
    assert result.returncode == 2
    assert not _manifest_path(tmp_path, "group-388").exists()


def test_parallel_status_tracks_planned_running_waiting_pass_and_halt(run_cli, tmp_path):
    run_cli(
        "parallel-init",
        "--group-id",
        "lifecycle-388",
        "--issue-ref",
        "101",
        "--issue-ref",
        "102",
        cwd=tmp_path,
        check=True,
    )
    assert _status(run_cli, tmp_path, "lifecycle-388")["children"]["101"]["status"] == "planned"

    one = _init_child(
        run_cli,
        tmp_path,
        group_id="lifecycle-388",
        issue_ref="101",
        session_id="one",
    )
    assert _status(run_cli, tmp_path, "lifecycle-388")["children"]["101"]["status"] == "running"

    _update_state(one, lease_expires_at="2000-01-01T00:00:00Z")
    waiting = _status(run_cli, tmp_path, "lifecycle-388")
    assert waiting["children"]["101"]["status"] == "waiting"
    assert waiting["waiting"] == ["101"]

    _update_state(one, loop_active=False, passes=True)
    assert _status(run_cli, tmp_path, "lifecycle-388")["children"]["101"]["status"] == "pass"

    two = _init_child(
        run_cli,
        tmp_path,
        group_id="lifecycle-388",
        issue_ref="102",
        session_id="two",
    )
    _update_state(
        two,
        loop_active=False,
        passes=False,
        halt_reason="provider unavailable",
        lease_expires_at="2000-01-01T00:00:00Z",
    )
    halted = _status(run_cli, tmp_path, "lifecycle-388")
    assert halted["pass"] == ["101"]
    assert halted["halt"] == ["102"]
    assert halted["incomplete"] == []
    closed = run_cli("parallel-closeout", "--group-id", "lifecycle-388", cwd=tmp_path)
    assert closed.returncode == 0, closed.stderr
    assert json.loads(closed.stdout)["outcome"] == "halt"
    assert json.loads(_manifest_path(tmp_path, "lifecycle-388").read_text())["outcome"] == "halt"


def test_parallel_status_accepts_planned_late_init_but_rejects_unplanned_child(run_cli, tmp_path):
    run_cli(
        "parallel-init",
        "--group-id",
        "late-388",
        "--issue-ref",
        "201",
        cwd=tmp_path,
        check=True,
    )
    assert _status(run_cli, tmp_path, "late-388")["planned"] == ["201"]
    child = _init_child(
        run_cli,
        tmp_path,
        group_id="late-388",
        issue_ref="201",
        session_id="planned-late",
    )
    assert _status(run_cli, tmp_path, "late-388")["running"] == ["201"]
    _update_state(
        child,
        loop_active=False,
        passes=True,
        lease_expires_at="2000-01-01T00:00:00Z",
    )
    assert _status(run_cli, tmp_path, "late-388")["pass"] == ["201"]

    _init_child(
        run_cli,
        tmp_path,
        group_id="late-388",
        issue_ref="999",
        session_id="unplanned",
    )
    status = _status(run_cli, tmp_path, "late-388")
    assert status["late_children"] == ["999"]
    assert run_cli("parallel-closeout", "--group-id", "late-388", cwd=tmp_path).returncode == 2


def test_parallel_closeout_rejects_active_lease_without_writing_manifest(run_cli, tmp_path):
    run_cli(
        "parallel-init",
        "--group-id",
        "lease-388",
        "--issue-ref",
        "388",
        cwd=tmp_path,
        check=True,
    )
    child = _init_child(
        run_cli,
        tmp_path,
        group_id="lease-388",
        issue_ref="388",
        session_id="leased",
    )
    _update_state(child, loop_active=False, passes=True)
    manifest_path = _manifest_path(tmp_path, "lease-388")
    before = manifest_path.read_bytes()
    status = _status(run_cli, tmp_path, "lease-388")
    assert status["active_leases"] == ["388"]
    rejected = run_cli("parallel-closeout", "--group-id", "lease-388", cwd=tmp_path)
    assert rejected.returncode == 2
    assert manifest_path.read_bytes() == before


def test_parallel_closeout_persists_actual_coverage_and_terminal_outcome(run_cli, tmp_path):
    refs = [str(400 + index) for index in range(10)]
    args = ["parallel-init", "--group-id", "coverage-388"]
    for ref in refs:
        args.extend(("--issue-ref", ref))
    run_cli(*args, cwd=tmp_path, check=True)

    for index, ref in enumerate(refs):
        path = _init_child(
            run_cli,
            tmp_path,
            group_id="coverage-388",
            issue_ref=ref,
            session_id=f"s{ref}",
        )
        changes = {
            "loop_active": False,
            "passes": True,
            "lease_expires_at": "2000-01-01T00:00:00Z",
        }
        if index < 9:
            changes.update(
                {
                    "artifact_applicability": "producing",
                    "artifact": {"path": f"reports/{ref}.md"},
                    "activity_segments": [
                        {
                            "kind": "active",
                            "phase": "executing",
                            "reason": "implementation",
                            "started_at": "2026-08-11T00:00:00Z",
                            "ended_at": "2026-08-11T00:01:00Z",
                            "duration_sec": 60.0,
                        }
                    ],
                    "score_history": [
                        {
                            "score_provenance": {
                                "score_source": "scoring-json",
                                "review_evidence_ref": {
                                    "kind": "review-aggregate",
                                    "path": f".mission-state/archive/{ref}.json",
                                    "digest": "sha256:" + "a" * 64,
                                    "generation": "a" * 16,
                                    "revision_scope": {
                                        "kind": "git",
                                        "base_sha": "b" * 40,
                                        "head_sha": "c" * 40,
                                    },
                                },
                                "revision_scope": {
                                    "kind": "git",
                                    "base_sha": "b" * 40,
                                    "head_sha": "c" * 40,
                                },
                            }
                        }
                    ],
                }
            )
        else:
            changes.update(
                {
                    "artifact_applicability": "pending",
                    "artifact": {},
                    "activity_segments": [],
                    "score_history": [{"score_provenance": {}}],
                }
            )
        _update_state(path, **changes)

    status = _status(run_cli, tmp_path, "coverage-388")
    for dimension in ("artifact", "activity", "review_provenance"):
        assert status["coverage"][dimension] == {"observed": 9, "eligible": 10, "ratio": 0.9}
    assert status["coverage"]["ratio"] == 0.9

    closed = run_cli("parallel-closeout", "--group-id", "coverage-388", cwd=tmp_path)
    assert closed.returncode == 0, closed.stderr
    manifest = json.loads(_manifest_path(tmp_path, "coverage-388").read_text())
    assert manifest["status"] == "terminal"
    assert manifest["outcome"] == "pass"
    assert manifest["coverage"] == status["coverage"]


def test_parallel_manifest_rejects_malformed_duplicate_and_unsafe_files(run_cli, tmp_path):
    run_cli(
        "parallel-init",
        "--group-id",
        "unsafe-388",
        "--issue-ref",
        "388",
        cwd=tmp_path,
        check=True,
    )
    manifest_path = _manifest_path(tmp_path, "unsafe-388")
    manifest = json.loads(manifest_path.read_text())
    manifest["planned_children"].append({"issue_ref": "#388"})
    manifest_path.write_text(json.dumps(manifest))
    assert run_cli("parallel-status", "--group-id", "unsafe-388", cwd=tmp_path).returncode == 2

    manifest["planned_children"] = [{"issue_ref": "388"}]
    manifest_path.write_text(json.dumps(manifest))
    backing = manifest_path.with_name("backing.json")
    manifest_path.rename(backing)
    manifest_path.symlink_to(backing.name)
    assert run_cli("parallel-status", "--group-id", "unsafe-388", cwd=tmp_path).returncode == 2
    manifest_path.unlink()
    os.link(backing, manifest_path)
    assert run_cli("parallel-status", "--group-id", "unsafe-388", cwd=tmp_path).returncode == 2


def test_parallel_status_ignores_legacy_sessions_without_group(run_cli, tmp_path):
    result = run_cli(
        "init",
        "legacy child",
        "--force-mission",
        "--issue-ref",
        "388",
        cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "legacy"},
    )
    assert result.returncode == 0
    run_cli(
        "parallel-init",
        "--group-id",
        "typed-388",
        "--issue-ref",
        "388",
        cwd=tmp_path,
        check=True,
    )
    status = _status(run_cli, tmp_path, "typed-388")
    assert status["planned"] == ["388"]
    assert status["late_children"] == []
