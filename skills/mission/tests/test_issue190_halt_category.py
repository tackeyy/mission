"""Issue #190: mark-halt --category による halt 理由の構造化。

実運用で threshold 未達 (composite 3.92) のランが「完了しました」という完了風の自由文
halt_reason で終端し、stats/audit 上は障害 halt と同じ HALT に集計されて区別できなかった
実害への対策。halt_category を導入し、部分達成 (partial-done) と障害・stale 系を分離する。
"""
import json


def test_mark_halt_with_valid_category(state_dir, run_cli, read_state):
    r = run_cli("mark-halt", "--reason", "external API を待つ", "--category", "blocked-external",
                cwd=state_dir.parent, check=True)
    assert r.returncode == 0
    s = read_state(state_dir)
    assert s["halt_reason"] == "external API を待つ"
    assert s["halt_category"] == "blocked-external"
    out = json.loads(r.stdout)
    assert out["halt_category"] == "blocked-external"


def test_mark_halt_without_category_defaults_to_other_with_warning(state_dir, run_cli, read_state):
    r = run_cli("mark-halt", "--reason", "理由のみ", cwd=state_dir.parent, check=True)
    assert r.returncode == 0
    assert "WARNING" in r.stderr
    assert read_state(state_dir)["halt_category"] == "other"


def test_mark_halt_with_invalid_category_falls_back_to_other_with_warning(state_dir, run_cli, read_state):
    """halt は緊急停止経路なので、不正カテゴリでも halt 自体は成功させる (exit 0)."""
    r = run_cli("mark-halt", "--reason", "x", "--category", "bogus-category",
                cwd=state_dir.parent, check=True)
    assert r.returncode == 0
    assert "WARNING" in r.stderr
    assert read_state(state_dir)["halt_category"] == "other"


def test_mark_halt_partial_done_category(state_dir, run_cli, read_state):
    """実運用の実害ケース: scope の実行可能分は完遂したが threshold 未達で halt."""
    r = run_cli("mark-halt", "--reason", "実行可能な項目は完了、threshold未達",
                "--category", "partial-done", cwd=state_dir.parent, check=True)
    assert r.returncode == 0
    assert read_state(state_dir)["halt_category"] == "partial-done"


def test_cleanup_stale_orphan_auto_classifies_as_stale(tmp_path, run_cli):
    """cleanup-stale の orphan 検出は halt_category='stale' を自動記録する."""
    sd = tmp_path / ".mission-state"
    sessions = sd / "sessions"
    sessions.mkdir(parents=True)
    sf = sessions / "s1.json"
    sf.write_text(json.dumps({
        "mission": "test", "mission_id": "abc", "session_id": "s1",
        "loop_active": True, "passes": False, "halt_reason": "",
        "pid": 999999, "project_root": str(tmp_path),
        "phase": "executing", "score_history": [],
        "started_at": "2026-05-25T00:00:00Z", "updated_at": "2026-05-25T00:00:00Z",
        "schema_version": 2,
    }))
    r = run_cli("cleanup-stale", "--root", str(tmp_path), "--execute", cwd=tmp_path, check=True)
    assert r.returncode == 0
    updated = json.loads(sf.read_text())
    assert updated["halt_category"] == "stale"
    assert updated["halt_reason"].startswith("orphan:")


def test_halt_all_records_category(tmp_path, run_cli):
    sd = tmp_path / "proj" / ".mission-state"
    sessions = sd / "sessions"
    sessions.mkdir(parents=True)
    sf = sessions / "s1.json"
    sf.write_text(json.dumps({
        "mission": "test", "mission_id": "abc", "session_id": "s1",
        "loop_active": True, "passes": False, "halt_reason": "",
        "pid": 0, "project_root": str(tmp_path / "proj"),
        "phase": "executing", "score_history": [],
        "started_at": "2026-05-25T00:00:00Z", "updated_at": "2026-05-25T00:00:00Z",
        "schema_version": 2,
    }))
    r = run_cli("halt", "--all", "--reason", "一括停止", "--category", "user-abort",
                "--root", str(tmp_path), cwd=tmp_path, check=True)
    assert r.returncode == 0
    updated = json.loads(sf.read_text())
    assert updated["halt_category"] == "user-abort"


def test_stats_text_shows_halt_category_breakdown(tmp_path, run_cli):
    sd = tmp_path / ".mission-state" / "sessions"
    sd.mkdir(parents=True)
    (sd / "h1.json").write_text(json.dumps({
        "mission": "m1", "mission_id": "a1", "session_id": "h1",
        "loop_active": False, "passes": False, "halt_reason": "x",
        "halt_category": "partial-done",
        "phase": "halted", "score_history": [], "iteration": 1,
        "started_at": "2026-05-25T00:00:00Z", "updated_at": "2026-05-25T00:10:00Z",
        "schema_version": 2, "project_root": str(tmp_path),
        "pid": 0, "hostname": "test", "created_at_session": "2026-05-25T00:00:00Z",
    }))
    (sd / "h2.json").write_text(json.dumps({
        "mission": "m2", "mission_id": "a2", "session_id": "h2",
        "loop_active": False, "passes": False, "halt_reason": "y",
        "halt_category": "stale",
        "phase": "halted", "score_history": [], "iteration": 1,
        "started_at": "2026-05-25T00:00:00Z", "updated_at": "2026-05-25T00:10:00Z",
        "schema_version": 2, "project_root": str(tmp_path),
        "pid": 0, "hostname": "test", "created_at_session": "2026-05-25T00:00:00Z",
    }))
    r = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path, check=True)
    assert "partial-done" in r.stdout
    assert "stale" in r.stdout

    r_json = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True)
    data = json.loads(r_json.stdout)
    assert data["by_halt_category"] == {"partial-done": 1, "stale": 1}
