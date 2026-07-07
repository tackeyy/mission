"""Issue #153: tail-first-failure cohort — planted-defect tasks that remove the ceiling effect.

Contract under test:
- `tasks.tail.json` defines a cohort whose quality markers are defect-specific
  content tokens (recall of planted defects), not structure/section titles.
- Decoys are scored via `forbidden_markers`: claiming a decoy as a defect
  subtracts from the net marker score (false-positive penalty).
- The runner removes the answer key (`hidden_paths`) from the cloned worktree
  before either arm runs, and injects cohort `prompt_rules` into both prompts.
"""

import json
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
TAIL_TASKS_PATH = BENCHMARK_DIR / "tasks.tail.json"


def _load_official_goal_runner():
    path = BENCHMARK_DIR / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tail_data() -> dict:
    return json.loads(TAIL_TASKS_PATH.read_text(encoding="utf-8"))


def test_tail_cohort_structure_targets_first_pass_failure():
    data = _tail_data()

    assert data["benchmark"] == "mission-vs-goal-pilot"
    assert data["cohort"] == "tail-first-failure"
    assert data["arms"] == ["claude_code_goal_command", "mission"]
    assert data["task_count"] == 5
    assert len(data["tasks"]) == 5
    assert len({task["id"] for task in data["tasks"]}) == 5

    # The answer key must never be readable by either arm inside the clone.
    assert "benchmarks/mission-vs-goal/tasks.tail.json" in data["hidden_paths"]
    # Both arms get the same out-of-bounds rule for benchmark metadata.
    assert data["prompt_rules"]
    assert any("benchmarks/mission-vs-goal" in rule for rule in data["prompt_rules"])

    required = {
        "id", "category", "difficulty", "prompt", "validator", "primary_metric",
        "hypothesis", "fixtures", "first_pass_failure_design",
        "quality_markers", "forbidden_markers",
    }
    for task in data["tasks"]:
        assert required <= task.keys(), f"{task.get('id')} missing keys"
        assert task["id"].startswith("tail-")
        assert task["mission_complexity"] in {"Complex", "Critical"}
        assert task["mission_max_iter"] >= 2
        # Markers are the answer key; the prompt must never enumerate them.
        assert task["markers_hidden"] is True
        assert task["validator"].strip()
        assert "smarter" not in task["hypothesis"].lower()
        assert "always" not in task["hypothesis"].lower()
        # Recall markers: at least five planted, defect-specific tokens.
        assert len(task["quality_markers"]) >= 5
        for marker in task["quality_markers"]:
            assert isinstance(marker, dict) and marker.get("patterns"), \
                f"{task['id']} markers must be dicts with content-token patterns"
        # At least one decoy with a false-positive penalty.
        assert len(task["forbidden_markers"]) >= 1
        for marker in task["forbidden_markers"]:
            assert isinstance(marker, dict) and marker.get("patterns")
        # Every fixture is committed, referenced in the prompt, and reads as a
        # natural document (no answer-key vocabulary leaking into the clone).
        assert task["fixtures"]
        for rel in task["fixtures"]:
            fixture = REPO_ROOT / rel
            assert fixture.is_file(), f"missing fixture {rel}"
            assert rel in task["prompt"], f"{task['id']} prompt must name {rel}"
            text = fixture.read_text(encoding="utf-8").lower()
            for leak in ("defect", "decoy", "planted", "forbidden_markers"):
                assert leak not in text, f"{rel} leaks answer-key vocabulary: {leak}"


def test_tail_hypotheses_stay_in_existing_marketing_safe_test_scope():
    # The shared marketing-safe test must now also cover the tail cohort.
    source = (REPO_ROOT / "skills" / "mission" / "tests" / "test_benchmark_package.py").read_text(
        encoding="utf-8"
    )
    assert "tasks.tail.json" in source


def test_evaluate_quality_markers_penalizes_forbidden_matches():
    module = _load_official_goal_runner()
    task = {
        "quality_markers": [
            {"name": "M1", "patterns": ["alpha-token"]},
            {"name": "M2", "patterns": ["beta-token"]},
            {"name": "M3", "patterns": ["gamma-token"]},
            {"name": "M4", "patterns": ["delta-token"]},
        ],
        "forbidden_markers": [
            {"name": "F1", "patterns": ["decoy-claim-token"]},
        ],
    }
    text = "found alpha-token and beta-token and gamma-token, plus decoy-claim-token."
    result = module.evaluate_quality_markers(text, task)

    assert result["quality_markers_matched"] == ["M1", "M2", "M3"]
    assert result["quality_marker_recall"] == 0.75
    assert result["forbidden_markers_matched"] == ["F1"]
    # Net score: (3 matched - 1 false positive) / 4 total.
    assert result["quality_marker_score"] == 0.5


