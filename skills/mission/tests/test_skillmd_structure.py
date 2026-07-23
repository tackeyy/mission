"""SKILL.md スリム化の構造テスト (S1: RED → S4: GREEN)."""
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"
REFS_DIR = SKILL_DIR / "refs"
REPO_ROOT = SKILL_DIR.parent.parent
REVIEWER_MD = REPO_ROOT / "skills" / "mission-reviewer" / "SKILL.md"
SCORER_MD = REPO_ROOT / "skills" / "mission-scorer" / "SKILL.md"
EXECUTOR_MD = REPO_ROOT / "skills" / "mission-executor" / "SKILL.md"


def _read(p):
    return p.read_text() if p.exists() else ""


def test_skillmd_size_under_300_lines():
    """#125: SKILL.md 本体は 300 行未満。進行 oracle は next/resume と refs に寄せる。"""
    n = len(_read(SKILL_MD).splitlines())
    assert n < 300, f"SKILL.md is {n} lines (target: < 300)"


def test_skillmd_contains_critical_keywords():
    """本体から失えない必須キーワードが残っていることを確認."""
    txt = _read(SKILL_MD)
    must_have = [
        "loop_active",       # 終了判定の核
        "passes",            # PASS 判定
        "score_history",     # 採点記録
        "threshold",         # 合格閾値
        "push-score",        # バグ修正の必須手順
        "resume",            # 復帰 oracle
        "next",              # 進行 oracle
        "Trigger 1",         # 不可逆操作確認
        "Trigger 2",         # 中断条件
        "観点D",             # 観点D 運用
        "Stop hook",         # ループ強制
        "差分レビュー",       # #240: iter2以降は state-driven で独立2名 (レビューコスト対策)
        "Planner spawn 判定",  # #124
        "critic_has_new_scope",  # #258: #240/#241 の scope 判定を state へ配線
        "bounded context",       # #258: #241 context_mode 消費と fail-safe fallback
        "--min-reviewers",       # #258: #240 合意偽装防止フラグの利用強制
        "adaptive routing",      # #276: Simple→goal ルーティングの配線
        "--force-mission",       # #276: routing の明示エスケープ
    ]
    missing = [kw for kw in must_have if kw not in txt]
    assert not missing, f"missing critical keywords: {missing}"


def test_refs_state_management_exists():
    """state-management.md が refs/ に存在する."""
    f = REFS_DIR / "state-management.md"
    assert f.exists(), f"{f} not found"
    n = len(_read(f).splitlines())
    assert n >= 80, f"refs/state-management.md too thin: {n} lines"


def test_refs_gotchas_exists():
    """gotchas.md が refs/ に存在する (本体から退避した実運用の落とし穴)."""
    f = REFS_DIR / "gotchas.md"
    assert f.exists(), f"{f} not found"
    txt = _read(f)
    # §1-§9 の見出しがすべて存在し、番号の重複がないこと (2026-06-11 §7 衝突の再発防止)
    for n in range(1, 12):  # §1-11 (§10/§11 も ### 見出しに統一)
        assert txt.count(f"### {n}.") == 1, f"gotchas.md の §{n} 見出しが 1 個でない"


def test_refs_changelog_exists():
    """changelog.md が存在し、SKILL.md が参照する ID を含む (参照孤立の検出)."""
    f = REFS_DIR / "changelog.md"
    assert f.exists(), f"{f} not found"
    txt = _read(f)
    for ref_id in ["P1", "P2", "P3-2", "P3-5", "M6", "M7", "R1", "H3", "EPT"]:
        assert ref_id in txt, f"changelog.md に {ref_id} の記載がない"


def test_refs_react_loop_details_exists():
    """react-loop-details.md が refs/ に存在する."""
    f = REFS_DIR / "react-loop-details.md"
    assert f.exists(), f"{f} not found"
    n = len(_read(f).splitlines())
    assert n >= 60, f"refs/react-loop-details.md too thin: {n} lines"


