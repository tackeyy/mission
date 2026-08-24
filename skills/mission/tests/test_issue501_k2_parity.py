"""Issue #501 K2 shadow parity against the actual legacy decision function."""

from __future__ import annotations

import importlib.util
import sys

import pytest

from .mission_state_fixture_corpus import (
    canonical_json_bytes,
    generate_cli_state_bytes,
    generate_cli_state_corpus,
)


_CORPUS_PARITY_CONTRACTS = {
    "corpus.handoff_prepared": ("advance-phase", "legacy-required", ("A4.executor-handoff",)),
    "corpus.handoff_consuming": ("advance-phase", "legacy-required", ("A4.executor-handoff",)),
    "corpus.handoff_consumed": ("advance-phase", "legacy-required", ("A4.executor-handoff",)),
    "corpus.handoff_rejected": ("advance-phase", "legacy-required", ("A4.executor-handoff",)),
    "corpus.provider_result_ready": ("planning-policy-import-planning-result", "legacy-required", ("A4.planning-provider",)),
    "corpus.provider_plan_imported": ("planning-policy-promote-canonical-plan", "legacy-required", ("A4.planning-provider",)),
    "corpus.provider_plan": ("advance-phase", "legacy-required", ("A4.plan-handoff",)),
    "corpus.review_input": ("planning-inline", "legacy-required", ("A1.lifecycle", "A4.plan-handoff")),
    "corpus.review_aggregate_and_bound_score": ("mark-passes", "legacy-required", ("A2.pass-authority",)),
    "corpus.manual_import_bound_score": ("mark-passes", "legacy-required", ("A2.pass-authority",)),
    "corpus.specialist_rejected_scoring": ("mark-passes", "legacy-required", ("A2.pass-authority",)),
    "corpus.lease_acquired": ("planning-inline", "legacy-required", ("A1.lifecycle", "A4.plan-handoff")),
    "corpus.lease_taken_over": ("review-external", "legacy-required", ("A2.review",)),
    "corpus.guidance_branches.awaiting_user": ("await-user", "legacy-required", ("A5.awaiting-user",)),
    "corpus.guidance_branches.inactive": ("resume-inactive", "legacy-required", ("A1.refresh-resume",)),
    "corpus.guidance_branches.stagnation": ("mark-halt", "legacy-required", ("A1.mark-halt",)),
    "corpus.guidance_branches.critic_scope": ("review-scope", "legacy-required", ("A2.critic-scope",)),
    "corpus.guidance_branches.provider_primary_binding_missing": ("planning-inline", "legacy-required", ("A1.lifecycle", "A4.plan-handoff")),
    "corpus.guidance_branches.iteration_zero_scoring": ("aggregate-reviews", "legacy-required", ("A2.review-score",)),
    "corpus.guidance_branches.simple_goal_route": ("route-goal", "legacy-required", ("legacy.goal-dispatch", "legacy.host-observation")),
    "corpus.guidance_branches.simple_host_observation": ("route-goal", "legacy-required", ("legacy.goal-dispatch", "legacy.host-observation")),
    "corpus.phases.planning": ("planning-inline", "legacy-required", ("A1.lifecycle", "A4.plan-handoff")),
    "corpus.phases.executing": ("advance-phase", "legacy-required", ("A4.executor-handoff",)),
    "corpus.phases.reviewing": ("review-external", "legacy-required", ("A2.review",)),
    "corpus.phases.scoring": ("aggregate-reviews", "legacy-required", ("A2.review-score",)),
    "corpus.phases.done": ("aggregate-reviews", "legacy-required", ("A2.review-score",)),
    "corpus.phases.halted": ("aggregate-reviews", "legacy-required", ("A2.review-score",)),
    "corpus.terminal_outcomes.completed_pass": ("terminal-pass", "exact", ()),
    "corpus.terminal_outcomes.completed_evidence": ("terminal-evidence", "exact", ()),
    "corpus.terminal_outcomes.blocked_external": ("terminal-halt", "exact", ()),
    "corpus.terminal_outcomes.awaiting_approval": ("terminal-halt", "exact", ()),
    "corpus.terminal_outcomes.stale_superseded": ("terminal-halt", "exact", ()),
    "corpus.terminal_outcomes.failed": ("terminal-halt", "exact", ()),
    "corpus.terminal_outcomes.incomplete": ("terminal-halt", "exact", ()),
    "corpus.terminal_outcomes.user_aborted": ("terminal-halt", "exact", ()),
    "corpus.terminal_outcomes.routed_elsewhere": ("terminal-halt", "exact", ()),
}


