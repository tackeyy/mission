"""CI シャード分割スクリプトの契約テスト。

CI の壁時間短縮のため pytest 対象を複数 job へ分割する。分割は
「決定的」「網羅的（どのシャードにも属さないファイルが無い）」
「排他的（重複実行が無い）」でなければ品質ゲートが空洞化する。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci_shard_targets.py"


def run_shard(index, total, targets, cwd=None):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index",
            str(index),
            "--total",
            str(total),
            "--targets",
            targets,
        ],
        capture_output=True,
        text=True,
        cwd=str(cwd or ROOT),
    )
    return result


def test_script_exists():
    assert SCRIPT.exists()


def test_directory_target_expands_to_tracked_test_files():
    result = run_shard(1, 1, "skills/mission")
    assert result.returncode == 0, result.stderr
    files = result.stdout.split()
    tracked = subprocess.run(
        ["git", "ls-files", "skills/mission/tests/test_*.py"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    ).stdout.split()
    assert files == sorted(tracked)


def test_shards_are_exhaustive_and_disjoint():
    total = 4
    everything = run_shard(1, 1, "skills/mission").stdout.split()
    seen = []
    for index in range(1, total + 1):
        result = run_shard(index, total, "skills/mission")
        assert result.returncode == 0, result.stderr
        seen.extend(result.stdout.split())
    assert sorted(seen) == sorted(everything)  # 網羅
    assert len(seen) == len(set(seen))  # 排他


def test_shards_are_balanced_within_one_file():
    total = 4
    sizes = [len(run_shard(i, total, "skills/mission").stdout.split()) for i in range(1, total + 1)]
    assert max(sizes) - min(sizes) <= 1


def test_output_is_deterministic():
    first = run_shard(2, 4, "skills/mission").stdout
    second = run_shard(2, 4, "skills/mission").stdout
    assert first == second


def test_explicit_file_targets_are_passed_through_and_sharded():
    targets = "skills/mission/tests/test_doc_consistency.py skills/mission/tests/test_plugins_in_sync.py"
    both = run_shard(1, 1, targets)
    assert both.returncode == 0, both.stderr
    assert both.stdout.split() == sorted(targets.split())

    first = run_shard(1, 2, targets).stdout.split()
    second = run_shard(2, 2, targets).stdout.split()
    assert sorted(first + second) == sorted(targets.split())


def test_empty_selection_fails_closed():
    """シャードが 1 件も選ばなかったら黙って success にしない。"""
    result = run_shard(3, 3, "skills/mission/tests/test_doc_consistency.py")
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_nonexistent_target_fails_closed():
    result = run_shard(1, 1, "skills/mission/tests/test_does_not_exist.py")
    assert result.returncode != 0


def test_invalid_index_fails_closed():
    for index, total in ((0, 4), (5, 4), (1, 0)):
        result = run_shard(index, total, "skills/mission")
        assert result.returncode != 0, f"index={index} total={total} should fail"
