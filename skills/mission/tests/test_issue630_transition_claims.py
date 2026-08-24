"""Issue #630/#644: atomic projection 境界の halt 回帰。"""

from __future__ import annotations

import contextlib
import copy
import json

from mission_application.ports import LegacyCommandExecutionResult
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.model import FrozenJsonObject


class _RecordingRepository:
    def __init__(self, before):
        self._before = copy.deepcopy(before)
        self.executed_transition = None
        self.saved = None

    def transaction(self):
        return contextlib.nullcontext()

    def load(self):
        return copy.deepcopy(self._before)

    def execute(self, command, **_kwargs):
        from mission_kernel import decode_mission_state, project_legacy_document
        from mission_kernel.transitions import decide

        state = decode_mission_state(json.dumps(self._before, sort_keys=True).encode("utf-8"))
        decision = decide(state, command)
        assert decision.accepted and decision.transition is not None
        proposed = json.loads(project_legacy_document(decision.transition.new_state))
        self.executed_transition = decision.transition
        self.saved = copy.deepcopy(proposed)
        frozen = freeze_json_value(proposed)
        assert isinstance(frozen, FrozenJsonObject)
        return LegacyCommandExecutionResult(decision, frozen)

    def save(self, state, *, backup=True, administrative=False, aggregate_action=None):
        self.saved = copy.deepcopy(state)


def test_mark_halt_terminal_phase_passes_the_transition_through_execute():
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


def test_end_to_end_saved_state_matches_decided_projection(tmp_path):
    """atomic execute が decided new_state の projection をそのまま保存する。"""
    import json

    from mission_kernel import project_legacy_document
    from mission_application.lifecycle import MarkHaltRequest, MarkHaltServices, mark_halt
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
        remove_from_aggregate=lambda: None,
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

    assert saved == json.loads(project_legacy_document(result.decision.transition.new_state))
