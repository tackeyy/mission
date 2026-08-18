"""Shared mission state fixtures for Issue #500 tests."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_ISSUE483_PATH = _HERE / "test_issue483_schema_compat_matrix.py"
_MISSION_STATE_PY = _HERE.parent / "bin" / "mission-state.py"


def _load_issue483_module():
    spec = importlib.util.spec_from_file_location("mission_issue483_schema_compat_matrix", _ISSUE483_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_ISSUE483 = _load_issue483_module()


def issue483_corpus() -> dict[str, dict]:
    """Return the canonical missing/v1-v4 corpus used by compatibility tests."""
    return {
        "missing": copy.deepcopy(_ISSUE483.MISSING_SCHEMA_STATE),
        "v1": copy.deepcopy(_ISSUE483.V1_STATE),
        "v2": copy.deepcopy(_ISSUE483.V2_STATE),
        "v3": copy.deepcopy(_ISSUE483.V3_STATE),
        "v4": copy.deepcopy(_ISSUE483.V4_STATE),
    }


def canonical_json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _run_cli(
    root: Path,
    *arguments: str,
    lease_id: str = "fixture-lease",
    env_extra: dict[str, str] | None = None,
    cli_path: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run the production CLI with an isolated, deterministic session carrier."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    environment.update(
        {
            "MISSION_SESSION_ID": "test",
            "MISSION_LEASE_ID": lease_id,
        }
    )
    if env_extra:
        environment.update(env_extra)
    return subprocess.run(
        [sys.executable, str(cli_path or _MISSION_STATE_PY), *arguments],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _checked_cli(
    root: Path,
    *arguments: str,
    lease_id: str = "fixture-lease",
    env_extra: dict[str, str] | None = None,
    cli_path: Path | None = None,
) -> subprocess.CompletedProcess:
    result = _run_cli(
        root,
        *arguments,
        lease_id=lease_id,
        env_extra=env_extra,
        cli_path=cli_path,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"CLI fixture command failed: {arguments!r}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


def _run_cli_with_clock(
    root: Path,
    *arguments: str,
    lease_id: str | None,
    now: str,
) -> subprocess.CompletedProcess:
    """Execute the CLI module with only its clock seam fixed for lease takeover."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    environment.update(
        {
            "MISSION_SESSION_ID": "test",
            "ISSUE500_CLI_PATH": str(_MISSION_STATE_PY),
            "ISSUE500_CLI_NOW": now,
            "ISSUE500_CLI_ARGS": json.dumps(list(arguments)),
        }
    )
    if lease_id is not None:
        environment["MISSION_LEASE_ID"] = lease_id
    bootstrap = (
        "import importlib.util,json,os,sys;"
        "p=os.environ['ISSUE500_CLI_PATH'];"
        "s=importlib.util.spec_from_file_location('issue500_cli_clock',p);"
        "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);"
        "m.iso_now=lambda:os.environ['ISSUE500_CLI_NOW'];"
        "sys.argv=[p,*json.loads(os.environ['ISSUE500_CLI_ARGS'])];m.main()"
    )
    return subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _read_cli_state(root: Path) -> dict:
    return json.loads((root / ".mission-state" / "sessions" / "test.json").read_text(encoding="utf-8"))


def _mutate_cli_state_for_fixture(root: Path, **fields: object) -> None:
    """Arrange a historical/derived state without exercising generic ``set``."""
    path = root / ".mission-state" / "sessions" / "test.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(fields)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _init_cli_state(
    root: Path,
    *,
    role: str = "implementer",
    complexity: str = "Standard",
) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    _checked_cli(
        root,
        "init",
        "Issue 500 CLI corpus",
        "--complexity",
        complexity,
        "--role",
        role,
        "--artifact-applicability",
        "not-applicable",
    )
    _materialize_legacy_init_fixture(root, cleanup_v5=True)
    return _read_cli_state(root)


