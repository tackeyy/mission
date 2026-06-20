"""SKILL.md スリム化の構造テスト (S1: RED → S4: GREEN)."""
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL_DIR / "SKILL.md"
REFS_DIR = SKILL_DIR / "refs"
REPO_ROOT = SKILL_DIR.parent.parent
REVIEWER_MD = REPO_ROOT / "skills" / "mission-reviewer" / "SKILL.md"
SCORER_MD = REPO_ROOT / "skills" / "mission-scorer" / "SKILL.md"


def _read(p):
    return p.read_text() if p.exists() else ""


def test_skillmd_size_under_420_lines():
    """SKILL.md 本体は 420 行以下.

    Anthropic 公式スキル作成ガイダンス (skill-creator) は「<500 行が理想」とし、
    500 に近づいたら Progressive Disclosure で詳細を refs/ へ退避せよと定める。
    420 はその規律値 (公式上限 500 に余裕を持たせたライン)。
    起動毎に全ロードされるため、状況依存の詳細は refs/gotchas.md 等へ退避済み。
    """
    n = len(_read(SKILL_MD).splitlines())
    assert n <= 420, f"SKILL.md is {n} lines (target: <= 420, 公式<500に余裕を持たせた規律値)"


def test_skillmd_contains_critical_keywords():
    """本体から失えない必須キーワードが残っていることを確認."""
    txt = _read(SKILL_MD)
    must_have = [
        "loop_active",       # 終了判定の核
        "passes",            # PASS 判定
        "score_history",     # 採点記録
        "threshold",         # 合格閾値
        "push-score",        # バグ修正の必須手順
        "Trigger 1",         # 不可逆操作確認
        "Trigger 2",         # 中断条件
        "観点D",             # 観点D 運用
        "Stop hook",         # ループ強制
        "差分レビュー",       # P2: iter2以降は検証1名 (レビューコスト40%の主因対策)
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
    for ref_id in ["P1", "P2", "P3-2", "P3-5", "M6", "M7"]:
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


def test_phase1_specialist_selection_checkpoint_documented():
    """#33: Phase 1 specialist selection must be an executable state checkpoint."""
    txt = _read(SKILL_MD)
    assert "specialists recommend" in txt
    assert "--record-state" in txt
    assert "init 後" in txt


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


def test_m5_consensus_carryover_ban_in_rubric():
    """M5: consensus 据置禁止ルールと省略時の push-score 具体例が rubric にある."""
    txt = _read(REFS_DIR / "scoring-rubric.md")
    assert "据置" in txt and "禁止" in txt
    assert "--min-item" in txt, "省略時の push-score 具体例がない"


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
