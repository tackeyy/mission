"""Tier1/2 ドキュメント整合の回帰ガード (RED→GREEN).

legacy 廃止(2026-06-13)後もドキュメントに残った陳腐化手順・重複を除去し、
再混入を防ぐためのガードテスト。実コードの実態を正とする。
"""
from pathlib import Path

MISSION_DIR = Path(__file__).resolve().parent.parent          # mission/
REPO_ROOT = MISSION_DIR.parents[1]
REFS = MISSION_DIR / "refs"
SKILLS_ROOT = MISSION_DIR.parent                              # .claude/skills/
SKILL_MD = MISSION_DIR / "SKILL.md"


def _r(p):
    return p.read_text() if p.exists() else ""


def _release_section(text, version):
    marker = f"## [{version}]"
    start = text.index(marker)
    rest = text[start:]
    next_release = rest.find("\n## [", len(marker))
    return rest if next_release == -1 else rest[:next_release]


# ---- Tier1-1: 廃止フラグが現役コマンドとして残っていない ----
def test_no_obsolete_multi_session_flag():
    txt = _r(REFS / "state-management.md")
    assert "--multi-session" not in txt, "廃止フラグ --multi-session が state-management.md に残存 (argparse未定義=実行エラー)"
    assert "MISSION_MULTI_SESSION=1" not in txt, "廃止 env MISSION_MULTI_SESSION=1 が残存"
    assert "export MISSION_MULTI_SESSION" not in txt, "廃止 env export が残存"


# ---- Tier1-5: react-loop-details に jq 直叩きで state.json を書く例が残っていない ----
def test_no_jq_direct_state_write():
    txt = _r(REFS / "react-loop-details.md")
    assert "jq -n" not in txt, "jq -n で state.json を生成する禁止手順が残存"
    assert "jq '." not in txt, "jq で state.json を直接書き換える禁止手順が残存"


# ---- Tier1-2: halt 後再開が正規コマンド(set)で案内されている ----
def test_reactivate_uses_set_command():
    txt = _r(REFS / "gotchas.md")
    assert "set loop_active=true" in txt, "halt 後再開の正規手順 (mission-state.py set loop_active=true) が gotchas.md にない"


# ---- Tier1-4: gotchas.md の歴史的記録残骸が圧縮されている ----
def test_gotchas_compressed():
    n = len(_r(REFS / "gotchas.md").splitlines())
    assert n <= 120, f"gotchas.md が {n} 行 (target <=120: §7/§10/§11 の歴史的記録を圧縮)"


# ---- Tier1-6: SKILL.md に陳腐化した後方互換の主張がない ----
def test_no_stale_legacy_backcompat_claim():
    txt = _r(SKILL_MD)
    assert "既存 legacy state.json があればそれを継続" not in txt, "resolve_state_file は legacy を読まないので後方互換主張は誤り"


# ---- Tier2-7: 絶対評価ペナルティ表は scoring-rubric.md の単一ソース ----
def test_penalty_table_single_source():
    rubric = _r(REFS / "scoring-rubric.md")
    reviewer = _r(SKILLS_ROOT / "mission-reviewer" / "SKILL.md")
    scorer = _r(SKILLS_ROOT / "mission-scorer" / "SKILL.md")
    assert "Low 2-3 件" in rubric, "正本テーブルが scoring-rubric.md にない"
    assert "Low 2-3 件" not in reviewer, "ペナルティ表が mission-reviewer に重複 (ポインタ化すべき)"
    assert "Low 2-3 件" not in scorer, "ペナルティ表が mission-scorer に重複 (ポインタ化すべき)"


# ---- Tier2-10: サブスキルからの scoring-rubric.md 参照は絶対パス ----
def test_scoring_rubric_absolute_path():
    for sub in ("mission-scorer", "mission-critic", "mission-reviewer"):
        txt = _r(SKILLS_ROOT / sub / "SKILL.md")
        if "scoring-rubric.md" in txt:
            assert "${CLAUDE_PLUGIN_ROOT}/skills/mission/refs/scoring-rubric.md" in txt, \
                f"{sub}/SKILL.md の scoring-rubric.md 参照が絶対パスでない (fork で解決不能)"