def _materialize_legacy_init_fixture(
    root: Path,
    *,
    session_ids: tuple[str, ...] | None = None,
    cleanup_v5: bool = False,
) -> None:
    """Convert a successful production init into an explicit legacy fixture."""

    # Most pre-C1 fixture consumers intentionally exercise the retained v4
    # repository path.  Production ``init`` now creates a v5 container, so
    # resolve its verified payload and materialize that payload as an explicit
    # legacy fixture before those compatibility scenarios continue.
    from mission_persistence.fenced_commit import LocalFencedRepository
    from mission_persistence.repository_binding import (
        RepositoryFormat,
        RepositorySelectionError,
        inspect_repository_bytes,
    )

    repository_root = root / ".mission-state"
    sessions = repository_root / "sessions"
    if not sessions.is_dir():
        return
    repository = LocalFencedRepository(repository_root)
    converted = False
    candidates = (
        [sessions / (session_id + ".json") for session_id in session_ids]
        if session_ids is not None
        else list(sessions.glob("*.json"))
    )
    for session_path in candidates:
        if not session_path.exists():
            continue
        try:
            inspection = inspect_repository_bytes(
                session_path.read_bytes(), expected_session_id=session_path.stem
            )
        except (OSError, RepositorySelectionError):
            # A successful legacy re-init leaves a v4 document here already;
            # malformed-state tests also intentionally exercise this path.
            continue
        if inspection.format is not RepositoryFormat.V5:
            continue
        state_bytes = repository.read(session_path.stem).state_bytes
        session_path.write_bytes(state_bytes)
        converted = True
    if converted and cleanup_v5:
        # Leave a genuine flat-v4 fixture, not an impossible v4 head with
        # orphaned v5 tombstones that could affect a later init in the case.
        for name in ("commits", "generations", "objects", "operations", "transactions"):
            shutil.rmtree(repository_root / name, ignore_errors=True)


def generate_cli_state_bytes(root: Path, *, role: str = "implementer") -> tuple[Path, bytes]:
    """Return an explicit v4 compatibility fixture using the current init payload."""
    _init_cli_state(root, role=role)
    state_path = root / ".mission-state" / "sessions" / "test.json"
    return state_path, state_path.read_bytes()


def _write_core_plan(root: Path) -> Path:
    plan = {
        "objective": "capture the production writer shape",
        "scope": {
            "resources": [],
            "actions": [{"type": "analyze", "effect_class": "reversible"}],
        },
        "assumptions": [
            {
                "id": "fixture-input",
                "statement": "the isolated fixture input exists",
                "validation": "read it before execution",
            }
        ],
        "steps": [
            {
                "id": "inspect",
                "action": "analyze",
                "inputs": [],
                "outputs": ["finding"],
                "depends_on": [],
                "acceptance_checks": ["the state was inspected"],
                "risk": "low",
                "rollback": "none",
            },
            {
                "id": "record",
                "action": "write",
                "inputs": ["finding"],
                "outputs": ["corpus"],
                "depends_on": ["inspect"],
                "acceptance_checks": ["the corpus contains the writer output"],
                "risk": "low",
                "rollback": "remove the isolated fixture directory",
            },
        ],
        "global_acceptance": ["all writer variants are represented"],
        "stop_conditions": ["a production CLI command rejects the fixture"],
    }
    path = root / "plan-input.json"
    path.write_bytes(canonical_json_bytes(plan))
    return path


def _prepare_handoff(root: Path) -> dict:
    _init_cli_state(root)
    source = _write_core_plan(root)
    _checked_cli(root, "planning", "adopt-core", "--input", str(source), "--source-id", "fixture-core")
    _checked_cli(root, "advance", "--phase", "executing")
    return _read_cli_state(root)


def _review_document(perspective: str) -> dict:
    return {
        "schema": "mission-review/1",
        "iteration": 1,
        "perspective": perspective,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.5,
            "completeness": 4.5,
            "usability": 4.5,
        },
        "findings": [],
        "same_score_note": "axis-specific CLI corpus fixture",
    }


