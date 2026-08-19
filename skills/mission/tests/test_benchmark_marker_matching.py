"""Tests for quality marker matching in benchmarks/mission-vs-goal.

TDD: RED step written first (before regex implementation).
Tests verify:
1. Fixture verbatim copy does NOT earn full marker score (< 0.5)
2. Known-good past artifacts still score above floor (>= 0.6)
3. Backward-compat: marker without match_type behaves as substring
4. Invalid regex pattern raises on compile
5. Invalid match_type value raises ValueError
6. No pattern in tasks.tail.json is <= 12 characters
7. run_paired_pilot.py guard: marker with patterns but no text raises ValueError
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
RUNNER_PATH = BENCH_DIR / "run_claude_goal_vs_mission.py"
PAIRED_PATH = BENCH_DIR / "run_paired_pilot.py"
TASKS_TAIL_PATH = BENCH_DIR / "tasks.tail.json"
FIXTURES_BASE = BENCH_DIR / "fixtures" / "tail"
ARTIFACTS_BASE = BENCH_DIR / "artifacts" / "2026-08-19-tail-v280-r2"

sys.path.insert(0, str(BENCH_DIR))


def _load_runner():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_paired():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_paired_pilot", PAIRED_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_tasks():
    return json.loads(TASKS_TAIL_PATH.read_text(encoding="utf-8"))


def _fixture_dir_for_task(task: dict) -> Path:
    """Return fixture directory for a tail task."""
    if "fixtures" in task:
        # Use first fixture path to derive directory
        first = REPO_ROOT / task["fixtures"][0]
        return first.parent
    task_id = task["id"]
    suffix = task_id.removeprefix("tail-")
    return FIXTURES_BASE / suffix


def _concatenate_fixtures(task: dict) -> str:
    """Concatenate all fixture files for a task."""
    if "fixtures" in task:
        paths = [REPO_ROOT / p for p in task["fixtures"]]
    else:
        fixture_dir = _fixture_dir_for_task(task)
        paths = sorted(fixture_dir.iterdir())
    parts = []
    for p in paths:
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Test 1: fixture verbatim copy does not earn full marker score
# ---------------------------------------------------------------------------

class TestFixtureVerbatimDoesNotEarnFullScore:
    """RED: before regex patterns are in place, 4/5 tasks score 1.0 on fixture verbatim."""

    def test_fixture_verbatim_copy_does_not_earn_full_marker_score(self):
        runner = _load_runner()
        tasks = _load_tasks()["tasks"]
        failures = []
        for task in tasks:
            fixture_text = _concatenate_fixtures(task)
            result = runner.evaluate_quality_markers(fixture_text, task)
            score = result["quality_marker_score"]
            if score is None:
                continue
            if score >= 0.5:
                failures.append(
                    f"{task['id']}: score={score} (matched={result['quality_markers_matched']})"
                )
        assert not failures, (
            "Fixture verbatim copy scored >= 0.5 for:\n"
            + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 2: known-good artifacts still score above floor
# ---------------------------------------------------------------------------

class TestKnownGoodArtifactsStillScoreAboveFloor:
    def test_known_good_artifacts_still_score_above_floor(self):
        runner = _load_runner()
        tasks = _load_tasks()["tasks"]
        failures = []
        for task in tasks:
            artifact_path = ARTIFACTS_BASE / f"{task['id']}-mission" / "artifact.md"
            if not artifact_path.exists():
                continue
            text = artifact_path.read_text(encoding="utf-8")
            result = runner.evaluate_quality_markers(text, task)
            score = result["quality_marker_score"]
            if score is None:
                continue
            if score < 0.6:
                failures.append(
                    f"{task['id']}: score={score} "
                    f"(matched={result['quality_markers_matched']}, "
                    f"missing={result['quality_markers_missing']})"
                )
        assert not failures, (
            "Known-good artifact scored < 0.6 for:\n"
            + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 3: backward-compat — marker without match_type behaves as substring
# ---------------------------------------------------------------------------

class TestBackwardCompatSubstr:
    def test_marker_without_match_type_uses_substr(self):
        runner = _load_runner()
        marker_dict = {"name": "test", "patterns": ["hello world"]}
        patterns = runner.quality_marker_patterns(marker_dict)
        # Should return list of (pattern, match_type) tuples
        assert isinstance(patterns, list)
        assert len(patterns) == 1
        pat, mt = patterns[0]
        assert pat == "hello world"
        assert mt == "substr"

    def test_plain_string_marker_uses_substr(self):
        runner = _load_runner()
        patterns = runner.quality_marker_patterns("hello world")
        assert len(patterns) == 1
        pat, mt = patterns[0]
        assert pat == "hello world"
        assert mt == "substr"

    def test_substr_marker_matches_correctly(self):
        runner = _load_runner()
        task = {
            "quality_markers": [
                {"name": "m1", "patterns": ["found it"]},
            ]
        }
        result = runner.evaluate_quality_markers("The text has found it here.", task)
        assert result["quality_marker_score"] == 1.0
        assert "m1" in result["quality_markers_matched"]

    def test_substr_marker_misses_correctly(self):
        runner = _load_runner()
        task = {
            "quality_markers": [
                {"name": "m1", "patterns": ["not present"]},
            ]
        }
        result = runner.evaluate_quality_markers("The text has something else.", task)
        assert result["quality_marker_score"] == 0.0
        assert "m1" in result["quality_markers_missing"]


# ---------------------------------------------------------------------------
# Test 4: invalid regex raises on compile
# ---------------------------------------------------------------------------

class TestInvalidRegexRaises:
    def test_invalid_regex_raises(self):
        runner = _load_runner()
        marker = {"name": "bad", "patterns": ["(unclosed"], "match_type": "regex"}
        with pytest.raises(re.error):
            runner.quality_marker_patterns(marker)


# ---------------------------------------------------------------------------
# Test 5: invalid match_type raises ValueError
# ---------------------------------------------------------------------------

class TestInvalidMatchTypeRaises:
    def test_invalid_match_type_raises(self):
        runner = _load_runner()
        marker = {"name": "bad", "patterns": ["foo"], "match_type": "fuzzy"}
        with pytest.raises(ValueError, match="match_type"):
            runner.quality_marker_patterns(marker)


# ---------------------------------------------------------------------------
# Test 6: no pattern <= 12 chars in tasks.tail.json
# ---------------------------------------------------------------------------

class TestNoShortPatterns:
    def test_no_pattern_12_chars_or_fewer_in_tasks_tail(self):
        tasks = _load_tasks()["tasks"]
        short_patterns = []
        for task in tasks:
            for marker in task.get("quality_markers", []) + task.get("forbidden_markers", []):
                if isinstance(marker, dict):
                    for pat in marker.get("patterns", []):
                        if len(str(pat)) <= 12:
                            short_patterns.append(
                                f"{task['id']} / {marker.get('name','?')}: {pat!r}"
                            )
                else:
                    if len(str(marker)) <= 12:
                        short_patterns.append(f"{task['id']}: {marker!r}")
        assert not short_patterns, (
            "Patterns <= 12 chars found in tasks.tail.json:\n"
            + "\n".join(short_patterns)
        )


# ---------------------------------------------------------------------------
# Test 7: run_paired_pilot.py guard
# ---------------------------------------------------------------------------

class TestPairedPilotGuard:
    def test_patterns_without_text_raises(self):
        paired = _load_paired()
        task = {
            "quality_markers": [
                {"name": "marker_with_patterns", "patterns": ["some pattern"]},
            ]
        }
        with pytest.raises(ValueError, match="text"):
            paired.evaluate_quality_markers("some text", task)

    def test_string_marker_in_paired_still_works(self):
        """String markers (without patterns key) should not trigger the guard."""
        paired = _load_paired()
        task = {
            "quality_markers": [
                "hello world",
            ]
        }
        # Should not raise — string markers use the text= logic
        result = paired.evaluate_quality_markers("text with hello world in it", task)
        assert result is not None
