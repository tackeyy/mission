"""Issue #644 PR1: golden projection comparison and projector defects."""

from __future__ import annotations

import copy
import ast
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
def test_projection_has_zero_remaining_a_payload_differences(
    projection_cases, path_name
):
    case = projection_cases[path_name]
    actual = set(_leaf_differences(case["saved"], case["projected"]))
    assert actual == set()


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


def test_compatibility_payload_deep_freezes_nested_json_and_encodes_deterministically():
    from mission_kernel.commands import (
        CompatibilityPayload,
        MarkHalt,
        encode_kernel_command,
    )
    from mission_kernel.model import HaltCategory

    nested = {"activity_segments": [{"kind": "active"}]}
    payload = CompatibilityPayload(nested, ["resume_target_phase"])
    nested["activity_segments"][0]["kind"] = "tampered"

    command = MarkHalt(
        HaltCategory.BLOCKED_EXTERNAL,
        "blocked",
        at="2030-08-23T01:00:00Z",
        legacy_reason=" blocked ",
        compatibility=payload,
    )
    first = encode_kernel_command(command)
    second = encode_kernel_command(command)

    assert payload.upserts.thaw() == {
        "activity_segments": [{"kind": "active"}]
    }
    assert payload.removals == ("resume_target_phase",)
    assert first == second


def test_compatibility_payload_rejects_non_finite_json():
    from mission_kernel.commands import CompatibilityPayload

    with pytest.raises(ValueError):
        CompatibilityPayload({"activity_rollup": {"observed_total_sec": float("nan")}})


def test_compatibility_payload_rejects_duplicate_frozen_keys():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import CompatibilityPayload, MarkHalt
    from mission_kernel.model import FrozenJsonObject, HaltCategory
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes({"phase": "executing", "loop_active": True})
    )
    decision = decide(
        state,
        MarkHalt(
            HaltCategory.BLOCKED_EXTERNAL,
            "blocked",
            compatibility=CompatibilityPayload(
                FrozenJsonObject(
                    (("phase_started_at", "a"), ("phase_started_at", "b"))
                )
            ),
        ),
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "compatibility-payload-invalid"


@pytest.mark.parametrize(
    ("upserts", "removals", "reason"),
    (
        ({"phase": "done"}, (), "compatibility-field-forbidden"),
        ({"unknown": True}, (), "compatibility-field-unknown"),
        ({"updated_at": "2030-08-23T01:00:00Z"}, ("updated_at",), "compatibility-field-overlap"),
    ),
)
def test_mark_halt_rejects_invalid_compatibility_payload(upserts, removals, reason):
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import CompatibilityPayload, MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes({"phase": "executing", "loop_active": True})
    )
    decision = decide(
        state,
        MarkHalt(
            HaltCategory.BLOCKED_EXTERNAL,
            "blocked",
            compatibility=CompatibilityPayload(upserts, removals),
        ),
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == reason


def test_command_specific_compatibility_allowlist_cannot_cross_commands():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import CompatibilityPayload, Reactivate
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes(
            {
                "phase": "halted",
                "loop_active": False,
                "halt_reason": "blocked",
                "halt_category": "blocked-external",
            }
        )
    )
    decision = decide(
        state,
        Reactivate(
            HaltCategory.BLOCKED_EXTERNAL,
            "unblocked",
            True,
            Phase.PLANNING,
            compatibility=CompatibilityPayload({"passes_forced": False}),
        ),
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "compatibility-field-unknown"


@pytest.mark.parametrize(
    ("legacy_reason", "expected_code"),
    (("different", "legacy-reason-mismatch"), ("   ", "invalid-legacy-reason")),
)
def test_mark_halt_validates_raw_legacy_reason(legacy_reason, expected_code):
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes({"phase": "executing", "loop_active": True})
    )
    decision = decide(
        state,
        MarkHalt(
            HaltCategory.BLOCKED_EXTERNAL,
            "blocked",
            legacy_reason=legacy_reason,
        ),
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == expected_code


@pytest.mark.parametrize("new_pid", (True, 0, -1))
def test_resume_stale_rejects_invalid_new_pid(new_pid):
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import ResumeStale
    from mission_kernel.model import Phase
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes(
            {
                "phase": "halted",
                "loop_active": False,
                "halt_reason": "stale: old owner",
                "halt_category": "stale",
            }
        )
    )
    decision = decide(state, ResumeStale(Phase.PLANNING, new_pid=new_pid))

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "invalid-new-pid"


