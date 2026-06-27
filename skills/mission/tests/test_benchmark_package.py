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


def test_mission_vs_goal_tasks_have_marketing_safe_hypotheses():
    task_sets = [
        json.loads((BENCHMARK_DIR / "tasks.json").read_text(encoding="utf-8")),
        json.loads((BENCHMARK_DIR / "tasks.complex.json").read_text(encoding="utf-8")),
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
    assert "human_quality_score" in schema["required"]
    assert "quality_score_method" in schema["required"]
    assert "automated_heuristic_not_blind_human" in schema["properties"]["quality_score_method"]["enum"]
    assert "evidence_completeness" in schema["required"]
    assert schema["properties"]["run_status"]["enum"] == ["completed", "failed", "blocked"]
    assert "api_usage_limit" in schema["properties"]["blocked_reason"]["enum"]
    assert "timeout" in schema["properties"]["blocked_reason"]["enum"]
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

    assert "Paired benchmark runs completed | 20 / 20" in report
    assert "Goal-only runs completed | 10 / 10" in report
    assert "Mission runs completed | 10 / 10" in report
    assert "Quality score method | automated heuristic" in report
    assert "Average quality score | 4.00 / 5 | 4.50 / 5" in report
    assert "Average evidence completeness | 3.80 / 5 | 4.70 / 5" in report
    assert "Benchmark + doc consistency tests | 29 passed / 29" in report
    assert "Full mission test suite | 394 passed / 394" in report
    assert "not a blind human evaluation" in report
    assert "Claude Code Official `/goal` Smoke" in report
    assert "workspace API usage limit" in report
    assert "does not support a marketing claim that either arm is better" in report
    assert "Paired benchmark runs completed | 20 / 20" in report_ja
    assert "Goal-only runs completed | 10 / 10" in report_ja
    assert "Mission runs completed | 10 / 10" in report_ja
    assert "Quality score method | automated heuristic" in report_ja
    assert "Average quality score | 4.00 / 5 | 4.50 / 5" in report_ja
    assert "Average evidence completeness | 3.80 / 5 | 4.70 / 5" in report_ja
    assert "Benchmark + doc consistency tests | 29 passed / 29" in report_ja
    assert "Full mission test suite | 394 passed / 394" in report_ja
    assert "blind human evaluation でもありません" in report_ja
    assert "Claude Code 公式 `/goal` smoke" in report_ja
    assert "workspace API usage limit" in report_ja
    assert "どちらが優れているという marketing claim は出せない" in report_ja


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
    assert "Proceed only if both arm records have" in runbook
    assert "run_status=blocked" in runbook
    assert "2026-07-01 09:00 JST" in runbook
    assert 'parser.add_argument("--tasks-file"' in runner
    assert 'parser.add_argument("--run-id"' in runner
    assert 'ARMS = ("claude_code_goal_command", "mission")' in official_runner
    assert 'parser.add_argument("--mission-max-iter"' in official_runner
    assert '"run_status": evaluation["run_status"]' in official_runner
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


def test_mission_vs_goal_report_template_rejects_unsupported_claims():
    template = (BENCHMARK_DIR / "report-template.md").read_text(encoding="utf-8")

    assert "internal pilot, not a general model benchmark" in template
    assert "not model intelligence" in template
    assert "`mission` is X% smarter than `/goal`" in template
    assert "`mission` always beats goal-based workflows" in template
