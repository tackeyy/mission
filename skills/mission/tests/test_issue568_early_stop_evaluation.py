"""#568: early-stop の継続条件の評価結果を state に記録する (記録のみ)。

SKILL.md の early-stop 規律は「composite 4.0-4.3 / Medium 3 件以上 /
1 iter で解消可能 / iteration < max_iter」が揃うときだけ継続を許す。この条件は
`passes` 式にも gate 判定にも含まれていないため、orchestrator が読み落としても
検知できず、なぜ継続しなかったかを事後に検証できない。

本テストは提案1 (記録のみ) を固定する。gate 意味論は変更しない。
"""
import json
import os

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


def _load_cli_module():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "gs_early_stop",
        Path(__file__).resolve().parent.parent / "bin" / "mission-state.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_band_boundaries_are_inclusive(tmp_path):
    """SKILL.md の "composite 4.0-4.3" は両端を含む。境界を実装から固定する."""
    module = _load_cli_module()

    def band(composite):
        evaluation = module._early_stop_evaluation(
            tmp_path, {"iteration": 1, "max_iter": 2}, {"composite": composite},
            "2026-08-22T00:00:00Z", None,
        )
        return evaluation["composite_in_band"]

    assert band(4.0) is True, "下端 4.0 は band に含む"
    assert band(4.3) is True, "上端 4.3 は band に含む"
    assert band(3.99) is False
    assert band(4.31) is False


def test_non_finite_composite_is_not_in_band(tmp_path):
    """NaN / Infinity を band 内と誤判定しない."""
    module = _load_cli_module()
    for composite in (float("nan"), float("inf"), float("-inf")):
        evaluation = module._early_stop_evaluation(
            tmp_path, {"iteration": 1, "max_iter": 2}, {"composite": composite},
            "2026-08-22T00:00:00Z", None,
        )
        assert evaluation["composite_in_band"] is False, composite
        assert evaluation["continuation_conditions_met"] is False


def test_observation_failure_never_aborts_the_gate(state_dir, run_cli, read_state, tmp_path):
    """観測子が例外を投げても pass 判定は続行し、失敗は沈黙せず記録される."""
    import subprocess
    import sys
    from pathlib import Path

    _set_state(state_dir, read_state, max_iter=2, iteration=1)
    _push_score_with_findings(run_cli, state_dir, composite=4.25, medium_count=3)

    # 観測子だけを確実に失敗させる (repo 既定の launcher 方式)。
    launcher = tmp_path / "raise_launcher.py"
    launcher.write_text(
        "import importlib.util, sys\n"
        "path = sys.argv[1]\n"
        "spec = importlib.util.spec_from_file_location('ms_raise', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "def _boom(*a, **k):\n"
        "    raise MemoryError('observation blew up')\n"
        "module._early_stop_evaluation = _boom\n"
        "sys.argv = [path] + sys.argv[2:]\n"
        "module.main()\n"
    )
    mission_state_py = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("MISSION_") and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    environment["MISSION_SESSION_ID"] = "test"
    environment["MISSION_LEASE_ID"] = "test-lease"
    result = subprocess.run(
        [sys.executable, str(launcher), str(mission_state_py), "mark-passes"],
        cwd=str(state_dir.parent), capture_output=True, text=True, env=environment,
    )

    assert result.returncode == 0, f"観測子の失敗が gate を止めてはならない: {result.stderr}"
    state = read_state(state_dir)
    assert state["passes"] is True
    assert state["loop_active"] is False
    # 沈黙させない: 記録が無いのか観測に失敗したのかを区別できること。
    assert state["early_stop_evaluation"]["status"] == "observation-failed"
    assert state["early_stop_evaluation"]["error"] == "MemoryError"
