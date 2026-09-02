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
from pathlib import Path

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


def test_the_gate_path_returns_the_archive_rather_than_a_projection():
    """Pin the separation itself, not only its current user-visible effect.

    The pass gate calls ``_revalidate_score_provenance`` too.  Today the
    projection happens to raise on nothing a legal archive contains, so running
    it there would be harmless -- but that is a property of the projection's
    current strictness, not of the gate.  Anything tightened later would leak
    straight into the gate.  Requiring the archive back is what keeps the
    projection on the write path.
    """
    import importlib.util
    import inspect

    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_690", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    source = inspect.getsource(module._revalidate_score_provenance)

    assert "findings_summary" not in source
    assert "project_findings" not in source
    assert source.rstrip().endswith("return parsed")


@pytest.mark.parametrize(
    "identifier",
    ["A-1", "", " ", "x"],
    ids=["normal", "empty", "blank", "no-prefix"],
)
def test_the_three_layers_agree_on_what_an_id_is(identifier):
    """The archive validator decides; the projection and kernel must follow.

    These three ran different id rules.  A blank id passed the archive
    re-validation, was projected, and then counted as unusable -- so an entry
    the producer legitimately wrote reported ``partial``.  An empty id went the
    other way: it passed re-validation but the projection rejected it, which
    made ``push-score`` refuse an archive it used to accept.

    Neither direction is acceptable, and the fix is not to pick the strictest
    rule: the archive validator runs on the pass-gate path, so tightening it
    would add rejections there.  It is the authority, and the other two follow.
    """
    from scoring_provenance import project_findings_summary, reduce_review_aggregate
    from mission_kernel.evidence import _is_usable_prior_finding

    review = {
        "schema": "mission-review/1",
        "perspective": "A",
        "iteration": 1,
        "scores": dict(ITEMS),
        "findings": [{"id": identifier, "severity": "Medium", "axis": "accuracy"}],
        "same_score_note": None,
    }

    try:
        reduce_review_aggregate([review], expected_iteration=1)
    except ValueError:
        archive_accepts = False
    else:
        archive_accepts = True

    try:
        projected = project_findings_summary([review])
    except ValueError:
        projection_accepts = False
    else:
        projection_accepts = True

    assert projection_accepts == archive_accepts, (
        "the projection must accept exactly what the archive validator accepts"
    )
    if archive_accepts:
        assert all(_is_usable_prior_finding(item) for item in projected), (
            "the kernel must treat everything the projection emits as usable"
        )


def test_the_kernel_and_the_projection_agree_on_what_a_severity_is():
    """The kernel restates the severity set to stay pure; catch it drifting."""
    from scoring_provenance import REVIEW_SEVERITIES
    from mission_kernel.evidence import PRIOR_FINDING_SEVERITIES

    assert set(PRIOR_FINDING_SEVERITIES) == set(REVIEW_SEVERITIES)


NON_STRING_SUMMARY_FINDING = {
    "id": "A-1",
    "severity": "Medium",
    "axis": "accuracy",
    # The review contract never constrains `summary`, so an archive carrying a
    # non-string here is legal.  Collecting it must not become a new reason to
    # reject the archive -- neither when writing the score nor at the gate.
    "summary": 1,
    "evidence": "lib/x.py:10 -- 引用",
}


def test_an_archive_the_review_contract_accepts_is_not_rejected_here(
    state_dir, run_cli, read_state, tmp_path
):
    """Collecting observation data must not narrow what push-score accepts."""
    _push(state_dir, run_cli, tmp_path, [NON_STRING_SUMMARY_FINDING])

    latest = read_state(state_dir)["score_history"][-1]
    assert latest["findings_summary"] == [
        {"id": "A-1", "severity": "Medium", "axis": "accuracy"}
    ]


def test_the_pass_gate_does_not_run_the_projection(
    state_dir, run_cli, read_state, tmp_path
):
    """mark-passes re-validates the archive; it must gain no new rejection.

    The projection is a write-path concern.  Running it from the gate would let
    an observation-only feature decide whether a mission passes.
    """
    _push(state_dir, run_cli, tmp_path, [NON_STRING_SUMMARY_FINDING])

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    assert read_state(state_dir)["passes"] is True


@pytest.mark.parametrize(
    "summary",
    [
        [{"id": "A-1", "severity": "not-a-severity", "axis": "accuracy"}],
    ],
    ids=["unknown-severity"],
)
def test_context_manifest_is_partial_for_a_finding_it_could_not_have_written(
    state_dir, run_cli, tmp_path, summary
):
    """Values the projection never emits do not get to claim completeness."""
    _push(state_dir, run_cli, tmp_path, [MEDIUM_FINDING])

    def mutate(entry):
        entry["findings_summary"] = summary

    _corrupt_latest_entry(state_dir, mutate)

    assert _manifest(state_dir, run_cli, tmp_path)["prior_findings_status"] == "partial"


def test_findings_summary_does_not_change_the_pass_gate(
    state_dir, run_cli, read_state, tmp_path
):
    """Manifest data is observability; mark-passes must behave exactly as before."""
    _push(state_dir, run_cli, tmp_path, [])

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    assert read_state(state_dir)["passes"] is True
