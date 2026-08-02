"""#339: orchestration ターン圧縮 (command_sequence + Standard planner inline).

portfolio-v4 実測: Standard の時間比 (6.9-14.5x) はトークン比 (4.0-4.7x) を大きく
超え、差分はターン数 (mission 19-31 turns vs goal 5)。subagent spin-up と
next 往復の削減で圧縮する。

Contract under test:
1. Standard + iteration 1 + 非 full tier → next_action=plan-inline (plan_mode=inline)
2. Complex / full tier / iteration>=2 → 従来どおり run-planner
3. planning / executing / reviewing の next に command_sequence (closeout まで)
4. Simple routing (route-to-goal #325) は本変更の影響を受けない
"""

import json


TEST_SID = "test-339"


def _make_state(tmp_path, **kw):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    d = {
        "mission": "m", "mission_id": "tc1", "pid": 12345,
        "loop_active": True, "passes": False, "halt_reason": "",
        "phase": "planning", "iteration": 1, "reviewer_count": 2,
        "complexity": "Standard", "force_mission": True,
        "project_root": str(tmp_path),
    }
    d.update(kw)
    (sessions / f"{TEST_SID}.json").write_text(json.dumps(d))


def _next(run_cli, tmp_path):
    result = run_cli("next", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": TEST_SID})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ===== 1. Standard iter1 → plan-inline =====

def test_standard_iter1_plans_inline(run_cli, tmp_path):
    _make_state(tmp_path)
    payload = _next(run_cli, tmp_path)
    assert payload["next_action"] == "plan-inline"
    assert payload["details"]["plan_mode"] == "inline"
    assert payload["command_sequence"][0].startswith("plan")


# ===== 2. 従来経路の維持 =====

def test_complex_still_uses_planner(run_cli, tmp_path):
    _make_state(tmp_path, complexity="Complex")
    payload = _next(run_cli, tmp_path)
    assert payload["next_action"] == "run-planner"


def test_full_tier_standard_still_uses_planner(run_cli, tmp_path):
    _make_state(tmp_path, review_tier="full")
    payload = _next(run_cli, tmp_path)
    assert payload["next_action"] == "run-planner"


def test_iter2_still_uses_planner(run_cli, tmp_path):
    _make_state(tmp_path, iteration=2)
    payload = _next(run_cli, tmp_path)
    assert payload["next_action"] == "run-planner"


# ===== 3. command_sequence =====

def test_planning_sequence_reaches_closeout(run_cli, tmp_path):
    _make_state(tmp_path, complexity="Complex")
    payload = _next(run_cli, tmp_path)
    seq = payload["command_sequence"]
    assert seq[0] == "Skill: mission-planner"
    assert any("review-finalize" in s for s in seq)
    assert seq[-1].endswith("closeout")


def test_executing_sequence_starts_at_executor(run_cli, tmp_path):
    _make_state(tmp_path, phase="executing")
    payload = _next(run_cli, tmp_path)
    seq = payload["command_sequence"]
    assert seq[0] == "Skill: mission-executor"
    assert seq[-1].endswith("closeout")


def test_reviewing_sequence_starts_at_reviewers(run_cli, tmp_path):
    _make_state(tmp_path, phase="reviewing")
    payload = _next(run_cli, tmp_path)
    assert payload["next_action"] == "run-reviewers"
    seq = payload["command_sequence"]
    assert "mission-reviewer" in seq[0]
    assert seq[-1].endswith("closeout")


# ===== 4. routing 非干渉 =====

def test_simple_routing_unaffected(run_cli, tmp_path):
    _make_state(tmp_path, complexity="Simple", force_mission=False)
    payload = _next(run_cli, tmp_path)
    assert payload["next_action"] == "route-to-goal"
