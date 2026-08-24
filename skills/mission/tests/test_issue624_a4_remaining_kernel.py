"""Issue #624: remaining A4 session writers use typed kernel transitions."""

from __future__ import annotations

import json
import ast
import hashlib
from contextlib import contextmanager
from pathlib import Path


def _canonical_plan_payload(*, dependencies=None) -> dict:
    return {
        "schema": "mission-plan/1",
        "steps": [
            {"id": "step-1", "depends_on": []},
            {"id": "step-2", "depends_on": ["step-1"]},
        ] if dependencies is None else dependencies,
    }


def _canonical_plan_raw(*, dependencies=None) -> bytes:
    return json.dumps(
        _canonical_plan_payload(dependencies=dependencies),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(char: str = "a") -> str:
    if char == "a":
        return "sha256:" + hashlib.sha256(_canonical_plan_raw()).hexdigest()
    return "sha256:" + char * 64


def _handoff_document(*, status: str = "prepared", decisions=None) -> dict:
    handoff = {
        "schema": "mission-executor-handoff/1",
        "handoff_id": "handoff_" + "a" * 32,
        "plan_path": ".mission-state/plans/plan.json",
        "plan_digest": _digest(),
        "plan_generation": 4,
        "plan_source": "provider",
        "source_id": "inv_" + "1" * 32,
        "selection_source": "automatic",
        "iteration": 2,
        "step_ids": ["step-1", "step-2"],
        "status": status,
    }
    if status in {"consuming", "consumed"}:
        handoff["begun_at"] = "2029-12-31T23:59:59Z"
    if status == "consumed":
        handoff["consumed_at"] = "2030-01-01T00:00:04Z"
    return {
        "schema_version": 4,
        "mission": "Execute a typed handoff",
        "mission_id": "mission-624",
        "session_id": "portable-session",
        "phase": "executing",
        "iteration": 2,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "updated_at": "2029-12-31T23:59:58Z",
        "canonical_plan": {
            "schema": "mission-plan/1",
            "path": ".mission-state/plans/plan.json",
            "digest": _digest(),
            "source": "provider",
            "source_id": "inv_" + "1" * 32,
            "source_digest": _digest("b"),
            "selection_source": "automatic",
            "iteration": 2,
            "generation": 4,
            "validated_at": "2030-01-01T00:00:00Z",
        },
        "executor_handoff": handoff,
        "decisions": list(decisions or []),
    }


def _plan_observation():
    from mission_kernel.commands import CanonicalPlanObservation

    return CanonicalPlanObservation(
        path=".mission-state/plans/plan.json",
        digest=_digest(),
        generation=4,
        source="provider",
        source_id="inv_" + "1" * 32,
        selection_source="automatic",
        iteration=2,
        ordered_step_ids=("step-1", "step-2"),
        dependencies=(("step-1", ()), ("step-2", ("step-1",))),
        raw=_canonical_plan_raw(),
    )


def test_init_adapter_policy_helpers_are_pure_and_preserve_current_contract():
    from mission_application.lifecycle import (
        initialization_operation_id,
        should_route_init_to_goal,
    )

    command_bytes = b'{"kind":"init"}'
    expected = "init:" + hashlib.sha256(
        b"portable-session\x00" + command_bytes
    ).hexdigest()
    assert initialization_operation_id("portable-session", command_bytes) == expected

    simple = {
        "complexity": "Simple",
        "review_tier_signals": [],
    }
    args = type(
        "InitArguments",
        (),
        {"force_mission": False, "issue_ref": None},
    )()
    assert should_route_init_to_goal(simple, args, None) is True
    assert should_route_init_to_goal(simple, args, "Full") is False


def test_handoff_operations_are_closed_typed_kernel_commands():
    from mission_kernel.commands import (
        BeginExecutorHandoff,
        CanonicalPlanObservation,
        CompleteExecutorHandoff,
        RecordExecutorStep,
        VerifyExecutorStep,
        kernel_command_type,
    )

    plan = CanonicalPlanObservation(
        path=".mission-state/plans/plan.json",
        digest=_digest(),
        generation=4,
        source="provider",
        source_id="inv_" + "1" * 32,
        selection_source="automatic",
        iteration=2,
        ordered_step_ids=("step-1", "step-2"),
        dependencies=(("step-1", ()), ("step-2", ("step-1",))),
        raw=_canonical_plan_raw(),
    )

    assert kernel_command_type(BeginExecutorHandoff("2030-01-01T00:00:00Z", plan)) == (
        "executor-handoff-begin"
    )
    assert kernel_command_type(
        VerifyExecutorStep("2030-01-01T00:00:00Z", "step-1", plan)
    ) == "executor-handoff-verify-step"
    assert kernel_command_type(
        RecordExecutorStep("2030-01-01T00:00:00Z", "step-1", "ok", plan)
    ) == "executor-handoff-record-step"
    assert kernel_command_type(
        CompleteExecutorHandoff("2030-01-01T00:00:00Z", plan)
    ) == "executor-handoff-complete"


def test_v4_codec_owns_only_current_handoff_decisions_in_a4_projection():
    from mission_kernel import decode_mission_state, project_legacy_document

    current = {
        "handoff_id": "handoff_" + "a" * 32,
        "plan_digest": _digest(),
        "plan_generation": 4,
        "plan_source": "provider",
        "source_id": "inv_" + "1" * 32,
        "selection_source": "automatic",
        "iteration": 2,
        "step_id": "step-1",
        "result": "ok",
    }
    historical = {**current, "handoff_id": "handoff_" + "b" * 32}
    document = {
        "schema_version": 4,
        "mission": "Execute a typed handoff",
        "mission_id": "mission-624",
        "session_id": "portable-session",
        "phase": "executing",
        "iteration": 2,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "canonical_plan": {
            "schema": "mission-plan/1",
            "path": ".mission-state/plans/plan.json",
            "digest": _digest(),
            "source": "provider",
            "source_id": "inv_" + "1" * 32,
            "source_digest": _digest("b"),
            "selection_source": "automatic",
            "iteration": 2,
            "generation": 4,
            "validated_at": "2030-01-01T00:00:00Z",
        },
        "executor_handoff": {
            "schema": "mission-executor-handoff/1",
            "handoff_id": current["handoff_id"],
            "plan_path": ".mission-state/plans/plan.json",
            "plan_digest": _digest(),
            "plan_generation": 4,
            "plan_source": "provider",
            "source_id": "inv_" + "1" * 32,
            "selection_source": "automatic",
            "iteration": 2,
            "step_ids": ["step-1", "step-2"],
            "status": "prepared",
        },
        "decisions": [historical, current],
    }

    state = decode_mission_state(json.dumps(document).encode("utf-8"))

    assert len(state.a4.current_handoff_decisions) == 1
    assert state.a4.current_handoff_decisions[0].step_id == "step-1"
    assert json.loads(project_legacy_document(state))["decisions"] == [
        historical,
        current,
    ]


def test_v4_codec_rejects_malformed_current_handoff_decision_only():
    import pytest

    from mission_kernel import decode_mission_state
    from mission_kernel.errors import MissionStateDecodeError

    document = _handoff_document()
    document["decisions"] = [
        {
            "handoff_id": "handoff_" + "a" * 32,
            "plan_digest": _digest(),
            "plan_generation": 4,
            "plan_source": "provider",
            "source_id": "inv_" + "1" * 32,
            "selection_source": "automatic",
            "iteration": 2,
            "step_id": "step-1",
            "result": "ok",
            "unowned": True,
        }
    ]

    with pytest.raises(MissionStateDecodeError):
        decode_mission_state(json.dumps(document).encode("utf-8"))


def test_planning_advance_rejects_historical_handoff_id_collision_without_mutation():
    from mission_kernel import decode_mission_state, project_legacy_document
    from mission_kernel.commands import AdvancePhase
    from mission_kernel.model import Phase, PreparedHandoff
    from mission_kernel.transitions import decide

    historical = {
        "handoff_id": "handoff_" + "a" * 32,
        "plan_digest": _digest(),
        "plan_generation": 4,
        "plan_source": "provider",
        "source_id": "inv_" + "1" * 32,
        "selection_source": "automatic",
        "iteration": 2,
        "step_id": "step-1",
        "result": "ok",
    }
    document = _handoff_document(decisions=[historical])
    document.pop("executor_handoff")
    document["phase"] = "planning"
    source = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    state = decode_mission_state(source)
    command = AdvancePhase(
        Phase.EXECUTING,
        PreparedHandoff(
            "mission-handoff/1",
            historical["handoff_id"],
            state.plan,
            ("step-1", "step-2"),
        ),
    )

    rejected = decide(state, command)

    assert rejected.accepted is False
    assert rejected.transition is None
    assert rejected.rejection.code == "handoff-id-collision"
    assert state.legacy_passthrough.thaw() == document
    assert source == json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

    accepted = decide(
        state,
        AdvancePhase(
            Phase.EXECUTING,
            PreparedHandoff(
                "mission-handoff/1", "handoff_distinct", state.plan, ("step-1", "step-2")
            ),
        ),
    )
    assert accepted.accepted is True
    assert json.loads(project_legacy_document(accepted.transition.new_state))["decisions"] == [
        historical
    ]


def test_v5_planning_advance_rejects_historical_handoff_id_collision():
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import AdvancePhase
    from mission_kernel.model import Phase, PreparedHandoff
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    historical = {"handoff_id": "handoff-v5-historical", "step_id": "old-step"}
    payload = current_v5_open_state()
    payload["handoff"] = {"kind": "absent"}
    payload["extensions"]["decisions"] = [historical]
    source = canonical_json_bytes(payload)
    snapshot = decode_snapshot(source)
    state = snapshot.state

    rejected = decide(
        state,
        AdvancePhase(
            Phase.EXECUTING,
            PreparedHandoff(
                "mission-handoff/1", "handoff-v5-historical", state.plan, ("s1", "s2")
            ),
        ),
    )

    assert rejected.accepted is False
    assert rejected.transition is None
    assert rejected.rejection.code == "handoff-id-collision"
    assert source == canonical_json_bytes(payload)

    accepted = decide(
        state,
        AdvancePhase(
            Phase.EXECUTING,
            PreparedHandoff("mission-handoff/1", "handoff-v5-distinct", state.plan, ("s1", "s2")),
        ),
    )
    assert accepted.accepted is True
    assert accepted.transition.new_state.extensions.thaw()["decisions"] == [historical]


def test_set_extension_fields_syncs_a4_projection_for_v4_and_v5():
    from mission_kernel import decode_mission_state, decode_snapshot, project_legacy_document
    from mission_kernel.codec_v5 import encode_v5_state
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    fields = freeze_json_value({"specialists_mode": "manual", "planning_policy_version": 0})
    assert fields is not None
    v4 = decode_mission_state(json.dumps(_handoff_document()).encode("utf-8"))
    v4_result = decide(v4, SetExtensionFields(fields))
    assert v4_result.accepted is True
    assert v4_result.transition.new_state.a4.specialist_selection.mode == "manual"
    assert v4_result.transition.new_state.a4.specialist_selection.planning_policy_version == 0
    projected_v4 = json.loads(project_legacy_document(v4_result.transition.new_state))
    assert projected_v4["specialists_mode"] == "manual"
    assert projected_v4["planning_policy_version"] == 0

    payload = current_v5_open_state()
    snapshot = decode_snapshot(canonical_json_bytes(payload))
    v5_result = decide(snapshot.state, SetExtensionFields(fields))
    assert v5_result.accepted is True
    assert v5_result.transition.new_state.a4.specialist_selection.mode == "manual"
    assert v5_result.transition.new_state.a4.specialist_selection.planning_policy_version == 0
    encoded_v5 = json.loads(encode_v5_state(v5_result.transition.new_state, snapshot.guidance))
    assert encoded_v5["extensions"]["specialists_mode"] == "manual"
    assert encoded_v5["extensions"]["planning_policy_version"] == 0


def test_handoff_reducers_preserve_lineage_and_current_non_durable_verify_order():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import (
        BeginExecutorHandoff,
        CompleteExecutorHandoff,
        RecordExecutorStep,
        VerifyExecutorStep,
    )
    from mission_kernel.transitions import decide

    state = decode_mission_state(json.dumps(_handoff_document()).encode("utf-8"))
    plan = _plan_observation()

    begun = decide(state, BeginExecutorHandoff("2030-01-01T00:00:01Z", plan))
    assert begun.accepted is True
    assert begun.events[0].type == "executor-handoff-begun"
    state = begun.transition.new_state
    assert state.handoff.kind.value == "consuming"

    first = decide(
        state,
        RecordExecutorStep("2030-01-01T00:00:02Z", "step-1", "ok", plan),
    )
    assert first.accepted is True
    state = first.transition.new_state
    assert [item.step_id for item in state.a4.current_handoff_decisions] == [
        "step-1"
    ]

    verified = decide(
        state,
        VerifyExecutorStep("2030-01-01T00:00:03Z", "step-2", plan),
    )
    assert verified.accepted is True
    assert verified.events[0].type == "executor-step-revalidated"
    assert verified.transition.new_state.a4 == state.a4
    state = verified.transition.new_state

    second = decide(
        state,
        RecordExecutorStep("2030-01-01T00:00:04Z", "step-2", "partial", plan),
    )
    assert second.accepted is True
    state = second.transition.new_state

    completed = decide(
        state, CompleteExecutorHandoff("2030-01-01T00:00:05Z", plan)
    )
    assert completed.accepted is True
    assert completed.events[0].type == "executor-handoff-consumed"
    assert completed.transition.effects == ()
    final = completed.transition.new_state
    assert final.handoff.kind.value == "consumed"
    assert final.control == state.control
    assert final.scores == state.scores
    assert final.legacy_passthrough.thaw()["updated_at"] == (
        "2030-01-01T00:00:05Z"
    )


def test_record_from_prepared_remains_allowed_and_verify_adds_no_receipt():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordExecutorStep, VerifyExecutorStep
    from mission_kernel.transitions import decide

    state = decode_mission_state(json.dumps(_handoff_document()).encode("utf-8"))
    plan = _plan_observation()

    recorded = decide(
        state,
        RecordExecutorStep("2030-01-01T00:00:01Z", "step-1", "ok", plan),
    )
    assert recorded.accepted is True
    recorded_state = recorded.transition.new_state
    assert recorded_state.handoff.kind.value == "prepared"

    verified = decide(
        recorded_state,
        VerifyExecutorStep("2030-01-01T00:00:02Z", "step-1", plan),
    )
    assert verified.accepted is True
    assert verified.transition.new_state.a4 == recorded_state.a4
    assert not hasattr(verified.transition.new_state.a4, "verification_receipts")


def test_handoff_rejects_plan_iteration_that_differs_from_session_control():
    from mission_application.ports import PreparedTransitionOperation
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordExecutorStep
    from mission_kernel.transitions import decide
    from mission_persistence.legacy_v4 import LegacyV4Repository

    document = _handoff_document()
    document["iteration"] = 3
    state = decode_mission_state(json.dumps(document).encode("utf-8"))

    rejected = decide(
        state,
        RecordExecutorStep(
            "2030-01-01T00:00:01Z", "step-1", "ok", _plan_observation()
        ),
    )

    assert rejected.accepted is False
    assert rejected.transition is None
    assert rejected.rejection.code == "executor-handoff-plan-drift"

    saved = []

    @contextmanager
    def lock():
        yield

    repository = LegacyV4Repository(
        lock=lock,
        read_state=lambda: document,
        write_state=lambda projection: saved.append(projection),
        backup_state=lambda: None,
    )
    _prepared, execution = repository.execute_transition_effects(
        lambda _state: PreparedTransitionOperation(
            command=RecordExecutorStep(
                "2030-01-01T00:00:01Z",
                "step-1",
                "ok",
                _plan_observation(),
            ),
            effects=(),
            result={"operation": "record"},
        )
    )
    assert execution.decision.accepted is False
    assert saved == []


def test_handoff_rejects_unhashable_non_string_dependency_as_closed_input():
    from dataclasses import replace

    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordExecutorStep
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        json.dumps(_handoff_document()).encode("utf-8")
    )
    malformed = replace(
        _plan_observation(),
        dependencies=(
            ("step-1", ([],)),
            ("step-2", ("step-1",)),
        ),
    )

    rejected = decide(
        state,
        RecordExecutorStep(
            "2030-01-01T00:00:01Z", "step-1", "ok", malformed
        ),
    )

    assert rejected.accepted is False
    assert rejected.transition is None
    assert rejected.rejection.code == "executor-handoff-dependencies-invalid"


