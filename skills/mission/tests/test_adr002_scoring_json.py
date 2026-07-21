"""ADR-002 Stage 1: --scoring-json 構造化採点入力のテスト (G-1) + スケール異常 reject + evidence deprecation (G-2 段階形).

背景 (docs/log-crosscheck-review-2026-07-02.md):
- orchestrator が scorer 出力テキストから数値を転記する経路が、スコア捏造・転記ミス・
  0-1 正規化スケール混入 (xai-cli cx-019efece: composite 0.96 = 4.8/5) を素通りさせていた。
- --scoring-json は items を JSON ファイルから読み、composite/min_item を CLI 側で再計算する。
"""

import json

CANONICAL_ITEMS = {
    "mission_achievement": 4.0,
    "accuracy": 4.5,
    "completeness": 4.0,
    "usability": 3.5,
    "reviewer_consensus": 4.0,
}


def _write_scoring_json(tmp_path, payload, name="scorer-out.json"):
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


# ===== 基本動作: items から composite/min_item を CLI 側で再計算 =====


def test_scoring_json_computes_composite_and_min_item(state_dir, run_cli, read_state, tmp_path):
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    entry = read_state(state_dir)["score_history"][0]
    assert entry["composite"] == 4.0  # mean(4.0, 4.5, 4.0, 3.5, 4.0)
    assert entry["min_item"] == 3.5
    assert entry["items"]["accuracy"] == 4.5
    assert entry["score_source"] == "scoring-json"


def test_scoring_json_duplicate_iteration_requires_reason(state_dir, run_cli, read_state, tmp_path):
    """#122: 重複 iteration ガードは --scoring-json 経路にも適用される (両経路共通の StateLock 内チェック)。"""
    src1 = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS}, name="a.json")
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(src1),
            cwd=state_dir.parent, check=True)
    src2 = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS}, name="b.json")
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src2), cwd=state_dir.parent)
    assert r.returncode == 2, r.stderr
    assert "既に採点済み" in r.stderr
    # 理由付きなら通り、旧 entry も保持される。
    r2 = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src2),
                 "--resubmit-reason", "re-score after fix", cwd=state_dir.parent)
    assert r2.returncode == 0, r2.stderr
    history = read_state(state_dir)["score_history"]
    assert len([h for h in history if h["iteration"] == 1]) == 2


def test_scoring_json_notes_from_file(state_dir, run_cli, read_state, tmp_path):
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS, "notes": "iter1: verified"})
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
            cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    assert entry["notes"] == "iter1: verified"


def test_scoring_json_stagnation_still_updates(state_dir, run_cli, read_state, tmp_path):
    """Q11 stagnation は scoring-json 経路でも機能する."""
    low = {k: 3.0 for k in CANONICAL_ITEMS}
    low2 = dict(low, mission_achievement=3.1)  # mean 3.02 → 改善幅 0.02 < 0.1
    s1 = _write_scoring_json(tmp_path, {"items": low}, "s1.json")
    s2 = _write_scoring_json(tmp_path, {"items": low2}, "s2.json")
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(s1),
            cwd=state_dir.parent, check=True)
    run_cli("push-score", "--iteration", "2", "--scoring-json", str(s2),
            cwd=state_dir.parent, check=True)
    assert read_state(state_dir)["stagnation_count"] == 1


# ===== 排他・入力検証 =====


def test_scoring_json_conflicts_with_items(state_dir, run_cli, tmp_path):
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode == 2, f"stderr: {r.stderr}"


def test_scoring_json_conflicts_with_composite(state_dir, run_cli, tmp_path):
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                "--composite", "4.0", cwd=state_dir.parent)
    assert r.returncode == 2, f"stderr: {r.stderr}"


def test_scoring_json_missing_file(state_dir, run_cli, tmp_path):
    r = run_cli("push-score", "--iteration", "1",
                "--scoring-json", str(tmp_path / "does-not-exist.json"),
                cwd=state_dir.parent)
    assert r.returncode == 2


def test_scoring_json_invalid_json(state_dir, run_cli, tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ broken ][", encoding="utf-8")
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(p),
                cwd=state_dir.parent)
    assert r.returncode == 2


def test_scoring_json_missing_items_key(state_dir, run_cli, tmp_path):
    src = _write_scoring_json(tmp_path, {"notes": "no items"})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 2


def test_scoring_json_rejects_unknown_keys(state_dir, run_cli, tmp_path):
    """strict path: 正規化後に未知キーが残ったら reject (従来 --items の WARN 素通りと異なる)."""
    items = dict(CANONICAL_ITEMS, implementation_quality=4.0)
    src = _write_scoring_json(tmp_path, {"items": items})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 2
    assert "implementation_quality" in r.stderr


def test_scoring_json_normalizes_alias_keys(state_dir, run_cli, read_state, tmp_path):
    items = {"mission_achievement": 4.0, "accuracy": 4.0, "completeness": 4.0,
             "practicality": 3.5, "reviewer_agreement": 4.5}
    src = _write_scoring_json(tmp_path, {"items": items})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    saved = read_state(state_dir)["score_history"][0]["items"]
    assert saved["usability"] == 3.5
    assert saved["reviewer_consensus"] == 4.5


def test_scoring_json_rejects_out_of_range_item(state_dir, run_cli, tmp_path):
    items = dict(CANONICAL_ITEMS, accuracy=5.5)
    src = _write_scoring_json(tmp_path, {"items": items})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 2


