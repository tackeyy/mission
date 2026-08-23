"""Issue #632: transition claims become the pass/advance writer contract."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import sys
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import canonical_json_bytes, generate_cli_state_corpus


_REVIEW_STATE = None


def _review_state(tmp_path):
    global _REVIEW_STATE
    if _REVIEW_STATE is None:
        corpus = generate_cli_state_corpus(tmp_path.resolve())
        _REVIEW_STATE = __import__("json").loads(
            canonical_json_bytes(corpus["review_aggregate_and_bound_score"])
        )
    return copy.deepcopy(_REVIEW_STATE)


def _load_cli_module(name):
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _RecordingRepository:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.execute_calls = []
        self.saved = None

    def transaction(self):
        return contextlib.nullcontext()

    def load(self):
        return copy.deepcopy(self.state)

    def execute(self, state, mutation, transition=None, finalize=None):
        from mission_persistence.legacy_v4 import _apply_transition_claims

        self.execute_calls.append((state, mutation, transition, finalize))
        proposed = copy.deepcopy(state)
        mutation(proposed)
        if transition is not None:
            _apply_transition_claims(transition, proposed)
        if finalize is not None:
            finalize(proposed)
        return proposed

    def save(self, state, **_kwargs):
        self.saved = copy.deepcopy(state)


def _pass_services(cli):
    from mission_application.review import MarkPassServices

    return MarkPassServices(
        verify_force_approval=lambda _data: {},
        validate_force_terminal=lambda _data, _verification: None,
        validate_score_evidence=lambda _data, _latest: None,
        validate_artifact_gate=lambda _data: None,
        validate_specialist_gate=lambda _data, _waiver: None,
        transition_phase=cli._transition_phase,
        optional_unclosed_skills=lambda _data: [],
        selection_id=lambda _data: None,
    )


def test_mark_pass_persists_through_repository_execute(tmp_path):
    from mission_application.review import MarkPassRequest, mark_pass

    state = _review_state(tmp_path)
    # The corpus returns a decoded state for kernel tests; recover its exact
    # legacy document so the application use case receives the v4 boundary.
    repository = _RecordingRepository(state)
    cli = _load_cli_module("issue632_mark_pass_execute")

    result = mark_pass(
        repository,
        MarkPassRequest(
            force=False,
            reason=None,
            approved_by_user=False,
            specialist_waiver="",
            at="2030-08-23T00:00:00Z",
        ),
        _pass_services(cli),
    )

    assert len(repository.execute_calls) == 1
    assert repository.execute_calls[0][2] is result.decision.transition


def test_mark_pass_saved_document_is_unchanged(tmp_path):
    from mission_application.review import MarkPassRequest, mark_pass

    state = _review_state(tmp_path)
    repository = _RecordingRepository(state)
    result = mark_pass(
        repository,
        MarkPassRequest(False, None, False, "", "2030-08-23T00:00:00Z"),
        _pass_services(_load_cli_module("issue632_mark_pass_bytes")),
    )

    assert result.decision.accepted is True
    assert repository.saved["passes"] is True
    assert repository.saved["loop_active"] is False
    assert repository.saved["phase"] == "done"
    assert repository.saved["terminal_outcome"] == "completed_pass"


def test_mark_pass_force_path_preserves_approval_binding(tmp_path):
    from mission_application.review import MarkPassRequest, mark_pass

    state = _review_state(tmp_path)
    repository = _RecordingRepository(state)
    called = []
    services = _pass_services(_load_cli_module("issue632_force_binding"))
    verification = {"consumed": False, "request": {"terminal_object_digest": "test"}}
    services = services.__class__(
        verify_force_approval=lambda _data: verification,
        validate_force_terminal=lambda data, received: called.append((data, received)),
        validate_score_evidence=services.validate_score_evidence,
        validate_artifact_gate=services.validate_artifact_gate,
        validate_specialist_gate=services.validate_specialist_gate,
        transition_phase=services.transition_phase,
        optional_unclosed_skills=services.optional_unclosed_skills,
        selection_id=services.selection_id,
    )
    mark_pass(
        repository,
        MarkPassRequest(True, "approved", True, "", "2030-08-23T00:00:00Z"),
        services,
    )

    assert called and called[0][1] is verification
    assert repository.saved["force_approval"]["consumed"] is True


def test_mark_pass_validate_services_are_called_in_the_recorded_order(tmp_path):
    from mission_application.review import MarkPassRequest, MarkPassServices, mark_pass

    state = _review_state(tmp_path)
    repository = _RecordingRepository(state)
    cli = _load_cli_module("issue632_validate_order")
    calls = []
    services = MarkPassServices(
        verify_force_approval=lambda _data: calls.append("force") or {},
        validate_force_terminal=lambda _data, _verification: None,
        validate_artifact_gate=lambda _data: calls.append("artifact"),
        validate_score_evidence=lambda _data, _latest: calls.append("score"),
        validate_specialist_gate=lambda _data, _waiver: calls.append("specialist"),
        transition_phase=cli._transition_phase,
        optional_unclosed_skills=lambda _data: [],
        selection_id=lambda _data: None,
    )
    mark_pass(
        repository,
        MarkPassRequest(False, None, False, "", "2030-08-23T00:00:00Z"),
        services,
    )

    assert calls == ["artifact", "score", "specialist"]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda state: state.pop("score_history"), "score-required"),
        (lambda state: state.update({"passes": True}), "terminal-state"),
        # provenance を落とすと typed score が BoundScore ではなくなる
        (
            lambda state: state["score_history"][-1].pop("score_provenance", None),
            "authoritative-score-required",
        ),
        (lambda state: state["score_history"][-1].update({"open_high": 1}), "open-high-findings"),
        (lambda state: state["score_history"][-1].update({"composite": 3.0}), "composite-below-threshold"),
        (lambda state: state["score_history"][-1].update({"min_item": 3.0}), "minimum-item-below-threshold"),
        (lambda state: state["score_history"][-1].update({"agreement_detail": {"mission_achievement": {"min": 3.0, "max": 5.0, "delta": 2.0}, "accuracy": {"min": 3.0, "max": 5.0, "delta": 2.0}, "completeness": {"min": 3.0, "max": 5.0, "delta": 2.0}, "usability": {"min": 3.0, "max": 5.0, "delta": 2.0}}}), "review-agreement-too-low"),
    ],
)
def test_mark_pass_gate_rejections_are_unchanged(tmp_path, change, reason):
    from mission_application.review import MarkPassRequest, ReviewFailure, mark_pass

    state = _review_state(tmp_path)
    change(state)
    repository = _RecordingRepository(state)
    # Use the production terminal writer, which delegates outcome derivation to
    # mission_common, rather than a test stub.
    services = _pass_services(_load_cli_module("issue632_gate_rejections"))
    with pytest.raises(ReviewFailure) as raised:
        mark_pass(
            repository,
            MarkPassRequest(False, None, False, "", "2030-08-23T00:00:00Z"),
            services,
        )

    assert raised.value.reason == reason
    assert repository.saved is None


def test_mark_pass_artifact_and_force_approval_rejections_do_not_save(tmp_path):
    from mission_application.review import MarkPassRequest, ReviewFailure, mark_pass

    state = _review_state(tmp_path)
    repository = _RecordingRepository(state)
    services = _pass_services(_load_cli_module("issue632_gate_artifact"))
    services = services.__class__(
        verify_force_approval=services.verify_force_approval,
        validate_force_terminal=services.validate_force_terminal,
        validate_score_evidence=services.validate_score_evidence,
        validate_artifact_gate=lambda _data: (_ for _ in ()).throw(ValueError("artifact blocked")),
        validate_specialist_gate=services.validate_specialist_gate,
        transition_phase=services.transition_phase,
        optional_unclosed_skills=services.optional_unclosed_skills,
        selection_id=services.selection_id,
    )
    with pytest.raises(ReviewFailure) as raised:
        mark_pass(repository, MarkPassRequest(False, None, False, "", "2030-08-23T00:00:00Z"), services)
    assert raised.value.reason == "artifact-gate-unsatisfied"
    assert repository.saved is None


def test_mark_pass_on_v4_repository_removes_from_aggregate_after_save(tmp_path):
    from mission_application.review import MarkPassRequest, mark_pass
    from mission_persistence.legacy_v4 import LegacyV4Repository

    state = _review_state(tmp_path)
    events = []
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(state),
        write_state=lambda _state, **_kwargs: events.append("write_state"),
        backup_state=lambda: None,
        remove_from_aggregate=lambda: events.append("remove_from_aggregate"),
    )
    mark_pass(
        repository,
        MarkPassRequest(False, None, False, "", "2030-08-23T00:00:00Z"),
        _pass_services(_load_cli_module("issue632_v4_aggregate")),
    )
    assert events == ["write_state", "remove_from_aggregate"]


@pytest.mark.parametrize(
    ("source_phase", "target_phase", "policy"),
    [
        ("planning", "planning", 0),
        ("planning", "executing", 0),
        ("planning", "reviewing", 0),
        ("reviewing", "scoring", 0),
    ],
    ids=("same-phase", "legacy-executing", "skip-ahead", "reviewing-scoring"),
)
def test_advance_compatibility_success_paths_send_no_transition(
    tmp_path, source_phase, target_phase, policy
):
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, advance

    state = _review_state(tmp_path)
    state["phase"] = source_phase
    state["planning_policy_version"] = policy
    state["artifact_applicability"] = "not-applicable"
    repository = _RecordingRepository(state)
    cli = _load_cli_module("issue632_advance_compatibility")
    result = advance(
        repository,
        AdvanceRequest(target_phase, "active:work", "2030-08-23T00:00:00Z", None, None, None, None),
        AdvanceServices(
            reject_active_provider_mutation=lambda _state, _operation: None,
            prepare_handoff=lambda _state: None,
            capture_artifact=cli.capture_artifact_identity,
            transition_phase=cli._transition_phase,
        ),
    )
    assert result.decision is None
    assert repository.execute_calls[0][2] is None


def test_advance_sends_the_accepted_transition_to_execute(tmp_path):
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, advance

    state = _review_state(tmp_path)
    state["phase"] = "executing"
    state["artifact_applicability"] = "not-applicable"
    repository = _RecordingRepository(state)
    cli = _load_cli_module("issue632_advance_execute")

    result = advance(
        repository,
        AdvanceRequest(
            phase="reviewing",
            activity="active:review",
            at="2030-08-23T00:00:00Z",
            detail=None,
            artifact_applicability=None,
            artifact_path=None,
            producer_run_id=None,
        ),
        AdvanceServices(
            reject_active_provider_mutation=lambda _state, _operation: None,
            prepare_handoff=lambda _state: None,
            capture_artifact=cli.capture_artifact_identity,
            transition_phase=cli._transition_phase,
        ),
    )

    assert result.decision is not None and result.decision.accepted is True
    assert repository.execute_calls[0][2] is result.decision.transition


@pytest.mark.parametrize("phase", ("done", "halted"))
def test_advance_rejection_paths_are_unchanged_for_terminal_phase(tmp_path, phase):
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, LifecycleFailure, advance

    repository = _RecordingRepository(_review_state(tmp_path))
    cli = _load_cli_module("issue632_advance_terminal_rejection")
    with pytest.raises(LifecycleFailure) as raised:
        advance(
            repository,
            AdvanceRequest(phase, "active:work", "2030-08-23T00:00:00Z", None, None, None, None),
            AdvanceServices(lambda _state, _op: None, lambda _state: None, cli.capture_artifact_identity, cli._transition_phase),
        )
    assert raised.value.reason == "terminal-phase"


def test_advance_rejection_paths_are_unchanged_for_artifact_pending(tmp_path):
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, LifecycleFailure, advance

    state = _review_state(tmp_path)
    state.update({"phase": "executing", "artifact_applicability": "pending"})
    repository = _RecordingRepository(state)
    cli = _load_cli_module("issue632_advance_pending_rejection")
    with pytest.raises(LifecycleFailure) as raised:
        advance(
            repository,
            AdvanceRequest("reviewing", "active:review", "2030-08-23T00:00:00Z", None, None, None, None),
            AdvanceServices(lambda _state, _op: None, lambda _state: None, cli.capture_artifact_identity, cli._transition_phase),
        )
    assert raised.value.reason == "artifact-applicability-pending"


@pytest.mark.parametrize(
    "decision_error",
    [
        ValueError("typed decode failed"),
        __import__("mission_application.lifecycle", fromlist=["LifecycleFailure"]).LifecycleFailure(
            "prepared executor handoff is invalid", reason="invalid-prepared-handoff"
        ),
    ],
    ids=("typed-decode", "invalid-prepared-handoff"),
)
def test_advance_defers_non_lifecycle_decision_errors_until_after_mutation(
    tmp_path, monkeypatch, decision_error
):
    from mission_application import lifecycle
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, LifecycleFailure

    state = _review_state(tmp_path)
    state["phase"] = "executing"
    repository = _RecordingRepository(state)
    cli = _load_cli_module("issue632_deferred_decision")

    def raise_decision_error(*_args):
        raise decision_error

    monkeypatch.setattr(lifecycle, "_advance_decision", raise_decision_error)
    with pytest.raises(LifecycleFailure) as raised:
        lifecycle.advance(
            repository,
            AdvanceRequest(
                phase="reviewing",
                activity="active:review",
                at="2030-08-23T00:00:00Z",
                detail=None,
                artifact_applicability=None,
                artifact_path="unexpected",
                producer_run_id=None,
            ),
            AdvanceServices(
                reject_active_provider_mutation=lambda _state, _operation: None,
                prepare_handoff=lambda _state: None,
                capture_artifact=cli.capture_artifact_identity,
                transition_phase=cli._transition_phase,
            ),
        )

    assert raised.value.reason == "artifact-applicability-required"


# --- property: 全 transition 送付経路で「決定された claims == 保存値」 ---


def _in_memory_repository(state, *, saved):
    """Real LegacyV4Repository so claims are actually applied on the way out."""
    from mission_persistence.legacy_v4 import LegacyV4Repository

    return LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(state),
        write_state=lambda document, **_kwargs: saved.update(copy.deepcopy(document)),
        backup_state=lambda: None,
        add_to_aggregate=lambda: None,
        remove_from_aggregate=lambda: None,
    )


def _active_document(**overrides):
    document = {
        "schema_version": 4,
        "mission": "issue632 property mission",
        "phase": "executing",
        "iteration": 1,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2030-08-23T00:00:00Z",
    }
    document.update(overrides)
    return document


def _timing_transition_phase(proposed, phase, at, **_kwargs):
    proposed["phase"] = phase
    proposed["phase_started_at"] = at


def _path_mark_pass(tmp_path, saved):
    from mission_application.review import MarkPassRequest, mark_pass

    repository = _in_memory_repository(_review_state(tmp_path), saved=saved)
    result = mark_pass(
        repository,
        MarkPassRequest(False, None, False, "", "2030-08-23T01:00:00Z"),
        _pass_services(_load_cli_module("issue632_property_pass")),
    )
    return result.decision


def _path_mark_halt(_tmp_path, saved):
    from mission_application.lifecycle import (
        MarkHaltRequest,
        MarkHaltServices,
        mark_halt,
    )

    repository = _in_memory_repository(_active_document(), saved=saved)
    result = mark_halt(
        repository,
        MarkHaltRequest(
            reason="external dependency down",
            category="blocked-external",
            at="2030-08-23T01:00:00Z",
            set_terminal_phase=True,
        ),
        MarkHaltServices(
            reject_active_provider_mutation=lambda _state, _command: None,
            transition_phase=_timing_transition_phase,
            goal_dispatch_fields=lambda _state: {},
        ),
    )
    return result.decision


def _path_reactivate(_tmp_path, saved):
    from mission_application.lifecycle import ReactivateRequest, reactivate

    document = _active_document(
        phase="halted",
        loop_active=False,
        halt_reason="blocked externally",
        halt_category="blocked-external",
        terminal_outcome="blocked_external",
    )
    repository = _in_memory_repository(document, saved=saved)
    result = reactivate(
        repository,
        ReactivateRequest(
            approved_by_user=True,
            reason="unblocked by the provider",
            expected_category="blocked-external",
            phase="planning",
            at="2030-08-23T01:00:00Z",
        ),
    )
    return result.decision


def _path_resume_stale(_tmp_path, saved):
    from mission_application.lifecycle import (
        RefreshPidRequest,
        RefreshPidServices,
        refresh_pid,
    )

    document = _active_document(
        phase="halted",
        loop_active=False,
        halt_reason="stale: superseded checkpoint",
        halt_category="stale",
        terminal_outcome="stale_superseded",
        resume_target_phase="planning",
        pid=424242,
    )
    repository = _in_memory_repository(document, saved=saved)
    result = refresh_pid(
        repository,
        RefreshPidRequest(
            new_pid=424243,
            force=False,
            reactivate=True,
            at="2030-08-23T01:00:00Z",
        ),
        RefreshPidServices(
            lease_fields_present=lambda _state: False,
            pid_is_agent=lambda _pid: False,
            resume_phase_timing=lambda _state, _at: None,
        ),
    )
    return result.decision


def _path_permission_preflight(_tmp_path, saved):
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    repository = _in_memory_repository(_active_document(), saved=saved)
    result = record_permission_observation(
        repository,
        PermissionObservationRequest(
            probes=(PermissionProbe("state", "denied", "write-unavailable"),),
            observed_at="2030-08-23T01:00:00Z",
        ),
    )
    return result.decision


def _path_set_fields(_tmp_path, saved):
    from mission_application.lifecycle import (
        SetFieldsRequest,
        SetFieldsServices,
        set_fields,
    )

    cli = _load_cli_module("issue632_property_set")
    repository = _in_memory_repository(_active_document(), saved=saved)
    result = set_fields(
        repository,
        SetFieldsRequest(kvs=("custom_note=kept",), at="2030-08-23T01:00:00Z"),
        SetFieldsServices(
            frozen_fields=frozenset(cli.FROZEN_FIELDS),
            reject_active_provider_mutation=lambda _state, _command: None,
            normalize_phase=cli._normalize_set_phase_value,
            transition_phase=cli._transition_phase,
            ensure_phase_timing=lambda _state, _at: None,
            derive_review_tier=cli.derive_review_tier,
            derive_review_tier_decision=cli.derive_review_tier_decision,
            reviewer_count_by_tier=dict(cli.TIER_REVIEWER_COUNT),
            goal_dispatch_fields=cli._goal_dispatch_route_fields,
            goal_dispatch_guidance=lambda _dispatch, _prefix: "",
        ),
    )
    return result.decision


def _path_advance(tmp_path, saved):
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, advance

    document = _review_state(tmp_path)
    document["phase"] = "executing"
    document["artifact_applicability"] = "not-applicable"
    cli = _load_cli_module("issue632_property_advance")
    repository = _in_memory_repository(document, saved=saved)
    result = advance(
        repository,
        AdvanceRequest(
            phase="reviewing",
            activity="active:review",
            at="2030-08-23T01:00:00Z",
            detail=None,
            artifact_applicability=None,
            artifact_path=None,
            producer_run_id=None,
        ),
        AdvanceServices(
            reject_active_provider_mutation=lambda _state, _operation: None,
            prepare_handoff=lambda _state: None,
            capture_artifact=cli.capture_artifact_identity,
            transition_phase=cli._transition_phase,
        ),
    )
    return result.decision


def _path_supersede(_tmp_path, saved):
    """supersede-reviews の active real-state transition 永続化経路。"""
    from mission_application.lifecycle import real_terminalizable_state
    from mission_common import is_supersede_marked
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    document = _active_document(review_group_id="issue632", review_generation=1)
    saved_holder = saved
    repository = _in_memory_repository(document, saved=saved_holder)
    with repository.transaction():
        state = repository.load()
        decision_state = real_terminalizable_state(state)
        assert decision_state is not None
        reason = "superseded by a replacement run"
        decision = decide(
            decision_state,
            MarkHalt(
                HaltCategory.STALE,
                reason,
                superseded=is_supersede_marked(
                    state.get("resolution_status"), reason
                ),
            ),
        )
        cli = _load_cli_module("issue632_property_supersede")

        def mutate(proposed):
            proposed.update(
                {
                    "passes": False,
                    "loop_active": False,
                    "halt_reason": "superseded by a replacement run",
                    "halt_category": "stale",
                }
            )
            cli._transition_phase(
                proposed, "halted", "2030-08-23T01:00:00Z", terminal_trusted_boundary=True
            )
            cli._write_terminal_outcome(proposed)

        proposed = repository.execute(state, mutate, decision.transition)
        repository.save(proposed, backup=False, administrative=True)
    return decision


@pytest.mark.parametrize(
    "path",
    (
        _path_mark_pass,
        _path_advance,
        _path_supersede,
        _path_mark_halt,
        _path_reactivate,
        _path_resume_stale,
        _path_permission_preflight,
        _path_set_fields,
    ),
    ids=(
        "mark-pass",
        "advance",
        "supersede-reviews",
        "mark-halt",
        "reactivate",
        "resume-stale",
        "permission-preflight",
        "set-fields",
    ),
)
def test_saved_document_matches_decided_claims_for_every_transition_path(tmp_path, path):
    """decide() が主張した完了隣接値が、そのまま保存 document の値になる。"""
    from mission_kernel.model import Phase
    from mission_kernel.transitions import transition_control_claims

    saved: dict = {}
    decision = path(tmp_path, saved)

    assert decision is not None and decision.accepted is True
    assert saved, "every transition path must persist a document"
    claims = transition_control_claims(decision.transition)
    for field_name, value in claims.items():
        expected = value.value if isinstance(value, (Phase, )) else value
        if hasattr(expected, "value"):
            expected = expected.value
        if expected is None:
            assert saved.get(field_name) is None
        else:
            assert saved.get(field_name) == expected, field_name


# --- V5 経路: commit された head が claims と一致し aggregate は 1 回だけ ---


class _FakeFencedRepository:
    """Minimal fenced backend for the V5 compatibility seam.

    実 ``LocalFencedRepository`` は lease/generation の実ファイルを要求するため、
    本テストでは commit 契約（stage → commit → aggregate）だけを観測できる薄い
    fake を使う。検証対象は ``V5CompatibilityRepository`` の execute/save 側で
    あり、fenced backend 自体は #542 系のテストが担保している。
    """

    def __init__(self, state):
        self._state = state
        self.commits = []

    def begin(self, _request):
        import types

        return types.SimpleNamespace(
            base=types.SimpleNamespace(state=self._state),
            pending_lease=types.SimpleNamespace(target=self._state.lease),
        )

    def _stage_persistence(self, _admitted, *, state_bytes, effects):
        import types

        assert effects == ()
        return types.SimpleNamespace(precondition=object(), state_bytes=state_bytes)

    def commit(self, prepared, precondition):
        assert precondition is prepared.precondition
        self.commits.append(__import__("json").loads(prepared.state_bytes))


def _v5_repository(tmp_path, *, calls, prepare_state=None):
    from mission_kernel.codec_v4 import decode_mission_state
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    document = _review_state(tmp_path)
    source = __import__("json").dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    backend = _FakeFencedRepository(decode_mission_state(source))
    repository = V5CompatibilityRepository(
        repository=backend,
        session_id=document.get("session_id") or "issue632-v5",
        lease_owner_session_id=document.get("session_id") or "issue632-v5",
        presented_lease_id=None,
        prepare_state=prepare_state,
        remove_from_aggregate=lambda: calls.append("remove"),
    )
    return repository, backend


def test_mark_pass_on_v5_repository_commits_claims_and_aggregate_once(tmp_path):
    from mission_application.review import MarkPassRequest, mark_pass
    from mission_kernel.model import Phase
    from mission_kernel.transitions import transition_control_claims

    calls: list = []
    repository, backend = _v5_repository(tmp_path, calls=calls)
    result = mark_pass(
        repository,
        MarkPassRequest(False, None, False, "", "2030-08-23T02:00:00Z"),
        _pass_services(_load_cli_module("issue632_v5_pass")),
    )

    assert result.decision.accepted is True
    assert len(backend.commits) == 1
    committed = backend.commits[0]
    for field_name, value in transition_control_claims(result.decision.transition).items():
        expected = value.value if isinstance(value, Phase) else value
        expected = getattr(expected, "value", expected)
        assert committed.get(field_name) == expected, field_name
    # aggregate remove は commit の後に 1 回だけ
    assert calls == ["remove"]


def test_mark_pass_on_v5_repository_rejects_claim_violation_without_commit(tmp_path):
    from mission_application.review import MarkPassRequest, MarkPassServices, mark_pass
    from mission_persistence.fenced_commit import FencedCommitError

    calls: list = []
    repository, backend = _v5_repository(tmp_path, calls=calls)
    cli = _load_cli_module("issue632_v5_violation")

    def diverging_transition_phase(proposed, _phase, at, **_kwargs):
        # kernel の決定 (done) と矛盾する第三の値を書く compatibility writer
        proposed["phase"] = "reviewing"
        proposed["phase_started_at"] = at

    services = MarkPassServices(
        verify_force_approval=lambda _data: {},
        validate_force_terminal=lambda _data, _verification: None,
        validate_score_evidence=lambda _data, _latest: None,
        validate_artifact_gate=lambda _data: None,
        validate_specialist_gate=lambda _data, _waiver: None,
        transition_phase=diverging_transition_phase,
        optional_unclosed_skills=lambda _data: [],
        selection_id=lambda _data: None,
    )
    with pytest.raises(FencedCommitError) as failure:
        mark_pass(
            repository,
            MarkPassRequest(False, None, False, "", "2030-08-23T02:00:00Z"),
            services,
        )

    assert failure.value.code == "transition-divergence"
    assert backend.commits == []
    assert calls == []