def test_handoff_rejects_direct_command_that_forges_bound_dependencies():
    from dataclasses import replace

    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordExecutorStep
    from mission_kernel.transitions import decide

    state = decode_mission_state(json.dumps(_handoff_document()).encode("utf-8"))
    forged = replace(
        _plan_observation(),
        dependencies=(("step-1", ()), ("step-2", ())),
    )

    rejected = decide(
        state,
        RecordExecutorStep("2030-01-01T00:00:01Z", "step-2", "ok", forged),
    )

    assert rejected.accepted is False
    assert rejected.transition is None


def test_handoff_closes_mutated_noncanonical_unknown_and_bool_plan_facts():
    from dataclasses import replace

    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordExecutorStep
    from mission_kernel.transitions import decide

    state = decode_mission_state(json.dumps(_handoff_document()).encode("utf-8"))
    for raw in (_canonical_plan_raw() + b" ", _canonical_plan_raw()[:-1]):
        rejected = decide(
            state,
            RecordExecutorStep(
                "2030-01-01T00:00:01Z",
                "step-1",
                "ok",
                replace(_plan_observation(), raw=raw),
            ),
        )
        assert rejected.accepted is False

    unknown_raw = _canonical_plan_raw(
        dependencies=[
            {"id": "step-1", "depends_on": []},
            {"id": "step-2", "depends_on": ["missing"]},
        ]
    )
    unknown_digest = "sha256:" + hashlib.sha256(unknown_raw).hexdigest()
    unknown_document = _handoff_document()
    unknown_document["canonical_plan"]["digest"] = unknown_digest
    unknown_document["executor_handoff"]["plan_digest"] = unknown_digest
    unknown_state = decode_mission_state(json.dumps(unknown_document).encode("utf-8"))
    unknown = decide(
        unknown_state,
        RecordExecutorStep(
            "2030-01-01T00:00:01Z",
            "step-1",
            "ok",
            replace(
                _plan_observation(),
                digest=unknown_digest,
                raw=unknown_raw,
                dependencies=(("step-1", ()), ("step-2", ("missing",))),
            ),
        ),
    )
    assert unknown.accepted is False

    bool_generation = decide(
        state,
        RecordExecutorStep(
            "2030-01-01T00:00:01Z",
            "step-1",
            "ok",
            replace(_plan_observation(), generation=True),
        ),
    )
    assert bool_generation.accepted is False


