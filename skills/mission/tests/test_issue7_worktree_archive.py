"""Issue #7: _iter_state_files が archive/worktree-*/*.json を include_archive=True で列挙する。"""
import importlib.util
import json
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("mission_state_mod", MISSION_STATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
_iter_state_files = _mod._iter_state_files


def _make_json(path: Path, data=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {"ok": True}))
    return path


def test_worktree_archive_included_with_flag(tmp_path):
    """include_archive=True のとき archive/worktree-foo/*.json が列挙される。"""
    ms_dir = tmp_path / ".mission-state"
    wt_file = _make_json(ms_dir / "archive" / "worktree-foo" / "cc-x.json",
                         {"loop_active": False, "passes": True})

    found = list(_iter_state_files(tmp_path, include_archive=True))
    assert wt_file in found, f"worktree ファイルが見つからない: {found}"


def test_worktree_archive_excluded_without_flag(tmp_path):
    """include_archive=False (デフォルト) のとき archive/worktree-*/*.json は列挙されない。"""
    ms_dir = tmp_path / ".mission-state"
    wt_file = _make_json(ms_dir / "archive" / "worktree-bar" / "cc-y.json")
    # sessions/ にアクティブな state も置いておく
    _make_json(ms_dir / "sessions" / "cc-active.json", {"loop_active": True})

    found = list(_iter_state_files(tmp_path, include_archive=False))
    assert wt_file not in found, "デフォルトで worktree が列挙されてしまった"


def test_worktree_archive_multiple_subdirs(tmp_path):
    """複数の worktree-* ディレクトリが全て列挙される。"""
    ms_dir = tmp_path / ".mission-state"
    f1 = _make_json(ms_dir / "archive" / "worktree-feat1" / "a.json")
    f2 = _make_json(ms_dir / "archive" / "worktree-feat2" / "b.json")
    f3 = _make_json(ms_dir / "archive" / "state-abc123.json")  # 通常 archive も含む

    found = list(_iter_state_files(tmp_path, include_archive=True))
    assert f1 in found
    assert f2 in found
    assert f3 in found
