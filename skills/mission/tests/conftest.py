"""Shared fixtures for mission-state.py tests."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"

# Claude Code/Codex のセッション識別 env。実運用では multi-session を自動有効化するが、
# テストは legacy 既定で動かすため隔離する (明示テストは env_extra/monkeypatch で注入)。
_SESSION_ENV_VARS = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


@pytest.fixture(autouse=True)
def _isolate_session_env(monkeypatch):
    """全テストを env 非依存にする: MISSION_* と Claude Code/Codex session env を除去。
    in-process (importlib) テストにも効く。subprocess は run_cli が別途遮断。"""
    for k in ("MISSION_MULTI_SESSION", "MISSION_SESSION_ID", "MISSION_SEARCH_ROOTS", *_SESSION_ENV_VARS):
        monkeypatch.delenv(k, raising=False)


def _read_state(sd):
    return json.loads((sd / "sessions" / "test.json").read_text())


@pytest.fixture
def read_state():
    return _read_state


@pytest.fixture
def state_dir(tmp_path):
    """tmp_path に .mission-state/state.json を初期化して返す."""
    sd = tmp_path / ".mission-state"
    (sd / "sessions").mkdir(parents=True)
    initial = {
        "mission": "test mission",
        "mission_id": "abc12345",
        "subtasks": [],
        "complexity": "Standard",
        "reviewer_count": 2,
        "max_iter": 5,
        "threshold": 4.0,
        "iteration": 1,
        "phase": "scoring",
        "score_history": [],
        "stagnation_count": 0,
        "decisions": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "assumptions_path": ".mission-state/assumptions.md",
        "started_at": "2026-05-25T00:00:00Z",
        "updated_at": "2026-05-25T00:00:00Z",
        "schema_version": 2,
        "project_root": str(tmp_path),
        "pid": 0,
        "hostname": "test",
        "session_id": "test",
        "created_at_session": "2026-05-25T00:00:00Z",
    }
    (sd / "sessions" / "test.json").write_text(json.dumps(initial, indent=2))
    return sd


@pytest.fixture
def run_cli(tmp_path):
    """mission-state.py をサブプロセスで呼ぶ helper.

    env isolation (2026-06-10): 既定で MISSION_* prefix の変数を
    継承環境から除去し、env_extra による明示注入のみ許す。外部セッションの
    MISSION_* 汚染でテスト結果が変わる非決定性を遮断する。
    """
    def _run(*args, cwd=None, check=False, env_extra=None):
        # MISSION_* prefix 一括遮断 (将来 mission-state.py が新しい MISSION_* を読んでも自動でマスク)
        base_env = {k: v for k, v in os.environ.items()
                    if not k.startswith("MISSION_") and k not in _SESSION_ENV_VARS}
        # env_extra でセッション識別 (MISSION_SESSION_ID/Claude Code/Codex) が明示されていなければ
        # デフォルト sid="test" を注入 (テストを sessions/test.json に固定)
        _sid_keys = ("MISSION_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
        if not (env_extra and any(k in env_extra for k in _sid_keys)):
            base_env["MISSION_SESSION_ID"] = "test"
        if args and args[0] == "push-score" and "--scoring-json" not in args and "--scoring-output" not in args:
            base_env["MISSION_REQUIRE_SCORING_EVIDENCE"] = "0"
        if env_extra is not None:
            for key, value in env_extra.items():
                if value is None:
                    base_env.pop(key, None)
                else:
                    base_env[key] = value
        return subprocess.run(
            [sys.executable, str(MISSION_STATE_PY), *args],
            cwd=str(cwd or tmp_path),
            capture_output=True,
            text=True,
            check=check,
            env=base_env,
        )
    return _run
