"""起動元エージェント(Claude Code/Codex/CLI)の独立記録 — ログでの起動元識別 (session_id 非依存)."""
import importlib.util
import json
from pathlib import Path

from mission_persistence.authoritative_reader import read_authoritative_snapshot

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load():
    spec = importlib.util.spec_from_file_location("gs_agent", MISSION_STATE_PY)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_agent_claude_code(monkeypatch):
    m = _load()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    assert m.resolve_agent() == "claude-code"


def test_agent_codex(monkeypatch):
    m = _load()
    monkeypatch.setenv("CODEX_THREAD_ID", "x")
    assert m.resolve_agent() == "codex"


def test_agent_cli_when_no_env(monkeypatch):
    m = _load()
    assert m.resolve_agent() == "cli"


def test_agent_independent_of_mission_session_id(monkeypatch):
    """MISSION_SESSION_ID を明示しても起動元 env から agent を正しく判定 (改善の核心)."""
    m = _load()
    monkeypatch.setenv("MISSION_SESSION_ID", "explicit-sid")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    assert m.resolve_agent() == "claude-code"


def test_init_records_agent_cli(tmp_path, run_cli):
    """run_cli (Claude Code/Codex env 遮断) の init は agent=cli を記録."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, check=True)
    path = tmp_path / ".mission-state" / "sessions" / "test.json"
    d = read_authoritative_snapshot(path, expected_session_id="test").document_copy()
    assert d["agent"] == "cli"


def test_init_records_agent_codex(tmp_path, run_cli):
    """CODEX_THREAD_ID 起動の init は agent=codex を記録 (MISSION_SESSION_ID 明示でも)."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path,
            env_extra={"CODEX_THREAD_ID": "t1"}, check=True)
    path = tmp_path / ".mission-state" / "sessions" / "cx-t1.json"
    d = read_authoritative_snapshot(path, expected_session_id="cx-t1").document_copy()
    assert d["agent"] == "codex"
