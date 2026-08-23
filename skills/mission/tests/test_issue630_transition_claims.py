"""Issue #630 批2-a-1: repository execute が transition の主張を fail-closed 検証する.

transition を渡された dict 経路の execute は、(a) canonical decision table が
発行した sealed transition であること、(b) compatibility mutation の結果が
transition の主張する completion 隣接 control 差分（phase / loop_active /
passes）と一致することを検証する。halt_reason / halt_category /
terminal_outcome は synthetic decision state（批2-a-2 #631 で実 state 化）が
解消するまで検証対象外。
"""

from __future__ import annotations

import contextlib
import copy

import pytest

from .mission_state_fixture_corpus import (
    canonical_json_bytes,
    generate_cli_state_bytes,
    generate_cli_state_corpus,
)


def _decoded_state(tmp_path):
    from mission_kernel import decode_snapshot

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    return decode_snapshot(source).state


def _halt_transition(state):
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    decision = decide(state, MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked"))
    assert decision.accepted is True
    return decision.transition


class _RecordingRepository:
    def __init__(self, before):
        self._before = copy.deepcopy(before)
        self.executed_transition = "unset"
        self.saved = None

    def transaction(self):
        return contextlib.nullcontext()

    def load(self):
        return copy.deepcopy(self._before)

    def execute(self, state, mutation, transition=None):
        self.executed_transition = transition
        proposed = copy.deepcopy(state)
        mutation(proposed)
        self._executed = copy.deepcopy(proposed)
        return proposed

    def save(self, state, *, backup=True, administrative=False, aggregate_action=None):
        self.saved = copy.deepcopy(state)


def test_transition_control_claims_returns_completion_adjacent_delta(tmp_path):
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import transition_control_claims

    state = _decoded_state(tmp_path)
    transition = _halt_transition(state)
    claims = transition_control_claims(transition)

    assert claims == {
        "phase": Phase.HALTED,
        "loop_active": False,
        "halt_category": HaltCategory.BLOCKED_EXTERNAL,
    }


def test_transition_control_claims_is_empty_for_extension_writes(tmp_path):
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide, transition_control_claims

    state = _decoded_state(tmp_path)
    decision = decide(
        state, SetExtensionFields(freeze_json_value({"custom_note": "kept"}))
    )
    assert decision.accepted is True

    assert transition_control_claims(decision.transition) == {}


def test_transition_control_claims_rejects_forged_transition(tmp_path):
    from mission_kernel.transitions import (
        KernelEvent,
        Transition,
        TransitionTableError,
        transition_control_claims,
    )

    state = _decoded_state(tmp_path)
    forged = Transition(state, (KernelEvent("forged"),))
    with pytest.raises(TransitionTableError):
        transition_control_claims(forged)


def _legacy_repository():
    from mission_persistence.legacy_v4 import LegacyV4Repository

    return LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        write_state=lambda _state: None,
        backup_state=lambda: None,
    )


def test_execute_accepts_mutation_matching_the_claims(tmp_path):
    state = _decoded_state(tmp_path)
    transition = _halt_transition(state)
    document = {"phase": "planning", "loop_active": True, "passes": False}

    def mutate(proposed):
        proposed["phase"] = "halted"
        proposed["loop_active"] = False
        proposed["halt_reason"] = "blocked"

    proposed = _legacy_repository().execute(document, mutate, transition)
    assert proposed["phase"] == "halted"
    assert proposed["loop_active"] is False