def _load_legacy_module():
    path = __import__("pathlib").Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue501_legacy_mission_state", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _actual_cli_snapshots(corpus):
    snapshots = []

    def visit(name, value):
        if isinstance(value, dict) and "mission_id" in value:
            snapshots.append((name, value))
        elif isinstance(value, dict):
            for child_name, child in value.items():
                visit(f"{name}.{child_name}", child)

    visit("corpus", corpus)
    return snapshots


def test_actual_cli_corpus_matches_legacy_derive_output(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import derive_next, normalize_legacy_guidance

    legacy = _load_legacy_module()
    corpus = generate_cli_state_corpus(tmp_path.resolve())
    snapshots = _actual_cli_snapshots(corpus)
    assert {name for name, _state in snapshots} == set(_CORPUS_PARITY_CONTRACTS)

    for name, legacy_state in snapshots:
        actual = normalize_legacy_guidance(legacy._derive_next_action(legacy_state))
        snapshot = decode_snapshot(canonical_json_bytes(legacy_state))
        recipe = derive_next(snapshot.state, snapshot.guidance)
        assert (
            recipe.rule_id,
            recipe.parity_status,
            recipe.legacy_dependency_ids,
        ) == _CORPUS_PARITY_CONTRACTS[name], name
        if recipe.rule_id == "route-goal":
            assert recipe.normalized is None, name
        else:
            assert recipe.normalized == actual, name


def test_transitive_dependency_recipe_is_immutable_and_never_exact(tmp_path):
    import dataclasses

    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import derive_next

    corpus = generate_cli_state_corpus(tmp_path.resolve())
    snapshot = decode_snapshot(canonical_json_bytes(corpus["provider_plan"]))

    recipe = derive_next(snapshot.state, snapshot.guidance)

    assert recipe.parity_status == "legacy-required"
    assert recipe.legacy_dependency_ids == ("A4.plan-handoff",)
    assert recipe.steps[0].owner == "A4"
    assert recipe.steps[0].kind == "external-observation"
    assert recipe.steps[0].action == "run-executor"
    assert recipe.steps[0].required_observation == "PreparedPlanHandoff"
    assert recipe.steps[0].follow_up_command == "AdvancePhase"
    assert recipe.normalized is not None
    assert isinstance(recipe.normalized.details.items, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        recipe.steps[0].owner = "K2"


def test_parity_exclusions_are_an_exact_named_dependency_inventory():
    from mission_kernel.guidance import PARITY_DEPENDENCY_INVENTORY

    assert tuple(
        (item.dependency_id, item.boundary, item.authority_inputs)
        for item in PARITY_DEPENDENCY_INVENTORY
    ) == (
        (
            "application.clock-budget-override",
            "outside-parity",
            ("$.budget_minutes", "$.started_at", "iso_now()"),
        ),
        (
            "legacy.goal-dispatch",
            "legacy-required",
            (
                "$.goal_dispatch_requested",
                "$.goal_dispatch_source",
                "$.goal_dispatch_resolution_fallback_reason",
            ),
        ),
        (
            "legacy.host-observation",
            "legacy-required",
            ("detect_host()",),
        ),
    )


def test_each_transitive_dependency_has_its_own_typed_continuation(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.guidance import derive_next

    _state_path, source = generate_cli_state_bytes(tmp_path.resolve())
    snapshot = decode_snapshot(source)

    recipe = derive_next(snapshot.state, snapshot.guidance)

    assert recipe.rule_id == "planning-inline"
    assert recipe.parity_status == "legacy-required"
    assert recipe.legacy_dependency_ids == ("A1.lifecycle", "A4.plan-handoff")
    assert tuple(
        (
            step.owner,
            step.kind,
            step.required_observation,
            step.follow_up_command,
        )
        for step in recipe.steps
    ) == (
        ("A1", "external-observation", "LifecycleObservation", "AdvancePhase"),
        ("A4", "external-observation", "PreparedPlanHandoff", "AdvancePhase"),
    )


def test_equal_rank_or_duplicate_guidance_rules_fail_definition():
    import pytest

    from mission_kernel.transitions import TransitionRule, TransitionTableError, build_transition_table

    def always(_state, _value):
        return True

    def factory(_state, _guidance, _rule_id):
        return None

    with pytest.raises(TransitionTableError) as equal_rank:
        build_transition_table(
            (
                TransitionRule("first", object, always, None, 10, always, factory),
                TransitionRule("second", object, always, None, 10, always, factory),
            )
        )
    assert equal_rank.value.code == "equal-rank-primary-tie"

    with pytest.raises(TransitionTableError) as duplicate:
        build_transition_table(
            (
                TransitionRule("same", object, always, None, 10, always, factory),
                TransitionRule("same", object, always, None, 20, always, factory),
            )
        )
    assert duplicate.value.code == "duplicate-rule-id"

    with pytest.raises(TransitionTableError) as missing_rank:
        build_transition_table(
            (
                TransitionRule(
                    "missing-rank",
                    object,
                    always,
                    None,
                    guidance_guard=always,
                    guidance_factory=factory,
                ),
            )
        )
    assert missing_rank.value.code == "incomplete-guidance-rule"


def test_incomplete_or_duplicate_continuation_contracts_fail_definition():
    from mission_kernel.transitions import (
        TransitionRule,
        TransitionTableError,
        build_transition_table,
    )

    def always(state, guidance):
        return True

    def factory(state, guidance, rule):
        return None

    with pytest.raises(TransitionTableError) as incomplete:
        build_transition_table(
            (
                TransitionRule(
                    "incomplete",
                    object,
                    always,
                    None,
                    10,
                    always,
                    factory,
                    (("A1.lifecycle", "", "AdvancePhase"),),
                ),
            )
        )
    assert incomplete.value.code == "invalid-continuation-contract"

    with pytest.raises(TransitionTableError) as duplicate:
        build_transition_table(
            (
                TransitionRule(
                    "duplicate",
                    object,
                    always,
                    None,
                    10,
                    always,
                    factory,
                    (
                        ("A1.lifecycle", "LifecycleObservation", "AdvancePhase"),
                        ("A1.lifecycle", "LifecycleObservation", "AdvancePhase"),
                    ),
                ),
            )
        )
    assert duplicate.value.code == "duplicate-continuation-contract"


def test_decide_and_guidance_share_one_named_transition_table():
    from mission_kernel.transitions import TRANSITION_TABLE

    primary_rules = [rule for rule in TRANSITION_TABLE if rule.guidance_rank is not None]

    assert all(rule.command_type is not None for rule in primary_rules)
    assert all(callable(rule.command_guard) for rule in primary_rules)
    assert all(callable(rule.guidance_guard) for rule in primary_rules)
    assert {rule.rule_id for rule in TRANSITION_TABLE if rule.reducer is not None} == {
        "advance-phase",
        "artifact-append-block",
        "artifact-export",
        "artifact-initialize",
        "artifact-record-publication",
        "artifact-render",
        "context-manifest-generate",
        "executor-handoff-begin",
        "executor-handoff-complete",
        "executor-handoff-record-step",
        "executor-handoff-reject-canonical-drift",
        "executor-handoff-verify-step",
        "progress-clear",
        "progress-update",
        "verification-record",
        "mark-halt",
        "mark-pass",
        "reactivate",
        "resume-stale",
        "set-extension-fields",  # #617 批1-a
        "specialist-selection-decline",  # #659
        "specialists-record-recommendation",
    }
    assert "advance-phase" in {rule.rule_id for rule in primary_rules}
    assert "aggregate-reviews" in {rule.rule_id for rule in primary_rules}
