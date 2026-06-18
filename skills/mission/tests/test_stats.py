"""stats サブコマンドのテスト (T6: RED → T7: GREEN)."""
import json
import pytest
from pathlib import Path


def _make_state(proj_dir, **overrides):
    sd = proj_dir / ".mission-state"
    sd.mkdir(parents=True, exist_ok=True)
    default = {
        "mission": "test", "mission_id": "abc",
        "subtasks": [], "complexity": "Standard",
        "reviewer_count": 2, "max_iter": 5, "threshold": 4.0,
        "iteration": 1, "phase": "scoring",
        "score_history": [{"iteration": 1, "composite": 4.2, "min_item": 3.8,
                          "items": {"a": 4.2}, "timestamp": "2026-05-25T00:00:00Z"}],
        "stagnation_count": 0, "decisions": [],
        "loop_active": False, "passes": True, "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": "2026-05-25T00:00:00Z",
        "updated_at": "2026-05-25T00:10:00Z",
        "schema_version": 2,
        "project_root": str(proj_dir),
        "pid": 0, "hostname": "test",
        "session_id": "test", "created_at_session": "2026-05-25T00:00:00Z",
    }
    default.update(overrides)
    # 現行形式 sessions/<sid>.json に書く (legacy state.json は test_stats_reads_legacy_state_json で別途カバー)
    sdir = sd / "sessions"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{default['session_id']}.json").write_text(json.dumps(default))
    return sd


def test_stats_empty_root_returns_zero(tmp_path, run_cli):
    """空ディレクトリで stats を呼んでも落ちずに 0 件を返す."""
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = json.loads(r.stdout)
    assert data["total_sessions"] == 0


def test_stats_counts_pass_and_halt_and_incomplete(tmp_path, run_cli):
    """PASS/HALT/incomplete を分類カウント."""
    _make_state(tmp_path / "p1", passes=True, loop_active=False, halt_reason="")
    _make_state(tmp_path / "p2", passes=False, loop_active=False, halt_reason="user_judgment_required: foo")
    _make_state(tmp_path / "p3", passes=False, loop_active=True, halt_reason="")
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = json.loads(r.stdout)
    assert data["total_sessions"] == 3
    assert data["pass_count"] == 1
    assert data["halt_count"] == 1
    assert data["incomplete_count"] == 1
    assert data["pass_rate"] == pytest.approx(1/3, abs=0.01)
    # abandoned (loop_active=false かつ pass/halt でない) の回帰ガード
    _make_state(tmp_path / "p4", passes=False, loop_active=False, halt_reason="")
    r2 = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert json.loads(r2.stdout)["abandoned_count"] == 1


