"""#568: early-stop の継続条件の評価結果を state に記録する (記録のみ)。

SKILL.md の early-stop 規律は「composite 4.0-4.3 / Medium 3 件以上 /
1 iter で解消可能 / iteration < max_iter」が揃うときだけ継続を許す。この条件は
`passes` 式にも gate 判定にも含まれていないため、orchestrator が読み落としても
検知できず、なぜ継続しなかったかを事後に検証できない。

本テストは提案1 (記録のみ) を固定する。gate 意味論は変更しない。
"""
import json

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


def _push_score(run_cli, state_dir, *, composite, min_item, iteration=1):
    return run_cli(
        "push-score",
        "--iteration", str(iteration),
        "--composite", str(composite),
        "--min-item", str(min_item),
        cwd=state_dir.parent,
        check=True,
    )


def _push_score_with_findings(run_cli, state_dir, *, composite, medium_count, iteration=1):
    """Publish a scoring-json whose evidence carries ``medium_count`` Medium findings."""
    root = state_dir.parent
    review = canonical_review({key: composite for key in
                               ("mission_achievement", "accuracy", "completeness", "usability")})
    review["findings"] = [
        {"id": f"fixture-M-{index}", "severity": "Medium", "axis": "accuracy"}
        for index in range(medium_count)
    ]
    _path, ref, claim = write_canonical_review_aggregate(
        root, [review], iteration=iteration, name_prefix="issue568"
    )
    score = state_dir / "archive" / f"issue568-score-{iteration}.json"
    score.write_text(json.dumps({
        "items": claim["items"],
        "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": ref["path"],
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": ref,
            "revision_scope": ref["revision_scope"],
        },
    }))
    run_cli(
        "push-score", "--iteration", str(iteration), "--scoring-json", str(score),
        cwd=root, check=True,
    )
    # reviewer cap (Medium 3 件で該当 axis が 3.5 に丸められる) を通した後の実値を返す。
    return claim


def _set_state(state_dir, read_state, **fields):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update(fields)
    state_path.write_text(json.dumps(state, indent=2))


def test_records_evaluation_when_continuation_conditions_are_met(state_dir, run_cli, read_state):
    """composite が band 内・Medium 3 件・iteration < max_iter で継続条件成立を記録する."""
    _set_state(state_dir, read_state, max_iter=2, iteration=1)
    claim = _push_score_with_findings(run_cli, state_dir, composite=4.25, medium_count=3)

    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, r.stderr

    evaluation = read_state(state_dir)["early_stop_evaluation"]
    assert evaluation["composite"] == claim["composite"]
    assert 4.0 <= claim["composite"] <= 4.3, claim["composite"]
    assert evaluation["composite_in_band"] is True
    assert evaluation["medium_count"] == 3
    assert evaluation["medium_count_source"] == "findings-evidence"
    assert evaluation["iteration"] == 1
    assert evaluation["max_iter"] == 2
    assert evaluation["iteration_lt_max"] is True
    assert evaluation["continuation_conditions_met"] is True
    # 主観判断は機械決定できない。宣言がなければ null のまま残す。
    assert evaluation["resolvable_in_one_iter"] is None
    assert evaluation["rationale"] is None
    assert evaluation["decision"] == "stop"
    assert evaluation["recorded_at"]


def test_composite_above_band_is_not_a_continuation_candidate(state_dir, run_cli, read_state):
    """composite > 4.3 は band 外なので継続条件は不成立."""
    _set_state(state_dir, read_state, max_iter=2, iteration=1)
    claim = _push_score_with_findings(run_cli, state_dir, composite=5.0, medium_count=3)
    assert claim["composite"] > 4.3, claim["composite"]

    assert run_cli("mark-passes", cwd=state_dir.parent).returncode == 0
    evaluation = read_state(state_dir)["early_stop_evaluation"]
    assert evaluation["composite_in_band"] is False
    assert evaluation["continuation_conditions_met"] is False


def test_fewer_than_three_medium_findings_is_not_a_continuation_candidate(
    state_dir, run_cli, read_state
):
    """Medium 2 件では継続条件は不成立 (SKILL.md は 3 件以上を要求)."""
    _set_state(state_dir, read_state, max_iter=2, iteration=1)
    _push_score_with_findings(run_cli, state_dir, composite=4.25, medium_count=2)

    assert run_cli("mark-passes", cwd=state_dir.parent).returncode == 0
    evaluation = read_state(state_dir)["early_stop_evaluation"]
    assert evaluation["medium_count"] == 2
    assert evaluation["continuation_conditions_met"] is False


