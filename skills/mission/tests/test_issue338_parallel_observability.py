"""#338: レビュアー並列実行の実効化 (検証可能性の強化).

portfolio-v4 (2026-08-02) で Standard 3 run すべて API 時間 ≒ wall 時間 —
reviewer は直列実行されていた。guidance 文言 (#282) だけでは実行様式を変えられ
なかったため、(1) windows 未申告への WARN、(2) 観測結果の state 永続化、
(3) stats 集計、(4) next details のフラグ化で検証可能性を上げる。

Contract under test:
1. aggregate-reviews: reviewer >= 2 で windows 未申告 → WARN (#338)、exit 0
2. 観測結果 (true/false/unknown) を state の last_parallel_execution へ永続化
3. stats に parallel_review_counts (true/false/unknown) を集計
4. next (phase=reviewing) の details に parallel_spawn_required: true
"""

import json
from pathlib import Path


TEST_SID = "test-338"


def _make_state(tmp_path, *, phase="reviewing", iteration=1):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    d = {
        "mission": "m", "mission_id": "pl1", "pid": 12345,
        "loop_active": True, "passes": False, "halt_reason": "",
        "phase": phase, "iteration": iteration, "reviewer_count": 2,
        "project_root": str(tmp_path),
    }
    (sessions / f"{TEST_SID}.json").write_text(json.dumps(d))
    return sessions / f"{TEST_SID}.json"


def _write_review(path, perspective, iteration=1):
    review = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": iteration,
        "scores": {"mission_achievement": 4.0, "accuracy": 4.5,
                   "completeness": 4.0, "usability": 4.0},
        "findings": [],
    }
    path.write_text(json.dumps(review))
    return path


def _aggregate(run_cli, tmp_path, *windows):
    r1 = _write_review(tmp_path / "r1.json", "A")
    r2 = _write_review(tmp_path / "r2.json", "B")
    args = ["aggregate-reviews", "--iteration", "1",
            "--input", str(r1), "--input", str(r2),
            "--out", str(tmp_path / "out.json"), "--json"]
    for w in windows:
        args += ["--reviewer-window", w]
    return run_cli(*args, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": TEST_SID})


# ===== 1. windows 未申告 WARN =====

def test_unreported_windows_warns(run_cli, tmp_path):
    _make_state(tmp_path)
    result = _aggregate(run_cli, tmp_path)
    assert result.returncode == 0
    assert "#338" in result.stderr
    assert "--reviewer-window" in result.stderr


def test_reported_windows_no_338_warn(run_cli, tmp_path):
    _make_state(tmp_path)
    result = _aggregate(
        run_cli, tmp_path,
        "A=2026-08-02T10:00:00Z..2026-08-02T10:05:00Z",
        "B=2026-08-02T10:00:30Z..2026-08-02T10:04:00Z",
    )
    assert result.returncode == 0
    assert "#338" not in result.stderr


# ===== 2. state 永続化 =====

def _read_state(sf):
    return json.loads(sf.read_text())


def test_parallel_windows_persist_true(run_cli, tmp_path):
    sf = _make_state(tmp_path)
    result = _aggregate(
        run_cli, tmp_path,
        "A=2026-08-02T10:00:00Z..2026-08-02T10:05:00Z",
        "B=2026-08-02T10:00:30Z..2026-08-02T10:04:00Z",
    )
    assert result.returncode == 0
    assert _read_state(sf)["last_parallel_execution"] is True


def test_serial_windows_persist_false(run_cli, tmp_path):
    sf = _make_state(tmp_path)
    result = _aggregate(
        run_cli, tmp_path,
        "A=2026-08-02T10:00:00Z..2026-08-02T10:05:00Z",
        "B=2026-08-02T10:06:00Z..2026-08-02T10:10:00Z",
    )
    assert result.returncode == 0
    assert _read_state(sf)["last_parallel_execution"] is False


def test_unreported_windows_persist_unknown(run_cli, tmp_path):
    sf = _make_state(tmp_path)
    result = _aggregate(run_cli, tmp_path)
    assert result.returncode == 0
    assert _read_state(sf)["last_parallel_execution"] == "unknown"


# ===== 3. stats 集計 =====

def test_stats_counts_parallel_review(run_cli, tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    base = {"mission": "m", "pid": 1, "loop_active": False, "passes": True,
            "halt_reason": "", "project_root": str(tmp_path)}
    for i, lpe in enumerate([True, False, False, "unknown"]):
        d = dict(base, mission_id=f"s{i}", session_id=f"s{i}", last_parallel_execution=lpe)
        (sessions / f"s{i}.json").write_text(json.dumps(d))
    (sessions / "s-none.json").write_text(json.dumps(dict(base, mission_id="sn", session_id="sn")))
    result = run_cli("stats", "--json", "--root", str(tmp_path), cwd=tmp_path)
    assert result.returncode == 0
    stats = json.loads(result.stdout)
    assert stats["parallel_review_counts"] == {"true": 1, "false": 2, "unknown": 1}


# ===== 4. next details =====

def test_next_run_reviewers_flags_parallel_required(run_cli, tmp_path):
    _make_state(tmp_path, phase="reviewing", iteration=1)
    result = run_cli("next", cwd=tmp_path,
                     env_extra={"MISSION_SESSION_ID": TEST_SID})
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["next_action"] == "run-reviewers"
    assert payload["details"]["parallel_spawn_required"] is True