def test_scoring_json_respects_consensus_policy(state_dir, run_cli, tmp_path):
    """Issue #10: Simple+Reviewer1名では reviewer_consensus を含む scoring-json も reject."""
    sf = state_dir / "sessions" / "test.json"
    s = json.loads(sf.read_text())
    s["complexity"] = "Simple"
    s["reviewer_count"] = 1
    sf.write_text(json.dumps(s))
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 2
    assert "reviewer_consensus" in r.stderr


# ===== evidence の archive =====


def test_scoring_json_archives_evidence_with_meta(state_dir, run_cli, read_state, tmp_path):
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS, "notes": "n1"})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    dst = state_dir / "archive" / "iter-1-abc12345-scoring.json"
    assert dst.exists()
    payload = json.loads(dst.read_text(encoding="utf-8"))
    assert payload["_meta"]["session_id"] == "test"
    assert payload["_meta"]["mission_id"] == "abc12345"
    assert payload["_meta"]["computed_composite"] == 4.0
    assert payload["items"]["accuracy"] == 4.5
    entry = read_state(state_dir)["score_history"][0]
    assert entry["scoring_evidence_path"] == str(dst)


def test_scoring_json_open_high_flows_to_gate(state_dir, run_cli, tmp_path):
    """scoring-json の open_high が mark-passes の High gate に届く."""
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS, "open_high": 2})
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
            cwd=state_dir.parent, check=True)
    r = run_cli("mark-passes", cwd=state_dir.parent)
    assert r.returncode == 2
    assert "High" in r.stderr


def test_scoring_json_open_high_wins_over_cli_flag(state_dir, run_cli, read_state, tmp_path):
    """JSON の open_high は CLI --open-high より優先 (scorer の構造化出力が authoritative)."""
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS, "open_high": 5})
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
            "--open-high", "3", cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    assert entry["open_high"] == 5


def test_scoring_json_without_open_high_falls_back_to_cli(state_dir, run_cli, read_state, tmp_path):
    """JSON に open_high キーがなければ CLI --open-high をフォールバックとして使う."""
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS})
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
            "--open-high", "2", cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    assert entry["open_high"] == 2


def test_scoring_json_evidence_written_before_state_records_path(state_dir, run_cli, read_state, tmp_path):
    """scoring_evidence_path が state に載る時点で archive ファイルが実在する
    (crash 時の dangling reference 防止: archive 書き込みは state 書き込みと同一 lock 内で先行)."""
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS})
    run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
            cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    from pathlib import Path
    assert Path(entry["scoring_evidence_path"]).exists()


# ===== 0-1 正規化スケール reject (xai-cli cx-019efece 回帰) =====


def test_scoring_json_rejects_normalized_scale(state_dir, run_cli, tmp_path):
    items = {k: v / 5.0 for k, v in CANONICAL_ITEMS.items()}  # 0.7-0.9 帯
    src = _write_scoring_json(tmp_path, {"items": items})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent)
    assert r.returncode == 2
    assert "0-1" in r.stderr or "正規化" in r.stderr


def test_items_path_rejects_normalized_scale(state_dir, run_cli):
    """従来 --items 経路でも全 items <= 1.0 は reject (実ログ: composite 0.96 が素通りした)."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "0.96", "--min-item", "0.93",
                "--items", '{"mission_achievement": 0.96, "accuracy": 0.95, "completeness": 0.94}',
                cwd=state_dir.parent,
                env_extra={"MISSION_REQUIRE_SCORING_EVIDENCE": "0"})
    assert r.returncode == 2
    assert "0-1" in r.stderr or "正規化" in r.stderr


def test_single_low_item_among_normal_passes(state_dir, run_cli):
    """1 項目だけ低い正当な採点 (max > 1.0) は通過する."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "2.25", "--min-item", "0.5",
                "--items", '{"mission_achievement": 0.5, "accuracy": 4.0}',
                cwd=state_dir.parent,
                env_extra={"MISSION_REQUIRE_SCORING_EVIDENCE": "0"})
    assert r.returncode == 0, f"stderr: {r.stderr}"


# ===== evidence なし push-score の hard reject (G-2 default flip) =====


def test_no_evidence_rejects_by_default(state_dir, run_cli, read_state):
    """scoring evidence なしの push-score は default で reject し state を汚さない."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"mission_achievement": 4.0}', cwd=state_dir.parent,
                env_extra={"MISSION_REQUIRE_SCORING_EVIDENCE": None})
    assert r.returncode == 2
    assert "scoring evidence" in r.stderr
    assert len(read_state(state_dir)["score_history"]) == 0


def test_allow_evidence_less_env_retains_deprecated_escape_hatch(state_dir, run_cli, read_state):
    # #226 (A-4): escape hatch は削除まで機能を維持するが、文言は DEPRECATED を明示する。
    r = run_cli("push-score", "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"mission_achievement": 4.0}', cwd=state_dir.parent,
                env_extra={"MISSION_REQUIRE_SCORING_EVIDENCE": "0"})
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "DEPRECATED ESCAPE HATCH" in r.stderr
    assert len(read_state(state_dir)["score_history"]) == 1


def test_require_evidence_env_accepts_scoring_json(state_dir, run_cli, tmp_path):
    src = _write_scoring_json(tmp_path, {"items": CANONICAL_ITEMS})
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(src),
                cwd=state_dir.parent,
                env_extra={"MISSION_REQUIRE_SCORING_EVIDENCE": "1"})
    assert r.returncode == 0, f"stderr: {r.stderr}"


# ===== 従来経路の必須引数 (argparse required を自前検証に移した回帰) =====


def test_legacy_path_still_requires_items_composite_min_item(state_dir, run_cli):
    r = run_cli("push-score", "--iteration", "1", cwd=state_dir.parent)
    assert r.returncode != 0