def test_specialists_consent_rejects_session_file_without_changing_session_bytes(
    run_cli, tmp_path
):
    session = tmp_path / ".mission-state" / "sessions" / "consent-proof.json"
    session.parent.mkdir(parents=True)
    original = b'{"schema":"mission-state/4","sentinel":"unchanged"}\n'
    session.write_bytes(original)

    result = run_cli(
        "specialists",
        "consent",
        "--provider",
        "portable-provider",
        "--consent-file",
        str(session),
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "provider-consent-session-path-forbidden" in result.stderr
    assert session.read_bytes() == original

    case_variant = Path(str(session).replace(".mission-state", ".MISSION-STATE"))
    variant_result = run_cli(
        "specialists",
        "consent",
        "--provider",
        "portable-provider",
        "--consent-file",
        str(case_variant),
        cwd=tmp_path,
    )
    assert variant_result.returncode == 2
    assert "provider-consent-session-path-forbidden" in variant_result.stderr
    assert session.read_bytes() == original


def test_provider_consent_path_policy_rejects_session_aggregate_parts():
    import pytest

    from mission_application.runtime_guard import validate_provider_consent_path_parts

    for marker in (".mission-state", ".MISSION-STATE", ".Mission-State"):
        with pytest.raises(ValueError, match="provider-consent-session-path-forbidden"):
            validate_provider_consent_path_parts(
                ("other-project", marker, "sessions", "session.json")
            )

    class LazyText(str):
        def casefold(self):
            raise AssertionError("application must not invoke subclass behavior")

    with pytest.raises(ValueError, match="provider-consent-session-path-forbidden"):
        validate_provider_consent_path_parts((LazyText(".mission-state"),))


def test_provider_consent_path_resolution_uses_one_validated_representation(tmp_path):
    import pytest

    from mission_application.runtime_guard import (
        ProviderConsentRequest,
        ResolvedProviderConsentPathObservation,
        RuntimeGuardFailure,
        resolve_provider_consent_path,
        validate_provider_consent_request,
    )

    default_path = tmp_path / "default" / "provider-consent.json"
    explicit_path = tmp_path / "explicit" / "provider-consent.json"
    assert resolve_provider_consent_path(
        ResolvedProviderConsentPathObservation(parts=tuple(default_path.resolve().parts))
    ) == tuple(default_path.resolve().parts)
    assert resolve_provider_consent_path(
        ResolvedProviderConsentPathObservation(parts=tuple(explicit_path.resolve().parts))
    ) == tuple(explicit_path.resolve().parts)

    provider, parts = validate_provider_consent_request(
        ProviderConsentRequest(
            provider="  portable-provider  ",
            resolved_path=ResolvedProviderConsentPathObservation(
                parts=tuple(explicit_path.resolve().parts)
            ),
        )
    )
    assert provider == "portable-provider"
    assert parts == tuple(explicit_path.resolve().parts)

    with pytest.raises(RuntimeGuardFailure, match="--provider is required"):
        validate_provider_consent_request(
            ProviderConsentRequest(
                provider="  ",
                resolved_path=ResolvedProviderConsentPathObservation(
                    parts=tuple(explicit_path.resolve().parts)
                ),
            )
        )

    with pytest.raises(TypeError):
        resolve_provider_consent_path(
            ResolvedProviderConsentPathObservation(parts=("safe",)),
            resolved_path="/forged/path",
        )

    forbidden = tmp_path / ".mission-state" / "sessions" / "session.json"
    with pytest.raises(
        RuntimeGuardFailure,
        match="provider-consent-session-path-forbidden",
    ):
        resolve_provider_consent_path(
            ResolvedProviderConsentPathObservation(parts=tuple(forbidden.resolve().parts))
        )


def test_approval_verifier_distribution_without_dist_requires_one_owned_tuple():
    import inspect
    import pytest

    from mission_application.runtime_guard import (
        validate_registered_entry_point_distribution,
    )

    facts = {
        "entry_point_name": "portable-verifier",
        "entry_point_value": "portable.module:verify",
        "has_attached_distribution": False,
        "distribution_name": "portable-verifiers",
        "distribution_version": "1.2.3",
        "owned_entry_points": (
            ("mission.approval_verifiers", "portable-verifier", "portable.module:verify"),
        ),
        "configured_distribution": "portable-verifiers",
        "configured_version": "1.2.3",
        "group": "mission.approval_verifiers",
    }
    assert tuple(inspect.signature(validate_registered_entry_point_distribution).parameters) == tuple(facts)
    validate_registered_entry_point_distribution(
        **facts,
    )

    with pytest.raises(ValueError, match="approval verifier distribution identity mismatch"):
        validate_registered_entry_point_distribution(
            **{**facts, "owned_entry_points": ()},
        )

    with pytest.raises(ValueError, match="approval verifier distribution identity mismatch"):
        validate_registered_entry_point_distribution(
            **{
                **facts,
                "owned_entry_points": facts["owned_entry_points"] * 2,
            },
        )

    class IoSentinel:
        def __getattribute__(self, _name):
            raise AssertionError("application must not inspect provider objects")

    with pytest.raises(TypeError):
        validate_registered_entry_point_distribution(IoSentinel())

    class LazyText(str):
        def lower(self):
            raise AssertionError("application must not invoke subclass behavior")

        def __eq__(self, _other):
            raise AssertionError("application must not invoke subclass behavior")

    with pytest.raises(ValueError, match="approval verifier distribution identity mismatch"):
        validate_registered_entry_point_distribution(
            **{**facts, "distribution_name": LazyText("portable-verifiers")}
        )


def test_approval_verifier_distribution_observation_cannot_override_expectations():
    import pytest

    from mission_application.runtime_guard import (
        RegisteredEntryPointDistributionObservation,
        validate_registered_approval_entry_point_distribution,
    )

    observed = RegisteredEntryPointDistributionObservation(
        entry_point_name="portable-verifier",
        entry_point_value="portable.module:verify",
        has_attached_distribution=False,
        distribution_name="portable-verifiers",
        distribution_version="1.2.3",
        owned_entry_points=(
            (
                "mission.approval_verifiers",
                "portable-verifier",
                "portable.module:verify",
            ),
        ),
    )

    configured_item = {
        "distribution": "portable-verifiers",
        "version": "1.2.3",
    }
    validate_registered_approval_entry_point_distribution(
        observed,
        configured_item,
        group="mission.approval_verifiers",
    )

    with pytest.raises(ValueError, match="distribution identity mismatch"):
        validate_registered_approval_entry_point_distribution(
            observed,
            {"distribution": "forged-distribution", "version": "1.2.3"},
            group="mission.approval_verifiers",
        )


def test_closed_canonical_drift_rejection_preserves_prior_begin_lineage():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import (
        CanonicalPlanRejectionCode,
        RejectExecutorHandoff,
    )
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        json.dumps(_handoff_document(status="consuming")).encode("utf-8")
    )

    rejected = decide(
        state,
        RejectExecutorHandoff(
            "2030-01-01T00:00:01Z",
            "verify-step",
            CanonicalPlanRejectionCode.DIGEST_DRIFT,
        ),
    )

    assert rejected.accepted is True
    assert rejected.events[0].type == "executor-handoff-rejected"
    next_handoff = rejected.transition.new_state.handoff
    assert next_handoff.kind.value == "rejected"
    assert next_handoff.begun_at == "2029-12-31T23:59:59Z"
    assert next_handoff.rejected_reason == "canonical-plan-digest-drift"
    invalid = decide(
        state,
        RejectExecutorHandoff(
            "2030-01-01T00:00:01Z",
            "record-step",
            CanonicalPlanRejectionCode.DIGEST_DRIFT,
        ),
    )
    assert invalid.accepted is False
    assert invalid.transition is None


