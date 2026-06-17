"""cleanup-stale の PID 判定修正 (P3-4a, 2026-06-10 検査レポートv2).

バグ: raw os.kill(pid,0) のみで判定するため、PID が agent CLI 以外のプロセスに
再利用されると「alive」として skip し続け、orphan state が永久放置される。
修正: _pid_is_agent() を使い「alive かつ agent CLI プロセス」のみ skip する。
"""
import json
import os
import pytest


def _make_state(proj_dir, **overrides):
    sd = proj_dir / ".mission-state"
    sd.mkdir(parents=True, exist_ok=True)
    default = {
        "mission": "test", "mission_id": "abc",
        "subtasks": [], "complexity": "Standard",
        "reviewer_count": 2, "max_iter": 5, "threshold": 4.0,
        "iteration": 1, "phase": "executing",
        "score_history": [], "stagnation_count": 0, "decisions": [],
        "loop_active": True, "passes": False, "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": "2026-06-07T00:00:00Z",
        "updated_at": "2026-06-07T00:00:00Z",
        "schema_version": 2, "project_root": str(proj_dir),
        "pid": 99999999, "hostname": "test",
        "session_id": "test", "created_at_session": "2026-06-07T00:00:00Z",
    }
    default.update(overrides)
    (sd / "state.json").write_text(json.dumps(default))
    return sd


def test_cleanup_stale_detects_dead_pid(tmp_path, run_cli):
    """存在しない PID は stale (would_halt) 判定."""
    _make_state(tmp_path / "p1", pid=99999999)
    r = run_cli("cleanup-stale", "--root", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = json.loads(r.stdout)
    assert len(data["would_halt"]) == 1


def test_cleanup_stale_detects_pid_reused_by_non_agent(tmp_path, run_cli):
    """PID が agent CLI 以外の生存プロセスに再利用されていても stale 判定する (本修正の核心).

    テスト自身の python プロセス PID は alive だが agent CLI ではない → stale であるべき。
    旧実装は os.kill(pid,0) 成功で skip していた (バグ)。
    """
    _make_state(tmp_path / "p1", pid=os.getpid())
    r = run_cli("cleanup-stale", "--root", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = json.loads(r.stdout)
    assert len(data["would_halt"]) == 1, f"agent CLI以外のPID再利用がskipされた: {data}"


def test_cleanup_stale_execute_halts(tmp_path, run_cli):
    """--execute で halt_reason 書き込み + loop_active=false."""
    sd = _make_state(tmp_path / "p1", pid=99999999)
    r = run_cli("cleanup-stale", "--root", str(tmp_path), "--execute", cwd=tmp_path)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert len(data["halted"]) == 1
    s = json.loads((sd / "state.json").read_text())
    assert s["loop_active"] is False
    assert "orphan" in s["halt_reason"]


def test_cleanup_stale_skips_inactive_and_passed(tmp_path, run_cli):
    """loop_active=false / passes=true は対象外 (既存挙動維持)."""
    _make_state(tmp_path / "p1", loop_active=False, pid=99999999)
    _make_state(tmp_path / "p2", passes=True, pid=99999999)
    r = run_cli("cleanup-stale", "--root", str(tmp_path), cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["would_halt"] == []
    assert data["halted"] == []


def test_cleanup_stale_default_root_unchanged(tmp_path, run_cli):
    """--root 未指定でもエラーにならない (後方互換)."""
    r = run_cli("cleanup-stale", cwd=tmp_path)
    assert r.returncode == 0


def test_cleanup_stale_execute_preserves_phase(tmp_path, run_cli):
    """M4 設計意図: orphan halt は phase を書き換えない (refresh-pid 再活性化後に進捗 phase を保持)."""
    import json
    sd = tmp_path / "proj" / ".mission-state"
    sd.mkdir(parents=True)
    (sd / "state.json").write_text(json.dumps({
        "mission": "orphan mission", "mission_id": "feedf00d12345678", "loop_active": True,
        "passes": False, "halt_reason": "", "phase": "executing", "iteration": 2,
        "score_history": [], "pid": 99999999, "hostname": "test",
    }))
    run_cli("cleanup-stale", "--root", str(tmp_path), "--execute", cwd=tmp_path, check=True)
    s = json.loads((sd / "state.json").read_text())
    assert s["loop_active"] is False
    assert s["halt_reason"].startswith("orphan:")
    assert s["phase"] == "executing"  # 保持される


def test_halt_all_sets_phase_halted(tmp_path, run_cli, monkeypatch):
    """M4: halt --all も phase=halted を設定する."""
    import json
    home = tmp_path / "home"
    proj = home / "dev" / "p1" / ".mission-state"
    proj.mkdir(parents=True)
    (proj / "state.json").write_text(json.dumps({
        "mission": "g", "mission_id": "aa11bb22cc33dd44", "loop_active": True,
        "passes": False, "halt_reason": "", "phase": "executing",
        "score_history": [], "pid": 0,
    }))
    monkeypatch.setenv("HOME", str(home))
    run_cli("halt", "--all", "--reason", "test halt", cwd=tmp_path, check=True)
    s = json.loads((proj / "state.json").read_text())
    assert s["halt_reason"] == "test halt"
    assert s["phase"] == "halted"


# ===== P2-1: project_root 陳腐化追従 =====


def test_update_project_root_updates_state(tmp_path, run_cli):
    """P2-1(a): update-project-root --path <new> が exit0 で state.project_root を更新する.

    実例 cc-48c91727: project_root=/dev/ccbattle が存在せず orphan 判定され続けた
    → update-project-root で正しいパスに更新すれば rescue できる。
    """
    import json
    sd = _make_state(tmp_path / "proj")
    new_root = str(tmp_path / "proj_new")
    r = run_cli("update-project-root", "--path", new_root, cwd=tmp_path / "proj")
    assert r.returncode == 0, f"exit非0: {r.stderr}"
    s = json.loads((sd / "state.json").read_text())
    assert s["project_root"] == new_root, f"project_root が更新されていない: {s['project_root']}"


def test_cleanup_stale_detects_nonexistent_project_root(tmp_path, run_cli):
    """P2-1(b): project_root が存在しないパスの loop_active=true state を would_halt に含める.

    実例 cc-48c91727: project_root=/dev/ccbattle 不存在 → pid チェックだけでは取りこぼす。
    MISSION_FORCE_PROJECT_ROOT_DEAD=1 で _pid_is_agent=True を強制固定し、
    「alive agent であっても project_root 不存在なら孤児扱い」の実装を真に検証する。
    (旧実装は pid=os.getpid() かつ agent CLI でないことで偶然 would_halt に入っていたが、
     それは project_root チェックではなく pid 非agent チェックで引っかかっていただけだった)
    """
    import json
    nonexistent = str(tmp_path / "does_not_exist")
    # MISSION_FORCE_PROJECT_ROOT_DEAD=1 で _pid_is_agent=True 固定
    # → pid チェックではなく project_root 不存在チェックで would_halt に入ることを確認
    _make_state(tmp_path / "proj", pid=os.getpid(), project_root=nonexistent)
    r = run_cli("cleanup-stale", "--root", str(tmp_path), cwd=tmp_path,
                env_extra={"MISSION_FORCE_PROJECT_ROOT_DEAD": "1"})
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = json.loads(r.stdout)
    # _pid_is_agent=True 固定・project_root 不存在 → 孤児扱いで would_halt に入るべき
    assert len(data["would_halt"]) == 1, (
        f"_pid_is_agent=True でも project_root不存在のstateがwould_haltに入っていない: {data}"
    )
