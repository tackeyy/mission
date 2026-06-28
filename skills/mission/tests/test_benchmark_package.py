import json
import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"


def _load_official_goal_runner():
    path = BENCHMARK_DIR / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mission_vs_goal_pilot_has_exactly_ten_tasks():
    data = json.loads((BENCHMARK_DIR / "tasks.json").read_text(encoding="utf-8"))
    complex_data = json.loads((BENCHMARK_DIR / "tasks.complex.json").read_text(encoding="utf-8"))
    quality_data = json.loads((BENCHMARK_DIR / "tasks.quality.json").read_text(encoding="utf-8"))

    assert data["benchmark"] == "mission-vs-goal-pilot"
    assert data["task_count"] == 10
    assert data["arms"] == ["goal_only", "mission"]
    assert len(data["tasks"]) == 10
    assert len({task["id"] for task in data["tasks"]}) == 10
    assert complex_data["benchmark"] == "mission-vs-goal-pilot"
    assert complex_data["cohort"] == "complex"
    assert complex_data["task_count"] == 10
    assert complex_data["arms"] == ["goal_only", "mission"]
    assert len(complex_data["tasks"]) == 10
    assert len({task["id"] for task in complex_data["tasks"]}) == 10
    assert all(task["mission_complexity"] in {"Complex", "Critical"} for task in complex_data["tasks"])
    assert all(task["mission_max_iter"] >= 2 for task in complex_data["tasks"])
    assert quality_data["benchmark"] == "mission-vs-goal-pilot"
    assert quality_data["cohort"] == "quality-critical"
    assert quality_data["task_count"] == 5
    assert quality_data["arms"] == ["claude_code_goal_command", "mission"]
    assert len(quality_data["tasks"]) == 5
    assert len({task["id"] for task in quality_data["tasks"]}) == 5
    assert all(len(task["quality_markers"]) >= 7 for task in quality_data["tasks"])


def test_mission_vs_goal_tasks_have_marketing_safe_hypotheses():
    task_sets = [
        json.loads((BENCHMARK_DIR / "tasks.json").read_text(encoding="utf-8")),
        json.loads((BENCHMARK_DIR / "tasks.complex.json").read_text(encoding="utf-8")),
        json.loads((BENCHMARK_DIR / "tasks.quality.json").read_text(encoding="utf-8")),
    ]
    required = {"id", "category", "difficulty", "prompt", "validator", "primary_metric", "hypothesis"}

    for data in task_sets:
        for task in data["tasks"]:
            assert required <= task.keys()
            assert "smarter" not in task["hypothesis"].lower()
            assert "always" not in task["hypothesis"].lower()
            assert task["validator"].strip()


