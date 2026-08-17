"""A1 lifecycle use cases preserve the production CLI's v4 bytes."""

from __future__ import annotations

import contextlib
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from .mission_state_fixture_corpus import (
    _checked_cli as _production_checked_cli,
    _run_cli as _production_run_cli,
    _write_core_plan,
    canonical_json_bytes,
    issue483_corpus,
)


_GOLDEN_PATH = Path(__file__).with_name("fixtures") / "lifecycle_a1" / "golden.json"
_GOLDEN = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
_ROOT_TOKEN = "__ROOT__"
_GOLDEN_SENTINEL_ROOT = Path("/__golden_normalize_root__")
_ENV_DERIVED_TOKEN = "<env-derived>"
_FIXED_PID = 424242
_FIXED_HOSTNAME = "fixture-host"


class _MutationBoundaryRepository:
    """Test port enforcing that only ``execute`` may mutate loaded state."""

    def __init__(self, before, write_state):
        self._before = copy.deepcopy(before)
        self._write_state = write_state
        self._executed = None

    def transaction(self):
        return contextlib.nullcontext()

    def load(self):
        return copy.deepcopy(self._before)

    def execute(self, state, mutation, transition=None):
        assert state == self._before
        proposed = copy.deepcopy(state)
        mutation(proposed)
        self._executed = copy.deepcopy(proposed)
        return proposed

    def save(
        self,
        state,
        *,
        backup=True,
        administrative=False,
        aggregate_action=None,
    ):
        assert state == self._executed
        self._write_state(state, administrative=administrative)


def _load_cli_module(name):
    path = __import__("pathlib").Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _golden_step(case: str, index: int = -1) -> dict:
    return _GOLDEN["cases"][case]["steps"][index]


def _normalize_golden_value(value, root: Path):
    if isinstance(value, dict):
        normalized = {
            key: _normalize_golden_value(item, root) for key, item in value.items()
        }
        for key in ("pid", "old_pid", "new_pid", "updated_by_pid"):
            if isinstance(normalized.get(key), int):
                normalized[key] = _FIXED_PID
        # pid を正規化するなら、同じ find_agent_pid() 由来の pid_source も
        # 起動元プロセスツリーに依存する環境メタデータとして揃えないと、
        # claude / codex 配下か CI 直下かで golden 比較の結果が変わる。
        if isinstance(normalized.get("pid_source"), str):
            normalized["pid_source"] = _ENV_DERIVED_TOKEN
        if isinstance(normalized.get("hostname"), str):
            normalized["hostname"] = _FIXED_HOSTNAME
        for key in ("host_run_id", "root_run_id"):
            if isinstance(normalized.get(key), str) and normalized[key].startswith(
                "mission-local-"
            ):
                normalized[key] = "mission-local-<generated>"
        if isinstance(normalized.get("handoff_id"), str) and normalized[
            "handoff_id"
        ].startswith("handoff_"):
            normalized["handoff_id"] = "handoff_<generated>"
        decision = normalized.get("specialists_decision")
        if isinstance(decision, dict) and isinstance(decision.get("selection_id"), str):
            decision["selection_id"] = "sel_<generated>"
        if isinstance(normalized.get("assumptions_path"), str):
            normalized["assumptions_path"] = "<generated-assumptions-path>"
        return normalized
    if isinstance(value, list):
        return [_normalize_golden_value(item, root) for item in value]
    if isinstance(value, str):
        for root_form in sorted({str(root), str(root.resolve())}, key=len, reverse=True):
            value = value.replace(root_form, _ROOT_TOKEN)
        return value
    return value


def _denormalize_golden_value(value, root: Path):
    if isinstance(value, dict):
        return {
            key: _denormalize_golden_value(item, root) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_denormalize_golden_value(item, root) for item in value]
    if isinstance(value, str):
        if value == "sel_<generated>":
            return "sel_00000000000000000000000000000000"
        if value == "handoff_<generated>":
            return "handoff_00000000000000000000000000000000"
        return value.replace(_ROOT_TOKEN, str(root.resolve()))
    return value


def _golden_state_bytes(case: str, index: int = -1, key: str = "after_state_bytes_b64") -> bytes:
    encoded = _golden_step(case, index)[key]
    assert encoded is not None
    payload = json.loads(base64.b64decode(encoded))
    return canonical_json_bytes(_normalize_golden_value(payload, _GOLDEN_SENTINEL_ROOT))


