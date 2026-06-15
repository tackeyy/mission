"""H3: multi-session 時の assumptions_path セッション分離 (2026-06-10 検査レポート).

Phase C は state を sessions/<sid>.json に分離したが assumptions_path が全セッション
".mission-state/assumptions.md" 共有のままで、並走時に相互上書きする (workspace で実害確認)。
"""
import json


def test_multi_session_init_separates_assumptions_path(tmp_path, run_cli):
    r = run_cli("init", "H3 session mission",
                cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "sess-aaa"})
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = json.loads((tmp_path / ".mission-state" / "sessions" / "sess-aaa.json").read_text())
    assert s["assumptions_path"] == ".mission-state/sessions/sess-aaa-assumptions.md"


def test_multi_session_two_sessions_do_not_share_assumptions(tmp_path, run_cli):
    run_cli("init", "H3 shared mission",
            cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "sess-one"})
    run_cli("init", "H3 shared mission",
            cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "sess-two"})
    s1 = json.loads((tmp_path / ".mission-state" / "sessions" / "sess-one.json").read_text())
    s2 = json.loads((tmp_path / ".mission-state" / "sessions" / "sess-two.json").read_text())
    assert s1["assumptions_path"] != s2["assumptions_path"]


