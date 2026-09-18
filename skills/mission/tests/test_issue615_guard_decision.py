"""Issue #615: typed Stop-hook guard decisions and judgment-free dispatch."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import subprocess
import tempfile
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
    # The verdict is settled before it is printed (#779): the guard applies the
    # observation in its own process, so what reaches the hook is the decision
    # after the command, not an instruction to run one.  A `stop-guard-observe`
    # here would mean the hook is expected to apply it -- the loop this change
    # removed.
    assert payload["command"]["kind"] == "none"
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


# bash accepts far more in a function name than an identifier: `_q-x` is legal.
# Restricting the name to `[A-Za-z0-9_]` let a wrapper hide behind a hyphen, so
# the name runs to the next character that would end a word in shell.
_NAME = r"[^\s;&|()<>{}'\"$`\\=]+"
_FUNCTION_HEADER = re.compile(
    r"(?m)^[ \t]*(?:function[ \t]+(?P<kw>" + _NAME + r")[ \t]*(?:\([ \t]*\))?"
    r"|(?P<paren>" + _NAME + r")[ \t]*\([ \t]*\))"
)

# `alias _al=_bounded` and `_q=_bounded` both make a second name reach the CLI.
_ALIAS = re.compile(r"(?m)^[ \t]*alias[ \t]+(?P<name>" + _NAME + r")=(?P<value>\S+)")
_ASSIGNMENT = re.compile(r"(?m)^[ \t]*(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>\S+)")


def _state_cli_callers(source: str) -> set[str]:
    """Names that may reach `mission-state.py`: the variable, and every function.

    The hook does not call the CLI directly; it defines a function that applies
    the timeout and calls through it.  A check that only looks for `python3`
    on the line therefore sees nothing -- `_mission_state_bounded resume` was
    accepted by exactly that gap.

    **Function bodies are not read.**  Earlier versions decided whether a
    function was a wrapper by looking inside it, which meant finding where the
    body ended, and every attempt at that leaked: a nested brace group, a `}`
    in a heredoc, and an escaped quote in `$'...'` each ended the body early,
    dropping the forwarding call so the wrapper went unrecognised.  All three
    are valid shell (`bash -n` accepts them), and each one re-opened the same
    hole -- a second `stop-verdict` reaching the hook unreported, which is the
    loop #779 removed coming back by another name.

    Locating the end of a shell construct needs a shell parser, and this check
    does not have one.  So it stops needing one: **every function defined in
    the file counts as a caller**, whatever its body.  The hook defines exactly
    one function, and its shape is "call the CLI once and print what comes
    back", so this costs nothing here and closes the whole class.

    Aliases and assignments give a name a second name (#796), so they are
    followed to a fixed point: `alias _al=_bounded` and `_q=_bounded` each let
    the CLI be reached under a spelling the definitions alone do not show.

    It errs toward reporting.  A helper called with a literal argument is
    reported even when it never touches the CLI, and that is the direction to
    fail in -- CI says so on the first push, rather than a bypass sitting
    unnoticed.
    """
    callers = {"$MISSION_STATE_PY", "${MISSION_STATE_PY}"}
    for header in _FUNCTION_HEADER.finditer(source):
        callers.add(header.group("kw") or header.group("paren"))
    growing = True
    while growing:
        growing = False
        for pattern in (_ALIAS, _ASSIGNMENT):
            for match in pattern.finditer(source):
                value = match.group("value").strip("\"'")
                if value not in callers:
                    continue
                name = match.group("name")
                # An assignment is reached through the variable, an alias by
                # its own name.
                spellings = (
                    {name} if pattern is _ALIAS
                    else {"$" + name, "${" + name + "}"}
                )
                if spellings <= callers:
                    continue
                callers |= spellings
                growing = True
    return callers


def _command_fragments(line: str) -> list[str]:
    """Split a line wherever bash can start a new command.

    `shlex` alone reads `x=$(_bounded resume)` as the words `x=$(_bounded` and
    `resume)`, so the caller never appears as a word and the call is missed
    (#796 review round 3).  Command substitution, backticks, and the operators
    `;` `|` `&` all begin a command, and each fragment is read as its own line.
    `$(` needs no case of its own: the `(` ends the fragment either way, and a
    branch no mutation can distinguish is a branch that rots.

    **Single quotes are the one thing that stops it.**  `'$(_bounded resume)'`
    is data and runs nothing, while `"$(_bounded resume)"` runs -- the same
    characters, told apart only by which quote encloses them.  The scan tracks
    that, so neither is guessed.
    """
    fragments: list[str] = []
    buffer: list[str] = []
    in_single = False
    in_double = False
    index = 0
    end = len(line)

    def cut() -> None:
        fragments.append("".join(buffer))
        buffer.clear()

    while index < end:
        char = line[index]
        if in_single:
            buffer.append(char)
            if char == "'":
                in_single = False
            index += 1
            continue
        if char == "\\" and index + 1 < end:
            buffer.append(char)
            buffer.append(line[index + 1])
            index += 2
            continue
        if char == "'":
            in_single = True
            buffer.append(char)
            index += 1
            continue
        if char == '"':
            in_double = not in_double
            buffer.append(char)
            index += 1
            continue
        if char == "`":
            cut()
            index += 1
            continue
        if char in "()" or (not in_double and char in ";|&"):
            cut()
            index += 1
            continue
        buffer.append(char)
        index += 1
    cut()
    return [fragment for fragment in fragments if fragment.strip()]


def _state_cli_invocations(source: str, callers: set[str]) -> list[tuple[str, str]]:
    """Return (line, first argument) for every call of the state CLI.

    **Words are built with `shlex`, not matched in the raw text.**  Two earlier
    shapes came from reading the text instead of the words.  Matching the name
    literally missed `_mission_state_'bounded' resume`, which bash joins into
    one word and runs.  Deleting every quote first caught that but reported
    `printf '%s' "_mission_state_'bounded' resume"`, where the same characters
    are data inside a string and nothing runs.  `shlex.split` makes the same
    words bash does, so both come out right without a shell parser.

    The forwarding call inside a wrapper -- the one whose argument is `"$@"` --
    is what makes it a wrapper, so it is exempt.  **Only the CLI path itself
    may forward.**  A function name followed by `"$@"` is counted, because
    `"$@"` says nothing about what is being passed:

        set -- resume
        _mission_state_bounded "$@"

    That reaches the CLI with `resume` and used to be exempt under the same
    rule that exempts the wrapper's own line (#796).  The hook forwards only
    through `python3 "$MISSION_STATE_PY" "$@"`, so narrowing the exemption
    costs it nothing.

    An argument that is not a literal is reported rather than ignored:
    `helper "$CMD"` asks for something this check cannot read, and
    deny-by-default means refusing what it cannot read.
    """
    forwarding = {"$MISSION_STATE_PY", "${MISSION_STATE_PY}"}
    joined = re.sub(r"\\\n\s*", " ", source)
    invocations: list[tuple[str, str]] = []
    for raw_line in joined.splitlines():
      for fragment in _command_fragments(raw_line):
        try:
            words = shlex.split(fragment)
        except ValueError:
            # An unbalanced quote: the line cannot be read as words.  Fall back
            # to the raw split rather than skipping it, so an unreadable line
            # cannot be used to hide a call.
            words = fragment.split()
        for index, word in enumerate(words):
            if word not in callers:
                continue
            if index and words[index - 1] == "function":
                # `function _w { ... }` -- a definition, not a call.  Only the
                # keyword form reaches here; `_w() {` makes the word `_w()`,
                # which is not a caller.  Excluding every `{` instead let a
                # real call pass its own `{` (#796 review round 2).
                continue
            if index + 1 >= len(words):
                continue
            argument = words[index + 1].rstrip(";&|")
            if argument == "$@" and word in forwarding:
                # The wrapper forwarding its own arguments to the CLI.
                continue
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", argument):
                invocations.append((raw_line, "<not-a-literal>"))
                continue
            invocations.append((raw_line, argument))
    return invocations


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
        # The hook applies nothing now (#779): the commands are applied inside
        # `stop-verdict`.  So the question is no longer "is this command inside
        # the dispatch block" but "is any subcommand other than `stop-verdict`
        # here at all".
        #
        # Deny by default rather than by allowlist.  An allowlist passes a
        # subcommand nobody has added to it yet, which is exactly the shape a
        # future change would take.
        for command in _COMMANDS:
            if re.search(r"(?<![A-Za-z0-9-])" + re.escape(command) + r"(?![A-Za-z0-9-])", line):
                violations.append(Violation("command-outside-dispatch", line))

    # Deny by default, at the call sites rather than by name.  Enumerating the
    # subcommands and forbidding all but one looked equivalent, but it read only
    # lines containing `python3` -- and the hook calls through a wrapper, so
    # `_mission_state_bounded resume` passed.  Asking "what does each call of
    # the CLI ask for" has no such gap, and needs no list to keep in step.
    callers = _state_cli_callers(source)
    invocations = _state_cli_invocations(source, callers)
    for line, subcommand in invocations:
        if subcommand != "stop-verdict":
            violations.append(Violation("command-not-allowlisted", line))
    if len(invocations) > 1:
        violations.append(Violation("command-not-allowlisted", source))

    # The dispatch block is gone, and its absence is the property now: if it
    # returns, the loop it belonged to has returned with it.
    #
    # Narrow to branching on the *decision's command*.  The hook still has an
    # unrelated `case` that validates the timeout string, and forbidding every
    # `case` would forbid that too -- a check that fails for the wrong reason
    # gets relaxed, and then it stops checking the right one.
    if (
        "GUARD_DECISION_DISPATCH_BEGIN" in source
        or "COMMAND_KIND" in source
        or re.search(r"case\s+\"?\$\{?(?:COMMAND|GUARD)", source)
    ):
        violations.append(Violation("dispatch-block-returned", source))
    return violations


# What the hook looks like now: one call, and the text it returns (#779).
_POSITIVE_DISPATCH = """
if ! GUARD_DECISION=$(printf '%s' "$INPUT" | _mission_state_bounded stop-verdict --hook-input - --json); then
    printf '%s\\n' '{"decision":"block"}'
    exit 0
fi
if ! SHELL_TEXT=$(printf '%s' "$GUARD_DECISION" | jq -er '.shell_text'); then
    printf '%s\\n' '{"decision":"block"}'
    exit 0
fi
printf '%s' "$SHELL_TEXT"
"""


_SYNTHETIC_GUARD_VIOLATIONS = {
    "numeric-stale-default": ("STALE_SECONDS=${X:-10800}", "policy-numeric-literal"),
    "numeric-age-compare": ('[ "$AGE_SEC" -gt 3600 ]', "numeric-policy-comparison"),
    "arithmetic-minutes": ("MINS=$((AGE_SEC / 60))", "shell-arithmetic"),
    "timestamp-compare": ('[[ "$LEASE_EXPIRES_AT" > "$NOW" ]]', "timestamp-comparison"),
    "date-epoch": ("NOW=$(date +%s)", "timestamp-calculation"),
    # The hook applies nothing now, so naming a command at all is the
    # violation -- inside a branch or not.
    "branch-selects-command": ("if true; then python3 tool mark-halt; fi", "command-outside-dispatch"),
    "command-at-top-level": ('python3 "$MISSION_STATE_PY" cleanup-stale --root "$ROOT" --execute', "command-outside-dispatch"),
    # Deny-by-default: `resume` was named before, but so is every other
    # subcommand that is not `stop-verdict`, including ones added later.
    "unexpected-command": ('python3 "$MISSION_STATE_PY" resume', "command-not-allowlisted"),
    "unlisted-subcommand": ('python3 "$MISSION_STATE_PY" closeout', "command-not-allowlisted"),
    "dynamic-command": ('eval "$COMMAND"', "dynamic-command-execution"),
    "jq-state-file": ("jq -r '.updated_at' \"$sf\"", "authoritative-jq-read"),
    "jq-input": ("printf '%s' \"$INPUT\" | jq -r '.cwd'", "jq-input-not-guard-decision"),
    "jq-construction": ("jq -n '{decision:\"block\"}'", "jq-construction"),
    # The loop coming back is itself the violation now.
    "dispatch-block-returned": ('case "$COMMAND_KIND" in\n    none) ;;\nesac', "dispatch-block-returned"),
    "dispatch-marker-returned": ("# GUARD_DECISION_DISPATCH_BEGIN", "dispatch-block-returned"),
}


def test_static_analyzer_accepts_minimal_dispatch_and_detects_every_synthetic_violation():
    assert analyze_guard_shell(_POSITIVE_DISPATCH) == []
    for fixture_id, (source, expected_code) in _SYNTHETIC_GUARD_VIOLATIONS.items():
        codes = {item.code for item in analyze_guard_shell(source)}
        assert expected_code in codes, (fixture_id, codes)


def test_canonical_hook_is_judgment_free_and_dispatches_the_closed_command_set():
    violations = analyze_guard_shell(HOOK.read_text(encoding="utf-8"))

    assert violations == []


# --- The shapes the hook would actually use (#779 review) -------------------
#
# The first version of this check read only lines containing `python3`, and
# enumerated the CLI's subcommands to forbid all but one.  Both halves were
# wrong in the same direction: the hook calls through `_mission_state_bounded`,
# so no line it uses contains `python3`, and `_mission_state_bounded resume`
# passed a check whose whole purpose was to reject it.
#
# These mutate the *real* hook rather than a synthetic line, because the gap was
# in how a call is written there, not in the rule.
@pytest.mark.parametrize(
    "label,inserted",
    [
        ("through the wrapper", "_mission_state_bounded resume"),
        ("a subcommand nobody listed", "_mission_state_bounded closeout"),
        ("bypassing the wrapper", 'python3 "$MISSION_STATE_PY" resume'),
        ("the path with no quotes", "python3 $MISSION_STATE_PY reactivate"),
    ],
)
def test_the_hook_may_not_call_any_other_subcommand(label, inserted):
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"', inserted + '\nprintf \'%s\' "$SHELL_TEXT"'
    )
    codes = [violation.code for violation in analyze_guard_shell(source)]
    assert "command-not-allowlisted" in codes, label


def test_the_hook_may_not_call_the_verdict_twice():
    """Two calls is the loop coming back by another name."""
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        '_mission_state_bounded stop-verdict --hook-input - --json\n'
        'printf \'%s\' "$SHELL_TEXT"',
    )
    codes = [violation.code for violation in analyze_guard_shell(source)]
    assert "command-not-allowlisted" in codes


def test_a_nested_brace_group_does_not_end_the_function():
    """The body ends at the matching brace, not at the first line that is `}`.

    This is the shape that matters most: a nested brace group is valid shell
    (`bash -n` accepts it), and truncating the body there drops the forwarding
    call, so the wrapper is never recognised and a second `stop-verdict`
    reaches the hook unreported -- the loop this change removed, back under
    another name.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        '_outer() {\n'
        '  {\n'
        '    :\n'
        '  }\n'
        '  _mission_state_bounded "$@"\n'
        '}\n'
        '_outer stop-verdict --hook-input - --json\n'
        'printf \'%s\' "$SHELL_TEXT"',
    )
    codes = [violation.code for violation in analyze_guard_shell(source)]
    assert "command-not-allowlisted" in codes


def test_every_function_in_the_file_counts_as_a_caller():
    """Which is what makes the body irrelevant, however it is written.

    Three separate bypasses came from getting the end of a body wrong.  This
    fixes the class by not needing the end: each definition contributes its
    name, and the shapes that used to hide inside a body have nowhere left to
    hide.  Asserting through the hook would not show it -- the hook has one
    function, so the set looks the same either way.
    """
    source = (
        "_paren() {\n  :\n}\n"
        "function _keyword {\n  :\n}\n"
        "_next_line()\n{\n  :\n}\n"
        "_one_line() { :; }\n"
    )

    callers = _state_cli_callers(source)

    assert {"_paren", "_keyword", "_next_line", "_one_line"} <= callers, callers
    assert {"$MISSION_STATE_PY", "${MISSION_STATE_PY}"} <= callers, callers


def test_a_wrapper_of_a_wrapper_cannot_hide_a_second_verdict_call():
    """The count rule is what catches the removed loop returning by another name.

    Every other row in the table below fires on "not allowlisted", so none of
    them exercises `len(invocations) > 1`.  A second `stop-verdict` is
    allowlisted by name; only the count rejects it.  Reaching it through a
    wrapper of a wrapper defeated that rule until the caller set was closed
    over wrappers.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        '_w2() { _mission_state_bounded "$@"; }\n'
        '_w2 stop-verdict --hook-input - --json\n'
        'printf \'%s\' "$SHELL_TEXT"',
    )
    codes = [violation.code for violation in analyze_guard_shell(source)]
    assert "command-not-allowlisted" in codes


def test_defining_a_helper_without_calling_it_is_not_a_violation():
    """The caller set widens what counts as a caller, not what counts as a call.

    Without this, making every definition a caller could be "satisfied" by
    reporting the definitions themselves, which would reject the hook.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        "_w7() { printf '%s' hi; }\n"
        'printf \'%s\' "$SHELL_TEXT"',
    )
    assert analyze_guard_shell(source) == []