def generate_cli_state_corpus(root: Path) -> dict[str, object]:
    """Capture current writer variants exclusively from production CLI state output.

    Input plan/review documents are test inputs. Every returned state snapshot is
    read from ``.mission-state/sessions/test.json`` after the named CLI command.
    """
    corpus: dict[str, object] = {}

    handoff_root = root / "handoff"
    corpus["handoff_prepared"] = copy.deepcopy(_prepare_handoff(handoff_root))
    _checked_cli(handoff_root, "executor-handoff", "begin")
    corpus["handoff_consuming"] = copy.deepcopy(_read_cli_state(handoff_root))
    _checked_cli(handoff_root, "executor-handoff", "record-step", "--step-id", "inspect", "--result", "ok")
    _checked_cli(handoff_root, "executor-handoff", "record-step", "--step-id", "record", "--result", "ok")
    _checked_cli(handoff_root, "executor-handoff", "complete")
    corpus["handoff_consumed"] = copy.deepcopy(_read_cli_state(handoff_root))

    rejected_root = root / "handoff-rejected"
    rejected = _prepare_handoff(rejected_root)
    canonical = rejected_root / rejected["canonical_plan"]["path"]
    canonical.write_bytes(b'{"schema":"mission-plan/1","steps":[]}')
    rejection = _run_cli(rejected_root, "executor-handoff", "begin")
    if rejection.returncode == 0:
        raise AssertionError("CLI fixture did not produce a rejected handoff")
    corpus["handoff_rejected"] = copy.deepcopy(_read_cli_state(rejected_root))

    provider_root = root / "provider-plan"
    provider_root.mkdir(parents=True)
    provider_spec = importlib.util.spec_from_file_location(
        "issue500_provider_fixture",
        _HERE / "test_planning_provider_lifecycle.py",
    )
    provider_module = importlib.util.module_from_spec(provider_spec)
    assert provider_spec.loader is not None
    provider_spec.loader.exec_module(provider_module)

    def provider_cli(*arguments, cwd, check=False, env_extra=None):
        result = _run_cli(Path(cwd), *arguments, env_extra=env_extra)
        if arguments and arguments[0] == "init" and result.returncode == 0:
            _materialize_legacy_init_fixture(Path(cwd))
        if check:
            result.check_returncode()
        return result

    registry, state_file, source, invocation, provider_env = provider_module._provider_import_fixture(
        provider_cli,
        provider_root,
    )
    corpus["provider_result_ready"] = copy.deepcopy(_read_cli_state(provider_root))
    _checked_cli(
        provider_root,
        "specialists",
        "plan-import",
        "--input",
        str(source),
        "--invocation-id",
        invocation,
        "--registry",
        str(registry),
        env_extra=provider_env,
    )
    corpus["provider_plan_imported"] = copy.deepcopy(_read_cli_state(provider_root))
    _checked_cli(
        provider_root,
        "planning",
        "promote-provider-plan",
        "--invocation-id",
        invocation,
        env_extra=provider_env,
    )
    corpus["provider_plan"] = copy.deepcopy(
        json.loads(state_file.read_text(encoding="utf-8"))
    )

    review_root = root / "reviews"
    _init_cli_state(review_root)
    references = []
    for perspective in ("correctness", "operability"):
        source = review_root / f"{perspective}.json"
        source.write_bytes(canonical_json_bytes(_review_document(perspective)))
        imported = _checked_cli(
            review_root,
            "review-import",
            "--iteration",
            "1",
            "--input",
            str(source),
        )
        references.append(json.loads(imported.stdout)["review_evidence_ref"]["path"])
    corpus["review_input"] = copy.deepcopy(_read_cli_state(review_root))
    scoring = review_root / "scoring.json"
    aggregate_arguments = [
        "aggregate-reviews",
        "--iteration",
        "1",
        "--out",
        str(scoring),
        "--min-reviewers",
        "2",
        "--reviewer-window",
        "correctness=2026-08-15T00:00:00Z..2026-08-15T00:05:00Z",
        "--reviewer-window",
        "operability=2026-08-15T00:00:00Z..2026-08-15T00:05:00Z",
    ]
    for reference in references:
        aggregate_arguments.extend(("--input-ref", reference))
    _checked_cli(review_root, *aggregate_arguments)
    _checked_cli(review_root, "push-score", "--iteration", "1", "--scoring-json", str(scoring))
    corpus["review_aggregate_and_bound_score"] = copy.deepcopy(_read_cli_state(review_root))

    manual_root = root / "manual-score"
    _init_cli_state(manual_root)
    _mutate_cli_state_for_fixture(manual_root, iteration=1)
    manual_state = _read_cli_state(manual_root)
    items = {
        "mission_achievement": 4.5,
        "accuracy": 4.4,
        "completeness": 4.3,
        "usability": 4.2,
    }
    unsigned_manual = {
        "schema": "mission-manual-score/1",
        "session_id": manual_state["session_id"],
        "mission_id": manual_state["mission_id"],
        "iteration": 1,
        "items": items,
        "composite": 4.35,
        "min_item": 4.2,
        "review_agreement": 4.8,
        "open_high": 0,
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "source_evidence_ref": {
            "kind": "manual-source-evidence",
            "ref": "sha256:" + "8" * 64,
            "digest": "sha256:" + "8" * 64,
        },
        "imported_at": "2026-08-15T00:00:00Z",
    }
    manual_payload = {
        **unsigned_manual,
        "input_digest": "sha256:"
        + hashlib.sha256(
            json.dumps(
                unsigned_manual,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    manual_input = manual_root / "manual-score.json"
    manual_input.write_bytes(canonical_json_bytes(manual_payload))
    manual_scoring = manual_root / "manual-scoring.json"
    _checked_cli(
        manual_root,
        "manual-score-capture",
        "--input",
        str(manual_input),
        "--out",
        str(manual_scoring),
    )
    _checked_cli(
        manual_root,
        "push-score",
        "--iteration",
        "1",
        "--scoring-json",
        str(manual_scoring),
    )
    corpus["manual_import_bound_score"] = copy.deepcopy(_read_cli_state(manual_root))
    _checked_cli(
        manual_root,
        "set",
        'specialists_selected=[{"skill":"fixture-specialist","role":"quality"}]',
        'specialist_invocations=[{"invocation_id":"inv_00000000000000000000000000000001",'
        '"iteration":1,"phase":"scoring","role":"quality",'
        '"skill":"fixture-specialist","mode":"skill-tool",'
        '"status":"rejected","lifecycle_state":"terminal"}]',
    )
    corpus["specialist_rejected_scoring"] = copy.deepcopy(
        _read_cli_state(manual_root)
    )

    lease_root = root / "lease"
    corpus["lease_acquired"] = copy.deepcopy(_init_cli_state(lease_root))
    takeover = _run_cli_with_clock(
        lease_root,
        "advance",
        "--phase",
        "reviewing",
        lease_id="replacement-lease",
        now="2099-01-01T00:00:00Z",
    )
    if takeover.returncode != 0:
        raise AssertionError(
            f"CLI lease takeover fixture failed\nstdout={takeover.stdout}\nstderr={takeover.stderr}"
        )
    corpus["lease_taken_over"] = copy.deepcopy(_read_cli_state(lease_root))

    guidance_cases: dict[str, dict] = {}
    awaiting_root = root / "guidance-awaiting-user"
    _init_cli_state(awaiting_root)
    _checked_cli(awaiting_root, "set", "awaiting_user=true")
    guidance_cases["awaiting_user"] = copy.deepcopy(_read_cli_state(awaiting_root))

    inactive_root = root / "guidance-inactive"
    _init_cli_state(inactive_root)
    _mutate_cli_state_for_fixture(inactive_root, loop_active=False)
    guidance_cases["inactive"] = copy.deepcopy(_read_cli_state(inactive_root))

    stagnation_root = root / "guidance-stagnation"
    _init_cli_state(stagnation_root)
    _mutate_cli_state_for_fixture(stagnation_root, stagnation_count=3)
    guidance_cases["stagnation"] = copy.deepcopy(_read_cli_state(stagnation_root))

    critic_scope_root = root / "guidance-critic-scope"
    _init_cli_state(critic_scope_root)
    _mutate_cli_state_for_fixture(
        critic_scope_root, iteration=2, phase="reviewing"
    )
    guidance_cases["critic_scope"] = copy.deepcopy(_read_cli_state(critic_scope_root))

    provider_fallback_root = root / "guidance-provider-primary-binding-missing"
    _init_cli_state(provider_fallback_root)
    _checked_cli(
        provider_fallback_root,
        "set",
        "planning_strategy=provider-primary",
        "planning_provider_binding=null",
    )
    guidance_cases["provider_primary_binding_missing"] = copy.deepcopy(
        _read_cli_state(provider_fallback_root)
    )

    iteration_zero_root = root / "guidance-iteration-zero-scoring"
    _init_cli_state(iteration_zero_root)
    _mutate_cli_state_for_fixture(
        iteration_zero_root, iteration=0, phase="scoring"
    )
    iteration_zero_state = copy.deepcopy(_read_cli_state(iteration_zero_root))
    # Current writers reject iteration-zero score creation, while the K1 legacy
    # decoder deliberately accepts it. Keep the CLI-emitted state as the base
    # and add only the accepted wire-boundary value needed for this parity class.
    iteration_zero_state["score_history"] = [
        {"iteration": 0, "composite": 4.5}
    ]
    guidance_cases["iteration_zero_scoring"] = iteration_zero_state

    simple_route_root = root / "guidance-simple-goal-route"
    _init_cli_state(simple_route_root)
    _checked_cli(
        simple_route_root,
        "set",
        "complexity=Simple",
        "force_mission=true",
    )
    _checked_cli(simple_route_root, "set", "force_mission=false")
    guidance_cases["simple_goal_route"] = copy.deepcopy(
        _read_cli_state(simple_route_root)
    )

    host_route_root = root / "guidance-simple-host-route"
    _init_cli_state(host_route_root)
    _checked_cli(
        host_route_root,
        "set",
        "complexity=Simple",
        "force_mission=true",
        "goal_dispatch_requested=host-native",
        "goal_dispatch_source=mission-instruction",
    )
    _checked_cli(host_route_root, "set", "force_mission=false")
    guidance_cases["simple_host_observation"] = copy.deepcopy(
        _read_cli_state(host_route_root)
    )

    corpus["guidance_branches"] = guidance_cases

    phases: dict[str, dict] = {}
    phase_root = root / "phases"
    _init_cli_state(phase_root)
    for phase in ("planning", "executing", "reviewing", "scoring", "done", "halted"):
        _mutate_cli_state_for_fixture(phase_root, phase=phase)
        phases[phase] = copy.deepcopy(_read_cli_state(phase_root))
    corpus["phases"] = phases

    terminal_categories = {
        "completed_evidence": ("checker", "evidence-submitted"),
        "blocked_external": ("implementer", "blocked-external"),
        "awaiting_approval": ("implementer", "awaiting-approval"),
        "stale_superseded": ("implementer", "stale"),
        "failed": ("implementer", "stagnation"),
        "incomplete": ("implementer", "partial-done"),
        "user_aborted": ("implementer", "user-abort"),
        "routed_elsewhere": ("implementer", "routed-goal"),
    }
    terminals: dict[str, dict] = {}
    _checked_cli(
        review_root,
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "CLI corpus completion",
        "--complexity",
        "Standard",
        "--record-state",
        "--json",
    )
    _checked_cli(review_root, "mark-passes")
    terminals["completed_pass"] = copy.deepcopy(_read_cli_state(review_root))
    for outcome, (role, category) in terminal_categories.items():
        terminal_root = root / f"terminal-{outcome}"
        _init_cli_state(terminal_root, role=role)
        _checked_cli(terminal_root, "mark-halt", "--reason", f"fixture {outcome}", "--category", category)
        state = _read_cli_state(terminal_root)
        if state.get("terminal_outcome") != outcome:
            raise AssertionError(f"CLI wrote {state.get('terminal_outcome')!r}, expected {outcome!r}")
        terminals[outcome] = copy.deepcopy(state)
    corpus["terminal_outcomes"] = terminals
    return corpus


def legacy_review_evidence(*, perspective: str = "quality", iteration: int = 1, status: str | None = None) -> dict:
    review = {
        "schema": "mission-review/1",
        "iteration": iteration,
        "perspective": perspective,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.2,
            "completeness": 4.1,
            "usability": 4.0,
        },
        "findings": [
            {
                "id": f"{perspective}-finding",
                "iteration": iteration,
                "perspective": perspective,
                "severity": "Medium",
                "axis": "accuracy",
                "summary": "legacy finding",
                "evidence": "bounded evidence",
                "recommendation": "validate it",
                "status": status,
            }
        ],
    }
    return review


def legacy_review_bytes(*, perspective: str = "quality", iteration: int = 1, status: str | None = None) -> bytes:
    return canonical_json_bytes(legacy_review_evidence(perspective=perspective, iteration=iteration, status=status))


def current_v5_open_state() -> dict:
    return {
        "schema_version": 5,
        "identity": {
            "mission": "typed kernel mission",
            "mission_id": "mission-500",
            "session_id": "session-500",
        },
        "control": {
            "phase": "planning",
            "terminal_outcome": None,
            "iteration": 2,
            "max_iter": 5,
            "threshold": 4.0,
            "reviewer_count": 2,
            "stagnation_count": 0,
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "halt_category": None,
            "session_role": "implementer",
        },
        "plan": {
            "kind": "core",
            "schema": "mission-plan/1",
            "path": ".mission-state/plans/canonical-core.json",
            "digest": "sha256:" + "1" * 64,
            "source": "core",
            "source_id": "core-fixture",
            "source_digest": "sha256:" + "2" * 64,
            "selection_source": "automatic",
            "iteration": 2,
            "generation": 1,
            "validated_at": "2026-08-14T00:00:00Z",
        },
        "handoff": {
            "kind": "prepared",
            "schema": "mission-handoff/1",
            "handoff_id": "handoff-500",
            "plan": {
                "schema": "mission-plan/1",
                "path": ".mission-state/plans/canonical-core.json",
                "digest": "sha256:" + "1" * 64,
                "source": "core",
                "source_id": "core-fixture",
                "source_digest": "sha256:" + "2" * 64,
                "selection_source": "automatic",
                "iteration": 2,
                "generation": 1,
            },
            "ordered_step_ids": ("s1", "s2"),
        },
        "reviews": (
            {
                "kind": "review-input",
                "relative_path": ".mission-state/archive/review-500.json",
                "digest": "sha256:" + "3" * 64,
                "size": 123,
                "iteration": 2,
                "perspective": "quality",
            },
        ),
        "findings": (),
        "scores": (),
        "lease": {
            "kind": "fenced",
            "owner_session_id": "session-500",
            "lease_id": "lease-500",
            "fencing_epoch": 2,
            "lease_expires_at": "2026-08-14T00:15:00Z",
            "lease_history": (),
        },
        "guidance": {
            "schema": "mission-guidance/1",
            "routing": {
                "awaiting_user": False,
                "complexity": "Standard",
                "force_mission": False,
                "issue_ref": None,
            },
            "planning": {
                "policy_version": 1,
                "provider_required": False,
                "strategy": "core",
            },
            "review": {
                "critic_has_new_scope": None,
                "tier": "standard",
                "tier_source": "auto",
                "tier_signals": (),
            },
            "advisories": {"pregate": None},
            "providers": {
                "primary_binding": None,
                "selections": (),
                "invocations": (),
                "imported_invocation_ids": (),
            },
        },
        "extensions": {"x": {"kind": "fixture"}},
    }
