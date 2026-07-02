"""Issue #99: Python 3.9 (macOS Xcode CLT 同梱 python3) で mission-state.py が即クラッシュする.

`str | None` (PEP 604) は Python 3.10+ 専用。SKILL.md のコマンド例は `python3` 直書きのため、
3.9 環境では skill 開始手順 step 1 から全滅する。`from __future__ import annotations` (3.7+) で
注釈を遅延評価にして解決する。
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BIN = Path(__file__).resolve().parents[1] / "bin"

# python3 直書きで実行され得る CLI エントリポイントと、それらが import するモジュール
TARGETS = [
    BIN / "mission-state.py",
    BIN / "mission-migrate.py",
    Path(__file__).resolve().parents[1] / "lib" / "specialist_accounting.py",
    REPO_ROOT / "scripts" / "mission-audit.py",
    REPO_ROOT / "scripts" / "mission-state.py",
]


def _has_future_annotations(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(a.name == "annotations" for a in node.names):
                return True
    return False


@pytest.mark.parametrize("path", TARGETS, ids=lambda p: p.name)
def test_future_annotations_import_present(path):
    """全 CLI エントリポイントが from __future__ import annotations を持つ (3.9 互換)."""
    assert path.exists(), f"missing target: {path}"
    assert _has_future_annotations(path), (
        f"{path.name} に `from __future__ import annotations` がありません。"
        " PEP 604 union 注釈 (X | None) が Python 3.9 でクラッシュします (Issue #99)"
    )


SYSTEM_PY = Path("/usr/bin/python3")
_needs_py39 = pytest.mark.skipif(
    not SYSTEM_PY.exists()
    or subprocess.run([str(SYSTEM_PY), "-c", "import sys; sys.exit(0 if sys.version_info < (3, 10) else 1)"],
                      capture_output=True).returncode != 0,
    reason="system python3 is not < 3.10; real 3.9 regression check not applicable",
)


@_needs_py39
def test_mission_state_help_runs_on_py39(tmp_path):
    """実機 3.9 回帰: --help がモジュールパース+argparse 構築を通過する (Issue #99 の実測クラッシュ点)."""
    r = subprocess.run([str(SYSTEM_PY), str(BIN / "mission-state.py"), "--help"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, f"stderr: {r.stderr[:500]}"
    assert "push-score" in r.stdout


@_needs_py39
def test_mission_state_next_runs_on_py39(tmp_path):
    """実機 3.9 回帰: state 不在の next (read-only 全コードパスの代表) が動く."""
    r = subprocess.run([str(SYSTEM_PY), str(BIN / "mission-state.py"), "next"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, f"stderr: {r.stderr[:500]}"
    assert "init" in r.stdout
