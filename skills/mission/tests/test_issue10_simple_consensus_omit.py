"""Issue #10: Simple/Reviewer 1名では reviewer_consensus を省略する."""
import json


def test_simple_reviewer_one_rejects_reviewer_consensus(state_dir, run_cli):
    """Simple + Reviewer 1名の push-score は reviewer_consensus 混入を reject する."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    items = {
        "mission_achievement": 3.8,
        "accuracy": 3.8,
        "completeness": 3.8,
        "usability": 3.8,
        "reviewer_consensus": 5.0,
    }

    r = run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", "4.04",
        "--min-item", "3.8",
        "--items", json.dumps(items),
        cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "reviewer_consensus" in r.stderr
    assert "省略" in r.stderr


def test_simple_reviewer_one_rejects_consensus_alias(state_dir, run_cli):
    """reviewer_agreement エイリアス経由でも reviewer_consensus として reject する."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    items = {
        "mission_achievement": 3.8,
        "accuracy": 3.8,
        "completeness": 3.8,
        "usability": 3.8,
        "reviewer_agreement": 5.0,
    }

    r = run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", "4.04",
        "--min-item", "3.8",
        "--items", json.dumps(items),
        cwd=state_dir.parent,
    )

    assert r.returncode == 2
    assert "reviewer_consensus" in r.stderr


def test_simple_reviewer_one_accepts_four_item_score(state_dir, run_cli, read_state):
    """consensus 省略時は4項目の composite/min_item を受理する."""
    run_cli("set", "complexity=Simple", cwd=state_dir.parent, check=True)
    items = {
        "mission_achievement": 4.0,
        "accuracy": 4.0,
        "completeness": 4.0,
        "usability": 4.0,
    }

    r = run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", "4.0",
        "--min-item", "4.0",
        "--items", json.dumps(items),
        "--notes", "Simple Reviewer1: consensus 省略・4項目で算出",
        cwd=state_dir.parent,
    )

    assert r.returncode == 0, r.stderr
    latest = read_state(state_dir)["score_history"][-1]
    assert "reviewer_consensus" not in latest["items"]
    assert latest["composite"] == 4.0


def test_standard_two_reviewers_still_accepts_reviewer_consensus(state_dir, run_cli):
    """複数 Reviewer 前提の Standard では従来どおり consensus を受理する."""
    items = {
        "mission_achievement": 4.0,
        "accuracy": 4.0,
        "completeness": 4.0,
        "usability": 4.0,
        "reviewer_consensus": 4.0,
    }

    r = run_cli(
        "push-score",
        "--iteration", "1",
        "--composite", "4.0",
        "--min-item", "4.0",
        "--items", json.dumps(items),
        cwd=state_dir.parent,
    )

    assert r.returncode == 0, r.stderr
