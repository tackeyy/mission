"""push-score サブコマンドのテスト (T1: RED → T2: GREEN)."""

from concurrent.futures import ThreadPoolExecutor
import json


LEGACY_EVIDENCE_ENV = {"MISSION_REQUIRE_SCORING_EVIDENCE": "0"}


def run_legacy_push_score(run_cli, *args, env_extra=None, **kwargs):
    """Use the temporary legacy escape hatch for tests that exercise --items behavior."""
    env = dict(LEGACY_EVIDENCE_ENV)
    if env_extra:
        env.update(env_extra)
    return run_cli("push-score", *args, env_extra=env, **kwargs)


def test_push_score_appends_to_empty_history(state_dir, run_cli, read_state):
    r = run_legacy_push_score(run_cli,
                              "--iteration", "1",
                              "--composite", "3.33",
                              "--min-item", "2.67",
                              "--items", '{"mission_achievement": 3.67, "accuracy": 2.67, "completeness": 3.33, "practicality": 3.0, "reviewer_consensus": 4.0}',
                              cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    s = read_state(state_dir)
    assert len(s["score_history"]) == 1
    entry = s["score_history"][0]
    assert entry["iteration"] == 1
    assert entry["composite"] == 3.33
    assert entry["min_item"] == 2.67
    assert entry["items"]["mission_achievement"] == 3.67


def test_push_score_appends_multiple_in_order(state_dir, run_cli, read_state):
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.5", "--min-item", "3.0",
            "--items", '{"a": 3.5}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "2", "--composite", "4.2", "--min-item", "4.0",
            "--items", '{"a": 4.2}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert [e["iteration"] for e in s["score_history"]] == [1, 2]
    assert s["score_history"][1]["composite"] == 4.2


def test_parallel_push_score_preserves_all_entries(state_dir, run_cli, read_state):
    """#98: 同一 session への並列 push-score で score_history が欠損しない。"""
    def push(iteration):
        return run_legacy_push_score(
            run_cli,
            "--iteration", str(iteration),
            "--composite", "4.0",
            "--min-item", "4.0",
            "--items", '{"mission_achievement":4.0}',
            cwd=state_dir.parent,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(push, range(1, 5)))

    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    entries = read_state(state_dir)["score_history"]
    assert sorted(e["iteration"] for e in entries) == [1, 2, 3, 4]


def test_push_score_updates_updated_at(state_dir, run_cli, read_state):
    before = read_state(state_dir)["updated_at"]
    import time; time.sleep(1.1)  # iso_now は秒精度なので 1 秒以上空ける
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"a": 4.0}', cwd=state_dir.parent, check=True)
    after = read_state(state_dir)["updated_at"]
    assert after != before


def test_push_score_with_notes(state_dir, run_cli, read_state):
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.5", "--min-item", "4.0",
            "--items", '{"a": 4.5}', "--notes", "Phase 2 完了直後の採点",
            cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    assert entry["notes"] == "Phase 2 完了直後の採点"


def test_push_score_records_timestamp(state_dir, run_cli, read_state):
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"a": 4.0}', cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    assert "timestamp" in entry
    assert entry["timestamp"].endswith("Z")


def test_push_score_requires_iteration(state_dir, run_cli):
    r = run_legacy_push_score(run_cli, "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode != 0


def test_push_score_rejects_iteration_zero(state_dir, run_cli):
    r = run_legacy_push_score(run_cli, "--iteration", "0", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode != 0
    assert "1 以上" in r.stderr


def test_push_score_rejects_invalid_items_json(state_dir, run_cli):
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", "not-json", cwd=state_dir.parent)
    assert r.returncode != 0


def test_push_score_does_not_touch_passes_flag(state_dir, run_cli, read_state):
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.8", "--min-item", "4.5",
            "--items", '{"a": 4.8}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["passes"] is False
    assert s["loop_active"] is True



# ===== --scoring-output 機能 (案 1: Scorer 出力 archive) =====


def test_push_score_with_scoring_output_archives_file(state_dir, run_cli, read_state, tmp_path):
    """--scoring-output で指定したファイルが .mission-state/archive/iter-N-scoring.md にコピーされる."""
    src = tmp_path / "scorer-out.md"
    src.write_text("# Scoring Iter 1\n\nReviewer A: 4.0/4.0/4.0/4.0\n", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"a": 4.0}', "--scoring-output", str(src),
            cwd=state_dir.parent, check=True)
    archive_path = state_dir / "archive" / "iter-1-abc12345-scoring.md"
    assert archive_path.exists(), f"expected archive at {archive_path}"
    assert "Reviewer A: 4.0/4.0/4.0/4.0" in archive_path.read_text(encoding="utf-8")


def test_push_score_scoring_output_missing_file_warns_not_fail(state_dir, run_cli, read_state, tmp_path):
    """--scoring-output で指定したファイルが存在しなくても exit 0 で続行 (後方互換)."""
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"a": 4.0}', "--scoring-output", str(tmp_path / "does-not-exist.md"),
                cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    # 警告メッセージが stderr に出ること
    assert "warning" in r.stderr.lower() or "not found" in r.stderr.lower()
    # state は正常に更新される
    s = read_state(state_dir)
    assert len(s["score_history"]) == 1


