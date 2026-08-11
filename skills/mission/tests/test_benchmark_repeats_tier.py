"""#249 (B3-ext) / #250 (B5): セル反復と mission state 帰属の計装.

- #249: --repeats N で counterbalanced plan を N 回展開し run_index を記録。
  summary にアーム別の marker 分散・コスト合計/平均を追加し、flaky とノイズを
  分離できるようにする。
- #250: mission arm の実行後 state から review_tier / iteration / complexity /
  passes / halt_category を fail-open で抽出し record に記録する。tier 別
  Pareto (light で速度がどこまで縮むか) を帰属可能にする。
"""
import importlib.util
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load(name: str):
    path = BENCH / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASKS = [{"id": "t1"}, {"id": "t2"}]


# ===== #249: repeats =====

def test_expanded_plan_repeats_counterbalanced_cells():
    module = _load("run_claude_goal_vs_mission.py")
    plan = module.expanded_plan(TASKS, module.ARMS, repeats=3)
    assert len(plan) == len(TASKS) * len(module.ARMS) * 3
    # 各 entry は (task, arm, arm_order, run_index)
    run_indexes = {entry[3] for entry in plan}
    assert run_indexes == {1, 2, 3}
    # counterbalance は反復内で維持される (task index 偶奇でアーム順が反転)
    rep1 = [e for e in plan if e[3] == 1]
    assert rep1[0][1] != rep1[2][1] or len(module.ARMS) == 1


def test_expanded_plan_default_single_repeat_matches_legacy():
    module = _load("run_claude_goal_vs_mission.py")
    plan = module.expanded_plan(TASKS, module.ARMS, repeats=1)
    legacy = module.counterbalanced_plan(TASKS, module.ARMS)
    assert [(t["id"], a, o) for t, a, o, _ in plan] == [(t["id"], a, o) for t, a, o in legacy]
    assert all(entry[3] == 1 for entry in plan)


def test_run_name_suffix_only_when_repeating():
    module = _load("run_claude_goal_vs_mission.py")
    assert module.run_name_for("t1", "mission", run_index=1, repeats=1) == "t1-mission"
    assert module.run_name_for("t1", "mission", run_index=2, repeats=3) == "t1-mission-rep2"
    assert module.run_name_for("t1", "mission", run_index=1, repeats=3) == "t1-mission-rep1"


def test_summary_reports_variance_and_cost(tmp_path):
    module = _load("run_claude_goal_vs_mission.py")
    records = [
        {"arm": "mission", "run_status": "completed", "comparable_attempt": True,
         "completion": True, "validator_pass": True, "human_quality_score": 4.0,
         "intervention_count": 0, "evidence_completeness": 4.0,
         "quality_marker_score": 0.8, "elapsed_minutes": 10.0, "total_cost_usd": 4.0},
        {"arm": "mission", "run_status": "completed", "comparable_attempt": True,
         "completion": True, "validator_pass": True, "human_quality_score": 5.0,
         "intervention_count": 0, "evidence_completeness": 5.0,
         "quality_marker_score": 1.0, "elapsed_minutes": 12.0, "total_cost_usd": 6.0},
        {"arm": "claude_code_goal_command", "run_status": "completed", "comparable_attempt": True,
         "completion": True, "validator_pass": True, "human_quality_score": 4.5,
         "intervention_count": 0, "evidence_completeness": 4.5,
         "quality_marker_score": 0.9, "elapsed_minutes": 3.0, "total_cost_usd": 1.0},
    ]
    tasks_path = BENCH / "tasks.tail.json"
    summary = module.summarize(records, TASKS, "rid", "abc", tasks_path, repeats=2)
    mission = summary["arms"]["mission"]
    assert mission["cost_usd_total"] == 10.0
    assert mission["cost_usd_mean"] == 5.0
    assert abs(mission["marker_score_variance"] - 0.01) < 1e-9  # var([0.8,1.0]) 標本分散=0.02? pvariance=0.01
    goal = summary["arms"]["claude_code_goal_command"]
    assert goal["marker_score_variance"] is None  # n<2 は分散なし
    assert summary["repeats"] == 2
    assert summary["expected_records"] == len(TASKS) * len(module.ARMS) * 2


# ===== #250: mission state 抽出 =====

def _write_state(root: Path, name: str, payload: dict):
    d = root / ".mission-state" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


