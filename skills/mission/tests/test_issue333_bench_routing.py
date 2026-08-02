"""#333: ベンチが routing を観測できるようにする測定側改訂.

portfolio-v3 で routing 発火 0/3 の根本原因 = ベンチの mission arm プロンプトが
loop 機構を明示要求し orchestrator が --force-mission していた (測定の自己矛盾)。

Contract under test:
1. mission arm プロンプトが routing 許容文言を含み、--force-mission を誘導しない
2. mission_routed の第一級記録: state の halt_category=routed-goal → true
3. Simple タスク + state 不在 + 完走 → mission_routed=true かつ comparable
   (init 経路 routing は state を作らないため。#261 ガードの Simple 例外)
4. 非 Simple + state 不在 → 従来どおり mission_loop_not_initialized で invalid
5. summary にアーム別 routed_records
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


# ===== 1. プロンプトの routing 許容 =====

def test_mission_prompt_permits_routing():
    task = {"id": "t", "category": "docs", "prompt": "p", "validator": "v",
            "mission_complexity": "Simple", "quality_markers": [], "markers_hidden": True}
    prompt = MODULE.build_prompt(task, "mission", "out.md")
    assert "rout" in prompt.lower(), "mission arm プロンプトは routing 許容を明記すべき"
    assert "force-mission" not in prompt, "--force-mission を誘導してはならない"


# ===== 2-4. mission_routed / adherence guard =====

def test_routed_goal_state_marks_routed(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "s.json").write_text(json.dumps({
        "mission_id": "x", "loop_active": False, "passes": False,
        "halt_reason": "routed-to-goal (#330)", "halt_category": "routed-goal",
        "iteration": 0,
    }))
    fields, note = MODULE.extract_mission_state_fields(tmp_path)
    assert fields["mission_routed"] is True


def test_full_loop_state_not_routed(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "s.json").write_text(json.dumps({
        "mission_id": "x", "loop_active": False, "passes": True,
        "halt_reason": "", "halt_category": None, "iteration": 1,
    }))
    fields, note = MODULE.extract_mission_state_fields(tmp_path)
    assert fields["mission_routed"] is False


def test_simple_task_missing_state_is_routed_and_comparable():
    """Simple + state 不在 + 完走 = init 経路 routing → valid routed record."""
    status = {"run_status": "completed", "blocked_reason": None,
              "failure_kind": None, "comparable_attempt": True}
    result = MODULE.apply_mission_adherence_guard(
        dict(status), arm="mission", mission_state_note="mission_state_missing",
        task_complexity="Simple")
    assert result["comparable_attempt"] is True
    assert result.get("failure_kind") is None


def test_non_simple_missing_state_still_invalid():
    status = {"run_status": "completed", "blocked_reason": None,
              "failure_kind": None, "comparable_attempt": True}
    result = MODULE.apply_mission_adherence_guard(
        dict(status), arm="mission", mission_state_note="mission_state_missing",
        task_complexity="Standard")
    assert result["comparable_attempt"] is False
    assert result["failure_kind"] == "mission_loop_not_initialized"


# ===== 5. summary の routed_records =====

def test_summarize_counts_routed_records():
    def _rec(arm, routed):
        return {"arm": arm, "run_status": "completed", "comparable_attempt": True,
                "completion": True, "validator_pass": True,
                "human_quality_score": 5.0, "intervention_count": 0,
                "evidence_completeness": 5.0, "quality_marker_score": 1.0,
                "elapsed_minutes": 1.0, "total_cost_usd": 1.0,
                "permission_mode_degraded": False, "mission_routed": routed}
    records = [_rec("mission", True), _rec("mission", False),
               _rec("claude_code_goal_command", None)]
    summary = MODULE.summarize(records, [{"id": "t1"}], "rid", "abc1234",
                               BENCH / "tasks.portfolio.json")
    assert summary["arms"]["mission"]["routed_records"] == 1