def test_handoff_application_prepares_existing_typed_executor_payload_only():
    from mission_application.planning import (
        ExecutorHandoffFacts,
        ExecutorHandoffRequest,
        prepare_executor_handoff,
    )
    from mission_application.ports import PreparedTransitionOperation
    from mission_kernel.commands import RecordExecutorStep

    prepared = prepare_executor_handoff(
        _handoff_document(),
        ExecutorHandoffRequest(
            operation="record",
            at="2030-01-01T00:00:01Z",
            step_id="step-1",
            result="ok",
        ),
        ExecutorHandoffFacts(
            plan_path=".mission-state/plans/plan.json",
            plan_digest=_digest(),
            plan_generation=4,
            plan_source="provider",
            source_id="inv_" + "1" * 32,
            selection_source="automatic",
            iteration=2,
            step_ids=("step-1", "step-2"),
            dependencies={"step-1": (), "step-2": ("step-1",)},
            decision_iteration=2,
            raw=_canonical_plan_raw(),
        ),
    )

    assert isinstance(prepared, PreparedTransitionOperation)
    assert isinstance(prepared.command, RecordExecutorStep)
    assert prepared.effects == ()
    assert prepared.result == {"operation": "record"}
    assert not hasattr(prepared, "state")


def test_existing_transition_executor_accepts_a4_empty_effect_preparation():
    from mission_application.planning import (
        ExecutorHandoffFacts,
        ExecutorHandoffRequest,
        prepare_executor_handoff,
    )
    from mission_persistence.legacy_v4 import LegacyV4Repository

    current = _handoff_document()
    saved = []

    @contextmanager
    def lock():
        yield

    repository = LegacyV4Repository(
        lock=lock,
        read_state=lambda: current,
        write_state=lambda document: saved.append(document),
        backup_state=lambda: None,
    )
    facts = ExecutorHandoffFacts(
        plan_path=".mission-state/plans/plan.json",
        plan_digest=_digest(),
        plan_generation=4,
        plan_source="provider",
        source_id="inv_" + "1" * 32,
        selection_source="automatic",
        iteration=2,
        step_ids=("step-1", "step-2"),
        dependencies={"step-1": (), "step-2": ("step-1",)},
        decision_iteration=2,
        raw=_canonical_plan_raw(),
    )

    prepared, execution = repository.execute_transition_effects(
        lambda state: prepare_executor_handoff(
            state,
            ExecutorHandoffRequest(
                operation="begin", at="2030-01-01T00:00:01Z"
            ),
            facts,
        )
    )

    assert prepared.effects == ()
    assert execution.decision.accepted is True
    assert execution.projection == saved[0]
    assert saved[0]["executor_handoff"]["status"] == "consuming"


