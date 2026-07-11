"""mark-passes threshold gate のテスト (TDD Red → Green).

背景:
  cmd_mark_passes が score_history を検証せず無条件で passes=true を書き込む既知バグを修正。
  - composite < threshold で passes=true となる事例: ある実プロジェクトの iter2 (composite=3.9, min_item=3.0)
  - 修正後は exit 2 で reject。人手 override 用に --force --reason を提供。
"""
import json


def _push_score(run_cli, state_dir, iteration, composite, min_item, items=None):
    items = items or {"mission_achievement": composite, "accuracy": composite}
    return run_cli(
        "push-score",
        "--iteration", str(iteration),
        "--composite", str(composite),
        "--min-item", str(min_item),
        "--items", json.dumps(items),
        cwd=state_dir.parent,
        check=True,
    )


# ===== Threshold gate (reject paths) =====


def test_mark_passes_rejects_when_score_history_empty(state_dir, run_cli, read_state):
    """score_history が空のまま mark-passes を呼んだら exit 2."""
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    assert "採点未実施" in r.stderr or "push-score" in r.stderr, \
        f"stderr should mention push-score, got: {r.stderr}"
    # state は変更されていないこと
    s = read_state(state_dir)
    assert s["passes"] is False
    assert s["loop_active"] is True


def test_mark_passes_rejects_when_composite_below_threshold(state_dir, run_cli, read_state):
    """composite < threshold で exit 2."""
    _push_score(run_cli, state_dir, iteration=1, composite=3.9, min_item=3.8)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    assert "composite" in r.stderr.lower()
    assert "3.9" in r.stderr
    assert "4.0" in r.stderr
    s = read_state(state_dir)
    assert s["passes"] is False
    assert s["loop_active"] is True


def test_mark_passes_rejects_when_min_item_below_3_5(state_dir, run_cli, read_state):
    """min_item < 3.5 で exit 2 (composite が 4.0 以上でも)."""
    _push_score(run_cli, state_dir, iteration=1, composite=4.2, min_item=3.0)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    assert "min_item" in r.stderr.lower() or "最低" in r.stderr
    assert "3.0" in r.stderr
    assert "3.5" in r.stderr
    s = read_state(state_dir)
    assert s["passes"] is False
    assert s["loop_active"] is True


def test_mark_passes_uses_latest_score_history_entry(state_dir, run_cli, read_state):
    """score_history の最新 entry (= 直前の iter) を検証する. 過去 entry に合格があっても最新が未達なら reject."""
    _push_score(run_cli, state_dir, iteration=1, composite=4.5, min_item=4.0)
    _push_score(run_cli, state_dir, iteration=2, composite=3.5, min_item=3.0)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2, f"expected exit 2 (latest iter is failing), got {r.returncode}"


# ===== Threshold gate (accept path) =====


def test_mark_passes_accepts_when_both_pass(state_dir, run_cli, read_state):
    """composite >= threshold AND min_item >= 3.5 で passes=true 書き込み."""
    _push_score(run_cli, state_dir, iteration=1, composite=4.2, min_item=3.7)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, f"expected exit 0, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True
    assert s["loop_active"] is False


