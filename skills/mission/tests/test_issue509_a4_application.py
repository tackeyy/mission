"""A4 plan, handoff, and provider-evidence use-case contracts."""

from __future__ import annotations

import pytest
from pathlib import Path
import importlib.util


def _digest(char: str = "a") -> str:
    return "sha256:" + char * 64


def _plan(**changes):
    value = {
        "schema": "mission-plan/1",
        "path": ".mission-state/plans/plan.json",
        "digest": _digest("a"),
        "source": "provider",
        "source_id": "inv_" + "1" * 32,
        "source_digest": _digest("b"),
        "selection_source": "automatic",
        "iteration": 2,
        "generation": 4,
        "validated_at": "2026-08-17T00:00:00Z",
    }
    value.update(changes)
    return value


def _handoff(**changes):
    value = {
        "schema": "mission-executor-handoff/1",
        "handoff_id": "handoff_" + "a" * 32,
        "plan_path": ".mission-state/plans/plan.json",
        "plan_digest": _digest("a"),
        "plan_generation": 4,
        "plan_source": "provider",
        "source_id": "inv_" + "1" * 32,
        "selection_source": "automatic",
        "iteration": 2,
        "step_ids": ["step-2", "step-4"],
        "status": "prepared",
    }
    value.update(changes)
    return value


def _intent(**changes):
    value = {
        "invocation_id": "inv_" + "1" * 32,
        "operation_id": "op-2",
        "outbound_packet_digest": _digest("c"),
        "iteration": 2,
        "fencing_epoch": 4,
    }
    value.update(changes)
    return value


def test_closed_plan_binding_includes_generation_digest_source_selection_invocation_and_iteration():
    from mission_application.planning import typed_plan_binding

    binding = typed_plan_binding(_plan())

    assert binding.generation == 4
    assert binding.digest == _digest("a")
    assert binding.source == "provider"
    assert binding.selection_source == "automatic"
    assert binding.invocation_id == "inv_" + "1" * 32
    assert binding.iteration == 2


def test_plan_binding_rejects_unknown_field():
    from mission_application.planning import PlanningFailure, typed_plan_binding

    with pytest.raises(PlanningFailure, match="plan-binding-invalid"):
        typed_plan_binding(_plan(authority="provider"))


def test_plan_binding_rejects_bool_as_int_generation():
    from mission_application.planning import PlanningFailure, typed_plan_binding

    with pytest.raises(PlanningFailure, match="plan-binding-invalid"):
        typed_plan_binding(_plan(generation=True))


def test_plan_binding_rejects_empty_source_id():
    from mission_application.planning import PlanningFailure, typed_plan_binding

    with pytest.raises(PlanningFailure, match="plan-binding-invalid"):
        typed_plan_binding(_plan(source_id=""))


def test_plan_binding_rejects_malformed_provider_invocation_id():
    from mission_application.planning import PlanningFailure, typed_plan_binding

    with pytest.raises(PlanningFailure, match="plan-binding-invalid"):
        typed_plan_binding(_plan(source_id="provider-not-an-invocation"))


def test_handoff_rejects_closed_binding_drift_before_phase_advance():
    from mission_application.planning import PlanningFailure, typed_handoff

    with pytest.raises(PlanningFailure, match="handoff-plan-drift"):
        typed_handoff(_handoff(plan_generation=6), _plan())


def test_handoff_rejects_unknown_key():
    from mission_application.planning import PlanningFailure, typed_handoff

    with pytest.raises(PlanningFailure, match="handoff-binding-invalid"):
        typed_handoff(_handoff(provider_decides_phase=True), _plan())


def test_handoff_rejects_bool_as_iteration():
    from mission_application.planning import PlanningFailure, typed_handoff

    with pytest.raises(PlanningFailure, match="handoff-binding-invalid"):
        typed_handoff(_handoff(iteration=False), _plan())


def test_handoff_rejects_empty_handoff_id():
    from mission_application.planning import PlanningFailure, typed_handoff

    with pytest.raises(PlanningFailure, match="handoff-binding-invalid"):
        typed_handoff(_handoff(handoff_id=""), _plan())


def test_legacy_handoff_binding_rejects_selection_drift():
    from mission_application.planning import PlanningFailure, verify_handoff_binding

    with pytest.raises(PlanningFailure, match="handoff-plan-drift"):
        verify_handoff_binding(
            _handoff(),
            plan_path=".mission-state/plans/plan.json", plan_digest=_digest("a"),
            plan_generation=4, plan_source="provider", source_id="inv_" + "1" * 32,
            selection_source="confirmed-user", iteration=2, step_ids=["step-2", "step-4"],
        )


