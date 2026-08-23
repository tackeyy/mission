"""Issue #615: typed Stop-hook guard decisions and judgment-free dispatch."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK = REPO_ROOT / "scripts" / "mission-stop-guard.sh"
EXPECTED_COMMAND_KINDS = {
    "none", "mark-halt", "cleanup-stale", "stop-guard-observe",
}


def _guard_module():
    from mission_application import runtime_guard

    return runtime_guard


def _candidate(**changes):
    guard = _guard_module()
    values = {
        "state_file": "/project/.mission-state/sessions/cc-own.json",
        "session_id": "cc-own",
        "project_root": "/project",
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "halt_category": "",
        "lease_owner_session_id": None,
        "lease_expires_at": None,
        "pid": 4242,
        "pid_alive": True,
        "heartbeat_at": None,
        "last_progress_at": None,
        "last_activity_at": None,
        "updated_at": "2026-08-23T00:00:00Z",
        "awaiting_user": False,
        "iteration": 1,
        "phase": "executing",
        "score_history_count": 0,
        "last_score": None,
        "threshold": 4.0,
        "mission": "neutral fixture mission",
        "issue_ref": None,
        "read_error": None,
    }
    values.update(changes)
    return guard.GuardSessionFact(**values)


def _request(*candidates, **changes):
    guard = _guard_module()
    values = {
        "project_root": "/project",
        "stop_hook_active": False,
        "mission_session_id": None,
        "claude_session_id": "own",
        "codex_thread_id": None,
        "hook_pid": 4242,
        "candidates": tuple(candidates) or (_candidate(),),
        "observed_at": "2026-08-23T01:00:00Z",
        "observed_epoch": 1000,
        "stale_halt_seconds_raw": None,
        "planning_warn_iterations_raw": None,
        "observe_ttl_seconds_raw": None,
        "pending_breakdown": "cc-own(incomplete)",
        "pending_digest": "a" * 64,
        "processed_orphan_state_files": (),
    }
    values.update(changes)
    return guard.GuardRequest(**values)


def test_guard_types_are_immutable_and_command_enum_is_closed():
    guard = _guard_module()

    assert {item.value for item in guard.GuardFindingKind} == {
        "none", "stale", "awaiting-user", "lease-expired", "orphan", "indeterminate",
    }
    assert {item.value for item in guard.SessionSelectionReason} == {
        "none", "exact-session-id", "exact-pid-fenced", "legacy-pid-fallback",
        "envless-first-eligible", "no-eligible-session", "authoritative-state-unreadable",
    }
    assert {item.value for item in guard.LeaseStatus} == {
        "absent", "unexpired", "expired", "invalid",
    }
    assert {item.value for item in guard.GuardCommandKind} == EXPECTED_COMMAND_KINDS

    variants = (
        guard.NoCommand(),
        guard.MarkHaltCommand(
            cwd="/project", session_id="s", reason="stale", category=guard.GuardHaltCategory.STALE,
            origin=guard.GuardFindingKind.STALE,
        ),
        guard.CleanupStaleExecuteCommand(
            root="/project", expected_state_file="/project/sessions/s.json", execute=True,
        ),
        guard.StopGuardObserveCommand(
            session_id="s", digest="a" * 64, now_epoch=1, ttl_seconds=600,
            attempt=0, max_attempts=3,
        ),
    )
    assert {item.kind.value for item in variants} == EXPECTED_COMMAND_KINDS
    with pytest.raises(dataclasses.FrozenInstanceError):
        variants[0].kind = guard.GuardCommandKind.MARK_HALT
    with pytest.raises(ValueError, match="cleanup-stale-execute-required"):
        guard.CleanupStaleExecuteCommand(
            root="/project", expected_state_file="/project/sessions/s.json", execute=False,
        )


@pytest.mark.parametrize(
    "request_factory, expected_file, expected_reason",
    [
        (
            lambda: _request(_candidate()),
            "/project/.mission-state/sessions/cc-own.json",
            "exact-session-id",
        ),
        (
            lambda: _request(
                _candidate(
                    state_file="/project/.mission-state/sessions/pid-4242.json",
                    session_id="pid-4242", lease_owner_session_id="pid-4242",
                    lease_expires_at="2026-08-23T02:00:00Z",
                ),
                claude_session_id=None,
            ),
            "/project/.mission-state/sessions/pid-4242.json",
            "exact-pid-fenced",
        ),
        (
            lambda: _request(
                _candidate(
                    state_file="/project/.mission-state/sessions/pid-4242.json",
                    session_id="pid-4242", lease_owner_session_id="pid-9999",
                    lease_expires_at="2026-08-23T02:00:00Z",
                ),
                _candidate(
                    state_file="/project/.mission-state/sessions/legacy.json",
                    session_id="legacy", pid=4242,
                ),
                claude_session_id=None,
            ),
            "/project/.mission-state/sessions/legacy.json",
            "legacy-pid-fallback",
        ),
        (
            lambda: _request(
                _candidate(
                    state_file="/project/.mission-state/sessions/pid-4242.json",
                    session_id="pid-4242", passes=True, loop_active=False,
                    lease_owner_session_id="pid-4242",
                    lease_expires_at="2026-08-23T02:00:00Z",
                ),
                _candidate(
                    state_file="/project/.mission-state/sessions/legacy.json",
                    session_id="legacy", pid=4242,
                ),
                claude_session_id=None,
            ),
            "/project/.mission-state/sessions/legacy.json",
            "legacy-pid-fallback",
        ),
        (
            lambda: _request(
                _candidate(
                    state_file="/project/.mission-state/sessions/pid-4242.json",
                    session_id="pid-4242", lease_owner_session_id="pid-4242",
                    lease_expires_at="2026-08-23T02:00:00Z",
                ),
                _candidate(
                    state_file="/project/.mission-state/sessions/aaa-legacy.json",
                    session_id="aaa-legacy", pid=4242,
                ),
                claude_session_id=None,
            ),
            "/project/.mission-state/sessions/pid-4242.json",
            "exact-pid-fenced",
        ),
        (
            lambda: _request(
                _candidate(session_id="foreign"),
                claude_session_id="missing",
            ),
            None,
            "no-eligible-session",
        ),
    ],
)
def test_session_selection_table(request_factory, expected_file, expected_reason):
    decision = _guard_module().decide_stop_guard(request_factory())

    assert decision.selection.state_file == expected_file
    assert decision.selection.reason.value == expected_reason


def test_selection_returns_considered_order_and_unreadable_is_fail_closed():
    unreadable = _candidate(
        state_file="/project/.mission-state/sessions/cc-own.json",
        read_error="invalid authoritative state",
    )
    later = _candidate(
        state_file="/project/.mission-state/sessions/zz-later.json",
        session_id="zz-later",
    )

    decision = _guard_module().decide_stop_guard(_request(unreadable, later))

    assert decision.selection.state_file == unreadable.state_file
    assert decision.selection.session_id == "cc-own"
    assert decision.selection.reason.value == "authoritative-state-unreadable"
    assert decision.selection.considered_state_files == (unreadable.state_file,)
    assert decision.finding.value == "indeterminate"
    assert decision.reply.emit is True
    assert decision.reason_code == "authoritative-state-unreadable"


@pytest.mark.parametrize(
    "changes, raw_halt, expected_field, expected_value, expected_age, expected_finding, expected_kind",
    [
        (
            {"heartbeat_at": "2026-08-23T00:59:00Z", "updated_at": "2020-01-01T00:00:00Z"},
            None, "heartbeat_at", "2026-08-23T00:59:00Z", 60, "none", "stop-guard-observe",
        ),
        (
            {"heartbeat_at": None, "last_progress_at": "2026-08-23T00:58:00Z"},
            None, "last_progress_at", "2026-08-23T00:58:00Z", 120, "none", "stop-guard-observe",
        ),
        (
            {"last_progress_at": None, "last_activity_at": "2026-08-23T00:57:00Z"},
            None, "last_activity_at", "2026-08-23T00:57:00Z", 180, "none", "stop-guard-observe",
        ),
        (
            {"last_activity_at": None, "updated_at": "2026-08-23T00:56:00Z"},
            None, "updated_at", "2026-08-23T00:56:00Z", 240, "none", "stop-guard-observe",
        ),
        (
            {"heartbeat_at": "invalid", "updated_at": "2020-01-01T00:00:00Z"},
            None, "heartbeat_at", "invalid", None, "indeterminate", "stop-guard-observe",
        ),
        (
            {"heartbeat_at": "2026-08-23T01:01:00Z"},
            None, "heartbeat_at", "2026-08-23T01:01:00Z", None, "indeterminate", "stop-guard-observe",
        ),
        (
            {"updated_at": "2026-08-23T00:00:00Z"},
            None, "updated_at", "2026-08-23T00:00:00Z", 3600, "none", "stop-guard-observe",
        ),
        (
            {"updated_at": "2026-08-22T23:59:59Z"},
            None, "updated_at", "2026-08-22T23:59:59Z", 3601, "none", "stop-guard-observe",
        ),
        (
            {"updated_at": "2026-08-22T22:00:00Z"},
            "10800", "updated_at", "2026-08-22T22:00:00Z", 10800, "none", "stop-guard-observe",
        ),
        (
            {"updated_at": "2026-08-22T21:59:59Z"},
            "10800", "updated_at", "2026-08-22T21:59:59Z", 10801, "stale", "mark-halt",
        ),
    ],
)
def test_freshness_priority_and_strict_boundaries(
    changes, raw_halt, expected_field, expected_value, expected_age,
    expected_finding, expected_kind,
):
    candidate = _candidate(**changes)
    decision = _guard_module().decide_stop_guard(
        _request(candidate, stale_halt_seconds_raw=raw_halt)
    )

    freshness = decision.evidence.freshness
    assert freshness.timestamp_field == expected_field
    assert freshness.timestamp_value == expected_value
    assert freshness.age_sec == expected_age
    assert decision.finding.value == expected_finding
    assert decision.command.kind.value == expected_kind
    if expected_age == 3601:
        assert decision.reply.reason.startswith("[WARN: state が 60分 未更新")


@pytest.mark.parametrize(
    "field, raw, expected",
    [
        ("stale_halt_seconds_raw", None, 10800),
        ("stale_halt_seconds_raw", "invalid", 10800),
        ("stale_halt_seconds_raw", "-1", 10800),
        ("stale_halt_seconds_raw", "299", 10800),
        ("stale_halt_seconds_raw", "300", 300),
        ("planning_warn_iterations_raw", "invalid", 3),
        ("planning_warn_iterations_raw", "0", 3),
        ("planning_warn_iterations_raw", "1", 1),
        ("observe_ttl_seconds_raw", "invalid", 600),
        ("observe_ttl_seconds_raw", "0", 600),
        ("observe_ttl_seconds_raw", "1", 1),
    ],
)
def test_policy_parsing_preserves_existing_defaults_and_clamps(field, raw, expected):
    guard = _guard_module()
    policy = guard.normalize_guard_policy(**{field: raw})

    name = {
        "stale_halt_seconds_raw": "halt_after_sec",
        "planning_warn_iterations_raw": "planning_warn_iterations",
        "observe_ttl_seconds_raw": "observe_ttl_seconds",
    }[field]
    assert getattr(policy, name) == expected


@pytest.mark.parametrize(
    "changes, finding, command_kind, lease_status",
    [
        (
            {"lease_owner_session_id": "cc-own", "lease_expires_at": "2026-08-23T02:00:00Z"},
            "stale", "stop-guard-observe", "unexpired",
        ),
        (
            {"awaiting_user": True},
            "awaiting-user", "stop-guard-observe", "absent",
        ),
        (
            {"lease_owner_session_id": "cc-own", "lease_expires_at": "2026-08-23T00:00:00Z"},
            "lease-expired", "cleanup-stale", "expired",
        ),
        (
            {"lease_owner_session_id": "cc-own", "lease_expires_at": "invalid"},
            "indeterminate", "cleanup-stale", "invalid",
        ),
        ({}, "stale", "mark-halt", "absent"),
    ],
)
def test_stale_priority_matrix(changes, finding, command_kind, lease_status):
    candidate = _candidate(updated_at="2020-01-01T00:00:00Z", **changes)
    decision = _guard_module().decide_stop_guard(_request(candidate))

    assert decision.finding.value == finding
    assert decision.command.kind.value == command_kind
    assert decision.evidence.lease.status.value == lease_status
    assert decision.evidence.awaiting_user is changes.get("awaiting_user", False)
    if command_kind == "cleanup-stale":
        assert decision.command.execute is True
        assert decision.command.expected_state_file == candidate.state_file


@pytest.mark.parametrize(
    "request_changes, candidate_changes, expected",
    [
        (
            {"mission_session_id": "own"},
            {
                "state_file": "/project/.mission-state/sessions/own.json",
                "session_id": "own", "pid": 9999, "pid_alive": False,
            },
            False,
        ),
        ({}, {"lease_owner_session_id": "leased", "lease_expires_at": "2026-08-23T02:00:00Z", "pid": 9999, "pid_alive": False}, False),
        ({}, {"pid": None, "pid_alive": None}, False),
        ({}, {"pid": 0, "pid_alive": False}, False),
        ({}, {"pid": 9999, "pid_alive": True}, False),
        ({}, {"pid": 9999, "pid_alive": False}, True),
    ],
)
def test_only_envless_lease_less_dead_positive_pid_is_orphan(
    request_changes, candidate_changes, expected,
):
    candidate = _candidate(**candidate_changes)
    values = {
        "claude_session_id": None,
        "hook_pid": None,
        **request_changes,
    }
    decision = _guard_module().decide_stop_guard(_request(candidate, **values))

    assert (decision.finding.value == "orphan") is expected
    assert (decision.command.kind.value == "mark-halt") is expected
    assert decision.evidence.orphan.pid == candidate_changes.get("pid")


def test_closed_commands_carry_complete_typed_arguments_without_argv():
    guard = _guard_module()
    stale = guard.decide_stop_guard(
        _request(_candidate(updated_at="2026-08-22T21:00:00Z"))
    )
    assert stale.command == guard.MarkHaltCommand(
        cwd="/project",
        session_id="cc-own",
        reason="stale: auto-halted after 240m idle",
        category=guard.GuardHaltCategory.STALE,
        origin=guard.GuardFindingKind.STALE,
    )

    cleanup = guard.decide_stop_guard(
        _request(_candidate(
            updated_at="2020-01-01T00:00:00Z",
            lease_owner_session_id="cc-own",
            lease_expires_at="2020-01-01T00:15:00Z",
        ))
    )
    assert cleanup.command.root == "/project"
    assert cleanup.command.expected_state_file.endswith("/cc-own.json")
    assert cleanup.command.execute is True

    observe = guard.decide_stop_guard(_request(_candidate()))
    assert observe.command == guard.StopGuardObserveCommand(
        session_id="cc-own", digest="a" * 64, now_epoch=1000,
        ttl_seconds=600, attempt=0, max_attempts=3,
    )
    for command in (stale.command, cleanup.command, observe.command):
        assert not hasattr(command, "argv")


def test_mark_halt_receipt_preserves_stale_failure_and_orphan_failure_difference():
    guard = _guard_module()
    stale = guard.decide_stop_guard(
        _request(_candidate(updated_at="2020-01-01T00:00:00Z"))
    )
    stale_success = guard.resolve_guard_command_receipt(
        stale, guard.GuardCommandReceipt(stale.decision_id, stale.command.kind, 0, "")
    )
    stale_failure = guard.resolve_guard_command_receipt(
        stale, guard.GuardCommandReceipt(stale.decision_id, stale.command.kind, 1, "")
    )
    assert stale_success.reply.emit is False
    assert stale_failure.reply.emit is True
    assert "cleanup-stale" in stale_failure.reply.reason

    orphan = guard.decide_stop_guard(_request(
        _candidate(pid=9999, pid_alive=False),
        claude_session_id=None, hook_pid=None,
    ))
    orphan_failure = guard.resolve_guard_command_receipt(
        orphan, guard.GuardCommandReceipt(orphan.decision_id, orphan.command.kind, 1, "")
    )
    assert orphan_failure.reply.emit is False
    assert orphan.selection.state_file in orphan_failure.continuation.processed_orphan_state_files


@pytest.mark.parametrize(
    "exit_code, stdout, emits",
    [
        (0, json.dumps({"halted": [{"path": "/project/.mission-state/sessions/cc-own.json"}]}), False),
        (0, json.dumps({"halted": [{"path": "/project/.mission-state/sessions/other.json"}]}), True),
        (0, json.dumps({"skipped": [{"path": "/project/.mission-state/sessions/cc-own.json"}]}), True),
        (0, "not-json", True),
        (1, "", True),
    ],
)
def test_cleanup_receipt_requires_the_expected_halted_path(exit_code, stdout, emits):
    guard = _guard_module()
    prior = guard.decide_stop_guard(_request(_candidate(
        updated_at="2020-01-01T00:00:00Z",
        lease_owner_session_id="cc-own", lease_expires_at="2020-01-01T00:15:00Z",
    )))
    result = guard.resolve_guard_command_receipt(
        prior, guard.GuardCommandReceipt(prior.decision_id, prior.command.kind, exit_code, stdout)
    )

    assert result.reply.emit is emits


def test_observe_receipt_selects_mode_retries_and_rejects_binding_mismatch():
    guard = _guard_module()
    prior = guard.decide_stop_guard(_request(_candidate()))
    detail = guard.resolve_guard_command_receipt(
        prior, guard.GuardCommandReceipt(prior.decision_id, prior.command.kind, 0, '{"mode":"detail"}')
    )
    heartbeat = guard.resolve_guard_command_receipt(
        prior, guard.GuardCommandReceipt(prior.decision_id, prior.command.kind, 0, '{"mode":"heartbeat"}')
    )
    retry = guard.resolve_guard_command_receipt(
        prior, guard.GuardCommandReceipt(prior.decision_id, prior.command.kind, 1, "")
    )
    assert detail.reply.reason == prior.display_reason
    assert "mission heartbeat" in heartbeat.reply.reason
    assert retry.command.attempt == 1

    second = guard.resolve_guard_command_receipt(
        retry, guard.GuardCommandReceipt(retry.decision_id, retry.command.kind, 1, "")
    )
    exhausted = guard.resolve_guard_command_receipt(
        second, guard.GuardCommandReceipt(second.decision_id, second.command.kind, 1, "")
    )
    assert exhausted.command.kind.value == "none"
    assert exhausted.reply.reason == prior.display_reason

    with pytest.raises(ValueError, match="guard-receipt-decision-mismatch"):
        guard.resolve_guard_command_receipt(
            prior, guard.GuardCommandReceipt("wrong", prior.command.kind, 0, "{}")
        )
    with pytest.raises(ValueError, match="guard-receipt-command-mismatch"):
        guard.resolve_guard_command_receipt(
            prior, guard.GuardCommandReceipt(prior.decision_id, guard.GuardCommandKind.MARK_HALT, 0, "")
        )


def _write_active_state(root: Path, session_id: str = "cc-own", **changes) -> Path:
    sessions = root / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    state = {
        "session_id": session_id,
        "mission": "neutral root fixture",
        "mission_id": "guard-fixture",
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "halt_category": "",
        "phase": "executing",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [],
        "pid": os.getpid(),
        "project_root": str(root),
        "updated_at": "2026-08-23T00:59:00Z",
    }
    state.update(changes)
    path = sessions / (session_id + ".json")
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


def test_stop_verdict_root_mode_serializes_typed_decision_and_keeps_legacy_projection(
    tmp_path, raw_run_cli,
):
    state_file = _write_active_state(tmp_path)
    result = raw_run_cli(
        "stop-verdict", "--hook-input", "-", "--json",
        cwd=tmp_path,
        input_text=json.dumps({"stop_hook_active": False, "cwd": str(tmp_path)}),
        env_extra={
            "MISSION_SESSION_ID": None,
            "CLAUDE_CODE_SESSION_ID": "own",
            "MISSION_STATE_NOW": "2026-08-23T01:00:00Z",
            "MISSION_STOP_GUARD_NOW_EPOCH": "1000",
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "mission-stop-verdict/1"
    assert payload["decision"] == "block"
    assert payload["reason"] == "active-unfinished"
    assert payload["outcome_kind"] == "expected-gate"
    assert payload["session_id"] == "cc-own"
    assert payload["selection"] == {
        "state_file": str(state_file),
        "session_id": "cc-own",
        "reason": "exact-session-id",
        "considered_state_files": [str(state_file)],
    }
    assert payload["command"]["kind"] == "stop-guard-observe"
    assert payload["evidence"]["freshness"]["timestamp_field"] == "updated_at"
    assert payload["evidence"]["freshness"]["age_sec"] == 60
    assert payload["shell_text"].startswith('{"decision": "block"')
    assert {"display_reason", "planning_warning", "pending_digest", "lease_present",
            "lease_unexpired", "awaiting_user", "orphan_pid"}.issubset(payload)


def test_stop_verdict_single_state_mode_keeps_terminal_and_error_contracts(
    tmp_path, raw_run_cli,
):
    terminal = _write_active_state(
        tmp_path, passes=True, loop_active=False, updated_at="2026-08-23T00:59:00Z"
    )
    result = raw_run_cli("stop-verdict", "--state-file", str(terminal), "--json", cwd=tmp_path)
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert (payload["schema"], payload["decision"], payload["reason"], payload["outcome_kind"]) == (
        "mission-stop-verdict/1", "skip", "passes-true", "completed-pass",
    )
    assert payload["command"]["kind"] == "none"

    broken = terminal.with_name("broken.json")
    broken.write_text("{", encoding="utf-8")
    failed = raw_run_cli("stop-verdict", "--state-file", str(broken), "--json", cwd=tmp_path)
    error = json.loads(failed.stderr)
    assert failed.returncode == 2
    assert set(error) == {"schema", "decision", "reason", "error"}
    assert error["reason"] == "authoritative-state-unreadable"


def test_stop_verdict_root_mode_turns_unreadable_candidate_into_typed_fail_closed(
    tmp_path, raw_run_cli,
):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    broken = sessions / "cc-own.json"
    broken.write_text("{", encoding="utf-8")

    result = raw_run_cli(
        "stop-verdict", "--hook-input", "-", "--json", cwd=tmp_path,
        input_text=json.dumps({"stop_hook_active": False, "cwd": str(tmp_path)}),
        env_extra={"MISSION_SESSION_ID": None, "CLAUDE_CODE_SESSION_ID": "own"},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert payload["reason"] == "authoritative-state-unreadable"
    assert payload["finding"] == "indeterminate"
    assert payload["command"]["kind"] == "none"
    assert str(broken) in payload["shell_text"]


@dataclasses.dataclass(frozen=True)
class Violation:
    code: str
    source: str


_POLICY_NAMES = re.compile(
    r"(?:STALE|FRESH|AGE|LEASE|TTL|ITER|ATTEMPT|EPOCH|TIMESTAMP)", re.IGNORECASE
)
_COMMANDS = {"mark-halt", "cleanup-stale", "stop-guard-observe"}


def analyze_guard_shell(source: str) -> list[Violation]:
    """Conservative detector for policy or open command execution in the hook."""
    violations = []
    dispatch_start = source.find("# GUARD_DECISION_DISPATCH_BEGIN")
    dispatch_end = source.find("# GUARD_DECISION_DISPATCH_END")
    in_dispatch = dispatch_start >= 0 and dispatch_end > dispatch_start
    dispatch = source[dispatch_start:dispatch_end] if in_dispatch else ""

    if re.search(r"\$\(\([^\n]*\)\)", source):
        violations.append(Violation("shell-arithmetic", source))
    if re.search(r"(?:\[\[?|\btest\b)[^\n]*(?:-lt|-le|-gt|-ge)\b", source):
        violations.append(Violation("numeric-policy-comparison", source))
    if any(
        _POLICY_NAMES.search(line)
        and re.search(r"(?:=|:-)\s*[0-9]+", line)
        for line in source.splitlines()
    ):
        violations.append(Violation("policy-numeric-literal", source))
    if "date +%s" in source:
        violations.append(Violation("timestamp-calculation", source))
    if re.search(r"\[\[[^\n]*(?:LEASE|TIMESTAMP|EXPIRES)[^\n]*>[^\n]*\]\]", source):
        violations.append(Violation("timestamp-comparison", source))
    if re.search(r"\b(?:eval|bash\s+-c|sh\s+-c)\b", source) or re.search(
        r"(?:^|[;&|]\s*)\"\$\{?(?:COMMAND|ARGV)", source, re.MULTILINE
    ):
        violations.append(Violation("dynamic-command-execution", source))
    if "jq -n" in source:
        violations.append(Violation("jq-construction", source))
    for line in source.splitlines():
        dependency_probe = line.strip() == "if ! command -v jq >/dev/null 2>&1; then"
        dependency_error = line.strip() == (
            "printf '%s\\n' '{\"decision\":\"block\",\"reason\":\"mission Stop guard "
            "requires jq; state verdict is unavailable\",\"outcome_kind\":\"expected-gate\"}'"
        )
        if (
            re.search(r"\bjq\b", line)
            and "$GUARD_DECISION" not in line
            and not dependency_probe
            and not dependency_error
        ):
            violations.append(Violation("jq-input-not-guard-decision", line))
        if re.search(r"\bjq\b", line) and re.search(
            r"\.(?:loop_active|passes|halt_reason|updated_at|heartbeat_at|lease_|awaiting_user|orphan_pid)",
            line,
        ):
            violations.append(Violation("authoritative-jq-read", line))
        tokens = []
        try:
            tokens = shlex.split(line, comments=True, posix=True)
        except ValueError:
            pass
        for command in _COMMANDS:
            if re.search(r"(?<![A-Za-z0-9-])" + re.escape(command) + r"(?![A-Za-z0-9-])", line) and not (
                in_dispatch and dispatch_start <= source.find(line) < dispatch_end
            ):
                violations.append(Violation("command-outside-dispatch", line))
        if tokens and "python3" in tokens:
            for token in tokens:
                if token in {"resume", "reactivate"}:
                    violations.append(Violation("command-not-allowlisted", line))

    labels = set(re.findall(r"^\s{4}(none|mark-halt|cleanup-stale|stop-guard-observe)\)\s*$", dispatch, re.MULTILINE))
    if in_dispatch and labels != EXPECTED_COMMAND_KINDS:
        violations.append(Violation("dispatch-set-mismatch", dispatch))
    if not in_dispatch:
        violations.append(Violation("dispatch-set-mismatch", source))
    return violations


_POSITIVE_DISPATCH = """
# GUARD_DECISION_DISPATCH_BEGIN
case "$COMMAND_KIND" in
    none)
        printf '%s' "$SHELL_TEXT"
        ;;
    mark-halt)
        MISSION_SESSION_ID="$COMMAND_SESSION_ID" python3 "$MISSION_STATE_PY" mark-halt --reason "$COMMAND_REASON" --category stale
        ;;
    cleanup-stale)
        python3 "$MISSION_STATE_PY" cleanup-stale --root "$COMMAND_ROOT" --execute
        ;;
    stop-guard-observe)
        python3 "$MISSION_STATE_PY" stop-guard-observe --session-id "$COMMAND_SESSION_ID" --digest "$COMMAND_DIGEST" --now-epoch "$COMMAND_NOW" --ttl-seconds "$COMMAND_TTL"
        ;;
    *) exit 0 ;;
