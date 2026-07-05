"""#123: resume コマンド (refresh-pid -> cleanup-empty -> cleanup-stale -> next 統合) のテスト."""

import argparse
import importlib.util
import json
from pathlib import Path

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("gs_resume", MISSION_STATE_PY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _make_foreign_state(proj_dir, sid="cx-foreign", pid=99999999, **overrides):
    """cwd 配下のサブディレクトリに別セッションの dead-PID state を作る."""
    sd = proj_dir / ".mission-state" / "sessions"
    sd.mkdir(parents=True, exist_ok=True)
    data = {
        "mission": "foreign", "mission_id": "def67890", "subtasks": [],
        "complexity": "Standard", "reviewer_count": 2, "max_iter": 5, "threshold": 4.0,
        "iteration": 1, "phase": "executing", "score_history": [], "stagnation_count": 0,
        "decisions": [], "loop_active": True, "passes": False, "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": "2026-06-07T00:00:00Z", "updated_at": "2026-06-07T00:00:00Z",
        "schema_version": 2, "project_root": str(proj_dir), "pid": pid, "hostname": "test",
        "session_id": sid, "created_at_session": "2026-06-07T00:00:00Z",
    }
    data.update(overrides)
    (sd / f"{sid}.json").write_text(json.dumps(data))
    return sd / f"{sid}.json"


def test_resume_invokes_subcommands_in_fixed_order(tmp_path, monkeypatch):
    """順序保証を直接検証: resume は refresh-pid → cleanup-empty → cleanup-stale → next の順で呼ぶ。

    Major review 対応: 実運用の危険シナリオ (loop_active=True かつ dead pid) は
    MISSION_FORCE_PID_IS_AGENT が全 pid に効くため subprocess では作れない。そこで
    合成対象の cmd_* を stub に差し替え、呼び出し順そのもの (refresh-pid < cleanup-stale)
    を機械的に検証する。これが「自 state を先に pid 更新してから cleanup する」保証の本体。
    """
    m = _load_module()
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "test.json").write_text(json.dumps({
        "loop_active": True, "passes": False, "halt_reason": "",
        "phase": "executing", "iteration": 1, "session_id": "test",
        "project_root": str(tmp_path), "pid": 0, "score_history": [],
    }))
    calls = []

    def _rec(name, out):
        def stub(ns):
            calls.append(name)
            print(out)
        return stub

    monkeypatch.setattr(m, "cmd_refresh_pid", _rec("refresh-pid", '{"reactivated": false}'))
    monkeypatch.setattr(m, "cmd_cleanup_empty", _rec("cleanup-empty", '{"action": "nothing"}'))
    monkeypatch.setattr(m, "cmd_cleanup_stale", _rec("cleanup-stale", '{"halted": []}'))
    monkeypatch.setattr(m, "cmd_next", _rec("next", '{"next_action": "run-planner"}'))
    monkeypatch.chdir(tmp_path)

    m.cmd_resume(argparse.Namespace(force=False, dry_run=False))

    assert calls == ["refresh-pid", "cleanup-empty", "cleanup-stale", "next"]
    # 核心の不変条件: refresh-pid は cleanup-stale より必ず先。
    assert calls.index("refresh-pid") < calls.index("cleanup-stale")


def test_resume_active_state_returns_next_action(state_dir, run_cli):
    """active state で resume すると next_action と resume サマリを返す (rc 0)。

    FORCE=1 で refresh 後 pid を agent 扱いに固定し、cleanup-stale が自 state を halt しない
    ようにして (非 agent CI 環境での orphan halt 誤 pass を防ぐ)、真に planning 系が返ることを見る。
    """
    r = run_cli("resume", "--json", cwd=state_dir.parent,
                env_extra={"MISSION_FORCE_PID_IS_AGENT": "1"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out.get("next_action") in {"run-planner", "run-executor", "run-reviewers", "run-scorer", "resume"}
    assert "resume" in out
    assert set(out["resume"]) >= {"pid_refreshed", "reactivated", "cleaned_empty", "halted_stale", "dry_run"}
    assert out["resume"]["pid_refreshed"] is True


def test_resume_refreshes_self_pid(state_dir, run_cli, read_state):
    """resume は自 state.pid を更新する (refresh-pid が実際に走っている証跡)."""
    before = json.loads((state_dir / "sessions" / "test.json").read_text())["pid"]
    assert before == 0
    run_cli("resume", "--json", cwd=state_dir.parent, check=True)
    after = json.loads((state_dir / "sessions" / "test.json").read_text())["pid"]
    assert after != 0  # 現 agent CLI (テストでは親プロセス) の pid に更新された


def test_resume_no_state_returns_init(tmp_path, run_cli):
    """state が無い場合は next_action=init を返し exit 0 (init 誘導)."""
    r = run_cli("resume", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["next_action"] == "init"
    assert out["resume"]["pid_refreshed"] is False


def test_resume_reactivates_orphan_halt(state_dir, run_cli):
    """orphan halt された自 state を resume (内部 refresh-pid) が再活性化する。

    注: これは refresh-pid の再活性化機能の検証であって、順序保証そのものではない
    (loop_active=False の state は cleanup-stale が無条件 skip するため前後で結果は同じ)。
    順序保証は test_resume_invokes_subcommands_in_fixed_order が担う。
    FORCE=1 は cleanup-stale が (再活性化後の) 自 state を再 halt しないための固定。
    """
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["loop_active"] = False
    data["halt_reason"] = "orphan: pid 111 dead"
    sf.write_text(json.dumps(data))

    r = run_cli("resume", "--json", cwd=state_dir.parent,
                env_extra={"MISSION_FORCE_PID_IS_AGENT": "1"})
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["resume"]["reactivated"] is True

    after = json.loads(sf.read_text())
    assert after["loop_active"] is True   # 再活性化された
    assert after["halt_reason"] == ""     # halt 解除
    # 自 state は halt されていない (refresh-pid が cleanup-stale より先に走った証跡)
    assert after["passes"] is False


def test_resume_halts_foreign_dead_pid_state(state_dir, run_cli):
    """cwd 配下の別セッション dead-PID state は resume の cleanup-stale で halt される."""
    foreign = _make_foreign_state(state_dir.parent / "subproj", pid=99999999)
    r = run_cli("resume", "--json", cwd=state_dir.parent)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["resume"]["halted_stale"] >= 1
    fdata = json.loads(foreign.read_text())
    assert fdata["loop_active"] is False
    assert "orphan" in fdata["halt_reason"]


def test_resume_dry_run_does_not_halt(state_dir, run_cli):
    """--dry-run では cleanup-stale が halt を実行しない."""
    foreign = _make_foreign_state(state_dir.parent / "subproj", pid=99999999)
    r = run_cli("resume", "--json", "--dry-run", cwd=state_dir.parent)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["resume"]["dry_run"] is True
    assert out["resume"]["halted_stale"] == 0
    fdata = json.loads(foreign.read_text())
    assert fdata["loop_active"] is True  # dry-run なので halt されない
