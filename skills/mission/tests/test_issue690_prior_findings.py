"""Issue #690: prior_findings must be supplied by the production path.

The bounded context manifest projects ``prior_findings`` from
``score_history[*].findings_summary``.  Before this issue nothing wrote that
field: every occurrence outside the projection was a test that synthesised the
state it then asserted on, so the feature stayed green while producing an empty
manifest in production.

These tests drive the value through the real path -- ``push-score`` derives it
from the digest-verified review aggregate, ``context-manifest`` consumes it --
so a missing producer fails here rather than passing unnoticed.
"""

from __future__ import annotations

import json

import pytest

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.4,
    "completeness": 4.3,
    "usability": 4.2,
}


def _review_with_findings(perspective, findings):
    review = canonical_review(ITEMS, perspective=perspective)
    review["findings"] = findings
    return review


def _write_evidence(state_dir, findings):
    return write_canonical_review_aggregate(
        state_dir.parent,
        [_review_with_findings("A", findings)],
        name_prefix="prior-findings",
    )


def _write_scoring_json(tmp_path, evidence):
    evidence_path, ref, claim = evidence
    payload = {
        "items": claim["items"],
        "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "notes": "issue-690 payload",
        "findings_evidence_path": str(evidence_path),
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": ref,
            "revision_scope": ref["revision_scope"],
        },
    }
    path = tmp_path / "scoring.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _push(state_dir, run_cli, tmp_path, findings):
    evidence = _write_evidence(state_dir, findings)
    scoring = _write_scoring_json(tmp_path, evidence)
    run_cli(
        "push-score", "--iteration", "1", "--scoring-json", str(scoring),
        cwd=state_dir.parent, check=True,
    )


def _manifest(state_dir, run_cli, tmp_path, iteration=2):
    out = tmp_path / "manifest.json"
    run_cli(
        "context-manifest", "--iteration", str(iteration), "--out", str(out),
        cwd=state_dir.parent, check=True,
    )
    return json.loads(out.read_text(encoding="utf-8"))


MEDIUM_FINDING = {
    "id": "A-1",
    "severity": "Medium",
    "axis": "accuracy",
    "summary": "境界条件が未検証",
    "evidence": "lib/x.py:10 -- 引用",
    "recommendation": "境界のテストを足す",
}


def test_push_score_derives_findings_summary_from_verified_archive(
    state_dir, run_cli, read_state, tmp_path
):
    """push-score stores a projection of the archive's findings, not the payload's."""
    _push(state_dir, run_cli, tmp_path, [MEDIUM_FINDING])

    latest = read_state(state_dir)["score_history"][-1]
    assert latest["findings_summary_source"] == "review-aggregate"
    assert latest["findings_summary"] == [
        {
            "id": "A-1",
            "severity": "Medium",
            "axis": "accuracy",
            "summary": "境界条件が未検証",
        }
    ]


def test_push_score_records_empty_summary_when_archive_has_no_findings(
    state_dir, run_cli, read_state, tmp_path
):
    """An empty projection is distinguishable from a missing producer."""
    _push(state_dir, run_cli, tmp_path, [])

    latest = read_state(state_dir)["score_history"][-1]
    assert latest["findings_summary"] == []
    assert latest["findings_summary_source"] == "review-aggregate"


def test_context_manifest_prior_findings_come_from_production_path(
    state_dir, run_cli, tmp_path
):
    """The end-to-end path fills prior_findings; a missing producer fails here."""
    _push(state_dir, run_cli, tmp_path, [MEDIUM_FINDING])

    manifest = _manifest(state_dir, run_cli, tmp_path)

    assert manifest["prior_findings_status"] == "complete"
    assert manifest["prior_findings"] == [
        {
            "id": "A-1",
            "severity": "Medium",
            "axis": "accuracy",
            "summary": "境界条件が未検証",
        }
    ]


def test_context_manifest_status_no_history_when_nothing_scored(
    state_dir, run_cli, tmp_path
):
    """Empty is expected before the first score; say so rather than looking broken."""
    manifest = _manifest(state_dir, run_cli, tmp_path, iteration=1)

    assert manifest["prior_findings_status"] == "no-history"
    assert manifest["prior_findings"] == []


