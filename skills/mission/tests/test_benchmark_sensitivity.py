"""#598: 計測器の感度検証ハーネス。

事前登録の最優先条件は「**差を検出できる計測器であることを先に証明する**」
こと。意図的に劣化させた arm と正常な arm を比較し、F1 差が閾値以上に
出るかを見る。ここを通過しない採点器では判定 run を実施しない。

#390 が「テストは green だが実 run では未解決」で close され、3 世代
同じ失敗を繰り返した根本原因への対処である。
"""

import json

import pytest

from mission_sensitivity import (
    SENSITIVITY_THRESHOLD,
    build_degraded_prompt_suffix,
    evaluate_sensitivity,
    load_answer_key,
)


def _result(f1):
    return {"f1": f1}


def test_threshold_matches_pre_registration():
    """事前登録した閾値をコードから動かせないようにする。"""
    assert SENSITIVITY_THRESHOLD == 0.15


def test_separation_above_threshold_passes():
    outcome = evaluate_sensitivity(
        normal=[_result(0.90), _result(0.85)],
        degraded=[_result(0.50), _result(0.55)],
    )
    assert outcome["normal_mean_f1"] == 0.875
    assert outcome["degraded_mean_f1"] == 0.525
    assert outcome["separation"] == 0.35
    assert outcome["passes"] is True


def test_separation_below_threshold_fails():
    outcome = evaluate_sensitivity(
        normal=[_result(0.80)], degraded=[_result(0.75)],
    )
    assert outcome["separation"] == pytest.approx(0.05)
    assert outcome["passes"] is False
    assert "cannot detect" in outcome["verdict"]


def test_degraded_scoring_higher_is_reported_as_failure_not_negative_pass():
    """劣化 arm が上回るのは計測器の異常。負の分離を合格にしない。"""
    outcome = evaluate_sensitivity(normal=[_result(0.40)], degraded=[_result(0.80)])
    assert outcome["separation"] < 0
    assert outcome["passes"] is False


def test_missing_scores_are_excluded_not_treated_as_zero():
    """採点不能 (f1=None) を 0 点と混同しない。"""
    outcome = evaluate_sensitivity(
        normal=[_result(0.9), {"f1": None}], degraded=[_result(0.4)],
    )
    assert outcome["normal_mean_f1"] == 0.9
    assert outcome["normal_scored"] == 1
    assert outcome["normal_unscored"] == 1


def test_all_unscored_cannot_conclude():
    outcome = evaluate_sensitivity(normal=[{"f1": None}], degraded=[{"f1": None}])
    assert outcome["passes"] is False
    assert outcome["separation"] is None
    assert "no scored" in outcome["verdict"]


def test_empty_input_does_not_raise():
    outcome = evaluate_sensitivity(normal=[], degraded=[])
    assert outcome["passes"] is False
    assert outcome["separation"] is None


# --- 劣化 arm のプロンプト -----------------------------------------------------

def test_degraded_prompt_restricts_evidence_not_effort():
    """劣化は「証拠へのアクセス制限」で作る。

    「手を抜け」と指示すると劣化の度合いがモデル任せになり再現しない。
    読める fixture を減らせば、**発見できない欠陥が確定的に決まる**。
    """
    suffix = build_degraded_prompt_suffix(["spec.md"])
    assert "spec.md" in suffix
    assert "only" in suffix.lower()


def test_degraded_prompt_requires_at_least_one_readable_fixture():
    with pytest.raises(ValueError):
        build_degraded_prompt_suffix([])


# --- 正解キー ------------------------------------------------------------------

def test_answer_key_loads_and_has_defects_and_decoys(tmp_path):
    payload = {"schema": "mission-benchmark-answer-key/1",
               "tasks": {"t1": {"defects": [{"location": "a", "key": "b", "actual": "1"}],
                                "decoys": [{"location": "a", "key": "c"}]}}}
    path = tmp_path / "k.json"
    path.write_text(json.dumps(payload))
    key = load_answer_key(path, "t1")
    assert len(key["defects"]) == 1
    assert len(key["decoys"]) == 1


def test_unknown_task_in_answer_key_raises(tmp_path):
    path = tmp_path / "k.json"
    path.write_text(json.dumps({"schema": "mission-benchmark-answer-key/1", "tasks": {}}))
    with pytest.raises(KeyError):
        load_answer_key(path, "missing")


