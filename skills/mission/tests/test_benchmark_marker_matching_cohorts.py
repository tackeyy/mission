"""Tests for quality marker matching — discriminating, openworld, portfolio cohorts.

TDD: RED step written first (before regex rewrite of cohort JSON files).
Tests verify:
1. Fixture verbatim copy does NOT earn score >= 0.5 (cohorts: discriminating, openworld, portfolio)
2. Known-good past artifacts still score >= 0.6 across ALL available run directories and both
   arms (including repeat variants), for discriminating, openworld, and portfolio cohorts.
   Asserts minimum cell count so the test fails if artifact directories are unexpectedly missing.
3. No pattern in cohort task files is <= 12 characters
4. All cohort task patterns use match_type: "regex"
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
ARTIFACTS_DIR = BENCH_DIR / "artifacts"
RUNNER_PATH = BENCH_DIR / "run_claude_goal_vs_mission.py"

TASKS_DISCRIMINATING = BENCH_DIR / "tasks.discriminating.json"
TASKS_OPENWORLD = BENCH_DIR / "tasks.openworld.json"
TASKS_PORTFOLIO = BENCH_DIR / "tasks.portfolio.json"

ARTIFACTS_DISCRIMINATING = BENCH_DIR / "artifacts" / "2026-07-23-discriminating-v2"
ARTIFACTS_OPENWORLD = BENCH_DIR / "artifacts" / "2026-07-22-claude-goal-vs-mission-openworld-v1"

sys.path.insert(0, str(BENCH_DIR))


def _load_runner():
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_cohort_tasks():
    """Return list of (task_dict, artifacts_base_or_None) for all cohort tasks with fixtures."""
    results = []
    for path, artifacts_base in [
        (TASKS_DISCRIMINATING, ARTIFACTS_DISCRIMINATING),
        (TASKS_OPENWORLD, ARTIFACTS_OPENWORLD),
        (TASKS_PORTFOLIO, None),
    ]:
        data = json.loads(path.read_text(encoding="utf-8"))
        for task in data["tasks"]:
            if task.get("fixtures"):
                results.append((task, artifacts_base))
    return results


def _load_all_cohort_tasks_by_id() -> dict[str, dict]:
    """Return {task_id: task_dict} for all cohort tasks across all three cohort files."""
    tasks: dict[str, dict] = {}
    for path in [TASKS_DISCRIMINATING, TASKS_OPENWORLD, TASKS_PORTFOLIO]:
        data = json.loads(path.read_text(encoding="utf-8"))
        for task in data["tasks"]:
            tasks[task["id"]] = task
    return tasks


def _discover_artifact_cells(
    task_ids: set[str],
) -> list[tuple[str, str, str, Path]]:
    """Scan artifacts directory and return (run, task_id, arm, artifact_path) for every
    artifact.md whose parent directory name matches <task_id>-<arm>."""
    cells = []
    if not ARTIFACTS_DIR.is_dir():
        return cells
    for run_dir in sorted(ARTIFACTS_DIR.iterdir()):
        if not run_dir.is_dir():
            continue
        for subdir in sorted(run_dir.iterdir()):
            if not subdir.is_dir():
                continue
            artifact_path = subdir / "artifact.md"
            if not artifact_path.exists():
                continue
            name = subdir.name
            for tid in task_ids:
                if name.startswith(tid + "-"):
                    arm = name[len(tid) + 1 :]
                    cells.append((run_dir.name, tid, arm, artifact_path))
                    break
    return cells


def _concatenate_fixtures(task: dict) -> str:
    parts = []
    for rel_path in task.get("fixtures", []):
        p = REPO_ROOT / rel_path
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _all_marker_patterns(task: dict) -> list[tuple[str, str, str]]:
    """Return (task_id, marker_name, pattern) for every pattern in quality_markers."""
    rows = []
    task_id = task["id"]
    for m in task.get("quality_markers", []):
        if isinstance(m, str):
            rows.append((task_id, m, m))
        else:
            for p in m.get("patterns", []):
                rows.append((task_id, m.get("name", ""), p))
    return rows


# ---------------------------------------------------------------------------
# Test 1: fixture verbatim copy does NOT earn score >= 0.5
# ---------------------------------------------------------------------------


class TestCohortFixtureVerbatimDoesNotEarnFullScore:
    """Fixture verbatim text must not score >= 0.5 on task's own quality markers.

    RED before cohort JSON rewrite: old substr patterns are too loose and
    many tasks will score 1.0 on fixture verbatim.
    GREEN after rewrite: all tasks score < 0.5.
    """

    def test_fixture_verbatim_copy_does_not_earn_half_score(self):
        runner = _load_runner()
        cohort_tasks = _load_cohort_tasks()
        assert cohort_tasks, "expected at least one cohort task with fixtures"

        failures = []
        for task, _ in cohort_tasks:
            fixture_text = _concatenate_fixtures(task)
            if not fixture_text.strip():
                continue
            result = runner.evaluate_quality_markers(fixture_text, task)
            score = result["quality_marker_score"]
            if score is None:
                continue
            if score >= 0.5:
                failures.append(
                    f"{task['id']}: fixture_score={score:.3f} "
                    f"(matched={result['quality_markers_matched']})"
                )

        assert not failures, (
            "Fixture verbatim scored >= 0.5 for:\n" + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 2: known-good artifacts still score >= 0.6
# ---------------------------------------------------------------------------


class TestCohortKnownGoodArtifactsStillScoreAboveFloor:
    """All cohort artifacts across every available run must score >= 0.6.

    Covers discriminating, openworld, and portfolio across ALL available run
    directories and both arms including repeat variants (-rep1, -rep2, -rep3).
    Asserts minimum cell count so the test fails if run directories are missing.
    """

    def test_known_good_artifacts_score_above_floor(self):
        runner = _load_runner()
        all_tasks = _load_all_cohort_tasks_by_id()
        task_ids = set(all_tasks.keys())
        cells = _discover_artifact_cells(task_ids)

        failures: list[str] = []
        checked = 0
        for run, tid, arm, artifact_path in cells:
            task = all_tasks[tid]
            text = artifact_path.read_text(encoding="utf-8")
            result = runner.evaluate_quality_markers(runner.strip_form(text), task)
            score = result["quality_marker_score"]
            if score is None:
                continue
            checked += 1
            if score < 0.6:
                failures.append(
                    f"{run}/{tid}-{arm}: score={score:.3f} "
                    f"(missing={result['quality_markers_missing'][:2]})"
                )

        # Minimum cell count:
        #   discriminating v2 (5 tasks × 2 arms) = 10
        #   openworld v1 (3 tasks × 2 arms) = 6
        #   portfolio (multiple runs × tasks × arms) = 100+
        # Total across all available runs must be at least 100.
        assert checked >= 100, (
            f"Expected at least 100 artifact cells across all cohort runs, got {checked}. "
            "Did a run directory go missing?"
        )
        assert not failures, (
            "Known-good artifact scored < 0.6 for:\n" + "\n".join(failures)
        )


# ---------------------------------------------------------------------------
# Test 3: no pattern is <= 12 characters
# ---------------------------------------------------------------------------


class TestCohortNoShortPatterns:
    """Every pattern in cohort task files must be longer than 12 characters.

    Patterns <= 12 chars are too likely to match fixture verbatim.
    """

    def test_no_pattern_is_12_chars_or_fewer(self):
        short = []
        for path in [TASKS_DISCRIMINATING, TASKS_OPENWORLD, TASKS_PORTFOLIO]:
            data = json.loads(path.read_text(encoding="utf-8"))
            for task in data["tasks"]:
                for task_id, name, pattern in _all_marker_patterns(task):
                    if len(pattern) <= 12:
                        short.append(f"{task_id} / {name!r}: {pattern!r} ({len(pattern)} chars)")

        assert not short, "Patterns <= 12 chars found:\n" + "\n".join(short)


# ---------------------------------------------------------------------------
# Test 4: all cohort task markers use match_type: "regex"
# ---------------------------------------------------------------------------


class TestCohortMarkersUseRegex:
    """All quality_markers entries in cohort task files must use match_type: "regex".

    Tasks with fixture files require contrast patterns; substr matching is insufficient.
    """

    def test_all_fixture_task_markers_use_regex(self):
        non_regex = []
        for path in [TASKS_DISCRIMINATING, TASKS_OPENWORLD, TASKS_PORTFOLIO]:
            data = json.loads(path.read_text(encoding="utf-8"))
            for task in data["tasks"]:
                if not task.get("fixtures"):
                    continue
                for m in task.get("quality_markers", []):
                    if isinstance(m, str):
                        non_regex.append(f"{task['id']}: plain string marker {m!r}")
                    elif m.get("match_type") != "regex":
                        non_regex.append(
                            f"{task['id']} / {m.get('name', '?')!r}: "
                            f"match_type={m.get('match_type')!r}"
                        )

        assert not non_regex, (
            "Markers without match_type='regex' found in fixture tasks:\n"
            + "\n".join(non_regex)
        )
