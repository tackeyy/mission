"""multi-session で全 cmd_* が sessions/<sid>.json にルーティングされ legacy を汚さない (§10 解消)."""
import json


_ITEMS = '{"mission_achievement":4.5,"accuracy":4.5,"completeness":4.5,"usability":4.5,"reviewer_consensus":4.5}'


def test_explicit_session_id_routing(tmp_path, run_cli):
    env = {"MISSION_SESSION_ID": "sess-x"}
    run_cli("init", "mission text", "--complexity", "Standard", cwd=tmp_path, env_extra=env, check=True)
    sf = tmp_path / ".mission-state" / "sessions" / "sess-x.json"
    assert sf.exists(), "session ファイルが作られていない"
    assert not (tmp_path / ".mission-state" / "state.json").exists(), "legacy を汚染した"
    # push-score → session ファイルへ
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", _ITEMS, cwd=tmp_path, env_extra=env, check=True)
    d = json.loads(sf.read_text())
    assert len(d["score_history"]) == 1 and d["score_history"][0]["composite"] == 4.5
    d["task_profile"] = {"primary": "test"}
    d["specialists_decision"] = {"policy": "fallback", "action": "continue-core"}
    sf.write_text(json.dumps(d))
    # mark-passes が通る (legacy 汚染なし = threshold gate が session の score を見る)
    r = run_cli("mark-passes", cwd=tmp_path, env_extra=env)
    assert r.returncode == 0, f"mark-passes 失敗: {r.stderr}"
    assert json.loads(sf.read_text())["passes"] is True


def test_cc_env_consistent_file(tmp_path, run_cli):
    """MISSION_SESSION_ID なし + Claude Code env のみでも cmd_init と push-score が同じ sessions/cc-<id>.json を見る."""
    env = {"CLAUDE_CODE_SESSION_ID": "abc"}
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra=env, check=True)
    sf = tmp_path / ".mission-state" / "sessions" / "cc-abc.json"
    assert sf.exists(), "session_id が cc-abc に統一されていない"
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", _ITEMS, cwd=tmp_path, env_extra=env, check=True)
    assert len(json.loads(sf.read_text())["score_history"]) == 1


def test_two_sessions_isolated(tmp_path, run_cli):
    """同一プロジェクトの2セッションが互いの state を汚さない (並列実行の核心)."""
    a = {"MISSION_SESSION_ID": "A"}
    b = {"MISSION_SESSION_ID": "B"}
    run_cli("init", "mission A", "--complexity", "Standard", cwd=tmp_path, env_extra=a, check=True)
    run_cli("init", "mission B", "--complexity", "Standard", cwd=tmp_path, env_extra=b, check=True)
    sa = json.loads((tmp_path / ".mission-state" / "sessions" / "A.json").read_text())
    sb = json.loads((tmp_path / ".mission-state" / "sessions" / "B.json").read_text())
    assert sa["mission"] == "mission A" and sb["mission"] == "mission B"


def test_mark_passes_without_state_errors(tmp_path, run_cli):
    """multi で session ファイルが無い状態の mark-passes はクラッシュせず exit 非0."""
    r = run_cli("mark-passes", cwd=tmp_path,
                env_extra={"MISSION_SESSION_ID": "nope"})
    assert r.returncode != 0
    assert "Traceback" not in r.stderr  # FileNotFoundError でクラッシュしない


def test_mark_halt_without_state_errors(tmp_path, run_cli):
    r = run_cli("mark-halt", "--reason", "x", cwd=tmp_path,
                env_extra={"MISSION_SESSION_ID": "nope"})
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
