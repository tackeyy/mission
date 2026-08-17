"""Issue #500: v1-v4 MissionState decode and CLI-writer corpus contract."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import (
    canonical_json_bytes,
    generate_cli_state_corpus,
    issue483_corpus,
    legacy_review_evidence,
)


@pytest.fixture(scope="module")
def cli_state_corpus(tmp_path_factory):
    return generate_cli_state_corpus(tmp_path_factory.mktemp("issue500-cli-corpus"))


def _decode(payload: dict):
    from mission_kernel import decode_mission_state

    return decode_mission_state(canonical_json_bytes(payload))


def test_cli_corpus_is_generated_from_state_files_and_covers_writer_variants(cli_state_corpus):
    assert {
        cli_state_corpus[name]["executor_handoff"]["status"]
        for name in (
            "handoff_prepared",
            "handoff_consuming",
            "handoff_consumed",
            "handoff_rejected",
        )
    } == {"prepared", "consuming", "consumed", "rejected"}
    assert len(cli_state_corpus["review_input"]["review_evidence_refs"]) == 2
    bound = cli_state_corpus["review_aggregate_and_bound_score"]["score_history"][-1]
    assert bound["score_provenance"]["review_evidence_ref"]["kind"] == "review-aggregate"
    manual = cli_state_corpus["manual_import_bound_score"]["score_history"][-1]
    assert manual["score_provenance"]["manual_evidence_ref"]["kind"] == "manual-score"
    assert cli_state_corpus["provider_plan"]["canonical_plan"]["source"] == "provider"
    assert cli_state_corpus["lease_acquired"]["fencing_epoch"] == 1
    assert cli_state_corpus["lease_taken_over"]["fencing_epoch"] == 2
    assert len(cli_state_corpus["lease_taken_over"]["lease_history"]) == 1
    assert set(cli_state_corpus["phases"]) == {
        "planning",
        "executing",
        "reviewing",
        "scoring",
        "done",
        "halted",
    }
    assert set(cli_state_corpus["terminal_outcomes"]) == {
        "completed_pass",
        "completed_evidence",
        "blocked_external",
        "awaiting_approval",
        "stale_superseded",
        "failed",
        "incomplete",
        "user_aborted",
        "routed_elsewhere",
    }


def test_cli_handoff_variants_decode_the_flattened_production_wire_shape(cli_state_corpus):
    from mission_kernel.model import (
        ConsumedHandoff,
        ConsumingHandoff,
        PreparedHandoff,
        RejectedHandoff,
    )

    expected_types = {
        "handoff_prepared": PreparedHandoff,
        "handoff_consuming": ConsumingHandoff,
        "handoff_consumed": ConsumedHandoff,
        "handoff_rejected": RejectedHandoff,
    }
    for name, expected_type in expected_types.items():
        decoded = _decode(cli_state_corpus[name])
        assert isinstance(decoded.handoff, expected_type)
        assert decoded.handoff.plan.digest == cli_state_corpus[name]["canonical_plan"]["digest"]
        assert decoded.handoff.ordered_step_ids == tuple(
            cli_state_corpus[name]["executor_handoff"]["step_ids"]
        )


def test_cli_provider_plan_variant_decodes_from_promoted_writer_output(cli_state_corpus):
    from mission_kernel.model import ProviderPlan

    decoded = _decode(cli_state_corpus["provider_plan"])
    assert isinstance(decoded.plan, ProviderPlan)
    assert decoded.plan.source_id == cli_state_corpus["provider_plan"]["canonical_plan"]["source_id"]


def test_cli_review_score_lease_phase_and_terminal_variants_decode(cli_state_corpus):
    from mission_kernel.model import (
        BoundScore,
        FencedLease,
        LegacyFindingsUnloaded,
        ReviewAggregateRef,
        ReviewInputRef,
    )

    review_state = _decode(cli_state_corpus["review_input"])
    assert all(isinstance(reference, ReviewInputRef) for reference in review_state.reviews)
    assert isinstance(review_state.findings, LegacyFindingsUnloaded)
    assert review_state.findings.review_refs == review_state.reviews

    score_state = _decode(cli_state_corpus["review_aggregate_and_bound_score"])
    assert isinstance(score_state.scores[-1], BoundScore)
    assert isinstance(score_state.scores[-1].review_evidence_ref, ReviewAggregateRef)
    assert score_state.reviews[-1] == score_state.scores[-1].review_evidence_ref

    manual_score_state = _decode(cli_state_corpus["manual_import_bound_score"])
    assert isinstance(manual_score_state.scores[-1], BoundScore)
    assert manual_score_state.scores[-1].source.value == "manual-import"
    assert manual_score_state.scores[-1].review_evidence_ref is None
    assert manual_score_state.scores[-1].manual_evidence_ref.kind == "manual-score"

    for lease_name in ("lease_acquired", "lease_taken_over"):
        assert isinstance(_decode(cli_state_corpus[lease_name]).lease, FencedLease)
    assert len(_decode(cli_state_corpus["lease_taken_over"]).lease.lease_history) == 1

    for phase, payload in cli_state_corpus["phases"].items():
        assert _decode(payload).control.phase.value == phase
    for outcome, payload in cli_state_corpus["terminal_outcomes"].items():
        assert _decode(payload).terminal_outcome.value == outcome


def test_issue483_golden_corpus_uses_literal_expected_control_and_preserves_source_bytes():
    from mission_kernel import decode_mission_state, project_legacy_document
    from mission_kernel.model import LegacyAbsentLease, LegacyScore, Phase

    expected = {
        "missing": ("completed_pass", Phase.DONE),
        "v1": ("completed_pass", Phase.DONE),
        "v2": ("stale_superseded", Phase.HALTED),
        "v3": ("stale_superseded", Phase.HALTED),
        "v4": ("completed_pass", Phase.DONE),
    }
    for label, payload in issue483_corpus().items():
        source = canonical_json_bytes(payload)
        source_hash = hashlib.sha256(source).digest()
        decoded = decode_mission_state(source)

        assert decoded.schema_origin.name.lower() == label
        assert decoded.terminal_outcome.value == expected[label][0]
        assert decoded.control.phase is expected[label][1]
        assert decoded.control.iteration == payload["iteration"]
        assert decoded.control.reviewer_count == 2
        assert decoded.control.stagnation_count == 0
        assert decoded.control.loop_active is payload["loop_active"]
        assert decoded.control.passes is payload["passes"]
        assert isinstance(decoded.lease, LegacyAbsentLease)
        if label == "v4":
            assert isinstance(decoded.scores[0], LegacyScore)
        assert hashlib.sha256(source).digest() == source_hash
        assert json.loads(project_legacy_document(decoded).decode("utf-8")) == payload


def test_empty_legacy_document_uses_design_defaults_and_halted_incomplete_phase():
    decoded = _decode({})

    assert decoded.control.iteration == 1
    assert decoded.control.max_iter is None
    assert decoded.control.threshold is None
    assert decoded.control.reviewer_count == 2
    assert decoded.control.stagnation_count == 0
    assert decoded.control.loop_active is False
    assert decoded.control.passes is False
    assert decoded.control.halt_reason == ""
    assert decoded.control.halt_category is None
    assert decoded.control.session_role.value == "implementer"
    assert decoded.terminal_outcome.value == "incomplete"
    assert decoded.control.phase.value == "halted"


@pytest.mark.parametrize("alias", ["execution", "review", "plan", "score"])
def test_legacy_phase_aliases_are_rejected_without_changing_source(alias):
    from mission_kernel.errors import MissionStateDecodeError

    source = canonical_json_bytes({"phase": alias})
    before = hashlib.sha256(source).digest()
    with pytest.raises(MissionStateDecodeError) as rejected:
        from mission_kernel import decode_mission_state

        decode_mission_state(source)
    assert rejected.value.code == "unknown-variant"
    assert rejected.value.json_path == "$.phase"
    assert hashlib.sha256(source).digest() == before


def test_legacy_unknown_terminal_outcome_fails_closed_but_remains_in_passthrough():
    from mission_kernel import project_legacy_document

    payload = {
        "passes": False,
        "loop_active": False,
        "halt_reason": "stopped",
        "halt_category": "other",
        "terminal_outcome": "future-outcome",
    }
    decoded = _decode(payload)
    assert decoded.terminal_outcome.value == "failed"
    assert decoded.legacy_passthrough.thaw()["terminal_outcome"] == "future-outcome"
    assert json.loads(project_legacy_document(decoded))["terminal_outcome"] == "failed"


def test_project_legacy_document_reprojects_typed_owned_fields_over_passthrough():
    from mission_kernel import project_legacy_document
    from mission_kernel.model import MissionIdentity

    payload = {
        "mission": "before",
        "mission_id": "mission-before",
        "session_id": "session-before",
        "unknown": {"nested": [1, 2, 3]},
    }
    decoded = _decode(payload)
    changed = replace(
        decoded,
        identity=MissionIdentity("after", "mission-after", "session-after"),
    )

    projected = json.loads(project_legacy_document(changed))
    assert projected["mission"] == "after"
    assert projected["mission_id"] == "mission-after"
    assert projected["session_id"] == "session-after"
    assert projected["unknown"] == payload["unknown"]


def test_project_legacy_document_reprojects_typed_scores_over_passthrough(cli_state_corpus):
    from mission_kernel import project_legacy_document

    decoded = _decode(cli_state_corpus["manual_import_bound_score"])
    projected = json.loads(project_legacy_document(replace(decoded, scores=())))
    assert projected["score_history"] == []


def test_v4_sparse_score_projection_preserves_writer_bytes_without_iteration_injection():
    from mission_kernel import decode_mission_state, project_legacy_document

    payload = {
        "schema_version": 4,
        "score_history": [{"composite": 4.0}],
    }
    source = canonical_json_bytes(payload)

    projected = project_legacy_document(decode_mission_state(source))

    assert projected == source


def test_legacy_passthrough_is_deeply_immutable():
    decoded = _decode({"unknown": {"nested": [1, {"value": 2}]}})
    nested = dict(decoded.legacy_passthrough.items)["unknown"]
    with pytest.raises((AttributeError, TypeError)):
        nested.items += (("extra", 3),)


@pytest.mark.parametrize(
    "status",
    [None, "open", "resolved", "accepted-risk", "not-reproducible", "future-status"],
    ids=["missing", "open", "resolved", "accepted-risk", "not-reproducible", "arbitrary"],
)
def test_legacy_review_statuses_all_normalize_to_open_and_preserve_payload(
    cli_state_corpus, status
):
    from mission_kernel import decode_legacy_review_evidence
    from mission_kernel.model import FindingStatus, OpenFinding

    reference = _decode(cli_state_corpus["review_input"]).reviews[0]
    document = legacy_review_evidence(status=status)
    if status is None:
        document["findings"][0].pop("status")
    materialized = decode_legacy_review_evidence(canonical_json_bytes(document), reference)
    finding = materialized.findings[0]

    assert isinstance(finding, OpenFinding)
    assert finding.status is FindingStatus.OPEN
    assert finding.legacy_payload.thaw().get("status") == status
