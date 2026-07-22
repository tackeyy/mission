"""Issue #240: diff-reviewer 独立2名の state-driven 強制.

Contract under test:
1. iteration >= 2 + critic_has_new_scope=false → next が reviewer_count=2 を返す
2. iteration >= 2 + critic_has_new_scope=true → next が full reviewer_count を返す
3. iteration == 1 → critic_has_new_scope に関わらず full reviewer_count を返す
4. aggregate-reviews が期待 reviewer 数と実 reviewer 数を照合 (不足で exit 2)
5. critic_has_new_scope は set で更新可能
"""

import json
import os
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

TEST_SID = "test-240"


def _make_state(tmp_path, *, iteration=1, phase="reviewing", reviewer_count=3,
                critic_has_new_scope=None, **extra):
    state_dir = tmp_path / ".mission-state"
    sessions_dir = state_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sf = sessions_dir / f"{TEST_SID}.json"
    data = {
        "mission": "test mission",
        "mission_id": "test1234abcdef",
        "pid": 12345,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "phase": phase,
        "iteration": iteration,
        "reviewer_count": reviewer_count,
        "project_root": str(tmp_path),
    }
    if critic_has_new_scope is not None:
        data["critic_has_new_scope"] = critic_has_new_scope
    data.update(extra)
    sf.write_text(json.dumps(data))
    return sf


# --- 1. next: iter >= 2, no new scope → reviewer_count=2 ---

def test_next_iter2_no_new_scope_returns_2_reviewers(tmp_path):
    """iteration >= 2 + critic_has_new_scope=false → reviewer_count=2."""
    _make_state(tmp_path, iteration=2, phase="reviewing",
                reviewer_count=3, critic_has_new_scope=False)
    result = MS._derive_next_action(json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text()))
    assert result["details"]["reviewer_count"] == 2


# --- 2. next: iter >= 2, has new scope → full reviewer_count ---

def test_next_iter2_new_scope_returns_full_reviewers(tmp_path):
    """iteration >= 2 + critic_has_new_scope=true → full reviewer_count."""
    _make_state(tmp_path, iteration=2, phase="reviewing",
                reviewer_count=3, critic_has_new_scope=True)
    result = MS._derive_next_action(json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text()))
    assert result["details"]["reviewer_count"] == 3


# --- 3. next: iter 1 → always full reviewer_count ---

def test_next_iter1_ignores_new_scope_flag(tmp_path):
    """iteration == 1 → critic_has_new_scope に関わらず full reviewer_count."""
    _make_state(tmp_path, iteration=1, phase="reviewing",
                reviewer_count=3, critic_has_new_scope=False)
    result = MS._derive_next_action(json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text()))
    assert result["details"]["reviewer_count"] == 3


# --- 4. next: iter >= 2, no critic_has_new_scope field → full ---

def test_next_iter2_missing_field_returns_full_reviewers(tmp_path):
    """critic_has_new_scope フィールドなし → full reviewer_count (安全側)."""
    _make_state(tmp_path, iteration=2, phase="reviewing", reviewer_count=3)
    result = MS._derive_next_action(json.loads(
        (tmp_path / ".mission-state" / "sessions" / f"{TEST_SID}.json").read_text()))
    assert result["details"]["reviewer_count"] == 3


# --- 5. aggregate-reviews: reviewer count mismatch → exit 2 ---

def _write_review_json(path, perspective="A", iteration=1, scores=None):
    review = {
        "schema": "mission-review/1",
        "perspective": perspective,
        "iteration": iteration,
        "scores": scores or {
            "mission_achievement": 3.0,
            "accuracy": 3.5,
            "completeness": 3.0,
            "usability": 4.0,
        },
        "findings": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review))
    return path


def test_aggregate_reviews_rejects_fewer_than_min_reviewers(tmp_path, monkeypatch):
    """--min-reviewers 3 に対して reviewer 2 名分 → exit 2."""
    monkeypatch.setenv("MISSION_SESSION_ID", TEST_SID)
    _make_state(tmp_path, iteration=2, reviewer_count=3, phase="reviewing")
    r1 = _write_review_json(tmp_path / "r1.json", perspective="A", iteration=2)
    r2 = _write_review_json(tmp_path / "r2.json", perspective="B", iteration=2)
    args = type("Args", (), {
        "iteration": 2,
        "input": [str(r1), str(r2)],
        "out": str(tmp_path / "out.json"),
        "json": True,
        "min_reviewers": 3,
    })()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        MS.cmd_aggregate_reviews(args)
    assert exc_info.value.code == 2


def test_aggregate_reviews_accepts_matching_min_reviewers(tmp_path, monkeypatch):
    """--min-reviewers 2 に対して reviewer 2 名分 → OK."""
    monkeypatch.setenv("MISSION_SESSION_ID", TEST_SID)
    _make_state(tmp_path, iteration=2, reviewer_count=2, phase="reviewing",
                critic_has_new_scope=False)
    r1 = _write_review_json(tmp_path / "r1.json", perspective="A", iteration=2)
    r2 = _write_review_json(tmp_path / "r2.json", perspective="B", iteration=2)
    args = type("Args", (), {
        "iteration": 2,
        "input": [str(r1), str(r2)],
        "out": str(tmp_path / "out.json"),
        "json": True,
        "min_reviewers": 2,
    })()
    monkeypatch.chdir(tmp_path)
    MS.cmd_aggregate_reviews(args)
    output = json.loads((tmp_path / "out.json").read_text())
    assert "items" in output


# --- 6. set critic_has_new_scope ---

def test_set_critic_has_new_scope(tmp_path, monkeypatch):
    """critic_has_new_scope を set で更新できる."""
    monkeypatch.setenv("MISSION_SESSION_ID", TEST_SID)
    sf = _make_state(tmp_path, iteration=2, phase="reviewing")
    args = type("Args", (), {
        "kvs": ["critic_has_new_scope=false"],
        "root": None,
    })()
    monkeypatch.chdir(tmp_path)
    MS.cmd_set(args)
    data = json.loads(sf.read_text())
    assert data["critic_has_new_scope"] is False
