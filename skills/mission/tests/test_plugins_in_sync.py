"""plugins/mission 配下の同期対象ファイルが skills/ 正典と一致することを確認する.

同期対象 (3ファイル):
  scripts/mission-stop-guard.sh
  skills/mission/bin/mission-state.py
  skills/mission/SKILL.md

対応する plugins 側パス:
  plugins/mission/scripts/mission-stop-guard.sh
  plugins/mission/skills/mission/bin/mission-state.py
  plugins/mission/skills/mission/SKILL.md

同期コマンド:
  cp scripts/mission-stop-guard.sh        plugins/mission/scripts/mission-stop-guard.sh
  cp skills/mission/bin/mission-state.py  plugins/mission/skills/mission/bin/mission-state.py
  cp skills/mission/SKILL.md              plugins/mission/skills/mission/SKILL.md
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
        REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "bin" / "mission-state.py",
    ),
    (
        REPO_ROOT / "skills" / "mission" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "SKILL.md",
    ),
]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


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
    src, dst = SYNC_PAIRS[1]
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
    src, dst = SYNC_PAIRS[2]
    assert src.exists(), f"正典が存在しない: {src}"
    assert dst.exists(), f"plugins 側が存在しない: {dst}"
    assert _md5(src) == _md5(dst), (
        f"SKILL.md が未同期。\n"
        f"  正典: {src}\n"
        f"  plugins: {dst}\n"
        f"  同期コマンド: cp {src} {dst}"
    )
