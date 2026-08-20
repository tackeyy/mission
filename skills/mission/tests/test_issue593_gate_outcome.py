"""#593 B-2/B-3/B-4: gate outcome の分類と集計。

本番 451 mission のうち「反復したが composite 不変」15 件は、現在
  - ゲートの誤検知 (弾いたが直すものが無い)
  - 修正の失敗 (直したが改善しない)
が混ざっている。B-1 の artifact_digest を使って両者を機械的に分離する。
"""

import pytest

from mission_gate_outcome import (
    CHANGED_NO_GAIN,
    IMPROVED,
    NO_CHANGE,
    UNKNOWN,
    classify_transition,
    classify_state,
    false_negative_summary,
    summarize_states,
)


def _entry(iteration, composite, digest, *, status="ok"):
    return {
        "iteration": iteration,
        "composite": composite,
        "artifact_digest": digest,
        "artifact_digest_status": status,
    }


# --- B-2: transition の分類 --------------------------------------------------

def test_artifact_changed_and_score_improved_is_true_positive():
    assert classify_transition(_entry(1, 3.2, "sha256:a"), _entry(2, 4.4, "sha256:b")) == IMPROVED


def test_artifact_changed_but_score_flat_is_changed_no_gain():
    assert classify_transition(_entry(1, 4.0, "sha256:a"), _entry(2, 4.0, "sha256:b")) == CHANGED_NO_GAIN


def test_artifact_changed_but_score_regressed_is_changed_no_gain():
    assert classify_transition(_entry(1, 4.2, "sha256:a"), _entry(2, 3.9, "sha256:b")) == CHANGED_NO_GAIN


def test_artifact_unchanged_is_false_positive_candidate():
    """弾いたのに直すものが無かった = ゲートの誤検知の強い候補。"""
    assert classify_transition(_entry(1, 4.0, "sha256:a"), _entry(2, 4.0, "sha256:a")) == NO_CHANGE


def test_artifact_unchanged_but_score_moved_is_still_no_change():
    """成果物が変わっていないのにスコアだけ動くのは採点のばらつき。

    修正の成果ではないので改善として数えない。
    """
    assert classify_transition(_entry(1, 3.8, "sha256:a"), _entry(2, 4.3, "sha256:a")) == NO_CHANGE


@pytest.mark.parametrize("prev_digest,curr_digest", [
    (None, "sha256:b"),
    ("sha256:a", None),
    (None, None),
])
def test_missing_digest_is_unknown_not_guessed(prev_digest, curr_digest):
    """digest が無い過去 state を推定で埋めない。"""
    prev = _entry(1, 4.0, prev_digest, status="not-configured")
    curr = _entry(2, 4.1, curr_digest, status="not-configured")
    assert classify_transition(prev, curr) == UNKNOWN


def test_missing_composite_is_unknown():
    prev = {"iteration": 1, "artifact_digest": "sha256:a", "composite": None}
    curr = {"iteration": 2, "artifact_digest": "sha256:b", "composite": 4.0}
    assert classify_transition(prev, curr) == UNKNOWN


# --- state 単位 --------------------------------------------------------------

def test_single_iteration_state_has_no_transitions():
    state = {"score_history": [_entry(1, 4.5, "sha256:a")]}
    result = classify_state(state)
    assert result["transitions"] == []
    assert result["mission_outcome"] is None


def test_state_with_multiple_transitions_reports_each():
    state = {"score_history": [
        _entry(1, 3.0, "sha256:a"),
        _entry(2, 3.0, "sha256:a"),
        _entry(3, 4.6, "sha256:b"),
    ]}
    result = classify_state(state)
    assert [t["outcome"] for t in result["transitions"]] == [NO_CHANGE, IMPROVED]


def test_mission_outcome_prefers_improved_over_other_outcomes():
    """1 度でも改善していればそのミッションは改善したと扱う。"""
    state = {"score_history": [
        _entry(1, 3.0, "sha256:a"),
        _entry(2, 4.6, "sha256:b"),
        _entry(3, 4.6, "sha256:b"),
    ]}
    assert classify_state(state)["mission_outcome"] == IMPROVED


def test_empty_or_malformed_history_does_not_raise():
    for state in ({}, {"score_history": None}, {"score_history": "nope"},
                  {"score_history": [None, 3]}):
        result = classify_state(state)
        assert result["transitions"] == []


# --- B-3: 集計 ---------------------------------------------------------------

def test_summarize_counts_and_lists_false_positive_candidates():
    states = [
        {"mission": "m1", "score_history": [_entry(1, 3.0, "sha256:a"), _entry(2, 4.5, "sha256:b")]},
        {"mission": "m2", "score_history": [_entry(1, 4.0, "sha256:c"), _entry(2, 4.0, "sha256:c")]},
        {"mission": "m3", "score_history": [_entry(1, 4.0, "sha256:d"), _entry(2, 4.0, "sha256:e")]},
        {"mission": "m4", "score_history": [_entry(1, 4.9, "sha256:f")]},
    ]
    summary = summarize_states(states)
    assert summary["missions_total"] == 4
    assert summary["missions_multi_iteration"] == 3
    assert summary["counts"][IMPROVED] == 1
    assert summary["counts"][NO_CHANGE] == 1
    assert summary["counts"][CHANGED_NO_GAIN] == 1
    assert [c["mission"] for c in summary["false_positive_candidates"]] == ["m2"]


