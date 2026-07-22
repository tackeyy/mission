"""Issue #241: bounded context projection — fork へ evidence manifest のみ渡す.

Contract under test:
1. context-manifest が state から bounded manifest JSON を生成する
2. manifest に mission goal, iteration, base/candidate HEAD, finding IDs が含まれる
3. _derive_next_action の reviewing が context_mode を返す
4. manifest 生成失敗時に context_mode="full" へ fallback
5. critic_has_new_scope=true → context_mode="full" (新規 scope では bounded 不適)
"""

import json
import os
import hashlib
import importlib.util
from pathlib import Path
from unittest.mock import patch

import pytest


def _load_mission_state():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load_mission_state()

TEST_SID = "test-241"


def _make_state(tmp_path, *, iteration=2, phase="reviewing", reviewer_count=3,
                mission="テスト: bounded context の検証",
                critic_has_new_scope=None, score_history=None, **extra):
    state_dir = tmp_path / ".mission-state"
    sessions_dir = state_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sf = sessions_dir / f"{TEST_SID}.json"
    data = {
        "mission": mission,
        "mission_id": "bounded1234abc",
        "pid": 12345,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "phase": phase,
        "iteration": iteration,
        "reviewer_count": reviewer_count,
        "project_root": str(tmp_path),
        "assumptions_path": ".mission-state/assumptions.md",
    }
    if critic_has_new_scope is not None:
        data["critic_has_new_scope"] = critic_has_new_scope
    if score_history is not None:
        data["score_history"] = score_history
    data.update(extra)
    sf.write_text(json.dumps(data))
    return sf


# --- 1. context-manifest generates bounded manifest ---

def test_context_manifest_generates_json(tmp_path, monkeypatch):
    """context-manifest が manifest JSON を生成する."""
    monkeypatch.setenv("MISSION_SESSION_ID", TEST_SID)
    _make_state(tmp_path, iteration=2)
    out = tmp_path / "manifest.json"
    args = type("Args", (), {
        "iteration": 2,
        "out": str(out),
    })()
    monkeypatch.chdir(tmp_path)
    MS.cmd_context_manifest(args)
    manifest = json.loads(out.read_text())
    assert manifest["schema"] == "mission-context-manifest/1"
    assert manifest["iteration"] == 2


def test_context_manifest_contains_mission_goal(tmp_path, monkeypatch):
    """manifest に mission goal が含まれる."""
    monkeypatch.setenv("MISSION_SESSION_ID", TEST_SID)
    _make_state(tmp_path, mission="品質非劣化で bounded context を検証")
    out = tmp_path / "manifest.json"
    args = type("Args", (), {"iteration": 2, "out": str(out)})()
    monkeypatch.chdir(tmp_path)
    MS.cmd_context_manifest(args)
    manifest = json.loads(out.read_text())
    assert manifest["mission_goal"] == "品質非劣化で bounded context を検証"


def test_context_manifest_contains_prior_findings(tmp_path, monkeypatch):
    """score_history の findings が manifest に含まれる."""
    monkeypatch.setenv("MISSION_SESSION_ID", TEST_SID)
    history = [
        {
            "iteration": 1,
            "composite": 3.5,
            "findings_summary": [
                {"id": "F-001", "severity": "High", "title": "型安全性の欠如"},
                {"id": "F-002", "severity": "Medium", "title": "エラーハンドリング不足"},
            ],
        }
    ]
    _make_state(tmp_path, iteration=2, score_history=history)
    out = tmp_path / "manifest.json"
    args = type("Args", (), {"iteration": 2, "out": str(out)})()
    monkeypatch.chdir(tmp_path)
    MS.cmd_context_manifest(args)
    manifest = json.loads(out.read_text())
    assert len(manifest["prior_findings"]) == 2
    assert manifest["prior_findings"][0]["id"] == "F-001"


# --- 2. _derive_next_action includes context_mode ---

def test_next_reviewing_iter2_no_new_scope_bounded(tmp_path):
    """iteration >= 2 + no new scope → context_mode=bounded."""
    _make_state(tmp_path, iteration=2, phase="reviewing",
                critic_has_new_scope=False)
    data = json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text())
    result = MS._derive_next_action(data)
    assert result["details"].get("context_mode") == "bounded"


def test_next_reviewing_iter1_full_context(tmp_path):
    """iteration 1 → context_mode=full."""
    _make_state(tmp_path, iteration=1, phase="reviewing")
    data = json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text())
    result = MS._derive_next_action(data)
    assert result["details"].get("context_mode") == "full"


def test_next_reviewing_new_scope_full_context(tmp_path):
    """critic_has_new_scope=true → context_mode=full."""
    _make_state(tmp_path, iteration=2, phase="reviewing",
                critic_has_new_scope=True)
    data = json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text())
    result = MS._derive_next_action(data)
    assert result["details"].get("context_mode") == "full"


def test_next_reviewing_missing_scope_field_full_context(tmp_path):
    """critic_has_new_scope 未設定 → context_mode=full (安全側)."""
    _make_state(tmp_path, iteration=2, phase="reviewing")
    data = json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text())
    result = MS._derive_next_action(data)
    assert result["details"].get("context_mode") == "full"
