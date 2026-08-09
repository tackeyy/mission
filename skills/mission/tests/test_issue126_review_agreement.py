"""Issue #126: review agreement is independent from composite and gates pass."""

from __future__ import annotations

import json

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.4,
    "completeness": 4.3,
    "usability": 4.2,
}


def _write_evidence(state_dir, *, delta):
    reviewer_a = dict(ITEMS, mission_achievement=5.0)
    reviewer_b = dict(ITEMS, mission_achievement=5.0 - delta)
    return write_canonical_review_aggregate(
        state_dir.parent,
        [
            canonical_review(reviewer_a, perspective="A"),
            canonical_review(reviewer_b, perspective="B"),
        ],
        name_prefix="review-agreement",
    )


def _write_scoring(tmp_path, evidence):
    evidence_path, ref, claim = evidence
    payload = {
        "items": claim["items"],
        "open_high": claim["open_high"],
        "findings_evidence_path": str(evidence_path),
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
    }
    payload["score_provenance"] = {"score_source": "scoring-json", "review_evidence_ref": ref,
                                   "revision_scope": ref["revision_scope"]}
    path = tmp_path / "scoring.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_aggregate_reviews_outputs_derived_consensus_and_independent_agreement(state_dir, run_cli, tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    base = {
        "schema": "mission-review/1",
        "iteration": 1,
        "scores": ITEMS,
        "findings": [],
        "same_score_note": None,
    }
    a.write_text(json.dumps(dict(base, perspective="A")), encoding="utf-8")
    b.write_text(json.dumps(dict(base, perspective="B", scores=dict(ITEMS, mission_achievement=3.5))), encoding="utf-8")
    out = tmp_path / "out.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
            "--out", str(out),
            "--reviewer-window", "A=2026-08-02T10:00:00Z..2026-08-02T10:05:00Z",
            "--reviewer-window", "B=2026-08-02T10:00:30Z..2026-08-02T10:04:00Z",
            cwd=state_dir.parent, check=True)

    payload = json.loads(out.read_text())
    assert set(payload["items"]) == {"mission_achievement", "accuracy", "completeness", "usability", "reviewer_consensus"}
    assert payload["items"]["reviewer_consensus"] == 4.0
    assert payload["review_agreement"] == 4.0
    assert payload["agreement_detail"]["mission_achievement"]["delta"] == 1.0


def test_push_score_records_review_agreement_independently(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.0)
    scoring = _write_scoring(tmp_path, evidence)

    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    entry = read_state(state_dir)["score_history"][-1]
    assert entry["items"] == {**ITEMS, "reviewer_consensus": 4.0}
    assert entry["composite"] == 4.35
    assert entry["review_agreement"] == 4.0
    assert entry["agreement_detail"]["mission_achievement"]["delta"] == 1.0


def test_mark_passes_rejects_max_delta_above_1_5(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.6)
    scoring = _write_scoring(tmp_path, evidence)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "低合意" in r.stderr
    assert "mission_achievement" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_warns_for_delta_above_1_0_and_passes(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.1)
    scoring = _write_scoring(tmp_path, evidence)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert "reviewer agreement is low" in r.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_allows_delta_at_1_5_boundary(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, delta=1.5)
    scoring = _write_scoring(tmp_path, evidence)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True
