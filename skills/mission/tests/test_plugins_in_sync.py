"""plugins/mission 配下の同期対象ファイルが skills/ 正典と一致することを確認する.

同期対象:
  scripts/mission-stop-guard.sh
  scripts/mission-audit.py
  skills/mission/bin/mission-state.py
  skills/mission/lib/activity_segments.py
  skills/mission/lib/mission_common.py
  skills/mission/refs/specialist-registry.md (存在する場合)
  skills/mission/refs/self-improvement.md
  skills/mission/refs/changelog.md
  skills/mission/refs/state-management.md
  skills/mission/SKILL.md
  skills/mission-planner/SKILL.md
  skills/mission-critic/SKILL.md
  skills/mission-reviewer/SKILL.md
  skills/mission-scorer/SKILL.md

対応する plugins 側パス:
  plugins/mission/scripts/mission-stop-guard.sh
  plugins/mission/scripts/mission-audit.py
  plugins/mission/skills/mission/bin/mission-state.py
  plugins/mission/skills/mission/lib/activity_segments.py
  plugins/mission/skills/mission/lib/mission_common.py
  plugins/mission/skills/mission/refs/specialist-registry.md (存在する場合)
  plugins/mission/skills/mission/refs/self-improvement.md
  plugins/mission/skills/mission/refs/changelog.md
  plugins/mission/skills/mission/refs/state-management.md
  plugins/mission/skills/mission/SKILL.md
  plugins/mission/skills/mission-planner/SKILL.md
  plugins/mission/skills/mission-critic/SKILL.md
  plugins/mission/skills/mission-reviewer/SKILL.md
  plugins/mission/skills/mission-scorer/SKILL.md

同期コマンド:
  cp scripts/mission-stop-guard.sh        plugins/mission/scripts/mission-stop-guard.sh
  cp scripts/mission-audit.py             plugins/mission/scripts/mission-audit.py
  cp skills/mission/bin/mission-state.py  plugins/mission/skills/mission/bin/mission-state.py
  cp skills/mission/lib/activity_segments.py plugins/mission/skills/mission/lib/activity_segments.py
  cp skills/mission/lib/mission_common.py plugins/mission/skills/mission/lib/mission_common.py
  cp skills/mission/refs/specialist-registry.md plugins/mission/skills/mission/refs/specialist-registry.md
  cp skills/mission/refs/self-improvement.md plugins/mission/skills/mission/refs/self-improvement.md
  cp skills/mission/refs/changelog.md plugins/mission/skills/mission/refs/changelog.md
  cp skills/mission/refs/state-management.md plugins/mission/skills/mission/refs/state-management.md
  cp skills/mission/SKILL.md              plugins/mission/skills/mission/SKILL.md
  cp skills/mission-planner/SKILL.md      plugins/mission/skills/mission-planner/SKILL.md
  cp skills/mission-critic/SKILL.md       plugins/mission/skills/mission-critic/SKILL.md
  cp skills/mission-reviewer/SKILL.md     plugins/mission/skills/mission-reviewer/SKILL.md
  cp skills/mission-scorer/SKILL.md       plugins/mission/skills/mission-scorer/SKILL.md
"""
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # mission-selfheal/

SYNC_PAIRS = [
    (
        REPO_ROOT / "scripts" / "mission-stop-guard.sh",
        REPO_ROOT / "plugins" / "mission" / "scripts" / "mission-stop-guard.sh",
    ),
    (
        REPO_ROOT / "scripts" / "mission-audit.py",
        REPO_ROOT / "plugins" / "mission" / "scripts" / "mission-audit.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "bin" / "mission-state.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "specialist-registry.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "specialist-registry.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "self-improvement.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "self-improvement.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "changelog.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "changelog.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-planner" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-planner" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-critic" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-critic" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-reviewer" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-reviewer" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-scorer" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-scorer" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "refs" / "state-management.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "refs" / "state-management.md",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "activity_segments.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "activity_segments.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "lib" / "mission_common.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib" / "mission_common.py",
    ),
]

MISSION_STATE_DISTRIBUTION_MARKERS = [
    "specialist accounting required before pass",
    "PREPARATION_ONLY_MARKERS",
    "_classify_command_provider_result",
]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _assert_optional_pair_in_sync(src: Path, dst: Path, label: str):
    if not src.exists() and not dst.exists():
        return
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"{label} が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_stop_guard_in_sync():
    """scripts/mission-stop-guard.sh と plugins/mission/scripts/mission-stop-guard.sh が一致する."""
    src, dst = SYNC_PAIRS[0]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-stop-guard.sh が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_mission_state_py_in_sync():
    """skills/mission/bin/mission-state.py と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[2]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-state.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_activity_segments_py_in_sync():
    """Shared activity timing reducer is identical in the distribution mirror."""
    src, dst = SYNC_PAIRS[-2]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"activity_segments.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_mission_common_py_in_sync():
    """Shared state identity and dedupe rank are identical in the mirror."""
    src, dst = SYNC_PAIRS[-1]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission_common.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_mission_state_distribution_contains_specialist_accounting_guards():
    """配布 wrapper が specialist accounting/result-contract gate を欠落させない."""
    src, dst = SYNC_PAIRS[2]
    for path in (src, dst):
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in MISSION_STATE_DISTRIBUTION_MARKERS if marker not in text]
        assert not missing, f"{path} is missing distribution-critical markers: {missing}"


def test_state_management_reference_in_sync():
    """worktree archive を含む state management reference が配布 wrapper と一致する."""
    src, dst = SYNC_PAIRS[11]
    _assert_optional_pair_in_sync(src, dst, "state-management.md")


def test_skill_md_in_sync():
    """skills/mission/SKILL.md と plugins/mission/skills/mission/SKILL.md が一致する."""
    src, dst = SYNC_PAIRS[6]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_planner_skill_md_in_sync():
    """skills/mission-planner/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[7]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-planner/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_critic_skill_md_in_sync():
    """skills/mission-critic/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[8]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-critic/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_reviewer_skill_md_in_sync():
    """skills/mission-reviewer/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[9]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-reviewer/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_scorer_skill_md_in_sync():
    """skills/mission-scorer/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[10]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-scorer/SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_mission_audit_py_in_sync():
    """scripts/mission-audit.py と plugins/mission/scripts/mission-audit.py が一致する."""
    src, dst = SYNC_PAIRS[1]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"mission-audit.py が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_specialist_registry_md_in_sync_when_present():
    """specialist-registry.md は作成済みの場合だけ plugins/mission 側との一致を確認する."""
    src, dst = SYNC_PAIRS[3]
    _assert_optional_pair_in_sync(src, dst, "specialist-registry.md")


def test_self_improvement_md_in_sync():
    """skills/mission/refs/self-improvement.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[4]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"self-improvement.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_changelog_md_in_sync():
    """skills/mission/refs/changelog.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[5]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"changelog.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )
