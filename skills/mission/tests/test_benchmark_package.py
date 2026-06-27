import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"


def test_mission_vs_goal_pilot_has_exactly_ten_tasks():
    data = json.loads((BENCHMARK_DIR / "tasks.json").read_text(encoding="utf-8"))

    assert data["benchmark"] == "mission-vs-goal-pilot"
    assert data["task_count"] == 10
    assert data["arms"] == ["goal_only", "mission"]
    assert len(data["tasks"]) == 10
    assert len({task["id"] for task in data["tasks"]}) == 10


def test_mission_vs_goal_tasks_have_marketing_safe_hypotheses():
    data = json.loads((BENCHMARK_DIR / "tasks.json").read_text(encoding="utf-8"))
    required = {"id", "category", "difficulty", "prompt", "validator", "primary_metric", "hypothesis"}

    for task in data["tasks"]:
        assert required <= task.keys()
        assert "smarter" not in task["hypothesis"].lower()
        assert "always" not in task["hypothesis"].lower()
        assert task["validator"].strip()


def test_mission_vs_goal_result_schema_matches_declared_arms():
    tasks = json.loads((BENCHMARK_DIR / "tasks.json").read_text(encoding="utf-8"))
    schema = json.loads((BENCHMARK_DIR / "result.schema.json").read_text(encoding="utf-8"))

    assert schema["properties"]["benchmark"]["const"] == tasks["benchmark"]
    assert schema["properties"]["arm"]["enum"] == tasks["arms"]
    assert "human_quality_score" in schema["required"]
    assert "evidence_completeness" in schema["required"]


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

    assert "10 タスク pilot benchmark" in readme_ja
    assert "Marketing Guardrails" in readme_ja
    assert "report-template.ja.md" in readme_ja
    assert "general model benchmark ではない" in report_ja
    assert "`mission` は `/goal` より X% 賢い" in report_ja


def test_mission_vs_goal_measured_reports_are_honest_about_missing_paired_runs():
    report = (BENCHMARK_DIR / "report.md").read_text(encoding="utf-8")
    report_ja = (BENCHMARK_DIR / "report.ja.md").read_text(encoding="utf-8")

    assert "Paired benchmark runs completed | 0 / 20" in report
    assert "Benchmark + doc consistency tests | 29 passed / 29" in report
    assert "Full mission test suite | 394 passed / 394" in report
    assert "comparative performance results" in report
    assert "Paired benchmark runs completed | 0 / 20" in report_ja
    assert "Benchmark + doc consistency tests | 29 passed / 29" in report_ja
    assert "Full mission test suite | 394 passed / 394" in report_ja
    assert "比較性能の結果はまだ未測定" in report_ja


def test_mission_vs_goal_protocol_controls_review_bias():
    protocol = (BENCHMARK_DIR / "README.md").read_text(encoding="utf-8")

    assert "Counter-balance run order" in protocol
    assert "Human Quality Rubric" in protocol
    assert "score blind to arm label" in protocol


def test_mission_vs_goal_report_template_rejects_unsupported_claims():
    template = (BENCHMARK_DIR / "report-template.md").read_text(encoding="utf-8")

    assert "internal pilot, not a general model benchmark" in template
    assert "not model intelligence" in template
    assert "`mission` is X% smarter than `/goal`" in template
    assert "`mission` always beats goal-based workflows" in template
