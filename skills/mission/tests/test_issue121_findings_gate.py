"""Issue #121: mark-passes validates machine-derived findings evidence."""

from __future__ import annotations

import json

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.4,
    "completeness": 4.3,
    "usability": 4.2,
}


def _write_evidence(state_dir, *, high_count=0):
    return write_canonical_review_aggregate(
        state_dir.parent,
        [canonical_review(ITEMS, perspective="A", high_count=high_count)],
        name_prefix="findings-gate",
    )


def _write_scoring_json(tmp_path, evidence=None, *, open_high=None, record_findings_evidence=True):
    if evidence is None:
        evidence = _write_evidence(tmp_path / ".mission-state", high_count=0)
    evidence_path, ref, claim = evidence
    payload = {
        "items": claim["items"],
        "open_high": claim["open_high"] if open_high is None else open_high,
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "notes": "aggregate-reviews test payload",
    }
    if record_findings_evidence:
        payload["findings_evidence_path"] = str(evidence_path)
    payload["score_provenance"] = {"score_source": "scoring-json", "review_evidence_ref": ref,
                                   "revision_scope": ref["revision_scope"]}
    path = tmp_path / "scoring.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_push_score_records_findings_evidence_path(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=0)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0)

    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    latest = read_state(state_dir)["score_history"][-1]
    assert latest["findings_evidence_path"] == str(evidence[0])


def test_mark_passes_rejects_scoring_json_missing_findings_evidence_path(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=0)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0, record_findings_evidence=False)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "findings_evidence_path" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_push_score_rejects_findings_evidence_open_high_mismatch(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=1)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0)
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "score claim mismatch" in r.stderr
    assert read_state(state_dir)["score_history"] == []
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_passes_when_findings_evidence_matches_open_high_zero(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=0)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True


def test_active_legacy_entry_without_provenance_cannot_mark_passes(state_dir, run_cli, read_state):
    session = state_dir / "sessions" / "test.json"
    document = json.loads(session.read_text())
    document["score_history"] = [{"iteration": 1, "composite": 4.25, "min_item": 4.0,
                                  "items": {"mission_achievement": 4.5, "accuracy": 4.0}, "open_high": 0}]
    session.write_text(json.dumps(document))

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "provenance" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_force_bypasses_missing_findings_evidence(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=0)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0, record_findings_evidence=False)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", "--force", "--reason", "manual override in test", "--approved-by-user",
                cwd=state_dir.parent)

    assert r.returncode == 2
    assert "approval-evidence-ref" in r.stderr
    assert read_state(state_dir)["passes"] is False
