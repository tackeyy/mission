"""#341: bench mission アームの implementer role 固定と evidence-submitted run の第一級記録.

portfolio-v4 で cx-ledger の mission アームが checker 挙動 (evidence-submitted,
iteration 0) で終了し、品質ゲート付きループを測っていなかった (1.53x)。

Contract under test:
1. mission arm プロンプトは implementer role での scored review loop 完走を
   肯定形で要求する。checker 系 role や halt category の字句は含めない
   (#333 の教訓: 負の指示でもトークン自体が挙動を誘発する)
2. mission_evidence_only の第一級記録: halt_category=evidence-submitted → true
3. summary にアーム別 evidence_only_records、>0 なら limitations に警告
"""

import importlib.util
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load():
    path = BENCH / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def _task(complexity="Standard"):
    return {"id": "t", "category": "docs", "prompt": "p", "validator": "v",
            "mission_complexity": complexity, "quality_markers": [], "markers_hidden": True}


# ===== 1. プロンプトの implementer role 固定 =====

def test_mission_prompt_requires_implementer_scored_loop():
    prompt = MODULE.build_prompt(_task(), "mission", "out.md")
    assert "implementer" in prompt, "implementer role の明示が必要"
    assert "scored review" in prompt, "scored review loop の完走要求が必要"


def test_mission_prompt_avoids_priming_tokens():
    prompt = MODULE.build_prompt(_task(), "mission", "out.md")
    assert "checker" not in prompt.lower(), "checker トークンは挙動を誘発するため含めない"
    assert "evidence-submitted" not in prompt, "halt category 字句は含めない"
    assert "--role" not in prompt, "CLI フラグ字句は含めない"


# ===== 2. mission_evidence_only の第一級記録 =====

def _write_state(tmp_path, **kw):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    state = {"review_tier": "full", "iteration": 0, "complexity": "Complex",
             "passes": False, "halt_category": None}
    state.update(kw)
    (sessions / "state-x.json").write_text(json.dumps(state), encoding="utf-8")


def test_evidence_submitted_state_marks_evidence_only(tmp_path):
    _write_state(tmp_path, halt_category="evidence-submitted")
    fields, note = MODULE.extract_mission_state_fields(tmp_path)
    assert note is None
    assert fields["mission_evidence_only"] is True
    assert fields["mission_routed"] is False


def test_normal_loop_state_not_evidence_only(tmp_path):
    _write_state(tmp_path, halt_category=None, iteration=1, passes=True)
    fields, note = MODULE.extract_mission_state_fields(tmp_path)
    assert fields["mission_evidence_only"] is False


def test_missing_state_leaves_evidence_only_none(tmp_path):
    fields, note = MODULE.extract_mission_state_fields(tmp_path)
    assert note == "mission_state_missing"
    assert fields["mission_evidence_only"] is None


# ===== 3. summary 集計と limitations 警告 =====

def _record(arm="mission", **kw):
    rec = {"arm": arm, "task_id": "t", "run_status": "completed", "completion": True,
           "validator_pass": True, "human_quality_score": 5.0, "intervention_count": 0,
           "evidence_completeness": 5.0, "quality_marker_score": 1.0,
           "elapsed_minutes": 1.0, "total_cost_usd": 1.0,
           "permission_mode_degraded": False, "mission_routed": False,
           "mission_evidence_only": False, "comparable_attempt": True}
    rec.update(kw)
    return rec


def _summarize(records):
    return MODULE.summarize(records, [{"id": "t1"}], "rid", "abc1234",
                            BENCH / "tasks.portfolio.json")


def test_summarize_counts_evidence_only_records():
    records = [_record(mission_evidence_only=True), _record(), _record(arm="claude_code_goal_command")]
    summary = _summarize(records)
    assert summary["arms"]["mission"]["evidence_only_records"] == 1
    assert any("#341" in lim for lim in summary["limitations"]), "evidence-only run の比較可能性警告が必要"


def test_no_evidence_only_no_warning():
    records = [_record(), _record(arm="claude_code_goal_command")]
    summary = _summarize(records)
    assert summary["arms"]["mission"]["evidence_only_records"] == 0
    assert not any("#341" in lim for lim in summary["limitations"])