def test_lease_failure_does_not_publish_plan_or_mutate_state():
    from mission_application.planning import PlanningFailure, commit_plan_evidence

    calls = []
    with pytest.raises(PlanningFailure, match="lease-rejected"):
        commit_plan_evidence(
            state={"canonical_plan": None},
            plan=_plan(),
            lease_verified=False,
            publish=lambda _plan: calls.append("published"),
        )
    assert calls == []


def test_publication_failure_does_not_return_authoritative_plan():
    from mission_application.planning import PlanningFailure, commit_plan_evidence

    state = {"canonical_plan": None}
    with pytest.raises(PlanningFailure, match="plan-publication-failed"):
        commit_plan_evidence(
            state=state,
            plan=_plan(),
            lease_verified=True,
            publish=lambda _plan: (_ for _ in ()).throw(OSError("publish")),
        )
    assert state["canonical_plan"] is None


def test_crash_before_dispatch_intent_leaves_no_invocation_record():
    from mission_application.planning import provider_saga_state

    assert provider_saga_state([], "inv_" + "1" * 32) is None


def test_crash_after_intent_before_spawn_is_dispatch_unknown():
    from mission_application.planning import record_dispatch_intent

    invocation = record_dispatch_intent([], _intent())

    assert invocation["status"] == "dispatch-unknown"


def test_crash_after_spawn_before_receipt_never_redispatches():
    from mission_application.planning import reconcile_dispatch_unknown, record_dispatch_intent

    invocations = [record_dispatch_intent([], _intent())]
    result = reconcile_dispatch_unknown(invocations, _intent(), observed_receipt=None)

    assert result["status"] == "abandoned-unknown"
    assert result["redispatch"] is False


def test_crash_after_receipt_before_terminal_is_running_only_with_exact_receipt():
    from mission_application.planning import record_dispatch_intent, record_provider_receipt

    invocations = [record_dispatch_intent([], _intent())]
    result = record_provider_receipt(
        invocations, _intent(), {"kind": "process", "identity": "pid:42"}
    )

    assert result["status"] == "running"


def test_receiptless_reconciliation_only_abandons_unknown():
    from mission_application.planning import PlanningFailure, reconcile_dispatch_unknown, record_dispatch_intent

    invocations = [record_dispatch_intent([], _intent())]
    with pytest.raises(PlanningFailure, match="receipt-required"):
        reconcile_dispatch_unknown(invocations, _intent(), observed_receipt={})


def test_stale_fencing_epoch_reconciliation_is_rejected():
    from mission_application.planning import PlanningFailure, reconcile_dispatch_unknown, record_dispatch_intent

    invocations = [record_dispatch_intent([], _intent(fencing_epoch=6))]
    with pytest.raises(PlanningFailure, match="stale-fencing-epoch"):
        reconcile_dispatch_unknown(invocations, _intent(fencing_epoch=4), observed_receipt=None)


def test_replayed_dispatch_intent_is_rejected():
    from mission_application.planning import PlanningFailure, record_dispatch_intent

    invocations = [record_dispatch_intent([], _intent())]
    with pytest.raises(PlanningFailure, match="dispatch-replay"):
        record_dispatch_intent(invocations, _intent())


def test_dispatch_intent_rejects_malformed_invocation_id():
    from mission_application.planning import PlanningFailure, record_dispatch_intent

    with pytest.raises(PlanningFailure, match="dispatch-intent-invalid"):
        record_dispatch_intent([], _intent(invocation_id="not-an-invocation"))


def test_receipt_replay_or_identity_mismatch_leaves_original_unknown_record_unchanged():
    from mission_application.planning import PlanningFailure, record_dispatch_intent, record_provider_receipt

    original = record_dispatch_intent([], _intent())
    with pytest.raises(PlanningFailure, match="receipt-intent-mismatch"):
        record_provider_receipt([original], _intent(operation_id="op-4"), {"kind": "process", "identity": "pid:2"})
    assert original["status"] == "dispatch-unknown"
    with pytest.raises(PlanningFailure, match="receipt-replay"):
        record_provider_receipt([{**original, "status": "running"}], _intent(), {"kind": "process", "identity": "pid:2"})
    assert original["status"] == "dispatch-unknown"


def test_exit_zero_with_invalid_result_remains_unvalidated_evidence():
    from mission_application.planning import classify_provider_result

    assert classify_provider_result(exit_code=0, result_validated=False) == "unvalidated-evidence"