def test_an_escaped_space_is_not_a_call_so_it_needs_no_rule():
    """`_bounded\\ resume` reads as one word, and that word is not a command.

    The static check lets it through, and it was reported as a bypass.  Running
    it says otherwise: `\\ ` escapes the space, so bash looks for a command
    named `_mission_state_bounded resume`, finds none, and exits 127.  The
    wrapper is never entered, so the contract is not broken and there is
    nothing here to close.

    Kept as a test because the shape looks like the ones above, and without a
    record the next reader spends the same time re-deriving that it is inert.
    """
    script = (
        '_mission_state_bounded() { printf "INVOKED:%s" "$*"; }\n'
        "_mission_state_bounded\\ resume\n"
    )
    path = tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False)
    try:
        path.write(script)
        path.close()
        syntax = subprocess.run(["bash", "-n", path.name], capture_output=True, text=True)
        run = subprocess.run(["bash", path.name], capture_output=True, text=True)
    finally:
        os.unlink(path.name)

    assert syntax.returncode == 0, syntax.stderr
    assert run.returncode == 127, (run.returncode, run.stdout, run.stderr)
    assert "INVOKED" not in run.stdout, run.stdout


def test_a_line_shlex_cannot_read_is_still_searched():
    """An unbalanced quote must not become a place to hide a call.

    `shlex.split` raises on it, and skipping the line would make "unreadable"
    the easiest bypass to write.  The raw split is used instead, which reads
    less but reads something.  `bash -n` rejects such a line, so this is
    insurance rather than a shape anyone can run -- and insurance that is
    never exercised is the kind that turns out not to work.

    Asked of the extraction directly: through the hook, the unbalanced quote
    would also break lines after it, so the test would pass for another
    reason.
    """
    import shlex as _shlex

    line = "_mission_state_bounded resume '"
    with pytest.raises(ValueError):
        _shlex.split(line)

    invocations = _state_cli_invocations(line + "\n", {"_mission_state_bounded"})

    assert [subcommand for _line, subcommand in invocations] == ["resume"], invocations


