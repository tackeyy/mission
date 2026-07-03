"""Tier4: カバレッジ穴埋め (refresh-pid / get / list / cleanup-empty / e2e chain).

過去 0 件だった cmd の特性テスト + push-score→mark-passes→hook unblock の連鎖検証。
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
HOOK = Path(__file__).resolve().parents[3] / "scripts" / "mission-stop-guard.sh"


def _load():
    spec = importlib.util.spec_from_file_location("mission_state_mod_t4", MISSION_STATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_hook(cwd, env_extra):
    import subprocess
    env = {"PATH": os.environ["PATH"], "MISSION_HOOK_CWD": str(cwd)}
    env.update(env_extra)
    return subprocess.run(["bash", str(HOOK)], input='{"stop_hook_active":false}',
                          capture_output=True, text=True, env=env)


# ===== cmd_get =====
def test_get_field_and_full(tmp_path, run_cli):
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    r = run_cli("get", "--field", "complexity", cwd=tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout) == "Standard"
    full = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert full["mission"] == "g"


def test_get_missing_state_exit1(tmp_path, run_cli):
    r = run_cli("get", cwd=tmp_path)
    assert r.returncode == 1 and json.loads(r.stdout)["ok"] is False


# ===== cmd_cleanup_empty =====
def test_cleanup_empty_removes_empty_dir(tmp_path, run_cli):
    (tmp_path / ".mission-state").mkdir()
    r = run_cli("cleanup-empty", str(tmp_path), cwd=tmp_path)
    assert json.loads(r.stdout)["action"] == "removed"
    assert not (tmp_path / ".mission-state").exists()


def test_cleanup_empty_nothing_when_absent(tmp_path, run_cli):
    r = run_cli("cleanup-empty", str(tmp_path), cwd=tmp_path)
    assert json.loads(r.stdout)["action"] == "nothing"


def test_cleanup_empty_skips_nonempty(tmp_path, run_cli):
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    r = run_cli("cleanup-empty", str(tmp_path), cwd=tmp_path)
    assert json.loads(r.stdout)["action"] == "skipped"


# ===== cmd_list (in-process: _default_search_roots を tmp に向ける) =====
def test_list_reports_session(tmp_path, monkeypatch, capsys):
    mod = _load()
    monkeypatch.setattr(mod, "_default_search_roots", lambda: [tmp_path])
    sd = tmp_path / "proj" / ".mission-state" / "sessions"
    sd.mkdir(parents=True)
    (sd / "s1.json").write_text(json.dumps({
        "project_root": str(tmp_path / "proj"), "loop_active": True,
        "mission": "g", "mission_id": "x", "session_id": "s1", "iteration": 0,
    }))
    mod.cmd_list(argparse.Namespace())
    data = json.loads(capsys.readouterr().out)
    assert any(e.get("session_id") == "s1" for e in data)


# ===== cmd_refresh_pid =====
def test_refresh_pid_reactivates_orphan_halt(tmp_path, run_cli):
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    run_cli("mark-halt", "--reason", "orphan: pid 999999 dead", cwd=tmp_path)
    r = run_cli("refresh-pid", cwd=tmp_path)
    assert r.returncode == 0 and json.loads(r.stdout)["reactivated"] is True
    st = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert st["loop_active"] is True and st["halt_reason"] == ""


def test_refresh_pid_no_reactivate_keeps_orphan_halt(tmp_path, run_cli):
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    run_cli("mark-halt", "--reason", "orphan: dead", cwd=tmp_path)
    run_cli("refresh-pid", "--no-reactivate", cwd=tmp_path)
    st = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert st["loop_active"] is False and st["halt_reason"] == "orphan: dead"


def test_refresh_pid_non_orphan_halt_not_reactivated(tmp_path, run_cli):
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    run_cli("mark-halt", "--reason", "user stop", cwd=tmp_path)
    run_cli("refresh-pid", cwd=tmp_path)
    st = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert st["halt_reason"] == "user stop" and st["loop_active"] is False


def test_refresh_pid_rejects_alive_agent_owner(tmp_path, monkeypatch, run_cli):
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    mod = _load()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setattr(mod, "_pid_is_agent", lambda p: True)   # 既存 owner を alive agent CLI と擬制
    monkeypatch.setattr(mod, "find_agent_pid", lambda: 1234567)  # 別 pid
    with pytest.raises(SystemExit) as ei:
        mod.cmd_refresh_pid(argparse.Namespace(force=False, no_reactivate=False))
    assert ei.value.code == 2


# ===== e2e: push-score → mark-passes → stop hook unblock =====
def test_e2e_pushscore_markpasses_hook_unblocks(tmp_path, run_cli):
    sid = {"MISSION_SESSION_ID": "e2e"}
    run_cli("init", "g", "--complexity", "Simple", "--threshold", "4.0",
            cwd=tmp_path, env_extra=sid)
    before = _run_hook(tmp_path, sid)
    assert "block" in before.stdout, "未達 state は hook が block すべき"
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", '{"a":4.5}', cwd=tmp_path, env_extra=sid)
    mp = run_cli("mark-passes", cwd=tmp_path, env_extra=sid)
    assert mp.returncode == 0, mp.stderr
    after = _run_hook(tmp_path, sid)
    assert "block" not in after.stdout, "合格後は hook が block しない"


def test_refresh_pid_force_overrides_alive_owner(tmp_path, monkeypatch, run_cli):
    """C#1: --force なら alive agent CLI owner でも拒否せず継承する (line 690 の force ブランチ)."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    mod = _load()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setattr(mod, "_pid_is_agent", lambda p: True)
    monkeypatch.setattr(mod, "find_agent_pid", lambda: 99999)
    mod.cmd_refresh_pid(argparse.Namespace(force=True, no_reactivate=False))  # exit しない
    st = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert st["pid"] == 99999