def _golden_state(case: str, root: Path, index: int = -1, key: str = "after_state_bytes_b64") -> dict:
    payload = json.loads(_golden_state_bytes(case, index, key))
    return _denormalize_golden_value(payload, root)


def _golden_state_bytes_for_root(
    case: str,
    root: Path,
    index: int = -1,
    key: str = "after_state_bytes_b64",
) -> bytes:
    return canonical_json_bytes(_normalize_golden_value(_golden_state(case, root, index, key), root))


def _write_golden_state(
    root: Path,
    case: str,
    index: int = -1,
    key: str = "after_state_bytes_b64",
) -> Path:
    state_path = root / ".mission-state" / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(_golden_state(case, root, index, key), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return state_path


def _normalized_output(text: str, root: Path) -> str:
    if not text:
        return text
    stripped = text.rstrip("\n")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        normalized = text
        for root_form in sorted({str(root), str(root.resolve())}, key=len, reverse=True):
            normalized = normalized.replace(root_form, _ROOT_TOKEN)
        return normalized
    suffix = "\n" if text.endswith("\n") else ""
    return json.dumps(_normalize_golden_value(payload, root), ensure_ascii=False) + suffix


def _assert_cli_result(result, case: str, root: Path, index: int = -1) -> None:
    expected = _golden_step(case, index)
    assert result.returncode == expected["exit_code"]
    assert _normalized_output(result.stdout, root) == expected["stdout"]
    assert _normalized_output(result.stderr, root) == expected["stderr"]


def _normalized_root_bytes(path: Path, root: Path) -> bytes:
    state = json.loads(path.read_text(encoding="utf-8"))
    return canonical_json_bytes(_normalize_golden_value(state, root))


def _write_corpus_state(root: Path, payload: dict) -> Path:
    state_path = root / ".mission-state" / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True)
    state = copy.deepcopy(payload)
    state["project_root"] = str(root)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return state_path


def test_golden_fixture_records_pinned_real_cli_provenance():
    assert _GOLDEN["schema"] == "mission-lifecycle-a1-golden/1"
    assert _GOLDEN["provenance"]["source"] == (
        "actual extraction-predecessor mission-state.py CLI output"
    )
    assert _GOLDEN["provenance"]["source_revision"] == (
        "57f43a740c933eaffa611cdf3c6b45e91be0b50c"
    )
    assert _GOLDEN["provenance"]["generator"].startswith(
        "python skills/mission/tests/fixtures/lifecycle_a1/generate.py"
    )


@pytest.mark.parametrize(
    ("pid_source",),
    (("agent",), ("fallback",)),
    ids=("agent", "fallback"),
)
def test_normalize_golden_value_normalizes_pid_source(tmp_path, pid_source):
    normalized = _normalize_golden_value(
        {"pid": 1234, "pid_source": pid_source}, tmp_path
    )
    assert normalized["pid"] == _FIXED_PID
    assert normalized["pid_source"] == _ENV_DERIVED_TOKEN


@pytest.mark.parametrize(
    ("comm", "expected_pid_source", "expected_fallback"),
    (
        ("claude", "agent", False),
        ("bash", "fallback", True),
    ),
    ids=("agent-ancestor", "fallback-no-ancestor"),
)
def test_find_agent_pid_and_stamp_metadata_track_pid_source_semantics(
    tmp_path, monkeypatch, comm, expected_pid_source, expected_fallback
):
    cli = _load_cli_module(f"issue506_pid_source_{expected_pid_source}")
    root_pid = 4321

    def fake_run(args, capture_output, text, timeout):
        assert capture_output is True
        assert text is True
        assert timeout == 2
        if args == ["ps", "-o", "comm=", "-p", str(root_pid)]:
            return type("Result", (), {"stdout": f"{comm}\n"})()
        if args == ["ps", "-o", "ppid=", "-p", str(root_pid)]:
            return type("Result", (), {"stdout": "1\n"})()
        raise AssertionError(args)

    monkeypatch.setattr(cli.os, "getppid", lambda: root_pid)
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    pid = cli.find_agent_pid()
    data = {}
    cli.stamp_metadata(data, tmp_path)

    assert pid == root_pid
    assert cli._last_pid_was_fallback() is expected_fallback
    assert data["pid"] == root_pid
    assert data["pid_source"] == expected_pid_source


