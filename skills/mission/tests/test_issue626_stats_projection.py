"""Issue #626 PR2: stats reduction lives in a pure projection module."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


MISSION_STATE_PATH = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
PROJECTION_PATH = (
    Path(__file__).resolve().parents[1]
    / "lib"
    / "mission_projection"
    / "stats.py"
)
LEGACY_HASH_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "issue626-stats-legacy-output-sha256.json"
)
OBSERVATION_NOW = datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)


def _load_mission_state():
    spec = importlib.util.spec_from_file_location(
        "issue626_stats_mission_state", MISSION_STATE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state(case: str, **overrides: object) -> dict:
    document = {
        "mission": "stats projection parity",
        "mission_id": f"mission-{case}",
        "session_id": f"session-{case}",
        "project_root": f"/project/{case}",
        "complexity": "Standard",
        "agent": "neutral-cli",
        "session_role": "implementer",
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "iteration": 1,
        "phase": "executing",
        "score_history": [],
        "started_at": "2026-08-23T00:00:00+00:00",
        "updated_at": "2026-08-23T00:30:00+00:00",
    }
    document.update(overrides)
    return document


def _stats_cases() -> dict[str, tuple[list[dict], int, int]]:
    pass_state = _state(
        "pass", passes=True, loop_active=False, phase="done",
        score_history=[{"iteration": 1, "composite": 4.2, "min_item": 4.0}],
    )
    halt_state = _state(
        "halt", loop_active=False, phase="halted", halt_reason="blocked",
        halt_category="blocked-external",
    )
    incomplete_state = _state("incomplete", updated_at="2026-08-23T23:59:00+00:00")
    abandoned_state = _state("abandoned", loop_active=False, phase="executing")
    cases = {
        "empty": ([], 0, 0),
        "single-pass": ([pass_state], 0, 0),
        "single-halt": ([halt_state], 0, 0),
        "single-incomplete": ([incomplete_state], 0, 0),
        "single-abandoned": ([abandoned_state], 0, 0),
        "mixed-terminal-health": (
            [pass_state, halt_state, incomplete_state, abandoned_state], 0, 0
        ),
        "duplicate-observation": ([pass_state], 3, 0),
        "read-error-observation": ([pass_state], 0, 2),
        "forced-pass": ([dict(pass_state, passes_forced=True, force_reason="manual")], 0, 0),
        "legacy-force-pass": ([dict(pass_state, force_reason="legacy")], 0, 0),
        "ungated-pass": ([dict(pass_state, score_history=[])], 0, 0),
        "final-score-before-note": ([dict(
            pass_state,
            score_history=[
                {"composite": 4.4},
                {"note": "progress only"},
            ],
        )], 0, 0),
        "invalid-score-bool": ([dict(pass_state, score_history=[{"composite": True}])], 0, 0),
        "invalid-score-string": ([dict(pass_state, score_history=[{"composite": "4.2"}])], 0, 0),
        "iteration-four-plus": ([dict(pass_state, iteration=7)], 0, 0),
        "iteration-zero": ([dict(pass_state, iteration=0)], 0, 0),
        "review-tier-mix": ([
            dict(pass_state, review_tier="light"),
            dict(halt_state, review_tier="full"),
            dict(incomplete_state),
        ], 0, 0),
        "cli-version-mix": ([
            dict(pass_state, cli_version="1.2.3"),
            dict(halt_state, cli_version="1.2.3"),
            dict(incomplete_state),
        ], 0, 0),
        "parallel-review-values": ([
            dict(pass_state, last_parallel_execution=True),
            dict(halt_state, last_parallel_execution=False),
            dict(incomplete_state, last_parallel_execution="unknown"),
        ], 0, 0),
        "halt-category-missing": ([dict(halt_state, halt_category="")], 0, 0),
        "halt-category-custom": ([dict(halt_state, halt_category="custom-category")], 0, 0),
        "phase-durations": ([dict(
            pass_state,
            phase_durations_sec={"planning": 12.5, "reviewing": 7},
        )], 0, 0),
        "invalid-phase-durations": ([dict(
            pass_state,
            phase_durations_sec={"planning": -1, "reviewing": "invalid", "scoring": True},
        )], 0, 0),
        "artifact-lint-clean": ([dict(pass_state, artifact_lint=[])], 0, 0),
        "artifact-lint-findings": ([dict(
            pass_state,
            artifact_lint=[
                {"kind": "empty-section"},
                {"kind": "stub-forward-reference"},
                {"kind": "unknown"},
            ],
        )], 0, 0),
        "reviewer-output-valid": ([dict(
            pass_state,
            reviewer_output_records=[
                {"prose_bytes": 100, "prose_ratio": 0.25},
                {"prose_bytes": 25_000, "prose_ratio": 0.9},
            ],
        )], 0, 0),
        "reviewer-output-invalid": ([dict(
            pass_state,
            reviewer_output_records=[
                {"prose_bytes": True, "prose_ratio": 0.25},
                {"prose_bytes": -1, "prose_ratio": 2.0},
                "invalid",
            ],
        )], 0, 0),
        "command-outcomes-valid": ([dict(
            pass_state,
            command_outcomes=[{"kind": "completed", "command": "plan"}],
        )], 0, 0),
        "command-outcomes-invalid": ([dict(
            pass_state,
            command_outcomes=["invalid"],
        )], 0, 0),
        "bounded-context-missing-manifest": ([dict(
            pass_state,
            iteration=2,
            critic_has_new_scope=False,
            context_manifests={
                "2": {
                    "path": "/does/not/exist",
                    "digest": "sha256:" + "0" * 64,
                    "generated_at": "2026-08-23T00:00:00+00:00",
                }
            },
        )], 0, 0),
        "activity-timing": ([dict(
            pass_state,
            activity_segments=[{
                "kind": "active",
                "phase": "executing",
                "started_at": "2026-08-23T00:00:00+00:00",
                "ended_at": "2026-08-23T00:01:00+00:00",
            }],
        )], 0, 0),
        "reviewer-role": ([dict(pass_state, session_role="reviewer")], 0, 0),
        "critic-role": ([dict(pass_state, session_role="critic")], 0, 0),
        "unknown-project-agent-complexity": ([dict(
            abandoned_state,
            project_root="",
            agent="",
            complexity="",
        )], 0, 0),
        "legacy-minimal": ([{
            "mission": "legacy",
            "mission_id": "legacy-mission",
            "session_id": "legacy-session",
            "passes": False,
            "loop_active": False,
            "halt_reason": "",
        }], 0, 0),
        "multiple-projects": ([
            dict(pass_state, project_root="/project/alpha"),
            dict(halt_state, project_root="/project/beta"),
        ], 0, 0),
    }
    assert len(cases) == 36
    return cases


def _output_digest(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _projection_input(legacy, states, duplicate_count, read_error_count):
    from mission_projection.stats import StatsProjectionInput

    snapshots = [legacy._authoritative_snapshot_for_state(state) for state in states]
    pass_rate_summary = legacy.summarize_authoritative_pass_rate_population(
        snapshots,
        now=OBSERVATION_NOW,
        stale_after_sec=3600,
    )
    bounded_context_observations = []
    for state in states:
        iteration = state.get("iteration", 1)
        expected_bounded = (
            isinstance(iteration, int)
            and legacy._expected_context_mode(state, iteration) == "bounded"
        )
        bounded_context_observations.append(
            (expected_bounded, legacy._context_manifest_generated(state, iteration))
        )
    return StatsProjectionInput(
        states=states,
        snapshots=snapshots,
        pass_rate_summary=pass_rate_summary,
        duplicate_state_group_count=duplicate_count,
        state_read_error_count=read_error_count,
        bounded_context_observations=bounded_context_observations,
        score_provenance_counts=legacy._score_provenance_counts(states),
        command_outcome_counts=legacy._command_outcome_counts(states),
        duration_observations=[
            duration
            for duration in (legacy._duration_sec(state) for state in states)
            if duration is not None and duration >= 0
        ],
        artifact_coverage=legacy.summarize_artifact_coverage(
            states,
            terminal_outcomes=[
                snapshot.artifact_terminal_outcome for snapshot in snapshots
            ],
        ),
        activity_timing=legacy.summarize_activity_states(
            states,
            phases=[snapshot.phase for snapshot in snapshots],
            session_roles=[snapshot.session_role for snapshot in snapshots],
        ),
        planning_provider_kpis=legacy.reduce_planning_provider_kpis(
            states, population_kind="observed"
        ),
        failure_ledger_counts=legacy.failure_ledger_counts(states),
        iteration_recovery=legacy.reduce_iteration_recovery(states),
    )


def test_projection_matches_36_fixed_legacy_outputs_exactly(monkeypatch):
    legacy = _load_mission_state()
    monkeypatch.setenv("MISSION_STALE_ACTIVE_SECONDS", "3600")
    from mission_projection.stats import project_stats

    expected_hashes = json.loads(LEGACY_HASH_PATH.read_text(encoding="utf-8"))
    cases = _stats_cases()
    assert expected_hashes.keys() == cases.keys()

    for name, (states, duplicate_count, read_error_count) in cases.items():
        expected_hash = expected_hashes[name]
        facade_output = legacy._aggregate(
            states,
            duplicate_count,
            observation_now=OBSERVATION_NOW,
            state_read_error_count=read_error_count,
        )
        projection_output = project_stats(
            _projection_input(
                legacy, states, duplicate_count, read_error_count
            )
        )

        assert facade_output == projection_output, name
        assert _output_digest(facade_output) == expected_hash, name


def test_projection_module_has_no_io_or_outer_layer_imports():
    tree = ast.parse(PROJECTION_PATH.read_text(encoding="utf-8"))
    imported_modules = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(
        module == forbidden or module.startswith(forbidden + ".")
        for module in imported_modules
        for forbidden in (
            "os",
            "pathlib",
            "subprocess",
            "datetime",
            "time",
            "sys",
            "socket",
            "http",
            "urllib",
            "mission_adapter",
            "mission_persistence",
        )
    )


def test_projection_does_not_open_paths_after_observation_collection(monkeypatch):
    legacy = _load_mission_state()
    state = _stats_cases()["bounded-context-missing-manifest"][0][0]
    request = _projection_input(legacy, [state], 0, 0)
    from mission_projection.stats import project_stats

    def fail_io(*_args, **_kwargs):
        raise AssertionError("projection attempted filesystem I/O")

    monkeypatch.setattr(Path, "read_bytes", fail_io)

    output = project_stats(request)

    assert output["bounded_context_counts"] == {
        "expected_bounded": 1,
        "manifest_generated": 0,
        "fallback_full": 1,
    }
