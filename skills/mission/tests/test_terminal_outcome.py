"""#380: role-aware terminal outcome taxonomy and audit population contracts."""

from __future__ import annotations

import importlib.util
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest


def _load_common():
    path = Path(__file__).resolve().parents[1] / "lib" / "mission_common.py"
    spec = importlib.util.spec_from_file_location("mission_common_terminal_outcome", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MC = _load_common()


def _halted(*, role="implementer", category="other", reason="terminal stop"):
    return {
        "passes": False,
        "loop_active": False,
        "halt_reason": reason,
        "halt_category": category,
        "session_role": role,
    }


def _passed(*, role="implementer"):
    return {
        "passes": True,
        "loop_active": False,
        "halt_reason": "",
        "session_role": role,
    }


def _state_file(root: Path) -> Path:
    return next((root / ".mission-state" / "sessions").glob("*.json"))


def _write_audit_state(root: Path, name: str, state: dict) -> None:
    project = root / name
    state_dir = project / ".mission-state" / "sessions"
    state_dir.mkdir(parents=True)
    payload = {
        "mission": f"fixture {name}",
        "mission_id": name,
        "session_id": name,
        "project_root": str(project),
        "passes": False,
        "loop_active": False,
        "halt_reason": "",
        "score_history": [],
        "iteration": 0,
        "started_at": "2026-08-09T00:00:00Z",
        "updated_at": "2026-08-09T00:01:00Z",
        **state,
    }
    (state_dir / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_completed_pass_is_derived_from_consistent_terminal_control_state():
    state = {
        "passes": True,
        "loop_active": False,
        "halt_reason": "",
        "session_role": "implementer",
    }

    assert MC.derive_terminal_outcome(state) == "completed_pass"


def test_role_and_halt_category_map_to_exclusive_terminal_outcomes():
    states = [
        _halted(role="checker", category="evidence-submitted"),
        _halted(role="planning", category="evidence-submitted"),
        _halted(role="analyze", category="evidence-submitted"),
        _halted(role="implementer", category="evidence-submitted"),
        _halted(role="release", category="evidence-submitted"),
        _halted(category="partial-done"),
        _halted(category="blocked-external"),
        _halted(category="awaiting-approval"),
        _halted(category="stale"),
        _halted(category="stagnation"),
        _halted(category="other"),
        _halted(category="user-abort"),
        _halted(category="routed-goal"),
        {"passes": False, "loop_active": False, "halt_reason": ""},
        {"passes": False, "loop_active": True, "halt_reason": ""},
    ]

    assert [MC.derive_terminal_outcome(state) for state in states] == [
        "completed_evidence",
        "completed_evidence",
        "completed_evidence",
        "incomplete",
        "incomplete",
        "incomplete",
        "blocked_external",
        "awaiting_approval",
        "stale_superseded",
        "failed",
        "failed",
        "user_aborted",
        "routed_elsewhere",
        "incomplete",
        None,
    ]


def test_explicit_outcome_and_control_state_conflicts_fail_closed():
    states = [
        {
            "terminal_outcome": "completed_pass",
            "passes": False,
            "loop_active": False,
            "halt_reason": "",
        },
        {
            "terminal_outcome": "completed_evidence",
            "passes": False,
            "loop_active": True,
            "halt_reason": "",
            "session_role": "checker",
        },
        {
            "terminal_outcome": "routed_elsewhere",
            **_halted(category="partial-done"),
        },
        {
            "terminal_outcome": "unrecognized",
            "passes": True,
            "loop_active": False,
            "halt_reason": "",
        },
        {
            "passes": True,
            "loop_active": True,
            "halt_reason": "",
        },
        {
            "passes": True,
            "loop_active": False,
            "halt_reason": "conflicting halt",
            "halt_category": "other",
        },
    ]

    assert [MC.derive_terminal_outcome(state) for state in states] == ["failed"] * 6


def test_explicit_outcome_conflict_is_classified_as_halt_not_active_or_pass():
    conflicting_active = {
        "terminal_outcome": "completed_evidence",
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "session_role": "checker",
    }
    conflicting_pass = {
        "terminal_outcome": "failed",
        "passes": True,
        "loop_active": False,
        "halt_reason": "",
    }

    assert MC.classify_state(conflicting_active) == "halt"
    assert MC.classify_state(conflicting_pass) == "halt"


@pytest.mark.parametrize("invalid_category", [None, True, 1, 1.5, [], {}])
def test_non_string_halt_category_fails_closed_without_breaking_conservation(
    invalid_category, run_cli, tmp_path
):
    state = _halted(category=invalid_category)
    _write_audit_state(tmp_path, "invalid-category", state)

    outcome = MC.derive_terminal_outcome(state)
    summary = MC.summarize_pass_rate_population([state], stale_after_sec=10_800)
    stats_result = run_cli(
        "stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True
    )
    stats = json.loads(stats_result.stdout)
    audit = Path(__file__).resolve().parents[3] / "scripts" / "mission-audit.py"
    audit_result = subprocess.run(
        [sys.executable, str(audit), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    audit_data = json.loads(audit_result.stdout)

    assert {
        "outcome": outcome,
        "summary_failed": summary["terminal_outcome_counts"]["failed"],
        "summary_conservation": sum(summary["terminal_outcome_counts"].values()),
        "stats_failed": stats["terminal_outcome_counts"]["failed"],
        "stats_conservation": sum(stats["terminal_outcome_counts"].values()),
        "audit_failed": audit_data["terminal_outcome_counts"]["failed"],
        "audit_conservation": sum(audit_data["terminal_outcome_counts"].values()),
    } == {
        "outcome": "failed",
        "summary_failed": 1,
        "summary_conservation": 1,
        "stats_failed": 1,
        "stats_conservation": 1,
        "audit_failed": 1,
        "audit_conservation": 1,
    }


def test_stats_normalizes_every_json_halt_category_to_string_buckets(
    run_cli, tmp_path
):
    cases = [
        ("null", None, "null"),
        ("false", False, "false"),
        ("true", True, "true"),
        ("zero", 0, "0"),
        ("integer", 7, "7"),
        ("float", 1.5, "1.5"),
        ("empty-list", [], "[]"),
        ("truthy-list", [1], "[1]"),
        ("empty-map", {}, "{}"),
        ("truthy-map", {"x": 1}, '{"x":1}'),
        ("string", "stagnation", "stagnation"),
    ]
    for name, category, _bucket in cases:
        _write_audit_state(tmp_path, name, _halted(category=category))

    json_result = run_cli(
        "stats", "--root", str(tmp_path), "--json", cwd=tmp_path
    )
    text_result = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path)
    data = json.loads(json_result.stdout) if json_result.returncode == 0 else {}
    expected_buckets = {bucket: 1 for _name, _category, bucket in cases}

    assert {
        "json_returncode": json_result.returncode,
        "text_returncode": text_result.returncode,
        "buckets": data.get("by_halt_category"),
        "terminal_count": data.get("terminal_count"),
        "conservation": sum((data.get("terminal_outcome_counts") or {}).values()),
        "text_buckets": all(
            f"      {bucket:<18} 1" in text_result.stdout for bucket in expected_buckets
        ),
    } == {
        "json_returncode": 0,
        "text_returncode": 0,
        "buckets": expected_buckets,
        "terminal_count": len(cases),
        "conservation": len(cases),
        "text_buckets": True,
    }


def test_legacy_states_are_derived_without_physical_rewrite():
    states = [
        {"schema_version": 1, "passes": True, "loop_active": False, "halt_reason": ""},
        {
            "schema_version": 1,
            "passes": False,
            "loop_active": False,
            "halt_reason": "orphan: prior process ended",
        },
        {"schema_version": 2, "passes": False, "loop_active": False, "halt_reason": ""},
        {
            "schema_version": 2,
            "passes": False,
            "loop_active": False,
            "halt_reason": "threshold was not met",
        },
        {
            "schema_version": 2,
            "passes": False,
            "loop_active": False,
            "halt_reason": "older run",
            "resolution_status": "superseded",
        },
        {
            "schema_version": 2,
            "passes": False,
            "loop_active": False,
            "halt_reason": "superseded by a replacement run",
        },
    ]
    before = copy.deepcopy(states)

    outcomes = [MC.derive_terminal_outcome(state) for state in states]

    assert (outcomes, states) == (
        [
            "completed_pass",
            "stale_superseded",
            "incomplete",
            "failed",
            "stale_superseded",
            "stale_superseded",
        ],
        before,
    )


@pytest.mark.parametrize("schema_version", [2, 3])
@pytest.mark.parametrize(
    ("resolution_status", "category", "expected"),
    [
        ("resolved", "blocked-external", "blocked_external"),
        ("closed", "partial-done", "incomplete"),
        ("superseded", "blocked-external", "stale_superseded"),
    ],
)
def test_resolution_status_preserves_category_outcome_across_v2_and_v3(
    schema_version, resolution_status, category, expected
):
    state = {
        "schema_version": schema_version,
        "resolution_status": resolution_status,
        **_halted(category=category),
    }
    if schema_version == 3:
        state["terminal_outcome"] = expected

    assert MC.derive_terminal_outcome(state) == expected


@pytest.mark.parametrize("schema_version", [2, 3])
@pytest.mark.parametrize(
    ("resolution_status", "expected_outcome", "expected_denominator"),
    [
        ("resolved", "failed", 1),
        ("closed", "failed", 1),
        ("superseded", "stale_superseded", 0),
    ],
)
def test_resolve_archive_writer_keeps_explicit_outcome_and_stats_consistent(
    schema_version,
    resolution_status,
    expected_outcome,
    expected_denominator,
    run_cli,
    tmp_path,
):
    project = tmp_path / "project"
    state = {
        "schema_version": schema_version,
        **_halted(category="stagnation"),
    }
    if schema_version == 3:
        state["terminal_outcome"] = "failed"
    _write_audit_state(tmp_path, "project", state)
    state_path = _state_file(project)

    resolved = run_cli(
        "resolve-archive",
        "--path",
        str(state_path),
        "--status",
        resolution_status,
        "--json",
        cwd=project,
    )
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    stats_result = run_cli(
        "stats", "--root", str(project), "--json", cwd=project, check=True
    )
    stats = json.loads(stats_result.stdout)

    assert {
        "resolve_returncode": resolved.returncode,
        "resolution_status": persisted.get("resolution_status"),
        "derived_outcome": MC.derive_terminal_outcome(persisted),
        "persisted_outcome": persisted.get("terminal_outcome", "<absent>"),
        "outcome_count": stats["terminal_outcome_counts"][expected_outcome],
        "terminal_count": stats["terminal_count"],
        "conservation": sum(stats["terminal_outcome_counts"].values()),
        "implementer_denominator": stats["implementer_pass_rate_denominator"],
    } == {
        "resolve_returncode": 0,
        "resolution_status": resolution_status,
        "derived_outcome": expected_outcome,
        "persisted_outcome": expected_outcome if schema_version == 3 else "<absent>",
        "outcome_count": 1,
        "terminal_count": 1,
        "conservation": 1,
        "implementer_denominator": expected_denominator,
    }


def test_role_mixture_and_31_evidence_records_preserve_implementer_rate_and_conservation():
    implementers = [
        _passed(),
        _passed(),
        _halted(category="stagnation"),
        _halted(category="partial-done"),
    ]
    evidence_roles = ("checker", "planning", "analyze")
    evidence = [
        _halted(role=evidence_roles[index % len(evidence_roles)], category="evidence-submitted")
        for index in range(31)
    ]
    mixed = implementers + evidence + [
        _passed(role="release"),
        {"passes": False, "loop_active": True, "halt_reason": "", "session_role": "checker"},
        _halted(category="routed-goal"),
        _halted(category="blocked-external"),
        _halted(category="awaiting-approval"),
        _halted(category="stale"),
        _halted(category="user-abort"),
    ]

    base = MC.summarize_pass_rate_population(implementers, stale_after_sec=10_800)
    result = MC.summarize_pass_rate_population(mixed, stale_after_sec=10_800)

    assert {
        "base_rate": base["implementer_pass_rate"],
        "mixed_rate": result["implementer_pass_rate"],
        "numerator": result["implementer_pass_rate_numerator"],
        "denominator": result["implementer_pass_rate_denominator"],
        "evidence_numerator": result["evidence_completion_rate_numerator"],
        "evidence_denominator": result["evidence_completion_rate_denominator"],
        "evidence_rate": result["evidence_completion_rate"],
        "terminal_count": result["terminal_count"],
        "outcome_sum": sum(result["terminal_outcome_counts"].values()),
        "active_without_outcome": result["non_terminal_count"],
        "outcomes": result["terminal_outcome_counts"],
    } == {
        "base_rate": 0.5,
        "mixed_rate": 0.5,
        "numerator": 2,
        "denominator": 4,
        "evidence_numerator": 31,
        "evidence_denominator": 31,
        "evidence_rate": 1.0,
        "terminal_count": 41,
        "outcome_sum": 41,
        "active_without_outcome": 1,
        "outcomes": {
            "completed_pass": 3,
            "completed_evidence": 31,
            "blocked_external": 1,
            "awaiting_approval": 1,
            "stale_superseded": 1,
            "failed": 1,
            "incomplete": 1,
            "user_aborted": 1,
            "routed_elsewhere": 1,
        },
    }


def test_init_schema_and_primary_terminal_writers_persist_explicit_outcomes(run_cli, tmp_path, push_provenance_score):
    pass_root = tmp_path / "pass"
    pass_root.mkdir()
    run_cli(
        "init", "implement change", "--complexity", "Standard",
        "--artifact-applicability", "not-applicable",
        cwd=pass_root, check=True,
    )
    initial = json.loads(_state_file(pass_root).read_text(encoding="utf-8"))
    push_provenance_score(pass_root)
    state = json.loads(_state_file(pass_root).read_text(encoding="utf-8"))
    state["task_profile"] = {"primary": "test"}
    state["specialists_decision"] = {"policy": "fallback", "action": "continue-core"}
    _state_file(pass_root).write_text(json.dumps(state), encoding="utf-8")
    run_cli("mark-passes", cwd=pass_root, check=True)
    passed = json.loads(_state_file(pass_root).read_text(encoding="utf-8"))

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    run_cli(
        "init",
        "review change",
        "--complexity",
        "Standard",
        "--role",
        "checker",
        cwd=evidence_root,
        check=True,
    )
    run_cli(
        "mark-halt",
        "--reason",
        "evidence complete",
        "--category",
        "evidence-submitted",
        cwd=evidence_root,
        check=True,
    )
    evidence = json.loads(_state_file(evidence_root).read_text(encoding="utf-8"))

    assert {
        "schema": initial["schema_version"],
        "initial_has_outcome": "terminal_outcome" in initial,
        "passed": passed["terminal_outcome"],
        "evidence": evidence["terminal_outcome"],
    } == {
        "schema": 4,
        "initial_has_outcome": False,
        "passed": "completed_pass",
        "evidence": "completed_evidence",
    }


def test_routing_writes_and_reactivation_paths_clear_terminal_outcome(run_cli, tmp_path):
    routed_root = tmp_path / "routed"
    routed_root.mkdir()
    run_cli("init", "fix one typo", cwd=routed_root, check=True)
    run_cli("set", "complexity=Simple", cwd=routed_root, check=True)
    routed = json.loads(_state_file(routed_root).read_text(encoding="utf-8"))

    manual_root = tmp_path / "manual"
    manual_root.mkdir()
    run_cli("init", "continue later", "--complexity", "Standard", cwd=manual_root, check=True)
    run_cli(
        "mark-halt",
        "--reason",
        "bounded handoff",
        "--category",
        "partial-done",
        cwd=manual_root,
        check=True,
    )
    run_cli(
        "reactivate",
        "--approved-by-user",
        "--expected-category",
        "partial-done",
        "--reason",
        "continue the same mission",
        cwd=manual_root,
        check=True,
    )
    reactivated = json.loads(_state_file(manual_root).read_text(encoding="utf-8"))

    stale_root = tmp_path / "stale"
    stale_root.mkdir()
    run_cli("init", "resume orphan", "--complexity", "Standard", cwd=stale_root, check=True)
    run_cli(
        "mark-halt",
        "--reason",
        "orphan: prior owner ended",
        "--category",
        "stale",
        cwd=stale_root,
        check=True,
    )
    run_cli("refresh-pid", "--force", cwd=stale_root, check=True)
    refreshed = json.loads(_state_file(stale_root).read_text(encoding="utf-8"))

    assert {
        "routed": routed.get("terminal_outcome"),
        "reactivated_has_outcome": "terminal_outcome" in reactivated,
        "reactivated_active": reactivated["loop_active"],
        "refreshed_has_outcome": "terminal_outcome" in refreshed,
        "refreshed_active": refreshed["loop_active"],
    } == {
        "routed": "routed_elsewhere",
        "reactivated_has_outcome": False,
        "reactivated_active": True,
        "refreshed_has_outcome": False,
        "refreshed_active": True,
    }


def test_batch_halt_and_cleanup_stale_persist_terminal_outcomes(run_cli, tmp_path):
    batch_root = tmp_path / "batch"
    batch_root.mkdir()
    run_cli("init", "batch target", "--complexity", "Standard", cwd=batch_root, check=True)
    run_cli(
        "halt",
        "--all",
        "--root",
        str(batch_root),
        "--reason",
        "external dependency unavailable",
        "--category",
        "blocked-external",
        cwd=batch_root,
        check=True,
    )
    batch = json.loads(_state_file(batch_root).read_text(encoding="utf-8"))

    stale_root = tmp_path / "janitor"
    stale_root.mkdir()
    run_cli("init", "janitor target", "--complexity", "Standard", cwd=stale_root, check=True)
    stale_path = _state_file(stale_root)
    stale = json.loads(stale_path.read_text(encoding="utf-8"))
    stale["pid"] = 99_999_999
    stale["pid_source"] = "agent"
    for key in ("owner_session_id", "lease_id", "fencing_epoch", "lease_expires_at"):
        stale.pop(key, None)
    stale_path.write_text(json.dumps(stale), encoding="utf-8")
    run_cli(
        "cleanup-stale",
        "--root",
        str(stale_root),
        "--execute",
        cwd=stale_root,
        check=True,
    )
    cleaned = json.loads(stale_path.read_text(encoding="utf-8"))

    assert {
        "batch": batch.get("terminal_outcome"),
        "cleaned": cleaned.get("terminal_outcome"),
    } == {
        "batch": "blocked_external",
        "cleaned": "stale_superseded",
    }


def test_generic_set_cannot_forge_terminal_outcome(run_cli, tmp_path):
    run_cli("init", "active mission", "--complexity", "Standard", cwd=tmp_path, check=True)

    result = run_cli("set", 'terminal_outcome="completed_pass"', cwd=tmp_path)
    state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))

    assert (result.returncode, "terminal_outcome" in state) == (2, False)


def test_session_role_is_init_only_and_failed_implementer_stays_in_rate_population(
    run_cli, tmp_path
):
    run_cli("init", "fixed role", "--complexity", "Standard", cwd=tmp_path, check=True)

    active_result = run_cli("set", "session_role=release", cwd=tmp_path)
    active_state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    run_cli(
        "mark-halt",
        "--reason",
        "quality gate failed",
        "--category",
        "stagnation",
        cwd=tmp_path,
        check=True,
    )
    terminal_result = run_cli("set", "session_role=release", cwd=tmp_path)
    terminal_state = json.loads(_state_file(tmp_path).read_text(encoding="utf-8"))
    summary = MC.summarize_pass_rate_population(
        [terminal_state], stale_after_sec=10_800
    )

    assert {
        "active_returncode": active_result.returncode,
        "active_role": active_state["session_role"],
        "terminal_returncode": terminal_result.returncode,
        "terminal_role": terminal_state["session_role"],
        "denominator": summary["implementer_pass_rate_denominator"],
        "rate": summary["implementer_pass_rate"],
    } == {
        "active_returncode": 2,
        "active_role": "implementer",
        "terminal_returncode": 2,
        "terminal_role": "implementer",
        "denominator": 1,
        "rate": 0.0,
    }


def test_audit_uses_role_aware_rate_and_excludes_31_evidence_halts(tmp_path):
    _write_audit_state(tmp_path, "pass-1", _passed())
    _write_audit_state(tmp_path, "pass-2", _passed())
    _write_audit_state(tmp_path, "failed", _halted(category="stagnation"))
    _write_audit_state(tmp_path, "partial", _halted(category="partial-done"))
    evidence_roles = ("checker", "planning", "analyze")
    for index in range(31):
        _write_audit_state(
            tmp_path,
            f"evidence-{index:02d}",
            _halted(
                role=evidence_roles[index % len(evidence_roles)],
                category="evidence-submitted",
            ),
        )

    audit = Path(__file__).resolve().parents[3] / "scripts" / "mission-audit.py"
    result = subprocess.run(
        [
            sys.executable,
            str(audit),
            "--root",
            str(tmp_path),
            "--min-pass-rate",
            "0.9",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)

    assert {
        "actionable_halts": data["actionable_halt_count"],
        "non_actionable_halts": data["non_actionable_halt_count"],
        "rate_numerator": data["actionable_pass_rate_numerator"],
        "rate_denominator": data["actionable_pass_rate_denominator"],
        "rate": data["actionable_pass_rate"],
        "evidence_numerator": data["evidence_completion_rate_numerator"],
        "evidence_denominator": data["evidence_completion_rate_denominator"],
        "low_pass_count": data["all_finding_code_counts"].get("low-pass-rate", 0),
        "outcome_conservation": sum(data["terminal_outcome_counts"].values()),
    } == {
        "actionable_halts": 2,
        "non_actionable_halts": 31,
        "rate_numerator": 2,
        "rate_denominator": 4,
        "rate": 0.5,
        "evidence_numerator": 31,
        "evidence_denominator": 31,
        "low_pass_count": 1,
        "outcome_conservation": 35,
    }


def test_stats_exposes_role_aware_rates_and_terminal_conservation(tmp_path, run_cli):
    _write_audit_state(tmp_path, "pass-1", _passed())
    _write_audit_state(tmp_path, "pass-2", _passed())
    _write_audit_state(tmp_path, "failed", _halted(category="stagnation"))
    evidence_roles = ("checker", "planning", "analyze")
    for index in range(31):
        _write_audit_state(
            tmp_path,
            f"evidence-{index:02d}",
            _halted(
                role=evidence_roles[index % len(evidence_roles)],
                category="evidence-submitted",
            ),
        )
    _write_audit_state(
        tmp_path,
        "active",
        {"passes": False, "loop_active": True, "halt_reason": "", "session_role": "checker"},
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True)
    data = json.loads(result.stdout)

    assert {
        "implementer": (
            data["implementer_pass_rate_numerator"],
            data["implementer_pass_rate_denominator"],
            data["implementer_pass_rate"],
        ),
        "evidence": (
            data["evidence_completion_rate_numerator"],
            data["evidence_completion_rate_denominator"],
            data["evidence_completion_rate"],
        ),
        "terminal": data["terminal_count"],
        "non_terminal": data["non_terminal_count"],
        "conserved": sum(data["terminal_outcome_counts"].values()),
    } == {
        "implementer": (2, 3, 2 / 3),
        "evidence": (31, 31, 1.0),
        "terminal": 34,
        "non_terminal": 1,
        "conserved": 34,
    }


def test_stats_text_reports_role_rates_and_terminal_outcomes(tmp_path, run_cli):
    _write_audit_state(tmp_path, "passed", _passed())
    _write_audit_state(
        tmp_path,
        "evidence",
        _halted(role="checker", category="evidence-submitted"),
    )

    result = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path, check=True)

    assert all(
        marker in result.stdout
        for marker in (
            "implementer_pass_rate:",
            "evidence_completion_rate:",
            "terminal_outcomes:",
            "completed_pass",
            "completed_evidence",
        )
    )
