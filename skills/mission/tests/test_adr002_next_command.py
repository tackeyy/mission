"""ADR-002 Stage 3: `mission-state.py next` — state から次の一手を決定論的に返す (G-3).

背景 (docs/log-crosscheck-review-2026-07-02.md L-9):
実行の 84% は Codex で走るが、ループ強制の Stop hook は Claude Code 専用。
`next` は「state を読めば次にやるべき 1 手が返る」ハーネス非依存の進行ガイドを提供し、
compaction 後の復元も散文 Compact Instructions への依存から state 駆動へ移す。
"""

import json


def _set_state(state_dir, **kv):
    sf = state_dir / "sessions" / "test.json"
    s = json.loads(sf.read_text())
    s.update(kv)
    sf.write_text(json.dumps(s))


def _next(run_cli, state_dir):
    r = run_cli("next", cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    return json.loads(r.stdout)


def test_next_without_state_suggests_init(tmp_path, run_cli):
    r = run_cli("next", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    out = json.loads(r.stdout)
    assert out["next_action"] == "init"


def test_next_when_halted_reports_blocker(state_dir, run_cli):
    _set_state(state_dir, loop_active=False, halt_reason="waiting for API key")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "report-blocker"
    assert "waiting for API key" in out["summary"]


def test_next_when_passed_reports_complete(state_dir, run_cli):
    _set_state(state_dir, passes=True, loop_active=False, phase="done")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "report-complete"


def test_next_when_inactive_suggests_resume(state_dir, run_cli):
    """loop_active=false / passes=false / halt なし → refresh-pid での再開を提案."""
    _set_state(state_dir, loop_active=False)
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "resume"
    assert "refresh-pid" in out["command_hint"]


def test_next_when_awaiting_user(state_dir, run_cli):
    _set_state(state_dir, awaiting_user=True)
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "await-user"


def test_next_planning_phase_suggests_planner(state_dir, run_cli):
    _set_state(state_dir, phase="planning")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-planner"


def test_next_executing_phase_suggests_executor(state_dir, run_cli):
    _set_state(state_dir, phase="executing")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-executor"


def test_next_reviewing_phase_suggests_parallel_reviewers(state_dir, run_cli):
    _set_state(state_dir, phase="reviewing", reviewer_count=3)
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-reviewers"
    assert out["details"]["reviewer_count"] == 3
    # ログ実測で並列起動遵守率 0/7 だった規律を machine-readable に埋め込む
    assert "並列" in out["summary"] or "parallel" in out["summary"].lower()


def test_next_scoring_phase_without_score_suggests_scorer(state_dir, run_cli):
    # fixture 既定: phase=scoring, iteration=1, score_history=[]
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-scorer"
    assert "--scoring-json" in out["command_hint"]


def test_next_scoring_phase_with_current_score_suggests_mark_passes(state_dir, run_cli):
    _set_state(state_dir, score_history=[
        {"iteration": 1, "composite": 4.2, "min_item": 4.0,
         "items": {"mission_achievement": 4.2}, "timestamp": "2026-07-02T00:00:00Z", "open_high": 0},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "mark-passes"


def test_next_scoring_with_stale_score_from_previous_iteration_suggests_scorer(state_dir, run_cli):
    """score_history が前 iteration のものしかない → 現 iteration の採点が先."""
    _set_state(state_dir, iteration=2, score_history=[
        {"iteration": 1, "composite": 3.2, "min_item": 3.0,
         "items": {"mission_achievement": 3.2}, "timestamp": "2026-07-02T00:00:00Z", "open_high": 0},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-scorer"


def test_next_stagnation_warns_consider_halt(state_dir, run_cli):
    _set_state(state_dir, stagnation_count=3, score_history=[
        {"iteration": 1, "composite": 3.2, "min_item": 3.0,
         "items": {"mission_achievement": 3.2}, "timestamp": "2026-07-02T00:00:00Z", "open_high": 0},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "consider-halt"
    assert "stagnation" in out["summary"].lower() or "停滞" in out["summary"]


def test_next_includes_state_snapshot(state_dir, run_cli):
    out = _next(run_cli, state_dir)
    assert out["phase"] == "scoring"
    assert out["iteration"] == 1
    assert out["session_id"] == "test"