def test_init_repository_boundary_matches_extraction_predecessor_bytes(
    tmp_path, monkeypatch
):
    from mission_application.lifecycle import InitRequest, initialize
    from mission_persistence.legacy_v4 import LegacyV4InitializerRepository

    cli = _load_cli_module("issue506_init_repository")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "fixture-lease")
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-16T00:00:00Z")
    saved = {}

    def write_state(path, state):
        cli.atomic_write_json(path, state)
        saved.update(json.loads(path.read_text(encoding="utf-8")))

    repository = LegacyV4InitializerRepository(
        initialize_state=cli._initialize_legacy_v4,
        write_state=write_state,
    )
    arguments = cli._build_parser().parse_args(
        [
            "init",
            "A1 init parity",
            "--complexity",
            "Standard",
            "--host-run-id",
            "host-run",
            "--root-run-id",
            "root-run",
            "--artifact-applicability",
            "not-applicable",
        ]
    )
    request = InitRequest(arguments=arguments)
    initialize(repository, request)
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result_bytes == _golden_state_bytes_for_root("init_repository", tmp_path)


@pytest.mark.parametrize(
    "label",
    ("missing", "v1", "v2", "v3", "v4"),
    ids=("missing", "v1", "v2", "v3", "v4"),
)
@pytest.mark.parametrize(
    ("arguments", "expected_returncode"),
    (
        (
            (
                "init",
                "A1 corpus replacement",
                "--complexity",
                "Standard",
                "--host-run-id",
                "host-run",
                "--root-run-id",
                "root-run",
                "--artifact-applicability",
                "not-applicable",
            ),
            0,
        ),
        (("advance", "--phase", "reviewing", "--artifact-applicability", "not-applicable"), 2),
        (("mark-halt", "--reason", "compatibility halt", "--category", "other"), 0),
    ),
    ids=("init", "advance", "halt"),
)
def test_issue483_variants_keep_exact_legacy_bytes_after_lifecycle_command(
    tmp_path, label, arguments, expected_returncode
):
    current_root = tmp_path / "current"
    current_path = _write_corpus_state(current_root, issue483_corpus()[label])
    environment = {"MISSION_STATE_NOW": "2026-08-16T00:10:00Z"}

    current = _production_run_cli(
        current_root,
        *arguments,
        env_extra=environment,
    )
    result_bytes = _normalized_root_bytes(current_path, current_root)
    action = {"mark-halt": "halt"}.get(arguments[0], arguments[0])
    case = f"issue483/{action}/{label}"

    _assert_cli_result(current, case, current_root)
    assert current.returncode == expected_returncode
    assert result_bytes == _golden_state_bytes_for_root(case, current_root)
    current_state = json.loads(current_path.read_text())
    expected_schema = (
        4 if arguments[0] == "init" else issue483_corpus()[label].get("schema_version")
    )
    assert current_state.get("schema_version") == expected_schema


@pytest.mark.parametrize(
    "reason",
    (" padded reason ", ""),
    ids=("surrounding-whitespace", "empty"),
)
def test_mark_halt_legacy_reason_boundary_keeps_exact_bytes(tmp_path, reason):
    current_root = tmp_path / "current"
    current_root.mkdir()
    environment = {"MISSION_STATE_NOW": "2026-08-16T00:20:00Z"}
    label = "surrounding-whitespace" if reason else "empty"
    case = f"mark_halt_reason/{label}"
    current_path = _write_golden_state(current_root, case, index=0)
    arguments = ("mark-halt", "--reason", reason, "--category", "other")

    current = _production_run_cli(
        current_root,
        *arguments,
        env_extra=environment,
    )
    result_bytes = _normalized_root_bytes(current_path, current_root)

    _assert_cli_result(current, case, current_root, index=1)
    assert result_bytes == _golden_state_bytes(case, index=1)


def test_activity_start_result_bytes_equal_real_cli_bytes(tmp_path):
    from mission_application.lifecycle import ActivityStartRequest, activity_start
    from mission_persistence.legacy_v4 import LegacyV4Repository

    now = "2026-08-16T01:02:03Z"
    before = _golden_state("activity_start", tmp_path, index=0)
    legacy_bytes = _golden_state_bytes_for_root("activity_start", tmp_path, index=1)

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = now
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
    )
    result = activity_start(
        repository,
        ActivityStartRequest(
            kind="active",
            reason="implementation",
            at=now,
            detail=None,
            resume=False,
        ),
    )
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.changed is True
    assert result_bytes == legacy_bytes


