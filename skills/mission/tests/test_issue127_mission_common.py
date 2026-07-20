"""Issue #127: mission-state and mission-audit share state helpers."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
MISSION_AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_state_and_audit_classification_match():
    state_mod = _load(MISSION_STATE_PY, "mission_state_issue127")
    audit_mod = _load(MISSION_AUDIT_PY, "mission_audit_issue127")
    states = [
        {"passes": True, "loop_active": False, "halt_reason": ""},
        {"passes": False, "loop_active": False, "halt_reason": "max-iter reached"},
        {"passes": False, "loop_active": False, "halt_reason": ""},
        {"passes": False, "loop_active": True, "halt_reason": ""},
    ]

    assert [state_mod._classify(s) for s in states] == [audit_mod.classify(s) for s in states]
    assert [audit_mod.classify(s) for s in states] == ["pass", "halt", "abandoned", "incomplete"]


def test_state_and_audit_duration_match():
    state_mod = _load(MISSION_STATE_PY, "mission_state_issue127_duration")
    audit_mod = _load(MISSION_AUDIT_PY, "mission_audit_issue127_duration")
    state = {
        "started_at": "2026-07-05T00:00:00Z",
        "updated_at": "2026-07-05T00:02:30Z",
    }

    assert state_mod._duration_sec(state) == audit_mod.duration_sec(state) == 150.0


def test_preparation_markers_use_audit_superset():
    state_mod = _load(MISSION_STATE_PY, "mission_state_issue127_markers")
    audit_mod = _load(MISSION_AUDIT_PY, "mission_audit_issue127_markers")

    assert state_mod.PREPARATION_ONLY_MARKERS == audit_mod.PREPARATION_ONLY_MARKERS
    assert "Prompt file:" in state_mod.PREPARATION_ONLY_MARKERS
    assert "Review URL:" in state_mod.PREPARATION_ONLY_MARKERS


def test_shared_pass_rate_health_is_exclusive_and_fails_stale_closed():
    state_mod = _load(MISSION_STATE_PY, "mission_state_issue208_health")
    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    states = [
        {"passes": True, "loop_active": False, "updated_at": "2026-07-21T11:59:00Z"},
        {"passes": False, "loop_active": False, "halt_reason": "blocked", "updated_at": "2026-07-21T11:59:00Z"},
        {"passes": False, "loop_active": False, "halt_reason": "", "updated_at": "2026-07-21T11:59:00Z"},
        {
            "passes": False,
            "loop_active": True,
            "halt_reason": "",
            "score_history": [{"composite": 4.0}],
            "updated_at": "2026-07-21T11:59:00+00:00",
        },
        {
            "passes": False,
            "loop_active": True,
            "halt_reason": "",
            "score_history": [{"composite": float("inf")}],
            "updated_at": "2026-07-21T11:59:00",
        },
        {
            "passes": False,
            "loop_active": True,
            "halt_reason": "",
            "score_history": [],
            "updated_at": "malformed",
        },
    ]

    summary = state_mod.summarize_pass_rate_population(
        states,
        now=now,
        stale_after_sec=3600,
    )

    assert summary["health_classes"] == [
        "pass", "halt", "abandoned", "active", "active-no-score", "stale"
    ]
    assert summary["incomplete_count"] == 3
    assert summary["raw_pass_rate_numerator"] == 1
    assert summary["raw_pass_rate_denominator"] == 6
    assert summary["completed_pass_rate_numerator"] == 1
    assert summary["completed_pass_rate_denominator"] == 4


def test_audit_aggregate_uses_one_observation_time_at_stale_boundary(monkeypatch, tmp_path):
    audit_mod = _load(MISSION_AUDIT_PY, "mission_audit_issue208_boundary")
    before_boundary = datetime(2026, 7, 21, 11, 59, 59, tzinfo=timezone.utc)
    after_boundary = datetime(2026, 7, 21, 12, 0, 1, tzinfo=timezone.utc)
    observed = iter([before_boundary, after_boundary, after_boundary, after_boundary])
    calls = []

    def advancing_now():
        calls.append(True)
        return next(observed)

    monkeypatch.setattr(audit_mod, "utc_now", advancing_now)
    monkeypatch.setenv("MISSION_STALE_ACTIVE_SECONDS", "3600")
    state = {
        "mission": "boundary",
        "mission_id": "boundary-mission",
        "session_id": "boundary-session",
        "project_root": str(tmp_path),
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "score_history": [],
        "started_at": "2026-07-21T11:00:00Z",
        "updated_at": "2026-07-21T11:00:00Z",
        "iteration": 1,
    }
    record = audit_mod.StateRecord(tmp_path / "state.json", state)

    summary = audit_mod.aggregate([record], [], 0, 1800)

    assert len(calls) == 1
    assert summary["active_no_score_count"] == 1
    assert summary["stale_count"] == 0
    assert summary["stale_active_no_score_count"] == 0
    assert summary["completed_pass_rate_denominator"] == 0
