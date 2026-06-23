import json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WRAPPER = REPO_ROOT / "scripts" / "mission-state.py"


def _json_result(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_progress_update_archives_checkpoint_and_keeps_state_machine_active(state_dir, run_cli, read_state):
    r = run_cli(
        "progress", "update",
        "--total", "7",
        "--completed", "3",
        "--batch-size", "2",
        "--last-unit", "issue-43",
        "--artifact", "output/progress.md",
        "--json",
        cwd=state_dir.parent,
    )

    data = _json_result(r)
    state = read_state(state_dir)
    progress = state["progress"]
    evidence = state_dir.parent / progress["evidence_path"]
    assert data["progress"]["remaining"] == 4
    assert progress["completed"] == 3
    assert progress["remaining"] == 4
    assert progress["artifact_path"] == "output/progress.md"
    assert evidence.exists()
    assert "Mission Progress Checkpoint" in evidence.read_text(encoding="utf-8")
    assert state["loop_active"] is True
    assert state["passes"] is False


def test_progress_get_and_clear_roundtrip(state_dir, run_cli, read_state):
    run_cli(
        "progress", "update",
        "--total", "5",
        "--completed", "5",
        "--json",
        cwd=state_dir.parent,
        check=True,
    )

    got = _json_result(run_cli("progress", "get", "--json", cwd=state_dir.parent))
    assert got["progress"]["completed"] == 5

    run_cli("progress", "clear", "--json", cwd=state_dir.parent, check=True)

    assert "progress" not in read_state(state_dir)


def test_stable_wrapper_delegates_to_canonical_mission_state_cli(tmp_path):
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "init", "wrapper smoke", "--complexity", "Simple"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={"MISSION_SESSION_ID": "wrapper-test"},
    )

    assert result.returncode == 0, result.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / "wrapper-test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["mission"] == "wrapper smoke"


def test_audit_slow_session_line_includes_progress_checkpoint(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    sessions.joinpath("slow.json").write_text(json.dumps({
        "mission": "long batch mission",
        "mission_id": "slow1234",
        "session_id": "slow",
        "project_root": str(tmp_path),
        "agent": "codex",
        "passes": False,
        "loop_active": True,
        "started_at": "2026-06-20T10:00:00Z",
        "updated_at": "2026-06-20T11:00:00Z",
        "progress": {
            "kind": "batch",
            "total": 10,
            "completed": 4,
            "remaining": 6,
            "updated_at": "2026-06-20T10:30:00Z",
        },
    }), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "mission-audit.py"),
            "--root",
            str(tmp_path),
            "--slow-threshold-sec",
            "60",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert re.search(r"`slow` .*progress 4/10 remaining 6", result.stdout)