def test_repo_answer_keys_are_wellformed():
    """リポジトリ同梱の正解キーが壊れていないこと。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    path = root / "benchmarks" / "mission-vs-goal" / "answer-keys" / "tail.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "mission-benchmark-answer-key/1"
    for task_id, entry in payload["tasks"].items():
        assert entry["defects"], task_id
        for defect in entry["defects"]:
            for field in ("location", "key", "expected", "actual"):
                assert defect.get(field), f"{task_id}: defect missing {field}"
        for decoy in entry.get("decoys", []):
            assert decoy.get("location") and decoy.get("key"), task_id
            assert decoy.get("note"), f"{task_id}: decoy needs a note explaining why it is compliant"


# --- 到達可能性 (劣化 arm の期待差を確定させる) --------------------------------

from mission_sensitivity import reachable_defects  # noqa: E402


def test_reachability_uses_evidence_not_just_location():
    """「主張がどこにあるか」と「検証に何が要るか」は別物。

    集計値の誤りを指摘するには、主張が載っている要約だけでなく元の明細表が
    要る。location だけで数えると、劣化 arm が実際には検出不能な欠陥を
    「検出できるはず」と誤判定する。
    """
    key = {"defects": [
        {"location": "summary.md", "key": "total", "evidence": ["summary.md", "detail.md"]},
    ]}
    assert reachable_defects(key, ["summary.md"]) == []
    assert len(reachable_defects(key, ["summary.md", "detail.md"])) == 1


def test_reachability_falls_back_to_location_when_evidence_absent():
    key = {"defects": [{"location": "a.md", "key": "k"}]}
    assert len(reachable_defects(key, ["a.md"])) == 1
    assert reachable_defects(key, ["b.md"]) == []


def test_repo_answer_keys_declare_evidence_for_every_defect():
    """evidence が無いと劣化 arm の期待差を事前に確定できない。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    key_path = root / "benchmarks" / "mission-vs-goal" / "answer-keys" / "tail.json"
    payload = json.loads(key_path.read_text(encoding="utf-8"))
    for task_id, entry in payload["tasks"].items():
        for defect in entry["defects"]:
            assert defect.get("evidence"), f"{task_id}: {defect['key']} has no evidence list"


def test_planned_degradation_removes_most_defects():
    """本番の劣化条件が、実際に大きな期待差を作ることを固定する。

    劣化させたつもりで実は差が出ない条件だと、8 セルを無駄に消費したうえ
    「計測器に検出力がない」と誤結論する。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    key_path = root / "benchmarks" / "mission-vs-goal" / "answer-keys" / "tail.json"
    plans = {
        "tail-config-spec-drift": ["spec.md", "impl-alpha.md"],
        "tail-metrics-reconciliation": ["quarterly-summary.md"],
    }
    for task_id, readable in plans.items():
        key = load_answer_key(key_path, task_id)
        reachable = reachable_defects(key, readable)
        ceiling = len(reachable) / len(key["defects"])
        assert ceiling <= 0.5, (
            f"{task_id}: degraded arm can still reach {ceiling:.0%} of defects; "
            "this degradation would not test the instrument"
        )


def test_answer_key_values_are_reportable_wordings():
    """正解キーの値は、artifact が実際に書く表記であること。

    実 run で `actual: "true"` としていた項目 (真偽の主張) が、正しく判定した
    artifact を誤検出扱いしていた。artifact は主張の文言を書くため、正解キー
    側も照合可能な表記にしておく必要がある。

    fixture に実在する設定値としての true/false は対象外。artifact もその
    リテラルを書くので照合できる。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    payload = json.loads(
        (root / "benchmarks" / "mission-vs-goal" / "answer-keys" / "tail.json").read_text(encoding="utf-8")
    )
    for task_id, entry in payload["tasks"].items():
        for defect in entry["defects"]:
            # 設定値としての true/false は fixture に literal で存在するので許す。
            # 判定を真偽で表した項目 (key が動詞句) だけを禁じる。
            if not any(token in defect["key"] for token in ("_is_", "_was_", "improved", "monotonic")):
                continue
            for field in ("expected", "actual"):
                assert str(defect[field]).strip().lower() not in ("true", "false"), (
                    f"{task_id}: {defect['key']}.{field} is a bare boolean; "
                    "use the wording an artifact would actually report"
                )
