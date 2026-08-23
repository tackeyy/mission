"""Issue #631 批2-a-2: mark-halt の実 state decide 化と claims の拡張・適用.

- mark_halt は decode 可能かつ active な state では実 state で decide し、
  transition を execute へ送付する。terminal / 劣化 doc は monotonic view へ
  fallback し gate-only（冪等 emergency halt の保証は不変）
- claims は halt_category まで拡張（halt_reason は raw-vs-semantic、
  terminal_outcome は derive_terminal_outcome の legacy reason 優先規則との
  乖離があり除外）
- execute は claimed field へ transition 値を適用する（writer 欠落は補完、
  異なる値は transition-divergence で fail-closed）
"""

from __future__ import annotations

import contextlib
import copy

import pytest

from .mission_state_fixture_corpus import generate_cli_state_bytes


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


def _active_state(**overrides) -> dict:
    state = {
        "schema_version": 4,
        "mission": "issue631 fixture mission",
        "phase": "executing",
        "iteration": 1,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2026-08-24T00:00:00Z",
    }
    state.update(overrides)
    return state


def _halt_services(cli_free=True):
    from mission_application.lifecycle import MarkHaltServices

    def transition_phase(proposed, phase, at, **_kwargs):
        proposed["phase"] = phase
        proposed["phase_started_at"] = at

    return MarkHaltServices(
        reject_active_provider_mutation=lambda _state, _command: None,
        transition_phase=transition_phase,
        goal_dispatch_fields=lambda _state: {},
    )


def _run_mark_halt(repository, *, category="blocked-external", reason="blocked"):
    from mission_application.lifecycle import MarkHaltRequest, mark_halt

    return mark_halt(
        repository,
        MarkHaltRequest(
            reason=reason,
            category=category,
            at="2026-08-24T01:00:00Z",
            set_terminal_phase=True,
        ),
        _halt_services(),
    )


def test_mark_halt_decides_on_the_real_state_when_active():
    """実 state decide: decision の入力に実 session_role が反映される。"""
    from mission_kernel.model import TerminalOutcome

    repository = _RecordingRepository(_active_state(session_role="checker"))
    result = _run_mark_halt(
        repository, category="evidence-submitted", reason="evidence submitted"
    )

    assert result.decision.accepted is True
    # synthetic view (implementer 固定) では INCOMPLETE になる。実 state の
    # checker role が使われた証拠として COMPLETED_EVIDENCE を要求する。
    assert (
        result.decision.transition.new_state.terminal_outcome
        is TerminalOutcome.COMPLETED_EVIDENCE
    )
    assert repository.executed_transition is result.decision.transition


def test_mark_halt_claims_include_halt_category():
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import transition_control_claims

    repository = _RecordingRepository(_active_state())
    result = _run_mark_halt(repository)

    claims = transition_control_claims(result.decision.transition)
    assert claims == {
        "phase": Phase.HALTED,
        "loop_active": False,
        "halt_category": HaltCategory.BLOCKED_EXTERNAL,
    }
    assert repository.saved["halt_category"] == "blocked-external"


def test_mark_halt_on_terminal_state_falls_back_to_gate_only():
    """既 halted state への再 halt は冪等のまま（monotonic fallback・gate-only）。"""
    repository = _RecordingRepository(
        _active_state(
            phase="halted",
            loop_active=False,
            halt_reason="previous halt",
            halt_category="blocked-external",
            terminal_outcome="blocked_external",
        )
    )
    result = _run_mark_halt(repository, reason="halt again")

    assert result.decision.accepted is True
    assert repository.executed_transition is None
    assert repository.saved["halt_reason"] == "halt again"
    assert repository.saved["phase"] == "halted"


def test_mark_halt_on_undecodable_state_falls_back_to_gate_only():
    """劣化 legacy doc でも emergency halt は成功する（fallback・gate-only）。"""
    state = _active_state()
    state["canonical_plan"] = {"malformed": True}  # kernel decode を壊す
    repository = _RecordingRepository(state)
    result = _run_mark_halt(repository)

    assert result.decision.accepted is True
    assert repository.executed_transition is None
    assert repository.saved["phase"] == "halted"
    assert repository.saved["halt_category"] == "blocked-external"


def test_execute_applies_claimed_values_when_writer_omits_them(tmp_path):
    """apply 化: writer が claimed field を書かなくても transition 値が永続値になる。"""
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide
    from mission_persistence.legacy_v4 import LegacyV4Repository

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    typed = decode_snapshot(source).state
    decision = decide(typed, MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked"))
    assert decision.accepted is True

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        write_state=lambda _state: None,
        backup_state=lambda: None,
    )
    document = {"phase": "planning", "loop_active": True, "passes": False}

    def mutate(proposed):
        proposed["halt_reason"] = "blocked"  # claimed fields は書かない

    proposed = repository.execute(document, mutate, decision.transition)
    assert proposed["phase"] == "halted"
    assert proposed["loop_active"] is False
    assert proposed["halt_category"] == "blocked-external"


def test_execute_rejects_writer_that_contradicts_a_claim(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.legacy_v4 import LegacyV4Repository

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    typed = decode_snapshot(source).state
    decision = decide(typed, MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked"))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        write_state=lambda _state: None,
        backup_state=lambda: None,
    )

    def mutate(proposed):
        proposed["phase"] = "reviewing"  # kernel の決定 (halted) と矛盾する値

    with pytest.raises(FencedCommitError) as failure:
        repository.execute({"phase": "planning", "loop_active": True}, mutate, decision.transition)
    assert failure.value.code == "transition-divergence"


def test_cli_reports_fenced_commit_error_as_internal_invariant(monkeypatch, capsys):
    """CLI catch 節: FencedCommitError は構造化エラー + exit 2 で報告される。"""
    import argparse
    import importlib.util
    import sys as _sys
    from pathlib import Path

    from mission_persistence.fenced_commit import FencedCommitError

    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue631_cli_invariant", path)
    cli = importlib.util.module_from_spec(spec)
    _sys.modules[spec.name] = cli
    spec.loader.exec_module(cli)

    def raising(*_args, **_kwargs):
        raise FencedCommitError("transition-divergence", "writer diverged")

    monkeypatch.setattr(cli, "_legacy_lifecycle_repository", lambda *a, **k: object())
    monkeypatch.setattr(cli, "run_set_fields", raising)
    with pytest.raises(SystemExit) as exit_info:
        cli._set_fields_with_repository(
            argparse.Namespace(kvs=["custom_note=x"]), Path.cwd(), Path("state.json")
        )
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert "internal-invariant: transition-divergence" in captured.err


def test_permission_invariant_failures_are_not_swallowed():
    """fail-open 解消: kernel 拒否は RuntimeError として伝播する（ValueError で
    吸収されない）。"""
    from mission_application import runtime_guard
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    class _Rejected:
        accepted = False

        class rejection:
            code = "invalid-reason"

        transition = None

    original = runtime_guard.monotonic_halt_decision
    runtime_guard.monotonic_halt_decision = lambda *_a, **_k: _Rejected()
    try:
        with pytest.raises(RuntimeError):
            record_permission_observation(
                _RecordingRepository(_active_state()),
                PermissionObservationRequest(
                    probes=(PermissionProbe("state", "denied", "write-unavailable"),),
                    observed_at="2026-08-24T01:00:00Z",
                ),
            )
    finally:
        runtime_guard.monotonic_halt_decision = original
