"""Issue #127: mission-state and mission-audit share state helpers."""

from __future__ import annotations

import importlib.util
import sys
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