def test_v5_transition_executor_returns_committed_projection_on_replay():
    from mission_application.planning import (
        ExecutorHandoffFacts,
        ExecutorHandoffRequest,
        prepare_executor_handoff,
    )
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = object.__new__(V5CompatibilityRepository)
    repository._callback_depth = 0
    repository._replayed = object()
    current = _handoff_document(status="consuming")

    @contextmanager
    def transaction():
        yield

    repository.transaction = transaction
    repository.load = lambda: current
    repository.execute = lambda _command: (_ for _ in ()).throw(
        AssertionError("replay must not decide or commit again")
    )
    facts = ExecutorHandoffFacts(
        plan_path=".mission-state/plans/plan.json",
        plan_digest=_digest(),
        plan_generation=4,
        plan_source="provider",
        source_id="inv_" + "1" * 32,
        selection_source="automatic",
        iteration=2,
        step_ids=("step-1", "step-2"),
        dependencies={"step-1": (), "step-2": ("step-1",)},
        decision_iteration=2,
        raw=_canonical_plan_raw(),
    )

    _prepared, execution = repository.execute_transition_effects(
        lambda state: prepare_executor_handoff(
            state,
            ExecutorHandoffRequest(
                operation="begin", at="2030-01-01T00:00:01Z"
            ),
            facts,
        )
    )

    assert execution.replayed is True
    assert execution.decision is None
    assert execution.projection == current


def test_handoff_application_maps_executor_result_to_cli_response():
    from mission_application.planning import executor_handoff_response
    from mission_application.ports import (
        LegacyCommandExecutionResult,
        PreparedTransitionOperation,
    )
    from mission_kernel.commands import BeginExecutorHandoff
    from mission_kernel.json_codec import freeze_json_value

    prepared = PreparedTransitionOperation(
        command=BeginExecutorHandoff("2030-01-01T00:00:01Z", _plan_observation()),
        effects=(),
        result={"operation": "begin"},
    )
    projection = freeze_json_value(_handoff_document(status="consuming"))
    execution = LegacyCommandExecutionResult(None, projection, replayed=True)

    assert executor_handoff_response(prepared, execution) == {
        "ok": True,
        "operation": "begin",
        "executor_handoff": _handoff_document(status="consuming")[
            "executor_handoff"
        ],
    }


def test_handoff_cli_adapter_has_no_direct_session_projection_writes():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_cmd_executor_handoff"
    )
    violations = []
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "save":
                violations.append((node.lineno, "repository.save"))
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value
                    in {"executor_handoff", "decisions", "updated_at"}
                ):
                    violations.append((node.lineno, target.slice.value))
    assert violations == []


