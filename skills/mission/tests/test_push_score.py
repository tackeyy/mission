"""push-score サブコマンドのテスト (T1: RED → T2: GREEN)."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


def run_legacy_push_score(run_cli, *args, env_extra=None, **kwargs):
    """Translate old inline fixtures into content-bound canonical payloads.

    This is deliberately a test fixture only: production ``push-score`` keeps
    rejecting raw ``--items`` input.  Historic tests that used ``a`` merely as
    a placeholder exercise score-history behaviour. Expand that placeholder
    into all canonical dimensions, then let the shared reducer derive the
    stored claim.
    """
    values = list(args)
    if "--items" not in values or "--iteration" not in values:
        return run_cli("push-score", *args, env_extra=env_extra, **kwargs)
    try:
        items = json.loads(values[values.index("--items") + 1])
        iteration = values[values.index("--iteration") + 1]
        if not isinstance(items, dict):
            raise ValueError
    except (ValueError, IndexError, json.JSONDecodeError):
        return run_cli("push-score", *args, env_extra=env_extra, **kwargs)
    if set(items) == {"a"}:
        items = {"mission_achievement": items["a"]}
    normalized, _, _ = _state_module().normalize_score_items(items)
    items = {
        axis: normalized[axis]
        for axis in ("mission_achievement", "accuracy", "completeness", "usability")
        if axis in normalized
    }
    if len(items) == 1:
        items = {axis: next(iter(items.values())) for axis in (
            "mission_achievement", "accuracy", "completeness", "usability",
        )}
    cwd = kwargs.get("cwd")
    root = Path(cwd)
    archive = root / ".mission-state" / "archive"
    open_high = int(values[values.index("--open-high") + 1]) if "--open-high" in values else 0
    _, ref, claim = write_canonical_review_aggregate(
        root,
        [canonical_review(items, high_count=open_high)],
        iteration=int(iteration),
        name_prefix="legacy-fixture",
    )
    payload = {
        "items": claim["items"],
        "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": ref["path"],
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": ref,
            "revision_scope": ref["revision_scope"],
        },
    }
    if "--notes" in values:
        payload["notes"] = values[values.index("--notes") + 1]
    source = archive / f"legacy-score-{iteration}.json"
    source.write_text(json.dumps(payload))
    retained = [value for value in values if value not in {"--composite", "--min-item", "--items"}]
    # remove values paired with obsolete inline options
    cleaned = []
    skip = False
    for value in values:
        if skip:
            skip = False; continue
        if value in {"--composite", "--min-item", "--items", "--scoring-output"}:
            skip = True; continue
        cleaned.append(value)
    cleaned.extend(["--scoring-json", str(source)])
    return run_cli("push-score", *cleaned, env_extra=env_extra, **kwargs)


def _state_module():
    """Load the CLI module to unit-test its pure item normalization helper."""
    script = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("state_push_score_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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
    assert entry["composite"] == 3.17
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
    # Parallel writers must share the fencing token acquired before fan-out.
    run_cli("set", "iteration=0", cwd=state_dir.parent, check=True)

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


def test_push_score_archives_content_addressed_scoring_artifact(state_dir, run_cli, read_state, tmp_path):
    """Canonical scoring JSON is archived as an immutable bound artifact."""
    src = tmp_path / "scorer-out.md"
    src.write_text("# Scoring Iter 1\n\nReviewer A: 4.0/4.0/4.0/4.0\n", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"a": 4.0}', "--scoring-output", str(src),
            cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    archive_path = state_dir.parent / entry["score_provenance"]["scoring_evidence_ref"]["path"]
    assert archive_path.exists(), f"expected archive at {archive_path}"
    artifact = json.loads(archive_path.read_text(encoding="utf-8"))
    assert artifact["schema"] == "mission-scoring-artifact/1"
    assert artifact["binding"]["items"] == entry["items"]


def test_push_score_raw_scoring_output_without_provenance_rejects(state_dir, run_cli, read_state, tmp_path):
    """A legacy output path cannot substitute for structured provenance."""
    r = run_cli("push-score", "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"a": 4.0}', "--scoring-output", str(tmp_path / "does-not-exist.md"),
                cwd=state_dir.parent)
    assert r.returncode == 2, r.stderr
    assert "provenance" in r.stderr
    s = read_state(state_dir)
    assert len(s["score_history"]) == 0


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


def test_push_score_resubmit_keeps_distinct_content_addressed_archives(state_dir, run_cli, read_state, tmp_path):
    """A resubmission keeps the original immutable artifact rather than overwriting it."""
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
    history = read_state(state_dir)["score_history"]
    first_path = state_dir.parent / history[0]["score_provenance"]["scoring_evidence_ref"]["path"]
    second_path = state_dir.parent / history[1]["score_provenance"]["scoring_evidence_ref"]["path"]
    assert first_path != second_path
    assert first_path.exists() and second_path.exists()


# ===== H1: scoring archive 命名に mission_id を含める (2026-06-10 検査レポート) =====
# 旧形式 iter-{N}-scoring.md は同一プロジェクトの連続ランで上書き消失する実害があった


def test_push_score_artifact_filename_includes_mission_id(state_dir, run_cli, read_state, tmp_path):
    """The immutable archive name includes the mission identifier."""
    src = tmp_path / "out.md"
    src.write_text("scored", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0}', "--scoring-output", str(src),
            cwd=state_dir.parent, check=True)
    ref = read_state(state_dir)["score_history"][0]["score_provenance"]["scoring_evidence_ref"]
    assert "/iter-1-abc12345-scoring-" in ref["path"]


def test_push_score_artifact_no_collision_across_runs(state_dir, run_cli, read_state, tmp_path):
    """Different mission IDs produce distinct immutable archive paths."""
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
    history = read_state(state_dir)["score_history"]
    refs = [entry["score_provenance"]["scoring_evidence_ref"]["path"] for entry in history]
    assert any("iter-1-abc12345-scoring-" in path for path in refs)
    assert any("iter-1-deadbeef-scoring-" in path for path in refs)


# ===== H2: スコア項目キーの正規化 (2026-06-10 検査レポート) =====
# 実ログで usefulness/practicality, reviewer_agreement/reviewer_consensus が混在し stats 集計が壊れる


def test_push_score_normalizes_alias_keys(state_dir, run_cli, read_state):
    """既知エイリアスは正規キーに正規化して保存される."""
    normalized, _, _ = _state_module().normalize_score_items({
        "mission_achievement": 4.0, "accuracy": 4.0, "completeness": 4.0,
        "practicality": 3.5, "reviewer_agreement": 4.5,
    })
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0, "accuracy": 4.0, "completeness": 4.0, "practicality": 3.5, "reviewer_agreement": 4.5}',
            cwd=state_dir.parent, check=True)
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 3.5
    assert normalized["reviewer_consensus"] == 4.5
    assert "practicality" not in items


def test_push_score_normalizes_usefulness_alias(state_dir, run_cli, read_state):
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.8", "--min-item", "3.5",
            "--items", '{"usefulness": 3.8}', cwd=state_dir.parent, check=True)
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 3.8
    assert "usefulness" not in items


def test_normalize_score_items_reports_unknown_key_without_discarding_it():
    """Legacy unknown-key behaviour remains covered as a pure helper contract."""
    normalized, unknown, collisions = _state_module().normalize_score_items({"mystery_key": 4.0})
    assert normalized == {"mystery_key": 4.0}
    assert unknown == ["mystery_key"]
    assert collisions == []


def test_push_score_canonical_keys_no_warning(state_dir, run_cli):
    """正規 5 キーのみなら stderr にキー関連の警告を出さない."""
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"mission_achievement": 4.0, "accuracy": 4.0, "completeness": 4.0, "usability": 4.0, "reviewer_consensus": 4.0}',
                cwd=state_dir.parent)
    assert r.returncode == 0
    diagnostics = "\n".join(
        line for line in r.stderr.splitlines()
        if not line.startswith("MISSION_LEASE_CARRIER=")
    )
    assert "キー" not in diagnostics and "key" not in diagnostics.lower()


def test_push_score_rejects_scalar_args_alongside_scoring_json(state_dir, run_cli, tmp_path):
    """A caller cannot inflate a canonical score with separate scalar claims."""
    _, ref, claim = write_canonical_review_aggregate(
        state_dir.parent,
        [canonical_review({"mission_achievement": 3.0})],
        iteration=1,
        name_prefix="inflation-evidence",
    )
    source = tmp_path / "score.json"
    source.write_text(json.dumps({
        "items": claim["items"],
        "open_high": claim["open_high"],
        "review_agreement": claim["review_agreement"],
        "agreement_detail": claim["agreement_detail"],
        "findings_evidence_path": ref["path"],
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": ref,
            "revision_scope": ref["revision_scope"],
        },
    }))
    r = run_cli("push-score", "--iteration", "1", "--scoring-json", str(source), "--composite", "4.5",
                cwd=state_dir.parent)
    assert r.returncode == 2, r.stderr
    assert "併用できません" in r.stderr


def test_push_score_derives_gate_scalars_from_canonical_items(state_dir, run_cli, read_state):
    """Canonical evidence derives both gate scalars from its bound item values."""
    r = run_legacy_push_score(
        run_cli,
        "--iteration", "1",
        "--composite", "3.0",
        "--min-item", "3.0",
        "--items", '{"mission_achievement":4.0,"accuracy":4.0,"completeness":4.0,"usability":4.0,"reviewer_consensus":4.0}',
        cwd=state_dir.parent,
    )
    assert r.returncode == 0, r.stderr
    entry = read_state(state_dir)["score_history"][-1]
    assert entry["composite"] == 4.0 and entry["min_item"] == 4.0


def test_push_score_rejects_duplicate_iteration_without_reason(state_dir, run_cli, read_state):
    """#122: 同一 iteration の再 push は --resubmit-reason なしでは exit 2。旧 entry は書き換わらない。"""
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "3.0",
            "--items", '{"a": 3.0}', cwd=state_dir.parent, check=True)
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "4.0",
            "--items", '{"a": 4.0}', cwd=state_dir.parent)
    assert r.returncode == 2, r.stderr
    assert "既に採点済み" in r.stderr
    history = read_state(state_dir)["score_history"]
    assert len(history) == 1 and history[0]["composite"] == 3.0


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
    _, _, collisions = _state_module().normalize_score_items({"practicality": 5.0, "usability": 3.0})
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "3.0", "--min-item", "3.0",
                "--items", '{"practicality": 5.0, "usability": 3.0}', cwd=state_dir.parent)
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert collisions == [("practicality", "usability")]
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
    _, _, collisions = _state_module().normalize_score_items({"usefulness": 4.2, "practicality": 3.9})
    r = run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
                "--items", '{"usefulness": 4.2, "practicality": 3.9}', cwd=state_dir.parent)
    assert r.returncode == 0
    items = read_state(state_dir)["score_history"][0]["items"]
    assert items["usability"] == 4.2
    assert collisions == [("practicality", "usability")]


# ===== #3: scoring ログへの起動元メタ自動付与 (2026-06-13 ログ調査) =====
# scoring md 単独で session_id/agent/mission_id を追えるようヘッダを前置する


def test_push_score_content_addressed_artifact_records_metadata(state_dir, run_cli, read_state, tmp_path):
    """The immutable scoring artifact carries session and mission metadata."""
    src = tmp_path / "out.md"
    src.write_text("# Scoring Iter 1\n本文ここ\n", encoding="utf-8")
    run_legacy_push_score(run_cli, "--iteration", "1", "--composite", "4.0", "--min-item", "3.5",
            "--items", '{"mission_achievement": 4.0}', "--scoring-output", str(src),
            cwd=state_dir.parent, check=True)
    entry = read_state(state_dir)["score_history"][0]
    path = state_dir.parent / entry["score_provenance"]["scoring_evidence_ref"]["path"]
    meta = json.loads(path.read_text(encoding="utf-8"))["_meta"]
    assert meta["session_id"] == "test"
    assert meta["mission_id"] == "abc12345"
    assert "agent" in meta


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
