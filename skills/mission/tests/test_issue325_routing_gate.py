"""#325: adaptive routing の next 駆動ゲート — set complexity 経路の routing 欠落修正.

portfolio-v1 (2026-08-02) で Simple 3 tasks の mission arm が routing を通らず
フルループが走った (routing は init 引数でのみ判定され、init → set complexity=Simple
の経路は素通り)。F4 (#309) と同型の機械的ゲートを next に追加する。

Contract under test:
1. planning + iter1 + Simple + シグナルなし + issue_ref なし + implementer +
   score_history 空 + force なし → next は run-planner でなく route-to-goal を返す
2. 除外条件それぞれで従来どおり run-planner: シグナルあり / issue_ref あり /
   --force-mission init / checker role / user 指定 tier / Standard
3. halt カテゴリ routed-goal が使え、pass-rate の completed 分母から除外される
   (routed_count として別計上)
4. init --force-mission は state に force_mission を記録する (gate スキップ用)
"""

import json
import importlib.util
from pathlib import Path


def _load(name: str, rel: str):
    path = Path(__file__).resolve().parents[1] / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load("mission_state", "bin/mission-state.py")
MC = _load("mission_common", "lib/mission_common.py")


def _data(**over):
    d = {
        "mission": "typo を1箇所直す",
        "mission_id": "r1", "loop_active": True, "passes": False,
        "halt_reason": "", "phase": "planning", "iteration": 1,
        "reviewer_count": 1, "complexity": "Simple",
        "review_tier": "light", "review_tier_source": "auto",
        "review_tier_signals": [], "session_role": "implementer",
        "score_history": [],
    }
    d.update(over)
    return d


# ===== 1. gate 発火 =====

def test_planning_simple_routes_to_goal():
    result = MS._derive_next_action(_data())
    assert result["next_action"] == "route-to-goal"
    assert "routed-goal" in result["command_hint"]


# ===== 2. 除外条件 =====

def test_signals_keep_loop():
    r = MS._derive_next_action(_data(review_tier_signals=["irreversible-keyword:deploy"],
                                     review_tier="full"))
    assert r["next_action"] == "run-planner"


def test_issue_ref_keeps_loop():
    r = MS._derive_next_action(_data(issue_ref="418"))
    assert r["next_action"] == "run-planner"


def test_force_mission_keeps_loop():
    r = MS._derive_next_action(_data(force_mission=True))
    assert r["next_action"] == "run-planner"


def test_checker_role_keeps_loop():
    r = MS._derive_next_action(_data(session_role="checker"))
    assert r["next_action"] == "run-planner"


def test_user_tier_keeps_loop():
    r = MS._derive_next_action(_data(review_tier_source="user"))
    assert r["next_action"] == "run-planner"


def test_standard_keeps_loop():
    r = MS._derive_next_action(_data(complexity="Standard", review_tier="standard"))
    assert r["next_action"] == "plan-inline"  # #339: 非 routed のままループ継続 (inline 計画)


def test_score_history_keeps_loop():
    r = MS._derive_next_action(_data(score_history=[{"iteration": 1, "composite": 4.0}]))
    assert r["next_action"] == "run-planner"


# ===== 3. routed-goal カテゴリと統計除外 =====

def test_routed_goal_in_categories():
    assert "routed-goal" in MC.HALT_CATEGORIES


def test_routed_goal_excluded_from_completed_denominator():
    states = [
        {"mission_id": "a", "loop_active": False, "passes": True, "halt_reason": "",
         "halt_category": "", "session_role": "implementer",
         "last_activity_at": "2026-08-02T00:00:00Z"},
        {"mission_id": "b", "loop_active": False, "passes": False,
         "halt_reason": "routed-to-goal (#325)", "halt_category": "routed-goal",
         "session_role": "implementer", "last_activity_at": "2026-08-02T00:00:00Z"},
    ]
    result = MC.summarize_pass_rate_population(states, stale_after_sec=10800)
    assert result["routed_count"] == 1
    assert result["completed_pass_rate_denominator"] == 1, (
        "routed-goal は completed 分母から除外されるべき")
    assert result["completed_pass_rate"] == 1.0
    assert result["implementer_pass_rate_denominator"] == 1
    assert result["implementer_pass_rate"] == 1.0


# ===== 4. init --force-mission の記録 =====

def test_init_force_mission_recorded(run_cli, tmp_path):
    run_cli("init", "typo を1箇所直す", "--complexity", "Simple",
            "--force-mission", cwd=tmp_path, check=True)
    sf = sorted((tmp_path / ".mission-state" / "sessions").glob("*.json"))[0]
    from mission_persistence.authoritative_reader import read_authoritative_snapshot

    state = read_authoritative_snapshot(
        sf, expected_session_id=sf.stem
    ).document_copy()
    assert state.get("force_mission") is True


def test_mark_halt_accepts_routed_goal(run_cli, tmp_path):
    run_cli("init", "m", "--complexity", "Standard", cwd=tmp_path, check=True)
    r = run_cli("mark-halt", "--reason", "routed-to-goal (#325)",
                "--category", "routed-goal", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
