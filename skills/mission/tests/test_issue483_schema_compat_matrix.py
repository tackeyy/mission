"""Issue #483: schema_version compatibility matrix and fail-closed reader guard."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


def _load_common():
    path = Path(__file__).resolve().parents[1] / "lib" / "mission_common.py"
    spec = importlib.util.spec_from_file_location("mission_common_schema_compat", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MC = _load_common()
STALE_AFTER_SEC = 10_800


def _state_path(root: Path) -> Path:
    return root / ".mission-state" / "sessions" / "test.json"


def _write_state(root: Path, state: dict) -> Path:
    state_path = _state_path(root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = copy.deepcopy(state)
    payload["project_root"] = str(root)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return state_path


def _load_json(output: str) -> dict:
    return json.loads(output)


def _summary_view(state: dict) -> dict:
    summary = MC.summarize_pass_rate_population([copy.deepcopy(state)], stale_after_sec=STALE_AFTER_SEC)
    return {
        "raw_pass_rate_numerator": summary["raw_pass_rate_numerator"],
        "raw_pass_rate_denominator": summary["raw_pass_rate_denominator"],
        "raw_pass_rate": summary["raw_pass_rate"],
        "completed_pass_rate_numerator": summary["completed_pass_rate_numerator"],
        "completed_pass_rate_denominator": summary["completed_pass_rate_denominator"],
        "completed_pass_rate": summary["completed_pass_rate"],
        "implementer_pass_rate_numerator": summary["implementer_pass_rate_numerator"],
        "implementer_pass_rate_denominator": summary["implementer_pass_rate_denominator"],
        "implementer_pass_rate": summary["implementer_pass_rate"],
        "terminal_count": summary["terminal_count"],
        "terminal_outcome_counts": summary["terminal_outcome_counts"],
    }


def _stats_view(run_cli, root: Path) -> dict:
    result = run_cli("stats", "--root", str(root), "--json", cwd=root, check=True)
    data = _load_json(result.stdout)
    return {
        "pass_count": data["pass_count"],
        "halt_count": data["halt_count"],
        "incomplete_count": data["incomplete_count"],
        "abandoned_count": data["abandoned_count"],
        "terminal_count": data["terminal_count"],
        "non_terminal_count": data["non_terminal_count"],
        "raw_pass_rate_numerator": data["raw_pass_rate_numerator"],
        "raw_pass_rate_denominator": data["raw_pass_rate_denominator"],
        "raw_pass_rate": data["raw_pass_rate"],
        "completed_pass_rate_numerator": data["completed_pass_rate_numerator"],
        "completed_pass_rate_denominator": data["completed_pass_rate_denominator"],
        "completed_pass_rate": data["completed_pass_rate"],
        "pass_rate": data["pass_rate"],
        "score_provenance_counts": data["score_provenance_counts"],
        "by_halt_category": data["by_halt_category"],
    }


def _raw_view(run_cli, root: Path) -> dict:
    result = run_cli("get", cwd=root, check=True)
    data = _load_json(result.stdout)
    return {
        "schema_version": data.get("schema_version", "<missing>"),
        "terminal_outcome": data.get("terminal_outcome", "<absent>"),
        "passes": data["passes"],
        "loop_active": data["loop_active"],
        "halt_reason": data["halt_reason"],
        "score_history_len": len(data.get("score_history") or []),
        "phase": data.get("phase"),
    }


def _next_action(run_cli, root: Path) -> str:
    result = run_cli("next", cwd=root, check=True)
    return _load_json(result.stdout)["next_action"]


V1_STATE = {
    "mission": "schema compat v1",
    "mission_id": "schema-compat-v1",
    "session_id": "test",
    "project_root": "__ROOT__",
    "passes": True,
    "loop_active": False,
    "halt_reason": "",
    "score_history": [],
    "iteration": 1,
    "started_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-01T00:05:00Z",
    "schema_version": 1,
    "phase": "done",
}

V2_STATE = {
    "mission": "schema compat v2",
    "mission_id": "schema-compat-v2",
    "session_id": "test",
    "project_root": "__ROOT__",
    "passes": False,
    "loop_active": False,
    "halt_reason": "superseded by a replacement run",
    "resolution_status": "superseded",
    "score_history": [],
    "iteration": 1,
    "started_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-01T00:05:00Z",
    "schema_version": 2,
    "phase": "halted",
}

V3_STATE = {
    "mission": "schema compat v3",
    "mission_id": "schema-compat-v3",
    "session_id": "test",
    "project_root": "__ROOT__",
    "passes": False,
    "loop_active": False,
    "halt_reason": "superseded by a replacement run",
    "resolution_status": "superseded",
    "terminal_outcome": "stale_superseded",
    "score_history": [],
    "iteration": 1,
    "started_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-01T00:05:00Z",
    "schema_version": 3,
    "phase": "halted",
}

V4_STATE = {
    "mission": "schema compat v4",
    "mission_id": "schema-compat-v4",
    "session_id": "test",
    "project_root": "__ROOT__",
    "passes": True,
    "loop_active": False,
    "halt_reason": "",
    "score_history": [
        {
            "iteration": 1,
            "composite": 4.5,
            "min_item": 4.0,
            "items": {
                "mission_achievement": 4.5,
                "accuracy": 4.5,
                "completeness": 4.5,
                "usability": 4.5,
            },
            "timestamp": "2026-07-01T00:00:00Z",
            "open_high": 0,
        }
    ],
    "iteration": 1,
    "started_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-01T00:05:00Z",
    "schema_version": 4,
    "phase": "done",
}

MISSING_SCHEMA_STATE = {
    "mission": "schema compat missing",
    "mission_id": "schema-compat-missing",
    "session_id": "test",
    "project_root": "__ROOT__",
    "passes": True,
    "loop_active": False,
    "halt_reason": "",
    "score_history": [],
    "iteration": 1,
    "started_at": "2026-07-01T00:00:00Z",
    "updated_at": "2026-07-01T00:05:00Z",
    "phase": "done",
}


@pytest.mark.parametrize(
    ("label", "state", "expected"),
    [
        (
            "v1",
            V1_STATE,
            {
                "raw": {
                    "schema_version": 1,
                    "terminal_outcome": "<absent>",
                    "passes": True,
                    "loop_active": False,
                    "halt_reason": "",
                    "score_history_len": 0,
                    "phase": "done",
                },
                "derived": {
                    "terminal_outcome": "completed_pass",
                    "classify_state": "pass",
                },
                "summary": {
                    "raw_pass_rate_numerator": 1,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 1.0,
                    "completed_pass_rate_numerator": 1,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 1.0,
                    "implementer_pass_rate_numerator": 1,
                    "implementer_pass_rate_denominator": 1,
                    "implementer_pass_rate": 1.0,
                    "terminal_count": 1,
                    "terminal_outcome_counts": {
                        "completed_pass": 1,
                        "completed_evidence": 0,
                        "blocked_external": 0,
                        "awaiting_approval": 0,
                        "stale_superseded": 0,
                        "failed": 0,
                        "incomplete": 0,
                        "user_aborted": 0,
                        "routed_elsewhere": 0,
                    },
                },
                "stats": {
                    "pass_count": 1,
                    "halt_count": 0,
                    "incomplete_count": 0,
                    "abandoned_count": 0,
                    "terminal_count": 1,
                    "non_terminal_count": 0,
                    "raw_pass_rate_numerator": 1,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 1.0,
                    "completed_pass_rate_numerator": 1,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 1.0,
                    "pass_rate": 1.0,
                    "score_provenance_counts": {
                        "verified": 0,
                        "legacy-unverifiable": 0,
                        "invalid": 0,
                    },
                    "by_halt_category": {},
                },
                "next_action": "report-complete",
            },
        ),
        (
            "v2",
            V2_STATE,
            {
                "raw": {
                    "schema_version": 2,
                    "terminal_outcome": "<absent>",
                    "passes": False,
                    "loop_active": False,
                    "halt_reason": "superseded by a replacement run",
                    "score_history_len": 0,
                    "phase": "halted",
                },
                "derived": {
                    "terminal_outcome": "stale_superseded",
                    "classify_state": "halt",
                },
                "summary": {
                    "raw_pass_rate_numerator": 0,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 0.0,
                    "completed_pass_rate_numerator": 0,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 0.0,
                    "implementer_pass_rate_numerator": 0,
                    "implementer_pass_rate_denominator": 0,
                    "implementer_pass_rate": None,
                    "terminal_count": 1,
                    "terminal_outcome_counts": {
                        "completed_pass": 0,
                        "completed_evidence": 0,
                        "blocked_external": 0,
                        "awaiting_approval": 0,
                        "stale_superseded": 1,
                        "failed": 0,
                        "incomplete": 0,
                        "user_aborted": 0,
                        "routed_elsewhere": 0,
                    },
                },
                "stats": {
                    "pass_count": 0,
                    "halt_count": 1,
                    "incomplete_count": 0,
                    "abandoned_count": 0,
                    "terminal_count": 1,
                    "non_terminal_count": 0,
                    "raw_pass_rate_numerator": 0,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 0.0,
                    "completed_pass_rate_numerator": 0,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 0.0,
                    "pass_rate": 0.0,
                    "score_provenance_counts": {
                        "verified": 0,
                        "legacy-unverifiable": 0,
                        "invalid": 0,
                    },
                    "by_halt_category": {
                        "unknown": 1,
                    },
                },
                "next_action": "report-blocker",
            },
        ),
        (
            "v3",
            V3_STATE,
            {
                "raw": {
                    "schema_version": 3,
                    "terminal_outcome": "stale_superseded",
                    "passes": False,
                    "loop_active": False,
                    "halt_reason": "superseded by a replacement run",
                    "score_history_len": 0,
                    "phase": "halted",
                },
                "derived": {
                    "terminal_outcome": "stale_superseded",
                    "classify_state": "halt",
                },
                "summary": {
                    "raw_pass_rate_numerator": 0,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 0.0,
                    "completed_pass_rate_numerator": 0,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 0.0,
                    "implementer_pass_rate_numerator": 0,
                    "implementer_pass_rate_denominator": 0,
                    "implementer_pass_rate": None,
                    "terminal_count": 1,
                    "terminal_outcome_counts": {
                        "completed_pass": 0,
                        "completed_evidence": 0,
                        "blocked_external": 0,
                        "awaiting_approval": 0,
                        "stale_superseded": 1,
                        "failed": 0,
                        "incomplete": 0,
                        "user_aborted": 0,
                        "routed_elsewhere": 0,
                    },
                },
                "stats": {
                    "pass_count": 0,
                    "halt_count": 1,
                    "incomplete_count": 0,
                    "abandoned_count": 0,
                    "terminal_count": 1,
                    "non_terminal_count": 0,
                    "raw_pass_rate_numerator": 0,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 0.0,
                    "completed_pass_rate_numerator": 0,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 0.0,
                    "pass_rate": 0.0,
                    "score_provenance_counts": {
                        "verified": 0,
                        "legacy-unverifiable": 0,
                        "invalid": 0,
                    },
                    "by_halt_category": {
                        "unknown": 1,
                    },
                },
                "next_action": "report-blocker",
            },
        ),
        (
            "v4",
            V4_STATE,
            {
                "raw": {
                    "schema_version": 4,
                    "terminal_outcome": "<absent>",
                    "passes": True,
                    "loop_active": False,
                    "halt_reason": "",
                    "score_history_len": 1,
                    "phase": "done",
                },
                "derived": {
                    "terminal_outcome": "completed_pass",
                    "classify_state": "pass",
                },
                "summary": {
                    "raw_pass_rate_numerator": 1,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 1.0,
                    "completed_pass_rate_numerator": 1,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 1.0,
                    "implementer_pass_rate_numerator": 1,
                    "implementer_pass_rate_denominator": 1,
                    "implementer_pass_rate": 1.0,
                    "terminal_count": 1,
                    "terminal_outcome_counts": {
                        "completed_pass": 1,
                        "completed_evidence": 0,
                        "blocked_external": 0,
                        "awaiting_approval": 0,
                        "stale_superseded": 0,
                        "failed": 0,
                        "incomplete": 0,
                        "user_aborted": 0,
                        "routed_elsewhere": 0,
                    },
                },
                "stats": {
                    "pass_count": 1,
                    "halt_count": 0,
                    "incomplete_count": 0,
                    "abandoned_count": 0,
                    "terminal_count": 1,
                    "non_terminal_count": 0,
                    "raw_pass_rate_numerator": 1,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 1.0,
                    "completed_pass_rate_numerator": 1,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 1.0,
                    "pass_rate": 1.0,
                    "score_provenance_counts": {
                        "verified": 0,
                        "legacy-unverifiable": 1,
                        "invalid": 0,
                    },
                    "by_halt_category": {},
                },
                "next_action": "report-complete",
            },
        ),
        (
            "missing",
            MISSING_SCHEMA_STATE,
            {
                "raw": {
                    "schema_version": "<missing>",
                    "terminal_outcome": "<absent>",
                    "passes": True,
                    "loop_active": False,
                    "halt_reason": "",
                    "score_history_len": 0,
                    "phase": "done",
                },
                "derived": {
                    "terminal_outcome": "completed_pass",
                    "classify_state": "pass",
                },
                "summary": {
                    "raw_pass_rate_numerator": 1,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 1.0,
                    "completed_pass_rate_numerator": 1,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 1.0,
                    "implementer_pass_rate_numerator": 1,
                    "implementer_pass_rate_denominator": 1,
                    "implementer_pass_rate": 1.0,
                    "terminal_count": 1,
                    "terminal_outcome_counts": {
                        "completed_pass": 1,
                        "completed_evidence": 0,
                        "blocked_external": 0,
                        "awaiting_approval": 0,
                        "stale_superseded": 0,
                        "failed": 0,
                        "incomplete": 0,
                        "user_aborted": 0,
                        "routed_elsewhere": 0,
                    },
                },
                "stats": {
                    "pass_count": 1,
                    "halt_count": 0,
                    "incomplete_count": 0,
                    "abandoned_count": 0,
                    "terminal_count": 1,
                    "non_terminal_count": 0,
                    "raw_pass_rate_numerator": 1,
                    "raw_pass_rate_denominator": 1,
                    "raw_pass_rate": 1.0,
                    "completed_pass_rate_numerator": 1,
                    "completed_pass_rate_denominator": 1,
                    "completed_pass_rate": 1.0,
                    "pass_rate": 1.0,
                    "score_provenance_counts": {
                        "verified": 0,
                        "legacy-unverifiable": 0,
                        "invalid": 0,
                    },
                    "by_halt_category": {},
                },
                "next_action": "report-complete",
            },
        ),
    ],
    ids=["v1", "v2", "v3", "v4", "missing"],
)
def test_schema_compat_golden_snapshots_fix_read_results(
    label, state, expected, run_cli, tmp_path
):
    root = tmp_path / label
    state_path = _write_state(root, state)
    before = state_path.read_bytes()

    raw = _raw_view(run_cli, root)
    summary = _summary_view(state)
    stats = _stats_view(run_cli, root)
    derived = {
        "terminal_outcome": MC.derive_terminal_outcome(state),
        "classify_state": MC.classify_state(state),
    }
    next_action = _next_action(run_cli, root)

    assert {
        "raw": raw,
        "derived": derived,
        "summary": summary,
        "stats": stats,
        "next_action": next_action,
    } == expected
    assert state_path.read_bytes() == before


@pytest.mark.parametrize(
    ("schema_version", "slug"),
    [
        ("string", "string"),
        ("null", "null"),
        ("float", "float"),
        ("bool", "bool"),
        ("future_int", "future-int"),
    ],
    ids=["string", "null", "float", "bool", "future-int"],
)
@pytest.mark.parametrize(
    "command",
    ["get", "next", "set", "stats"],
    ids=["get", "next", "set", "stats"],
)
def test_unsupported_schema_versions_fail_closed_without_writing_state(
    schema_version, slug, command, run_cli, tmp_path
):
    root = tmp_path / f"{command}-{slug}"
    state = copy.deepcopy(V4_STATE)
    if schema_version == "string":
        state["schema_version"] = "5"
    elif schema_version == "null":
        state["schema_version"] = None
    elif schema_version == "float":
        state["schema_version"] = 5.0
    elif schema_version == "bool":
        state["schema_version"] = True
    elif schema_version == "future_int":
        state["schema_version"] = 5
    else:  # pragma: no cover - defensive default for test data mistakes
        raise AssertionError(schema_version)

    state_path = _write_state(root, state)
    before = state_path.read_bytes()

    args = [command]
    if command == "set":
        args.append("compatibility_probe=1")
    if command == "stats":
        args.extend(["--root", str(root), "--json"])

    result = run_cli(*args, cwd=root)

    assert result.returncode != 0
    assert "schema_version" in result.stderr
    assert state_path.read_bytes() == before


def test_schema_version_missing_keeps_legacy_v1_compatibility(run_cli, tmp_path):
    root = tmp_path / "legacy-missing"
    state = copy.deepcopy(MISSING_SCHEMA_STATE)
    state_path = _write_state(root, state)

    assert _raw_view(run_cli, root)["schema_version"] == "<missing>"
    assert _next_action(run_cli, root) == "report-complete"
    assert _stats_view(run_cli, root)["pass_count"] == 1
    before = state_path.read_bytes()
    result = run_cli("set", "compatibility_probe=1", cwd=root)

    assert result.returncode == 0, result.stderr
    assert state_path.read_bytes() != before
