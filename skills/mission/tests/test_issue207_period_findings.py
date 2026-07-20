"""Issue #207: audit findings are partitioned into current and historical risk."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT = REPO_ROOT / "scripts" / "mission-audit.py"
FINDING_LIB = REPO_ROOT / "skills" / "mission" / "lib" / "audit_findings.py"


def _write_state(root: Path, session_id: str, updated_at: str, **overrides: object) -> None:
    path = root / session_id / ".mission-state" / "sessions" / f"{session_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, object] = {
        "mission": "period finding fixture",
        "mission_id": f"mission-{session_id}",
        "session_id": session_id,
        "project_root": str(root / session_id),
        "agent": "codex",
        "complexity": "Simple",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "loop_active": False,
        "passes": True,
        "halt_reason": "",
        "started_at": updated_at,
        "updated_at": updated_at,
    }
    state.update(overrides)
    path.write_text(json.dumps(state), encoding="utf-8")


def _audit(root: Path, *extra: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(AUDIT), "--root", str(root), *extra, "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _load_finding_lib():
    mission_lib = str(FINDING_LIB.parent)
    if mission_lib not in sys.path:
        sys.path.insert(0, mission_lib)
    spec = importlib.util.spec_from_file_location("audit_findings", FINDING_LIB)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_audit_module():
    spec = importlib.util.spec_from_file_location("mission_audit_issue207", AUDIT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_period_classifier_handles_boundary_timezone_and_invalid_timestamps():
    findings = _load_finding_lib()
    cutoff = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)

    assert findings.classify_finding_period("2026-07-19T23:59:59.999999Z", cutoff) == "historical"
    assert findings.classify_finding_period("2026-07-20T00:00:00Z", cutoff) == "current"
    assert findings.classify_finding_period("2026-07-20T00:00:00.000001Z", cutoff) == "current"
    assert findings.classify_finding_period("2026-07-20T08:59:59+09:00", cutoff) == "historical"
    assert findings.classify_finding_period("2026-07-20T09:00:00+09:00", cutoff) == "current"
    assert findings.classify_finding_period("2026-07-20T00:00:00", cutoff) == "current"
    assert findings.classify_finding_period("not-a-timestamp", cutoff) == "current"


def test_shared_cutoff_parser_handles_date_offset_boundary_and_upper_bound():
    findings = _load_finding_lib()

    assert findings.parse_audit_cutoff("2026-07-20").isoformat() == "2026-07-20T00:00:00+00:00"
    assert findings.parse_audit_cutoff("2026-07-20", upper=True).isoformat() == "2026-07-20T23:59:59.999999+00:00"
    assert findings.parse_audit_cutoff("2026-07-20T09:00:00+09:00").isoformat() == "2026-07-20T00:00:00+00:00"
    cutoff = findings.parse_audit_cutoff("2026-07-20T00:00:00Z")
    assert findings.classify_finding_period("2026-07-19T23:59:59.999999Z", cutoff) == "historical"
    assert findings.classify_finding_period("2026-07-20T00:00:00Z", cutoff) == "current"
    assert findings.classify_finding_period("2026-07-20T00:00:00.000001Z", cutoff) == "current"


def test_forced_pass_and_specialist_provenance_share_period_partition(tmp_path: Path):
    _write_state(
        tmp_path,
        "old-force",
        "2026-07-19T23:59:59Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    _write_state(
        tmp_path,
        "new-force",
        "2026-07-20T00:00:00Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    for session_id, updated_at in (
        ("old-gap", "2026-07-19T14:59:59-09:00"),
        ("new-gap", "2026-07-20T09:00:01+09:00"),
    ):
        _write_state(
            tmp_path,
            session_id,
            updated_at,
            passes=False,
            halt_reason="provider result absent",
            specialists_selected=[{"skill": "review-provider", "required": True}],
            specialist_invocations=[],
        )

    data = _audit(tmp_path, "--current-since", "2026-07-20T00:00:00Z")
    current = data["current_findings"]
    historical = data["historical_findings"]

    assert {item["session_id"] for item in current if item["code"] == "forced-pass"} == {"new-force"}
    assert {item["session_id"] for item in historical if item["code"] == "forced-pass"} == {"old-force"}
    assert {item["session_id"] for item in current if item["code"] == "specialist-invocation-gap"} == {"new-gap"}
    assert {item["session_id"] for item in historical if item["code"] == "specialist-invocation-gap"} == {"old-gap"}
    assert any(item["code"] == "forced-pass-autonomous-suspect" and item["priority"] == "P0" for item in current)
    assert any(item["code"] == "forced-pass-autonomous-suspect" and item["priority"] == "P0" for item in historical)


def test_json_period_counts_preserve_all_findings(tmp_path: Path):
    _write_state(
        tmp_path,
        "old-force",
        "2026-07-19T23:59:59Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    _write_state(
        tmp_path,
        "boundary-force",
        "2026-07-20T00:00:00Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    data = _audit(tmp_path, "--current-since", "2026-07-20T00:00:00Z")

    assert len(data["current_findings"]) + len(data["historical_findings"]) == len(data["all_findings"])
    assert data["current_finding_counts"]["total"] + data["historical_finding_counts"]["total"] == data["all_finding_counts"]["total"]
    for priority in ("P0", "P1", "P2"):
        assert data["current_finding_counts"][priority] + data["historical_finding_counts"][priority] == data["all_finding_counts"][priority]
    assert set(data["current_findings_by_code"]) | set(data["historical_findings_by_code"]) == set(data["all_findings_by_code"])
    for code, total in data["all_finding_code_counts"].items():
        assert data["current_finding_code_counts"].get(code, 0) + data["historical_finding_code_counts"].get(code, 0) == total
        index = data["all_findings_by_code"][code]
        assert index["count"] == total
        assert len(index["indexes"]) == total


def test_markdown_lists_current_before_historical_and_keeps_severity(tmp_path: Path):
    _write_state(
        tmp_path,
        "old-force",
        "2026-07-19T23:59:59Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    _write_state(
        tmp_path,
        "new-force",
        "2026-07-20T00:00:00Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT),
            "--root",
            str(tmp_path),
            "--current-since",
            "2026-07-20T00:00:00Z",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.index("## Current Findings") < result.stdout.index("## Historical Risk")
    assert "- P0: 1" in result.stdout
    assert "`new-force`" in result.stdout
    assert "`old-force`" in result.stdout
    assert "P0 `forced-pass-autonomous-suspect`" in result.stdout


def test_missing_cutoff_keeps_legacy_all_current_behavior(tmp_path: Path):
    _write_state(
        tmp_path,
        "legacy-force",
        "2026-01-01T00:00:00Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    data = _audit(tmp_path)

    assert data["current_since"] is None
    assert data["historical_findings"] == []
    assert data["current_findings"] == data["all_findings"]
    assert data["current_finding_counts"] == data["all_finding_counts"]
    assert any(item["code"] == "forced-pass" for item in data["findings"])


def test_historical_risk_does_not_enter_current_improvement_prompt(tmp_path: Path):
    _write_state(
        tmp_path,
        "old-force",
        "2026-07-19T23:59:59Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT),
            "--root",
            str(tmp_path),
            "--current-since",
            "2026-07-20T00:00:00Z",
            "--self-improvement-prompt",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert "forced-pass" not in result.stdout
    assert "historical-fixed-debt" not in result.stdout


def test_markdown_historical_summary_is_derived_from_generic_findings(tmp_path: Path):
    _write_state(
        tmp_path,
        "old-force",
        "2026-07-19T23:59:59Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(AUDIT),
            "--root",
            str(tmp_path),
            "--current-since",
            "2026-07-20T00:00:00Z",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    historical = result.stdout[result.stdout.index("## Historical Risks"):]
    assert "- P0: 1" in historical
    assert "- P1: 1" in historical
    assert "`forced-pass` (P1): 1" in historical
    assert "`forced-pass-autonomous-suspect` (P0): 1" in historical
    assert "## Historical Audit Debt" not in result.stdout


def test_checkpoint_and_unselected_provenance_use_updated_at_cutoff(tmp_path: Path):
    for session_id, updated_at in (
        ("before", "2026-07-19T23:59:59.999999Z"),
        ("equal", "2026-07-20T00:00:00Z"),
        ("after", "2026-07-20T00:00:00.000001Z"),
    ):
        _write_state(
            tmp_path,
            session_id,
            updated_at,
            complexity="Standard",
            created_at_session="2026-07-01T00:00:00Z",
            passes_forced=True,
            force_reason="user approved",
            force_approved_by_user=True,
            specialists_selected=[],
            specialist_invocations=[
                {
                    "role": "review",
                    "skill": "review-provider",
                    "status": "inline-applied",
                    "mode": "codex-inline",
                    "iteration": 1,
                }
            ],
        )

    data = _audit(tmp_path, "--current-since", "2026-07-20T00:00:00Z")
    for code in ("missing-specialist-selection-checkpoint", "unselected-specialist-invocation"):
        assert {item["session_id"] for item in data["current_findings"] if item["code"] == code} == {"equal", "after"}
        assert {item["session_id"] for item in data["historical_findings"] if item["code"] == code} == {"before"}


def test_all_finding_specs_are_registry_wired_and_priority_sorted(tmp_path: Path):
    audit = _load_audit_module()

    assert len(audit.FINDING_SPECS) == 20
    assert all(spec.source_key for spec in audit.FINDING_SPECS.values())
    assert all(spec.source_kind for spec in audit.FINDING_SPECS.values())
    assert all(spec.item_summary for spec in audit.FINDING_SPECS.values())
    assert all(spec.aggregate_summary for spec in audit.FINDING_SPECS.values())

    _write_state(
        tmp_path,
        "force",
        "2026-07-20T00:00:00Z",
        passes_forced=True,
        force_reason="provider unavailable",
    )
    data = _audit(tmp_path, "--current-since", "2026-07-20T00:00:00Z")
    rank = {"P0": 0, "P1": 1, "P2": 2}
    priorities = [rank[item["priority"]] for item in data["current_findings"]]
    assert priorities == sorted(priorities)
    row_priorities = [rank[item["priority"]] for item in data["findings"] if item["priority"] in rank]
    assert row_priorities == sorted(row_priorities)


def test_period_indexes_stay_compact_relative_to_canonical_lists(tmp_path: Path):
    for index in range(8):
        _write_state(
            tmp_path,
            f"force-{index}",
            f"2026-07-20T00:00:{index:02d}Z",
            passes_forced=True,
            force_reason="provider unavailable " + ("x" * 200),
        )
    result = subprocess.run(
        [sys.executable, str(AUDIT), "--root", str(tmp_path), "--current-since", "2026-07-20", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    baseline = {
        key: value
        for key, value in data.items()
        if key not in {
            "all_findings",
            "current_findings",
            "historical_findings",
            "all_finding_counts",
            "current_finding_counts",
            "historical_finding_counts",
            "all_finding_code_counts",
            "current_finding_code_counts",
            "historical_finding_code_counts",
            "all_findings_by_code",
            "current_findings_by_code",
            "historical_findings_by_code",
        }
    }
    compact_size = len(json.dumps(data, ensure_ascii=False))
    baseline_size = len(json.dumps(baseline, ensure_ascii=False))
    assert compact_size < baseline_size * 2.5
    assert len(json.dumps(data["all_findings_by_code"])) < len(json.dumps(data["all_findings"]))