def test_skillmd_links_to_refs():
    """SKILL.md から refs/*.md への参照がある (Claude が必要時に Read できるよう)."""
    txt = _read(SKILL_MD)
    assert "refs/state-management.md" in txt
    assert "refs/react-loop-details.md" in txt
    assert "refs/scoring-rubric.md" in txt  # 既存リンクの維持
    assert "refs/gotchas.md" in txt  # 退避先リンク


def test_phase15_not_resurrected():
    """Phase 1.5 の記述が誤って refs/ に残っていないか."""
    for f in [SKILL_MD] + list(REFS_DIR.glob("*.md")):
        txt = _read(f)
        assert "Phase 1.5" not in txt, f"Phase 1.5 found in {f}"
        assert "乖離チェック" not in txt, f"乖離チェック found in {f}"


def test_kanten_d_preserved():
    """観点 D 運用ルールが本体か refs/ のどちらかに残っていることを確認 (削除されない)."""
    txt = _read(SKILL_MD)
    for f in REFS_DIR.glob("*.md"):
        txt += _read(f)
    assert "観点D" in txt
    assert "計画指示明瞭度" in txt


def test_push_score_workflow_documented():
    """push-score の呼び出し手順が本体か refs/ に残っていることを確認."""
    txt = _read(SKILL_MD)
    for f in REFS_DIR.glob("*.md"):
        txt += _read(f)
    assert "push-score" in txt
    assert "mark-passes" in txt


def test_executor_declares_bounded_allowed_tools_without_agent_or_rm():
    """#93: executor は許可ツールを明示し、Agent と rm 系を含めない。"""
    txt = _read(EXECUTOR_MD)
    header = txt.split("---", 2)[1]
    assert "allowed-tools:" in header
    assert "Agent" not in header
    assert "Bash(rm" not in header
    for tool in ["Read", "Edit", "Write", "Grep", "Glob"]:
        assert f"  - {tool}" in header


def test_phase1_specialist_selection_checkpoint_documented():
    """#33: Phase 1 specialist selection must be an executable state checkpoint."""
    txt = _read(SKILL_MD)
    assert "specialists recommend" in txt
    assert "--record-state" in txt
    assert "init 後" in txt


def test_specialist_final_report_summary_documented():
    """#34: final reports distinguish specialist selection intent from invocation evidence."""
    txt = _read(SKILL_MD)
    assert "【Specialists】" in txt
    assert "selected:" in txt
    assert "used:" in txt
    assert "degraded:" in txt
    assert "unselected-manual:" in txt
    assert "codex-inline" in txt
    assert "実呼び出し証跡" in txt


# ===== iter2 (A-M2 + C-H1/H2 回帰防止): M5/M6 文書ルールと旧フロー復活防止 =====


def test_skillmd_no_stale_readiness_flow():
    """廃止済み readiness_score 方式がフロー図・実行例に復活していない (C-H1)."""
    txt = _read(SKILL_MD)
    assert "autonomous_readiness_score" not in txt
    assert "readiness 0.85" not in txt
    assert "readiness 0.3" not in txt


def test_m6_maker_checker_in_compact_instructions():
    """M6 (インライン修正の再確認) が Compact Instructions 内に存在する (C-H2)."""
    txt = _read(SKILL_MD)
    compact = txt.split("## state.json 操作")[0]
    assert "M6" in compact and "インライン修正" in compact, "M6 が compaction 耐性セクションにない"


def test_issue126_review_agreement_gate_in_rubric():
    """#126: review_agreement は items から独立した gate として rubric にある."""
    txt = _read(REFS_DIR / "scoring-rubric.md")
    assert "review_agreement" in txt
    assert "composite には含めない" in txt
    assert "max delta > 1.5" in txt
    assert "WARN" in txt


def test_skillmd_reads_assumptions_path_field():
    """B-M1: 復元手順が固定パスでなく assumptions_path フィールド参照を指示している."""
    txt = _read(SKILL_MD)
    assert "assumptions_path" in txt


