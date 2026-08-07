"""#326: critic scope 強制の hard gate 化.

disc-v3 (2026-08-02) で orchestrator が iter2 到達時に next を呼ばず review へ進み、
#309 の guidance 強制が bypass された (critic_has_new_scope=None のまま完走)。
guidance は「next を呼ぶ」規律に依存するため、集計側で fail-closed に強制する。

Contract under test:
1. aggregate-reviews: iteration >= 2 + state の critic_has_new_scope 未記録 → exit 2
2. 記録済み (false/true) → 従来どおり集計成功
3. iteration 1 → gate 対象外 (従来どおり)
4. escape hatch は存在しない (エラーメッセージが set コマンドを案内する)
"""

import json
from pathlib import Path


TEST_SID = "test-326"


def _make_state(tmp_path, *, iteration=2, critic_has_new_scope="ABSENT"):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    d = {
        "mission": "m", "mission_id": "hg1", "pid": 12345,
        "loop_active": True, "passes": False, "halt_reason": "",
        "phase": "reviewing", "iteration": iteration, "reviewer_count": 2,
        "project_root": str(tmp_path),
    }
    if critic_has_new_scope != "ABSENT":
        d["critic_has_new_scope"] = critic_has_new_scope
    (sessions / f"{TEST_SID}.json").write_text(json.dumps(d))


def _write_review(path, perspective, iteration):
    review = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": iteration,
        "scores": {"mission_achievement": 3.0, "accuracy": 3.5,
                   "completeness": 3.0, "usability": 4.0},
        "findings": [],
    }
    path.write_text(json.dumps(review))
    return path


def _run_aggregate(run_cli, tmp_path, iteration):
    r1 = _write_review(tmp_path / "r1.json", "A", iteration)
    r2 = _write_review(tmp_path / "r2.json", "B", iteration)
    return run_cli(
        "aggregate-reviews", "--iteration", str(iteration),
        "--input", str(r1), "--input", str(r2),
        "--out", str(tmp_path / "out.json"), "--json",
        "--reviewer-window", "A=2026-08-02T10:00:00Z..2026-08-02T10:05:00Z",
        "--reviewer-window", "B=2026-08-02T10:00:30Z..2026-08-02T10:04:00Z",
        cwd=tmp_path, env_extra={"MISSION_SESSION_ID": TEST_SID},
    )


def test_iter2_unrecorded_scope_rejected(run_cli, tmp_path):
    _make_state(tmp_path, iteration=2)
    r = _run_aggregate(run_cli, tmp_path, 2)
    assert r.returncode == 2, f"exit 2 expected, got {r.returncode}: {r.stderr}"
    assert "critic_has_new_scope" in r.stderr


def test_iter2_recorded_false_accepted(run_cli, tmp_path):
    _make_state(tmp_path, iteration=2, critic_has_new_scope=False)
    r = _run_aggregate(run_cli, tmp_path, 2)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "out.json").exists()


def test_iter2_recorded_true_accepted(run_cli, tmp_path):
    _make_state(tmp_path, iteration=2, critic_has_new_scope=True)
    r = _run_aggregate(run_cli, tmp_path, 2)
    assert r.returncode == 0, r.stderr


def test_iter1_not_gated(run_cli, tmp_path):
    _make_state(tmp_path, iteration=1)
    r = _run_aggregate(run_cli, tmp_path, 1)
    assert r.returncode == 0, r.stderr


def test_error_message_guides_set_command(run_cli, tmp_path):
    _make_state(tmp_path, iteration=2)
    r = _run_aggregate(run_cli, tmp_path, 2)
    assert "set critic_has_new_scope" in r.stderr