def test_push_score_without_scoring_output_rejects_by_default(state_dir, run_cli, read_state):
    """--scoring-output / --scoring-json なしでは score_history も archive も作らない."""
    r = run_cli("push-score", "--iteration", "2", "--composite", "4.5", "--min-item", "4.0",
                "--items", '{"a": 4.5}', "--notes", "scored inline",
                cwd=state_dir.parent,
                env_extra={"MISSION_REQUIRE_SCORING_EVIDENCE": None})
    assert r.returncode == 2
    s = read_state(state_dir)
    assert len(s["score_history"]) == 0
    archive_path = state_dir / "archive" / "iter-2-abc12345-scoring.md"
    assert not archive_path.exists()


def test_push_score_scoring_output_overwrites_existing(state_dir, run_cli, tmp_path):
    """同じ iteration に対して 2 回 push-score した場合、archive は上書きされる."""
    src1 = tmp_path / "out1.md"
    src1.write_text("first", encoding="utf-8")
    src2 = tmp_path / "out2.md"
    src2.write_text("second", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"a": 4.0}', "--scoring-output", str(src1),
            cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.2", "--min-item", "4.0",
            "--items", '{"a": 4.2}', "--scoring-output", str(src2),
            "--resubmit-reason", "re-score same iteration",
            cwd=state_dir.parent, check=True)
    archive_path = state_dir / "archive" / "iter-1-abc12345-scoring.md"
    content = archive_path.read_text(encoding="utf-8")
    assert "second" in content and "first" not in content  # 上書き(追記でない)をメタ前置下でも検証


# ===== H1: scoring archive 命名に mission_id を含める (2026-06-10 検査レポート) =====
# 旧形式 iter-{N}-scoring.md は同一プロジェクトの連続ランで上書き消失する実害があった


def test_push_score_scoring_output_filename_includes_mission_id(state_dir, run_cli, tmp_path):
    """archive 名は iter-{N}-{mission_id[:8]}-scoring.md (ラン間上書き防止)."""
    src = tmp_path / "out.md"
    src.write_text("scored", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0}', "--scoring-output", str(src),
            cwd=state_dir.parent, check=True)
    assert (state_dir / "archive" / "iter-1-abc12345-scoring.md").exists()


def test_push_score_scoring_output_no_collision_across_runs(state_dir, run_cli, tmp_path):
    """mission_id が異なる連続ランで scoring archive が相互上書きされない."""
    import json as _json
    src1 = tmp_path / "o1.md"; src1.write_text("run-A", encoding="utf-8")
    src2 = tmp_path / "o2.md"; src2.write_text("run-B", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0}', "--scoring-output", str(src1),
            cwd=state_dir.parent, check=True)
    s = _json.loads((state_dir / "sessions" / "test.json").read_text())
    s["mission_id"] = "deadbeefcafe0123"
    (state_dir / "sessions" / "test.json").write_text(_json.dumps(s))
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.2", "--min-item", "4.0",
            "--items", '{"mission_achievement": 4.2}', "--scoring-output", str(src2),
            "--resubmit-reason", "different mission_id, same iteration",
            cwd=state_dir.parent, check=True)
    a = state_dir / "archive"
    assert "run-A" in (a / "iter-1-abc12345-scoring.md").read_text(encoding="utf-8")
    assert "run-B" in (a / "iter-1-deadbeef-scoring.md").read_text(encoding="utf-8")


# ===== H2: スコア項目キーの正規化 (2026-06-10 検査レポート) =====
# 実ログで usefulness/practicality, reviewer_agreement/reviewer_consensus が混在し stats 集計が壊れる