def test_evaluate_quality_markers_without_forbidden_is_backward_compatible():
    module = _load_official_goal_runner()
    task = {
        "quality_markers": [
            {"name": "M1", "patterns": ["alpha-token"]},
            {"name": "M2", "patterns": ["beta-token"]},
        ],
    }
    result = module.evaluate_quality_markers("alpha-token only", task)

    assert result["quality_marker_recall"] == 0.5
    assert result["quality_marker_score"] == 0.5
    assert result["forbidden_markers_matched"] == []


def test_evaluate_quality_markers_net_score_floors_at_zero():
    module = _load_official_goal_runner()
    task = {
        "quality_markers": [{"name": "M1", "patterns": ["alpha-token"]}],
        "forbidden_markers": [
            {"name": "F1", "patterns": ["decoy-one"]},
            {"name": "F2", "patterns": ["decoy-two"]},
        ],
    }
    result = module.evaluate_quality_markers("decoy-one decoy-two", task)

    assert result["quality_marker_recall"] == 0.0
    assert result["quality_marker_score"] == 0.0


def test_sanitize_worktree_removes_hidden_paths(tmp_path):
    module = _load_official_goal_runner()
    hidden = tmp_path / "benchmarks" / "mission-vs-goal" / "tasks.tail.json"
    hidden.parent.mkdir(parents=True)
    hidden.write_text("{}", encoding="utf-8")
    keep = tmp_path / "benchmarks" / "mission-vs-goal" / "fixtures.md"
    keep.write_text("fixture", encoding="utf-8")

    removed = module.sanitize_worktree(
        tmp_path,
        ["benchmarks/mission-vs-goal/tasks.tail.json", "does/not/exist.json"],
    )

    assert not hidden.exists()
    assert keep.exists()
    assert removed == ["benchmarks/mission-vs-goal/tasks.tail.json"]


def test_sanitize_worktree_rejects_paths_escaping_the_worktree(tmp_path):
    module = _load_official_goal_runner()
    with pytest.raises(ValueError):
        module.sanitize_worktree(tmp_path, ["../outside.txt"])
    with pytest.raises(ValueError):
        module.sanitize_worktree(tmp_path, ["/etc/hosts"])


def test_sanitize_worktree_unlinks_symlink_endpoint_not_its_target(tmp_path):
    module = _load_official_goal_runner()
    real = tmp_path / "real.json"
    real.write_text("{}", encoding="utf-8")
    link = tmp_path / "answer.json"
    link.symlink_to(real)

    removed = module.sanitize_worktree(tmp_path, ["answer.json"])

    assert removed == ["answer.json"]
    assert not link.exists()
    assert real.exists()


def test_build_prompt_injects_cohort_rules_for_both_arms():
    module = _load_official_goal_runner()
    task = {
        "id": "tail-example",
        "category": "analysis",
        "prompt": "Example prompt.",
        "validator": "Example validator.",
    }
    rule = "Do not read benchmark metadata."
    for arm in ("claude_code_goal_command", "mission"):
        prompt = module.build_prompt(task, arm, "out.md", extra_rules=[rule])
        assert rule in prompt
        assert prompt.index(rule) < prompt.index("Task id:")


def test_build_prompt_hides_answer_key_markers_when_markers_hidden():
    module = _load_official_goal_runner()
    task = {
        "id": "tail-example",
        "category": "analysis",
        "prompt": "Example prompt.",
        "validator": "Example validator.",
        "markers_hidden": True,
        "quality_markers": [{"name": "Secret Wrong Value 27000", "patterns": ["27000"]}],
    }
    for arm in ("claude_code_goal_command", "mission"):
        prompt = module.build_prompt(task, arm, "out.md")
        assert "Secret Wrong Value 27000" not in prompt
        assert "Quality scoring markers" not in prompt


def test_run_one_wires_sanitize_and_prompt_rules():
    source = (BENCHMARK_DIR / "run_claude_goal_vs_mission.py").read_text(encoding="utf-8")
    # Sanitization must happen inside run_one, after cloning, before the arm runs.
    run_one_body = source[source.index("def run_one("):]
    assert "sanitize_worktree(" in run_one_body
    assert run_one_body.index("prepare_clone(") < run_one_body.index("sanitize_worktree(")
    assert '"hidden_paths"' in source
    assert '"prompt_rules"' in source
    # Recorded per run so results stay auditable.
    assert '"quality_marker_recall"' in source
    assert '"forbidden_markers_matched"' in source


def test_result_schema_covers_tail_scoring_fields():
    schema = json.loads((BENCHMARK_DIR / "result.schema.json").read_text(encoding="utf-8"))
    assert schema["properties"]["quality_marker_recall"]["maximum"] == 1
    assert schema["properties"]["forbidden_markers_matched"]["type"] == "array"


def test_benchmark_readmes_document_tail_cohort():
    en = (BENCHMARK_DIR / "README.md").read_text(encoding="utf-8")
    ja = (BENCHMARK_DIR / "README.ja.md").read_text(encoding="utf-8")
    for text in (en, ja):
        assert "tasks.tail.json" in text
        assert "tail-first-failure" in text
        assert "hidden_paths" in text
        assert "forbidden_markers" in text