def test_an_operator_inside_double_quotes_is_not_a_call():
    """`;` ends a command only outside quotes.

    `printf "%s" "a; _bounded resume"` prints the text and runs nothing.
    Ending the fragment at every `;` would report it -- a clean hook failing,
    which is how a check gets relaxed.  This pins the quote tracking that the
    operator rows above rely on.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        'printf "%s" "a; _mission_state_bounded resume"\n'
        'printf \'%s\' "$SHELL_TEXT"',
    )
    assert analyze_guard_shell(source) == []


def test_a_substitution_inside_single_quotes_is_not_a_call():
    """Which quote encloses it is the whole difference.

    `"$(_bounded resume)"` runs; `'$(_bounded resume)'` is data.  The
    characters are identical, so splitting at `$(` without tracking quotes
    would report the second -- a clean hook failing, which is how a check gets
    relaxed.  This pins the control for the rows above.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        "printf '%s' '$(_mission_state_bounded resume)'\n"
        'printf \'%s\' "$SHELL_TEXT"',
    )
    assert analyze_guard_shell(source) == []


def test_the_same_characters_inside_a_string_are_not_a_call():
    """Words are what bash runs; characters inside a string are data.

    `_mission_state_'bounded' resume` is one word and runs.  The same
    characters inside `printf '%s' "..."` are one argument to `printf`, and
    nothing runs.  Deleting every quote before matching cannot tell them
    apart and reported the second -- a clean hook failing the check, which is
    how a check gets relaxed.  Building words with `shlex` separates them.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        'printf \'%s\' "_mission_state_\'bounded\' resume"\n'
        'printf \'%s\' "$SHELL_TEXT"',
    )
    assert analyze_guard_shell(source) == []


def test_a_definition_header_alone_is_not_a_call():
    """`function _w { ... }` is a definition; counting it hollows out the rows.

    The name is followed by whitespace and `{`, which read as a call with the
    argument `{`.  That reports the definition on its own, so a row that
    defines *and* calls a function passes on the definition alone -- the call
    site, which is what the row exists to check, is never exercised.  Found by
    deleting the call line from the hyphen row and watching it still pass.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        'function _q-x { python3 "$MISSION_STATE_PY" "$@"; }\n'
        'printf \'%s\' "$SHELL_TEXT"',
    )
    assert analyze_guard_shell(source) == []


