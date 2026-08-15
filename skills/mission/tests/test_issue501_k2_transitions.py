"""Issue #501 K2 state-only command and transition-table contract."""

from __future__ import annotations

from .mission_state_fixture_corpus import (
    canonical_json_bytes,
    generate_cli_state_bytes,
    generate_cli_state_corpus,
)


def test_state_only_commands_are_deterministic_and_terminal_safe(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import AdvancePhase, MarkHalt, Reactivate, ResumeStale
    from mission_kernel.model import HaltCategory, Phase, PreparedHandoff, TerminalOutcome
    from mission_kernel.transitions import decide

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    planning = decode_snapshot(canonical_json_bytes(corpus["provider_plan"])).state

    handoff = PreparedHandoff(
        schema="mission-handoff/1",
        handoff_id="handoff-k2-contract",
        plan=planning.plan,
        ordered_step_ids=("execute",),
    )
    command = AdvancePhase(Phase.EXECUTING, handoff)
    first = decide(planning, command)
    second = decide(planning, command)

    assert first == second
    assert first.accepted is True
    assert first.transition is not None
    assert first.transition.new_state.control.phase is Phase.EXECUTING
    assert first.transition.new_state.handoff == handoff
    assert first.transition.effects == ()

    halted = decide(
        first.transition.new_state,
        MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked by external authority"),
    )
    assert halted.accepted is True
    assert halted.transition.new_state.terminal_outcome is TerminalOutcome.BLOCKED_EXTERNAL

    rejected = decide(halted.transition.new_state, AdvancePhase(Phase.REVIEWING))
    assert rejected.accepted is False
    assert rejected.transition is None
    assert rejected.rejection.code == "terminal-state"
    assert rejected.events == ()
    assert rejected.effects == ()

    stale = decode_snapshot(
        canonical_json_bytes(corpus["terminal_outcomes"]["stale_superseded"])
    ).state
    wrong_recovery = decide(
        stale,
        Reactivate(
            HaltCategory.STALE,
            "approved but wrong recovery path",
            True,
            Phase.PLANNING,
        ),
    )
    assert wrong_recovery.accepted is False
    assert wrong_recovery.rejection.code == "stale-requires-resume"
    resumed = decide(stale, ResumeStale(Phase.PLANNING))
    assert resumed.accepted is True
    assert resumed.transition.new_state.terminal_outcome is None

    manual = decode_snapshot(
        canonical_json_bytes(corpus["terminal_outcomes"]["blocked_external"])
    ).state
    wrong_category = decide(
        manual,
        Reactivate(
            HaltCategory.AWAITING_APPROVAL,
            "approved category must match",
            True,
            Phase.PLANNING,
        ),
    )
    assert wrong_category.accepted is False
    assert wrong_category.rejection.code == "halt-category-mismatch"


def test_terminal_outcome_is_computed_from_typed_control(tmp_path):
    from dataclasses import fields, replace

    from mission_kernel import decode_snapshot
    from mission_kernel.model import HaltCategory, Phase, TerminalOutcome

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state
    halted_control = replace(
        state.control,
        phase=Phase.HALTED,
        terminal_outcome=TerminalOutcome.BLOCKED_EXTERNAL,
        loop_active=False,
        halt_reason="typed control halt",
        halt_category=HaltCategory.BLOCKED_EXTERNAL,
    )
    halted = replace(state, control=halted_control)

    assert "terminal_outcome" not in {item.name for item in fields(state)}
    assert halted.terminal_outcome is halted.control.terminal_outcome


def test_planning_advance_requires_one_matching_prepared_handoff(tmp_path):
    from dataclasses import replace

    from mission_kernel import decode_snapshot
    from mission_kernel.commands import AdvancePhase
    from mission_kernel.model import Phase, PreparedHandoff
    from mission_kernel.transitions import decide

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    planning = decode_snapshot(canonical_json_bytes(corpus["provider_plan"])).state

    missing = decide(planning, AdvancePhase(Phase.EXECUTING))
    assert missing.rejection.code == "prepared-handoff-required"

    other_plan = replace(planning.plan, generation=planning.plan.generation + 1)
    mismatched = PreparedHandoff(
        schema="mission-handoff/1",
        handoff_id="handoff-mismatch",
        plan=other_plan,
        ordered_step_ids=("execute",),
    )
    rejected = decide(planning, AdvancePhase(Phase.EXECUTING, mismatched))
    assert rejected.rejection.code == "handoff-plan-mismatch"


def test_planning_advance_rejects_malformed_handoff_command_payload(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import AdvancePhase
    from mission_kernel.model import Phase, PreparedHandoff
    from mission_kernel.transitions import decide

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    planning = decode_snapshot(canonical_json_bytes(corpus["provider_plan"])).state
    cases = (
        PreparedHandoff("", "handoff-ok", planning.plan, ("execute",)),
        PreparedHandoff("mission-handoff/1", "", planning.plan, ("execute",)),
        PreparedHandoff("mission-handoff/1", "handoff-ok", planning.plan, (" ",)),
        PreparedHandoff("mission-handoff/1", "handoff-ok", planning.plan, ("x" * 129,)),
    )

    for handoff in cases:
        rejected = decide(planning, AdvancePhase(Phase.EXECUTING, handoff))
        assert rejected.accepted is False
        assert rejected.rejection.code == "invalid-prepared-handoff"


def test_legacy_stale_terminal_uses_resume_not_manual_reactivation():
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import Reactivate, ResumeStale
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import canonical_json_bytes, issue483_corpus

    legacy_stale = decode_snapshot(
        canonical_json_bytes(issue483_corpus()["v2"])
    ).state

    wrong_path = decide(
        legacy_stale,
        Reactivate(
            HaltCategory.OTHER,
            "legacy stale must use the recovery command",
            True,
            Phase.PLANNING,
        ),
    )
    assert wrong_path.rejection.code == "stale-requires-resume"

    resumed = decide(legacy_stale, ResumeStale(Phase.PLANNING))
    assert resumed.accepted is True
    assert resumed.transition.new_state.control.phase is Phase.PLANNING
    assert resumed.transition.new_state.control.halt_category is None


def test_duplicate_command_rules_fail_table_construction():
    import pytest

    from mission_kernel.commands import AdvancePhase
    from mission_kernel.transitions import TransitionRule, TransitionTableError, build_transition_table

    def accepts(_state, _command):
        return True

    def reduce_to_none(_state, _command):
        return None

    rule = TransitionRule("advance-one", AdvancePhase, accepts, reduce_to_none)
    duplicate = TransitionRule("advance-two", AdvancePhase, accepts, reduce_to_none)

    with pytest.raises(TransitionTableError) as rejected:
        build_transition_table((rule, duplicate))

    assert rejected.value.code == "duplicate-command-rule"


def test_bounded_state_only_graph_is_deterministic_and_invariant_safe(tmp_path):
    from collections import deque

    from mission_kernel import decode_snapshot
    from mission_kernel.commands import AdvancePhase, MarkHalt
    from mission_kernel.model import AbsentPlan, HaltCategory, Phase, PreparedHandoff
    from mission_kernel.transitions import decide

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    initial = decode_snapshot(canonical_json_bytes(corpus["provider_plan"])).state
    queue = deque([(initial, 0)])
    visited = set()

    while queue:
        state, depth = queue.popleft()
        identity = (
            state.control.phase,
            state.terminal_outcome,
            state.control.halt_category,
            state.handoff.kind,
        )
        if identity in visited or depth > 3:
            continue
        visited.add(identity)
        commands = [MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "bounded graph halt")]
        if state.control.phase is Phase.PLANNING and not isinstance(state.plan, AbsentPlan):
            commands.append(
                AdvancePhase(
                    Phase.EXECUTING,
                    PreparedHandoff(
                        "mission-handoff/1",
                        "handoff-bounded-graph",
                        state.plan,
                        ("execute",),
                    ),
                )
            )
        elif state.control.phase is Phase.EXECUTING:
            commands.append(AdvancePhase(Phase.REVIEWING))

        for command in commands:
            first = decide(state, command)
            assert first == decide(state, command)
            if not first.accepted:
                assert first.transition is None
                assert first.events == ()
                assert first.effects == ()
                continue
            next_state = first.transition.new_state
            assert next_state.terminal_outcome == next_state.control.terminal_outcome
            queue.append((next_state, depth + 1))

    assert {item[0] for item in visited} >= {
        Phase.PLANNING,
        Phase.EXECUTING,
        Phase.REVIEWING,
        Phase.HALTED,
    }


def test_derive_next_continuation_commands_execute_through_the_same_named_rule(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import derive_next
    from mission_kernel.model import Phase
    import mission_kernel.transitions as transitions

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    snapshot = decode_snapshot(canonical_json_bytes(corpus["phases"]["executing"]))
    original_rule = next(
        rule
        for rule in transitions.TRANSITION_TABLE
        if rule.rule_id == "advance-phase"
    )
    guard_calls = []
    factory_calls = []

    def observed_command_guard(state, command):
        guard_calls.append((state.control.phase, type(command)))
        return original_rule.command_guard(state, command)

    def observed_guidance_factory(state, guidance, rule):
        factory_calls.append(rule)
        return original_rule.guidance_factory(state, guidance, rule)

    shared_rule = replace(
        original_rule,
        command_guard=observed_command_guard,
        guidance_factory=observed_guidance_factory,
    )
    monkeypatch.setattr(
        transitions,
        "TRANSITION_TABLE",
        tuple(
            shared_rule if rule is original_rule else rule
            for rule in transitions.TRANSITION_TABLE
        ),
    )

    recipe = derive_next(snapshot.state, snapshot.guidance)
    state = snapshot.state

    assert recipe.rule_id == "advance-phase"
    assert recipe.continuation_commands
    assert len(factory_calls) == 1
    assert factory_calls[0] is shared_rule
    assert shared_rule.guidance_guard(state, snapshot.guidance) is True
    for command in recipe.continuation_commands:
        decision = transitions.decide(state, command)
        assert decision.accepted is True
        assert decision.rule_id == recipe.rule_id
        state = decision.transition.new_state

    assert guard_calls == [(Phase.EXECUTING, type(recipe.continuation_commands[0]))]
    assert state.control.phase is Phase.REVIEWING
