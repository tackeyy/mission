"""Issue #617 批1-a: A1.lifecycle の完了隣接 command を kernel 化する.

対象は generic ``set`` のフィールド権限（kernel command 化）と init の
genesis 不変条件。halt / halt --all / cleanup-stale / refresh-pid の完了隣接
書き込みは既に decide() 経由 (mark-halt / resume-stale) であることを実測済み。
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import sys
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import (
    canonical_json_bytes,
    generate_cli_state_bytes,
    generate_cli_state_corpus,
)


COMPLETION_ADJACENT_FIELDS = (
    "terminal_outcome",
    "phase",
    "passes",
    "loop_active",
    "halt_reason",
    "halt_category",
)


def _load_cli_module(name):
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _RecordingRepository:
    """Minimal port capturing execute/save arguments for wiring assertions."""

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


def _active_legacy_state() -> dict:
    return {
        "schema_version": 4,
        "mission": "issue617 fixture mission",
        "phase": "executing",
        "iteration": 2,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2026-08-22T00:00:00Z",
    }


def _set_services(cli):
    from mission_application.lifecycle import SetFieldsServices

    return SetFieldsServices(
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
    )


def test_set_extension_fields_is_a_kernel_command(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import SetExtensionFields, kernel_command_type
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    state = decode_snapshot(canonical_json_bytes(corpus["provider_plan"])).state
    command = SetExtensionFields(
        freeze_json_value({"complexity": "Complex", "custom_note": "kept"})
    )

    assert kernel_command_type(command) == "set-extension-fields"
    first = decide(state, command)
    second = decide(state, command)
    assert first == second
    assert first.accepted is True
    assert first.rule_id == "set-extension-fields"
    assert first.transition is not None
    extensions = first.transition.new_state.extensions.thaw()
    assert extensions["complexity"] == "Complex"
    assert extensions["custom_note"] == "kept"
    # 完了隣接 authority は変化しない。
    assert first.transition.new_state.control == state.control
    assert [event.type for event in first.transition.events] == [
        "extension-fields-set"
    ]


@pytest.mark.parametrize("field", COMPLETION_ADJACENT_FIELDS)
def test_set_extension_fields_rejects_completion_adjacent_fields(tmp_path, field):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state
    decision = decide(
        state, SetExtensionFields(freeze_json_value({field: "attacker-value"}))
    )

    assert decision.accepted is False
    assert decision.transition is None
    assert decision.rejection.code in {"frozen-field", "dedicated-field"}


@pytest.mark.parametrize(
    "field", ("passes", "terminal_outcome", "score_history", "threshold")
)
def test_set_extension_fields_rejects_pass_gate_fields_as_frozen(tmp_path, field):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state
    decision = decide(
        state, SetExtensionFields(freeze_json_value({field: True}))
    )

    assert decision.accepted is False
    assert decision.rejection.code == "frozen-field"


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"": "empty-key"},
        "not-an-object",
        ["list"],
        42,
        None,
    ),
    ids=("empty", "empty-key", "string", "list", "int", "none"),
)
def test_set_extension_fields_rejects_malformed_payload(tmp_path, payload):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state
    decision = decide(
        state, SetExtensionFields(freeze_json_value(payload))
    )

    assert decision.accepted is False
    assert decision.rejection.code == "invalid-set-fields"


def test_generic_set_field_authority_is_kernel_owned():
    from mission_application.lifecycle import DEDICATED_SET_FIELDS
    from mission_kernel.commands import (
        GENERIC_SET_DEDICATED_FIELDS,
        GENERIC_SET_FROZEN_FIELDS,
    )

    cli = _load_cli_module("issue617_field_authority_cli")

    # application / CLI の分類は kernel の閉集合そのもの (drift 不可)。
    assert DEDICATED_SET_FIELDS == GENERIC_SET_DEDICATED_FIELDS
    assert frozenset(cli.FROZEN_FIELDS) == GENERIC_SET_FROZEN_FIELDS
    # 完了隣接フィールドは必ずどちらかの閉集合に属する (fail-closed)。
    for field in COMPLETION_ADJACENT_FIELDS:
        assert field in GENERIC_SET_DEDICATED_FIELDS | GENERIC_SET_FROZEN_FIELDS
    # 二重分類は権限の曖昧化なので禁止。
    assert not (GENERIC_SET_DEDICATED_FIELDS & GENERIC_SET_FROZEN_FIELDS)


def test_set_fields_use_case_gates_through_kernel_decision():
    from mission_application.lifecycle import SetFieldsRequest, set_fields

    cli = _load_cli_module("issue617_set_gate_cli")
    repository = _RecordingRepository(_active_legacy_state())
    result = set_fields(
        repository,
        SetFieldsRequest(
            kvs=("custom_note=hello", "estimate_minutes=25"),
            at="2026-08-22T01:00:00Z",
        ),
        _set_services(cli),
    )

    assert result.decision is not None
    assert result.decision.accepted is True
    assert result.decision.rule_id == "set-extension-fields"
    # 認可済み transition が repository へ渡る (批2-a で writer になる前提の配線)。
    assert repository.executed_transition is result.decision.transition
    assert repository.saved["custom_note"] == "hello"
    assert repository.saved["estimate_minutes"] == 25


@pytest.mark.parametrize("field", COMPLETION_ADJACENT_FIELDS)
def test_set_fields_use_case_rejects_completion_adjacent_fields(field):
    from mission_application.lifecycle import (
        LifecycleFailure,
        SetFieldsRequest,
        set_fields,
    )

    cli = _load_cli_module("issue617_set_reject_cli")
    repository = _RecordingRepository(_active_legacy_state())
    with pytest.raises(LifecycleFailure):
        set_fields(
            repository,
            SetFieldsRequest(kvs=(f"{field}=true",), at="2026-08-22T01:00:00Z"),
            _set_services(cli),
        )
    assert repository.saved is None


def test_set_fields_frozen_field_message_is_preserved():
    from mission_application.lifecycle import (
        LifecycleFailure,
        SetFieldsRequest,
        set_fields,
    )

    cli = _load_cli_module("issue617_set_frozen_message_cli")
    repository = _RecordingRepository(_active_legacy_state())
    with pytest.raises(LifecycleFailure) as failure:
        set_fields(
            repository,
            SetFieldsRequest(kvs=("mission_id=forged",), at="2026-08-22T01:00:00Z"),
            _set_services(cli),
        )
    assert failure.value.reason == "frozen-field"
    assert "`mission_id` は set で変更不可" in failure.value.message


def test_routed_goal_set_keeps_the_mark_halt_decision():
    """#330 routed-goal 経路の decision は MarkHalt のまま上書きされない。"""
    from mission_application.lifecycle import SetFieldsRequest, set_fields

    cli = _load_cli_module("issue617_routed_goal_cli")
    before = {
        "schema_version": 4,
        "mission": "simple mission",
        "phase": "planning",
        "iteration": 1,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2026-08-22T00:00:00Z",
    }
    repository = _RecordingRepository(before)
    result = set_fields(
        repository,
        SetFieldsRequest(kvs=("complexity=Simple",), at="2026-08-22T01:00:00Z"),
        _set_services(cli),
    )

    assert result.routed_verdict is not None
    assert result.decision is not None
    assert result.decision.rule_id == "mark-halt"
    assert repository.saved["halt_category"] == "routed-goal"


def test_init_genesis_state_decodes_to_active_kernel_control(tmp_path):
    """init は kernel 外 genesis: 生成物は必ず active な typed control に decode される。"""
    from mission_kernel import decode_snapshot
    from mission_kernel.model import Phase

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state

    assert state.control.phase is Phase.PLANNING
    assert state.control.loop_active is True
    assert state.control.passes is False
    assert state.control.halt_reason == ""
    assert state.control.terminal_outcome is None
    assert state.control.halt_category is None


def test_pass_gate_semantics_unchanged_after_extension_write(tmp_path):
    """SetExtensionFields 適用後も mark-pass の gate 意味論 (score 必須) は不変。"""
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkPass, SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state
    written = decide(
        state, SetExtensionFields(freeze_json_value({"custom_note": "kept"}))
    )
    assert written.accepted is True

    gate = decide(
        written.transition.new_state,
        MarkPass(artifact_gate_satisfied=True, specialist_gate_satisfied=True),
    )
    assert gate.accepted is False
    assert gate.rejection.code == "score-required"