def test_application_rejects_closed_receipt_bad_kind_without_mutating_unknown_record():
    from mission_application.planning import PlanningFailure, record_dispatch_intent, record_provider_receipt

    original = record_dispatch_intent([], _intent())
    with pytest.raises(PlanningFailure, match="receipt-invalid"):
        record_provider_receipt([original], _intent(), {"kind": "other", "identity": "provider:1"})
    assert original == record_dispatch_intent([], _intent())


def test_application_rejects_pathlike_control_and_whitespace_receipt_identity():
    from mission_application.planning import PlanningFailure, record_dispatch_intent, record_provider_receipt

    for identity in (
        "",
        "  ",
        "provider\nidentity",
        "/private/identity",
        "../private/socket",
        "relative/socket",
        "opaque:/Users/<user>/secret",
        "receipt file:/Users/<user>/secret",
        "opaque:C:\\Users\\<user>\\secret",
        "opaque:~/private",
        "x" * 257,
    ):
        original = record_dispatch_intent([], _intent())
        with pytest.raises(PlanningFailure, match="receipt-invalid"):
            record_provider_receipt([original], _intent(), {"kind": "provider", "identity": identity})
        assert original["status"] == "dispatch-unknown"


def test_application_rejects_zero_and_bool_fencing_epoch_without_mutation():
    from mission_application.planning import PlanningFailure, record_dispatch_intent

    for fencing_epoch in (0, False, True):
        with pytest.raises(PlanningFailure, match="dispatch-intent-invalid"):
            record_dispatch_intent([], _intent(fencing_epoch=fencing_epoch))


def test_public_consumer_accepts_application_receipt_and_rejects_noncanonical_identity():
    from mission_application.planning import record_dispatch_intent, record_provider_receipt
    from provider_public_contract import SpecialistPublicContractError, validate_specialist_public_state

    unknown = record_dispatch_intent([], _intent())
    accepted = record_provider_receipt(
        [unknown], _intent(), {"kind": "provider", "identity": "receipt:opaque-1"}
    )
    public_record = {
        **accepted,
        "phase": "planning", "role": "planner", "skill": "portable-provider",
        "mode": "command-provider", "timestamp": "2026-08-17T00:00:00Z",
    }
    validate_specialist_public_state({"specialist_invocations": [public_record]})
    public_record["provider_receipt"] = {"kind": "provider", "identity": "/private/identity"}
    with pytest.raises(SpecialistPublicContractError, match="unsafe-legacy-specialist-record"):
        validate_specialist_public_state({"specialist_invocations": [public_record]})


def test_public_consumer_rejects_zero_and_bool_fencing_epoch():
    from provider_public_contract import SpecialistPublicContractError, validate_specialist_public_state

    base = {
        "invocation_id": "inv_" + "1" * 32, "operation_id": "op-2",
        "outbound_packet_digest": _digest("c"), "iteration": 2, "phase": "planning",
        "role": "planner", "skill": "portable-provider", "mode": "command-provider",
        "status": "dispatch-unknown", "lifecycle_state": "dispatch-unknown",
        "timestamp": "2026-08-17T00:00:00Z",
    }
    for fencing_epoch in (0, False, True):
        with pytest.raises(SpecialistPublicContractError, match="unsafe-legacy-specialist-record"):
            validate_specialist_public_state({"specialist_invocations": [{**base, "fencing_epoch": fencing_epoch}]})


def test_handoff_rejects_bool_equal_plan_generation_before_drift_comparison_without_mutation():
    from mission_application.planning import PlanningFailure, verify_handoff_binding

    original = _handoff(plan_generation=True)
    with pytest.raises(PlanningFailure, match="handoff-binding-invalid"):
        verify_handoff_binding(
            original, plan_path=".mission-state/plans/plan.json", plan_digest=_digest("a"),
            plan_generation=1, plan_source="provider", source_id="inv_" + "1" * 32,
            selection_source="automatic", iteration=2, step_ids=["step-2", "step-4"],
        )
    assert original["plan_generation"] is True


