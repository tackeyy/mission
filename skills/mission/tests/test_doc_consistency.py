"""Tier1/2 ドキュメント整合の回帰ガード (RED→GREEN).

legacy 廃止(2026-06-13)後もドキュメントに残った陳腐化手順・重複を除去し、
再混入を防ぐためのガードテスト。実コードの実態を正とする。
"""
import json
import re
from pathlib import Path

MISSION_DIR = Path(__file__).resolve().parent.parent          # mission/
REPO_ROOT = MISSION_DIR.parents[1]
REFS = MISSION_DIR / "refs"
SKILLS_ROOT = MISSION_DIR.parent                              # .claude/skills/
SKILL_MD = MISSION_DIR / "SKILL.md"

# #118: distributed ref files must not leak a maintainer's home path or personal
# skill names (OSS Personal Skill Boundary, AGENTS.md).
_HOME_PATH_RE = re.compile(r"/Users/[^/\s`\"')]+/")
_PERSONAL_SKILL_NAMES = ("designer-product",)


def _r(p):
    return p.read_text() if p.exists() else ""


def _distributed_ref_files():
    roots = [MISSION_DIR / "refs", REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs"]
    files = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.glob("*.md")))
    return files


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
        "specialist selection checkpoint",
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
        "specialist selection checkpoint",
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


def test_v103_changelog_mentions_selection_checkpoint_audit():
    """v1.0.3 の audit/checkpoint release themes を changelog から落とさない."""
    en = _release_section(_r(REPO_ROOT / "CHANGELOG.md"), "1.0.3").lower()
    ja = _release_section(_r(REPO_ROOT / "CHANGELOG.ja.md"), "1.0.3").lower()
    for token in ("specialist-selection checkpoint", "specialist selection checkpoint", "git log <previous-tag>..head --oneline"):
        assert token in en, f"CHANGELOG.md v1.0.3 missing release theme: {token}"
    for token in ("selection metadata", "specialist selection checkpoint", "git log <previous-tag>..head --oneline"):
        assert token in ja, f"CHANGELOG.ja.md v1.0.3 missing release theme: {token}"


def test_v104_changelog_mentions_provider_extension_release_theme():
    """v1.0.4 の provider extension release themes を changelog から落とさない."""
    en = _release_section(_r(REPO_ROOT / "CHANGELOG.md"), "1.0.4").lower()
    ja = _release_section(_r(REPO_ROOT / "CHANGELOG.ja.md"), "1.0.4").lower()
    for token in ("auto-discovered", "skill/plugin manifest", "kind: command", "first-use risk consent", "oracle"):
        assert token in en, f"CHANGELOG.md v1.0.4 missing release theme: {token}"
    for token in ("自動 discovery", "skill/plugin manifest", "kind: command", "first-use risk consent", "oracle"):
        assert token in ja, f"CHANGELOG.ja.md v1.0.4 missing release theme: {token}"


def test_v110_changelog_mentions_release_themes():
    """v1.1.0 の release themes を changelog から落とさない."""
    en = _release_section(_r(REPO_ROOT / "CHANGELOG.md"), "1.1.0").lower()
    ja = _release_section(_r(REPO_ROOT / "CHANGELOG.ja.md"), "1.1.0").lower()
    required_tokens = (
        "aggregate-reviews",
        "task-required",
        "resume",
        "model_id",
        "review_agreement",
        "findings_evidence_path",
        "mission_common.py",
        "oss portability",
        "push-score --scoring-json",
        "--resubmit-reason",
        "open_high",
    )
    for token in required_tokens:
        assert token.lower() in en, f"CHANGELOG.md v1.1.0 missing release theme: {token}"
        assert token.lower() in ja, f"CHANGELOG.ja.md v1.1.0 missing release theme: {token}"


def test_release_version_paths_are_in_sync():
    """Plugin manifests and visible install paths should point at the same release version."""
    manifest_paths = (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "plugins/mission/.codex-plugin/plugin.json",
    )
    versions = {
        json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))["version"]
        for path in manifest_paths
    }
    assert len(versions) == 1, f"manifest versions differ: {versions}"
    version = versions.pop()
    expected_path = f"mission-marketplace/mission/{version}"
    for rel in (
        "README.md",
        "README.ja.md",
        "skills/mission/refs/codex-setup.md",
        "plugins/mission/skills/mission/refs/codex-setup.md",
    ):
        assert expected_path in _r(REPO_ROOT / rel), f"{rel} does not reference {expected_path}"


