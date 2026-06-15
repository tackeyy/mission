"""multi-session 完全統一の保証テスト (legacy 廃止後の不変条件)."""
import json


_ITEMS = '{"mission_achievement":4.5,"accuracy":4.5,"completeness":4.5,"usability":4.5,"reviewer_consensus":4.5}'


def test_init_ignores_legacy_state_json(tmp_path, run_cli):
    """既存 legacy state.json があっても init は sessions/<sid>.json に書き、legacy は読まれず残る."""
    sd = tmp_path / ".mission-state"
    sd.mkdir()
    (sd / "state.json").write_text(json.dumps({"mission": "old legacy", "loop_active": True, "session_id": "old"}))
    run_cli("init", "new mission", "--complexity", "Standard", cwd=tmp_path, check=True)
    # 新規は sessions/test.json (run_cli デフォルト sid)
    assert (sd / "sessions" / "test.json").exists()
    # legacy state.json は読まれず無害に残る
    assert json.loads((sd / "state.json").read_text())["mission"] == "old legacy"


def test_sid_consistent_across_commands(tmp_path, run_cli):
    """同一 MISSION_SESSION_ID で init→push-score→mark-passes が同一 sessions/<sid>.json を通る (sid一致保証)."""
    env = {"MISSION_SESSION_ID": "consistent"}
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra=env, check=True)
    sf = tmp_path / ".mission-state" / "sessions" / "consistent.json"
    assert sf.exists()
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", _ITEMS, cwd=tmp_path, env_extra=env, check=True)
    assert len(json.loads(sf.read_text())["score_history"]) == 1
    r = run_cli("mark-passes", cwd=tmp_path, env_extra=env)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert json.loads(sf.read_text())["passes"] is True


def test_no_legacy_state_file_created(tmp_path, run_cli):
    """統一後: init は legacy state.json を一切作らない."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, check=True)
    assert not (tmp_path / ".mission-state" / "state.json").exists()
    assert (tmp_path / ".mission-state" / "sessions" / "test.json").exists()