def test_handoff_rejects_bool_equal_decision_bindings_before_completion():
    from mission_application.planning import (
        ExecutorHandoffFacts,
        PlanningFailure,
        decide_executor_handoff,
    )

    original = _handoff(
        status="consuming",
        begun_at="2026-08-17T00:00:00Z",
        plan_generation=1,
        step_ids=["step-2"],
    )
    facts = ExecutorHandoffFacts(
        plan_path=".mission-state/plans/plan.json",
        plan_digest=_digest("a"),
        plan_generation=1,
        plan_source="provider",
        source_id="inv_" + "1" * 32,
        selection_source="automatic",
        iteration=2,
        step_ids=("step-2",),
        dependencies={"step-2": ()},
        decision_iteration=1,
    )
    forged = [{
        "handoff_id": original["handoff_id"],
        "plan_digest": _digest("a"),
        "plan_generation": True,
        "plan_source": "provider",
        "source_id": "inv_" + "1" * 32,
        "selection_source": "automatic",
        "iteration": True,
        "step_id": "step-2",
        "result": "ok",
    }]

    with pytest.raises(PlanningFailure, match="executor-handoff-decisions-invalid"):
        decide_executor_handoff(original, forged, _handoff_request("complete"), facts)
    assert original["status"] == "consuming"


def test_a4_terminal_decision_preserves_exit_zero_invalid_evidence():
    from mission_application.planning import PlanningFailure, decide_provider_terminal_result

    decision = decide_provider_terminal_result(
        exit_code=0, evidence_status="unvalidated-evidence", reason="invalid structured result"
    )
    assert decision.status == "unvalidated-evidence"
    assert decision.reason == "invalid structured result"

    awaiting = decide_provider_terminal_result(
        exit_code=7, evidence_status="awaiting-input", reason="approval required"
    )
    assert awaiting.status == "awaiting-input"

    with pytest.raises(PlanningFailure, match="provider-result-invalid"):
        decide_provider_terminal_result(exit_code=0, evidence_status="failed")
    with pytest.raises(PlanningFailure, match="provider-result-invalid"):
        decide_provider_terminal_result(exit_code=7, evidence_status="completed")