def test_reactivate_rejects_audit_previous_values_that_do_not_match_input():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import CompatibilityPayload, Reactivate
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes(
            {
                "phase": "halted",
                "loop_active": False,
                "halt_reason": "blocked",
                "halt_category": "blocked-external",
            }
        )
    )
    audit = {
        "timestamp": "2030-08-23T01:00:00Z",
        "previous_halt_reason": "different",
        "previous_halt_category": "blocked-external",
        "previous_phase": "halted",
        "approved_reason": "unblocked",
        "approved_by_user": True,
        "target_phase": "planning",
    }
    decision = decide(
        state,
        Reactivate(
            HaltCategory.BLOCKED_EXTERNAL,
            "unblocked",
            True,
            Phase.PLANNING,
            at="2030-08-23T01:00:00Z",
            compatibility=CompatibilityPayload({"reactivation_history": [audit]}),
        ),
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "reactivation-audit-invalid"


@pytest.mark.parametrize(
    "payload",
    (
        {"phase_started_at": "2030-08-23T00:00:00Z"},
        {"activity_segments": [{"kind": "active"}]},
        {"resume_target_phase": "planning"},
    ),
)
def test_permission_observation_rejects_invalid_timing_shape(payload):
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import CompatibilityPayload, MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes({"phase": "executing", "loop_active": True})
    )
    decision = decide(
        state,
        MarkHalt(
            HaltCategory.BLOCKED_EXTERNAL,
            "permission denied",
            at="2030-08-23T01:00:00Z",
            legacy_reason="permission denied",
            compatibility=CompatibilityPayload(payload),
            permission_observation=True,
        ),
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "permission-transition-invalid"


def test_force_pass_rejects_terminal_digest_not_bound_to_completed_new_state():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import CompatibilityPayload, MarkPass
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        canonical_json_bytes({"phase": "scoring", "loop_active": True})
    )
    decision = decide(
        state,
        MarkPass(
            force=True,
            force_approval_verified=True,
            artifact_gate_satisfied=True,
            at="2030-08-23T01:00:00Z",
            compatibility=CompatibilityPayload(
                {
                    "passes_forced": True,
                    "force_approval": {
                        "request": {"terminal_object_digest": "sha256:" + "0" * 64},
                        "consumed": True,
                    },
                }
            ),
        ),
    )

    assert decision.accepted is False
    assert decision.rejection is not None
    assert decision.rejection.code == "force-approval-binding-invalid"


def test_force_digest_rejection_writes_nothing_and_skips_aggregate():
    import contextlib

    from mission_kernel.commands import CompatibilityPayload, MarkPass
    from mission_persistence.legacy_v4 import LegacyV4Repository

    writes = []
    aggregate = []
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {"phase": "scoring", "loop_active": True},
        write_state=lambda document: writes.append(copy.deepcopy(document)),
        backup_state=lambda: None,
        remove_from_aggregate=lambda: aggregate.append("remove"),
    )
    command = MarkPass(
        force=True,
        force_approval_verified=True,
        artifact_gate_satisfied=True,
        compatibility=CompatibilityPayload(
            {
                "passes_forced": True,
                "force_approval": {
                    "request": {"terminal_object_digest": "sha256:" + "0" * 64},
                    "consumed": True,
                },
            }
        ),
    )
    with repository.transaction():
        repository.load()
        result = repository.execute(command, aggregate_action="remove")

    assert result.decision.accepted is False
    assert result.decision.rejection.code == "force-approval-binding-invalid"
    assert writes == []
    assert aggregate == []


def test_legacy_execute_contract_has_no_mutation_transition_or_finalize_parameters():
    import inspect

    from mission_application.ports import LegacyMissionRepository
    from mission_persistence.legacy_v4 import LegacyV4Repository

    for target in (LegacyMissionRepository.execute, LegacyV4Repository.execute):
        parameters = inspect.signature(target).parameters
        assert "mutation" not in parameters
        assert "transition" not in parameters
        assert "finalize" not in parameters