def test_activity_end_result_bytes_equal_real_cli_bytes(tmp_path):
    from mission_application.lifecycle import ActivityEndRequest, activity_end
    from mission_persistence.legacy_v4 import LegacyV4Repository

    started_at = "2026-08-16T01:02:03Z"
    ended_at = "2026-08-16T01:03:05Z"
    before = _golden_state("activity_end", tmp_path, index=1)
    legacy_bytes = _golden_state_bytes_for_root("activity_end", tmp_path, index=2)

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = ended_at
        state["lease_expires_at"] = "2026-08-16T01:18:05Z"
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
    )
    result = activity_end(repository, ActivityEndRequest(at=ended_at))
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.changed is True
    assert result_bytes == legacy_bytes


def test_advance_result_bytes_equal_real_cli_bytes_and_kernel_accepts(tmp_path):
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, advance
    from mission_persistence.legacy_v4 import LegacyV4Repository

    cli = _load_cli_module("issue506_advance_services")
    reviewing_at = "2026-08-16T02:04:06Z"
    before = _golden_state("advance", tmp_path, index=2)
    legacy_bytes = _golden_state_bytes_for_root("advance", tmp_path, index=3)

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = reviewing_at
        state["lease_expires_at"] = "2026-08-16T02:19:06Z"
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
    )
    result = advance(
        repository,
        AdvanceRequest(
            phase="reviewing",
            activity="active:review",
            at=reviewing_at,
            detail=None,
            artifact_applicability="not-applicable",
            artifact_path=None,
            producer_run_id=None,
        ),
        AdvanceServices(
            reject_active_provider_mutation=cli._reject_active_provider_mutation,
            prepare_handoff=lambda _state: None,
            capture_artifact=cli.capture_artifact_identity,
            transition_phase=cli._transition_phase,
        ),
    )
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.decision.accepted is True
    assert result.decision.rule_id == "advance-phase"
    assert result_bytes == legacy_bytes


def test_policy_v1_advance_with_plan_to_executing_matches_real_cli_bytes(tmp_path):
    from mission_application.lifecycle import AdvanceRequest, AdvanceServices, advance
    from mission_persistence.legacy_v4 import LegacyV4Repository

    cli = _load_cli_module("issue506_executing_advance_services")
    executing_at = "2026-08-16T02:00:00Z"
    plan_source = _write_core_plan(tmp_path)
    _production_checked_cli(
        tmp_path,
        "init",
        "A1 advance parity",
        "--complexity",
        "Standard",
        env_extra={"MISSION_STATE_NOW": executing_at},
    )
    _production_checked_cli(
        tmp_path,
        "planning",
        "adopt-core",
        "--input",
        str(plan_source),
        "--source-id",
        "issue506-core",
        env_extra={"MISSION_STATE_NOW": executing_at},
    )
    before = _golden_state("advance", tmp_path, index=1)
    legacy_bytes = _golden_state_bytes_for_root("advance", tmp_path, index=2)
    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = executing_at
        state["lease_expires_at"] = "2026-08-16T02:15:00Z"
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
    )
    result = advance(
        repository,
        AdvanceRequest(
            phase="executing",
            activity="active:implementation",
            at=executing_at,
            detail=None,
            artifact_applicability=None,
            artifact_path=None,
            producer_run_id=None,
        ),
        AdvanceServices(
            reject_active_provider_mutation=cli._reject_active_provider_mutation,
            prepare_handoff=lambda state: cli._prepare_advance_handoff(
                tmp_path, state
            ),
            capture_artifact=cli.capture_artifact_identity,
            transition_phase=cli._transition_phase,
        ),
    )
    result_bytes = canonical_json_bytes(
        _normalize_golden_value(saved, tmp_path)
    )

    assert result.decision.accepted is True
    assert result.decision.rule_id == "advance-phase"
    assert result_bytes == _golden_state_bytes("advance", index=2)


def test_mark_halt_result_bytes_equal_real_cli_bytes_and_kernel_accepts(tmp_path):
    from mission_application.lifecycle import MarkHaltRequest, MarkHaltServices, mark_halt
    from mission_persistence.legacy_v4 import LegacyV4Repository

    cli = _load_cli_module("issue506_mark_halt_services")
    now = "2026-08-16T03:05:07Z"
    before = _golden_state("mark_halt", tmp_path, index=0)
    legacy_bytes = _golden_state_bytes_for_root("mark_halt", tmp_path, index=1)

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = now
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
        remove_from_aggregate=lambda: None,
    )
    result = mark_halt(
        repository,
        MarkHaltRequest(
            reason="waiting for an external prerequisite",
            category="blocked-external",
            at=now,
        ),
        MarkHaltServices(
            reject_active_provider_mutation=cli._reject_active_provider_mutation,
            transition_phase=cli._transition_phase,
            goal_dispatch_fields=cli._goal_dispatch_route_fields,
        ),
    )
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.decision.accepted is True
    assert result.decision.rule_id == "mark-halt"
    assert result.aggregate_error is None
    assert result_bytes == legacy_bytes


