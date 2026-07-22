"""Issue #251: openworld-discovery cohort — open-world tasks without pre-enumerated finding lists.

Contract under test:
- `tasks.openworld.json` defines a cohort where the solver must independently
  discover findings (divergences, contradictions, root causes) without being
  told what to look for.
- Decoys are scored via `forbidden_markers`: claiming a consistent value as a
  contradiction or a ruled-out hypothesis as the root cause subtracts from the
  net marker score.
- The runner removes the answer key (`hidden_paths`) from the cloned worktree
  before either arm runs, and injects cohort `prompt_rules` into both prompts.
"""

import json
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
OPENWORLD_TASKS_PATH = BENCHMARK_DIR / "tasks.openworld.json"


def _load_official_goal_runner():
    path = BENCHMARK_DIR / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _openworld_data() -> dict:
    return json.loads(OPENWORLD_TASKS_PATH.read_text(encoding="utf-8"))


def test_openworld_cohort_structure():
    data = _openworld_data()

    assert data["benchmark"] == "mission-vs-goal-pilot"
    assert data["cohort"] == "openworld-discovery"
    assert data["arms"] == ["claude_code_goal_command", "mission"]
    assert data["task_count"] == 3
    assert len(data["tasks"]) == 3
    assert len({task["id"] for task in data["tasks"]}) == 3

    assert "benchmarks/mission-vs-goal/tasks.openworld.json" in data["hidden_paths"]
    assert data["prompt_rules"]
    assert any("benchmarks/mission-vs-goal" in rule for rule in data["prompt_rules"])

    required = {
        "id", "category", "difficulty", "prompt", "validator", "primary_metric",
        "hypothesis", "fixtures", "first_pass_failure_design",
        "quality_markers", "forbidden_markers",
    }
    for task in data["tasks"]:
        assert required <= task.keys(), f"{task.get('id')} missing keys"
        assert task["id"].startswith("openworld-")
        assert task["mission_complexity"] in {"Complex", "Critical"}
        assert task["mission_max_iter"] >= 2
        assert task["markers_hidden"] is True
        assert task["validator"].strip()
        assert "smarter" not in task["hypothesis"].lower()
        assert "always" not in task["hypothesis"].lower()
        assert len(task["quality_markers"]) >= 3
        for marker in task["quality_markers"]:
            assert isinstance(marker, dict) and marker.get("patterns"), \
                f"{task['id']} markers must be dicts with content-token patterns"
        assert len(task["forbidden_markers"]) >= 1
        for marker in task["forbidden_markers"]:
            assert isinstance(marker, dict) and marker.get("patterns")
        assert task["fixtures"]
        for rel in task["fixtures"]:
            fixture = REPO_ROOT / rel
            assert fixture.is_file(), f"missing fixture {rel}"
            assert rel in task["prompt"], f"{task['id']} prompt must name {rel}"
            text = fixture.read_text(encoding="utf-8").lower()
            for leak in ("defect", "decoy", "planted", "forbidden_markers"):
                assert leak not in text, f"{rel} leaks answer-key vocabulary: {leak}"


def test_openworld_hypotheses_mention_open_world():
    data = _openworld_data()
    for task in data["tasks"]:
        assert "open-world" in task["hypothesis"].lower() or "open world" in task["hypothesis"].lower(), \
            f"{task['id']} hypothesis should mention open-world discovery"


def test_constant_hunt_markers_detect_real_divergence():
    module = _load_official_goal_runner()
    task = _openworld_data()["tasks"][0]
    assert task["id"] == "openworld-constant-hunt"

    good_text = (
        "The worker service has CONNECT_TIMEOUT_MS set to 6500, which diverges "
        "from the canonical default of 4000 defined in the shared defaults. "
        "The gateway service matches the canonical default."
    )
    result = module.evaluate_quality_markers(good_text, task)
    assert result["quality_marker_recall"] == 1.0
    assert result["forbidden_markers_matched"] == []
    assert result["quality_marker_score"] == 1.0


def test_constant_hunt_penalizes_false_read_timeout_divergence():
    module = _load_official_goal_runner()
    task = _openworld_data()["tasks"][0]

    false_positive_text = (
        "Found 6500 vs 4000 for CONNECT_TIMEOUT_MS. Gateway is fine. "
        "Also READ_TIMEOUT_MS diverges from the spec."
    )
    result = module.evaluate_quality_markers(false_positive_text, task)
    assert "False divergence: READ_TIMEOUT_MS" in result["forbidden_markers_matched"]


def test_contradiction_chain_markers_detect_latency_mismatch():
    module = _load_official_goal_runner()
    task = _openworld_data()["tasks"][1]
    assert task["id"] == "openworld-contradiction-chain"

    good_text = (
        "The implementation guide specifies a latency SLO of 250 ms, which "
        "contradicts the product spec's 200 ms. The product spec is the source "
        "of truth. The runbook's '6 tries' is consistent with the retry limit "
        "of 5 because it includes the initial attempt."
    )
    result = module.evaluate_quality_markers(good_text, task)
    assert result["quality_marker_recall"] == 1.0
    assert result["forbidden_markers_matched"] == []


def test_contradiction_chain_penalizes_false_retry_contradiction():
    module = _load_official_goal_runner()
    task = _openworld_data()["tasks"][1]

    false_positive_text = (
        "Latency SLO is 250 ms vs 200 ms in spec. Implementation guide is wrong. "
        "Also, 6 tries contradicts the spec's retry limit of 5."
    )
    result = module.evaluate_quality_markers(false_positive_text, task)
    assert "False contradiction: 6 tries vs retry limit 5" in result["forbidden_markers_matched"]


def test_incremental_reveal_markers_identify_migration_root_cause():
    module = _load_official_goal_runner()
    task = _openworld_data()["tasks"][2]
    assert task["id"] == "openworld-incremental-reveal"

    good_text = (
        "The root cause is the runaway migration job that held an exclusive lock "
        "on the orders table. Connection pool saturation started at 01:02, before "
        "the 01:05 deploy. The serializer rollback did not help, confirming it was "
        "not the cause."
    )
    result = module.evaluate_quality_markers(good_text, task)
    assert result["quality_marker_recall"] == 1.0
    assert result["forbidden_markers_matched"] == []
    assert result["quality_marker_score"] == 1.0


def test_incremental_reveal_penalizes_false_serializer_blame():
    module = _load_official_goal_runner()
    task = _openworld_data()["tasks"][2]

    false_blame_text = (
        "The migration job was involved. The exclusive lock on orders table was "
        "the issue. Pool saturation started at 01:02. Rollback did not help. "
        "But the serializer is the root cause of the failure."
    )
    result = module.evaluate_quality_markers(false_blame_text, task)
    assert "False root cause: serializer deploy" in result["forbidden_markers_matched"]


def test_build_prompt_injects_cohort_rules_for_openworld():
    module = _load_official_goal_runner()
    task = {
        "id": "openworld-example",
        "category": "analysis",
        "prompt": "Example prompt.",
        "validator": "Example validator.",
    }
    rule = "Do not read benchmark metadata."
    for arm in ("claude_code_goal_command", "mission"):
        prompt = module.build_prompt(task, arm, "out.md", extra_rules=[rule])
        assert rule in prompt
        assert prompt.index(rule) < prompt.index("Task id:")


def test_result_schema_covers_openworld_scoring_fields():
    schema = json.loads((BENCHMARK_DIR / "result.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["quality_marker_recall"]["maximum"] == 1
    assert schema["properties"]["forbidden_markers_matched"]["type"] == "array"
    assert schema["properties"]["quality_marker_score"]["minimum"] == 0
