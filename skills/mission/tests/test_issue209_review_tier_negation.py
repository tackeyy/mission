"""Issue #209: review tier actual-operation signals understand local context.

The public ``derive_review_tier`` tuple and ``review_tier_signals`` string list
remain backward compatible.  Additive decision details explain every matched
signal, including actual-operation candidates suppressed by a clear negation.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import time

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mission_state_issue209", MISSION_STATE_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _decision(mission: str, complexity: str = "Simple", risk: str | None = None):
    return _load_module().derive_review_tier_decision(mission, complexity, risk)


def _details(decision: dict, *, category: str = "actual-operation") -> list[dict]:
    return [item for item in decision["signal_details"] if item["category"] == category]


def test_affirmative_actual_operation_is_kept_with_provenance():
    decision = _decision("本番へ deploy する")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    detail = next(item for item in _details(decision) if item["keyword"] == "deploy")
    assert detail["decision"] == "included"
    assert detail["reason"] == "affirmative-actual-operation"
    assert detail["match"] == "deploy"
    assert "deploy" in detail["context"]
    assert detail["source"] == "mission_text"
    assert decision["mission_text"][detail["start"] : detail["end"]] == detail["match"]


def test_clearly_negated_actual_operation_is_suppressed():
    decision = _decision("本番への deploy は実行しない")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert _details(decision)
    assert {item["decision"] for item in _details(decision)} == {"suppressed"}
    assert {item["reason"] for item in _details(decision)} == {"negated-actual-operation"}


def test_double_negation_stays_conservative():
    decision = _decision("本番へ deploy しないわけではない")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "uncertain-or-double-negation" for item in _details(decision))


@pytest.mark.parametrize(
    "mission",
    [
        "we will not not deploy",
        "we should not not release",
        "we do not not publish",
        "we won't not deploy",
        "we won’t not deploy",
        "we don't not release",
        "we don’t not publish",
        "we can't not deploy",
        "we cannot not deploy",
    ],
)
def test_english_prefixed_double_negation_stays_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert any(
        item["reason"] == "uncertain-or-double-negation"
        for item in _details(decision)
    )


@pytest.mark.parametrize(
    ("mission", "expected_tier", "expected_reason"),
    [
        ("we will not deploy", "light", "negated-actual-operation"),
        ("we won't deploy", "light", "negated-actual-operation"),
        ("we won’t deploy", "light", "negated-actual-operation"),
        ("we cannot deploy", "light", "negated-actual-operation"),
        ("we will deploy", "full", "affirmative-actual-operation"),
    ],
)
def test_english_prefixed_negation_keeps_simple_and_affirmative_regression(
    mission, expected_tier, expected_reason
):
    decision = _decision(mission)

    assert decision["tier"] == expected_tier
    assert expected_reason in {item["reason"] for item in _details(decision)}


def test_conditional_actual_operation_stays_conservative():
    decision = _decision("必要なら本番へ deploy する")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "conditional-or-uncertain-context" for item in _details(decision))


def test_quoted_operation_without_explicit_non_execution_stays_conservative():
    decision = _decision("手順書には「本番へ deploy する」と書かれている")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "quoted-context-conservative" for item in _details(decision))


def test_quoted_operation_is_suppressed_only_when_quote_only_intent_is_explicit():
    decision = _decision("「本番へ deploy する」という文言を手順書に引用する")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert _details(decision)
    assert {item["reason"] for item in _details(decision)} == {"quoted-non-operation"}


def test_explicit_execution_overrides_quoted_example():
    decision = _decision("手順書の「本番へ deploy する」を実際に実行する")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["decision"] == "included" for item in _details(decision))


def test_distant_negation_does_not_suppress_a_separate_actual_operation():
    decision = _decision("データ削除は行わない。代わりに本番へ deploy する")

    assert decision["tier"] == "full"
    deleted = [item for item in _details(decision) if item["keyword"] == "データ削除"]
    deployed = [item for item in _details(decision) if item["keyword"] == "deploy"]
    assert deleted and {item["decision"] for item in deleted} == {"suppressed"}
    assert deployed and {item["decision"] for item in deployed} == {"included"}
    assert "irreversible-keyword:deploy" in decision["signals"]


def test_global_explicit_non_operation_statement_suppresses_prior_candidate():
    decision = _decision("deploy 手順を調査する。実操作は行わない")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert {item["decision"] for item in _details(decision)} == {"suppressed"}
    assert {item["reason"] for item in _details(decision)} == {"global-explicit-non-operation"}


def test_global_non_operation_suppresses_all_candidates_in_the_logical_unit():
    decision = _decision("deploy to production の手順を調査する。実操作は行わない")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert {item["keyword"] for item in _details(decision)} == {"deploy", "production"}
    assert {item["decision"] for item in _details(decision)} == {"suppressed"}


def test_global_non_operation_does_not_leak_across_paragraph_or_list_item():
    decision = _decision(
        "deploy 手順を調査する。実操作は行わない\n\n- production へ deploy する"
    )

    deploy_details = [item for item in _details(decision) if item["keyword"] == "deploy"]
    production = [item for item in _details(decision) if item["keyword"] == "production"]
    assert decision["tier"] == "full"
    assert [item["decision"] for item in deploy_details] == ["suppressed", "included"]
    assert production and {item["decision"] for item in production} == {"included"}


def test_quote_and_actual_execution_flags_are_independent_across_units():
    decision = _decision(
        "「deploy する」という文言を引用する\n\n- production へ deploy を実際に実行する"
    )

    deploy_details = [item for item in _details(decision) if item["keyword"] == "deploy"]
    assert decision["tier"] == "full"
    assert [item["decision"] for item in deploy_details] == ["suppressed", "included"]
    assert [item["reason"] for item in deploy_details] == [
        "quoted-non-operation",
        "affirmative-actual-operation",
    ]


def test_quote_only_marker_does_not_leak_to_a_different_unit():
    decision = _decision(
        "手順書には「deploy する」と書かれている\n\n- 「release する」は引用するだけ"
    )

    deploy = [item for item in _details(decision) if item["keyword"] == "deploy"]
    release = [item for item in _details(decision) if item["keyword"] == "release"]
    assert deploy and deploy[0]["reason"] == "quoted-context-conservative"
    assert release and release[0]["reason"] == "quoted-non-operation"


def test_global_non_operation_double_negation_stays_conservative():
    decision = _decision("deploy 手順を調査する。実操作は行わないわけではない")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert {item["decision"] for item in _details(decision)} == {"included"}


def test_negated_exclusion_marker_stays_conservative():
    decision = _decision("deploy は禁止ではない")

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "uncertain-or-double-negation" for item in _details(decision))


def test_every_occurrence_is_evaluated_when_same_keyword_has_mixed_intent():
    decision = _decision("deploy しないが、deploy する")

    deploy_details = [item for item in _details(decision) if item["keyword"] == "deploy"]
    assert decision["tier"] == "full"
    assert decision["signals"] == ["irreversible-keyword:deploy"]
    assert [item["decision"] for item in deploy_details] == ["suppressed", "included"]
    assert [item["match"] for item in deploy_details] == ["deploy", "deploy"]


@pytest.mark.parametrize(
    ("mission", "included_keyword", "suppressed_keyword"),
    [
        ("deploy しないが deploy する", "deploy", "deploy"),
        ("deploy しないけど deploy する", "deploy", "deploy"),
        ("deploy する一方、データ削除はしない", "deploy", "データ削除"),
        ("deploy and do not delete data", "deploy", "delete"),
        ("do not deploy and then deploy", "deploy", "deploy"),
    ],
)
def test_negation_is_anchored_to_its_operation_across_conjunctions(
    mission, included_keyword, suppressed_keyword
):
    decision = _decision(mission)

    included = [
        item
        for item in _details(decision)
        if item["keyword"] == included_keyword and item["decision"] == "included"
    ]
    suppressed = [
        item
        for item in _details(decision)
        if item["keyword"] == suppressed_keyword and item["decision"] == "suppressed"
    ]
    assert decision["tier"] == "full"
    assert included
    assert suppressed


@pytest.mark.parametrize(
    "mission",
    [
        "deploy はしない、release する",
        "deploy しないものの release する",
        "deploy しない代わりに release する",
        "do not deploy, release instead",
        "do not deploy then release",
        "do not deploy while release proceeds",
    ],
)
def test_negation_requires_direct_operation_anchor_even_with_unknown_connector(mission):
    decision = _decision(mission)

    deploy = [item for item in _details(decision) if item["keyword"] == "deploy"]
    release = [item for item in _details(decision) if item["keyword"] == "release"]
    assert decision["tier"] == "full"
    assert deploy and {item["decision"] for item in deploy} == {"suppressed"}
    assert release and {item["decision"] for item in release} == {"included"}


@pytest.mark.parametrize(
    "mission",
    [
        "we will not perform a production migration",
        "we will not execute a production migration",
        "production migration will not be performed",
        "publish will not be executed",
        "migration should not be performed",
        "we do not perform migration",
    ],
)
def test_english_perform_execute_and_passive_negation_are_explicit(mission):
    decision = _decision(mission)

    actual = _details(decision)
    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert actual and {item["decision"] for item in actual} == {"suppressed"}
    assert {item["reason"] for item in actual} == {"negated-actual-operation"}


@pytest.mark.parametrize(
    "mission",
    [
        "本番環境に deploy しない",
        "本番へ deploy する予定はない",
        "we will not deploy to our target production environment",
        "we will not deploy to the target production environment",
        "本番の release はしない",
        "deploy が行われない",
        "we won’t deploy",
        "we are not going to deploy",
        "deployment is not planned",
        "we cannot deploy",
    ],
)
def test_additional_direct_negation_forms_are_suppressed(mission):
    decision = _decision(mission)

    actual = _details(decision)
    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert actual and {item["decision"] for item in actual} == {"suppressed"}


@pytest.mark.parametrize(
    "mission",
    ["we won't deploy unless approval is granted", "we won't deploy without approval"],
)
def test_conditional_exception_to_direct_negation_stays_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "conditional-or-uncertain-context" for item in _details(decision))


@pytest.mark.parametrize(
    "mission",
    [
        "do not deploy unless approved",
        "deployment will not be performed unless approved",
        "should not release unless approval is granted",
        "承認されない限り deployしない",
        "承認時以外は deployしない",
        "deployしない。ただし承認時を除く",
    ],
)
def test_direct_negation_with_exception_scope_stays_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert any(
        item["reason"] == "conditional-or-uncertain-context"
        for item in _details(decision)
    )


@pytest.mark.parametrize(
    "mission",
    [
        "deploy しない予定ではない",
        "deploy しない予定はない",
        "deploy しない方針ではない",
        "deploy を行わない計画ではない",
        "deploy しないとは言っていない",
    ],
)
def test_negated_non_operation_intent_stays_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert any(
        item["reason"] == "uncertain-or-double-negation"
        for item in _details(decision)
    )


@pytest.mark.parametrize(
    "mission",
    [
        "deployしないこともない",
        "deployしないでもない",
        "deployしないとも言えない",
        "deployしないとは断言できない",
    ],
)
def test_additional_japanese_negation_reversals_stay_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert any(
        item["reason"] == "uncertain-or-double-negation"
        for item in _details(decision)
    )


@pytest.mark.parametrize(
    "mission",
    [
        "原則 deployしない",
        "deployしないことがある",
        "緊急時を除き deployしない",
        "例外として deployしない",
    ],
)
def test_japanese_exception_cues_keep_direct_negation_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert any(
        item["reason"] == "conditional-or-uncertain-context"
        for item in _details(decision)
    )


def test_separate_japanese_negations_do_not_form_a_false_reversal():
    mission = "deployしないし、releaseもしない"
    decision = _decision(mission)

    actual = _details(decision)
    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert {item["keyword"] for item in actual} == {"deploy", "release"}
    assert {item["decision"] for item in actual} == {"suppressed"}
    assert {item["reason"] for item in actual} == {"negated-actual-operation"}


@pytest.mark.parametrize(
    "mission",
    [
        "実操作は行わない。ただし release する",
        "Actual operations will not be performed. However release to production",
        "実操作は行わない\n> release する",
        "実操作は行わない\n## Execute\nrelease する",
    ],
)
def test_global_non_operation_does_not_leak_into_exception_or_structural_unit(mission):
    decision = _decision(mission)

    release = [item for item in _details(decision) if item["keyword"] == "release"]
    assert decision["tier"] == "full"
    assert release and {item["decision"] for item in release} == {"included"}


@pytest.mark.parametrize(
    "mission",
    [
        "Actual operations will not be performed. Release to production.",
        "Actual operations will not be performed; release to production",
        "Actual operations will not be performed, except we will deploy",
        "Actual operations will not be performed\nRelease to production",
        "Actual operations will not be performed, but we will deploy",
        "実操作は行わない。release する",
        "実操作は行わない。だが release する",
        "実操作は行わない。例外として release する",
        "実操作は行わないが release する",
    ],
)
def test_global_non_operation_keeps_later_contradictory_operation_conservative(mission):
    decision = _decision(mission)

    actual = _details(decision)
    assert decision["tier"] == "full"
    assert actual and any(item["decision"] == "included" for item in actual)


def test_global_non_operation_still_suppresses_prior_meta_candidate():
    decision = _decision("deploy 手順を調査する。実操作は行わない")

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert {item["decision"] for item in _details(decision)} == {"suppressed"}


@pytest.mark.parametrize(
    "mission",
    [
        "Deploy now. Actual operations will not be performed.",
        "Perform the release. Actual operations will not be performed.",
        "deployしてください。実操作は行わない",
        "deployを行います。実操作は行わない",
        "releaseせよ。実操作は行わない",
    ],
)
def test_global_non_operation_does_not_suppress_prior_non_meta_operation(mission):
    decision = _decision(mission)

    actual = _details(decision)
    assert decision["tier"] == "full"
    assert actual and {item["decision"] for item in actual} == {"included"}


def test_global_non_operation_suppresses_meta_candidate_on_either_side():
    for mission in (
        "deploy 手順を調査する。実操作は行わない",
        "実操作は行わない。deploy 手順を調査する",
        "No actual operations. Review the deploy procedure.",
    ):
        decision = _decision(mission)
        assert decision["tier"] == "light"
        assert decision["signals"] == []
        assert {item["reason"] for item in _details(decision)} == {
            "global-explicit-non-operation"
        }


@pytest.mark.parametrize(
    "mission",
    [
        "「deploy」は引用するだけだが「release」は実行する",
        "「deploy」は引用するだけ。「release」は実行する",
        'only quote "deploy" but execute "release"',
        'only quote "deploy" but actually execute "release"',
    ],
)
def test_quote_only_and_execution_intent_are_anchored_per_match(mission):
    decision = _decision(mission)

    deploy = [item for item in _details(decision) if item["keyword"] == "deploy"]
    release = [item for item in _details(decision) if item["keyword"] == "release"]
    assert decision["tier"] == "full"
    assert deploy and {item["decision"] for item in deploy} == {"suppressed"}
    assert release and {item["decision"] for item in release} == {"included"}
    assert {item["reason"] for item in deploy} == {"quoted-non-operation"}
    assert {item["reason"] for item in release} == {"affirmative-actual-operation"}


def test_execution_word_inside_quote_does_not_override_quote_only_intent():
    decision = _decision('only quote "do not execute migration"')

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    migration = [item for item in _details(decision) if item["keyword"] == "migration"]
    assert migration and {item["decision"] for item in migration} == {"suppressed"}
    assert {item["reason"] for item in migration} == {"quoted-non-operation"}


@pytest.mark.parametrize(
    ("mission", "quoted_keyword", "executed_keyword"),
    [
        ('only quote "deploy", execute release', "deploy", "release"),
        ("引用するだけ: 「deploy」、releaseを実行する", "deploy", "release"),
    ],
)
def test_unrelated_execution_does_not_override_quoted_non_operation(
    mission, quoted_keyword, executed_keyword
):
    decision = _decision(mission)

    quoted = next(item for item in _details(decision) if item["keyword"] == quoted_keyword)
    executed = next(item for item in _details(decision) if item["keyword"] == executed_keyword)
    assert decision["tier"] == "full"
    assert (quoted["decision"], quoted["reason"]) == (
        "suppressed",
        "quoted-non-operation",
    )
    assert executed["decision"] == "included"
    for detail in (quoted, executed):
        assert mission[detail["start"] : detail["end"]] == detail["match"]


@pytest.mark.parametrize(
    "mission",
    [
        "手順書の「本番へ deploy する」を実際に実行する",
        'execute the quoted "deploy" command',
    ],
)
def test_execution_directly_targeting_quoted_command_is_affirmative(mission):
    decision = _decision(mission)

    deploy = next(item for item in _details(decision) if item["keyword"] == "deploy")
    assert decision["tier"] == "full"
    assert (deploy["decision"], deploy["reason"]) == (
        "included",
        "affirmative-actual-operation",
    )
    assert mission[deploy["start"] : deploy["end"]] == deploy["match"]


@pytest.mark.parametrize(
    "mission",
    [
        'only quote "deploy", "release" will be executed',
        'only quote "deploy", "release" must be performed',
    ],
)
def test_passive_execution_targets_only_the_immediately_preceding_quote(mission):
    decision = _decision(mission)

    deploy = next(item for item in _details(decision) if item["keyword"] == "deploy")
    release = next(item for item in _details(decision) if item["keyword"] == "release")
    assert decision["tier"] == "full"
    assert (deploy["decision"], deploy["reason"]) == (
        "suppressed",
        "quoted-non-operation",
    )
    assert (release["decision"], release["reason"]) == (
        "included",
        "affirmative-actual-operation",
    )
    for detail in (deploy, release):
        assert mission[detail["start"] : detail["end"]] == detail["match"]


@pytest.mark.parametrize(
    "mission",
    [
        'only quote "deploy", then execute it',
        'only quote "deploy" and execute it',
        'only quote "deploy", then apply it',
        'only quote "deploy", then proceed with it',
        "引用するだけ: 「deploy」。その後それを実行する",
        "引用するだけ:「deploy」、その後適用する",
        "引用するだけ:「deploy」、その後進める",
    ],
)
def test_ambiguous_pronoun_execution_keeps_quoted_candidate_conservative(mission):
    decision = _decision(mission)

    deploy = next(item for item in _details(decision) if item["keyword"] == "deploy")
    assert decision["tier"] == "full"
    assert (deploy["decision"], deploy["reason"]) == (
        "included",
        "ambiguous-execution-reference",
    )
    assert mission[deploy["start"] : deploy["end"]] == deploy["match"]


@pytest.mark.parametrize(
    "mission",
    [
        "Review the deploy procedure, then execute it. "
        "Actual operations will not be performed.",
        "Review the deploy procedure, then carry it out. "
        "Actual operations will not be performed.",
        "deploy手順を調査してから実行する。実操作は行わない",
        "deploy手順を調査して実行する。実操作は行わない",
        "deploy手順を調査後に実行する。実操作は行わない",
    ],
)
def test_later_ambiguous_execution_vetoes_global_meta_suppression(mission):
    decision = _decision(mission)

    actual = _details(decision)
    assert decision["tier"] == "full"
    assert actual and {item["decision"] for item in actual} == {"included"}
    assert "ambiguous-execution-reference" in {item["reason"] for item in actual}
    for detail in actual:
        assert mission[detail["start"] : detail["end"]] == detail["match"]


@pytest.mark.parametrize(
    "mission",
    [
        "Review the deploy procedure, then proceed with it. "
        "Actual operations will not be performed.",
        "Review the deploy procedure, then apply it. "
        "Actual operations will not be performed.",
        "deploy手順を調査して適用する。実操作は行わない",
        "deploy手順を調査し、そのまま進める。実操作は行わない",
    ],
)
def test_unknown_trailing_action_prevents_global_meta_only_suppression(mission):
    decision = _decision(mission)

    actual = _details(decision)
    assert decision["tier"] == "full"
    assert actual and {item["decision"] for item in actual} == {"included"}
    for detail in actual:
        assert mission[detail["start"] : detail["end"]] == detail["match"]


@pytest.mark.parametrize(
    "mission",
    [
        'only quote "deploy, then execute it"',
        "引用するだけ:「deployして実行する」",
    ],
)
def test_execution_cue_inside_quote_does_not_veto_quote_only_suppression(mission):
    decision = _decision(mission)

    deploy = next(item for item in _details(decision) if item["keyword"] == "deploy")
    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert (deploy["decision"], deploy["reason"]) == (
        "suppressed",
        "quoted-non-operation",
    )
    assert mission[deploy["start"] : deploy["end"]] == deploy["match"]


@pytest.mark.parametrize(
    "mission",
    [
        "本番へ deploy する可能性がある",
        "本番へ deploy するかもしれない",
        "本番へ deploy するかは未確定",
    ],
)
def test_uncertain_context_stays_conservative(mission):
    decision = _decision(mission)

    assert decision["tier"] == "full"
    assert "irreversible-keyword:deploy" in decision["signals"]
    assert any(item["reason"] == "conditional-or-uncertain-context" for item in _details(decision))


@pytest.mark.parametrize(
    ("mission", "expected_tier", "expected_reason"),
    [
        ("do not deploy to production", "light", "negated-actual-operation"),
        ("do not deploy without approval", "full", "conditional-or-uncertain-context"),
        ("deployment is not impossible", "full", "uncertain-or-double-negation"),
        ("deployment is out of scope", "light", "negated-actual-operation"),
        ("deploy only if approval is granted", "full", "conditional-or-uncertain-context"),
        ('the guide says "deploy to production"', "full", "quoted-context-conservative"),
    ],
)
def test_english_negation_uncertainty_condition_and_quote(
    mission, expected_tier, expected_reason
):
    decision = _decision(mission)

    assert decision["tier"] == expected_tier
    assert expected_reason in {item["reason"] for item in _details(decision)}


@pytest.mark.parametrize(
    "mission",
    ["deploy せず調査だけ行う", "deploy しません", "deploy を禁止する", "deploy は対象外とする"],
)
def test_japanese_explicit_non_operation_variants_are_suppressed(mission):
    decision = _decision(mission)

    assert decision["tier"] == "light"
    assert decision["signals"] == []
    assert {item["reason"] for item in _details(decision)} == {"negated-actual-operation"}


def test_only_actual_operation_signals_are_suppressed_by_negation():
    decision = _decision("API token は更新しない")

    assert decision["tier"] == "full"
    assert "security-keyword:api token" in decision["signals"]
    security = _details(decision, category="security")
    assert security and {item["decision"] for item in security} == {"included"}
    assert {item["reason"] for item in security} == {"security-context-conservative"}


def test_base_critical_and_high_risk_escalators_are_never_downgraded():
    critical = _decision("本番への deploy は実行しない", complexity="Critical")
    high_risk = _decision("small refactor", risk="high")

    assert critical["base_tier"] == "full"
    assert critical["tier"] == "full"
    assert critical["signals"] == []
    assert high_risk["tier"] == "full"
    assert high_risk["signals"] == ["task_profile.risk=high"]
    assert any(
        item["reason"] == "task-profile-high-risk" and item["decision"] == "included"
        for item in high_risk["signal_details"]
    )


def test_existing_tuple_api_and_string_signal_list_remain_compatible():
    module = _load_module()

    tier, signals = module.derive_review_tier("deploy to production", "Simple")

    assert tier == "full"
    assert signals == ["irreversible-keyword:deploy", "irreversible-keyword:production"]
    assert all(isinstance(signal, str) for signal in signals)


def test_legacy_signal_order_and_same_keyword_deduplication_are_preserved():
    module = _load_module()

    tier, signals = module.derive_review_tier(
        "production deploy, then deploy; API token and secret", "Simple"
    )

    assert tier == "full"
    assert signals == [
        "irreversible-keyword:deploy",
        "irreversible-keyword:production",
        "security-keyword:secret",
        "security-keyword:api token",
    ]


def test_init_persists_additive_signal_details_for_audit(run_cli, tmp_path):
    result = run_cli(
        "init",
        "本番への deploy は実行しない",
        "--complexity",
        "Simple",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    assert state["review_tier"] == "light"
    assert state["review_tier_signals"] == []
    assert all(isinstance(signal, str) for signal in state["review_tier_signals"])
    details = state["review_tier_signal_details"]
    assert details and {item["decision"] for item in details} == {"suppressed"}
    assert all(
        {
            "signal",
            "category",
            "keyword",
            "match",
            "context",
            "decision",
            "reason",
            "source",
            "start",
            "end",
        }
        <= item.keys()
        for item in details
    )


def test_complexity_rederive_refreshes_additive_signal_details(run_cli, tmp_path):
    run_cli(
        "init",
        "本番への deploy は実行しない",
        "--complexity",
        "Simple",
        cwd=tmp_path,
        check=True,
    )

    result = run_cli("set", "complexity=Standard", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["review_tier"] == "standard"
    assert state["review_tier_signals"] == []
    assert state["review_tier_signal_details"]
    assert {item["reason"] for item in state["review_tier_signal_details"]} == {
        "negated-actual-operation"
    }


def test_get_exposes_additive_signal_details_as_public_audit_output(run_cli, tmp_path):
    run_cli(
        "init",
        "本番への deploy は実行しない",
        "--complexity",
        "Simple",
        cwd=tmp_path,
        check=True,
    )

    result = run_cli("get", cwd=tmp_path, check=True)
    output = json.loads(result.stdout)

    assert output["review_tier_signal_details"]
    assert output["review_tier_signal_details"][0]["decision"] == "suppressed"


def test_user_override_preserves_auto_signal_audit_meaning(run_cli, tmp_path):
    run_cli(
        "init", "deploy to production", "--complexity", "Simple", cwd=tmp_path, check=True
    )
    before = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    run_cli("set", "review_tier=light", cwd=tmp_path, check=True)
    after = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    assert after["review_tier_source"] == "user"
    assert after["review_tier_signal_details"] == before["review_tier_signal_details"]
    assert after["review_tier_signals"] == before["review_tier_signals"]


def test_legacy_state_without_details_supports_set_next_and_get(run_cli, state_dir):
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text())
    state["mission"] = "本番への deploy は実行しない"
    state["review_tier"] = "full"
    state["review_tier_source"] = "auto"
    state["review_tier_signals"] = ["irreversible-keyword:deploy"]
    state.pop("review_tier_signal_details", None)
    state_file.write_text(json.dumps(state))

    assert run_cli("get", cwd=state_dir.parent).returncode == 0
    assert run_cli("next", cwd=state_dir.parent).returncode == 0
    result = run_cli("set", "complexity=Simple", cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    updated = json.loads(state_file.read_text())
    assert updated["review_tier"] == "light"
    assert updated["review_tier_signals"] == []
    assert updated["review_tier_signal_details"]


def test_adversarial_long_mission_is_evaluated_without_regex_blowup():
    mission = ("ordinary planning context; " * 8_000) + "do not deploy"

    started = time.perf_counter()
    decision = _decision(mission)
    elapsed = time.perf_counter() - started

    assert decision["tier"] == "light"
    assert elapsed < 2.0


def test_repeated_keyword_context_lookup_scales_below_quadratic():
    def elapsed_for(count: int) -> tuple[float, dict]:
        started = time.perf_counter()
        decision = _decision("deploy. " * count)
        return time.perf_counter() - started, decision

    small_elapsed, _ = elapsed_for(1_000)
    large_elapsed, large = elapsed_for(4_000)

    deploy_details = [item for item in _details(large) if item["keyword"] == "deploy"]
    assert len(deploy_details) == 4_000
    assert large_elapsed < 2.5
    assert large_elapsed < (small_elapsed * 8) + 0.05


def test_dense_no_boundary_context_analysis_scales_below_quadratic():
    def elapsed_for(count: int) -> tuple[float, dict]:
        started = time.perf_counter()
        decision = _decision("deploy " * count)
        return time.perf_counter() - started, decision

    small_elapsed, _ = elapsed_for(1_000)
    large_elapsed, large = elapsed_for(4_000)

    deploy_details = [item for item in _details(large) if item["keyword"] == "deploy"]
    assert len(deploy_details) == 4_000
    assert large_elapsed < 2.5
    assert large_elapsed < (small_elapsed * 8) + 0.05


def test_dense_global_markers_and_prior_candidates_scale_below_quadratic():
    mission = ("review deploy procedure. " * 4_000) + (
        "Actual operations will not be performed. " * 4_000
    )

    started = time.perf_counter()
    decision = _decision(mission)
    elapsed = time.perf_counter() - started

    deploy_details = [item for item in _details(decision) if item["keyword"] == "deploy"]
    assert decision["tier"] == "light"
    assert len(deploy_details) == 4_000
    assert elapsed < 2.5