def test_mark_halt_reports_aggregate_failure_after_session_write(tmp_path):
    from mission_application.lifecycle import MarkHaltRequest, MarkHaltServices, mark_halt
    from mission_persistence.legacy_v4 import LegacyV4Repository

    cli = _load_cli_module("issue506_mark_halt_aggregate_failure")
    now = "2026-08-16T03:06:08Z"
    before = _golden_state("aggregate_failure", tmp_path)
    saved = {}

    def fail_aggregate():
        raise OSError("aggregate index is unavailable")

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=lambda state, **_kwargs: saved.update(copy.deepcopy(state)),
        backup_state=lambda: None,
        remove_from_aggregate=fail_aggregate,
    )
    result = mark_halt(
        repository,
        MarkHaltRequest("bounded failure", "blocked-external", now),
        MarkHaltServices(
            reject_active_provider_mutation=cli._reject_active_provider_mutation,
            transition_phase=cli._transition_phase,
            goal_dispatch_fields=cli._goal_dispatch_route_fields,
        ),
    )

    assert result.aggregate_error == "aggregate index is unavailable"
    assert saved["phase"] == "halted"
    assert saved["loop_active"] is False


def test_reactivate_result_bytes_equal_real_cli_bytes_and_kernel_accepts(tmp_path):
    from mission_application.lifecycle import ReactivateRequest, reactivate
    from mission_persistence.legacy_v4 import LegacyV4Repository

    reactivated_at = "2026-08-16T04:02:04Z"
    before = _golden_state("reactivate", tmp_path, index=1)
    legacy_bytes = _golden_state_bytes_for_root("reactivate", tmp_path, index=2)

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = reactivated_at
        state["lease_expires_at"] = "2026-08-16T04:17:04Z"
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
        add_to_aggregate=lambda: None,
    )
    result = reactivate(
        repository,
        ReactivateRequest(
            approved_by_user=True,
            reason="approval was recorded",
            expected_category="awaiting-approval",
            phase="planning",
            at=reactivated_at,
        ),
    )
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.decision.accepted is True
    assert result.decision.rule_id == "reactivate"
    assert result.aggregate_error is None
    assert result_bytes == legacy_bytes


def test_refresh_pid_result_bytes_equal_real_cli_bytes(tmp_path):
    from mission_application.lifecycle import RefreshPidRequest, RefreshPidServices, refresh_pid

    cli = _load_cli_module("issue506_refresh_pid_services")
    now = "2026-08-16T05:01:03Z"
    before = _golden_state("refresh_pid", tmp_path, index=0)
    legacy_output = json.loads(_golden_step("refresh_pid", 1)["stdout"])
    legacy_bytes = _golden_state_bytes_for_root("refresh_pid", tmp_path, index=1)

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = now
        saved.update(copy.deepcopy(state))

    repository = _MutationBoundaryRepository(before, write_state)
    result = refresh_pid(
        repository,
        RefreshPidRequest(
            new_pid=legacy_output["new_pid"],
            force=False,
            reactivate=True,
            at=now,
        ),
        RefreshPidServices(
            lease_fields_present=cli._lease_fields_present,
            pid_is_agent=cli._pid_is_agent,
            resume_phase_timing=cli._resume_phase_timing,
        ),
    )
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.old_pid == legacy_output["old_pid"]
    assert result.new_pid == legacy_output["new_pid"]
    assert result.reactivated is False
    assert result_bytes == legacy_bytes


def test_refresh_pid_closes_resume_activity_inside_repository_execute(tmp_path):
    from mission_application.lifecycle import RefreshPidRequest, RefreshPidServices, refresh_pid

    cli = _load_cli_module("issue506_refresh_pid_mutation_boundary")
    before = _golden_state("refresh_pid", tmp_path, index=0)
    now = "2026-08-16T05:02:03Z"
    saved = {}

    result = refresh_pid(
        _MutationBoundaryRepository(
            before,
            lambda state, **_kwargs: saved.update(copy.deepcopy(state)),
        ),
        RefreshPidRequest(
            new_pid=before["pid"],
            force=False,
            reactivate=True,
            at=now,
        ),
        RefreshPidServices(
            lease_fields_present=cli._lease_fields_present,
            pid_is_agent=cli._pid_is_agent,
            resume_phase_timing=cli._resume_phase_timing,
        ),
    )

    assert result.new_pid == before["pid"]
    assert saved["activity_current"]["started_at"] == now
    assert saved["activity_unobserved_gap_sec"] == 60.0