def test_mark_passes_rejects_new_standard_without_specialist_selection_checkpoint(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update({
        "created_at_session": "2026-07-03T00:00:00Z",
        "started_at": "2026-07-03T00:00:00Z",
        "task_profile": {},
        "specialists_decision": {},
    })
    state_path.write_text(json.dumps(state, indent=2))
    _push_score(run_cli, state_dir, iteration=1, composite=4.3, min_item=4.0)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "specialist selection checkpoint missing" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_accepts_new_standard_with_fallback_specialist_selection_checkpoint(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update({
        "created_at_session": "2026-07-03T00:00:00Z",
        "started_at": "2026-07-03T00:00:00Z",
        "task_profile": {"primary": "documentation"},
        "specialists_decision": {"policy": "fallback", "action": "continue-core"},
    })
    state_path.write_text(json.dumps(state, indent=2))
    _push_score(run_cli, state_dir, iteration=1, composite=4.3, min_item=4.0)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0, r.stderr
    assert read_state(state_dir)["passes"] is True


def test_mark_passes_rejects_required_specialist_without_applied_result(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update({
        "specialists_candidates": [{
            "role": "external-reviewer",
            "skill": "external-reviewer",
            "kind": "command",
            "task_profiles": ["documentation"],
            "status": "available",
            "required": True,
        }],
        "specialist_invocations": [{
            "role": "external-reviewer",
            "skill": "external-reviewer",
            "kind": "command",
            "mode": "command-provider",
            "status": "prepared",
            "reason": "provider prepared but produced no review findings",
        }],
    })
    state_path.write_text(json.dumps(state, indent=2))
    _push_score(run_cli, state_dir, iteration=1, composite=4.3, min_item=4.0)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 2
    assert "required specialist result evidence missing" in r.stderr
    assert read_state(state_dir)["passes"] is False


def test_mark_passes_accepts_required_specialist_with_completed_result(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state.update({
        "specialists_candidates": [{
            "role": "external-reviewer",
            "skill": "external-reviewer",
            "kind": "command",
            "task_profiles": ["documentation"],
            "status": "available",
            "required": True,
        }],
        "specialist_invocations": [{
            "role": "external-reviewer",
            "skill": "external-reviewer",
            "kind": "command",
            "mode": "command-provider",
            "status": "completed",
        }],
    })
    state_path.write_text(json.dumps(state, indent=2))
    _push_score(run_cli, state_dir, iteration=1, composite=4.3, min_item=4.0)

    r = run_cli("mark-passes", cwd=state_dir.parent)

    assert r.returncode == 0
    assert read_state(state_dir)["passes"] is True


# ===== --force / --reason =====


def test_mark_passes_force_with_reason_records_override(state_dir, run_cli, read_state):
    """--force + --reason + --approved-by-user で バリデーション skip & passes=true 書き込み & force_reason 保存."""
    _push_score(run_cli, state_dir, iteration=1, composite=3.5, min_item=3.0)
    r = run_cli(
        "mark-passes", "--force", "--reason", "manual approval after offline review", "--approved-by-user",
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, f"expected exit 0, got {r.returncode}\nstderr: {r.stderr}"
    # WARNING が stderr に出ること
    assert "warning" in r.stderr.lower() or "WARNING" in r.stderr
    s = read_state(state_dir)
    assert s["passes"] is True
    assert s["loop_active"] is False
    assert s.get("force_reason") == "manual approval after offline review"
    assert s.get("force_approved_by_user") is True


def test_mark_passes_force_with_reason_but_without_approved_by_user_rejects(state_dir, run_cli, read_state):
    """#185: --force --reason だけでは --approved-by-user 欠落で exit 2."""
    _push_score(run_cli, state_dir, iteration=1, composite=3.5, min_item=3.0)
    r = run_cli(
        "mark-passes", "--force", "--reason", "manual approval after offline review",
        cwd=state_dir.parent,
    )
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
    assert "--approved-by-user" in r.stderr
    s = read_state(state_dir)
    assert s["passes"] is False


def test_mark_passes_force_without_reason_rejects(state_dir, run_cli, read_state):
    """--force 単体 (reason なし) は exit 2."""
    _push_score(run_cli, state_dir, iteration=1, composite=3.5, min_item=3.0)
    r = run_cli("mark-passes", "--force", cwd=state_dir.parent)
    assert r.returncode != 0, f"expected non-zero, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is False


def test_mark_passes_force_works_even_when_score_history_empty(state_dir, run_cli, read_state):
    """--force --reason --approved-by-user は score_history が空でも override 可能 (緊急時の最後の手段)."""
    r = run_cli(
        "mark-passes", "--force", "--reason", "emergency manual close", "--approved-by-user",
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, f"expected exit 0, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True


# ===== 境界値テスト (Reviewer B M-3 反映) =====


def test_mark_passes_accepts_when_composite_equals_threshold(state_dir, run_cli, read_state):
    """composite == threshold (4.0) は合格扱い (< 演算子の境界条件確認)."""
    _push_score(run_cli, state_dir, iteration=1, composite=4.0, min_item=3.6)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, f"composite==threshold should pass, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True


def test_mark_passes_accepts_when_min_item_equals_3_5(state_dir, run_cli, read_state):
    """min_item == 3.5 は合格扱い (< 演算子の境界条件確認)."""
    _push_score(run_cli, state_dir, iteration=1, composite=4.2, min_item=3.5)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 0, f"min_item==3.5 should pass, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is True


# ===== 空文字列 reason の reject (Reviewer B M-2 反映) =====


def test_mark_passes_force_with_empty_string_reason_rejects(state_dir, run_cli, read_state):
    """--force --reason \"\" (空文字列) も exit 2 (reason は実質的な内容必須)."""
    _push_score(run_cli, state_dir, iteration=1, composite=3.5, min_item=3.0)
    r = run_cli("mark-passes", "--force", "--reason", "", cwd=state_dir.parent)
    assert r.returncode == 2, f"empty reason should be rejected with exit 2, got {r.returncode}\nstderr: {r.stderr}"
    s = read_state(state_dir)
    assert s["passes"] is False


# ===== exit code 一貫性 (Reviewer B L-2 反映) =====


def test_mark_passes_force_without_reason_exits_with_2(state_dir, run_cli):
    """--force --reason 欠落は exit 2 を厳密に検証 (他のケースと一貫)."""
    _push_score(run_cli, state_dir, iteration=1, composite=3.5, min_item=3.0)
    r = run_cli("mark-passes", "--force", cwd=state_dir.parent)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}\nstderr: {r.stderr}"
