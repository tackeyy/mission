"""Tests for benchmarks/mission-vs-goal/judge_quality_markers.py (Issue #561).

Covers:
- dry-run: prompts emitted, judge NOT called
- dummy judge injection: output record shape
- --model-id required (argparse errors without it)
- patterns never appear in the prompt
- raising judge → identified: null, processing continues
- sidecar does not mutate existing results
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest  # noqa: F401 (used for pytest.approx, pytest.raises, fixtures)

# ---------------------------------------------------------------------------
# Module loader (follows the pattern in test_benchmark_adherence_guard.py)
# ---------------------------------------------------------------------------

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"
_MOD_PATH = BENCH / "judge_quality_markers.py"


def _load_judge_module():
    spec = importlib.util.spec_from_file_location("judge_quality_markers", _MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_judge_module()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TASK: Dict[str, Any] = {
    "id": "test-task",
    "quality_markers": [
        {
            "name": "Drift: request timeout",
            # Patterns are distinct strings not appearing in the marker name
            "patterns": ["PATTERN_ALPHA_9182", "PATTERN_BETA_7364"],
        },
        {
            "name": "Drift: retry count in runbook",
            "patterns": ["PATTERN_GAMMA_5541", "PATTERN_DELTA_2278"],
        },
    ],
}

SAMPLE_ARTIFACT = (
    "The audit found a request timeout mismatch which contradicts "
    "the spec value. This is identified as a spec mismatch."
)


def _make_dummy_judge(responses: Optional[List[Dict[str, Any]]] = None):
    """Return a dummy judge that cycles through the given responses."""
    call_count = [0]
    _responses = responses or [{"identified": True, "reason": "found it"}]

    def _judge(prompt: str, model_id: str) -> Dict[str, Any]:
        idx = call_count[0] % len(_responses)
        call_count[0] += 1
        return _responses[idx]

    return _judge


def _make_raising_judge(exc: Exception):
    """Return a judge that always raises."""
    def _judge(prompt: str, model_id: str) -> Dict[str, Any]:
        raise exc
    return _judge


# ---------------------------------------------------------------------------
# 1. Patterns must NOT appear in the prompt
# ---------------------------------------------------------------------------

def test_patterns_not_in_prompt():
    """The marker's patterns list must not appear in the judge prompt.

    SAMPLE_TASK uses patterns that are distinct from marker names and artifact
    body, so any occurrence in the prompt is evidence of a leak.
    """
    for marker in SAMPLE_TASK["quality_markers"]:
        # Use a neutral artifact body that does not contain any of the patterns
        prompt = MOD.build_judge_prompt(marker["name"], "ARTIFACT_PLACEHOLDER")
        for pattern in marker["patterns"]:
            assert pattern not in prompt, (
                f"Pattern {pattern!r} leaked into judge prompt. "
                f"The patterns list must not be injected as search hints."
            )


def test_patterns_not_in_prompt_with_real_artifact():
    """Patterns must not appear even when the artifact body is included."""
    for marker in SAMPLE_TASK["quality_markers"]:
        prompt = MOD.build_judge_prompt(marker["name"], SAMPLE_ARTIFACT)
        for pattern in marker["patterns"]:
            # Our SAMPLE_ARTIFACT does not contain any PATTERN_* strings,
            # so their presence anywhere in the prompt is a leak.
            assert pattern not in prompt, (
                f"Pattern {pattern!r} leaked into judge prompt outside artifact body."
            )


# ---------------------------------------------------------------------------
# 2. Dummy judge injection: output record shape
# ---------------------------------------------------------------------------

def test_dummy_judge_record_shape(tmp_path: Path):
    """Dummy judge injection produces the documented output record shape."""
    tasks = {"test-task": SAMPLE_TASK}
    artifacts_dir = tmp_path / "artifacts"
    arm_dir = artifacts_dir / "test-run" / "test-task-mission"
    arm_dir.mkdir(parents=True)
    (arm_dir / "artifact.md").write_text(SAMPLE_ARTIFACT, encoding="utf-8")

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    out_path = tmp_path / "judge_results" / "test-run.jsonl"

    dummy = _make_dummy_judge([
        {"identified": True, "reason": "found it"},
        {"identified": False, "reason": "not found"},
    ])

    records = MOD.run(
        run_id="test-run",
        artifacts_dir=artifacts_dir,
        results_dir=results_dir,
        tasks=tasks,
        model_id="dummy-model",
        out_path=out_path,
        judge_fn=dummy,
        dry_run=False,
    )

    assert len(records) == 1
    rec = records[0]

    # Required fields
    assert rec["run_id"] == "test-run"
    assert rec["task_id"] == "test-task"
    assert rec["arm"] == "mission"
    assert rec["judge_model_id"] == "dummy-model"

    # judge_marker_results shape
    jmr = rec["judge_marker_results"]
    assert isinstance(jmr, list)
    assert len(jmr) == 2
    for item in jmr:
        assert "marker" in item
        assert "identified" in item
        assert "reason" in item

    # judge_marker_score: fraction identified (excluding nulls)
    valid = [r for r in jmr if r["identified"] is not None]
    expected_score = sum(1 for r in valid if r["identified"]) / len(valid)
    assert rec["judge_marker_score"] == pytest.approx(expected_score)

    # automated_marker_score: null because no results file
    assert rec["automated_marker_score"] is None

    # artifact body must NOT be in the record
    rec_str = json.dumps(rec)
    assert SAMPLE_ARTIFACT not in rec_str


# ---------------------------------------------------------------------------
# 3. --model-id is required (argparse errors without it)
# ---------------------------------------------------------------------------

def test_model_id_required():
    """argparse must error when --model-id is absent."""
    parser = MOD._build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--run-id", "some-run"])
    assert exc_info.value.code != 0


def test_model_id_provided():
    """No error when --model-id is provided."""
    parser = MOD._build_parser()
    args = parser.parse_args(["--run-id", "some-run", "--model-id", "my-model"])
    assert args.model_id == "my-model"


# ---------------------------------------------------------------------------
# 4. Dry-run: prompts emitted, judge NOT called
# ---------------------------------------------------------------------------

def test_dry_run_emits_prompts_without_calling_judge(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    """In dry-run mode, prompts are printed and the judge callable is never invoked."""
    tasks = {"test-task": SAMPLE_TASK}
    artifacts_dir = tmp_path / "artifacts"
    arm_dir = artifacts_dir / "test-run" / "test-task-mission"
    arm_dir.mkdir(parents=True)
    (arm_dir / "artifact.md").write_text(SAMPLE_ARTIFACT, encoding="utf-8")

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    out_path = tmp_path / "judge_results" / "test-run.jsonl"

    judge_called = []

    def _spy_judge(prompt: str, model_id: str) -> Dict[str, Any]:
        judge_called.append(prompt)
        return {"identified": True, "reason": "spy"}

    records = MOD.run(
        run_id="test-run",
        artifacts_dir=artifacts_dir,
        results_dir=results_dir,
        tasks=tasks,
        model_id="dummy-model",
        out_path=out_path,
        judge_fn=_spy_judge,
        dry_run=True,
    )

    # Judge must NOT have been called
    assert judge_called == [], "Judge was called during dry-run; it must not be."

    # Prompts must have been printed
    captured = capsys.readouterr()
    assert "[DRY-RUN]" in captured.out
    # Each marker name should appear in output
    for marker in SAMPLE_TASK["quality_markers"]:
        assert marker["name"] in captured.out

    # Output file must NOT have been written
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# 5. Raising judge → identified: null, processing continues
# ---------------------------------------------------------------------------

def test_raising_judge_yields_null_and_continues(tmp_path: Path):
    """A judge that raises records identified=null and the run completes."""
    markers = [
        {"name": "Marker A", "patterns": ["aaa"]},
        {"name": "Marker B", "patterns": ["bbb"]},
        {"name": "Marker C", "patterns": ["ccc"]},
    ]
    task = {"id": "test-task", "quality_markers": markers}
    tasks = {"test-task": task}

    artifacts_dir = tmp_path / "artifacts"
    arm_dir = artifacts_dir / "test-run" / "test-task-mission"
    arm_dir.mkdir(parents=True)
    (arm_dir / "artifact.md").write_text("Some artifact text.", encoding="utf-8")

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    out_path = tmp_path / "out.jsonl"

    raising_judge = _make_raising_judge(RuntimeError("API overloaded"))

    records = MOD.run(
        run_id="test-run",
        artifacts_dir=artifacts_dir,
        results_dir=results_dir,
        tasks=tasks,
        model_id="dummy-model",
        out_path=out_path,
        judge_fn=raising_judge,
        dry_run=False,
    )

    # Run must complete (not raise)
    assert len(records) == 1
    jmr = records[0]["judge_marker_results"]
    assert len(jmr) == 3

    # All markers must have identified=null
    for item in jmr:
        assert item["identified"] is None
        assert "judge-error" in item["reason"]

    # Score must be null (all null → no denominator)
    assert records[0]["judge_marker_score"] is None


# ---------------------------------------------------------------------------
# 6. Sidecar does not mutate existing results
# ---------------------------------------------------------------------------

def test_sidecar_does_not_mutate_results(tmp_path: Path):
    """Running the judge must not modify any file under results/."""
    tasks = {"test-task": SAMPLE_TASK}

    # Set up a fake results file
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    results_file = results_dir / "test-run.jsonl"
    original_content = json.dumps({
        "run_id": "test-run",
        "task_id": "test-task",
        "arm": "mission",
        "quality_marker_score": 1.0,
    }) + "\n"
    results_file.write_text(original_content, encoding="utf-8")
    original_mtime = results_file.stat().st_mtime

    artifacts_dir = tmp_path / "artifacts"
    arm_dir = artifacts_dir / "test-run" / "test-task-mission"
    arm_dir.mkdir(parents=True)
    (arm_dir / "artifact.md").write_text(SAMPLE_ARTIFACT, encoding="utf-8")

    out_path = tmp_path / "judge_results" / "test-run.jsonl"

    dummy = _make_dummy_judge([{"identified": True, "reason": "ok"}])

    MOD.run(
        run_id="test-run",
        artifacts_dir=artifacts_dir,
        results_dir=results_dir,
        tasks=tasks,
        model_id="dummy-model",
        out_path=out_path,
        judge_fn=dummy,
        dry_run=False,
    )

    # Results file must be unchanged
    assert results_file.read_text(encoding="utf-8") == original_content
    assert results_file.stat().st_mtime == original_mtime


# ---------------------------------------------------------------------------
# 7. automated_marker_score is carried over from results
# ---------------------------------------------------------------------------

def test_automated_score_carried_over(tmp_path: Path):
    """automated_marker_score is read from existing results and carried over."""
    tasks = {"test-task": SAMPLE_TASK}

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    results_record = {
        "run_id": "test-run",
        "task_id": "test-task",
        "arm": "mission",
        "quality_marker_score": 0.857,
    }
    (results_dir / "test-run.jsonl").write_text(
        json.dumps(results_record) + "\n", encoding="utf-8"
    )

    artifacts_dir = tmp_path / "artifacts"
    arm_dir = artifacts_dir / "test-run" / "test-task-mission"
    arm_dir.mkdir(parents=True)
    (arm_dir / "artifact.md").write_text(SAMPLE_ARTIFACT, encoding="utf-8")

    out_path = tmp_path / "out.jsonl"
    dummy = _make_dummy_judge([{"identified": True, "reason": "ok"}])

    records = MOD.run(
        run_id="test-run",
        artifacts_dir=artifacts_dir,
        results_dir=results_dir,
        tasks=tasks,
        model_id="dummy-model",
        out_path=out_path,
        judge_fn=dummy,
        dry_run=False,
    )

    assert len(records) == 1
    assert records[0]["automated_marker_score"] == pytest.approx(0.857)


# ---------------------------------------------------------------------------
# 8. compute_marker_score denominator policy
# ---------------------------------------------------------------------------

def test_score_excludes_null_from_denominator():
    """identified=null entries excluded from denominator."""
    marker_results = [
        {"marker": "A", "identified": True, "reason": "ok"},
        {"marker": "B", "identified": None, "reason": "error"},
        {"marker": "C", "identified": False, "reason": "nope"},
    ]
    # valid = [A, C]; identified = [A]; score = 1/2
    score = MOD.compute_marker_score(marker_results)
    assert score == pytest.approx(0.5)


def test_score_all_null_returns_none():
    """All null → score is None (avoid 0/0)."""
    marker_results = [
        {"marker": "A", "identified": None, "reason": "error"},
        {"marker": "B", "identified": None, "reason": "error"},
    ]
    assert MOD.compute_marker_score(marker_results) is None


def test_score_all_identified():
    """All identified=True → score 1.0."""
    marker_results = [
        {"marker": "A", "identified": True, "reason": "ok"},
        {"marker": "B", "identified": True, "reason": "ok"},
    ]
    assert MOD.compute_marker_score(marker_results) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 9. Output does not contain artifact body
# ---------------------------------------------------------------------------

def test_output_does_not_contain_artifact_body(tmp_path: Path):
    """The output JSONL record must not contain the artifact body text."""
    tasks = {"test-task": SAMPLE_TASK}

    artifacts_dir = tmp_path / "artifacts"
    arm_dir = artifacts_dir / "test-run" / "test-task-mission"
    arm_dir.mkdir(parents=True)
    artifact_body = "UNIQUE_ARTIFACT_BODY_TEXT_FOR_DETECTION_1234567890"
    (arm_dir / "artifact.md").write_text(artifact_body, encoding="utf-8")

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    out_path = tmp_path / "out.jsonl"

    dummy = _make_dummy_judge([{"identified": True, "reason": "ok"}])

    records = MOD.run(
        run_id="test-run",
        artifacts_dir=artifacts_dir,
        results_dir=results_dir,
        tasks=tasks,
        model_id="dummy-model",
        out_path=out_path,
        judge_fn=dummy,
        dry_run=False,
    )

    assert len(records) == 1
    rec_str = json.dumps(records[0])
    assert artifact_body not in rec_str