def test_routed_goal_set_mutates_only_inside_repository_execute(tmp_path):
    from mission_application.lifecycle import SetFieldsRequest, SetFieldsServices, set_fields

    cli = _load_cli_module("issue506_routed_goal_set_services")
    now = "2026-08-16T09:07:09Z"
    before = _golden_state("set_narrowing", tmp_path, index=0)
    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is True
        saved.update(copy.deepcopy(state))

    result = set_fields(
        _MutationBoundaryRepository(before, write_state),
        SetFieldsRequest(kvs=("complexity=Simple",), at=now),
        SetFieldsServices(
            frozen_fields=frozenset(cli.FROZEN_FIELDS),
            reject_active_provider_mutation=cli._reject_active_provider_mutation,
            normalize_phase=cli._normalize_set_phase_value,
            transition_phase=cli._transition_phase,
            ensure_phase_timing=cli._ensure_phase_timing,
            derive_review_tier=cli.derive_review_tier,
            derive_review_tier_decision=cli.derive_review_tier_decision,
            reviewer_count_by_tier=dict(cli.TIER_REVIEWER_COUNT),
            goal_dispatch_fields=cli._goal_dispatch_route_fields,
            goal_dispatch_guidance=cli._goal_dispatch_guidance,
        ),
    )

    assert result.routed_verdict is not None
    assert result.routed_verdict["route"] == "goal"
    assert saved["phase"] == "halted"
    assert saved["halt_category"] == "routed-goal"
    assert saved["updated_at"] == now


def test_update_project_root_result_bytes_equal_real_cli_bytes(tmp_path):
    from mission_application.lifecycle import UpdateProjectRootRequest, update_project_root
    from mission_persistence.legacy_v4 import LegacyV4Repository

    now = "2026-08-16T06:02:04Z"
    destination = tmp_path / "moved-project"
    destination.mkdir()
    before = _golden_state("update_project_root", tmp_path, index=0)
    legacy_bytes = _golden_state_bytes_for_root(
        "update_project_root", tmp_path, index=1
    )

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = now
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
    )
    result = update_project_root(
        repository,
        UpdateProjectRootRequest(new_root=str(destination.resolve()), at=now),
    )
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.old_root == str(tmp_path.resolve())
    assert result.new_root == str(destination.resolve())
    assert result_bytes == legacy_bytes


def test_set_result_bytes_equal_real_cli_bytes_without_new_narrowing(tmp_path):
    from mission_application.lifecycle import SetFieldsRequest, SetFieldsServices, set_fields
    from mission_persistence.legacy_v4 import LegacyV4Repository

    cli = _load_cli_module("issue506_set_services")
    now = "2026-08-16T07:03:05Z"
    before = _golden_state("set_fields", tmp_path, index=0)
    legacy_bytes = _golden_state_bytes_for_root("set_fields", tmp_path, index=1)

    saved = {}

    def write_state(state, *, administrative=False):
        assert administrative is False
        state["last_activity_at"] = now
        saved.update(copy.deepcopy(state))

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=write_state,
        backup_state=lambda: None,
        add_to_aggregate=lambda: None,
        remove_from_aggregate=lambda: None,
    )
    result = set_fields(
        repository,
        SetFieldsRequest(
            kvs=("complexity=Complex", "custom_legacy_field=preserved"),
            at=now,
        ),
        SetFieldsServices(
            frozen_fields=frozenset(cli.FROZEN_FIELDS),
            reject_active_provider_mutation=cli._reject_active_provider_mutation,
            normalize_phase=cli._normalize_set_phase_value,
            transition_phase=cli._transition_phase,
            ensure_phase_timing=cli._ensure_phase_timing,
            derive_review_tier=cli.derive_review_tier,
            derive_review_tier_decision=cli.derive_review_tier_decision,
            reviewer_count_by_tier=dict(cli.TIER_REVIEWER_COUNT),
            goal_dispatch_fields=cli._goal_dispatch_route_fields,
            goal_dispatch_guidance=cli._goal_dispatch_guidance,
        ),
    )
    result_bytes = canonical_json_bytes(_normalize_golden_value(saved, tmp_path))

    assert result.routed_verdict is None
    assert saved["custom_legacy_field"] == "preserved"
    assert result_bytes == legacy_bytes


