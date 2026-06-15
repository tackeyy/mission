"""resolve_session_id の env 優先順位 (Claude Code/Codex 並列実行の session 識別)."""
import importlib.util
from pathlib import Path

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load():
    spec = importlib.util.spec_from_file_location("gs_sid", MISSION_STATE_PY)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def test_priority_mission_session_id_wins(monkeypatch):
    m = _load()
    monkeypatch.setenv("MISSION_SESSION_ID", "explicit")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ccid")
    monkeypatch.setenv("CODEX_THREAD_ID", "cxid")
    assert m.resolve_session_id() == "explicit"


def test_cc_session_id(monkeypatch):
    m = _load()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ccid")
    assert m.resolve_session_id() == "cc-ccid"


def test_codex_thread_id(monkeypatch):
    m = _load()
    monkeypatch.setenv("CODEX_THREAD_ID", "cxid")
    assert m.resolve_session_id() == "cx-cxid"


def test_cc_wins_over_codex(monkeypatch):
    m = _load()
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "ccid")
    monkeypatch.setenv("CODEX_THREAD_ID", "cxid")
    assert m.resolve_session_id() == "cc-ccid"


def test_session_id_sanitized(monkeypatch):
    """MISSION_SESSION_ID のパストラバーサル文字を除去 (sessions/<sid>.json の任意パス書込防止)."""
    m = _load()
    monkeypatch.setenv("MISSION_SESSION_ID", "../../etc/passwd")
    sid = m.resolve_session_id()
    assert "/" not in sid and "\\" not in sid


def test_session_id_empty_after_sanitize(monkeypatch):
    """サニタイズ後に空になる入力は default にフォールバック."""
    m = _load()
    monkeypatch.setenv("MISSION_SESSION_ID", "...")  # lstrip(".") で空になる
    assert m.resolve_session_id() == "default"