esac
# GUARD_DECISION_DISPATCH_END
"""


_SYNTHETIC_GUARD_VIOLATIONS = {
    "numeric-stale-default": ("STALE_SECONDS=${X:-10800}", "policy-numeric-literal"),
    "numeric-age-compare": ('[ "$AGE_SEC" -gt 3600 ]', "numeric-policy-comparison"),
    "arithmetic-minutes": ("MINS=$((AGE_SEC / 60))", "shell-arithmetic"),
    "timestamp-compare": ('[[ "$LEASE_EXPIRES_AT" > "$NOW" ]]', "timestamp-comparison"),
    "date-epoch": ("NOW=$(date +%s)", "timestamp-calculation"),
    "branch-selects-command": ("if true; then python3 tool mark-halt; fi", "command-outside-dispatch"),
    "unexpected-command": (_POSITIVE_DISPATCH.replace("python3 \"$MISSION_STATE_PY\" cleanup-stale", "python3 \"$MISSION_STATE_PY\" resume"), "command-not-allowlisted"),
    "dynamic-command": ('eval "$COMMAND"', "dynamic-command-execution"),
    "jq-state-file": ('jq -r \'.updated_at\' "$sf"', "authoritative-jq-read"),
    "jq-input": ('printf \'%s\' "$INPUT" | jq -r \'.cwd\'', "jq-input-not-guard-decision"),
    "jq-construction": ("jq -n '{decision:\"block\"}'", "jq-construction"),
    "missing-arm": (_POSITIVE_DISPATCH.replace("    none)\n        printf '%s' \"$SHELL_TEXT\"\n        ;;\n", ""), "dispatch-set-mismatch"),
}


def test_static_analyzer_accepts_minimal_dispatch_and_detects_every_synthetic_violation():
    assert analyze_guard_shell(_POSITIVE_DISPATCH) == []
    for fixture_id, (source, expected_code) in _SYNTHETIC_GUARD_VIOLATIONS.items():
        codes = {item.code for item in analyze_guard_shell(source)}
        assert expected_code in codes, (fixture_id, codes)


def test_canonical_hook_is_judgment_free_and_dispatches_the_closed_command_set():
    violations = analyze_guard_shell(HOOK.read_text(encoding="utf-8"))

    assert violations == []