def test_execute_rejects_mutation_diverging_from_the_claims(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    state = _decoded_state(tmp_path)
    transition = _halt_transition(state)
    document = {"phase": "planning", "loop_active": True, "passes": False}

    def mutate(proposed):
        # 主張と矛盾する値を書く compatibility writer は divergence
        # (書かない場合は #631 の apply 化により transition 値が補完される)。
        proposed["phase"] = "reviewing"
        proposed["loop_active"] = False

    with pytest.raises(FencedCommitError) as failure:
        _legacy_repository().execute(document, mutate, transition)
    assert failure.value.code == "transition-divergence"


def test_execute_rejects_non_bool_projection_of_bool_claims(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    state = _decoded_state(tmp_path)
    transition = _halt_transition(state)
    document = {"phase": "planning", "loop_active": True, "passes": False}

    def mutate(proposed):
        proposed["phase"] = "halted"
        proposed["loop_active"] = 0  # falsy but not False: fail-closed

    with pytest.raises(FencedCommitError) as failure:
        _legacy_repository().execute(document, mutate, transition)
    assert failure.value.code == "transition-divergence"


def test_execute_rejects_forged_transition(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_kernel.transitions import KernelEvent, Transition

    state = _decoded_state(tmp_path)
    forged = Transition(state, (KernelEvent("forged"),))

    def mutate(proposed):
        proposed["phase"] = "halted"

    with pytest.raises(FencedCommitError) as failure:
        _legacy_repository().execute({"phase": "planning"}, mutate, forged)
    assert failure.value.code == "transition-unsealed"


def test_execute_without_transition_preserves_compatibility_behavior():
    def mutate(proposed):
        proposed["custom"] = "value"

    proposed = _legacy_repository().execute({"phase": "planning"}, mutate)
    assert proposed == {"phase": "planning", "custom": "value"}


def test_v5_compatibility_execute_enforces_the_same_claims(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    state = _decoded_state(tmp_path)
    transition = _halt_transition(state)
    repository = V5CompatibilityRepository(
        repository=None,
        session_id="issue630-session",
        lease_owner_session_id="issue630-session",
        presented_lease_id=None,
    )

    def diverging(proposed):
        proposed["phase"] = "reviewing"  # kernel の決定 (halted) と矛盾する値
        proposed["loop_active"] = False

    with pytest.raises(FencedCommitError) as failure:
        repository.execute(
            {"phase": "planning", "loop_active": True}, diverging, transition
        )
    assert failure.value.code == "transition-divergence"

    def matching(proposed):
        proposed["phase"] = "halted"
        proposed["loop_active"] = False

    proposed = repository.execute(
        {"phase": "planning", "loop_active": True}, matching, transition
    )
    assert proposed["phase"] == "halted"


def test_mark_halt_terminal_phase_passes_the_transition_through_execute():
    from mission_application.lifecycle import MarkHaltRequest, MarkHaltServices, mark_halt
    from mission_common import derive_terminal_outcome as _derive

    before = {
        "schema_version": 4,
        "mission": "issue630 fixture mission",
        "phase": "executing",
        "iteration": 1,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2026-08-22T00:00:00Z",
    }

    def transition_phase(proposed, phase, at, **_kwargs):
        proposed["phase"] = phase
        proposed["phase_started_at"] = at

    repository = _RecordingRepository(before)
    result = mark_halt(
        repository,
        MarkHaltRequest(
            reason="blocked externally",
            category="blocked-external",
            at="2026-08-22T01:00:00Z",
            set_terminal_phase=True,
        ),
        MarkHaltServices(
            reject_active_provider_mutation=lambda _state, _command: None,
            transition_phase=transition_phase,
            goal_dispatch_fields=lambda _state: {},
        ),
    )

    assert result.decision.accepted is True
    assert repository.executed_transition is result.decision.transition
    assert repository.saved["phase"] == "halted"


def test_mark_halt_soft_terminal_stays_gate_only():
    """set_terminal_phase=False（janitor の orphan 経路）は kernel の主張
    (phase→halted) から意図的に逸脱するため、transition を execute へ渡さない。"""
    from mission_application.lifecycle import MarkHaltRequest, MarkHaltServices, mark_halt

    before = {
        "schema_version": 4,
        "mission": "issue630 fixture mission",
        "phase": "executing",
        "iteration": 1,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2026-08-22T00:00:00Z",
    }

    repository = _RecordingRepository(before)
    result = mark_halt(
        repository,
        MarkHaltRequest(
            reason="orphan: pid dead (cleanup-stale)",
            category="stale",
            at="2026-08-22T01:00:00Z",
            set_terminal_phase=False,
        ),
        MarkHaltServices(
            reject_active_provider_mutation=lambda _state, _command: None,
            transition_phase=lambda *_args, **_kwargs: None,
            goal_dispatch_fields=lambda _state: {},
            terminalize_without_phase=lambda _state, _at, _trusted: None,
        ),
    )

    assert result.decision.accepted is True
    assert repository.executed_transition is None
    # soft-terminal: phase は維持され halt_reason だけが立つ
    assert repository.saved["phase"] == "executing"
    assert repository.saved["halt_reason"] == "orphan: pid dead (cleanup-stale)"


def test_end_to_end_saved_state_matches_decided_claims(tmp_path):
    """decide() の結果と保存される state の一致 (subset property)。

    実 LegacyV4Repository（in-memory writer + claims 検証つき execute）で
    mark_halt use case を通し、保存 doc が transition の主張と一致することを
    execute の検証が通ったこと自体で確認する。
    """
    from mission_application.lifecycle import MarkHaltRequest, MarkHaltServices, mark_halt
    from mission_kernel.transitions import transition_control_claims
    from mission_persistence.legacy_v4 import LegacyV4Repository

    before = {
        "schema_version": 4,
        "mission": "issue630 e2e mission",
        "phase": "reviewing",
        "iteration": 2,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2026-08-22T00:00:00Z",
    }
    saved = {}
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=lambda state: saved.update(copy.deepcopy(state)),
        backup_state=lambda: None,
    )

    def transition_phase(proposed, phase, at, **_kwargs):
        proposed["phase"] = phase
        proposed["phase_started_at"] = at

    result = mark_halt(
        repository,
        MarkHaltRequest(
            reason="external dependency down",
            category="blocked-external",
            at="2026-08-22T02:00:00Z",
            set_terminal_phase=True,
        ),
        MarkHaltServices(
            reject_active_provider_mutation=lambda _state, _command: None,
            transition_phase=transition_phase,
            goal_dispatch_fields=lambda _state: {},
        ),
    )

    claims = transition_control_claims(result.decision.transition)
    for field, value in claims.items():
        stored = saved.get(field, False)
        expected = value.value if field == "phase" else value
        assert stored == expected