def test_readmes_describe_current_scoring_flow_and_test_count():
    """README の主要説明が現行 source の標準 scoring flow と検証数から drift しないこと。"""
    en = _r(REPO_ROOT / "README.md")
    ja = _r(REPO_ROOT / "README.ja.md")
    for rel, txt in (("README.md", en), ("README.ja.md", ja)):
        for token in ("mission-review/1", "aggregate-reviews", "push-score --scoring-json", "553 passed"):
            assert token in txt, f"{rel} missing current README source-sync token: {token}"
        assert "327 passed" not in txt, f"{rel} still reports stale test count"
    assert "reviewer/scorer phases" not in en
    assert "reviewer/scorer phase" not in ja


def test_release_checklist_requires_git_log_changelog_reconciliation():
    """release 時に commit subjects と changelog の突合を必須化する."""
    required = "git log <previous-tag>..HEAD --oneline"
    for rel in ("docs/MARKETPLACE_RELEASE_CHECKLIST.md", "docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md"):
        txt = _r(REPO_ROOT / rel)
        assert required in txt and "CHANGELOG" in txt.upper(), \
            f"{rel} must require git log vs changelog reconciliation"


def test_distribution_release_requires_remote_tag_and_github_release_verification():
    """version bump 後の release 完了報告前に tag/GitHub Release の実在確認を必須化する."""
    required_tokens = (
        "git ls-remote --tags origin vX.Y.Z",
        "gh release view vX.Y.Z --repo tackeyy/mission",
        "distribution release",
    )
    for rel in ("docs/MARKETPLACE_RELEASE_CHECKLIST.md", "docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md"):
        txt = _r(REPO_ROOT / rel)
        for token in required_tokens:
            assert token in txt, f"{rel} must require release publication verification: {token}"
    agents = _r(REPO_ROOT / "AGENTS.md")
    assert "Distribution Release Rule" in agents
    assert "gh release view vX.Y.Z --repo tackeyy/mission" in agents
    assert "git ls-remote --tags origin vX.Y.Z" in agents


def test_versioning_policy_separates_merge_and_distribution_releases():
    """通常 PR merge と配布 version bump を混同しないための方針を docs で固定する."""
    en = _r(REPO_ROOT / "docs/VERSIONING.md").lower()
    ja = _r(REPO_ROOT / "docs/VERSIONING.ja.md").lower()
    assert "merge release" in en and "distribution release" in en
    assert "do not bump versions for every merged pr" in en
    assert "hotfix" in en and "at most weekly" in en
    assert "merge release" in ja and "distribution release" in ja
    assert "pr を merge するたびに version を上げません" in ja
    assert "hotfix" in ja and "最大でも週 1 回" in ja


def test_release_checklist_links_versioning_policy_before_version_bump():
    """distribution release checklist は version bump 前に versioning policy 確認を求める."""
    for rel, policy in (
        ("docs/MARKETPLACE_RELEASE_CHECKLIST.md", "VERSIONING.md"),
        ("docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md", "VERSIONING.ja.md"),
    ):
        txt = _r(REPO_ROOT / rel)
        assert policy in txt, f"{rel} must link {policy}"
        assert "distribution release" in txt, f"{rel} must distinguish distribution release"
        assert "[Unreleased]" in txt, f"{rel} must preserve unreleased accumulation rule"


def test_self_improvement_issue_creation_requires_duplicate_check_and_review():
    """Audit-driven issue creation must include duplicate-check and tech-lead review evidence."""
    txt = _r(REFS / "self-improvement.md").lower()
    assert "open and closed issues" in txt
    assert "not a duplicate" in txt
    assert "development/tech-lead review" in txt
    assert "oss portability" in txt


# ---- #118: OSS portability — distributed refs must not leak personal env ----


def test_distributed_refs_have_no_maintainer_home_path():
    """配布される ref (skills + plugin mirror) に個人ホーム絶対パスが混入していないこと。"""
    offenders = []
    for f in _distributed_ref_files():
        for i, line in enumerate(f.read_text().splitlines(), 1):
            if _HOME_PATH_RE.search(line):
                offenders.append(f"{f.relative_to(REPO_ROOT)}:{i}: {line.strip()}")
    assert not offenders, "personal home paths leaked into distributed refs:\n" + "\n".join(offenders)


def test_beginner_presets_have_no_personal_skill_names():
    """specialist-registry の Beginner Presets に個人 skill 名を焼き込まないこと。"""
    offenders = []
    for f in _distributed_ref_files():
        text = f.read_text()
        for name in _PERSONAL_SKILL_NAMES:
            if name in text:
                offenders.append(f"{f.relative_to(REPO_ROOT)} contains '{name}'")
    assert not offenders, "personal skill names leaked into distributed refs:\n" + "\n".join(offenders)


