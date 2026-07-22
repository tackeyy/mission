"""Issue #237 (F2): atomic `advance` — phase 遷移と activity 切替の単一コマンド化.

背景 (Codex 実行速度監査 F2): activity 計測が opt-in のため strict cohort の
activity coverage が 9.96% に留まり、速度改善の根拠が作れない。`set phase=` と
`activity start` が別コマンドのため「phase だけ進んで activity が空」の state を
作れてしまうのが構造要因。

`advance --phase <phase> --activity <kind>:<reason>` は両者を 1 lock で atomic に
行い、片方だけ進んだ state を機械的に作れなくする。

- terminal phase (done/halted) への遷移は mark-passes / mark-halt 専用のため reject
  (advance を pass gate の迂回路にしない)。
- crash / 放置時間の unobserved 分類は #211 の trusted boundary
  (test_issue211_activity_segments.py::test_terminal_closes_only_to_last_trusted_update_...)
  が既にカバーする。本ファイルでは再テストしない。
"""
import json


def _read(tmp_path):
    return json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())


def test_advance_sets_phase_and_activity_atomically(run_cli, tmp_path):
    run_cli("init", "advance test", "--complexity", "Standard", cwd=tmp_path, check=True)
    r = run_cli("advance", "--phase", "executing", "--activity", "active:implementation",
                cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = _read(tmp_path)
    assert s["phase"] == "executing"
    cur = s["activity_current"]
    assert cur is not None and cur["kind"] == "active" and cur["reason"] == "implementation"
    assert cur["phase"] == "executing"


def test_advance_rejects_invalid_activity_and_leaves_phase_unchanged(run_cli, tmp_path):
    """activity が不正なら phase も進まない (atomic の核)."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    before = _read(tmp_path)["phase"]
    r = run_cli("advance", "--phase", "executing", "--activity", "bogus:thing", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    s = _read(tmp_path)
    assert s["phase"] == before
    assert s.get("activity_current") in (None, {})


def test_advance_rejects_malformed_activity_format(run_cli, tmp_path):
    """kind:reason 形式でない --activity は reject."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    r = run_cli("advance", "--phase", "executing", "--activity", "active", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"


def test_advance_rejects_invalid_phase(run_cli, tmp_path):
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    r = run_cli("advance", "--phase", "deploying", "--activity", "active:work", cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"


def test_advance_rejects_terminal_phase(run_cli, tmp_path):
    """done/halted への遷移は mark-passes / mark-halt 専用 (gate 迂回の防止)."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    for terminal in ("done", "halted"):
        r = run_cli("advance", "--phase", terminal, "--activity", "active:work", cwd=tmp_path)
        assert r.returncode == 2, f"phase={terminal}: expected exit 2, got {r.returncode}"
        assert "mark-" in r.stderr


def test_advance_normalizes_phase_alias_with_warning(run_cli, tmp_path):
    """#188 の別名正規化 (execution→executing) を advance でも適用する."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    r = run_cli("advance", "--phase", "execution", "--activity", "active:implementation",
                cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert _read(tmp_path)["phase"] == "executing"
    assert "#188" in r.stderr or "executing" in r.stderr


def test_advance_within_same_phase_switches_activity(run_cli, tmp_path):
    """同一 phase 内での activity 切替にも使える (旧 segment を close して記録)."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    run_cli("advance", "--phase", "executing", "--activity", "active:implementation",
            cwd=tmp_path, check=True)
    r = run_cli("advance", "--phase", "executing", "--activity",
                "reviewer-wait:review-response", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = _read(tmp_path)
    assert s["phase"] == "executing"
    assert s["activity_current"]["kind"] == "reviewer-wait"
    segs = s.get("activity_segments") or []
    assert len(segs) >= 1
    assert segs[-1]["kind"] == "active"


def test_advance_accrues_previous_phase_duration(run_cli, tmp_path):
    """phase 遷移時に旧 phase の経過が phase_durations_sec へ加算される (set phase= と同等)."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    run_cli("advance", "--phase", "planning", "--activity", "active:planning",
            cwd=tmp_path, check=True)
    run_cli("advance", "--phase", "executing", "--activity", "active:implementation",
            cwd=tmp_path, check=True)
    s = _read(tmp_path)
    durations = s.get("phase_durations_sec") or {}
    assert "planning" in durations
    assert durations["planning"] >= 0


def test_advance_from_halted_state_is_rejected_without_write(run_cli, tmp_path):
    """halt 済み state からの advance は un-halt の迂回路にならない。
    _transition_phase 後の start_activity_segment が terminal 検出で raise し、
    atomic 設計により一切 write されない."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    run_cli("mark-halt", "--reason", "blocked", "--category", "blocked-external",
            cwd=tmp_path, check=True)
    r = run_cli("advance", "--phase", "executing", "--activity", "active:implementation",
                cwd=tmp_path)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    s = _read(tmp_path)
    assert s["phase"] == "halted"
    assert s["loop_active"] is False
    assert s["halt_reason"] == "blocked"


def test_advance_cross_phase_does_not_record_phantom_segment(run_cli, tmp_path):
    """cross-phase advance で _transition_phase のキャリーフォワードが作る
    「旧 reason + 新 phase・0秒」の phantom segment を記録しない (レビュー指摘)。
    旧 segment は旧 phase・旧 reason のまま 1 件だけ閉じる."""
    run_cli("init", "advance test", cwd=tmp_path, check=True)
    run_cli("advance", "--phase", "planning", "--activity", "active:planning",
            cwd=tmp_path, check=True)
    run_cli("advance", "--phase", "executing", "--activity", "active:implementation",
            cwd=tmp_path, check=True)
    s = _read(tmp_path)
    segs = s.get("activity_segments") or []
    assert len(segs) == 1, f"expected 1 closed segment, got {len(segs)}: {segs}"
    assert segs[0]["phase"] == "planning" and segs[0]["reason"] == "planning"
    rollup = s.get("activity_rollup") or {}
    assert rollup.get("closed_segment_count") == 1
    # 矛盾ラベル (新 phase + 旧 reason) のエントリが存在しない
    assert not any(g["phase"] == "executing" and g["reason"] == "planning" for g in segs)