def test_production_execute_calls_have_no_caller_supplied_state_or_transition():
    mission_root = Path(__file__).resolve().parents[1]
    paths = (
        mission_root / "lib" / "mission_application" / "lifecycle.py",
        mission_root / "lib" / "mission_application" / "review.py",
        mission_root / "lib" / "mission_application" / "runtime_guard.py",
        mission_root / "bin" / "mission-state.py",
    )
    offenders = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and len(node.args) > 1
            ):
                offenders.append((str(path), node.lineno, len(node.args)))
    assert offenders == []


def test_legacy_command_execute_decides_projects_and_writes_atomically():
    import contextlib

    from mission_kernel.commands import CompatibilityPayload, MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_persistence.legacy_v4 import LegacyV4Repository

    source = {"phase": "executing", "loop_active": True, "custom_note": "kept"}
    writes = []
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(source),
        write_state=lambda document: writes.append(copy.deepcopy(document)),
        backup_state=lambda: None,
        remove_from_aggregate=lambda: None,
    )

    with repository.transaction():
        loaded = repository.load()
        result = repository.execute(
            MarkHalt(
                HaltCategory.BLOCKED_EXTERNAL,
                "blocked",
                at="2030-08-23T01:00:00Z",
                compatibility=CompatibilityPayload(
                    {"phase_started_at": "2030-08-23T01:00:00Z"}
                ),
            ),
            aggregate_action="remove",
        )

    assert loaded == source
    assert result.decision.accepted is True
    assert result.projection == writes[0]
    assert writes == [
        {
            "phase": "halted",
            "loop_active": False,
            "custom_note": "kept",
            "halt_reason": "blocked",
            "halt_category": "blocked-external",
            "terminal_outcome": "blocked_external",
            "phase_started_at": "2030-08-23T01:00:00Z",
            "updated_at": "2030-08-23T01:00:00Z",
        }
    ]


def test_unrelated_typed_command_preserves_tolerated_legacy_plan_and_review_wire_shape():
    import contextlib

    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_application.ports import AggregateIndexError
    from mission_persistence.legacy_v4 import LegacyV4Repository

    digest = "sha256:" + "a" * 64
    source = {
        "schema_version": 4,
        "phase": "planning",
        "iteration": 1,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2030-08-23T00:00:00Z",
        "canonical_plan": {
            "path": ".mission-state/plans/core.json",
            "digest": digest,
            "source": "core",
            "source_id": "fixture-core",
            "selection_source": "automatic",
            "iteration": 1,
            "generation": 1,
        },
        "review_evidence_refs": [
            {
                "path": ".mission-state/archive/review.json",
                "digest": digest,
                "size": 10,
                "iteration": 1,
                "perspective": "fixture",
            }
        ],
    }
    writes = []

    def fail_aggregate():
        raise OSError("aggregate index is unavailable")

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(source),
        write_state=lambda document: writes.append(copy.deepcopy(document)),
        backup_state=lambda: None,
        remove_from_aggregate=fail_aggregate,
    )

    with pytest.raises(AggregateIndexError) as raised:
        with repository.transaction():
            repository.load()
            repository.execute(
                MarkHalt(
                    HaltCategory.BLOCKED_EXTERNAL,
                    "blocked",
                    at="2030-08-23T01:00:00Z",
                    legacy_reason="blocked",
                ),
                aggregate_action="remove",
            )

    assert writes[0]["canonical_plan"] == source["canonical_plan"]
    assert writes[0]["review_evidence_refs"] == source["review_evidence_refs"]
    assert raised.value.execution.projection == writes[0]


def test_atomic_execute_does_not_run_aggregate_after_write_failure():
    import contextlib

    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_persistence.legacy_v4 import LegacyV4Repository

    aggregate = []

    def fail_write(_document):
        raise OSError("write failed")

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {"phase": "executing", "loop_active": True},
        write_state=fail_write,
        backup_state=lambda: None,
        remove_from_aggregate=lambda: aggregate.append("remove"),
    )
    with pytest.raises(OSError, match="write failed"):
        with repository.transaction():
            repository.load()
            repository.execute(
                MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked"),
                aggregate_action="remove",
            )

    assert aggregate == []