def test_iteration_at_max_iter_is_not_a_continuation_candidate(state_dir, run_cli, read_state):
    """iteration == max_iter では継続余地がないので不成立."""
    _set_state(state_dir, read_state, max_iter=1, iteration=1)
    _push_score_with_findings(run_cli, state_dir, composite=4.25, medium_count=3)

    assert run_cli("mark-passes", cwd=state_dir.parent).returncode == 0
    evaluation = read_state(state_dir)["early_stop_evaluation"]
    assert evaluation["iteration_lt_max"] is False
    assert evaluation["continuation_conditions_met"] is False


def test_unbounded_max_iter_keeps_iteration_condition_true(state_dir, run_cli, read_state):
    """max_iter=None (上限なし) では iteration < max_iter は常に真として扱う."""
    _set_state(state_dir, read_state, max_iter=None, iteration=1)
    _push_score_with_findings(run_cli, state_dir, composite=4.25, medium_count=3)

    assert run_cli("mark-passes", cwd=state_dir.parent).returncode == 0
    evaluation = read_state(state_dir)["early_stop_evaluation"]
    assert evaluation["max_iter"] is None
    assert evaluation["iteration_lt_max"] is True
    assert evaluation["continuation_conditions_met"] is True


def test_rationale_flag_is_recorded(state_dir, run_cli, read_state):
    """--early-stop-rationale で「なぜ継続しなかったか」を証跡に残せる."""
    _set_state(state_dir, read_state, max_iter=2, iteration=1)
    _push_score_with_findings(run_cli, state_dir, composite=4.25, medium_count=3)

    r = run_cli(
        "mark-passes",
        "--early-stop-rationale", "残 Medium は仕様上の制約で 1 iter では解消できない",
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, r.stderr
    evaluation = read_state(state_dir)["early_stop_evaluation"]
    assert evaluation["rationale"] == "残 Medium は仕様上の制約で 1 iter では解消できない"
    assert evaluation["resolvable_in_one_iter"] is False


def test_medium_counter_returns_none_for_unusable_evidence(tmp_path):
    """evidence が読めない場合に件数を捏造しない (0 と "不明" を混同しない).

    pass 経路では provenance gate が structured evidence を要求するため、この分岐は
    防御的なもの。記録が gate を動かさないことを担保するためにここで固定する。
    """
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "gs_early_stop",
        Path(__file__).resolve().parent.parent / "bin" / "mission-state.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._count_medium_findings_in_evidence(tmp_path, "missing.json") is None

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json")
    assert module._count_medium_findings_in_evidence(tmp_path, "malformed.json") is None

    wrong_shape = tmp_path / "wrong.json"
    wrong_shape.write_text(json.dumps({"inputs": "not-a-list"}))
    assert module._count_medium_findings_in_evidence(tmp_path, "wrong.json") is None

    counted = tmp_path / "counted.json"
    counted.write_text(json.dumps({"inputs": [
        {"findings": [{"severity": "Medium"}, {"severity": "High"}, {"severity": "Medium"}]},
        {"findings": [{"severity": "Medium"}]},
    ]}))
    assert module._count_medium_findings_in_evidence(tmp_path, "counted.json") == 3


# ===== gate 意味論の不変性 =====


def test_recording_does_not_change_the_reject_path(state_dir, run_cli, read_state):
    """gate が reject する条件では state を書かない (記録も残さない)."""
    _push_score(run_cli, state_dir, composite=3.9, min_item=3.8)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2
    state = read_state(state_dir)
    assert state["passes"] is False
    assert "early_stop_evaluation" not in state


def test_recording_does_not_gate_a_continuation_candidate(state_dir, run_cli, read_state):
    """継続条件が全て揃っていても mark-passes は成功する (提案2 の機械強制はしない)."""
    _set_state(state_dir, read_state, max_iter=2, iteration=1)
    _push_score_with_findings(run_cli, state_dir, composite=4.25, medium_count=3)

    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, r.stderr
    state = read_state(state_dir)
    assert state["passes"] is True
    assert state["loop_active"] is False
    assert state["early_stop_evaluation"]["continuation_conditions_met"] is True