def test_mission_vs_goal_result_schema_matches_declared_arms():
    tasks = json.loads((BENCHMARK_DIR / "tasks.json").read_text(encoding="utf-8"))
    schema = json.loads((BENCHMARK_DIR / "result.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["benchmark"]["const"] == tasks["benchmark"]
    assert set(tasks["arms"]) <= set(schema["properties"]["arm"]["enum"])
    assert "claude_code_goal_command" in schema["properties"]["arm"]["enum"]
    assert schema["properties"]["mission_profile"]["enum"] == ["full", "light", "quality", None]
    assert schema["properties"]["quality_marker_score"]["maximum"] == 1
    assert schema["properties"]["quality_markers_matched"]["type"] == "array"
    assert schema["properties"]["quality_markers_missing"]["type"] == "array"
    assert "human_quality_score" in schema["required"]
    assert "quality_score_method" in schema["required"]
    assert "automated_heuristic_not_blind_human" in schema["properties"]["quality_score_method"]["enum"]
    assert "evidence_completeness" in schema["required"]
    assert schema["properties"]["run_status"]["enum"] == ["completed", "failed", "blocked"]
    assert "api_usage_limit" in schema["properties"]["blocked_reason"]["enum"]
    assert "max_budget_usd" in schema["properties"]["blocked_reason"]["enum"]
    assert "timeout" in schema["properties"]["blocked_reason"]["enum"]
    assert "max_budget_usd" in schema["properties"]["failure_kind"]["enum"]
    assert schema["properties"]["comparable_attempt"]["type"] == "boolean"


def test_mission_vs_goal_docs_link_benchmark_package():
    loop_doc = (REPO_ROOT / "docs" / "LOOP_ENGINEERING.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_ja = (REPO_ROOT / "README.ja.md").read_text(encoding="utf-8")

    expected_en = "benchmarks/mission-vs-goal/README.md"
    expected_ja = "benchmarks/mission-vs-goal/README.ja.md"
    assert expected_en in loop_doc
    assert expected_en in readme
    assert expected_ja in readme_ja


def test_mission_vs_goal_has_japanese_benchmark_docs():
    readme_ja = (BENCHMARK_DIR / "README.ja.md").read_text(encoding="utf-8")
    report_ja = (BENCHMARK_DIR / "report-template.ja.md").read_text(encoding="utf-8")
    complex_plan_ja = (BENCHMARK_DIR / "complex-validation-plan.ja.md").read_text(encoding="utf-8")

    assert "10 タスク pilot benchmark" in readme_ja
    assert "Marketing Guardrails" in readme_ja
    assert "report-template.ja.md" in readme_ja
    assert "tasks.complex.json" in readme_ja
    assert "claude_code_goal_command" in readme_ja
    assert "general model benchmark ではない" in report_ja
    assert "`mission` は `/goal` より X% 賢い" in report_ja
    assert "workspace API usage limit" in complex_plan_ja
    assert "結果ではありません" in complex_plan_ja
    assert (BENCHMARK_DIR / "official-goal-rerun-runbook.ja.md").exists()


def test_mission_artifact_required_smoke_result_is_marketing_safe():
    smoke_path = BENCHMARK_DIR / "results" / "2026-06-28-mission-artifact-required-smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))

    assert smoke["benchmark"] == "mission-artifact-required-smoke"
    assert smoke["scope"] == "local CLI smoke, not a paired /goal comparison"
    assert smoke["artifact_required"] is True
    assert smoke["artifact_completion"] is True
    assert smoke["validator_pass"] is True
    assert smoke["quality_score_method"] == "unit_test_backed_cli_smoke_not_blind_human"
    assert "mission artifact support outperforms Claude Code /goal" in smoke["claims_not_supported"]
    assert "25 passed" in smoke["verification"]["focused_result"]


def test_mission_vs_goal_measured_reports_are_honest_about_paired_runs():
    report = (BENCHMARK_DIR / "report.md").read_text(encoding="utf-8")
    report_ja = (BENCHMARK_DIR / "report.ja.md").read_text(encoding="utf-8")
    results_path = BENCHMARK_DIR / "results" / "2026-06-27-codex-cli-local.jsonl"
    summary_path = BENCHMARK_DIR / "results" / "2026-06-27-codex-cli-local-summary.json"
    official_results_path = BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-smoke-v2.jsonl"
    official_summary_path = BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-smoke-v2-summary.json"
    official_rerun_smoke_path = BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-smoke-v3.jsonl"
    official_rerun_smoke_summary_path = (
        BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-smoke-v3-summary.json"
    )
    official_rerun_full_path = BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-complex-v1.jsonl"
    official_rerun_full_summary_path = (
        BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-complex-v1-summary.json"
    )
    official_incremental_path = BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-incremental-v1.jsonl"
    official_incremental_summary_path = (
        BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-incremental-v1-summary.json"
    )
    official_light_path = BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-light-v1.jsonl"
    official_light_summary_path = (
        BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-light-v1-summary.json"
    )
    official_quality_path = BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-quality-v1.jsonl"
    official_quality_summary_path = (
        BENCHMARK_DIR / "results" / "2026-06-28-claude-goal-vs-mission-quality-v1-summary.json"
    )
    official_mission_raw = (
        BENCHMARK_DIR
        / "artifacts"
        / "2026-06-28-claude-goal-vs-mission-smoke-v2"
        / "complex-cross-file-feature-mission"
        / "claude-result.json"
    )

    records = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    by_arm = {
        arm: [record for record in records if record["arm"] == arm]
        for arm in ("goal_only", "mission")
    }

    assert len(records) == 20
    assert len(by_arm["goal_only"]) == 10
    assert len(by_arm["mission"]) == 10
    assert all(record["completion"] for record in records)
    assert all(record["validator_pass"] for record in records)
    assert {record["quality_score_method"] for record in records} == {"automated_heuristic_not_blind_human"}
    assert summary["records"] == 20
    assert summary["expected_records"] == 20
    assert summary["arms"]["goal_only"]["average_quality_score"] == 4.0
    assert summary["arms"]["mission"]["average_quality_score"] == 4.5
    assert summary["arms"]["goal_only"]["average_evidence_completeness"] == 3.8
    assert summary["arms"]["mission"]["average_evidence_completeness"] == 4.7

    official_records = [json.loads(line) for line in official_results_path.read_text(encoding="utf-8").splitlines()]
    official_summary = json.loads(official_summary_path.read_text(encoding="utf-8"))
    official_by_arm = {
        arm: [record for record in official_records if record["arm"] == arm]
        for arm in ("claude_code_goal_command", "mission")
    }

    assert len(official_records) == 2
    assert official_summary["records"] == 2
    assert official_summary["expected_records"] == 2
    assert official_by_arm["claude_code_goal_command"][0]["completion"] is True
    assert official_by_arm["claude_code_goal_command"][0]["validator_pass"] is True
    assert official_by_arm["mission"][0]["completion"] is False
    assert official_by_arm["mission"][0]["validator_pass"] is False
    assert "workspace API usage limits" in official_mission_raw.read_text(encoding="utf-8")

    rerun_smoke_records = [
        json.loads(line) for line in official_rerun_smoke_path.read_text(encoding="utf-8").splitlines()
    ]
    rerun_smoke_summary = json.loads(official_rerun_smoke_summary_path.read_text(encoding="utf-8"))
    rerun_smoke_by_arm = {
        arm: [record for record in rerun_smoke_records if record["arm"] == arm]
        for arm in ("claude_code_goal_command", "mission")
    }
    assert len(rerun_smoke_records) == 2
    assert rerun_smoke_summary["records"] == 2
    assert rerun_smoke_summary["expected_records"] == 2
    assert all(record["run_status"] == "completed" for record in rerun_smoke_records)
    assert all(record["comparable_attempt"] is True for record in rerun_smoke_records)
    assert all(record["completion"] is True for record in rerun_smoke_records)
    assert all(record["validator_pass"] is True for record in rerun_smoke_records)
    assert rerun_smoke_by_arm["claude_code_goal_command"][0]["human_quality_score"] == 4.0
    assert rerun_smoke_by_arm["mission"][0]["human_quality_score"] == 4.0
    assert rerun_smoke_by_arm["claude_code_goal_command"][0]["elapsed_minutes"] == 1.7
    assert rerun_smoke_by_arm["mission"][0]["elapsed_minutes"] == 6.5

    rerun_full_records = [
        json.loads(line) for line in official_rerun_full_path.read_text(encoding="utf-8").splitlines()
    ]
    rerun_full_summary = json.loads(official_rerun_full_summary_path.read_text(encoding="utf-8"))
    rerun_full_by_arm = {
        arm: [record for record in rerun_full_records if record["arm"] == arm]
        for arm in ("claude_code_goal_command", "mission")
    }
    assert len(rerun_full_records) == 20
    assert rerun_full_summary["records"] == 20
    assert rerun_full_summary["expected_records"] == 20
    assert all(record["run_status"] == "blocked" for record in rerun_full_records)
    assert all(record["blocked_reason"] == "api_usage_limit" for record in rerun_full_records)
    assert all(record["comparable_attempt"] is False for record in rerun_full_records)
    assert len(rerun_full_by_arm["claude_code_goal_command"]) == 10
    assert len(rerun_full_by_arm["mission"]) == 10
    assert rerun_full_summary["arms"]["claude_code_goal_command"]["blocked_records"] == 10
    assert rerun_full_summary["arms"]["mission"]["blocked_records"] == 10

    incremental_records = [
        json.loads(line) for line in official_incremental_path.read_text(encoding="utf-8").splitlines()
    ]
    incremental_summary = json.loads(official_incremental_summary_path.read_text(encoding="utf-8"))
    incremental_by_arm = {
        arm: [record for record in incremental_records if record["arm"] == arm]
        for arm in ("claude_code_goal_command", "mission")
    }
    assert incremental_summary["selected_task_ids"] == [
        "complex-failing-test-triage",
        "complex-review-thread-resolution",
    ]
    assert incremental_summary["records"] == 4
    assert incremental_summary["expected_records"] == 4
    assert incremental_summary["arms"]["claude_code_goal_command"]["comparable_records"] == 2
    assert incremental_summary["arms"]["mission"]["comparable_records"] == 0
    assert all(record["run_status"] == "completed" for record in incremental_by_arm["claude_code_goal_command"])
    assert all(record["validator_pass"] is True for record in incremental_by_arm["claude_code_goal_command"])
    assert all(record["run_status"] == "blocked" for record in incremental_by_arm["mission"])
    assert all(record["blocked_reason"] == "max_budget_usd" for record in incremental_by_arm["mission"])
    assert all(record["comparable_attempt"] is False for record in incremental_by_arm["mission"])

    light_records = [json.loads(line) for line in official_light_path.read_text(encoding="utf-8").splitlines()]
    light_summary = json.loads(official_light_summary_path.read_text(encoding="utf-8"))
    light_by_arm = {
        arm: [record for record in light_records if record["arm"] == arm]
        for arm in ("claude_code_goal_command", "mission")
    }
    assert light_summary["selected_task_ids"] == ["complex-failing-test-triage"]
    assert light_summary["mission_profile"] == "light"
    assert light_summary["records"] == 2
    assert light_summary["expected_records"] == 2
    assert light_summary["arms"]["claude_code_goal_command"]["comparable_records"] == 1
    assert light_summary["arms"]["mission"]["comparable_records"] == 1
    assert all(record["run_status"] == "completed" for record in light_records)
    assert all(record["validator_pass"] is True for record in light_records)
    assert light_by_arm["mission"][0]["mission_profile"] == "light"
    assert light_by_arm["claude_code_goal_command"][0]["elapsed_minutes"] == 9.56
    assert light_by_arm["mission"][0]["elapsed_minutes"] == 5.27

    quality_records = [json.loads(line) for line in official_quality_path.read_text(encoding="utf-8").splitlines()]
    quality_summary = json.loads(official_quality_summary_path.read_text(encoding="utf-8"))
    assert quality_summary["selected_task_ids"] == ["quality-critical-release-governance"]
    assert quality_summary["mission_profile"] == "quality"
    assert quality_summary["task_cohort"] == "quality"
    assert quality_summary["records"] == 1
    assert quality_summary["expected_records"] == 2
    assert quality_summary["stopped_early"] is True
    assert quality_summary["arms"]["claude_code_goal_command"]["blocked_records"] == 1
    assert quality_summary["arms"]["claude_code_goal_command"]["comparable_records"] == 0
    assert quality_summary["arms"]["claude_code_goal_command"]["average_quality_marker_score"] is None
    assert quality_summary["arms"]["mission"]["records"] == 0
    assert quality_records[0]["arm"] == "claude_code_goal_command"
    assert quality_records[0]["run_status"] == "blocked"
    assert quality_records[0]["blocked_reason"] == "api_usage_limit"
    assert quality_records[0]["quality_marker_score"] is None

    assert "Paired benchmark runs completed | 20 / 20" in report
    assert "Goal-only runs completed | 10 / 10" in report
    assert "Mission runs completed | 10 / 10" in report
    assert "Quality score method | automated heuristic" in report
    assert "Average quality score | 4.00 / 5 | 4.50 / 5" in report
    assert "Average evidence completeness | 3.80 / 5 | 4.70 / 5" in report
    assert "Benchmark + doc consistency tests | 30 passed / 30" in report
    assert "Full mission test suite | 402 passed / 402" in report
    assert "not a blind human evaluation" in report
    assert "Claude Code Official `/goal` Smoke" in report
    assert "workspace API usage limit" in report
    assert "does not support a marketing claim that either arm is better" in report
    assert "Claude Code Official `/goal` Rerun After API Limit Increase" in report
    assert "Run id | `2026-06-28-claude-goal-vs-mission-smoke-v3`" in report
    assert "Average elapsed minutes | 1.70 | 6.50" in report
    assert "Run id | `2026-06-28-claude-goal-vs-mission-complex-v1`" in report
    assert "Blocked records | 20 / 20" in report
    assert "Denominator is zero" in report
    assert "Cost-Controlled Incremental Rerun" in report
    assert "Run id | `2026-06-28-claude-goal-vs-mission-incremental-v1`" in report
    assert "Max-budget blocked records | 2 / 4" in report
    assert "Total Claude cost recorded | USD 9.39057695" in report
    assert "`/goal` completed both tasks; `/mission` did not return success" in report
    assert "Lightweight Mission Profile Rerun" in report
    assert "Run id | `2026-06-28-claude-goal-vs-mission-light-v1`" in report
    assert "Mission profile | `light`" in report
    assert "Average elapsed minutes | 9.56 | 5.27" in report
    assert "Recorded Claude cost | USD 3.00670750 | USD 2.00569500" in report
    assert "Quality-Focused Critical Task Attempt" in report
    assert "Run id | `2026-06-28-claude-goal-vs-mission-quality-v1`" in report
    assert "Mission profile | `quality`" in report
    assert "Records written | 1 / 2" in report
    assert "`/mission` records | 0" in report
    assert "Quality-marker comparison | unavailable" in report
    assert "Paired benchmark runs completed | 20 / 20" in report_ja
    assert "Goal-only runs completed | 10 / 10" in report_ja
    assert "Mission runs completed | 10 / 10" in report_ja
    assert "Quality score method | automated heuristic" in report_ja
    assert "Average quality score | 4.00 / 5 | 4.50 / 5" in report_ja
    assert "Average evidence completeness | 3.80 / 5 | 4.70 / 5" in report_ja
    assert "Benchmark + doc consistency tests | 30 passed / 30" in report_ja
    assert "Full mission test suite | 402 passed / 402" in report_ja
    assert "blind human evaluation でもありません" in report_ja
    assert "Claude Code 公式 `/goal` smoke" in report_ja
    assert "workspace API usage limit" in report_ja
    assert "どちらが優れているという marketing claim は出せない" in report_ja
    assert "API limit 引き上げ後の Claude Code 公式 `/goal` 再実行" in report_ja
    assert "Run id | `2026-06-28-claude-goal-vs-mission-smoke-v3`" in report_ja
    assert "Average elapsed minutes | 1.70 | 6.50" in report_ja
    assert "Run id | `2026-06-28-claude-goal-vs-mission-complex-v1`" in report_ja
    assert "Blocked records | 20 / 20" in report_ja
    assert "denominator が 0" in report_ja
    assert "Cost-controlled incremental rerun" in report_ja
    assert "Run id | `2026-06-28-claude-goal-vs-mission-incremental-v1`" in report_ja
    assert "Max-budget blocked records | 2 / 4" in report_ja
    assert "Total Claude cost recorded | USD 9.39057695" in report_ja
    assert "`/goal` は両 task を完了、`/mission` は success を返せなかった" in report_ja
    assert "Lightweight mission profile rerun" in report_ja
    assert "Run id | `2026-06-28-claude-goal-vs-mission-light-v1`" in report_ja
    assert "Mission profile | `light`" in report_ja
    assert "Average elapsed minutes | 9.56 | 5.27" in report_ja
    assert "Recorded Claude cost | USD 3.00670750 | USD 2.00569500" in report_ja
    assert "Quality-focused critical task attempt" in report_ja
    assert "Run id | `2026-06-28-claude-goal-vs-mission-quality-v1`" in report_ja
    assert "Mission profile | `quality`" in report_ja
    assert "Records written | 1 / 2" in report_ja
    assert "`/mission` records | 0" in report_ja
    assert "Quality-marker comparison | unavailable" in report_ja


def test_mission_vs_goal_protocol_controls_review_bias():
    protocol = (BENCHMARK_DIR / "README.md").read_text(encoding="utf-8")
    runner = (BENCHMARK_DIR / "run_paired_pilot.py").read_text(encoding="utf-8")
    official_runner = (BENCHMARK_DIR / "run_claude_goal_vs_mission.py").read_text(encoding="utf-8")
    complex_plan = (BENCHMARK_DIR / "complex-validation-plan.md").read_text(encoding="utf-8")
    runbook = (BENCHMARK_DIR / "official-goal-rerun-runbook.md").read_text(encoding="utf-8")
    runner_module = _load_official_goal_runner()

    assert "Counter-balance run order" in protocol
    assert "Human Quality Rubric" in protocol
    assert "score blind to arm label" in protocol
    assert "--tasks-file" in protocol
    assert "--run-id" in protocol
    assert "workspace API usage limits" in complex_plan
    assert "These are hypotheses to test, not results" in complex_plan
    assert "run_status" in protocol
    assert "blocked_reason" in protocol
    assert "comparable_attempt" in protocol
    assert "official-goal-rerun-runbook.md" in protocol
    assert "tasks.quality.json" in protocol
    assert "--mission-profile quality" in protocol
    assert "Proceed only if both arm records have" in runbook
    assert "Step 2d: Quality-Focused Critical Pilot" in runbook
    assert "run_status=blocked" in runbook
    assert "2026-07-01 09:00 JST" in runbook
    assert 'parser.add_argument("--tasks-file"' in runner
    assert 'parser.add_argument("--run-id"' in runner
    assert 'ARMS = ("claude_code_goal_command", "mission")' in official_runner
    assert 'parser.add_argument("--mission-max-iter"' in official_runner
    assert '"--mission-profile"' in official_runner
    assert 'MISSION_PROFILES = ("full", "light", "quality")' in official_runner
    assert "quality_marker_score" in official_runner
    assert '"--task-ids"' in official_runner
    assert '"--stop-on-blocked"' in official_runner
    assert '"run_status": evaluation["run_status"]' in official_runner
    task_data = json.loads((BENCHMARK_DIR / "tasks.complex.json").read_text(encoding="utf-8"))
    selected = runner_module.select_tasks(
        task_data,
        limit_tasks=10,
        task_ids="complex-failing-test-triage,complex-review-thread-resolution",
    )
    assert [task["id"] for task in selected] == [
        "complex-failing-test-triage",
        "complex-review-thread-resolution",
    ]
    limited = runner_module.select_tasks(task_data, limit_tasks=1)
    assert [task["id"] for task in limited] == ["complex-cross-file-feature"]
    light_prompt = runner_module.build_prompt(
        task_data["tasks"][1],
        "mission",
        "benchmarks/mission-vs-goal/run-output/test.md",
        mission_profile="light",
    )
    assert "Mission profile: light" in light_prompt
    assert "--max-iter 1" in light_prompt
    assert "Lightweight benchmark profile" in light_prompt
    light_summary = runner_module.summarize(
        records=[],
        tasks=selected,
        run_id="test-light",
        starting_commit="abcdef0",
        tasks_path=BENCHMARK_DIR / "tasks.complex.json",
        mission_profile="light",
    )
    assert light_summary["mission_profile"] == "light"
    quality_data = json.loads((BENCHMARK_DIR / "tasks.quality.json").read_text(encoding="utf-8"))
    quality_prompt = runner_module.build_prompt(
        quality_data["tasks"][0],
        "mission",
        "benchmarks/mission-vs-goal/run-output/quality-test.md",
        mission_profile="quality",
    )
    assert "Mission profile: quality" in quality_prompt
    assert "Quality benchmark profile" in quality_prompt
    assert "Evidence Map" in quality_prompt
    marker_eval = runner_module.evaluate_quality_markers(
        "Evidence Map\nRejected Hypotheses\nStop/Proceed Decision\nUnsupported Claims",
        quality_data["tasks"][0],
    )
    assert marker_eval["quality_markers_total"] == 7
    assert marker_eval["quality_marker_score"] == 0.57
    assert marker_eval["quality_markers_matched"] == [
        "Evidence Map",
        "Rejected Hypotheses",
        "Stop/Proceed Decision",
        "Unsupported Claims",
    ]
    quality_summary = runner_module.summarize(
        records=[
            {
                "arm": "claude_code_goal_command",
                "completion": True,
                "validator_pass": True,
                "human_quality_score": 4.0,
                "intervention_count": 0,
                "evidence_completeness": 4.0,
                "elapsed_minutes": 1.0,
                "quality_marker_score": 0.57,
            },
            {
                "arm": "mission",
                "completion": True,
                "validator_pass": True,
                "human_quality_score": 5.0,
                "intervention_count": 0,
                "evidence_completeness": 5.0,
                "elapsed_minutes": 2.0,
                "quality_marker_score": 1.0,
            },
        ],
        tasks=quality_data["tasks"][:1],
        run_id="test-quality",
        starting_commit="abcdef0",
        tasks_path=BENCHMARK_DIR / "tasks.quality.json",
        mission_profile="quality",
    )
    assert quality_summary["mission_profile"] == "quality"
    assert quality_summary["arms"]["claude_code_goal_command"]["average_quality_marker_score"] == 0.57
    assert quality_summary["arms"]["mission"]["average_quality_marker_score"] == 1.0
    blocked = runner_module.classify_run_status(
        stdout="API Error: 400 You have reached your specified workspace API usage limits.",
        stderr="",
        timed_out=False,
        returncode=1,
        output_exists=False,
        validator_pass=False,
    )
    assert blocked == {
        "run_status": "blocked",
        "blocked_reason": "api_usage_limit",
        "failure_kind": "api_usage_limit",
        "comparable_attempt": False,
    }
    max_budget = runner_module.classify_run_status(
        stdout='{"subtype":"error_max_budget_usd"}',
        stderr="",
        timed_out=False,
        returncode=1,
        output_exists=True,
        validator_pass=False,
    )
    assert max_budget == {
        "run_status": "blocked",
        "blocked_reason": "max_budget_usd",
        "failure_kind": "max_budget_usd",
        "comparable_attempt": False,
    }
    completed_with_usage_limit_text = runner_module.classify_run_status(
        stdout="Artifact complete. Rejected hypothesis: prior API usage limit was not the current cause.",
        stderr="",
        timed_out=False,
        returncode=0,
        output_exists=True,
        validator_pass=True,
    )
    assert completed_with_usage_limit_text == {
        "run_status": "completed",
        "blocked_reason": None,
        "failure_kind": None,
        "comparable_attempt": True,
    }


def test_mission_vs_goal_report_template_rejects_unsupported_claims():
    template = (BENCHMARK_DIR / "report-template.md").read_text(encoding="utf-8")

    assert "internal pilot, not a general model benchmark" in template
    assert "not model intelligence" in template
    assert "`mission` is X% smarter than `/goal`" in template
    assert "`mission` always beats goal-based workflows" in template
