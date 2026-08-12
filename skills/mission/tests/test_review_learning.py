"""Issue #375: structured review learning and materialized failure ledger."""
from __future__ import annotations

import importlib.util
import json
import subprocess
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


@pytest.mark.parametrize("field,value", [
    ("cause", ""),
    ("cause", "bad\nline"),
    ("general_fix_rule", "x" * 4097),
    ("weak_phase", 1),
])
def test_marker_rejects_empty_control_oversize_and_type_errors(field, value):
    payload = _review()
    payload["findings"][0][field] = value
    with pytest.raises(LearningContractError):
        validate_review_learning(payload)


def test_marker_rejects_unknown_learning_keys():
    payload = _review()
    payload["learning_extra"] = True
    with pytest.raises(LearningContractError):
        validate_review_learning(payload)
    payload = _review()
    payload["findings"][0]["learning_extra"] = True
    with pytest.raises(LearningContractError):
        validate_review_learning(payload)


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


def test_weak_phase_counts_iteration_occurrences_not_pattern_rows():
    ledger = reduce_failure_ledger([
        {"iteration": 1, "review": _review(iteration=1), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "1" * 64}},
        {"iteration": 2, "review": _review(iteration=2), "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "2" * 64}},
    ])
    counts = failure_ledger_counts([{"failure_ledger": ledger}])
    assert counts["pattern_count"] == 1
    assert counts["recurring_pattern_count"] == 1
    assert counts["weak_phase_counts"] == {"execution": 2}


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


def test_stats_and_audit_failure_ledger_counts_match(run_cli, tmp_path):
    run_cli("init", "learning counts", "--complexity", "Standard", cwd=tmp_path, check=True)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["failure_ledger"] = reduce_failure_ledger([{
        "iteration": 1, "review": _review(),
        "review_aggregate_ref": {"kind": "review-aggregate", "digest": "sha256:" + "e" * 64},
    }])
    state_path.write_text(json.dumps(state), encoding="utf-8")

    stats = json.loads(run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True).stdout)
    audit_path = Path(__file__).resolve().parents[3] / "scripts" / "mission-audit.py"
    audit = subprocess.run(
        [sys.executable, str(audit_path), "--root", str(tmp_path), "--json"],
        capture_output=True, text=True, check=True,
    )
    audit_counts = json.loads(audit.stdout)["failure_ledger_counts"]
    assert stats["failure_ledger_counts"] == audit_counts
    assert audit_counts["weak_phase_counts"] == {"execution": 1}
    assert "general_fix_rule" not in json.dumps(audit_counts)


def test_generic_set_cannot_modify_failure_ledger(run_cli, tmp_path):
    run_cli("init", "immutable learning ledger", "--complexity", "Standard", cwd=tmp_path, check=True)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    before = state_path.read_bytes()
    result = run_cli("set", 'failure_ledger={"schema":"mission-failure-ledger/1","patterns":[]}', cwd=tmp_path)
    assert result.returncode == 2
    assert "変更不可" in result.stderr
    assert state_path.read_bytes() == before