def test_specialist_registry_separates_provider_consent_scopes():
    """External-send, browser-session material, and paid quota approvals must stay distinct."""
    txt = _r(REFS / "specialist-registry.md")
    assert "risk.external_service" in txt
    assert "risk.browser_automation" in txt
    assert "risk.browser_session_material" in txt
    assert "risk.may_consume_paid_quota" in txt
    assert "external service does not imply approval to reuse browser session material" in txt
    assert "neither implies paid API/model quota approval" in txt
    assert "awaiting_input_markers" in txt
    assert "awaiting_input_exit_codes" in txt


# ---- #128: ドキュメント整合 sweep の回帰ガード ----


def test_skillmd_terminal_condition_includes_open_high():
    """SKILL.md の終了判定擬似コードは open_high==0 を含む (mark-passes gate と整合)."""
    txt = _r(SKILL_MD)
    assert "open_high == 0" in txt, \
        "SKILL.md の終了判定に open_high==0 がない (mark-passes は open_high>0 を reject する)"


def test_findings_evidence_gate_documented_for_mark_passes():
    """#121: mark-passes の findings evidence 突合 gate を docs から落とさない."""
    skill = _r(SKILL_MD)
    rubric = _r(REFS / "scoring-rubric.md")
    adr = _r(REPO_ROOT / "docs" / "adr" / "002-typed-mission-state-objects.md")
    for txt, name in ((skill, "SKILL.md"), (rubric, "scoring-rubric.md"), (adr, "ADR-002")):
        assert "findings_evidence_path" in txt, f"{name} missing findings_evidence_path gate"
        assert "open_high" in txt, f"{name} missing open_high gate"


def test_review_agreement_gate_documented_separately_from_items():
    """#126: reviewer agreement は composite items から独立した gate として文書化する."""
    skill = _r(SKILL_MD)
    rubric = _r(REFS / "scoring-rubric.md")
    assert "max_agreement_delta <= 1.5" in skill
    assert "review_agreement" in rubric
    assert "composite には含めない" in rubric


def test_critic_output_includes_executor_compatible_next_iteration_plan():
    """#124: critic output must include a planner-compatible execution plan."""
    critic = _r(SKILLS_ROOT / "mission-critic" / "SKILL.md")
    required = (
        "### 実行計画 (次 iteration)",
        "| # | アクション | 完了条件 (observable) | 依存 | 対応finding |",
        "|---|---|---|---|---|",
        "`対応finding`",
        "`new`",
    )
    for token in required:
        assert token in critic, f"mission-critic/SKILL.md missing #124 output token: {token}"


def test_orchestrator_documents_single_new_based_planner_spawn_rule():
    """#124: iter2+ planner spawn must be mechanically decided by the critic plan's new marker."""
    skill = _r(SKILL_MD)
    assert "Planner spawn 判定" in skill
    assert "全ステップの `対応finding` が finding id のみ" in skill
    assert "planner を spawn せず executor に直接渡す" in skill
    assert "`new` を含むステップが 1 つでもある" in skill
    assert "iter1 は従来どおり planner 必須" in skill
    assert "mission-planner` / `mission-executor` は周回ごとに走り続ける" not in skill


def test_planner_scope_is_initial_plan_or_new_step_replan():
    """#124: mission-planner is not the default iter2+ handoff when critic plan is finding-only."""
    planner = _r(SKILLS_ROOT / "mission-planner" / "SKILL.md")
    assert "iter1 の初期計画" in planner
    assert "critic 計画に `new` ステップがある場合の再計画" in planner
    assert "finding id のみ" in planner
    assert "executor に直接渡す" in planner


def test_react_loop_no_deprecated_composite_pushscore_example():
    """react-loop-details の push-score 例は非推奨 --composite 手渡しでなく --scoring-json."""
    txt = _r(REFS / "react-loop-details.md")
    assert "--composite X --min-item Y" not in txt, \
        "react-loop-details に非推奨の --composite 手渡し push-score 例が残存 (--scoring-json 推奨)"
    assert "--scoring-json" in txt, "react-loop-details に推奨 --scoring-json 例がない"


def test_state_management_no_practicality_items_sample():
    """state-management に practicality を items キーに使う採点サンプルがない (正規キーは usability)."""
    txt = _r(REFS / "state-management.md")
    assert '"practicality":' not in txt and '"practicality" :' not in txt, \
        "state-management に旧キー practicality の items サンプルが残存 (エイリアス説明の言及は可)"
