"""#314 (F6): shared-PID false-stale の停止.

実運用監査 (2026-08-01): 親プロセスの PID を複数子 session が共有し、親が alive でも
3 時間無スコアで一括 stale 化 (7/25-27 に 7 件、PID 39146 x5 / 7452 x2)。
被害は planning-checker 中心 — Checker 系は設計上 score を書かないため
live-pid no-score 判定が構造的に誤爆する。

Contract under test:
1. checker 系 role + live agent PID + no-score + age 超過 → stale 化しない
   (skipped: checker-role-no-score-by-design)
2. implementer + 同条件 → 従来どおり stale 対象 (非退行)
3. 同一 PID が複数 active session に登録されている場合、results に重複 PID の
   warning が出る (可観測性)
4. dead PID の checker → 従来どおり orphan 経路で回収可能 (非退行)
"""

import json
import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load()


def _write_session(tmp_path, name, *, role, pid=12345, old=True):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    ts = "2020-01-01T00:00:00Z" if old else "2099-01-01T00:00:00Z"
    state = {
        "mission": f"m-{name}",
        "mission_id": f"mid-{name}",
        "session_id": name,
        "session_role": role,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "pid": pid,
        "pid_source": "agent",
        "started_at": ts,
        "updated_at": ts,
        "last_activity_at": ts,
        "project_root": str(tmp_path),
    }
    sf = sessions / f"{name}.json"
    sf.write_text(json.dumps(state))
    return sf


def _run_cleanup(tmp_path, monkeypatch, *, execute=True):
    monkeypatch.setenv("MISSION_FORCE_PID_IS_AGENT", "1")  # live agent PID を強制
    monkeypatch.chdir(tmp_path)
    import io, contextlib
    args = type("Args", (), {"root": str(tmp_path), "execute": execute})()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        MS.cmd_cleanup_stale(args)
    return json.loads(buf.getvalue())


def test_checker_live_pid_no_score_not_staled(tmp_path, monkeypatch):
    sf = _write_session(tmp_path, "chk", role="checker")
    result = _run_cleanup(tmp_path, monkeypatch)
    state = json.loads(sf.read_text())
    assert state["loop_active"] is True, "checker 役の live-pid no-score を stale 化してはならない"
    assert any(
        s.get("reason") == "checker-role-no-score-by-design"
        for s in result.get("skipped", [])
    )


def test_implementer_live_pid_no_score_still_staled(tmp_path, monkeypatch):
    sf = _write_session(tmp_path, "impl", role="implementer")
    _run_cleanup(tmp_path, monkeypatch)
    state = json.loads(sf.read_text())
    assert state["loop_active"] is False, "implementer の no-score stale 判定は非退行であるべき"
    assert "stale" in state["halt_reason"]


def test_duplicate_pid_warning_emitted(tmp_path, monkeypatch):
    _write_session(tmp_path, "a", role="checker", pid=777)
    _write_session(tmp_path, "b", role="checker", pid=777)
    result = _run_cleanup(tmp_path, monkeypatch, execute=False)
    warnings = result.get("warnings", [])
    assert any(w.get("kind") == "duplicate-pid" and w.get("pid") == 777 for w in warnings), (
        "同一 PID の複数 active session は warning に出るべき")


def test_dead_pid_checker_still_orphan_haltable(tmp_path, monkeypatch):
    sf = _write_session(tmp_path, "deadchk", role="checker", pid=999999)
    monkeypatch.delenv("MISSION_FORCE_PID_IS_AGENT", raising=False)
    monkeypatch.chdir(tmp_path)
    import io, contextlib
    args = type("Args", (), {"root": str(tmp_path), "execute": True})()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        MS.cmd_cleanup_stale(args)
    state = json.loads(sf.read_text())
    assert state["loop_active"] is False, "dead PID の checker は従来どおり orphan 回収されるべき"
