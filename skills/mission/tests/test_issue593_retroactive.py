"""#593 追補: digest を持たない過去 state を head_sha で遡及分類する。

artifact_digest は #593 以降の run にしか無い。しかし score_history の
各エントリは `revision_scope.head_sha` を持っており、commit を伴う mission
なら iteration 間の head_sha 変化が「作業が実際に変わったか」の代理指標に
なる (ローカルの本番 state で変化を実測済み)。

**推定であることを明示し、digest ベースの判定と混ぜない。**
信頼度の違う数字を同じ箱に入れると、集計値の意味が壊れる。
"""

from mission_gate_outcome import (
    CHANGED_NO_GAIN,
    IMPROVED,
    INFERRED_SUFFIX,
    NO_CHANGE,
    UNKNOWN,
    classify_transition,
    summarize_states,
)


def _digest_entry(iteration, composite, digest):
    return {"iteration": iteration, "composite": composite, "artifact_digest": digest}


def _sha_entry(iteration, composite, head_sha):
    return {
        "iteration": iteration,
        "composite": composite,
        "revision_scope": {"kind": "git", "base_sha": "b" * 40, "head_sha": head_sha},
    }


def test_head_sha_change_infers_improvement_when_score_rises():
    outcome = classify_transition(_sha_entry(1, 3.0, "aaa"), _sha_entry(2, 4.5, "bbb"))
    assert outcome == IMPROVED + INFERRED_SUFFIX


def test_identical_head_sha_infers_no_change():
    outcome = classify_transition(_sha_entry(1, 4.0, "aaa"), _sha_entry(2, 4.0, "aaa"))
    assert outcome == NO_CHANGE + INFERRED_SUFFIX


def test_head_sha_change_without_gain_is_inferred_changed_no_gain():
    outcome = classify_transition(_sha_entry(1, 4.2, "aaa"), _sha_entry(2, 4.0, "bbb"))
    assert outcome == CHANGED_NO_GAIN + INFERRED_SUFFIX


def test_digest_takes_precedence_over_head_sha():
    """digest がある側を優先する。推定へ格下げしない。"""
    prev = dict(_digest_entry(1, 4.0, "sha256:a"),
                revision_scope={"kind": "git", "head_sha": "aaa"})
    curr = dict(_digest_entry(2, 4.6, "sha256:b"),
                revision_scope={"kind": "git", "head_sha": "aaa"})
    assert classify_transition(prev, curr) == IMPROVED


def test_missing_head_sha_stays_unknown():
    prev = {"iteration": 1, "composite": 4.0, "revision_scope": {"kind": "git"}}
    curr = {"iteration": 2, "composite": 4.1, "revision_scope": {"kind": "git"}}
    assert classify_transition(prev, curr) == UNKNOWN


def test_non_git_revision_scope_is_not_used_for_inference():
    """git 以外の revision_scope を作業変化の代理指標にしない。"""
    prev = {"iteration": 1, "composite": 4.0, "revision_scope": {"kind": "none", "head_sha": "aaa"}}
    curr = {"iteration": 2, "composite": 4.5, "revision_scope": {"kind": "none", "head_sha": "bbb"}}
    assert classify_transition(prev, curr) == UNKNOWN


def test_summary_separates_measured_from_inferred():
    """推定を実測と混ぜない。集計値の意味が壊れるため。"""
    states = [
        {"mission": "measured-fp", "score_history": [
            _digest_entry(1, 4.0, "sha256:a"), _digest_entry(2, 4.0, "sha256:a")]},
        {"mission": "inferred-fp", "score_history": [
            _sha_entry(1, 4.0, "aaa"), _sha_entry(2, 4.0, "aaa")]},
        {"mission": "inferred-tp", "score_history": [
            _sha_entry(1, 3.0, "aaa"), _sha_entry(2, 4.6, "bbb")]},
    ]
    summary = summarize_states(states)

    # 実測のみを主指標に使う
    assert summary["missions_classifiable"] == 1
    assert summary["counts"][NO_CHANGE] == 1
    assert summary["false_positive_rate"] == 1.0

    inferred = summary["inferred"]
    assert inferred["missions_classifiable"] == 2
    assert inferred["counts"][NO_CHANGE] == 1
    assert inferred["counts"][IMPROVED] == 1
    assert inferred["false_positive_rate"] == 0.5


def test_inferred_summary_is_absent_of_measured_missions():
    """実測で分類できた mission を推定側に二重計上しない。"""
    states = [{"mission": "m", "score_history": [
        _digest_entry(1, 4.0, "sha256:a"), _digest_entry(2, 4.0, "sha256:a")]}]
    summary = summarize_states(states)
    assert summary["inferred"]["missions_classifiable"] == 0