def test_v4_a4_projection_closes_selection_and_active_invocation_guard_inputs():
    from mission_kernel import decode_mission_state

    document = _handoff_document()
    selection_id = "sel_" + "1" * 32
    document.update(
        {
            "complexity": "Complex",
            "task_profile": {"primary": "backend", "secondary": [], "confidence": 1.0, "risk": "low", "signals": []},
            "specialists_candidates": [
                {"skill": "portable-provider", "selection_id": selection_id}
            ],
            "specialists_selected": [
                {"skill": "portable-provider", "selection_id": selection_id}
            ],
            "specialists_unavailable": [],
            "specialists_ineligible": [],
            "specialist_registry_projection": None,
            "specialists_decision": {
                "decision": "selected",
                "selection_id": selection_id,
                "reason_code": "candidate-selected",
                "lifecycle_state": "selected",
            },
            "specialists_phase_plan": [],
            "specialists_mode": "auto",
            "planning_policy_version": 1,
            "planning_strategy": "core",
            "specialist_invocations": [
                {
                    "invocation_id": "inv_" + "2" * 32,
                    "status": "running",
                },
                {
                    "invocation_id": "inv_" + "3" * 32,
                    "status": "completed",
                },
            ],
        }
    )

    state = decode_mission_state(json.dumps(document).encode("utf-8"))
    selection = state.a4.specialist_selection

    assert selection.task_profile.thaw() == {
        "primary": "backend",
        "secondary": [],
        "confidence": 1.0,
        "risk": "low",
        "signals": [],
    }
    assert selection.selected[0].thaw()["selection_id"] == selection_id
    assert selection.active_provider_invocation_ids == ("inv_" + "2" * 32,)
    assert selection.planning_policy_version == 1
    assert selection.planning_strategy == "core"


def test_recommendation_application_prepares_selection_checkpoint_without_authority():
    from mission_application.planning import prepare_specialist_recommendation
    from mission_application.ports import PreparedTransitionOperation
    from mission_kernel.commands import RecordSpecialistRecommendation

    selection_id = "sel_" + "1" * 32
    prepared = prepare_specialist_recommendation(
        _handoff_document(),
        at="2030-01-01T00:00:01Z",
        expected_complexity="Complex",
        expected_iteration=2,
        result={
            "task_profile": {"primary": "backend", "secondary": [], "confidence": 1.0, "risk": "low", "signals": []},
            "specialists_candidates": [
                {"skill": "portable-provider", "selection_id": selection_id}
            ],
            "specialists_selected": [
                {"skill": "portable-provider", "selection_id": selection_id}
            ],
            "specialists_unavailable": [],
            "specialists_ineligible": [],
            "specialist_registry_projection": None,
            "specialists_decision": {
                "decision": "selected",
                "selection_id": selection_id,
                "reason_code": "candidate-selected",
                "lifecycle_state": "selected",
                "prompted_user": False,
            },
            "specialists_phase_plan": [],
        },
    )

    assert isinstance(prepared, PreparedTransitionOperation)
    assert isinstance(prepared.command, RecordSpecialistRecommendation)
    assert prepared.effects == ()
    assert prepared.result == {}
    encoded = json.dumps(
        prepared.command.projection.decision.thaw(), sort_keys=True
    )
    for forbidden in ("passes", "score", "review", "terminal_outcome"):
        assert forbidden not in encoded


def test_recommendation_reducer_records_selection_and_derived_planning_binding_only():
    from mission_application.planning import prepare_specialist_recommendation
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import decide

    document = _handoff_document()
    document.update(
        {
            "complexity": "Complex",
            "planning_policy_version": 1,
            "planning_strategy": "core",
            "specialists_candidates": [],
            "specialists_selected": [],
            "specialists_unavailable": [],
            "specialists_ineligible": [],
            "specialists_phase_plan": [],
            "specialists_decision": None,
        }
    )
    state = decode_mission_state(json.dumps(document).encode("utf-8"))
    selection_id = "sel_" + "1" * 32
    provider = {
        "provider_id": "portable-provider",
        "skill": "portable-provider",
        "selection_id": selection_id,
        "planning_mode": "primary",
        "planning_contract_digest": _digest("c"),
    }
    prepared = prepare_specialist_recommendation(
        document,
        at="2030-01-01T00:00:01Z",
        expected_complexity="Complex",
        expected_iteration=2,
        result={
            "task_profile": {"primary": "backend", "secondary": [], "confidence": 1.0, "risk": "low", "signals": []},
            "specialists_candidates": [provider],
            "specialists_selected": [provider],
            "specialists_unavailable": [],
            "specialists_ineligible": [],
            "specialist_registry_projection": None,
            "specialists_decision": {
                "decision": "selected",
                "selection_id": selection_id,
                "reason_code": "candidate-selected",
                "lifecycle_state": "selected",
                "prompted_user": False,
            },
            "specialists_phase_plan": [],
        },
    )

    decision = decide(state, prepared.command)

    assert decision.accepted is True
    assert decision.events[0].type == "specialist-recommendation-recorded"
    assert decision.transition.effects == ()
    next_state = decision.transition.new_state
    selected = next_state.a4.specialist_selection
    assert selected.planning_strategy == "provider-primary"
    assert selected.planning_contract_digest == _digest("c")
    assert selected.planning_provider_binding.thaw() == {
        "provider_id": "portable-provider",
        "selection_id": selection_id,
        "planning_contract_digest": _digest("c"),
    }
    assert next_state.control == state.control
    assert next_state.scores == state.scores


def test_recommendation_policy_fallback_and_legacy_policy_preserve_contract():
    from mission_application.planning import prepare_specialist_recommendation
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import decide

    def recommendation(document):
        return prepare_specialist_recommendation(
            document,
            at="2030-01-01T00:00:01Z",
            expected_complexity="Complex",
            expected_iteration=2,
            result={
                "task_profile": {"primary": "backend", "secondary": [], "confidence": 1.0, "risk": "low", "signals": []},
                "specialists_candidates": [],
                "specialists_selected": [],
                "specialists_unavailable": [],
                "specialists_ineligible": [],
                "specialist_registry_projection": None,
                "specialists_decision": {
                    "decision": "unavailable",
                    "selection_id": "sel_" + "1" * 32,
                    "reason_code": "no-candidate",
                    "lifecycle_state": "terminal",
                    "prompted_user": False,
                },
                "specialists_phase_plan": [],
            },
        )

    binding = {
        "provider_id": "portable-provider",
        "selection_id": "sel_" + "0" * 32,
        "planning_contract_digest": _digest("c"),
    }
    policy_v1 = _handoff_document()
    policy_v1.update(
        {
            "complexity": "Complex",
            "planning_policy_version": 1,
            "planning_strategy": "provider-primary",
            "planning_contract_digest": _digest("c"),
            "planning_provider_binding": binding,
        }
    )
    policy_v1_state = decode_mission_state(json.dumps(policy_v1).encode("utf-8"))
    fallback = decide(policy_v1_state, recommendation(policy_v1).command)
    assert fallback.accepted is True
    fallback_selection = fallback.transition.new_state.a4.specialist_selection
    assert fallback_selection.planning_strategy == "core"
    assert fallback_selection.planning_provider_binding is None
    assert fallback_selection.planning_contract_digest == _digest("c")

    legacy = dict(policy_v1)
    legacy.pop("planning_policy_version")
    legacy_state = decode_mission_state(json.dumps(legacy).encode("utf-8"))
    preserved = decide(legacy_state, recommendation(legacy).command)
    assert preserved.accepted is True
    preserved_selection = preserved.transition.new_state.a4.specialist_selection
    assert preserved_selection.planning_strategy == "provider-primary"
    assert preserved_selection.planning_contract_digest == _digest("c")
    assert preserved_selection.planning_provider_binding.thaw() == binding


