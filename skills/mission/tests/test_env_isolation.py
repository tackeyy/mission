"""env isolation: MISSION_* 外部環境変数が設定されていてもテスト結果が変わらないことを検証.

2026-06-10 iter2 レビュー申し送り (A-Low #11)。run_cli が既定で MISSION_SESSION_ID /
MISSION_MULTI_SESSION を遮断し、env_extra による明示注入のみ許すことを保証する。
"""
import json


def test_run_cli_masks_mission_session_id_from_outer_env(tmp_path, run_cli, monkeypatch):
    """外部 MISSION_SESSION_ID があっても run_cli は無視する (isolation)."""
    monkeypatch.setenv("MISSION_SESSION_ID", "outer-session-id")
    r = run_cli("init", "isolation test mission", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["session_id"] == "test"  # 外部 outer は遮断され run_cli デフォルト sid



def test_run_cli_env_extra_injects_explicitly(tmp_path, run_cli):
    """env_extra による明示注入は通る (multi-session テストの基盤)."""
    r = run_cli("init", "explicit injection mission",
                cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "sess-explicit"})
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert (tmp_path / ".mission-state" / "sessions" / "sess-explicit.json").exists()


def test_run_cli_env_extra_overrides_outer_pollution(tmp_path, run_cli, monkeypatch):
    """外部汚染 + env_extra 併用時、env_extra の明示値が必ず勝つ (遮断の証明)."""
    monkeypatch.setenv("MISSION_SESSION_ID", "outer-bad")
    r = run_cli("init", "combined pollution mission",
                cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "sess-good"})
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert (tmp_path / ".mission-state" / "sessions" / "sess-good.json").exists()
    assert not (tmp_path / ".mission-state" / "sessions" / "outer-bad.json").exists()


def test_run_cli_env_extra_injects_session_id(tmp_path, run_cli):
    """MISSION_SESSION_ID の env_extra 注入で sessions/<sid>.json になる (常時 multi)."""
    r = run_cli("init", "multi via env mission", cwd=tmp_path,
                env_extra={"MISSION_SESSION_ID": "sess-env"})
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert (tmp_path / ".mission-state" / "sessions" / "sess-env.json").exists()
