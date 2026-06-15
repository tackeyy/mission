"""Tier3: コード堅牢性 (race / lock占有 / sanitize乖離) の TDD ガード."""
import argparse
import importlib.util
import json
import threading
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load():
    spec = importlib.util.spec_from_file_location("mission_state_mod", MISSION_STATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===== #11: aggregate.json の並列更新は StateLock で直列化される =====
def test_concurrent_mark_halt_aggregate_consistent(tmp_path, run_cli):
    """N セッションを同時 mark-halt しても active_sessions が漏れなく空になる.

    回帰/ストレスガード: race は timing 依存で subprocess 直列化により常時は再現しない
    (本テストは fix 前でも pass し得る)。fix の本体は案A=_remove を StateLock 内へ移動で、
    既に lock 内で呼ぶ cleanup-stale/halt --all と同じパターンに統一する構造的修正。
    """
    sids = [f"S{i}" for i in range(10)]
    for sid in sids:
        r = run_cli("init", f"mission-{sid}", "--complexity", "Standard",
                    cwd=tmp_path, env_extra={"MISSION_SESSION_ID": sid})
        assert r.returncode == 0, r.stderr

    errors = []
    barrier = threading.Barrier(len(sids))  # Low#3: 全スレッドを同時突入させ race window を最大化

    def _halt(sid):
        barrier.wait()
        r = run_cli("mark-halt", "--reason", "stress",
                    cwd=tmp_path, env_extra={"MISSION_SESSION_ID": sid})
        if r.returncode != 0:
            errors.append(r.stderr)

    ts = [threading.Thread(target=_halt, args=(s,)) for s in sids]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors, errors
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert agg["active_sessions"] == [], f"race で sid が残留: {agg['active_sessions']}"


# ===== #12: stamp_metadata は pid 既存時に find_agent_pid (subprocess) を呼ばない =====
def test_stamp_metadata_skips_pid_lookup_when_present(monkeypatch, tmp_path):
    """pid が既にある state に stamp_metadata しても ps subprocess を起動しない.

    setdefault("pid", find_agent_pid()) は pid 存在時も RHS を eager 評価し、
    StateLock 保持中に最大 8x2s の ps を走らせて lock timeout を誘発する。
    """
    mod = _load()

    def _boom():
        raise AssertionError("find_agent_pid が呼ばれた (pid 既存なのに subprocess 起動)")

    monkeypatch.setattr(mod, "find_agent_pid", _boom)
    data = {"pid": 12345, "session_id": "x", "agent": "claude-code"}
    out = mod.stamp_metadata(data, tmp_path)  # 例外が出なければ呼ばれていない
    assert out["pid"] == 12345


# ===== H-1: cleanup_stale / halt --all も aggregate 更新を StateLock 内で行う =====
def test_cleanup_stale_removes_orphan_from_aggregate(tmp_path, run_cli):
    """dead-pid orphan を cleanup-stale --execute すると aggregate から除去される (lock内更新)."""
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "orphanX"})
    run_cli("set", "pid=999999", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "orphanX"})  # dead pid 擬制
    agg0 = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert "orphanX" in agg0["active_sessions"]
    run_cli("cleanup-stale", "--root", str(tmp_path), "--execute", cwd=tmp_path)
    agg1 = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert "orphanX" not in agg1["active_sessions"], "cleanup-stale が aggregate から除去していない"


def test_halt_all_removes_from_aggregate(tmp_path, run_cli, monkeypatch):
    """halt --all 後、対象 sid が aggregate から除去される (lock内更新).

    halt --all の aggregate 除去を in-process(monkeypatch)で検証 (subprocess + --root <tmp> でも代替可)。
    """
    run_cli("init", "g", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "hA"})
    run_cli("init", "g2", "--complexity", "Standard", cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "hB"})
    mod = _load()
    monkeypatch.setattr(mod, "_default_search_roots", lambda: [tmp_path])
    mod.cmd_halt(argparse.Namespace(all=True, reason="all stop"))
    agg = json.loads((tmp_path / ".mission-state" / "aggregate.json").read_text())
    assert agg["active_sessions"] == [], f"halt --all が aggregate を空にしていない: {agg['active_sessions']}"


def test_halt_all_root_scopes_and_spares_outside(tmp_path, run_cli):
    """候補1: halt --all --root <dir> は root 配下のみ halt し、外(別root)は触らない(実ホーム誤halt防止)."""
    a = tmp_path / "a"; b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    run_cli("init", "g", "--complexity", "Standard", cwd=a, env_extra={"MISSION_SESSION_ID": "ina"})
    run_cli("init", "g", "--complexity", "Standard", cwd=b, env_extra={"MISSION_SESSION_ID": "inb"})
    r = run_cli("halt", "--all", "--root", str(a), "--reason", "scoped", cwd=a)
    assert r.returncode == 0, r.stderr
    sta = json.loads((a / ".mission-state" / "sessions" / "ina.json").read_text())
    stb = json.loads((b / ".mission-state" / "sessions" / "inb.json").read_text())
    assert sta["halt_reason"] == "scoped" and sta["loop_active"] is False, "root内がhaltされていない"
    assert stb["loop_active"] is True and stb["halt_reason"] == "", "root外(b)が誤ってhaltされた"
    # --root 経由でも aggregate 除去が走ること / root外は active のまま
    agg_a = json.loads((a / ".mission-state" / "aggregate.json").read_text())
    agg_b = json.loads((b / ".mission-state" / "aggregate.json").read_text())
    assert "ina" not in agg_a["active_sessions"], "--root halt で aggregate 除去されていない"
    assert "inb" in agg_b["active_sessions"], "root外の aggregate が誤って除去された"
