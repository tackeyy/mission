"""Issue #119: aggregate reviewer JSON into deterministic scoring JSON."""

from __future__ import annotations

import json

import pytest


def _review(tmp_path, name, *, perspective="A", iteration=1, scores=None, findings=None, same_score_note=None):
    payload = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": iteration,
        "scores": scores if scores is not None else {
            "mission_achievement": 4.6,
            "accuracy": 4.4,
            "completeness": 4.2,
            "usability": 4.0,
        },
        "findings": findings if findings is not None else [],
        "same_score_note": same_score_note,
        "notes": f"{perspective} review",
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_aggregate_reviews_writes_scoring_json_and_evidence(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    b = _review(tmp_path, "b.json", perspective="B", scores={
        "mission_achievement": 4.4,
        "accuracy": 4.2,
        "completeness": 4.0,
        "usability": 3.8,
    })
    out = tmp_path / "scoring.json"

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
                "--out", str(out), "--json", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)
    payload = _load(out)
    assert result["out"] == str(out)
    assert payload["items"] == {
        "mission_achievement": 4.5,
        "accuracy": 4.3,
        "completeness": 4.1,
        "usability": 3.9,
        "reviewer_consensus": 5.0,
    }
    assert payload["open_high"] == 0
    assert (state_dir.parent / payload["findings_evidence_path"]).exists()


def test_aggregate_reviews_is_deterministic(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    out1 = tmp_path / "one.json"
    out2 = tmp_path / "two.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out1), cwd=state_dir.parent, check=True)
    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out2), cwd=state_dir.parent, check=True)

    assert _load(out1) == _load(out2)


def test_aggregate_reviews_caps_scores_per_reviewer_findings(state_dir, run_cli, tmp_path):
    finding = {
        "id": "A-1",
        "severity": "High",
        "axis": "accuracy",
        "summary": "Bug remains",
        "evidence": "file.py:1 `bad()`",
        "recommendation": "Fix it",
    }
    a = _review(tmp_path, "a.json", perspective="A", scores={
        "mission_achievement": 5.0,
        "accuracy": 5.0,
        "completeness": 5.0,
        "usability": 5.0,
    }, findings=[finding], same_score_note="All axes independently checked")
    out = tmp_path / "scoring.json"

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    payload = _load(out)
    assert payload["items"]["accuracy"] == 3.0
    assert payload["items"]["mission_achievement"] == 5.0
    assert payload["open_high"] == 1


def test_aggregate_reviews_uses_findings_only_reviewer_without_scores(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    d = _review(tmp_path, "d.json", perspective="D", scores=None, findings=[{
        "id": "D-1",
        "severity": "Low",
        "axis": "completeness",
        "summary": "Planning note",
        "evidence": "",
        "recommendation": "Clarify next plan",
    }])
    payload = _load(d)
    payload["scores"] = None
    d.write_text(json.dumps(payload), encoding="utf-8")
    out = tmp_path / "scoring.json"

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(d),
                "--out", str(out), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    payload = _load(out)
    assert "reviewer_consensus" not in payload["items"]
    assert "1 scoring reviewer(s), 1 findings-only reviewer(s)" in payload["notes"]


def test_aggregate_reviews_consensus_score_boundaries(state_dir, run_cli, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A", scores={
        "mission_achievement": 5.0, "accuracy": 4.8, "completeness": 4.8, "usability": 4.8,
    })
    b = _review(tmp_path, "b.json", perspective="B", scores={
        "mission_achievement": 3.4, "accuracy": 4.8, "completeness": 4.8, "usability": 4.8,
    })
    out = tmp_path / "scoring.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--input", str(b),
            "--out", str(out), cwd=state_dir.parent, check=True)

    assert _load(out)["items"]["reviewer_consensus"] == 2.0


def test_aggregate_reviews_output_can_be_pushed(state_dir, run_cli, read_state, tmp_path):
    a = _review(tmp_path, "a.json", perspective="A")
    out = tmp_path / "scoring.json"

    run_cli("aggregate-reviews", "--iteration", "1", "--input", str(a), "--out", str(out), cwd=state_dir.parent, check=True)
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(out), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    entry = read_state(state_dir)["score_history"][0]
    assert entry["score_source"] == "scoring-json"
    assert entry["items"]["accuracy"] == 4.4


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda p: p.pop("schema"), "schema"),
        (lambda p: p.update(iteration=2), "iteration"),
        (lambda p: p.pop("scores"), "scores field is required"),
        (lambda p: p.update(scores={"mission_achievement": 4.0}), "scores"),
        (lambda p: p["scores"].update(accuracy=0.5, mission_achievement=0.5, completeness=0.5, usability=0.5), "0-1"),
        (lambda p: p["findings"].append({"id": "A-1", "severity": "High", "axis": "accuracy", "summary": "x", "evidence": "", "recommendation": "y"}), "evidence"),
        (lambda p: p["findings"].append({"id": "A-1", "severity": "Medium", "axis": "wrong", "summary": "x", "evidence": "e", "recommendation": "y"}), "axis"),
        (lambda p: p["findings"].extend([
            {"id": "A-1", "severity": "Low", "axis": "accuracy", "summary": "x", "evidence": "", "recommendation": "y"},
            {"id": "A-1", "severity": "Low", "axis": "accuracy", "summary": "x", "evidence": "", "recommendation": "y"},
        ]), "duplicate"),
        (lambda p: p.update(scores={k: 4.0 for k in ("mission_achievement", "accuracy", "completeness", "usability")}, same_score_note=None), "same_score_note"),
    ],
)
def test_aggregate_reviews_rejects_invalid_review_json(state_dir, run_cli, tmp_path, mutate, expected):
    src = _review(tmp_path, "bad.json", perspective="A")
    payload = _load(src)
    mutate(payload)
    src.write_text(json.dumps(payload), encoding="utf-8")

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(src), cwd=state_dir.parent)

    assert r.returncode == 2
    assert expected in r.stderr


def test_aggregate_reviews_rejects_findings_only_inputs(state_dir, run_cli, tmp_path):
    src = _review(tmp_path, "d.json", perspective="D")
    payload = _load(src)
    payload["scores"] = None
    src.write_text(json.dumps(payload), encoding="utf-8")

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(src), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "採点対象 reviewer" in r.stderr


def test_aggregate_reviews_rejects_overall_impression_same_score(state_dir, run_cli, tmp_path):
    src = _review(tmp_path, "a.json", perspective="A", scores={
        "mission_achievement": 4.0,
        "accuracy": 4.0,
        "completeness": 4.0,
        "usability": 4.0,
    }, same_score_note="全体印象で同じ点にした")

    r = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(src), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "全採点 reviewer" in r.stderr
