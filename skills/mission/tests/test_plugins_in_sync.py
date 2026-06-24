"""plugins/mission 配下の同期対象ファイルが skills/ 正典と一致することを確認する.

同期対象:
  scripts/mission-stop-guard.sh
  scripts/mission-audit.py
  skills/mission/bin/mission-state.py
  skills/mission/refs/specialist-registry.md (存在する場合)
  skills/mission/refs/self-improvement.md
  skills/mission/SKILL.md
  skills/mission-reviewer/SKILL.md
  skills/mission-scorer/SKILL.md

対応する plugins 側パス:
  plugins/mission/scripts/mission-stop-guard.sh
  plugins/mission/scripts/mission-audit.py
  plugins/mission/skills/mission/bin/mission-state.py
  plugins/mission/skills/mission/refs/specialist-registry.md (存在する場合)
  plugins/mission/skills/mission/refs/self-improvement.md
  plugins/mission/skills/mission/SKILL.md
  plugins/mission/skills/mission-reviewer/SKILL.md
  plugins/mission/skills/mission-scorer/SKILL.md

同期コマンド:
  cp scripts/mission-stop-guard.sh        plugins/mission/scripts/mission-stop-guard.sh
  cp scripts/mission-audit.py             plugins/mission/scripts/mission-audit.py
  cp skills/mission/bin/mission-state.py  plugins/mission/skills/mission/bin/mission-state.py
  cp skills/mission/refs/specialist-registry.md plugins/mission/skills/mission/refs/specialist-registry.md
  cp skills/mission/refs/self-improvement.md plugins/mission/skills/mission/refs/self-improvement.md
  cp skills/mission/SKILL.md              plugins/mission/skills/mission/SKILL.md
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
        REPO_ROOT / "skills" / "mission" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-reviewer" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-reviewer" / "SKILL.md",
    ),
    (
        REPO_ROOT / "skills" / "mission-scorer" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission-scorer" / "SKILL.md",
    ),
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


def test_skill_md_in_sync():
    """skills/mission/SKILL.md と plugins/mission/skills/mission/SKILL.md が一致する."""
    src, dst = SYNC_PAIRS[5]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )


def test_reviewer_skill_md_in_sync():
    """skills/mission-reviewer/SKILL.md と plugins/mission 側が一致する."""
    src, dst = SYNC_PAIRS[6]
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
    src, dst = SYNC_PAIRS[7]
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