def test_v4_and_v5_compatibility_commit_the_same_projection():
    import contextlib

    from .evidence_doubles import FakeFencedRepository as _FakeFencedRepository
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import CompatibilityPayload, MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_persistence.legacy_v4 import (
        LegacyV4Repository,
        V5CompatibilityRepository,
    )

    source = {
        "session_id": "issue644-v4-v5",
        "phase": "executing",
        "loop_active": True,
        "custom_note": "kept",
    }
    command = MarkHalt(
        HaltCategory.BLOCKED_EXTERNAL,
        "blocked",
        at="2030-08-23T01:00:00Z",
        legacy_reason="blocked",
        compatibility=CompatibilityPayload(
            {"phase_started_at": "2030-08-23T01:00:00Z"}
        ),
    )
    v4_writes = []
    v4 = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(source),
        write_state=lambda document: v4_writes.append(copy.deepcopy(document)),
        backup_state=lambda: None,
    )
    with v4.transaction():
        v4.load()
        v4_result = v4.execute(command)

    backend = _FakeFencedRepository(
        decode_mission_state(canonical_json_bytes(source))
    )
    v5 = V5CompatibilityRepository(
        repository=backend,
        session_id="issue644-v4-v5",
        lease_owner_session_id="issue644-v4-v5",
        presented_lease_id=None,
    )
    with v5.transaction():
        v5.load()
        v5_result = v5.execute(command)

    assert v4_result.projection == v5_result.projection
    assert v4_writes == backend.commits == [v4_result.projection]


def test_v5_post_commit_aggregate_failure_carries_metadata_exact_execution():
    from .evidence_doubles import FakeFencedRepository as _FakeFencedRepository
    from mission_application.ports import AggregateIndexError
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    source = {
        "session_id": "issue644-v5-fault",
        "phase": "executing",
        "loop_active": True,
    }
    backend = _FakeFencedRepository(
        decode_mission_state(canonical_json_bytes(source))
    )
    intent = object()

    def fail_finalize(_intent):
        raise OSError("aggregate index is unavailable")

    repository = V5CompatibilityRepository(
        repository=backend,
        session_id="issue644-v5-fault",
        lease_owner_session_id="issue644-v5-fault",
        presented_lease_id=None,
        metadata={"hostname": "metadata-host"},
        aggregate_recover=lambda: None,
        aggregate_prepare=lambda action: intent if action == "remove" else None,
        aggregate_finalize=fail_finalize,
    )
    command = MarkHalt(
        HaltCategory.BLOCKED_EXTERNAL,
        "blocked",
        at="2030-08-23T01:00:00Z",
        legacy_reason="blocked",
    )

    with pytest.raises(AggregateIndexError) as raised:
        with repository.transaction():
            repository.load()
            repository.execute(command, aggregate_action="remove")

    assert len(backend.commits) == 1
    assert raised.value.execution.projection == backend.commits[0]
    assert raised.value.execution.projection["hostname"] == "metadata-host"


def test_v5_metadata_constructor_rejects_fields_outside_the_nine_field_set():
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    with pytest.raises(ValueError, match="metadata field is not closed"):
        V5CompatibilityRepository(
            repository=object(),
            session_id="issue644-metadata",
            lease_owner_session_id="issue644-metadata",
            presented_lease_id=None,
            metadata={"phase": "halted"},
        )


def test_atomic_cutover_removes_pending_claims_and_renames_callback_depth():
    repository_source = (
        Path(__file__).resolve().parents[1]
        / "lib"
        / "mission_persistence"
        / "legacy_v4.py"
    ).read_text(encoding="utf-8")

    for removed in (
        "_PendingDecision",
        "_pending",
        "_apply_transition_claims",
        "_verify_transition_claims",
        "_executing",
        "_prepare_state",
    ):
        assert removed not in repository_source
    assert repository_source.count("self._callback_depth = 0") == 2
    transition_source = (
        Path(__file__).resolve().parents[1]
        / "lib"
        / "mission_kernel"
        / "transitions.py"
    ).read_text(encoding="utf-8")
    assert "transition_control_claim_bounds" not in transition_source
    assert "transition_control_claims" not in transition_source


def test_atomic_cutover_keeps_the_issued_transition_registry():
    from mission_kernel import transitions

    assert isinstance(transitions._ISSUED_TRANSITIONS, dict)
    assert callable(transitions.is_sealed_transition)
    assert callable(transitions.is_transition_bound_to)