def test_stats_avg_iterations_and_composite(tmp_path, run_cli):
    """平均 iteration 数と平均 final composite を算出."""
    sh_a = [{"iteration": 1, "composite": 4.0, "min_item": 3.5, "items": {}, "timestamp": "2026-05-25T00:00:00Z"}]
    sh_b = [
        {"iteration": 1, "composite": 3.0, "min_item": 2.5, "items": {}, "timestamp": "2026-05-25T00:00:00Z"},
        {"iteration": 2, "composite": 4.6, "min_item": 4.0, "items": {}, "timestamp": "2026-05-25T00:05:00Z"},
    ]
    _make_state(tmp_path / "p1", iteration=1, score_history=sh_a, passes=True, loop_active=False)
    _make_state(tmp_path / "p2", iteration=2, score_history=sh_b, passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["avg_iterations"] == pytest.approx(1.5)
    assert data["avg_final_composite"] == pytest.approx((4.0 + 4.6) / 2)


def test_stats_avg_session_duration(tmp_path, run_cli):
    """started_at→updated_at の差をセッション時間として平均."""
    _make_state(tmp_path / "p1",
                started_at="2026-05-25T00:00:00Z",
                updated_at="2026-05-25T00:10:00Z",
                passes=True, loop_active=False)
    _make_state(tmp_path / "p2",
                started_at="2026-05-25T00:00:00Z",
                updated_at="2026-05-25T00:30:00Z",
                passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    # (600 + 1800) / 2 = 1200 sec
    assert data["avg_session_duration_sec"] == pytest.approx(1200, abs=1)


def test_stats_period_filter_since(tmp_path, run_cli):
    """--since で期間絞り込み."""
    _make_state(tmp_path / "old", updated_at="2026-05-20T00:00:00Z", passes=True, loop_active=False)
    _make_state(tmp_path / "new", updated_at="2026-05-25T00:00:00Z", passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), "--since", "2026-05-24", "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["total_sessions"] == 1


def test_stats_period_filter_until(tmp_path, run_cli):
    """--until で期間絞り込み."""
    _make_state(tmp_path / "old", updated_at="2026-05-20T00:00:00Z", passes=True, loop_active=False)
    _make_state(tmp_path / "new", updated_at="2026-05-25T00:00:00Z", passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), "--until", "2026-05-22", "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["total_sessions"] == 1


def test_stats_text_output_default(tmp_path, run_cli):
    """--json なしならテキスト整形して返す."""
    _make_state(tmp_path / "p1", passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0
    assert "total_sessions" in r.stdout.lower() or "1" in r.stdout
    assert "{" not in r.stdout[:50]  # JSON ではない


def test_stats_includes_archive_sessions(tmp_path, run_cli):
    """archive/ 配下の state-*.json も集計対象."""
    sd = tmp_path / "p1" / ".mission-state"
    sd.mkdir(parents=True)
    arc = sd / "archive"
    arc.mkdir()
    arc.write_text  # placeholder; we actually write a file below
    archived_state = {
        "mission": "old", "mission_id": "old",
        "subtasks": [], "complexity": "Standard",
        "reviewer_count": 2, "max_iter": 5, "threshold": 4.0,
        "iteration": 1, "phase": "scoring",
        "score_history": [{"iteration": 1, "composite": 4.5, "min_item": 4.0,
                          "items": {}, "timestamp": "2026-05-23T00:00:00Z"}],
        "stagnation_count": 0, "decisions": [],
        "loop_active": False, "passes": True, "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": "2026-05-23T00:00:00Z",
        "updated_at": "2026-05-23T00:10:00Z",
        "schema_version": 2,
        "project_root": str(tmp_path / "p1"),
        "pid": 0, "hostname": "test",
        "session_id": "old", "created_at_session": "2026-05-23T00:00:00Z",
    }
    (arc / "state-archived-1.json").write_text(json.dumps(archived_state))
    _make_state(tmp_path / "p1", passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["total_sessions"] == 2  # current + archived


def test_stats_dedupes_worktree_archive_copy(tmp_path, run_cli):
    """worktree archive に退避した同一 state を active と二重集計しない."""
    proj = tmp_path / "p1"
    sd = _make_state(proj, passes=True, loop_active=False)
    active_state = json.loads((sd / "sessions" / "test.json").read_text())
    worktree_archive = sd / "archive" / "worktree-feature"
    worktree_archive.mkdir(parents=True)
    (worktree_archive / "test.json").write_text(json.dumps(active_state))

    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["total_sessions"] == 1
    assert data["duplicate_state_group_count"] == 1


def test_stats_phase_duration_totals_and_averages(tmp_path, run_cli):
    """phase_durations_sec を横断集計し、phase 別速度を見える化する."""
    _make_state(tmp_path / "p1", phase_durations_sec={"planning": 30, "scoring": 90})
    _make_state(tmp_path / "p2", phase_durations_sec={"planning": 60, "executing": 120})
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["phase_duration_totals_sec"] == {
        "executing": 120.0,
        "planning": 90.0,
        "scoring": 90.0,
    }
    assert data["phase_duration_avg_sec"]["planning"] == pytest.approx(45.0)


# ===== #2: agent 別集計 (2026-06-13 ログ調査) =====
# Claude Code/Codex/CLI どの起動元の成績か内訳を見えるようにする


def test_stats_by_agent_breakdown(tmp_path, run_cli):
    """agent 別 (claude-code/codex) に total/pass/halt を内訳集計する."""
    import json as _json
    _make_state(tmp_path / "p1", agent="claude-code", passes=True, loop_active=False)
    _make_state(tmp_path / "p2", agent="claude-code", passes=True, loop_active=False)
    _make_state(tmp_path / "p3", agent="codex", passes=False, loop_active=False, halt_reason="x")
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = _json.loads(r.stdout)
    assert data["by_agent"]["claude-code"]["total"] == 2
    assert data["by_agent"]["claude-code"]["pass"] == 2
    assert data["by_agent"]["codex"]["total"] == 1
    assert data["by_agent"]["codex"]["halt"] == 1


def test_stats_by_agent_missing_agent_is_unknown(tmp_path, run_cli):
    """agent フィールド欠落の旧 state は 'unknown' に集計される."""
    import json as _json
    _make_state(tmp_path / "p1", passes=True, loop_active=False)  # agent キーなし
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = _json.loads(r.stdout)
    assert data["by_agent"]["unknown"]["total"] == 1
    assert data["by_agent"]["unknown"]["pass"] == 1


def test_stats_median_session_duration(tmp_path, run_cli):
    """median はセッション時間の外れ値に頑健 (放置 run で平均が歪むため併記)."""
    _make_state(tmp_path / "p1", started_at="2026-05-25T00:00:00Z",
                updated_at="2026-05-25T00:10:00Z", passes=True, loop_active=False)  # 10min
    _make_state(tmp_path / "p2", started_at="2026-05-25T00:00:00Z",
                updated_at="2026-05-25T00:30:00Z", passes=True, loop_active=False)  # 30min
    _make_state(tmp_path / "p3", started_at="2026-05-25T00:00:00Z",
                updated_at="2026-05-26T16:40:00Z", passes=True, loop_active=False)  # 2440min outlier
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    # median of [600, 1800, 146400] = 1800
    assert data["median_session_duration_sec"] == pytest.approx(1800, abs=1)
    # 外れ値で平均は median の 5 倍超に歪む (median 併記の意義)
    assert data["avg_session_duration_sec"] > data["median_session_duration_sec"] * 5


def test_stats_reads_legacy_state_json(tmp_path, run_cli):
    """移行期ガード: _iter_state_files が legacy .mission-state/state.json も収集対象に含むこと.

    legacy 読取りパスが将来削除されたら本テストが落ちて検出できる (削除時は本テストも一緒に削除)。
    """
    sd = tmp_path / "p1" / ".mission-state"
    sd.mkdir(parents=True)
    (sd / "state.json").write_text(json.dumps({
        "passes": True, "loop_active": False, "halt_reason": "",
        "mission": "legacy", "mission_id": "x", "session_id": "legacy",
        "score_history": [], "started_at": "2026-05-25T00:00:00Z", "updated_at": "2026-05-25T00:10:00Z",
    }))
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["total_sessions"] == 1