def test_skillmd_hallucination_discipline_has_verification():
    """P1-1: Compact Instructions のハルシネーション項に照合・機械検証規律が存在する.

    bd12 (scorer/push-score/critic/Edit 捏造) や ss-5292 (PR番号/push 捏造) を再発させないため、
    機械検証可能なアクションの結果を外部再照合で確認する規律を ハルシネーション項に明記すること。
    '照合' かつ ('捏造' or '機械検証' or '再取得') がCompact Instructions (## state.json 操作より前) に存在する。
    """
    txt = _read(SKILL_MD)
    compact = txt.split("## state.json 操作")[0]
    has_kensho = "照合" in compact
    has_discipline = any(kw in compact for kw in ("捏造", "機械検証", "再取得"))
    assert has_kensho and has_discipline, (
        "Compact Instructions のハルシネーション項に照合+機械検証規律が見つからない "
        f"(照合={has_kensho}, discipline={has_discipline})"
    )


def test_reviewer_regression_baseline_uses_merge_base():
    """#15: worktree 退行判定は merge-base を基点にする規律を持つ."""
    txt = _read(REVIEWER_MD)
    assert "merge-base" in txt
    assert "git diff $BASE" in txt


def test_test_authenticity_rules_documented():
    """#17: reviewer/scorer に negative case を含むテスト真正性チェックがある."""
    reviewer = _read(REVIEWER_MD)
    scorer = _read(SCORER_MD)
    assert "negative case" in reviewer or "トートロジー" in reviewer
    assert "テスト真正性" in scorer or "negative" in scorer


def test_issue120_standard_phase5_uses_aggregate_reviews_not_scorer_spawn():
    """#120: 標準 Phase 5 は aggregate-reviews → push-score で、scorer spawn を含めない."""
    txt = _read(SKILL_MD) + "\n" + _read(REFS_DIR / "react-loop-details.md")
    assert "aggregate-reviews" in txt
    assert "push-score --scoring-json" in txt
    forbidden = [
        'Skill(skill="mission-scorer"',
        "scorer にはフル 5 項目再採点",
        "scorer が書いた JSON",
    ]
    leaked = [token for token in forbidden if token in txt]
    assert not leaked, f"standard flow still references scorer spawn/old role: {leaked}"


def test_issue120_scorer_is_fallback_converter_without_write_contract():
    """#120: mission-scorer は fallback converter で、Write 権限や Write 指示を持たない."""
    txt = _read(SCORER_MD)
    header = txt.split("---", 2)[1]
    assert "allowed-tools:" in header
    assert "Write" not in header
    assert "fallback converter" in txt
    assert "mission-review/1" in txt
    assert "ファイルへ Write しません" in txt
    assert "JSON ファイルを書き込む" in txt
    assert "composite / min_item" in txt


def test_issue120_fallback_condition_is_canonical_in_scorer_skill():
    """#120: fallback 発動条件は mission-scorer/SKILL.md の 1 セクションに集約する."""
    scorer = _read(SCORER_MD)
    non_scorer = "\n".join([
        _read(SKILL_MD),
        _read(REFS_DIR / "react-loop-details.md"),
        _read(REFS_DIR / "gotchas.md"),
    ])
    combined = "\n".join([
        non_scorer,
        scorer,
    ])
    assert scorer.count("## Fallback 発動条件") == 1
    assert combined.count("## Fallback 発動条件") == 1
    assert "reviewer に 1 回だけ再依頼" in scorer
    assert "reviewer に 1 回だけ再依頼" not in non_scorer


def test_complexity_overestimate_cost_documented():
    """#18: 複雑度過大見積もりコストと assumptions.md への根拠記録を明記する."""
    txt = _read(SKILL_MD)
    assert "過大見積もり" in txt
    assert "assumptions.md" in txt and "判定根拠" in txt


def test_halt_report_cannot_sound_complete():
    """halt_reason がある状態を達成扱いで報告しないための文言ガード."""
    txt = _read(SKILL_MD)
    compact = txt.split("## state.json 操作")[0]
    assert "halt_reason" in compact
    assert "完了報告語彙は禁止" in compact
    assert "⏸️ 中断 / 未完了" in txt