def test_recommendation_kernel_rejects_toctou_active_invocation_and_provider_authority():
    from dataclasses import replace

    from mission_application.planning import prepare_specialist_recommendation
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordSpecialistRecommendation
    from mission_kernel.transitions import decide

    selection_id = "sel_" + "1" * 32
    provider = {
        "skill": "portable-provider",
        "selection_id": selection_id,
    }
    result = {
        "task_profile": {"primary": "backend", "secondary": [], "confidence": 1.0, "risk": "low", "signals": []},
        "specialists_candidates": [provider],
        "specialists_selected": [provider],
        "specialists_unavailable": [],
        "specialists_ineligible": [],
        "specialist_registry_projection": None,
        "specialists_decision": {
            "decision": "selected",
            "selection_id": selection_id,
            "reason_code": "candidate-selected",
            "lifecycle_state": "selected",
            "prompted_user": False,
        },
        "specialists_phase_plan": [],
    }
    document = _handoff_document()
    document["complexity"] = "Complex"
    state = decode_mission_state(json.dumps(document).encode("utf-8"))
    prepared = prepare_specialist_recommendation(
        document,
        at="2030-01-01T00:00:01Z",
        expected_complexity="Complex",
        expected_iteration=2,
        result=result,
    )

    mismatch = replace(prepared.command, expected_complexity="Critical")
    assert decide(state, mismatch).rejection.code == (
        "specialist-recommendation-context-mismatch"
    )

    active_document = dict(document)
    active_document["specialist_invocations"] = [
        {"invocation_id": "inv_" + "2" * 32, "status": "running"}
    ]
    active_state = decode_mission_state(
        json.dumps(active_document).encode("utf-8")
    )
    assert decide(active_state, prepared.command).rejection.code == (
        "provider-invocation-active"
    )

    from mission_kernel.a4 import SpecialistRecommendationProjection
    from mission_kernel.json_codec import freeze_json_value

    forged_provider = freeze_json_value({**provider, "passes": True})
    forged_projection = SpecialistRecommendationProjection(
        task_profile=prepared.command.projection.task_profile,
        candidates=(forged_provider,),
        selected=(forged_provider,),
        unavailable=(),
        ineligible=(),
        registry_projection=None,
        decision=prepared.command.projection.decision,
        phase_plan=(),
        mode="auto",
    )
    forged = replace(prepared.command, projection=forged_projection)
    forged_decision = decide(state, forged)
    assert isinstance(forged, RecordSpecialistRecommendation)
    assert forged_decision.accepted is False
    assert forged_decision.rejection.code == (
        "specialist-recommendation-authority-invalid"
    )


def test_recommendation_kernel_revalidates_direct_typed_public_projection():
    from dataclasses import replace

    from mission_application.planning import prepare_specialist_recommendation
    from mission_kernel import decode_mission_state
    from mission_kernel.a4 import SpecialistRecommendationProjection
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide
    from mission_persistence.legacy_v4 import LegacyV4Repository

    document = _handoff_document()
    document["complexity"] = "Complex"
    state = decode_mission_state(json.dumps(document).encode("utf-8"))
    selection_id = "sel_" + "1" * 32
    provider = {
        "skill": "portable-provider",
        "selection_id": selection_id,
    }
    prepared = prepare_specialist_recommendation(
        document,
        at="2030-01-01T00:00:01Z",
        expected_complexity="Complex",
        expected_iteration=2,
        result={
            "task_profile": {"primary": "backend", "secondary": [], "confidence": 1.0, "risk": "low", "signals": []},
            "specialists_candidates": [provider],
            "specialists_selected": [provider],
            "specialists_unavailable": [],
            "specialists_ineligible": [],
            "specialist_registry_projection": None,
            "specialists_decision": {
                "decision": "selected",
                "selection_id": selection_id,
                "reason_code": "candidate-selected",
                "lifecycle_state": "selected",
                "prompted_user": False,
            },
            "specialists_phase_plan": [],
        },
    )
    private = freeze_json_value(
        {**provider, "unexpected_private_field": "provider stdout"}
    )
    forged_projection = SpecialistRecommendationProjection(
        task_profile=prepared.command.projection.task_profile,
        candidates=(private,),
        selected=(private,),
        unavailable=(),
        ineligible=(),
        registry_projection=None,
        decision=prepared.command.projection.decision,
        phase_plan=(),
        mode="auto",
    )

    rejected = decide(
        state,
        replace(prepared.command, projection=forged_projection),
    )

    assert rejected.accepted is False
    assert rejected.transition is None
    assert rejected.rejection.code == "specialist-selection-invalid"

    forged_task_profile = replace(
        prepared.command.projection,
        task_profile=freeze_json_value(
            {
                "primary": "/private/local-provider",
                "secondary": ["portable-provider"],
                "confidence": 1.0,
                "risk": "low",
                "signals": [],
            }
        ),
    )
    task_profile_rejected = decide(
        state, replace(prepared.command, projection=forged_task_profile)
    )
    assert task_profile_rejected.accepted is False
    assert task_profile_rejected.rejection.code == "specialist-task-profile-invalid"

    invalid_lifecycle_decision = freeze_json_value(
        {
            **prepared.command.projection.decision.thaw(),
            "lifecycle_state": "terminal",
        }
    )
    lifecycle_projection = replace(
        prepared.command.projection,
        decision=invalid_lifecycle_decision,
    )
    lifecycle_rejection = decide(
        state,
        replace(prepared.command, projection=lifecycle_projection),
    )
    assert lifecycle_rejection.accepted is False
    assert lifecycle_rejection.rejection.code == "specialist-selection-invalid"

    plain_record_projection = replace(
        prepared.command.projection,
        candidates=({**provider},),
        selected=({**provider},),
    )
    plain_record_command = replace(
        prepared.command,
        projection=plain_record_projection,
    )
    plain_record_rejection = decide(state, plain_record_command)
    assert plain_record_rejection.accepted is False
    assert plain_record_rejection.transition is None
    assert plain_record_rejection.rejection.code == (
        "specialist-recommendation-invalid"
    )

    forged_mode_command = replace(
        prepared.command,
        projection=replace(
            prepared.command.projection,
            mode="forged-mode",
        ),
    )
    forged_mode_rejection = decide(state, forged_mode_command)
    assert forged_mode_rejection.accepted is False
    assert forged_mode_rejection.transition is None
    assert forged_mode_rejection.rejection.code == (
        "specialist-recommendation-invalid"
    )

    invalid_sequence_commands = (
        replace(
            prepared.command,
            projection=replace(
                prepared.command.projection,
                candidates=None,
            ),
        ),
        replace(
            prepared.command,
            projection=replace(
                prepared.command.projection,
                phase_plan=None,
            ),
        ),
    )
    for invalid_sequence_command in invalid_sequence_commands:
        invalid_sequence_rejection = decide(state, invalid_sequence_command)
        assert invalid_sequence_rejection.accepted is False
        assert invalid_sequence_rejection.transition is None
        assert invalid_sequence_rejection.rejection.code == (
            "specialist-recommendation-invalid"
        )

    numeric_complexity_document = dict(document)
    numeric_complexity_document["complexity"] = 42
    numeric_complexity_state = decode_mission_state(
        json.dumps(numeric_complexity_document).encode("utf-8")
    )
    numeric_complexity_rejection = decide(
        numeric_complexity_state,
        replace(prepared.command, expected_complexity=42),
    )
    assert numeric_complexity_rejection.accepted is False
    assert numeric_complexity_rejection.transition is None
    assert numeric_complexity_rejection.rejection.code == (
        "specialist-recommendation-invalid"
    )

    saved = []

    @contextmanager
    def lock():
        yield

    repository = LegacyV4Repository(
        lock=lock,
        read_state=lambda: document,
        write_state=lambda projection: saved.append(projection),
        backup_state=lambda: None,
    )
    forged_prepared = replace(
        prepared,
        command=replace(prepared.command, projection=forged_projection),
    )
    _prepared, execution = repository.execute_transition_effects(
        lambda _state: forged_prepared
    )
    assert execution.decision.accepted is False
    assert saved == []
    for invalid_command in (
        plain_record_command,
        forged_mode_command,
        *invalid_sequence_commands,
    ):
        invalid_prepared = replace(prepared, command=invalid_command)
        _prepared, invalid_execution = repository.execute_transition_effects(
            lambda _state, value=invalid_prepared: value
        )
        assert invalid_execution.decision.accepted is False
        assert saved == []