@pytest.mark.parametrize("terminal_phase", ("done", "halted"), ids=("done", "halted"))
def test_advance_to_terminal_phase_rejects_with_exact_bytes_unchanged(
    tmp_path, terminal_phase
):
    from .mission_state_fixture_corpus import _run_cli

    now = "2026-08-16T08:04:06Z"
    step_index = 1 if terminal_phase == "done" else 2
    state_path = _write_golden_state(tmp_path, "terminal_rejection", index=0)
    legacy_bytes = state_path.read_bytes()

    result = _run_cli(
        tmp_path,
        "advance",
        "--phase",
        terminal_phase,
        env_extra={"MISSION_STATE_NOW": now},
    )
    result_bytes = state_path.read_bytes()

    _assert_cli_result(result, "terminal_rejection", tmp_path, index=step_index)
    assert result_bytes == legacy_bytes
    assert _normalized_root_bytes(state_path, tmp_path) == _golden_state_bytes(
        "terminal_rejection", index=step_index
    )


def test_reactivate_without_approval_rejects_with_exact_bytes_unchanged(tmp_path):
    from .mission_state_fixture_corpus import _run_cli

    now = "2026-08-16T08:05:07Z"
    state_path = _write_golden_state(
        tmp_path, "reactivate_without_approval", index=1
    )
    legacy_bytes = state_path.read_bytes()

    result = _run_cli(
        tmp_path,
        "reactivate",
        "--reason",
        "missing explicit approval",
        "--expected-category",
        "awaiting-approval",
        env_extra={"MISSION_STATE_NOW": now},
    )
    result_bytes = state_path.read_bytes()

    _assert_cli_result(result, "reactivate_without_approval", tmp_path, index=2)
    assert result_bytes == legacy_bytes
    assert _normalized_root_bytes(state_path, tmp_path) == _golden_state_bytes(
        "reactivate_without_approval", index=2
    )


def test_legacy_repository_execute_is_pure_and_does_not_call_io_ports():
    from mission_persistence.legacy_v4 import LegacyV4Repository

    def unexpected_io(*_args, **_kwargs):
        raise AssertionError("execute called an I/O port")

    repository = LegacyV4Repository(
        lock=unexpected_io,
        read_state=unexpected_io,
        write_state=unexpected_io,
        backup_state=unexpected_io,
        add_to_aggregate=unexpected_io,
        remove_from_aggregate=unexpected_io,
    )
    source = {"phase": "planning", "custom": {"preserved": True}}

    result = repository.execute(
        source,
        lambda proposed: proposed.update({"phase": "executing"}),
    )

    assert source["phase"] == "planning"
    assert result == {"phase": "executing", "custom": {"preserved": True}}


def test_real_cli_reports_corrupt_aggregate_after_session_write(tmp_path):
    from .mission_state_fixture_corpus import _run_cli

    now = "2026-08-16T09:05:07Z"
    state_path = _write_golden_state(tmp_path, "corrupt_aggregate", index=0)
    aggregate_path = tmp_path / ".mission-state" / "aggregate.json"
    aggregate_path.write_text("{corrupt", encoding="utf-8")

    result = _run_cli(
        tmp_path,
        "mark-halt",
        "--reason",
        "aggregate fault must not roll back authority",
        "--category",
        "blocked-external",
        env_extra={"MISSION_STATE_NOW": now},
    )
    state = json.loads(
        state_path.read_text()
    )

    expected = _golden_step("corrupt_aggregate", 1)
    assert result.returncode == expected["exit_code"]
    assert _normalized_output(result.stdout, tmp_path) == expected["stdout"]
    assert "aggregate index update failed" in result.stderr
    assert _normalized_root_bytes(state_path, tmp_path) == _golden_state_bytes(
        "corrupt_aggregate", index=1
    )
    assert state["phase"] == "halted"
    assert state["loop_active"] is False
    assert aggregate_path.read_text(encoding="utf-8") == "{corrupt"


