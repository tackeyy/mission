"""Issue #188: `set phase=` の enum 検証。

実運用で `phase=execution` (正しくは executing) が無検証で受理され、
phase_durations_sec に不正キーが混入して stats を汚染した実害への回帰テスト。
"""
import json


def test_set_phase_accepts_valid_value(state_dir, run_cli, read_state):
    r = run_cli("set", "phase=executing", cwd=state_dir.parent, check=True)
    assert r.returncode == 0
    assert read_state(state_dir)["phase"] == "executing"


def test_set_phase_rejects_unknown_value(state_dir, run_cli):
    r = run_cli("set", "phase=bogus", cwd=state_dir.parent)
    assert r.returncode == 2
    assert "無効です" in r.stderr


def test_set_phase_normalizes_known_alias_execution_to_executing(state_dir, run_cli, read_state):
    """#188 の実害そのもの: phase=execution (typo) は executing として保存され WARN が出る."""
    r = run_cli("set", "phase=execution", cwd=state_dir.parent, check=True)
    assert r.returncode == 0
    assert "WARNING" in r.stderr
    assert read_state(state_dir)["phase"] == "executing"
    # 不正キー execution が保存されていないこと
    assert "execution" not in json.dumps(read_state(state_dir))


def test_set_phase_normalizes_review_alias_to_reviewing(state_dir, run_cli, read_state):
    r = run_cli("set", "phase=review", cwd=state_dir.parent, check=True)
    assert r.returncode == 0
    assert read_state(state_dir)["phase"] == "reviewing"


def test_set_phase_normalizes_plan_and_score_aliases(state_dir, run_cli, read_state):
    run_cli("set", "phase=plan", cwd=state_dir.parent, check=True)
    assert read_state(state_dir)["phase"] == "planning"
    run_cli("set", "phase=score", cwd=state_dir.parent, check=True)
    assert read_state(state_dir)["phase"] == "scoring"


def test_set_phase_still_tracks_duration_on_transition(state_dir, run_cli, read_state):
    """enum 検証後も phase_durations_sec の加算ロジックは変わらない (回帰確認)."""
    run_cli("set", "phase=executing", cwd=state_dir.parent, check=True)
    run_cli("set", "phase=reviewing", cwd=state_dir.parent, check=True)
    durations = read_state(state_dir).get("phase_durations_sec", {})
    assert "executing" in durations
    assert all(k in {"planning", "executing", "reviewing", "scoring", "done", "halted"} for k in durations)
