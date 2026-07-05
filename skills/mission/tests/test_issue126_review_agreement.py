"""Issue #126: review agreement is independent from composite and gates pass."""

from __future__ import annotations

import json


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.4,
    "completeness": 4.3,
    "usability": 4.2,
}


def _write_evidence(state_dir):
    path = state_dir / "archive" / "iter-1-abc12345-reviews.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "mission-review-aggregate/1",
        "iteration": 1,
        "inputs": [{"findings": []}],
        "open_high": 0,
    }), encoding="utf-8")
    return path


def _write_scoring(tmp_path, evidence_path, *, delta, review_agreement=3.0):
    payload = {
        "items": ITEMS,
        "open_high": 0,
        "findings_evidence_path": str(evidence_path),
        "review_agreement": review_agreement,
        "agreement_detail": {
            "mission_achievement": {"min": 3.0, "max": 3.0 + delta, "delta": delta},
            "accuracy": {"min": 4.0, "max": 4.0, "delta": 0.0},
            "completeness": {"min": 4.0, "max": 4.0, "delta": 0.0},
            "usability": {"min": 4.0, "max": 4.0, "delta": 0.0},
        },
    }
    path = tmp_path / "scoring.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_aggregate_reviews_outputs_four_axis_items_and_independent_agreement(state_dir, run_cli, tmp_path):
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
            "--out", str(out), cwd=state_dir.parent, check=True)

    payload = json.loads(out.read_text())
    assert set(payload["items"]) == {"mission_achievement", "accuracy", "completeness", "usability"}
    assert payload["review_agreement"] == 4.0
    assert payload["agreement_detail"]["mission_achievement"]["delta"] == 1.0


def test_push_score_records_review_agreement_independently(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir)
    scoring = _write_scoring(tmp_path, evidence, delta=1.0, review_agreement=4.0)

    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    entry = read_state(state_dir)["score_history"][-1]
    assert entry["items"] == ITEMS
    assert entry["composite"] == 4.35
    assert entry["review_agreement"] == 4.0
    assert entry["agreement_detail"]["mission_achievement"]["delta"] == 1.0


def test_mark_passes_rejects_max_delta_above_1_5(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir)
    scoring = _write_scoring(tmp_path, evidence, delta=1.6, review_agreement=2.0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "低合意" in r.stderr
    assert "mission_achievement" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_warns_for_delta_above_1_0_and_passes(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir)
    scoring = _write_scoring(tmp_path, evidence, delta=1.1, review_agreement=3.0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert "reviewer agreement is low" in r.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_allows_delta_at_1_5_boundary(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir)
    scoring = _write_scoring(tmp_path, evidence, delta=1.5, review_agreement=3.0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True
