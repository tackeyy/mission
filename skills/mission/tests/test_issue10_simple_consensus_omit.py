"""Issue #10: Simple/Reviewer 1名では reviewer_consensus を省略する."""

import json

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


FOUR_AXES = {
    "mission_achievement": 4.0,
    "accuracy": 4.0,
    "completeness": 4.0,
    "usability": 4.0,
}


def _write_scoring_json(state_dir, tmp_path, reviews, *, items=None):
    """Build a canonical aggregate archive and its provenance-bound score payload."""
    evidence_path, ref, claim = write_canonical_review_aggregate(
        state_dir.parent, reviews, name_prefix="issue10",
    )
    payload = {
        "items": claim["items"] if items is None else items,
        "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": str(evidence_path),
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": ref,
            "revision_scope": ref["revision_scope"],
        },
    }
    path = tmp_path / "issue10-score.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_simple_reviewer_one_rejects_reviewer_consensus(state_dir, run_cli, tmp_path):
    """Simple + Reviewer 1名の明示 consensus は provenance より先に reject する."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    score = _write_scoring_json(
        state_dir, tmp_path, [canonical_review(FOUR_AXES)],
        items={**FOUR_AXES, "reviewer_consensus": 5.0},
    )

    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(score), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "reviewer_consensus" in r.stderr
    assert "正規" in r.stderr


def test_simple_reviewer_one_rejects_consensus_alias(state_dir, run_cli, tmp_path):
    """reviewer_agreement エイリアスでも policy の consensus reject を通る."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    score = _write_scoring_json(
        state_dir, tmp_path, [canonical_review(FOUR_AXES)],
        items={**FOUR_AXES, "reviewer_agreement": 5.0},
    )

    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(score), cwd=state_dir.parent)

    assert r.returncode == 2
    assert "reviewer_agreement" in r.stderr
    assert "正規" in r.stderr


def test_simple_reviewer_one_accepts_four_item_score(state_dir, run_cli, read_state, tmp_path):
    """Single-review canonical claim omits consensus and accepts the four-axis score."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    score = _write_scoring_json(state_dir, tmp_path, [canonical_review(FOUR_AXES)])

    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(score), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    latest = read_state(state_dir)["score_history"][-1]
    assert "reviewer_consensus" not in latest["items"]
    assert latest["composite"] == 4.0


def test_standard_two_reviewers_keep_reducer_derived_agreement_outside_items(state_dir, run_cli, read_state, tmp_path):
    """Standard agreement stays metadata while items remain exactly four axes."""
    score = _write_scoring_json(
        state_dir,
        tmp_path,
        [
            canonical_review(FOUR_AXES, perspective="first"),
            canonical_review(FOUR_AXES, perspective="second"),
        ],
    )

    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(score), cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    latest = read_state(state_dir)["score_history"][-1]
    assert latest["items"] == FOUR_AXES
    assert latest["composite"] == 4.0
    assert latest["review_agreement"] == 5.0