def test_policy_v1_advance_without_plan_rejects_with_exact_bytes_unchanged(tmp_path):
    from .mission_state_fixture_corpus import _run_cli

    now = "2026-08-16T09:06:08Z"
    state_path = _write_golden_state(tmp_path, "missing_plan", index=0)
    legacy_bytes = state_path.read_bytes()

    result = _run_cli(
        tmp_path,
        "advance",
        "--phase",
        "executing",
        env_extra={"MISSION_STATE_NOW": now},
    )
    result_bytes = state_path.read_bytes()

    _assert_cli_result(result, "missing_plan", tmp_path, index=1)
    assert result_bytes == legacy_bytes
    assert _normalized_root_bytes(state_path, tmp_path) == _golden_state_bytes(
        "missing_plan", index=1
    )


@pytest.mark.parametrize(
    "dedicated_update",
    ("halt_reason=forged", "halt_category=other"),
    ids=("halt-reason", "halt-category"),
)
def test_set_rejects_dedicated_halt_fields_with_exact_bytes_unchanged(
    tmp_path, dedicated_update
):
    from .mission_state_fixture_corpus import _run_cli

    now = "2026-08-16T09:07:09Z"
    state_path = _write_golden_state(tmp_path, "set_narrowing")
    legacy_bytes = state_path.read_bytes()

    result = _run_cli(
        tmp_path,
        "set",
        dedicated_update,
        env_extra={"MISSION_STATE_NOW": now},
    )
    result_bytes = state_path.read_bytes()

    assert result.returncode == 2
    assert result_bytes == legacy_bytes


@pytest.mark.parametrize(
    ("malformed_update", "case_id"),
    (
        ({"iteration": "oops"}, "string-iteration"),
        ({"owner_session_id": "partial-owner"}, "partial-lease"),
        ({"reviewer_count": "oops"}, "string-reviewer-count"),
    ),
)
def test_mark_halt_remains_available_for_malformed_legacy_v4_state(
    tmp_path, malformed_update, case_id
):
    from .mission_state_fixture_corpus import _run_cli

    now = "2026-08-17T02:00:00Z"
    initialized = _run_cli(
        tmp_path,
        "init",
        f"A1 emergency halt {case_id}",
        "--complexity",
        "Standard",
        env_extra={"MISSION_STATE_NOW": now},
    )
    assert initialized.returncode == 0, initialized.stderr
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if case_id == "partial-lease":
        for key in ("lease_id", "fencing_epoch", "lease_expires_at"):
            state.pop(key, None)
    state.update(malformed_update)
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = _run_cli(
        tmp_path,
        "mark-halt",
        "--reason",
        "emergency stop",
        "--category",
        "other",
        env_extra={"MISSION_STATE_NOW": now},
    )

    halted = json.loads(state_path.read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stderr
    assert halted["loop_active"] is False
    assert halted["halt_reason"] == "emergency stop"
    assert halted["halt_category"] == "other"


@pytest.mark.parametrize(
    "dedicated_update",
    (
        "phase=executing",
        "pid=123",
        "loop_active=false",
        "lease_id=forged",
        "activity_current=null",
        "resume_target_phase=reviewing",
        "activity_last_event_at=2099-01-01T00:00:00Z",
        "activity_last_event_phase=reviewing",
        'activity_anomaly_counts={"forged":1}',
    ),
)
def test_set_rejects_all_dedicated_lifecycle_fields_with_bytes_unchanged(
    tmp_path, dedicated_update
):
    from .mission_state_fixture_corpus import _run_cli

    now = "2026-08-17T02:01:00Z"
    state_path = _write_golden_state(tmp_path, "set_narrowing")
    before = state_path.read_bytes()

    result = _run_cli(
        tmp_path,
        "set",
        dedicated_update,
        env_extra={"MISSION_STATE_NOW": now},
    )

    assert result.returncode == 2
    assert state_path.read_bytes() == before


def test_reactivate_rejects_real_cli_passed_state_without_write(tmp_path):
    from mission_application.lifecycle import (
        LifecycleFailure,
        ReactivateRequest,
        reactivate,
    )
    from mission_persistence.legacy_v4 import LegacyV4Repository
    from .mission_state_fixture_corpus import generate_cli_state_corpus

    passed = generate_cli_state_corpus(tmp_path)["terminal_outcomes"]["completed_pass"]

    def unexpected_write(*_args, **_kwargs):
        raise AssertionError("passed state was mutated")

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(passed),
        write_state=unexpected_write,
        backup_state=unexpected_write,
    )
    with pytest.raises(LifecycleFailure) as rejected:
        reactivate(
            repository,
            ReactivateRequest(
                approved_by_user=True,
                reason="must remain terminal",
                expected_category="other",
                phase="planning",
                at="2026-08-16T09:08:10Z",
            ),
        )

    assert rejected.value.reason == "terminal-state"
