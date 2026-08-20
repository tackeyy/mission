"""#594 A: reviewer 起動前の verification 結果を記録する。

reviewer には検証の**能力**はある (allowed-tools に pytest / npm test / Grep)
が、**義務**がない。実測では反復は 5.6% (5/90) でしか起きておらず、
捕まえている欠陥も「注意深く読めば気づく」類に限られている。

reviewer は executor と同じ基底モデルであり、読むだけなら同じ盲点で 2 回
見ている懸念がある。テスト実行・存在確認・再計算は**モデルの意見ではなく
事実**であり、真の独立性を生む。

本テストは検証結果の記録と、それが gate を変えないことを固定する。
"""

import json

import pytest

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate

ITEMS = {
    "mission_achievement": 4.0,
    "accuracy": 4.5,
    "completeness": 4.0,
    "usability": 3.5,
}


def _verification(checks):
    return json.dumps({"schema": "mission-verification/1", "checks": checks})


def _ok_check(name="tests"):
    return {"name": name, "ok": True, "detail": "12 passed"}


def _failed_check(name="tests"):
    return {"name": name, "ok": False, "detail": "1 failed: test_totals_reconcile"}


def _record(run_cli, state_dir, checks, iteration=1):
    return run_cli("verification", "record", "--iteration", str(iteration),
                   "--stdin", cwd=state_dir.parent, input_text=_verification(checks))


def test_verification_result_is_recorded_for_iteration(state_dir, run_cli, read_state):
    result = _record(run_cli, state_dir, [_ok_check()])
    assert result.returncode == 0, result.stderr
    entry = read_state(state_dir)["verification_history"][0]
    assert entry["iteration"] == 1
    assert entry["status"] == "passed"
    assert entry["failed_count"] == 0
    assert entry["checks"][0]["name"] == "tests"


def test_failed_check_marks_verification_failed(state_dir, run_cli, read_state):
    assert _record(run_cli, state_dir, [_ok_check(), _failed_check()]).returncode == 0
    entry = read_state(state_dir)["verification_history"][0]
    assert entry["status"] == "failed"
    assert entry["failed_count"] == 1


def test_verification_failure_does_not_block_the_command(state_dir, run_cli):
    """検証が失敗しても mission を止めない。判断は reviewer と gate に委ねる。"""
    assert _record(run_cli, state_dir, [_failed_check()]).returncode == 0


def test_empty_checks_are_recorded_as_not_run_not_passed(state_dir, run_cli, read_state):
    """検証していないことを「合格」と混同しない。"""
    assert _record(run_cli, state_dir, []).returncode == 0
    entry = read_state(state_dir)["verification_history"][0]
    assert entry["status"] == "not-run"


def test_malformed_payload_is_rejected(state_dir, run_cli):
    result = run_cli("verification", "record", "--iteration", "1", "--stdin",
                     cwd=state_dir.parent, input_text="{not json")
    assert result.returncode != 0


def test_checks_missing_ok_field_are_rejected(state_dir, run_cli):
    """ok が無い check を「合格」と解釈しない。"""
    payload = json.dumps({"schema": "mission-verification/1",
                          "checks": [{"name": "tests", "detail": "ran"}]})
    result = run_cli("verification", "record", "--iteration", "1", "--stdin",
                     cwd=state_dir.parent, input_text=payload)
    assert result.returncode != 0


def test_latest_record_wins_for_same_iteration(state_dir, run_cli, read_state):
    assert _record(run_cli, state_dir, [_failed_check()]).returncode == 0
    assert _record(run_cli, state_dir, [_ok_check()]).returncode == 0
    history = read_state(state_dir)["verification_history"]
    latest = [e for e in history if e["iteration"] == 1][-1]
    assert latest["status"] == "passed"


def _scoring_json(tmp_path, iteration=1):
    _p, ref, claim = write_canonical_review_aggregate(
        tmp_path, [canonical_review(ITEMS, perspective=f"fx-{i}") for i in range(2)],
        iteration=iteration,
    )
    payload = {
        "items": ITEMS, "open_high": 0,
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": ref["path"],
        "score_provenance": {"score_source": "scoring-json",
                             "review_evidence_ref": ref,
                             "revision_scope": ref["revision_scope"]},
    }
    path = tmp_path / f"scoring-{iteration}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_gate_semantics_unchanged_by_verification_record(state_dir, run_cli, read_state, tmp_path):
    """検証はゲートの**入力**を増やすものであり、ゲートの式を変えない。"""
    assert _record(run_cli, state_dir, [_failed_check()]).returncode == 0
    src = _scoring_json(tmp_path)
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                   cwd=state_dir.parent).returncode == 0
    entry = read_state(state_dir)["score_history"][0]
    assert entry["composite"] == 4.0
    assert entry["min_item"] == 3.5
    assert entry["open_high"] == 0
    assert entry["review_agreement"] is not None


# --- #593 B-4 の解消: verification が FN の客観ラベルになる --------------------

from mission_gate_outcome import false_negative_summary  # noqa: E402


def _state(mission, *, iterations, verification):
    return {
        "mission": mission,
        "score_history": [
            {"iteration": i, "composite": 4.5, "artifact_digest": f"sha256:{mission}{i}"}
            for i in range(1, iterations + 1)
        ],
        "passes": True,
        "verification_history": verification,
    }


def _v(iteration, status, failed=0):
    return {"iteration": iteration, "status": status, "failed_count": failed, "checks": []}


def test_review_passed_but_verification_failed_is_a_false_negative():
    """gate は通したのに検証は失敗した = 見逃し。これが FN の客観ラベル。"""
    states = [_state("m1", iterations=1, verification=[_v(1, "failed", failed=2)])]
    fn = false_negative_summary(states)
    assert fn["status"] == "measured"
    assert fn["count"] == 1
    assert fn["missions"] == ["m1"]


def test_review_passed_and_verification_passed_is_not_a_false_negative():
    states = [_state("m1", iterations=1, verification=[_v(1, "passed")])]
    fn = false_negative_summary(states)
    assert fn["status"] == "measured"
    assert fn["count"] == 0


def test_not_run_verification_does_not_count_as_evidence_of_absence():
    """検証していないことを「欠陥なし」と混同しない。"""
    states = [_state("m1", iterations=1, verification=[_v(1, "not-run")])]
    fn = false_negative_summary(states)
    assert fn["status"] == "unmeasurable"
    assert fn["count"] is None


def test_states_without_verification_remain_unmeasurable():
    states = [{"mission": "legacy", "score_history": [{"iteration": 1, "composite": 4.5}], "passes": True}]
    fn = false_negative_summary(states)
    assert fn["status"] == "unmeasurable"
    assert fn["count"] is None


def test_mixed_population_reports_only_the_measurable_part():
    """一部しか検証が無い母集団で、測れた分だけを母数にする。"""
    states = [
        _state("m1", iterations=1, verification=[_v(1, "failed", failed=1)]),
        _state("m2", iterations=1, verification=[_v(1, "passed")]),
        {"mission": "legacy", "score_history": [{"iteration": 1, "composite": 4.5}], "passes": True},
    ]
    fn = false_negative_summary(states)
    assert fn["status"] == "measured"
    assert fn["count"] == 1
    assert fn["missions_with_verification"] == 2
    assert fn["false_negative_rate"] == 0.5