def test_extract_mission_state_fields(tmp_path):
    module = _load("run_claude_goal_vs_mission.py")
    _write_state(tmp_path, "abc.json", {
        "review_tier": "light", "iteration": 2, "complexity": "Simple",
        "passes": True, "halt_category": None,
        "schema_version": 3, "terminal_outcome": "completed_pass",
        "artifact_applicability": "producing",
        "artifact": {"path": "reports/out.md", "digest": "a" * 64, "size": 1, "producer_run_id": "run-1"},
        "artifact_lint_status": "clean",
        "artifact_lint_identity": {"path": "reports/out.md", "digest": "a" * 64, "size": 1, "producer_run_id": "run-1"},
        "activity_segments": [{
            "kind": "active", "reason": "work", "phase": "executing", "duration_sec": 60.0,
            "started_at": "2026-08-12T00:00:00Z", "ended_at": "2026-08-12T00:01:00Z",
        }],
        "score_history": [{"iteration": 2, "composite": 4.5, "score_provenance": {"score_source": "scoring-json"}}],
        "reviewer_count": 2,
        "review_evidence_refs": [
            {"iteration": 2, "perspective": "reviewer-a"},
            {"iteration": 2, "perspective": "reviewer-b"},
        ],
        "passes_forced": True,
        "command_outcomes": [
            {"event_id": "gate-1", "root_event_id": "gate-root", "attempt": 1, "command": "fixture", "outcome_kind": "expected-gate"},
            {"event_id": "gate-2", "root_event_id": "gate-root", "attempt": 2, "retry_of": "gate-1", "command": "fixture", "outcome_kind": "expected-gate"},
        ],
    })
    fields, note = module.extract_mission_state_fields(tmp_path)
    assert fields["mission_review_tier"] == "light"
    assert fields["mission_iterations"] == 2
    assert fields["mission_complexity"] == "Simple"
    assert fields["mission_passes"] is True
    assert fields["mission_halt_category"] is None
    assert fields["measurement_observations"] == {
        "artifact_observation_coverage": {"status": "observed", "numerator": 1, "denominator": 1},
        "activity_coverage": {"status": "observed", "numerator": 60.0, "denominator": 60.0},
        "structured_score_provenance": {"status": "observed", "numerator": 1, "denominator": 1},
        "reviewer_freshness": {"status": "observed", "numerator": 2, "denominator": 2},
        "force_pass_rate": {"status": "observed", "numerator": 1, "denominator": 1},
        "expected_gate_retry_count": {"status": "observed", "count": 1},
        "group_closeout_completeness": {"status": "unavailable", "value": None},
    }
    assert note is None


def test_extract_mission_state_picks_newest_session(tmp_path):
    import os
    module = _load("run_claude_goal_vs_mission.py")
    _write_state(tmp_path, "old.json", {"review_tier": "full", "iteration": 1})
    _write_state(tmp_path, "new.json", {"review_tier": "standard", "iteration": 3})
    old = tmp_path / ".mission-state" / "sessions" / "old.json"
    os.utime(old, (1_000_000_000, 1_000_000_000))
    fields, _ = module.extract_mission_state_fields(tmp_path)
    assert fields["mission_review_tier"] == "standard"


def test_extract_mission_state_fail_open_on_missing_or_corrupt(tmp_path):
    module = _load("run_claude_goal_vs_mission.py")

    def assert_fail_open(fields):
        assert all(value is None for key, value in fields.items() if key != "measurement_observations")
        assert all(
            value == {"status": "unavailable", "value": None}
            for value in fields["measurement_observations"].values()
        )

    fields, note = module.extract_mission_state_fields(tmp_path)  # state なし
    assert_fail_open(fields)
    assert note is not None
    d = tmp_path / ".mission-state" / "sessions"
    d.mkdir(parents=True)
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    fields2, note2 = module.extract_mission_state_fields(tmp_path)
    assert_fail_open(fields2)
    assert note2 is not None


def test_schema_accepts_instrumentation_fields():
    schema = json.loads((BENCH / "result.schema.json").read_text())
    props = schema["properties"]
    for key in ("run_index", "mission_review_tier", "mission_iterations",
                "mission_complexity", "mission_passes", "mission_halt_category",
                "measurement_observations"):
        assert key in props, key
        # 既存 JSONL の後方互換: 新フィールドは required に含めない
        assert key not in schema["required"], key