# ---- 案B: コード位置主張は該当行の現物テキストを verbatim 引用させる ----
def test_reviewer_requires_verbatim_code_evidence():
    txt = _r(SKILLS_ROOT / "mission-reviewer" / "SKILL.md")
    assert "現物テキスト" in txt and "行番号のみ" in txt, \
        "mission-reviewer に『コード位置主張は行番号だけでなく現物テキストを貼る (行番号のみ不可)』ルールがない (案B)"


# ---- H-3: archive 自動退避の虚偽主張がない ----
def test_no_false_archive_autoexport_claim():
    sk = _r(SKILL_MD)
    assert "完了済 state は自動的に" not in sk, "cmd_init に無い archive 自動退避を SKILL.md が主張(H-3)"
    go = _r(REFS / "gotchas.md")
    assert "新規初期化せず" not in go and "action\": \"skipped\"} を返す" not in go, \
        "gotchas §6 が legacy の init skipped 挙動を記載(現行 cmd_init は skipped 返さない)"


# ---- SKILL.md の refs 説明に削除済み jq 更新コマンド例 への言及がない ----
def test_no_jq_update_example_reference():
    assert "jq 更新コマンド例" not in _r(SKILL_MD), "react-loop から削除済みの jq 更新コマンド例 を SKILL.md が参照"


# ---- jq 直書きは「禁止」で統一 (非推奨表記を残さない) ----
def test_jq_prohibition_wording_consistent():
    sm = _r(REFS / "state-management.md")
    assert "race condition の原因となるため禁止" in sm, "state-management の jq 表記が禁止に統一されていない"


# ---- GitHub Flow: issue 連携 → PR Closes #N → マージで自動クローズの規律 ----
def test_state_management_has_github_flow_issue_link():
    """GitHub Flow: issue連携ミッションは PR本文に Closes #N を入れマージで自動クローズする規律."""
    txt = _r(REFS / "state-management.md")
    assert "Closes #" in txt and "issue" in txt.lower(), \
        "state-management.md に GitHub Flow(issue→PR Closes #N→マージで自動クローズ)規律がない"


def test_v102_changelog_mentions_specialist_registry_release_theme():
    """v1.0.2 の public-facing release themes を changelog から落とさない."""
    en = _release_section(_r(REPO_ROOT / "CHANGELOG.md"), "1.0.2").lower()
    ja = _release_section(_r(REPO_ROOT / "CHANGELOG.ja.md"), "1.0.2")
    en_tokens = (
        "specialist registry",
        "task profiles",
        "invocation evidence",
        "--files",
        "overlapping files",
        "scripts/mission-audit.py",
        "self-improvement prompts",
        "low-score-pass buckets",
        "github flow",
        "closes #n",
        "contributors",
        "merge-base",
        "test-authenticity",
        "halt/incomplete root causes",
        "hook-packaging",
    )
    ja_tokens = (
        "specialist registry",
        "task_profile",
        "自動選定",
        "呼び出し証跡",
        "--files",
        "対象ファイルが重複",
        "scripts/mission-audit.py",
        "self-improvement prompt",
        "low-score pass",
        "github flow",
        "closes #n",
        "contributors",
        "merge-base",
        "テスト真正性",
        "halt/incomplete",
        "hook packaging",
    )
    for token in en_tokens:
        assert token in en, f"CHANGELOG.md v1.0.2 missing release theme: {token}"
    ja_lower = ja.lower()
    for token in ja_tokens:
        assert token.lower() in ja_lower, f"CHANGELOG.ja.md v1.0.2 missing release theme: {token}"


def test_release_checklist_requires_git_log_changelog_reconciliation():
    """release 時に commit subjects と changelog の突合を必須化する."""
    required = "git log <previous-tag>..HEAD --oneline"
    for rel in ("docs/MARKETPLACE_RELEASE_CHECKLIST.md", "docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md"):
        txt = _r(REPO_ROOT / rel)
        assert required in txt and "CHANGELOG" in txt.upper(), \
            f"{rel} must require git log vs changelog reconciliation"
