"""Issue #10: Simple/Reviewer 1名では reviewer_consensus を省略する."""
import json
import subprocess

import pytest


def test_simple_reviewer_one_rejects_reviewer_consensus(state_dir, run_cli, push_provenance_score):
    """Simple + Reviewer 1名の push-score は reviewer_consensus 混入を reject する."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    items = {
        "mission_achievement": 3.8,
        "accuracy": 3.8,
        "completeness": 3.8,
        "usability": 3.8,
        "reviewer_consensus": 5.0,
    }

    with pytest.raises(subprocess.CalledProcessError) as exc:
        push_provenance_score(state_dir.parent, items=items)
    assert "reviewer_consensus" in exc.value.stderr


def test_simple_reviewer_one_rejects_consensus_alias(state_dir, run_cli, push_provenance_score):
    """reviewer_agreement エイリアス経由でも reviewer_consensus として reject する."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    items = {
        "mission_achievement": 3.8,
        "accuracy": 3.8,
        "completeness": 3.8,
        "usability": 3.8,
        "reviewer_agreement": 5.0,
    }

    with pytest.raises(subprocess.CalledProcessError) as exc:
        push_provenance_score(state_dir.parent, items=items)
    assert "reviewer_consensus" in exc.value.stderr


def test_simple_reviewer_one_accepts_four_item_score(state_dir, run_cli, read_state, push_provenance_score):
    """consensus 省略時は4項目の composite/min_item を受理する."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    items = {
        "mission_achievement": 4.0,
        "accuracy": 4.0,
        "completeness": 4.0,
        "usability": 4.0,
    }

    push_provenance_score(state_dir.parent, items=items)
    latest = read_state(state_dir)["score_history"][-1]
    assert "reviewer_consensus" not in latest["items"]
    assert latest["composite"] == 4.0


def test_standard_two_reviewers_still_accepts_reviewer_consensus(state_dir, run_cli, push_provenance_score):
    """複数 Reviewer 前提の Standard では従来どおり consensus を受理する."""
    items = {
        "mission_achievement": 4.0,
        "accuracy": 4.0,
        "completeness": 4.0,
        "usability": 4.0,
        "reviewer_consensus": 4.0,
    }

    push_provenance_score(state_dir.parent, items=items)