def test_push_score_normalizes_alias_keys(state_dir, run_cli, read_state):
    """既知エイリアスは正規キーに正規化して保存される."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0, "accuracy": 4.0, "completeness": 4.0, "practicality": 3.5, "reviewer_agreement": 4.5}',
            cwd=state_dir.parent, check=True)
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 3.5
    assert items["reviewer_consensus"] == 4.5
    assert "practicality" not in items
    assert "reviewer_agreement" not in items


def test_push_score_normalizes_usefulness_alias(state_dir, run_cli, read_state):
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.8", "--min-item", "3.5",
            "--items", '{"usefulness": 3.8}', cwd=state_dir.parent, check=True)
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 3.8
    assert "usefulness" not in items


def test_push_score_warns_on_unknown_keys_but_accepts(state_dir, run_cli, read_state):
    """未知キーは WARN を stderr に出すが受理する (後方互換)."""
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"mystery_key": 4.0}', cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "warn" in r.stderr.lower()
    assert "mystery_key" in r.stderr
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["mystery_key"] == 4.0


def test_push_score_canonical_keys_no_warning(state_dir, run_cli):
    """正規 5 キーのみなら stderr にキー関連の警告を出さない."""
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"mission_achievement": 4.0, "accuracy": 4.0, "completeness": 4.0, "usability": 4.0, "reviewer_consensus": 4.0}',
                cwd=state_dir.parent)
    assert r.returncode == 0
    assert "キー" not in r.stderr and "key" not in r.stderr.lower()


def test_push_score_rejects_when_scalar_scores_inflate_above_items(state_dir, run_cli):
    """#122: 自己申告 composite/min_item が items 明細より 0.1 超で上振れしたら exit 2。

    以前は WARNING (#91) だったが、mark-passes gate はこの自己申告値を使うため、
    上振れ (inflation) は合格迂回になる。過小申告は保守側なので別テストで許容を確認する。
    """
    r = run_legacy_push_score(
        run_cli,
        "--iteration", "1",
        "--composite", "4.5",
        "--min-item", "4.0",
        "--items", '{"mission_achievement":3.0,"accuracy":3.0,"completeness":3.0,"usability":3.0,"reviewer_consensus":3.0}',
        cwd=state_dir.parent,
    )
    assert r.returncode == 2, r.stderr
    assert "上振れ" in r.stderr
    assert "composite=4.5" in r.stderr
    assert "min_item=4.0" in r.stderr


def test_push_score_allows_conservative_under_report(state_dir, run_cli):
    """#122: 過小申告 (items より低い composite/min_item) は保守側なので許容する。"""
    r = run_legacy_push_score(
        run_cli,
        "--iteration", "1",
        "--composite", "3.0",
        "--min-item", "3.0",
        "--items", '{"mission_achievement":4.0,"accuracy":4.0,"completeness":4.0,"usability":4.0,"reviewer_consensus":4.0}',
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, r.stderr


def test_push_score_rejects_duplicate_iteration_without_reason(state_dir, run_cli):
    """#122: 同一 iteration の再 push は --resubmit-reason なしでは exit 2。"""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "3.0",
            "--items", '{"a": 3.0}', cwd=state_dir.parent, check=True)
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "4.0",
            "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode == 2, r.stderr
    assert "既に採点済み" in r.stderr


def test_push_score_allows_duplicate_iteration_with_reason(state_dir, run_cli, read_state):
    """#122: 理由付きの再 push は許容し、resubmit_reason を entry に残す (旧 entry も保持)。"""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "3.0",
            "--items", '{"a": 3.0}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "4.0",
            "--items", '{"a": 4.0}', "--resubmit-reason", "inline 修正後の再採点",
            cwd=state_dir.parent, check=True)
    history = read_state(state_dir)["score_history"]
    iter1_entries = [h for h in history if h["iteration"] == 1]
    assert len(iter1_entries) == 2
    assert iter1_entries[-1]["resubmit_reason"] == "inline 修正後の再採点"