def test_strict_backend_uses_the_same_closed_receipt_validator(monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_issue509_strict_receipt", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    class Entry:
        name = "strict-backend"
        value = "portable.backend:run"

        @staticmethod
        def load():
            return object()

    class Entries:
        @staticmethod
        def select(**_kwargs):
            return [Entry()]

    monkeypatch.setattr(module.importlib.metadata, "entry_points", lambda: Entries())
    monkeypatch.setattr(
        module, "dispatch_prepared_packet",
        lambda *_args, **_kwargs: {"returncode": 0, "receipt": {"kind": "provider", "identity": "bad\nreceipt"}},
    )
    descriptor = {
        "entry_point": "strict-backend", "entry_point_value": "portable.backend:run",
        "attestation": {"policy_digest": _digest("d")},
    }
    with pytest.raises(module.ProviderPreflightError, match="strict-receipt-invalid"):
        module._run_strict_provider_backend(descriptor, b"{}")


def test_cli_call_path_uses_a4_dispatch_and_terminal_decisions():
    source = (Path(__file__).resolve().parents[1] / "bin" / "mission-state.py").read_text(encoding="utf-8")

    assert "intent_decision = record_dispatch_intent(" in source
    assert "entry = {**current_entry, **intent_decision," in source
    assert "terminal = decide_provider_terminal_result(" in source
    assert "status, reason = terminal.status, terminal.reason" in source


def test_provider_plan_import_does_not_expose_an_injectable_parser():
    from mission_application.planning import validate_provider_plan_import

    with pytest.raises(TypeError):
        validate_provider_plan_import(
            b"{}",
            expected_binding={},
            result_contract={},
            workspace=".",
            parser=lambda *_args, **_kwargs: {
                "document": {"passes": True},
                "raw_result_digest": _digest("f"),
            },
        )


def test_proven_popen_failure_can_terminalize_unknown_without_claiming_running():
    from specialist_lifecycle import validate_invocation_transition

    existing = {
        "invocation_id": "inv_" + "1" * 32,
        "selection_id": "sel_" + "2" * 32,
        "iteration": 2,
        "phase": "planning",
        "role": "planning-provider",
        "skill": "provider",
        "mode": "command-provider",
        "status": "dispatch-unknown",
        "lifecycle_state": "dispatch-unknown",
    }
    requested = {
        **existing,
        "status": "failed-before-start",
        "lifecycle_state": "terminal",
        "proven_no_dispatch": True,
    }

    validate_invocation_transition(existing, requested)


def _handoff_facts():
    from mission_application.planning import ExecutorHandoffFacts
    return ExecutorHandoffFacts(
        plan_path=".mission-state/plans/plan.json", plan_digest=_digest("a"),
        plan_generation=4, plan_source="provider", source_id="inv_" + "1" * 32,
        selection_source="automatic", iteration=2, step_ids=("step-2", "step-4"),
        dependencies={"step-2": (), "step-4": ("step-2",)}, decision_iteration=2,
    )


def _handoff_request(operation, **changes):
    from mission_application.planning import ExecutorHandoffRequest
    value = {"operation": operation, "at": "2026-08-17T00:00:00Z", "step_id": None, "result": None}
    value.update(changes)
    return ExecutorHandoffRequest(**value)


def test_handoff_begin_returns_new_closed_state_without_mutating_input():
    from mission_application.planning import decide_executor_handoff
    original = _handoff()
    result = decide_executor_handoff(original, [], _handoff_request("begin"), _handoff_facts())
    assert original["status"] == "prepared"
    assert result.handoff["status"] == "consuming"


def test_handoff_verify_rejects_nonexistent_step_without_mutating_input():
    from mission_application.planning import PlanningFailure, decide_executor_handoff
    original = _handoff()
    with pytest.raises(PlanningFailure, match="executor-step-not-member"):
        decide_executor_handoff(original, [], _handoff_request("verify", step_id="other"), _handoff_facts())
    assert original == _handoff()


def test_handoff_record_rejects_dependency_and_replay_without_mutating_input():
    from mission_application.planning import PlanningFailure, decide_executor_handoff
    original = _handoff(status="consuming", begun_at="2026-08-17T00:00:00Z")
    with pytest.raises(PlanningFailure, match="executor-step-dependency-incomplete"):
        decide_executor_handoff(original, [], _handoff_request("record", step_id="step-4", result="ok"), _handoff_facts())
    first = decide_executor_handoff(original, [], _handoff_request("record", step_id="step-2", result="ok"), _handoff_facts())
    with pytest.raises(PlanningFailure, match="executor-step-already-recorded"):
        decide_executor_handoff(original, [first.appended_decision], _handoff_request("record", step_id="step-2", result="ok"), _handoff_facts())
    assert original["status"] == "consuming"


def test_handoff_complete_rejects_incomplete_and_terminal_replay_without_mutation():
    from mission_application.planning import PlanningFailure, decide_executor_handoff
    original = _handoff(status="consuming", begun_at="2026-08-17T00:00:00Z")
    with pytest.raises(PlanningFailure, match="executor-handoff-steps-incomplete"):
        decide_executor_handoff(original, [], _handoff_request("complete"), _handoff_facts())
    first = decide_executor_handoff(
        original, [], _handoff_request("record", step_id="step-2", result="ok"), _handoff_facts()
    ).appended_decision
    second = decide_executor_handoff(
        original, [first], _handoff_request("record", step_id="step-4", result="ok"), _handoff_facts()
    ).appended_decision
    done = [first, second]
    completed = decide_executor_handoff(original, done, _handoff_request("complete"), _handoff_facts())
    with pytest.raises(PlanningFailure, match="executor-handoff-not-consuming"):
        decide_executor_handoff(completed.handoff, done, _handoff_request("complete"), _handoff_facts())


def test_handoff_complete_rejects_unclosed_or_duplicate_decision_evidence():
    from mission_application.planning import PlanningFailure, decide_executor_handoff

    original = _handoff(status="consuming", begun_at="2026-08-17T00:00:00Z")
    forged = [
        {"handoff_id": original["handoff_id"], "step_id": "step-2"},
        {"handoff_id": original["handoff_id"], "step_id": "step-4"},
    ]
    with pytest.raises(PlanningFailure, match="executor-handoff-decisions-invalid"):
        decide_executor_handoff(original, forged, _handoff_request("complete"), _handoff_facts())

    first = decide_executor_handoff(
        original, [], _handoff_request("record", step_id="step-2", result="ok"), _handoff_facts()
    ).appended_decision
    second = decide_executor_handoff(
        original, [first], _handoff_request("record", step_id="step-4", result="ok"), _handoff_facts()
    ).appended_decision
    with pytest.raises(PlanningFailure, match="executor-handoff-decisions-invalid"):
        decide_executor_handoff(
            original, [first, first, second], _handoff_request("complete"), _handoff_facts()
        )


def test_handoff_rejects_unknown_operation_and_extra_field_without_mutating_input():
    from mission_application.planning import PlanningFailure, decide_executor_handoff
    original = _handoff()
    with pytest.raises(PlanningFailure, match="executor-handoff-request-invalid"):
        decide_executor_handoff(original, [], _handoff_request("erase"), _handoff_facts())
    with pytest.raises(PlanningFailure, match="handoff-binding-invalid"):
        decide_executor_handoff(_handoff(extra=True), [], _handoff_request("begin"), _handoff_facts())
    assert original == _handoff()