def test_recommendation_cli_adapter_has_no_direct_session_projection_writes():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_specialists"
    )
    owned = {
        "task_profile",
        "specialists_candidates",
        "specialists_selected",
        "specialists_unavailable",
        "specialists_ineligible",
        "specialist_registry_projection",
        "specialists_decision",
        "specialists_phase_plan",
        "planning_strategy",
        "planning_contract_digest",
        "planning_provider_binding",
        "specialists_mode",
        "updated_at",
    }
    violations = []
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "save":
                violations.append((node.lineno, "repository.save"))
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in owned
                ):
                    violations.append((node.lineno, target.slice.value))
    assert violations == []


def test_v5_extensions_round_trip_the_same_closed_a4_projection():
    from mission_kernel import decode_snapshot, encode_v5_snapshot

    from .mission_state_fixture_corpus import (
        canonical_json_bytes,
        current_v5_open_state,
    )

    payload = current_v5_open_state()
    selection_id = "sel_" + "1" * 32
    payload["extensions"].update(
        {
            "task_profile": {"primary": "backend", "secondary": [], "confidence": 1.0, "risk": "low", "signals": []},
            "specialists_candidates": [
                {"skill": "portable-provider", "selection_id": selection_id}
            ],
            "specialists_selected": [
                {"skill": "portable-provider", "selection_id": selection_id}
            ],
            "specialists_unavailable": [],
            "specialists_ineligible": [],
            "specialists_decision": {
                "decision": "selected",
                "selection_id": selection_id,
                "reason_code": "candidate-selected",
                "lifecycle_state": "selected",
            },
            "specialists_phase_plan": [],
            "specialists_mode": "auto",
            "specialist_invocations": [
                {
                    "invocation_id": "inv_" + "2" * 32,
                    "status": "reserved",
                }
            ],
        }
    )

    snapshot = decode_snapshot(canonical_json_bytes(payload))

    assert snapshot.state.a4.specialist_selection.active_provider_invocation_ids == (
        "inv_" + "2" * 32,
    )
    assert encode_v5_snapshot(snapshot) == canonical_json_bytes(payload)


def test_recommend_dry_run_does_not_create_or_mutate_session_state(run_cli, tmp_path):
    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Inspect a portable backend implementation",
        "--installed-skills",
        "backend-provider",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / ".mission-state").exists()


def test_handoff_rejects_duplicate_begin_duplicate_step_and_incomplete_complete():
    """設計書の必須 reject パス 3 件を kernel 遷移で直接固定する（Checker M-1）。

    旧 `decide_executor_handoff` 経路のテストは kernel 化後の production パスに
    到達しないため、遷移関数そのものへの回帰テストとして置く。
    """
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import (
        BeginExecutorHandoff,
        CompleteExecutorHandoff,
        RecordExecutorStep,
    )
    from mission_kernel.transitions import decide

    state = decode_mission_state(json.dumps(_handoff_document()).encode("utf-8"))
    plan = _plan_observation()

    begun = decide(state, BeginExecutorHandoff("2030-01-01T00:00:01Z", plan))
    assert begun.accepted is True
    consuming = begun.transition.new_state

    duplicate_begin = decide(
        consuming, BeginExecutorHandoff("2030-01-01T00:00:02Z", plan)
    )
    assert duplicate_begin.accepted is False
    assert duplicate_begin.rejection.code == "executor-handoff-not-prepared"

    first = decide(
        consuming, RecordExecutorStep("2030-01-01T00:00:03Z", "step-1", "ok", plan)
    )
    assert first.accepted is True
    one_step = first.transition.new_state

    duplicate_step = decide(
        one_step, RecordExecutorStep("2030-01-01T00:00:04Z", "step-1", "ok", plan)
    )
    assert duplicate_step.accepted is False
    assert duplicate_step.rejection.code == "executor-step-already-recorded"

    incomplete = decide(
        one_step, CompleteExecutorHandoff("2030-01-01T00:00:05Z", plan)
    )
    assert incomplete.accepted is False
    assert incomplete.rejection.code == "executor-handoff-incomplete"
