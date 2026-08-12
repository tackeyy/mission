"""Issue #375: structured review learning and materialized failure ledger."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from review_learning import (
    LearningContractError,
    failure_ledger_counts,
    reduce_failure_ledger,
    validate_review_learning,
)


def _review(*, iteration=1, perspective="A", learning=True):
    finding = {
        "id": f"{perspective}-1", "severity": "Medium", "axis": "accuracy",
        "summary": "Observed failure", "evidence": "bounded evidence", "recommendation": "fix it",
    }
    payload = {
        "schema": "mission-review/1", "iteration": iteration, "perspective": perspective,
        "scores": {"mission_achievement": 4.0, "accuracy": 4.0, "completeness": 4.0, "usability": 4.0},
        "same_score_note": "independent checks converged", "findings": [finding],
    }
    if learning:
        payload["learning_schema"] = "mission-review-learning/1"
        finding.update(cause="The validation boundary was omitted", general_fix_rule="  Validate   every boundary  ", weak_phase="execution")
    return payload


def test_marker_requires_structured_learning_fields():
    payload = _review()
    validate_review_learning(payload)
    for field in ("cause", "general_fix_rule", "weak_phase"):
        bad = json.loads(json.dumps(payload))
        bad["findings"][0].pop(field)
        with pytest.raises(LearningContractError):
            validate_review_learning(bad)
    bad = json.loads(json.dumps(payload))
    bad["findings"][0]["weak_phase"] = "testing"
    with pytest.raises(LearningContractError):
        validate_review_learning(bad)


def test_legacy_accepts_absent_learning_but_rejects_mixed_fields():
    legacy = _review(learning=False)
    validate_review_learning(legacy)
    legacy["findings"][0]["cause"] = "mixed"
    with pytest.raises(LearningContractError):
        validate_review_learning(legacy)


def test_reducer_dedupes_same_iteration_and_counts_cross_iteration_recurrence():
    first = _review(iteration=1, perspective="A")
    duplicate = _review(iteration=1, perspective="B")
    recurring = _review(iteration=2, perspective="A")
    ledger = reduce_failure_ledger([
        {"iteration": 1, "review": first, "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "a" * 64}},
        {"iteration": 1, "review": duplicate, "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "a" * 64}},
        {"iteration": 2, "review": recurring, "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "b" * 64}},
    ])
    assert ledger["schema"] == "mission-failure-ledger/1"
    assert len(ledger["patterns"]) == 1
    pattern = ledger["patterns"][0]
    assert pattern["iterations"] == [1, 2]
    assert pattern["recurrence_count"] == 1
    assert pattern["general_fix_rule"] == "validate every boundary"


def test_reducer_uses_digest_ref_only_and_never_copies_cause_or_evidence():
    ledger = reduce_failure_ledger([{
        "iteration": 1, "review": _review(),
        "review_aggregate_ref": {"kind": "review-aggregate", "path": ".mission-state/archive/private.json", "digest": "sha256:" + "c" * 64},
    }])
    serialized = json.dumps(ledger, ensure_ascii=False)
    assert "bounded evidence" not in serialized
    assert "validation boundary was omitted" not in serialized
    assert "private.json" not in serialized
    assert ledger["patterns"][0]["examples"] == [{"iteration": 1, "review_aggregate_digest": "sha256:" + "c" * 64}]


def test_failure_ledger_counts_are_text_free_and_invalid_is_observable():
    ledger = reduce_failure_ledger([{
        "iteration": 1, "review": _review(),
        "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "d" * 64},
    }])
    counts = failure_ledger_counts([{"failure_ledger": ledger}, {"failure_ledger": {"schema": "bad"}}])
    assert counts == {
        "pattern_count": 1, "recurring_pattern_count": 0,
        "weak_phase_counts": {"execution": 1}, "invalid_ledger_count": 1,
    }
    assert "validate every boundary" not in json.dumps(counts)


def test_mission_state_strict_parser_enforces_learning_marker():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_learning", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    payload = _review()
    module._validate_review_payload(payload, 1)
    payload["findings"][0].pop("cause")
    with pytest.raises(ValueError, match="cause"):
        module._validate_review_payload(payload, 1)
