"""Codex 対応: エージェントプロセス判定が claude/codex 両対応であることを検証.

mission スキルを Codex CLI から実行した場合、親プロセス名は 'codex' になる。
PID owner 判定 (find_agent_pid / _pid_is_agent) が claude だけでなく codex も
エージェントプロセスとして認識しないと、Codex 上で Stop hook のループ強制が
機能しない。共通ヘルパー _comm_is_agent() がその判定の単一の真実源。
"""
import importlib.util
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load():
    spec = importlib.util.spec_from_file_location("mission_state_mod", MISSION_STATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("comm,expected", [
    ("claude", True),
    ("claude.exe", True),
    ("/Applications/Claude/claude", True),   # フルパス末尾でも可
    ("codex", True),                         # Codex 対応の核心
    ("codex.exe", True),
    ("/usr/local/bin/codex", True),
    ("bash", False),
    ("python3", False),
    ("node", False),
    ("zsh", False),
    ("notcodex", False),       # basename 一致でない: false positive を除外
    ("xclaude", False),
    ("myclaude", False),
    ("notcodex.exe", False),
    ("/path/to/notcodex", False),  # ディレクトリ名末尾の偽陽性も除外
    ("", False),
    ("   ", False),
])
def test_comm_is_agent(comm, expected):
    mod = _load()
    assert mod._comm_is_agent(comm) is expected
