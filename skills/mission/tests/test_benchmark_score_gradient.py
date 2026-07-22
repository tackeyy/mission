"""#247 (B1) / #248 (B4): スコア勾配化と validator 対称化.

- B1: `1 + 3×validator(二値) + 1×marker` は validator 通過 + marker 全一致で必ず
  5.0 になり、tail-v1/v2 の完走 record が全て天井に張り付いた。markered task は
  `1 + 1×validator_fraction + 3×marker_net` へ変更し、内容 recall を支配項にする。
  marker-less task は歴史的意味を保つため legacy 二値 (1.0/4.0) を維持する。
- B4: goal 5 見出し vs mission 8 見出しの非対称 gate を、両アーム共通見出し
  (Evidence/Assumptions) のみの gate に統一。アーム固有見出しの欠落は
  `missing_arm_specific_headings` に情報として残すが pass に影響しない。
"""
import importlib.util
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load(name: str):
    path = BENCH / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MARKED_TASK = {
    "id": "gradient-check",
    "quality_markers": [
        {"name": "A", "patterns": ["alpha finding"]},
        {"name": "B", "patterns": ["beta finding"]},
    ],
}
COMMON = "## Evidence\n\nevidence body\n\n## Assumptions\n\nassumption body\n"


def _evaluate(tmp_path, module, text, arm="claude_code_goal_command", task=MARKED_TASK):
    out_rel = "artifact.md"
    (tmp_path / out_rel).write_text(text, encoding="utf-8")
    return module.evaluate_run(
        tmp_path, task, arm, out_rel,
        returncode=0, stdout="", stderr="", timed_out=False,
    )


# ===== B1: 勾配化 =====

def test_marker_recall_dominates_score(tmp_path):
    """marker 半分一致は全一致より明確に低いスコアになる (勾配)."""
    module = _load("run_claude_goal_vs_mission.py")
    full = _evaluate(tmp_path, module, COMMON + "alpha finding and beta finding\n")
    half = _evaluate(tmp_path, module, COMMON + "alpha finding only\n")
    assert full["human_quality_score"] == 5.0
    assert half["human_quality_score"] == 3.5  # 1 + 1*1.0 + 3*0.5
    assert half["human_quality_score"] < full["human_quality_score"]


def test_validator_fraction_gives_partial_credit(tmp_path):
    """共通見出しが半分欠けると validator は fail だが、fraction が記録される."""
    module = _load("run_claude_goal_vs_mission.py")
    partial = _evaluate(tmp_path, module, "## Evidence\n\nalpha finding beta finding\n")
    assert partial["validator_pass"] is False
    assert partial["validator_fraction"] == 0.5
    # validator fail 時は marker が null になり、スコアは fail 帯 (1 + 0.5)
    assert partial["human_quality_score"] == 1.5


def test_markerless_task_keeps_legacy_binary_mapping(tmp_path):
    """marker なし task は 1.0/4.0 の歴史的意味を維持する."""
    module = _load("run_claude_goal_vs_mission.py")
    task = {"id": "no-markers"}
    ok = _evaluate(tmp_path, module, COMMON, task=task)
    assert ok["human_quality_score"] == 4.0
    assert ok["quality_score_method"] == "automated_heuristic_form_stripped_not_blind_human"


def test_gradient_method_string_distinguishes_new_records(tmp_path):
    module = _load("run_claude_goal_vs_mission.py")
    out = _evaluate(tmp_path, module, COMMON + "alpha finding beta finding\n")
    assert out["quality_score_method"] == (
        "automated_heuristic_form_stripped_gradient_v2_not_blind_human"
    )


# ===== B4: 対称化 =====

def test_same_artifact_same_validator_result_across_arms(tmp_path):
    """同一本文ならアームが違っても validator_pass / スコアが一致する."""
    module = _load("run_claude_goal_vs_mission.py")
    text = COMMON + "alpha finding beta finding\n"
    goal = _evaluate(tmp_path, module, text, arm="claude_code_goal_command")
    mission = _evaluate(tmp_path, module, text, arm="mission")
    assert goal["validator_pass"] is True and mission["validator_pass"] is True
    assert goal["human_quality_score"] == mission["human_quality_score"]
    assert goal["validator_fraction"] == mission["validator_fraction"]


def test_arm_specific_headings_recorded_but_not_gating(tmp_path):
    """アーム固有見出しの欠落は情報として残るが pass に影響しない."""
    module = _load("run_claude_goal_vs_mission.py")
    out = _evaluate(tmp_path, module, COMMON + "alpha finding beta finding\n", arm="mission")
    assert out["validator_pass"] is True
    missing = out["missing_arm_specific_headings"]
    assert "Mission" in missing and "Plan" in missing
    # 共通見出し gate の missing_headings は空
    assert out["missing_headings"] == []


def test_schema_accepts_new_fields_and_method():
    import json
    schema = json.loads((BENCH / "result.schema.json").read_text())
    assert "automated_heuristic_form_stripped_gradient_v2_not_blind_human" in (
        schema["properties"]["quality_score_method"]["enum"]
    )
    assert "validator_fraction" in schema["properties"]
    assert "missing_arm_specific_headings" in schema["properties"]


def test_paired_runner_gate_is_also_symmetric(tmp_path):
    """#248 (レビュー指摘): paired runner にも同じ対称 gate を適用する。
    gradient_v2 ラベルの母集団が official/paired で非互換にならないこと."""
    module = _load("run_paired_pilot.py")
    text = COMMON + "alpha finding beta finding\n"
    out_rel = "artifact.md"
    (tmp_path / out_rel).write_text(text, encoding="utf-8")
    # paired runner の marker は legacy 形式 ({"text": ...})
    paired_task = {
        "id": "gradient-check",
        "quality_markers": [{"text": "alpha finding"}, {"text": "beta finding"}],
    }
    goal = module.evaluate_run(tmp_path, paired_task, "goal_only", out_rel,
                               session_id="s", returncode=0)
    # mission arm は mission_state_passes gate があるため goal_only で対称性を検証
    assert goal["validator_pass"] is True
    assert goal["validator_fraction"] == 1.0
    assert "Goal" in goal["missing_arm_specific_headings"]
    assert goal["missing_headings"] == []
    assert goal["human_quality_score"] == 5.0
    assert goal["quality_score_method"] == (
        "automated_heuristic_form_stripped_gradient_v2_not_blind_human"
    )
