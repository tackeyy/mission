"""ADR-002 Stage 3: `mission-state.py next` — state から次の一手を決定論的に返す (G-3).

背景 (docs/log-crosscheck-review-2026-07-02.md L-9):
実行の 84% は Codex で走るが、ループ強制の Stop hook は Claude Code 専用。
`next` は「state を読めば次にやるべき 1 手が返る」ハーネス非依存の進行ガイドを提供し、
compaction 後の復元も散文 Compact Instructions への依存から state 駆動へ移す。
"""

import json


def _set_state(state_dir, **kv):
    sf = state_dir / "sessions" / "test.json"
    s = json.loads(sf.read_text())
    s.update(kv)
    sf.write_text(json.dumps(s))


def _next(run_cli, state_dir):
    r = run_cli("next", cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    return json.loads(r.stdout)


def test_next_without_state_suggests_init(tmp_path, run_cli):
    r = run_cli("next", cwd=tmp_path)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    out = json.loads(r.stdout)
    assert out["next_action"] == "init"


def test_next_when_halted_reports_blocker(state_dir, run_cli):
    _set_state(state_dir, loop_active=False, halt_reason="waiting for API key")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "report-blocker"
    assert "waiting for API key" in out["summary"]


def test_next_manual_halt_suggests_audited_reactivate(state_dir, run_cli):
    _set_state(
        state_dir,
        loop_active=False,
        phase="halted",
        halt_reason="waiting for explicit approval",
        halt_category="awaiting-approval",
    )

    out = _next(run_cli, state_dir)

    assert out["next_action"] == "report-blocker"
    assert "reactivate" in out["command_hint"]
    assert "--approved-by-user" in out["command_hint"]
    assert "--expected-category awaiting-approval" in out["command_hint"]
    assert "refresh-pid" not in out["command_hint"]


def test_next_stale_halt_suggests_resume_not_manual_reactivate(state_dir, run_cli):
    _set_state(
        state_dir,
        loop_active=False,
        phase="halted",
        halt_reason="orphan: previous agent exited",
        halt_category="stale",
    )

    out = _next(run_cli, state_dir)

    assert out["next_action"] == "report-blocker"
    assert out["command_hint"] == "mission-state.py resume"
    assert "reactivate" not in out["command_hint"]


def test_next_when_passed_reports_complete(state_dir, run_cli):
    _set_state(state_dir, passes=True, loop_active=False, phase="done")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "report-complete"


def test_next_when_inactive_suggests_resume(state_dir, run_cli):
    """loop_active=false / passes=false / halt なし → refresh-pid での再開を提案."""
    _set_state(state_dir, loop_active=False)
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "resume"
    assert "refresh-pid" in out["command_hint"]


def test_next_when_awaiting_user(state_dir, run_cli):
    _set_state(state_dir, awaiting_user=True)
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "await-user"


def test_next_planning_phase_suggests_planner(state_dir, run_cli):
    _set_state(state_dir, phase="planning")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-planner"


def test_next_executing_phase_suggests_executor(state_dir, run_cli):
    _set_state(state_dir, phase="executing")
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-executor"


def test_next_reviewing_phase_suggests_parallel_reviewers(state_dir, run_cli):
    _set_state(state_dir, phase="reviewing", reviewer_count=3)
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-reviewers"
    assert out["details"]["reviewer_count"] == 3
    # ログ実測で並列起動遵守率 0/7 だった規律を machine-readable に埋め込む
    assert "並列" in out["summary"] or "parallel" in out["summary"].lower()


def test_next_scoring_phase_without_score_suggests_aggregate_reviews(state_dir, run_cli):
    # fixture 既定: phase=scoring, iteration=1, score_history=[]
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "aggregate-reviews"
    assert "aggregate-reviews" in out["command_hint"]
    assert "--scoring-json" in out["command_hint"]


def test_next_scoring_phase_with_current_score_suggests_mark_passes(state_dir, run_cli):
    _set_state(state_dir, score_history=[
        {"iteration": 1, "composite": 4.2, "min_item": 4.0,
         "items": {"mission_achievement": 4.2}, "timestamp": "2026-07-02T00:00:00Z", "open_high": 0},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "mark-passes"


def test_next_scoring_with_stale_score_from_previous_iteration_suggests_aggregate_reviews(state_dir, run_cli):
    """score_history が前 iteration のものしかない → 現 iteration の採点が先."""
    _set_state(state_dir, iteration=2, score_history=[
        {"iteration": 1, "composite": 3.2, "min_item": 3.0,
         "items": {"mission_achievement": 3.2}, "timestamp": "2026-07-02T00:00:00Z", "open_high": 0},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "aggregate-reviews"


def test_next_stagnation_warns_consider_halt(state_dir, run_cli):
    _set_state(state_dir, stagnation_count=3, score_history=[
        {"iteration": 1, "composite": 3.2, "min_item": 3.0,
         "items": {"mission_achievement": 3.2}, "timestamp": "2026-07-02T00:00:00Z", "open_high": 0},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "consider-halt"
    assert "stagnation" in out["summary"].lower() or "停滞" in out["summary"]


def test_next_includes_state_snapshot(state_dir, run_cli):
    out = _next(run_cli, state_dir)
    assert out["phase"] == "scoring"
    assert out["iteration"] == 1
    assert out["session_id"] == "test"


def test_next_stagnation_does_not_interrupt_reviewing(state_dir, run_cli):
    """複合状態 (手動 set 等で stagnation>=3 かつ phase=reviewing) ではレビュー完了を優先する.

    通常経路では push-score が phase=scoring に遷移させるため共起しないが、
    `set stagnation_count=N` は許可された操作なので防御的にレビュー中断を避ける。"""
    _set_state(state_dir, phase="reviewing", stagnation_count=3)
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "run-reviewers"


def test_next_snapshot_coerces_null_stagnation(state_dir, run_cli):
    """stagnation_count=null が保存されていても snapshot は内部判定と同じく 0 を返す."""
    _set_state(state_dir, stagnation_count=None)
    out = _next(run_cli, state_dir)
    assert out["stagnation_count"] == 0


# ===== #187: aggregate-reviews 失敗時の fallback 導線 (force に逃げない) =====


def test_next_scoring_json_entry_without_findings_evidence_suggests_retry_not_force(state_dir, run_cli):
    """score_source=scoring-json だが findings_evidence_path が無い (手作り scoring-json 等で
    aggregate-reviews を経由しなかった) 場合、mark-passes ではなく aggregate-reviews への
    リトライを提案する。force には触れても「使うな」という禁止文脈でのみで、command_hint
    (実行すべきコマンド列) 自体には --force を一切含めない (提案しない)。"""
    _set_state(state_dir, score_history=[
        {"iteration": 1, "composite": 4.5, "min_item": 4.5,
         "items": {"mission_achievement": 4.5}, "timestamp": "2026-07-11T00:00:00Z",
         "open_high": 0, "score_source": "scoring-json"},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "aggregate-reviews"
    assert out["details"]["missing_findings_evidence"] is True
    # command_hint は実行すべきコマンド列そのものなので --force を一切含めない (提案しない)
    assert "--force" not in out["command_hint"]
    # summary は「force を使うな」という禁止の文脈でのみ言及してよい
    assert "使わず" in out["summary"] or "禁止" in out["summary"]


def test_next_scoring_json_entry_with_findings_evidence_suggests_mark_passes(state_dir, run_cli):
    """findings_evidence_path が揃っていれば通常どおり mark-passes を提案する (回帰確認)."""
    _set_state(state_dir, score_history=[
        {"iteration": 1, "composite": 4.5, "min_item": 4.5,
         "items": {"mission_achievement": 4.5}, "timestamp": "2026-07-11T00:00:00Z",
         "open_high": 0, "score_source": "scoring-json",
         "findings_evidence_path": ".mission-state/archive/iter-1-abc12345-reviews.json"},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "mark-passes"


def test_next_legacy_items_entry_without_score_source_still_suggests_mark_passes(state_dir, run_cli):
    """score_source フィールド自体がない legacy entry は findings evidence チェック対象外
    (mark-passes 側も legacy は WARN のみで hard block しない) — 後方互換確認."""
    _set_state(state_dir, score_history=[
        {"iteration": 1, "composite": 4.2, "min_item": 4.0,
         "items": {"mission_achievement": 4.2}, "timestamp": "2026-07-02T00:00:00Z", "open_high": 0},
    ])
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "mark-passes"


def test_next_aggregate_reviews_default_hint_does_not_mention_force(state_dir, run_cli):
    """score_history が空の通常経路のヒントにも --force への言及がないことを確認."""
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "aggregate-reviews"
    assert "force" not in out["command_hint"].lower()


def test_next_missing_evidence_with_unclosed_specialist_still_reports_it(state_dir, run_cli):
    """#187 review advisory: missing_findings_evidence=True の間も #189 の
    unclosed_specialists 情報を details から欠落させない (aggregate-reviews リトライ待ちの
    可視性を保つ)."""
    _set_state(
        state_dir,
        score_history=[
            {"iteration": 1, "composite": 4.5, "min_item": 4.5,
             "items": {"mission_achievement": 4.5}, "timestamp": "2026-07-11T00:00:00Z",
             "open_high": 0, "score_source": "scoring-json"},
        ],
        specialists_selected=[{"skill": "dev-security-reviewer", "role": "security-reviewer"}],
    )
    out = _next(run_cli, state_dir)
    assert out["next_action"] == "aggregate-reviews"
    assert out["details"]["missing_findings_evidence"] is True
    assert out["details"]["unclosed_specialists"] == ["dev-security-reviewer"]
