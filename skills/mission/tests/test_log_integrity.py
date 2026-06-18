"""ログ整合性の改善 (2026-06-10, 106ラン分析で発見した3問題への対処).

- 改善1: force-pass の監査可能化 (passes=true 11% が品質ゲート未通過だった)
- 改善2: push-score が top-level iteration を同期 (iteration と score_history 長の不整合 15件)
- 改善3: composite 範囲バリデーション + mark-passes が composite 欠損エントリを読み飛ばす
"""
import json
import pytest


def _make_state(proj_dir, **overrides):
    sd = proj_dir / ".mission-state" / "sessions"
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
    (sd / "test.json").write_text(json.dumps(default))
    return sd


# ===== 改善2: push-score が top-level iteration を同期 =====

def test_push_score_syncs_top_level_iteration(state_dir, run_cli, read_state):
    """push-score --iteration N 後、top-level の iteration が N に同期される."""
    run_cli("push-score", "--iteration", "3", "--composite", "4.2", "--min-item", "4.0",
            "--items", '{"a": 4.2}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["iteration"] == 3


def test_push_score_iteration_advances_with_history(state_dir, run_cli, read_state):
    """連続 push-score で top-level iteration が最新値を追従する."""
    run_cli("push-score", "--iteration", "1", "--composite", "3.5", "--min-item", "3.0",
            "--items", '{"a": 3.5}', cwd=state_dir.parent, check=True)
    run_cli("push-score", "--iteration", "2", "--composite", "4.2", "--min-item", "4.0",
            "--items", '{"a": 4.2}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["iteration"] == 2
    assert len(s["score_history"]) == 2


# ===== 改善3a: composite / min-item の範囲バリデーション =====

def test_push_score_rejects_out_of_range_composite(state_dir, run_cli):
    """composite が [0,5] 範囲外なら reject."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "9.0", "--min-item", "4.0",
                "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode != 0
    assert "composite" in r.stderr.lower()


def test_push_score_rejects_negative_composite(state_dir, run_cli):
    """composite が負なら reject."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "-1.0", "--min-item", "4.0",
                "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode != 0


def test_push_score_rejects_nan_composite(state_dir, run_cli):
    """composite が NaN なら reject (float('nan') は argparse を通過してしまう)."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "nan", "--min-item", "4.0",
                "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode != 0


def test_push_score_accepts_boundary_values(state_dir, run_cli, read_state):
    """境界値 0 と 5 は許容."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "5.0", "--min-item", "0.0",
                "--items", '{"a": 5.0}', cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"


# ===== 改善3b: mark-passes が composite 欠損エントリを読み飛ばす =====

def test_mark_passes_uses_latest_scored_entry(state_dir, run_cli, read_state):
    """末尾に composite 欠損の進捗ノートが混入していても、直近の採点エントリで gate 判定する."""
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["score_history"] = [
        {"iteration": 1, "composite": 4.5, "min_item": 4.0, "items": {"a": 4.5}, "timestamp": "2026-05-25T00:00:00Z"},
        {"phase": "phase_1", "iter": 1, "passed": True, "commit": "abc123", "note": "実機確認"},  # composite 欠損
    ]
    sf.write_text(json.dumps(data))
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_rejects_when_no_scored_entry(state_dir, run_cli):
    """composite を持つエントリが一つもなければ採点未実施として reject."""
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["score_history"] = [
        {"phase": "phase_1", "iter": 1, "passed": True, "note": "ノートのみ"},
    ]
    sf.write_text(json.dumps(data))
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2


# ===== 改善1: force-pass の監査可能化 =====

def test_mark_passes_force_sets_forced_flag(state_dir, run_cli, read_state):
    """--force で合格させると passes_forced=True が記録される (監査用)."""
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["score_history"] = []
    sf.write_text(json.dumps(data))
    r = run_cli("mark-passes", "--force", "--reason", "Reviewer 取得不能",
                cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True
    assert s["passes_forced"] is True
    assert s["force_reason"] == "Reviewer 取得不能"


def test_normal_pass_does_not_set_forced_flag(state_dir, run_cli, read_state):
    """通常合格では passes_forced は False (または未設定)."""
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", '{"a": 4.5}', cwd=state_dir.parent, check=True)
    run_cli("mark-passes", cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s.get("passes_forced", False) is False


def test_stats_reports_forced_pass_count(tmp_path, run_cli):
    """stats が force-pass 件数とその比率を集計する (品質ゲート未通過の可視化)."""
    _make_state(tmp_path / "p1", passes=True, loop_active=False)  # 通常合格
    _make_state(tmp_path / "p2", passes=True, loop_active=False,
                passes_forced=True, force_reason="Reviewer 取得不能", score_history=[])  # force-pass
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = json.loads(r.stdout)
    assert data["forced_pass_count"] == 1
    assert data["forced_pass_rate"] == pytest.approx(0.5)


def test_stats_final_composite_uses_latest_scored_entry(tmp_path, run_cli):
    """末尾が composite 欠損エントリでも、直近の採点値を avg_final_composite に含める."""
    sh = [
        {"iteration": 1, "composite": 4.0, "min_item": 3.5, "items": {}, "timestamp": "2026-05-25T00:00:00Z"},
        {"phase": "phase_1", "note": "ノート"},  # composite 欠損
    ]
    _make_state(tmp_path / "p1", score_history=sh, passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["avg_final_composite"] == pytest.approx(4.0)


def test_stats_reports_ungated_pass_count(tmp_path, run_cli):
    """採点記録なしで passes=true (ゲートバイパス) を ungated として集計する."""
    _make_state(tmp_path / "p1", passes=True, loop_active=False)  # 正常 (採点あり)
    _make_state(tmp_path / "p2", passes=True, loop_active=False, score_history=[])  # ゲートバイパス
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    data = json.loads(r.stdout)
    assert data["ungated_pass_count"] == 1
    assert data["ungated_pass_rate"] == pytest.approx(0.5)


# ===== code-review 指摘の回帰テスト =====

def test_mark_passes_rejects_nan_in_old_data(state_dir, run_cli):
    """旧データに NaN composite が残っていても gate を迂回させない (指摘1)."""
    sf = state_dir / "sessions" / "test.json"
    data = json.loads(sf.read_text())
    data["score_history"] = [
        {"iteration": 1, "composite": float("nan"), "min_item": 4.0, "items": {}, "timestamp": "2026-05-25T00:00:00Z"},
    ]
    sf.write_text(json.dumps(data))
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2, f"NaN で合格してはいけない: stderr={r.stderr}"


def test_stats_old_force_pass_not_counted_as_ungated(tmp_path, run_cli):
    """旧版 force-pass (force_reason のみ・passes_forced 未記録) を ungated に誤計上しない (指摘2)."""
    _make_state(tmp_path / "p1", passes=True, loop_active=False,
                score_history=[], force_reason="旧版で force した")
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    assert data["ungated_pass_count"] == 0


def test_stats_avg_final_composite_skips_nan(tmp_path, run_cli):
    """NaN composite を avg_final_composite に混入させない (指摘1)."""
    sh = [{"iteration": 1, "composite": float("nan"), "min_item": 4.0, "items": {}, "timestamp": "2026-05-25T00:00:00Z"}]
    _make_state(tmp_path / "p1", score_history=sh, passes=True, loop_active=False)
    r = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    data = json.loads(r.stdout)
    # NaN のみなら final 値なし = None (NaN を平均に混ぜない)
    assert data["avg_final_composite"] is None


# ===== M4: phase フィールド自動更新 (2026-06-10 検査レポート) =====
# 全ランで phase が "planning" のまま更新されず、R1 復帰手順 (phase 基準) が誤動作するリスク


def _set_phase(state_dir, phase):
    import json
    sf = state_dir / "sessions" / "test.json"
    d = json.loads(sf.read_text())
    d["phase"] = phase
    sf.write_text(json.dumps(d))


def _set_phase_started_at(state_dir, timestamp):
    import json
    sf = state_dir / "sessions" / "test.json"
    d = json.loads(sf.read_text())
    d["phase_started_at"] = timestamp
    d["phase_durations_sec"] = {}
    sf.write_text(json.dumps(d))


def test_push_score_updates_phase_to_scoring(state_dir, run_cli, read_state):
    _set_phase(state_dir, "planning")
    run_cli("push-score", "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0}', cwd=state_dir.parent, check=True)
    assert read_state(state_dir)["phase"] == "scoring"


def test_push_score_records_previous_phase_duration(state_dir, run_cli, read_state):
    _set_phase(state_dir, "planning")
    _set_phase_started_at(state_dir, "2026-05-25T00:00:00Z")
    run_cli("push-score", "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0}', cwd=state_dir.parent, check=True)
    state = read_state(state_dir)
    assert state["phase"] == "scoring"
    assert state["phase_started_at"] == state["updated_at"]
    assert state["phase_durations_sec"]["planning"] >= 0


def test_mark_passes_updates_phase_to_done(state_dir, run_cli, read_state):
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", '{"mission_achievement": 4.5}', cwd=state_dir.parent, check=True)
    run_cli("mark-passes", cwd=state_dir.parent, check=True)
    assert read_state(state_dir)["phase"] == "done"


def test_mark_passes_records_scoring_duration(state_dir, run_cli, read_state):
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", '{"mission_achievement": 4.5}', cwd=state_dir.parent, check=True)
    _set_phase_started_at(state_dir, "2026-05-25T00:00:00Z")
    run_cli("mark-passes", cwd=state_dir.parent, check=True)
    state = read_state(state_dir)
    assert state["phase"] == "done"
    assert state["phase_durations_sec"]["scoring"] >= 0


def test_mark_halt_updates_phase_to_halted(state_dir, run_cli, read_state):
    run_cli("mark-halt", "--reason", "max-iter reached", cwd=state_dir.parent, check=True)
    assert read_state(state_dir)["phase"] == "halted"


def test_mark_halt_records_current_phase_duration(state_dir, run_cli, read_state):
    _set_phase(state_dir, "executing")
    _set_phase_started_at(state_dir, "2026-05-25T00:00:00Z")
    run_cli("mark-halt", "--reason", "max-iter reached", cwd=state_dir.parent, check=True)
    state = read_state(state_dir)
    assert state["phase"] == "halted"
    assert state["phase_durations_sec"]["executing"] >= 0


def test_mark_passes_min_item_error_mentions_scored_items(state_dir, run_cli):
    """C-M1: min_item gate のエラー文言が「5項目」固定でなく採点 items 基準である."""
    run_cli("push-score", "--iteration", "1", "--composite", "4.5", "--min-item", "3.0",
            "--items", '{"mission_achievement": 4.5, "accuracy": 3.0}', cwd=state_dir.parent, check=True)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2
    assert "5項目" not in r.stderr