def test_summarize_handles_zero_states_without_dividing_by_zero():
    summary = summarize_states([])
    assert summary["missions_total"] == 0
    assert summary["false_positive_rate"] is None


def test_false_positive_rate_is_over_multi_iteration_missions():
    """FP 率の母数は「ゲートが発火したミッション」であり全ミッションではない。"""
    states = [
        {"mission": "m1", "score_history": [_entry(1, 4.0, "sha256:a"), _entry(2, 4.0, "sha256:a")]},
        {"mission": "m2", "score_history": [_entry(1, 3.0, "sha256:b"), _entry(2, 4.5, "sha256:c")]},
        {"mission": "m3", "score_history": [_entry(1, 4.9, "sha256:d")]},
    ]
    summary = summarize_states(states)
    assert summary["missions_multi_iteration"] == 2
    assert summary["false_positive_rate"] == 0.5


# --- B-4: false negative は測れないことを明示する ------------------------------

def test_fp_rate_is_none_when_nothing_is_classifiable():
    """判定不能しか無いのに 0.0 を返さない。

    `unknown` だけの母集団で rate 0.0 を出すと「FP なし」に読めるが、
    実際は「判定できない」。意味のない数字を意味ありげに出さない。
    """
    legacy = {"iteration": 1, "composite": 4.0}
    states = [{"mission": "legacy", "score_history": [
        dict(legacy, iteration=1), dict(legacy, iteration=2)]}]
    summary = summarize_states(states)
    assert summary["missions_multi_iteration"] == 1
    assert summary["missions_classifiable"] == 0
    assert summary["counts"][UNKNOWN] == 1
    assert summary["false_positive_rate"] is None


def test_fp_rate_denominator_excludes_unknown_missions():
    """FP 率の母数は判定できたミッションに限る。"""
    states = [
        {"mission": "fp", "score_history": [
            _entry(1, 4.0, "sha256:a"), _entry(2, 4.0, "sha256:a")]},
        {"mission": "tp", "score_history": [
            _entry(1, 3.0, "sha256:b"), _entry(2, 4.6, "sha256:c")]},
        {"mission": "legacy", "score_history": [
            {"iteration": 1, "composite": 4.0}, {"iteration": 2, "composite": 4.0}]},
    ]
    summary = summarize_states(states)
    assert summary["missions_multi_iteration"] == 3
    assert summary["missions_classifiable"] == 2
    assert summary["false_positive_rate"] == 0.5


def test_false_negative_is_reported_as_unmeasurable_without_verification():
    """ground truth が無いものを推定で埋めない。"""
    states = [{"mission": "m1", "score_history": [_entry(1, 4.5, "sha256:a")]}]
    fn = false_negative_summary(states)
    assert fn["status"] == "unmeasurable"
    assert fn["count"] is None
    assert "verification" in fn["reason"]


# --- B-3: mission-audit.py への配線 -------------------------------------------

import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"


def _write_mission_state(path, mission, history):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "mission": mission,
        "mission_id": "abc123456789",
        "complexity": "Standard",
        "iteration": history[-1]["iteration"],
        "threshold": 4.0,
        "score_history": history,
        "loop_active": False,
        "passes": True,
        "halt_reason": "",
        "started_at": "2026-06-18T00:00:00Z",
        "updated_at": "2026-06-18T00:10:00Z",
        "project_root": str(path.parents[2]),
        "session_id": path.stem,
        "agent": "codex",
    }), encoding="utf-8")


def _run_audit(tmp_path):
    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--json"],
        capture_output=True, text=True, cwd=str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _hist(iteration, composite, digest):
    return {
        "iteration": iteration, "composite": composite, "min_item": composite,
        "items": {}, "timestamp": f"2026-06-18T00:0{iteration}:00Z",
        "artifact_digest": digest, "artifact_digest_status": "ok",
    }


def test_audit_reports_gate_outcome_section(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    _write_mission_state(sessions / "fp.json", "gate fired but nothing changed",
                         [_hist(1, 4.0, "sha256:a"), _hist(2, 4.0, "sha256:a")])
    _write_mission_state(sessions / "tp.json", "gate fired and artifact improved",
                         [_hist(1, 3.0, "sha256:b"), _hist(2, 4.6, "sha256:c")])

    stats = _run_audit(tmp_path)
    gate = stats["gate_outcome"]
    assert gate["missions_multi_iteration"] == 2
    assert gate["counts"]["no-change"] == 1
    assert gate["counts"]["improved"] == 1
    assert gate["false_positive_rate"] == 0.5
    assert gate["false_negative"]["status"] == "unmeasurable"


def test_audit_gate_outcome_absent_digest_is_unknown_not_guessed(tmp_path):
    """digest を持たない過去 state を推定で分類しない。"""
    sessions = tmp_path / ".mission-state" / "sessions"
    legacy = [
        {"iteration": 1, "composite": 4.0, "min_item": 4.0, "items": {}, "timestamp": "2026-06-18T00:01:00Z"},
        {"iteration": 2, "composite": 4.0, "min_item": 4.0, "items": {}, "timestamp": "2026-06-18T00:02:00Z"},
    ]
    _write_mission_state(sessions / "legacy.json", "legacy state without digest", legacy)

    gate = _run_audit(tmp_path)["gate_outcome"]
    assert gate["counts"]["unknown"] == 1
    assert gate["counts"]["no-change"] == 0
    # 判定できていないので率を主張しない (0.0 だと「FP なし」に読める)
    assert gate["missions_classifiable"] == 0
    assert gate["false_positive_rate"] is None
