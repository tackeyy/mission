"""Issue #644 PR1: golden projection comparison and projector defects."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import canonical_json_bytes


_PATH_NAMES = (
    "_path_mark_pass",
    "_path_advance",
    "_path_mark_halt",
    "_path_reactivate",
    "_path_resume_stale",
    "_path_set_fields",
    "_path_permission_preflight",
    "_path_supersede",
)

_A_PAYLOAD = "A-payload"
_A_INPUT = "A-input"
_B_PROJECTOR = "B-projector"

# This is the complete pre-fix inventory measured at the PR1 base.  Keeping the
# classification beside the executable comparison makes an accidentally
# broadened allowance visible instead of silently blessing a new difference.
_DOCUMENTED_PRE_FIX_DIFFERENCES = {
    "_path_mark_pass": {
        "$.activity_current": _A_PAYLOAD,
        "$.activity_rollup.activity_duration_totals_sec.active": _A_PAYLOAD,
        "$.activity_rollup.closed_segment_count": _A_PAYLOAD,
        "$.activity_rollup.observed_total_sec": _A_PAYLOAD,
        "$.activity_rollup.phase_activity_duration_totals_sec.scoring": _A_PAYLOAD,
        "$.activity_segments[1]": _A_PAYLOAD,
        "$.lease_history": _B_PROJECTOR,
        "$.passes_forced": _A_PAYLOAD,
        "$.phase_durations_sec.scoring": _A_PAYLOAD,
        "$.phase_started_at": _A_PAYLOAD,
        "$.terminal_outcome": _B_PROJECTOR,
        "$.updated_at": _A_PAYLOAD,
    },
    "_path_advance": {
        "$.activity_current.iteration": _A_PAYLOAD,
        "$.activity_current.origin": _A_PAYLOAD,
        "$.activity_current.phase": _A_PAYLOAD,
        "$.activity_current.reason": _A_PAYLOAD,
        "$.activity_current.started_at": _A_PAYLOAD,
        "$.activity_rollup.activity_duration_totals_sec.active": _A_PAYLOAD,
        "$.activity_rollup.closed_segment_count": _A_PAYLOAD,
        "$.activity_rollup.observed_total_sec": _A_PAYLOAD,
        "$.activity_rollup.phase_activity_duration_totals_sec.scoring": _A_PAYLOAD,
        "$.activity_segments[1]": _A_PAYLOAD,
        "$.lease_history": _B_PROJECTOR,
        "$.phase_durations_sec.executing": _A_PAYLOAD,
        "$.phase_started_at": _A_PAYLOAD,
        "$.updated_at": _A_PAYLOAD,
    },
    "_path_mark_halt": {
        "$.halt_category": _B_PROJECTOR,
        "$.phase_started_at": _A_PAYLOAD,
        "$.terminal_outcome": _B_PROJECTOR,
        "$.updated_at": _A_PAYLOAD,
    },
    "_path_reactivate": {
        "$.activity_current": _A_PAYLOAD,
        "$.activity_rollup": _A_PAYLOAD,
        "$.activity_segments": _A_PAYLOAD,
        "$.phase_started_at": _A_PAYLOAD,
        "$.reactivation_history": _A_PAYLOAD,
        "$.updated_at": _A_PAYLOAD,
    },
    "_path_resume_stale": {
        "$.activity_current": _A_PAYLOAD,
        "$.activity_rollup": _A_PAYLOAD,
        "$.activity_segments": _A_PAYLOAD,
        "$.phase_started_at": _A_PAYLOAD,
        "$.pid": _A_PAYLOAD,
        "$.resume_target_phase": _A_PAYLOAD,
        "$.updated_at": _A_PAYLOAD,
    },
    "_path_set_fields": {
        "$.updated_at": _A_PAYLOAD,
    },
    "_path_permission_preflight": {
        "$.halt_category": _B_PROJECTOR,
        "$.terminal_outcome": _B_PROJECTOR,
        "$.updated_at": _A_PAYLOAD,
    },
    "_path_supersede": {
        "$.activity_anomaly_counts": _A_PAYLOAD,
        "$.activity_current": _A_PAYLOAD,
        "$.halt_category": _B_PROJECTOR,
        "$.iteration": _A_INPUT,
        "$.mission": _A_INPUT,
        "$.phase_durations_sec": _A_PAYLOAD,
        "$.phase_started_at": _A_PAYLOAD,
        "$.resume_target_phase": _A_PAYLOAD,
        "$.review_generation": _A_INPUT,
        "$.review_group_id": _A_INPUT,
        "$.terminal_outcome": _B_PROJECTOR,
        "$.updated_at": _A_INPUT,
    },
}

_GOLDEN_DOCUMENTS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "issue632_main_saved_documents.json").read_text(
        encoding="utf-8"
    )
)
_CORPUS_BACKED_PATHS = frozenset({"_path_mark_pass", "_path_advance"})
_ENVIRONMENT_DERIVED_FIELDS = frozenset(
    {
        "activity_rollup",
        "activity_segments",
        "command_outcomes",
        "created_at_session",
        "goal_dispatch_host",
        "host_run_id",
        "hostname",
        "last_activity_at",
        "lease_expires_at",
        "phase_durations_sec",
        "pid",
        "pid_source",
        "project_root",
        "root_run_id",
        "score_history",
        "specialists_decision",
        "started_at",
    }
)
_ENVIRONMENT_PLACEHOLDER = "<environment-derived>"


def _leaf_differences(current, projected, path="$"):
    differences = []
    if isinstance(current, dict) and isinstance(projected, dict):
        for key in sorted(set(current) | set(projected)):
            child = "%s.%s" % (path, key)
            if key not in current or key not in projected:
                differences.append(child)
            else:
                differences.extend(_leaf_differences(current[key], projected[key], child))
        return differences
    if isinstance(current, list) and isinstance(projected, list):
        for index in range(max(len(current), len(projected))):
            child = "%s[%d]" % (path, index)
            if index >= len(current) or index >= len(projected):
                differences.append(child)
            else:
                differences.extend(_leaf_differences(current[index], projected[index], child))
        return differences
    if current != projected or type(current) is not type(projected):
        differences.append(path)
    return differences


@pytest.fixture(scope="module")
def projection_cases(tmp_path_factory):
    from mission_kernel import project_legacy_document
    from . import test_issue632_transition_is_the_writer as harness

    tmp_path = tmp_path_factory.mktemp("issue644-projection")
    cases = {}
    for path_name in _PATH_NAMES:
        saved = {}
        decision = getattr(harness, path_name)(tmp_path, saved)
        assert decision is not None and decision.accepted is True
        assert decision.transition is not None
        projected_bytes = project_legacy_document(decision.transition.new_state)
        cases[path_name] = {
            "saved": saved,
            "projected": json.loads(projected_bytes),
            "projected_bytes": projected_bytes,
        }
    return cases


@pytest.mark.parametrize("path_name", _PATH_NAMES)
def test_current_saved_document_matches_checked_in_golden(
    projection_cases, path_name
):
    saved = projection_cases[path_name]["saved"]
    golden = _GOLDEN_DOCUMENTS[path_name]

    assert set(saved) == set(golden)
    differing = {key for key in golden if saved[key] != golden[key]}
    allowed = (
        _ENVIRONMENT_DERIVED_FIELDS
        if path_name in _CORPUS_BACKED_PATHS
        else frozenset()
    )
    assert differing <= allowed
    if path_name in _CORPUS_BACKED_PATHS:
        assert all(
            golden[key] == _ENVIRONMENT_PLACEHOLDER
            for key in allowed & set(golden)
        )
    else:
        assert _ENVIRONMENT_PLACEHOLDER not in golden.values()


@pytest.mark.parametrize("path_name", _PATH_NAMES)
@pytest.mark.xfail(
    strict=True,
    reason="PR2 adds the typed compatibility payload needed for exact projection",
)
def test_current_saved_document_exactly_matches_new_state_projection(
    projection_cases, path_name
):
    case = projection_cases[path_name]
    equality = (
        case["saved"] == case["projected"],
        canonical_json_bytes(case["saved"]) == case["projected_bytes"],
    )
    assert equality == (True, True)


@pytest.mark.parametrize("path_name", _PATH_NAMES)
def test_projection_gap_is_only_the_documented_a_payload(
    projection_cases, path_name
):
    case = projection_cases[path_name]
    actual = set(_leaf_differences(case["saved"], case["projected"]))
    expected = {
        path
        for path, classification in _DOCUMENTED_PRE_FIX_DIFFERENCES[path_name].items()
        if classification == _A_PAYLOAD
    }
    assert actual == expected


def test_documented_pre_fix_inventory_has_exactly_59_classified_leaves():
    inventory = {
        (path_name, path): classification
        for path_name, paths in _DOCUMENTED_PRE_FIX_DIFFERENCES.items()
        for path, classification in paths.items()
    }
    assert len(inventory) == 59
    assert set(inventory.values()) == {_A_PAYLOAD, _A_INPUT, _B_PROJECTOR}
    assert {
        classification: tuple(inventory.values()).count(classification)
        for classification in {_A_PAYLOAD, _A_INPUT, _B_PROJECTOR}
    } == {_A_PAYLOAD: 45, _A_INPUT: 5, _B_PROJECTOR: 9}


def test_59_leaf_synthetic_fixture_detects_a_single_tampered_leaf():
    current = {
        "%s:%s" % (path_name, path): classification
        for path_name, paths in _DOCUMENTED_PRE_FIX_DIFFERENCES.items()
        for path, classification in paths.items()
    }
    projected = copy.deepcopy(current)
    changed_key = sorted(projected)[37]
    projected[changed_key] = "tampered"

    assert _leaf_differences(current, projected) == ["$.%s" % changed_key]
    assert canonical_json_bytes(current) != canonical_json_bytes(projected)


@pytest.mark.parametrize(
    ("field", "expected"),
    (
        ("halt_category", "blocked-external"),
        ("terminal_outcome", "blocked_external"),
    ),
)
def test_projector_inserts_non_none_terminal_control_without_source_key(
    field, expected
):
    from mission_kernel import decode_mission_state, project_legacy_document
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    state = decode_mission_state(canonical_json_bytes({"phase": "executing", "loop_active": True}))
    decision = decide(state, MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked"))
    assert decision.accepted is True

    projected = json.loads(project_legacy_document(decision.transition.new_state))
    assert projected[field] == expected


def test_projector_removes_none_terminal_control_values():
    from mission_kernel import decode_mission_state, project_legacy_document

    source = {
        "phase": "halted",
        "loop_active": False,
        "halt_reason": "blocked",
        "halt_category": "blocked-external",
        "terminal_outcome": "blocked_external",
    }
    state = decode_mission_state(canonical_json_bytes(source))
    control = replace(
        state.control,
        halt_category=None,
        terminal_outcome=None,
    )
    projected = json.loads(project_legacy_document(replace(state, control=control)))

    assert "halt_category" not in projected
    assert "terminal_outcome" not in projected


def _leased_document(*, include_history, history):
    document = {
        "owner_session_id": "session-a",
        "lease_id": "lease-current",
        "fencing_epoch": 2,
        "lease_expires_at": "2030-08-23T02:00:00Z",
    }
    if include_history:
        document["lease_history"] = history
    return document


@pytest.mark.parametrize(
    ("include_history", "history", "expected_history"),
    (
        (False, [], None),
        (True, [], []),
        (
            False,
            [
                {
                    "owner_session_id": "session-old",
                    "lease_id": "lease-old",
                    "fencing_epoch": 1,
                    "reason": "lease-expired-takeover",
                    "at": "2030-08-23T00:00:00Z",
                }
            ],
            [
                {
                    "owner_session_id": "session-old",
                    "lease_id": "lease-old",
                    "fencing_epoch": 1,
                    "reason": "lease-expired-takeover",
                    "at": "2030-08-23T00:00:00Z",
                }
            ],
        ),
    ),
    ids=("missing-empty", "present-empty", "missing-nonempty"),
)
def test_projector_preserves_legacy_lease_history_presence(
    include_history, history, expected_history
):
    from mission_kernel import decode_mission_state, project_legacy_document
    from mission_kernel.model import LeaseHistoryEntry

    source = _leased_document(include_history=include_history, history=history)
    state = decode_mission_state(canonical_json_bytes(source))
    if history and not include_history:
        state = replace(
            state,
            lease=replace(
                state.lease,
                lease_history=tuple(LeaseHistoryEntry(**entry) for entry in history),
            ),
        )
    projected = json.loads(project_legacy_document(state))

    if expected_history is None:
        assert "lease_history" not in projected
    else:
        assert projected["lease_history"] == expected_history
