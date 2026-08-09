"""#312 (F3): CC エージェントの activity_segments 記録ギャップの修復.

実運用監査 (2026-08-01): segments カバレッジが Codex 86% vs CC 13%。原因は
CC が従う `next` の command_hint が `set phase='"..."'` (segment 非記録経路) を
案内していたこと。SKILL.md は advance 優先と書いているが、state-driven loop の
CC は next の hint に従う。

Contract under test:
1. next の command_hint から `set phase=` が消え、atomic な advance
   (--phase + --activity) を案内する (planning / executing の 2 遷移)
2. state 側 fallback: `set phase=<非終端>` 実行時に open segment が無ければ
   phase に応じた active segment を自動 open する (set 時点から。過去は塗らない)
3. 既に open segment がある場合は変更しない (推測切替をしない)
4. 終端 phase への set では segment を open しない
"""

import json
import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load()


def _sessions(tmp_path):
    d = tmp_path / ".mission-state" / "sessions"
    return sorted(d.glob("*.json")) if d.is_dir() else []


# ===== 1. next hint が advance を案内 =====

def _next_for(phase, iteration=1):
    return MS._derive_next_action({
        "mission": "m", "mission_id": "x", "loop_active": True, "passes": False,
        "halt_reason": "", "phase": phase, "iteration": iteration,
        "reviewer_count": 2,
    })


def test_planning_hint_uses_advance():
    hint = _next_for("planning")["command_hint"]
    assert "set phase" not in hint
    assert "advance --phase executing --activity active:implementation" in hint


def test_executing_hint_uses_advance():
    hint = _next_for("executing")["command_hint"]
    assert "set phase" not in hint
    assert "advance --phase reviewing --activity reviewer-wait:review-response" in hint


# ===== 2-4. set phase の fallback =====

def test_set_phase_opens_segment_when_none(run_cli, tmp_path):
    run_cli("init", "m", "--complexity", "Standard", cwd=tmp_path, check=True)
    r = run_cli("set", "phase=executing", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    state = json.loads(_sessions(tmp_path)[0].read_text())
    cur = state.get("activity_current")
    assert cur and cur["kind"] == "active" and cur["reason"] == "implementation", (
        "#312: set phase は open segment が無ければ fallback で active segment を開くべき")


def test_set_phase_keeps_existing_open_segment(run_cli, tmp_path):
    run_cli("init", "m", "--complexity", "Standard", cwd=tmp_path, check=True)
    run_cli("activity", "start", "--kind", "active", "--reason", "planning",
            cwd=tmp_path, check=True)
    before = json.loads(_sessions(tmp_path)[0].read_text())["activity_current"]
    assert before["origin"] == "manual"
    run_cli("set", "phase=executing", cwd=tmp_path, check=True)
    state = json.loads(_sessions(tmp_path)[0].read_text())
    cur = state.get("activity_current")
    assert cur and cur["reason"] == "planning", "手動 override は phase 遷移後も保持する"
    assert cur["origin"] == "manual"


def test_set_reviewing_opens_reviewer_wait(run_cli, tmp_path):
    run_cli(
        "init", "m", "--complexity", "Standard",
        "--artifact-applicability", "not-applicable",
        cwd=tmp_path, check=True,
    )
    run_cli("set", "phase=executing", cwd=tmp_path, check=True)
    # executing の fallback segment を閉じて、open なしの状態で reviewing へ
    run_cli("activity", "end", cwd=tmp_path, check=True)
    run_cli("set", "phase=reviewing", cwd=tmp_path, check=True)
    state = json.loads(_sessions(tmp_path)[0].read_text())
    cur = state.get("activity_current")
    assert cur and cur["kind"] == "reviewer-wait" and cur["reason"] == "review-response"