def test_context_manifest_status_partial_for_entry_without_a_producer(
    state_dir, run_cli, read_state, tmp_path
):
    """Entries written before this change carry no summary; report that as partial."""
    _push(state_dir, run_cli, tmp_path, [MEDIUM_FINDING])
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    entry = state["score_history"][-1]
    entry.pop("findings_summary", None)
    entry.pop("findings_summary_source", None)
    state_file.write_text(json.dumps(state), encoding="utf-8")

    manifest = _manifest(state_dir, run_cli, tmp_path)

    assert manifest["prior_findings_status"] == "partial"
    assert manifest["prior_findings"] == []


def _corrupt_latest_entry(state_dir, mutate):
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    mutate(state["score_history"][-1])
    state_file.write_text(json.dumps(state), encoding="utf-8")


def _set_source(value):
    def mutate(entry):
        entry["findings_summary_source"] = value
    return mutate


def test_context_manifest_rejects_a_source_marker_it_did_not_write(
    state_dir, run_cli, tmp_path
):
    """Only the canonical marker counts; any other truthy value is not a producer."""
    _push(state_dir, run_cli, tmp_path, [MEDIUM_FINDING])
    _corrupt_latest_entry(state_dir, _set_source("payload-forged"))

    assert _manifest(state_dir, run_cli, tmp_path)["prior_findings_status"] == "partial"


def test_context_manifest_is_partial_when_the_marker_has_no_summary_beside_it(
    state_dir, run_cli, tmp_path
):
    """A marker without the field it vouches for is not a supplied entry."""
    _push(state_dir, run_cli, tmp_path, [MEDIUM_FINDING])
    _corrupt_latest_entry(state_dir, lambda entry: entry.pop("findings_summary"))

    manifest = _manifest(state_dir, run_cli, tmp_path)

    assert manifest["prior_findings_status"] == "partial"
    assert manifest["prior_findings"] == []


@pytest.mark.parametrize(
    "summary",
    [
        [1],
        [{"severity": "Medium", "axis": "accuracy"}],
        [{"id": "A-1", "axis": "accuracy"}],
        [{"id": 1, "severity": "Medium", "axis": "accuracy"}],
    ],
    ids=["not-a-mapping", "missing-id", "missing-severity", "id-not-a-string"],
)
def test_context_manifest_is_partial_when_a_projected_finding_is_malformed(
    state_dir, run_cli, tmp_path, summary
):
    """complete must mean the projection is usable, not merely present."""
    _push(state_dir, run_cli, tmp_path, [MEDIUM_FINDING])

    def mutate(entry):
        entry["findings_summary"] = summary

    _corrupt_latest_entry(state_dir, mutate)

    assert _manifest(state_dir, run_cli, tmp_path)["prior_findings_status"] == "partial"


def test_projection_rejects_input_the_review_contract_would_not_produce():
    """The helper validates on its own; it does not lean on its current caller."""
    from scoring_provenance import project_findings_summary

    valid = {"findings": [{"id": "A-1", "severity": "Medium", "axis": "accuracy"}]}
    assert project_findings_summary([valid]) == [
        {"id": "A-1", "severity": "Medium", "axis": "accuracy"}
    ]

    with pytest.raises(ValueError):
        project_findings_summary([])
    with pytest.raises(ValueError):
        project_findings_summary([{"findings": [{"id": "", "severity": "Medium", "axis": "accuracy"}]}])
    with pytest.raises(ValueError):
        project_findings_summary(
            [{"findings": [{"id": "A-1", "severity": "Medium", "axis": "accuracy", "summary": 1}]}]
        )


def test_findings_summary_does_not_change_the_pass_gate(
    state_dir, run_cli, read_state, tmp_path
):
    """Manifest data is observability; mark-passes must behave exactly as before."""
    _push(state_dir, run_cli, tmp_path, [])

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    assert read_state(state_dir)["passes"] is True