def test_push_score_accepts_matching_partial_items_without_warning(state_dir, run_cli):
    """#91: 差分レビューの 4 items は、その items だけを分母に照合する。"""
    r = run_legacy_push_score(
        run_cli,
        "--iteration", "1",
        "--composite", "4.0",
        "--min-item", "3.5",
        "--items", '{"mission_achievement":4.0,"accuracy":4.5,"completeness":4.0,"usability":3.5}',
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, r.stderr
    assert "items-derived" not in r.stderr


# ===== iter2: エイリアス+正規キー同時指定の衝突 (B-H1) =====


def test_push_score_canonical_wins_over_alias_collision(state_dir, run_cli, read_state):
    """正規キーとエイリアスが同一正規キーに衝突した場合、明示された正規キーの値が勝ち WARN が出る."""
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "3.0",
                "--items", '{"practicality": 5.0, "usability": 3.0}', cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "practicality" in r.stderr and ("衝突" in r.stderr or "collision" in r.stderr.lower())
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 3.0  # 正規キー明示値が勝つ (dict 順序に依存しない)


def test_push_score_canonical_wins_regardless_of_order(state_dir, run_cli, read_state):
    """逆順 (正規キーが先) でも結果が同じ = 順序非依存."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "3.0",
            "--items", '{"usability": 3.0, "practicality": 5.0}', cwd=state_dir.parent, check=True)
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 3.0


def test_push_score_two_aliases_same_canonical_first_wins_warns(state_dir, run_cli, read_state):
    """エイリアス2つが同一正規キーへ衝突 → 先勝ち + WARN."""
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"usefulness": 4.2, "practicality": 3.9}', cwd=state_dir.parent)
    assert r.returncode == 0
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 4.2
    assert "practicality" in r.stderr


# ===== #3: scoring ログへの起動元メタ自動付与 (2026-06-13 ログ調査) =====
# scoring md 単独で session_id/agent/mission_id を追えるようヘッダを前置する


def test_push_score_scoring_output_prepends_metadata(state_dir, run_cli, tmp_path):
    """archive された scoring md の冒頭に session_id/agent/mission_id メタが付与される (本文も保持)."""
    src = tmp_path / "out.md"
    src.write_text("# Scoring Iter 1\n本文ここ\n", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0}', "--scoring-output", str(src),
            cwd=state_dir.parent, check=True)
    content = (state_dir / "archive" / "iter-1-abc12345-scoring.md").read_text(encoding="utf-8")
    assert "session_id=test" in content
    assert "mission_id=abc12345" in content
    assert "agent=" in content
    assert "本文ここ" in content  # 元の本文が失われない


# ===== Q11: stagnation_count 自動計算 =====


def test_q11_first_push_stagnation_zero(state_dir, run_cli, read_state):
    """初回 push-score → stagnation_count=0 (前エントリなし)."""
    run_legacy_push_score(
        run_cli,
        "--iteration", "1",
        "--composite", "3.0",
        "--min-item", "2.5",
        "--items", '{"mission_achievement": 3.0}',
        cwd=state_dir.parent, check=True,
    )
    s = read_state(state_dir)
    assert s["stagnation_count"] == 0


def test_q11_improvement_gte_0_1_resets_to_zero(state_dir, run_cli, read_state):
    """composite 改善幅 >= 0.1 → stagnation_count=0 にリセット."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "2.5",
            "--items", '{"mission_achievement": 3.0}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "2", "--composite", "3.1", "--min-item", "3.0",
            "--items", '{"mission_achievement": 3.1}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["stagnation_count"] == 0


def test_q11_improvement_lt_0_1_increments(state_dir, run_cli, read_state):
    """composite 改善幅 < 0.1 (0.05) → stagnation_count += 1."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "2.5",
            "--items", '{"mission_achievement": 3.0}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "2", "--composite", "3.05", "--min-item", "3.0",
            "--items", '{"mission_achievement": 3.05}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["stagnation_count"] == 1


def test_q11_stagnation_cumulative(state_dir, run_cli, read_state):
    """改善幅 < 0.1 が続くと stagnation_count が累積する."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "2.5",
            "--items", '{"a": 3.0}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "2", "--composite", "3.05", "--min-item", "2.5",
            "--items", '{"a": 3.05}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "3", "--composite", "3.08", "--min-item", "2.5",
            "--items", '{"a": 3.08}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["stagnation_count"] == 2


def test_q11_reset_after_stagnation(state_dir, run_cli, read_state):
    """stagnation 後に大きく改善したら stagnation_count がリセットされる."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "2.5",
            "--items", '{"a": 3.0}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "2", "--composite", "3.05", "--min-item", "2.5",
            "--items", '{"a": 3.05}', cwd=state_dir.parent, check=True)
    # stagnation_count == 1 の状態
    run_legacy_push_score(run_cli, "--iteration", "3", "--composite", "3.5", "--min-item", "3.0",
            "--items", '{"a": 3.5}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["stagnation_count"] == 0


def test_q11_exact_0_1_improvement_resets(state_dir, run_cli, read_state):
    """改善幅がちょうど 0.1 → stagnation_count=0 (境界値)."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "2.5",
            "--items", '{"a": 3.0}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "2", "--composite", "3.1", "--min-item", "3.0",
            "--items", '{"a": 3.1}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    assert s["stagnation_count"] == 0


def test_q11_regression_does_not_increment_stagnation(state_dir, run_cli, read_state):
    """後退ケース: prev=4.0, cur=3.0 → delta=-1.0 < 0 なので stagnation_count は増えない (リセット)."""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"a": 4.0}', cwd=state_dir.parent, check=True)
    run_legacy_push_score(run_cli, "--iteration", "2", "--composite", "3.0", "--min-item", "2.5",
            "--items", '{"a": 3.0}', cwd=state_dir.parent, check=True)
    s = read_state(state_dir)
    # 後退は停滞ではなく「改善なし」扱い → stagnation_count = 0 にリセット
    assert s["stagnation_count"] == 0, (
        f"後退時は stagnation_count をインクリメントしてはならない: {s['stagnation_count']}"
    )
