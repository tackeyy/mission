"""Issue #121: mark-passes validates machine-derived findings evidence."""

from __future__ import annotations

import json


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.4,
    "completeness": 4.3,
    "usability": 4.2,
}


def _write_evidence(state_dir, *, high_count=0):
    findings = [
        {
            "id": f"H-{i}",
            "severity": "High",
            "axis": "accuracy",
            "summary": "High finding",
            "evidence": "file.py:1 `bad()`",
            "recommendation": "Fix it",
        }
        for i in range(high_count)
    ]
    evidence = {
        "schema": "mission-review-aggregate/1",
        "iteration": 1,
        "inputs": [
            {
                "schema": "mission-review/1",
                "perspective": "A",
                "iteration": 1,
                "scores": ITEMS,
                "findings": findings,
            }
        ],
    }
    path = state_dir / "archive" / "iter-1-abc12345-reviews.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence), encoding="utf-8")
    return path


def _write_scoring_json(tmp_path, evidence_path=None, *, open_high=0):
    payload = {
        "items": ITEMS,
        "open_high": open_high,
        "notes": "aggregate-reviews test payload",
    }
    if evidence_path is not None:
        payload["findings_evidence_path"] = str(evidence_path)
    path = tmp_path / "scoring.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_push_score_records_findings_evidence_path(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=0)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0)

    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    latest = read_state(state_dir)["score_history"][-1]
    assert latest["findings_evidence_path"] == str(evidence)


def test_mark_passes_rejects_scoring_json_missing_findings_evidence_path(state_dir, run_cli, read_state, tmp_path):
    scoring = _write_scoring_json(tmp_path, evidence_path=None, open_high=0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "findings_evidence_path" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_rejects_findings_evidence_open_high_mismatch(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=1)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "High 件数" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_passes_when_findings_evidence_matches_open_high_zero(state_dir, run_cli, read_state, tmp_path):
    evidence = _write_evidence(state_dir, high_count=0)
    scoring = _write_scoring_json(tmp_path, evidence, open_high=0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_legacy_entry_warns_and_uses_stored_open_high(state_dir, run_cli, read_state):
    items = json.dumps({"mission_achievement": 4.5, "accuracy": 4.0})
    run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", "4.25",
        "--min-item", "4.0",
        "--items", items,
        "--open-high", "0",
        cwd=state_dir.parent,
        check=True,
    )

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert "legacy score entry" in r.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_force_bypasses_missing_findings_evidence(state_dir, run_cli, read_state, tmp_path):
    scoring = _write_scoring_json(tmp_path, evidence_path=None, open_high=0)
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent, check=True)

    r = run_cli("mark-passes", "--force", "--reason", "manual override in test", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True