def test_a_second_wrapper_is_reported_even_before_it_is_called():
    """Only the CLI path may forward `"$@"`; a function name doing so counts.

    This is the cost of closing `set -- resume` + `_bounded "$@"` (#796):
    `"$@"` says nothing about what is being passed, so exempting it for
    function names exempts whatever the positional parameters were set to.
    The hook forwards only through `python3 "$MISSION_STATE_PY" "$@"`, so it
    pays nothing -- but a second wrapper is now reported on sight, before
    anyone calls it.  That is the direction to fail in, and writing it down
    keeps the next reader from filing it as a false positive.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        '_w8() { _mission_state_bounded "$@"; }\n'
        'printf \'%s\' "$SHELL_TEXT"',
    )
    codes = [violation.code for violation in analyze_guard_shell(source)]
    assert "command-not-allowlisted" in codes


def test_the_wrapper_definition_is_not_itself_a_call():
    """It forwards `"$@"`; counting it would report the hook as violating itself."""
    assert analyze_guard_shell(HOOK.read_text(encoding="utf-8")) == []


# The shapes a previous fix still let through.  Each was reported as passing a
# check whose purpose is to reject it.  The last four came from the independent
# checker: the caller set was seeded with one spelling of the variable, read
# only `name() {` as a definition, skipped a one-line body, and did not follow a
# wrapper of a wrapper.
@pytest.mark.parametrize(
    "label,inserted",
    [
        ("a variable as the subcommand", 'CMD=resume; _mission_state_bounded "$CMD"'),
        ("a continuation line", "_mission_state_bounded \\\n    resume"),
        ("hidden in another helper", "_other() {\n  _mission_state_bounded resume\n}"),
        ("hidden in another helper, direct", '_other() {\n  python3 "$MISSION_STATE_PY" resume\n}'),
        ("a wrapper of a wrapper", '_w2() { _mission_state_bounded "$@"; }\n_w2 resume'),
        ("the braced spelling of the variable", 'python3 "${MISSION_STATE_PY}" resume'),
        ("a function defined with the keyword",
         'function _w3 { python3 "$MISSION_STATE_PY" "$@"; }\n_w3 resume'),
        ("a function whose brace is on the next line",
         '_w4()\n{\n  python3 "$MISSION_STATE_PY" "$@"\n}\n_w4 resume'),
        # Each of these ended a function body early while `bash -n` accepted the
        # script, so the wrapper went unrecognised.  The check no longer reads
        # bodies, and they are kept as the regression for that decision.
        ("a `}` in a heredoc body",
         "_hd() {\n  cat <<'EOF'\n}\nEOF\n"
         '  _mission_state_bounded "$@"\n}\n_hd resume'),
        ("an escaped quote in an ANSI-C string",
         "_ac() {\n  printf '%s' $'\\'}'\n"
         '  _mission_state_bounded "$@"\n}\n_ac resume'),
        ("a nested brace group before the call",
         '_w8() {\n  {\n    :\n  }\n  _mission_state_bounded "$@"\n}\n_w8 resume'),
        # The brace in each of these is *unbalanced*, so a parser that does not
        # skip quoted text closes the body on it.  A balanced `{...}` would pass
        # either way and would not exercise the skipping at all.
        ("an unbalanced brace inside single quotes",
         '_w9() {\n  printf \'%s\' \'}\'\n'
         '  _mission_state_bounded "$@"\n}\n_w9 resume'),
        ("an unbalanced brace inside double quotes",
         '_w11() {\n  printf \'%s\' "}"\n'
         '  _mission_state_bounded "$@"\n}\n_w11 resume'),
        ("a brace escaped with a backslash",
         '_w12() {\n  printf \'%s\' \\}\n'
         '  _mission_state_bounded "$@"\n}\n_w12 resume'),
        # #796: names and call sites the matching missed.  Each of these was
        # checked by running it under `bash` with a stub wrapper: all five
        # actually invoke the wrapper with `resume`, so each is a real hole and
        # not just a shape the matching reads differently.
        # These forward through `"$MISSION_STATE_PY"`, not through the hook's
        # wrapper, so the definition line stays exempt and only the *call* can
        # trip the check.  Written the other way, the definition itself is
        # reported (a function name may not forward `"$@"`), which passes the
        # row while leaving the name matching untested.
        ("a name containing a hyphen",
         'function _q-x { python3 "$MISSION_STATE_PY" "$@"; }\n_q-x resume'),
        ("a call whose name is quoted",
         '_q() { python3 "$MISSION_STATE_PY" "$@"; }\n\'_q\' resume'),
        ("a call through a variable",
         '_q=_mission_state_bounded\n"$_q" resume'),
        ("a call through the braced spelling of a variable",
         '_q=_mission_state_bounded\n"${_q}" resume'),
        ("a variable assigned from another variable",
         '_q=_mission_state_bounded\n_r=$_q\n"$_r" resume'),
        # Aliases are scanned before assignments, so this one needs a second
        # pass: `$_q` is not a caller yet when the alias line is read.  It runs
        # (bash expands `$_q` when the alias is defined), so the fixed point is
        # not theoretical.
        # A command can begin inside a substitution or after an operator, and
        # `shlex` alone joins the caller to the punctuation before it.
        ("a call inside a command substitution",
         'x=$(_mission_state_bounded resume)'),
        ("a call inside backticks",
         'x=`_mission_state_bounded resume`'),
        ("a substitution inside double quotes",
         'printf \'%s\' "$(_mission_state_bounded resume)"'),
        ("a call after a semicolon", 'true; _mission_state_bounded resume'),
        # No space: `shlex` alone yields `true;_mission_state_bounded` as one
        # word, so the operator has to end the fragment.  Both of these run.
        ("a call after a semicolon with no space",
         'true;_mission_state_bounded resume'),
        ("a call after a pipe with no space",
         'true|_mission_state_bounded resume'),
        # `\"` is a literal quote, not the start of a string.  Reading it as
        # one leaves the rest of the line "inside quotes", so the operators
        # stop ending fragments and the call joins the punctuation before it.
        ("an escaped quote before the operators",
         'printf \'%s\' \\";true;_mission_state_bounded resume'),
        ("a call after a pipe", 'true | _mission_state_bounded resume'),
        ("a call after &&", 'true && _mission_state_bounded resume'),
        ("a brace passed as the argument of a real call",
         "_mission_state_bounded {"),
        ("a name quoted in the middle",
         "_mission_state_'bounded' resume"),
        ("an alias whose value is quoted",
         "shopt -s expand_aliases\nalias _al='_mission_state_bounded'\n_al resume"),
        ("an alias whose value is a variable",
         'shopt -s expand_aliases\n_q=_mission_state_bounded\n'
         'alias _al=$_q\n_al resume'),
        ("a call through an alias",
         'shopt -s expand_aliases\nalias _al=_mission_state_bounded\n_al resume'),
        ("positional parameters replaced before forwarding",
         'set -- resume\n_mission_state_bounded "$@"'),
        ("a brace inside a comment",
         '_w10() {\n  # }\n  _mission_state_bounded "$@"\n}\n_w10 resume'),
    ],
)
def test_the_hook_may_not_reach_the_cli_by_any_other_shape(label, inserted):
    # Inserted as its own lines after the last `fi`, not spliced into the
    # `printf`.  Splicing put the continuation case on the same line as
    # another command, so it was caught by a different rule and the
    # continuation handling was never exercised -- a mutation that removed
    # the line-joining survived.
    source = HOOK.read_text(encoding="utf-8")
    marker = "\nfi\n"
    at = source.rindex(marker) + len(marker)
    source = source[:at] + inserted + "\n" + source[at:]
    codes = [violation.code for violation in analyze_guard_shell(source)]
    assert "command-not-allowlisted" in codes, label


def test_an_unreadable_subcommand_is_refused_rather_than_ignored():
    """Deny-by-default includes what the check cannot read.

    `helper "$CMD"` may be `stop-verdict` at runtime or anything else.  Reading
    it is not possible here, and treating unreadable as acceptable is the same
    hole as an allowlist that nobody updated.
    """
    source = HOOK.read_text(encoding="utf-8").replace(
        'printf \'%s\' "$SHELL_TEXT"',
        '_mission_state_bounded "$SOMETHING"\nprintf \'%s\' "$SHELL_TEXT"',
    )
    codes = [violation.code for violation in analyze_guard_shell(source)]
    assert "command-not-allowlisted" in codes


def test_a_call_sharing_a_line_with_the_forwarding_call_is_still_read():
    """Only the forwarding call is exempt, not the line it sits on.

    The wrapper is recognised by forwarding `"$@"`, so that one call has to be
    skipped.  Skipping the whole *line* let anything after the `;` through, and
    a wrapper definition is exactly where such a line is plausible.  Asserting
    through the hook cannot show this: the hook has no such line, so the rule
    would look satisfied while the extraction dropped the call.
    """
    source = 'python3 "$MISSION_STATE_PY" "$@"; _mission_state_bounded resume\n'
    callers = _state_cli_callers(
        '_mission_state_bounded() {\n  python3 "$MISSION_STATE_PY" "$@"\n}\n'
    )

    invocations = _state_cli_invocations(source, callers)

    assert [subcommand for _line, subcommand in invocations] == ["resume"], invocations


def test_a_continuation_line_is_read_as_one_command():
    """Joining continuations is what makes the subcommand visible at all.

    Without it the analyzer sees `_mission_state_bounded \\` and then a bare
    `resume`, and reads neither as a call.  Asserting through the whole hook
    hid this: the extra call also trips the "more than one invocation" rule, so
    a mutation that removed the joining still failed the test for the other
    reason.  This asks the extraction directly.
    """
    source = "_mission_state_bounded \\\n    resume\n"
    callers = _state_cli_callers('_x() {\n  python3 "$MISSION_STATE_PY" "$@"\n}\n') | {"_mission_state_bounded"}

    invocations = _state_cli_invocations(source, callers)

    assert [subcommand for _line, subcommand in invocations] == ["resume"], invocations
