"""#283: transactional コマンド review-finalize / closeout.

mission run は 52-56 turns (goal 比 6x) で、1 turn ≈ 107K context の再処理を伴う。
Phase 5 の aggregate-reviews → push-score、Phase 6 の mark-passes → next の
頻出連鎖を 1 コマンド化して orchestration turn を削減する。
既存 validator をそのまま内部呼び出しし、gate 意味論は不変。
"""

from __future__ import annotations

import hashlib
import json


def _review(tmp_path, name, *, perspective="A", scores=None):
    payload = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": 1,
        "scores": scores if scores is not None else {
            "mission_achievement": 4.6,
            "accuracy": 4.4,
            "completeness": 4.2,
            "usability": 4.0,
        },
        "findings": [],
        "same_score_note": None,
        "notes": f"{perspective} review",
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _two_reviews(tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    b = _review(tmp_path, "b.json", perspective="B", scores={
        "mission_achievement": 4.4,
        "accuracy": 4.2,
        "completeness": 4.0,
        "usability": 3.8,
    })
    return a, b


def _reviewer_windows():
    return (
        "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
        "--reviewer-window", "B=2026-07-25T10:00:30Z..2026-07-25T10:04:00Z",
    )


# ===== review-finalize =====


def test_review_finalize_aggregates_and_pushes_in_one_command(state_dir, run_cli, read_state, tmp_path):
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    r = run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
                "--out", str(out), "--min-reviewers", "2", *_reviewer_windows(), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["ok"] is True
    assert result["aggregate"]["out"] == str(out)
    entry = read_state(state_dir)["score_history"][0]
    assert entry["score_source"] == "scoring-json"
    assert entry["composite"] == result["push"]["appended"]["composite"]
    assert entry["items"]["mission_achievement"] == 4.5


def test_review_finalize_min_reviewers_failure_is_atomic(state_dir, run_cli, read_state, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")

    r = run_cli("review-finalize", "--iteration", "1", "--input", str(a),
                "--min-reviewers", "2", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "reviewer 数不足" in r.stderr
    # 集計に失敗したら score は push されない (atomic)
    assert read_state(state_dir)["score_history"] == []


def test_review_finalize_passes_reviewer_windows_through(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)

    r = run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
                "--reviewer-window", "A=2026-07-25T10:00:00Z..2026-07-25T10:05:00Z",
                "--reviewer-window", "B=2026-07-25T10:06:00Z..2026-07-25T10:10:00Z",
                cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert "WARN" in r.stderr and "直列" in r.stderr
    assert json.loads(r.stdout)["aggregate"]["parallel_execution"] is False


def test_review_finalize_reviewer_window_gate_emits_own_typed_outcome_once(state_dir, run_cli, tmp_path):
    a, b = _two_reviews(tmp_path)
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
        "--event-id", "finalize-event", "--root-event-id", "finalize-root", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "expected-gate"
    assert payload["outcome"]["command"] == "review-finalize"
    assert payload["outcome"]["event_id"] == "finalize-event"
    assert state_file.read_bytes() == before
    token = hashlib.sha256(b"test").hexdigest()[:16]
    sidecar = json.loads(
        (state_dir / "telemetry" / "command-outcomes" / f"{token}.json").read_text(encoding="utf-8")
    )
    assert sidecar["records"] == [payload["outcome"]]


def test_review_finalize_nested_invalid_review_maps_to_invalid_input(state_dir, run_cli, tmp_path):
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema":"wrong"}', encoding="utf-8")
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "review-finalize", "--iteration", "1", "--input", str(invalid),
        "--event-id", "finalize-invalid", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "invalid-input"
    assert payload["outcome"]["command"] == "review-finalize"
    assert state_file.read_bytes() == before


def test_review_finalize_gate_values_match_split_commands(state_dir, run_cli, read_state, tmp_path):
    """review-finalize の gate 値は aggregate-reviews → push-score 分割実行と一致する."""
    a, b = _two_reviews(tmp_path)
    out = tmp_path / "scoring.json"

    run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
            "--out", str(out), *_reviewer_windows(), cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]

    assert entry["composite"] == 4.2
    assert entry["min_item"] == 3.9
    assert entry["open_high"] == 0
    assert entry["review_agreement"] == 5.0
    assert entry["findings_evidence_path"]


# ===== closeout =====


def _push_passing_score(run_cli, state_dir, tmp_path):
    a, b = _two_reviews(tmp_path)
    run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
            *_reviewer_windows(), cwd=state_dir.parent, check=True)


def test_closeout_marks_passes_and_returns_next(state_dir, run_cli, read_state, tmp_path):
    _push_passing_score(run_cli, state_dir, tmp_path)

    r = run_cli("closeout", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    assert result["ok"] is True
    assert result["mark_passes"]["passes"] is True
    assert result["next"]["next_action"] == "report-complete"
    data = read_state(state_dir)
    assert data["passes"] is True
    assert data["loop_active"] is False


def test_closeout_gate_failure_keeps_state_and_returns_guidance(state_dir, run_cli, read_state, tmp_path):
    low = _review(tmp_path, "low.json", perspective="A", scores={
        "mission_achievement": 3.0,
        "accuracy": 3.2,
        "completeness": 3.4,
        "usability": 3.6,
    })
    run_cli("review-finalize", "--iteration", "1", "--input", str(low), cwd=state_dir.parent, check=True)

    r = run_cli("closeout", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "threshold" in r.stderr
    result = json.loads(r.stdout)
    assert result["ok"] is False
    assert "next" in result and result["next"]["next_action"]
    data = read_state(state_dir)
    assert data["passes"] is False
    assert data["loop_active"] is True


def test_closeout_without_score_fails_closed(state_dir, run_cli, read_state, tmp_path):
    r = run_cli("closeout", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "採点未実施" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_closeout_rejects_force_flag(state_dir, run_cli, tmp_path):
    """closeout は標準経路専用。override は mark-passes --force を直接使う."""
    r = run_cli("closeout", "--force", cwd=state_dir.parent)

    assert r.returncode == 2


def test_review_finalize_push_failure_after_aggregate_keeps_history(state_dir, run_cli, read_state, tmp_path):
    """aggregate 成功 → push-score 失敗 (同一 iteration 再 push を --resubmit-reason なし) でも
    score_history は増えない (#122 の再 push 保護が review-finalize 経由でも効く)."""
    a, b = _two_reviews(tmp_path)
    run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
            *_reviewer_windows(), cwd=state_dir.parent, check=True)

    r = run_cli("review-finalize", "--iteration", "1", "--input", str(a), "--input", str(b),
                *_reviewer_windows(), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "resubmit-reason" in r.stderr
    assert len(read_state(state_dir)["score_history"]) == 1