def test_set_rejects_frozen_field(tmp_path, run_cli):
    """C#2: FROZEN_FIELDS (mission_id 等) の set は exit 2 で拒否される (不変条件ガード)."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    r = run_cli("set", "mission_id=hack", cwd=tmp_path)
    assert r.returncode == 2 and "変更不可" in r.stderr


def test_set_rejects_pass_and_score_gate_fields(tmp_path, run_cli):
    """#90: pass/score/threshold は set で変更できず、専用 gate 経路だけが更新できる。"""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    forbidden = [
        "passes=true",
        "passes_forced=true",
        "force_reason=manual",
        'score_history=[{"composite":5.0,"min_item":5.0,"open_high":0}]',
        "threshold=0",
    ]
    for kv in forbidden:
        r = run_cli("set", kv, cwd=tmp_path)
        assert r.returncode == 2, f"{kv} should be frozen, stderr={r.stderr}"


def test_set_still_allows_loop_active_reactivation(tmp_path, run_cli):
    """#90: loop_active は凍結せず、既存の再活性化経路を維持する。"""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "react"})
    run_cli("mark-halt", "--reason", "manual stop", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "react"}, check=True)

    r = run_cli("set", "loop_active=true", "halt_reason=", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "react"})

    assert r.returncode == 0, r.stderr
    data = json.loads((tmp_path / ".mission-state" / "sessions" / "react.json").read_text())
    assert data["loop_active"] is True
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert "react" in agg["active_sessions"]


def test_push_score_rejects_bad_items_and_range(tmp_path, run_cli):
    """A#1: _validate_score_args の異常系 (不正JSON / 非dict / 範囲外) は exit 1."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path)
    base = ["push-score", "--iteration", "1", "--composite", "4.0", "--min-item", "4.0"]
    assert run_cli(*base, "--items", "not json", cwd=tmp_path).returncode == 1
    assert run_cli(*base, "--items", "[1,2]", cwd=tmp_path).returncode == 1
    assert run_cli("push-score", "--iteration", "1", "--composite", "9.9",
                   "--min-item", "4.0", "--items", '{"a":4}', cwd=tmp_path).returncode == 1


def test_init_survives_corrupt_aggregate(tmp_path, run_cli):
    """F-6: aggregate.json が壊れていても init が JSONDecodeError で落ちない."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "a"})
    (tmp_path / ".mission-state" / "aggregate.json").write_text("{ broken json !!!")
    r = run_cli("init", "g2", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "b"})
    assert r.returncode == 0, f"corrupt aggregate で init が落ちた: {r.stderr}"


def test_refresh_pid_readds_to_aggregate_on_reactivate(tmp_path, run_cli):
    """F-4: orphan halt で aggregate から除去後、refresh-pid 再活性化で再追加される."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "r1"})
    run_cli("mark-halt", "--reason", "orphan: dead", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "r1"})
    agg_after_halt = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert "r1" not in agg_after_halt["active_sessions"]
    run_cli("refresh-pid", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "r1"})
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert "r1" in agg["active_sessions"], "refresh-pid 再活性化後に aggregate へ再追加されていない"


def test_refresh_pid_reactivates_stale_halt(tmp_path, run_cli):
    """#97: stale auto-halt も refresh-pid で resume 可能にする。"""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "stale1"})
    run_cli("mark-halt", "--reason", "stale: auto-halted after 180m idle", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "stale1"})

    r = run_cli("refresh-pid", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "stale1"})

    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["reactivated"] is True
    data = json.loads((tmp_path / ".mission-state" / "sessions" / "stale1.json").read_text())
    assert data["loop_active"] is True
    assert data["halt_reason"] == ""
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert "stale1" in agg["active_sessions"]


def test_stats_classifies_abandoned(tmp_path, run_cli, monkeypatch):
    """_classify: loop_active=false/passes=false/halt空 を abandoned に分類 (incomplete と区別)."""
    mod = _load()
    monkeypatch.setattr(mod, "_default_search_roots", lambda: [tmp_path])
    sd = tmp_path / "p" / ".mission-state" / "sessions"; sd.mkdir(parents=True)
    (sd / "ab.json").write_text(json.dumps({
        "project_root": str(tmp_path / "p"), "loop_active": False, "passes": False,
        "halt_reason": "", "mission": "g", "mission_id": "x", "session_id": "ab", "score_history": [],
    }))
    assert mod._classify(json.loads((sd / "ab.json").read_text())) == "abandoned"


def test_set_loop_active_true_readds_to_aggregate(tmp_path, run_cli):
    """F-4: set loop_active=true での再活性化も aggregate へ再追加される(gotchas §2 手順)."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "s4"})
    run_cli("mark-halt", "--reason", "x", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "s4"})
    run_cli("set", "loop_active=true", "halt_reason=", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "s4"})
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert "s4" in agg["active_sessions"], "set loop_active=true で aggregate へ再追加されていない"
