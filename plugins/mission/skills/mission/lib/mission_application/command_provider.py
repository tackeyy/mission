"""Application use case for a legacy command provider invocation."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Optional

from mission_application.planning import PlanningFailure, decide_provider_terminal_result, record_dispatch_intent, record_provider_receipt
from mission_common import PREPARATION_ONLY_MARKERS
from provider_public_contract import SpecialistPublicContractError

DEFAULT_COMMAND_ATTEMPT = 1


class CommandProviderFailure(ValueError):
    """An application rejection the CLI renders to stderr with a stable exit."""

    def __init__(self, code: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


@dataclass(frozen=True)
class CommandProviderResult:
    """Closed CLI presentation returned by the application use case."""

    rendered: str


@dataclass(frozen=True)
class CommandProviderRequest:
    provider_id: str
    iteration: int
    phase: str
    specialists_cmd: Optional[str]
    preflight_id: Optional[str]
    selection_source: Optional[str]
    timeout_override: Optional[int]
    input_file: Optional[str]
    execution_isolator: Optional[str]
    registry: object
    json_output: bool
    event_id: Optional[str]
    root_event_id: Optional[str]
    raw_attempt: object
    retry_of: Optional[str]

    @property
    def attempt(self) -> int:
        return DEFAULT_COMMAND_ATTEMPT if self.raw_attempt is Ellipsis else self.raw_attempt


@dataclass(frozen=True)
class WorkspaceServices:
    current_directory: object
    resolve_state_file: object
    path_exists: object
    read_bytes: object
    read_text: object
    inspect_repository_bytes: object
    v5_format: object


@dataclass(frozen=True)
class ProviderPolicyServices:
    find_provider: object
    provider_gate: object
    validate_specialist_public_state: object
    verified_preflight_packet: object
    confirmed_selection_required: object
    require_current_provider_application: object
    reject_unbounded_orchestrator_execution: object
    current_selection_id: object
    reject_active_provider_mutation: object
    enforce_session_lease_for_write: object
    resolve_session_id: object
    invocation_by_id: object
    validate_invocation_transition: object
    specialist_lifecycle_error: object
    applied_statuses: object


@dataclass(frozen=True)
class StateEffectsServices:
    compatibility_operation_arguments: object
    canonical_compatibility_operation: object
    repository_factory: object
    prepare_specialist_invocation_state: object
    record_activity_event: object
    replace_provider_invocation: object
    command_outcome: object
    end_activity_segment: object
    stamp_metadata: object
    add_selected_specialist_metadata: object
    append_command_outcome: object
    commit_specialist_state_with_save: object


@dataclass(frozen=True)
class ExecutionServices:
    base_environment: object
    command_available: object
    strict_dispatch: object
    configured_execution_isolator: object
    strict_backend: object
    Popen: object
    PIPE: object
    TimeoutExpired: object
    redact: object
    value_digest: object
    clock: object
    json_loads: object
    provider_preflight_error: object


def _string_map(value):
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if key is not None and item is not None}


def _merge_result_contract(defaults: dict, explicit: dict) -> dict:
    merged = dict(defaults)
    merged.update(explicit)
    markers = [
        *[str(value) for value in defaults.get("forbidden_markers") or []],
        *[str(value) for value in explicit.get("forbidden_markers") or []],
    ]
    if markers:
        merged["forbidden_markers"] = list(dict.fromkeys(markers))
    return merged


def _non_template_text_length(text: str, forbidden_markers: list[str]) -> int:
    cleaned = text
    for marker in forbidden_markers:
        cleaned = cleaned.replace(marker, "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned)


def _contract_exit_codes(contract: dict, key: str) -> set[int]:
    codes = contract.get(key) or []
    if isinstance(codes, (str, int)):
        codes = [codes]
    result: set[int] = set()
    for value in codes:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _classify_command_provider_result(provider: dict, exit_code: Optional[int], stdout: str, stderr: str):
    explicit_contract = provider.get("result_contract") if isinstance(provider.get("result_contract"), dict) else {}
    contract = _merge_result_contract({}, explicit_contract)
    combined = "\n".join([stdout or "", stderr or ""])
    awaiting_markers = [str(value) for value in contract.get("awaiting_input_markers") or []]
    awaiting_hits = [marker for marker in awaiting_markers if marker and marker in combined]
    if awaiting_hits:
        return "awaiting-input", f"command provider awaiting input: {', '.join(awaiting_hits[:3])}"
    if exit_code in _contract_exit_codes(contract, "awaiting_input_exit_codes"):
        return "awaiting-input", f"command provider awaiting input after exit code {exit_code}"
    if exit_code != 0:
        return "failed", f"command provider exited with status {exit_code}"
    forbidden_markers = [str(value) for value in contract.get("forbidden_markers") or PREPARATION_ONLY_MARKERS]
    marker_hits = [marker for marker in forbidden_markers if marker and marker in combined]
    try:
        min_chars = int(contract.get("min_non_template_chars") or 0)
    except (TypeError, ValueError):
        min_chars = 0
    non_template_len = _non_template_text_length(combined, forbidden_markers)
    if marker_hits:
        return "prepared", f"command provider returned preparation-only evidence: {', '.join(marker_hits[:3])}"
    if not explicit_contract:
        return "unvalidated-evidence", "command provider has no explicit result contract"
    if min_chars and non_template_len < min_chars:
        return "prepared", f"command provider evidence below result_contract.min_non_template_chars ({non_template_len} < {min_chars})"
    return "completed", None


def _provider_timeout(provider: dict, override: Optional[int]) -> int:
    value = override if override is not None else provider.get("timeout", 120)
    if value is None:
        value = 120
    if type(value) is not int or not 1 <= value <= 86400:
        raise SpecialistPublicContractError("/specialist_invocations/pending/timeout")
    return value


def _invoke_command_provider(request, workspace, provider_policy, state_effects, execution):
    cwd = workspace.current_directory()
    sf = workspace.resolve_state_file(cwd)
    if not workspace.path_exists(sf):
        raise CommandProviderFailure("state-not-found", "state.json が見つかりません。先に `init` してください。", 1)
    # Repository setup (before any state reads to enable MISSION_OPERATION_ID check for v5)
    session_id = sf.stem
    _command_name_invoke = "specialists-invoke-command"
    _preflight_id_invoke = getattr(request, "preflight_id", None)
    _command_arguments_invoke = {
        "provider": str(request.provider_id),
        "iteration": int(request.iteration),
        "phase": str(request.phase),
        "preflight_id": str(_preflight_id_invoke) if _preflight_id_invoke else "",
    }
    try:
        _target_bytes_invoke = workspace.read_bytes(sf)
        _inspected_invoke = workspace.inspect_repository_bytes(_target_bytes_invoke, expected_session_id=session_id)
        _target_digest_invoke = "sha256:" + hashlib.sha256(_target_bytes_invoke).hexdigest()
        _caller_op_invoke, _op_args_invoke = state_effects.compatibility_operation_arguments(
            _command_arguments_invoke, target_digest=_target_digest_invoke,
            require_caller=_inspected_invoke.format is workspace.v5_format,
        )
        _op_id_invoke, _op_cmd_invoke = state_effects.canonical_compatibility_operation(
            session_id, _command_name_invoke, _op_args_invoke,
            caller_operation_id=_caller_op_invoke,
        )
    except (OSError, ValueError) as _err_invoke:
        raise CommandProviderFailure("repository-selection-invalid", str(_err_invoke), 2)

    def _make_repo_invoke(suffix):
        return state_effects.repository_factory(
            cwd, sf, stamp=True, strict_read=True, session_id=session_id,
            operation_id=_op_id_invoke + suffix,
            operation_command=_op_cmd_invoke,
            operation_command_type=_command_name_invoke,
        )

    # Pre-validate (read-only, before any locks)
    # For v5 sessions sf is the HEAD file which has no session data; load the
    # actual session state through a read-only repository transaction.
    if _inspected_invoke.format is workspace.v5_format:
        _preread_repo = _make_repo_invoke(":v5-preread")
        with _preread_repo.transaction():
            data = _preread_repo.load()
    else:
        data = execution.json_loads(workspace.read_text(sf))
    provider_policy.validate_specialist_public_state(data)
    provider = provider_policy.find_provider(data, request.provider_id)
    if not provider:
        raise CommandProviderFailure("provider-not-found", f"provider not found in mission state: {request.provider_id}", 2)
    if provider.get("kind") != "command":
        raise CommandProviderFailure("provider-kind-invalid", f"provider is not kind=command: {request.provider_id}", 2)
    if getattr(request, "specialists_cmd", None) != "invoke-prepared" and getattr(request, "preflight_id", None):
        provider_policy.provider_gate("use-invoke-prepared")
    # #396: any command provider is an external-risk invocation until a
    # verified per-invocation preflight/receipt proves otherwise.  Keep this
    # guard before reservation, state mutation, and subprocess creation.
    if not getattr(request, "preflight_id", None):
        provider_policy.provider_gate("preflight-required")
    # For invoke-prepared with a consumed preflight: the preflight was already used
    # by a previous call. Skip full validation here — Section 2 will detect
    # operation_replayed and short-circuit without re-dispatching. Pass the raw
    # pointer through so entry construction can read outbound_packet_digest.
    _pf_preflights_raw = data.get("provider_preflights") or {}
    _pf_pointer_raw = _pf_preflights_raw.get(request.preflight_id) if isinstance(_pf_preflights_raw, dict) else None
    _pf_consumed_replay = (
        getattr(request, "specialists_cmd", None) == "invoke-prepared"
        and isinstance(_pf_pointer_raw, dict)
        and _pf_pointer_raw.get("status") == "consumed"
    )
    if _pf_consumed_replay:
        pointer, packet = _pf_pointer_raw, b""
    else:
        pointer, packet = provider_policy.verified_preflight_packet(cwd, data, provider, request)
    if provider_policy.confirmed_selection_required(data, provider.get("skill") or provider.get("role"), "completed") and not request.selection_source:
        raise CommandProviderFailure("selection-confirmation-required", "specialists_decision requested user confirmation; pass --selection-source confirmed-user when invoking an applied command provider after confirmation.", 2)
    provider_policy.require_current_provider_application(
        data,
        provider,
        requested_phase=request.phase,
        requested_iteration=request.iteration,
        application_kind="preflight",
        selection_source=request.selection_source,
        cwd=cwd,
        registry_args=request,
    )
    provider_policy.reject_unbounded_orchestrator_execution(data, provider.get("skill") or provider.get("role"), request.phase)

    now = execution.clock()
    entry = {
        "invocation_id": pointer["invocation_id"],
        "provider_id": provider.get("provider_id"),
        "iteration": request.iteration,
        "phase": request.phase,
        "role": provider.get("role"),
        "skill": provider.get("skill") or provider.get("role"),
        "mode": "command-provider",
        "status": "reserved",
        "lifecycle_state": "reserved",
        "timestamp": now,
        "transitioned_at": now,
        "reserved_at": now,
        "provider_kind": "command",
        "input_outbound_packet_digest": pointer["outbound_packet_digest"],
        **{
            field: data.get(field)
            for field in ("host_run_id", "root_run_id", "parent_run_id", "child_run_id", "logical_group_id")
            if data.get(field) is not None
        },
    }
    selection_id = provider_policy.current_selection_id(data)
    if selection_id:
        entry["selection_id"] = selection_id
    timeout = _provider_timeout(provider, request.timeout_override)
    entry["timeout"] = timeout

    # ── Section 1: Reservation ──
    _repo_invoke_reserve = _make_repo_invoke(":reserve")
    with _repo_invoke_reserve.transaction():
        dispatch_state = _repo_invoke_reserve.load()
        if not getattr(_repo_invoke_reserve, "operation_replayed", False):
            provider_policy.validate_specialist_public_state(dispatch_state)
            provider_policy.reject_active_provider_mutation(dispatch_state, "invoke-command")
            lease_decision = provider_policy.enforce_session_lease_for_write(sf, dispatch_state)
            provider = provider_policy.require_current_provider_application(
                dispatch_state,
                provider_policy.find_provider(dispatch_state, request.provider_id),
                requested_phase=request.phase,
                requested_iteration=request.iteration,
                application_kind="preflight",
                selection_source=request.selection_source,
                invocation_id=entry["invocation_id"],
                cwd=cwd,
                registry_args=request,
            )
            entry["application_context_digest"] = provider.pop("_application_context_digest")
            entry["reservation_owner_session_id"] = str(dispatch_state.get("owner_session_id") or provider_policy.resolve_session_id())
            entry["fencing_epoch"] = int(dispatch_state.get("fencing_epoch") or lease_decision.fencing_epoch)
            # The preflight ID is single-use and already bound to the immutable
            # outbound packet, so it is the caller-stable operation identity for
            # the non-rollbackable provider dispatch saga.
            entry["operation_id"] = request.preflight_id
            entry["outbound_packet_digest"] = pointer["outbound_packet_digest"]
            dispatch_state, entry, _ = state_effects.prepare_specialist_invocation_state(
                dispatch_state,
                entry,
                cwd=cwd,
                iteration=request.iteration,
                evidence_planned=True,
            )
            preflight_pointer = (dispatch_state.get("provider_preflights") or {}).get(request.preflight_id)
            if not isinstance(preflight_pointer, dict):
                provider_policy.provider_gate("approval-required")
            elif preflight_pointer.get("status") == "consumed":
                provider_policy.provider_gate("receipt-replayed")
            elif preflight_pointer.get("status") != "approved":
                provider_policy.provider_gate("approval-required")
            preflight_pointer["status"] = "consuming"
            preflight_pointer["consuming_invocation_id"] = entry["invocation_id"]
            state_effects.record_activity_event(dispatch_state, "specialist", now)
            dispatch_state["updated_at"] = now
            _repo_invoke_reserve.save(dispatch_state)
        else:
            # Get invocation_id from cached state
            for _inv in (dispatch_state.get("specialist_invocations") or []):
                if isinstance(_inv, dict) and _inv.get("input_outbound_packet_digest") == pointer["outbound_packet_digest"]:
                    entry.update({k: v for k, v in _inv.items() if k not in entry or k in ("invocation_id", "fencing_epoch", "reservation_owner_session_id", "application_context_digest", "operation_id", "outbound_packet_digest")})
                    break

    # ── Section 2: Dispatch intent (idempotency gate — if replayed, skip external dispatch) ──
    running_at = execution.clock()
    _repo_invoke_dispatch = _make_repo_invoke(":dispatch")
    already_dispatched = False
    with _repo_invoke_dispatch.transaction():
        dispatch_state = _repo_invoke_dispatch.load()
        if getattr(_repo_invoke_dispatch, "operation_replayed", False):
            already_dispatched = True
        else:
            provider_policy.validate_specialist_public_state(dispatch_state)
            current_entry = dict(provider_policy.invocation_by_id(dispatch_state, entry["invocation_id"]))
            provider = provider_policy.require_current_provider_application(
                dispatch_state,
                provider_policy.find_provider(dispatch_state, request.provider_id),
                requested_phase=request.phase,
                requested_iteration=request.iteration,
                application_kind="preflight",
                selection_source=request.selection_source,
                invocation_id=entry["invocation_id"],
                cwd=cwd,
                registry_args=request,
            )
            # Re-snapshot payload inputs after the reservation lock acquisition;
            # no byte validated before this point is eligible for subprocess stdin.
            preflight_pointer, packet = provider_policy.verified_preflight_packet(
                cwd, dispatch_state, provider, request, consuming_invocation_id=entry["invocation_id"]
            )
            if provider.pop("_application_context_digest") != current_entry.get("application_context_digest"):
                rejected = {**current_entry, "status": "rejected", "lifecycle_state": "terminal",
                            "reason_code": "application-context-drift", "completed_at": running_at,
                            "transitioned_at": running_at}
                provider_policy.validate_invocation_transition(current_entry, rejected)
                state_effects.replace_provider_invocation(dispatch_state, rejected)
                dispatch_state["updated_at"] = running_at
                _repo_invoke_dispatch.save(dispatch_state)
                raise CommandProviderFailure("provider-ineligible", "provider-ineligible: application-context-drift", 2)
            try:
                intent_decision = record_dispatch_intent(
                    [],
                    {
                        "invocation_id": entry["invocation_id"],
                        "operation_id": entry["operation_id"],
                        "outbound_packet_digest": entry["outbound_packet_digest"],
                        "iteration": entry["iteration"],
                        "fencing_epoch": entry["fencing_epoch"],
                    },
                )
            except PlanningFailure as exc:
                provider_policy.provider_gate(exc.code)
            # This is the durable pre-spawn commit.  A process crash after it but
            # before a receipt remains deliberately unknowable and must never be
            # retried automatically by reconciliation.
            entry = {**current_entry, **intent_decision,
                     "dispatch_intent_at": running_at, "transitioned_at": running_at}
            provider_policy.validate_invocation_transition(current_entry, entry)
            state_effects.replace_provider_invocation(dispatch_state, entry)
            dispatch_state["updated_at"] = running_at
            _repo_invoke_dispatch.save(dispatch_state)

    # If dispatch was already committed (idempotent replay), skip external call
    if already_dispatched:
        # Return result from state
        _final_state = execution.json_loads(workspace.read_text(sf))
        _cached_inv = next(
            (inv for inv in (_final_state.get("specialist_invocations") or [])
             if isinstance(inv, dict) and inv.get("invocation_id") == entry["invocation_id"]),
            entry,
        )
        _outcome = state_effects.command_outcome(request, "specialists-invoke-command",
                                    "ok" if _cached_inv.get("status") == "completed" else "external")
        result = {"ok": _cached_inv.get("status") == "completed",
                  "outcome_kind": _outcome["outcome_kind"], "outcome": _outcome, "entry": _cached_inv}
        return result

    command = provider.get("command")
    argv = [command, *[str(a) for a in provider.get("args") or []]]
    command_env = execution.base_environment.copy()
    command_env.update(_string_map(provider.get("env")))
    execution_context = preflight_pointer.get("execution_context") if isinstance(preflight_pointer, dict) else None
    strict_result = None
    if isinstance(execution_context, dict) and execution_context.get("isolation") == "strict":
        try:
            strict_result = execution.strict_dispatch(
                execution_context, packet,
                lambda _: (_ for _ in ()).throw(execution.provider_preflight_error("isolator-unavailable")),
                lambda attestation, _policy, exact_packet: execution.strict_backend(
                    execution.configured_execution_isolator(cwd, request.execution_isolator), exact_packet
                ) if request.execution_isolator else (_ for _ in ()).throw(execution.provider_preflight_error("isolator-unavailable")),
            )
        except (execution.provider_preflight_error, ValueError, OSError):
            provider_policy.provider_gate("isolator-unavailable")
    elif not execution.command_available(command):
        completed_at = execution.clock()
        failed = {**entry, "status": "failed-before-start", "lifecycle_state": "terminal",
                  "transitioned_at": completed_at, "completed_at": completed_at,
                  "reason_code": "command-unavailable",
                  "proven_no_dispatch": True,
                  "reason": f"command provider is not available: {command}"}
        _repo_invoke_prefail = _make_repo_invoke(":prefail")
        with _repo_invoke_prefail.transaction():
            dispatch_state = _repo_invoke_prefail.load()
            if not getattr(_repo_invoke_prefail, "operation_replayed", False):
                current_entry = provider_policy.invocation_by_id(dispatch_state, entry["invocation_id"])
                provider_policy.validate_invocation_transition(current_entry, failed)
                state_effects.replace_provider_invocation(dispatch_state, failed)
                dispatch_state["updated_at"] = completed_at
                _repo_invoke_prefail.save(dispatch_state)
        return CommandProviderResult(
            json.dumps({"ok": False, "outcome_kind": "external", "entry": failed}, ensure_ascii=False)
        )
    spawn_failed_reason = None
    if strict_result is not None:
        # A strict backend is still external work.  Its return value becomes
        # usable only after it supplies a closed, identity-bearing receipt and
        # that receipt is committed against the pre-spawn intent.
        try:
            strict_receipt = strict_result["receipt"]
        except (KeyError, TypeError):
            provider_policy.provider_gate("strict-receipt-invalid")
        _repo_invoke_receipt = _make_repo_invoke(":receipt")
        with _repo_invoke_receipt.transaction():
            dispatch_state = _repo_invoke_receipt.load()
            if not getattr(_repo_invoke_receipt, "operation_replayed", False):
                current_entry = dict(provider_policy.invocation_by_id(dispatch_state, entry["invocation_id"]))
                try:
                    receipt_state = record_provider_receipt(
                        [current_entry],
                        {
                            "invocation_id": entry["invocation_id"],
                            "operation_id": entry["operation_id"],
                            "outbound_packet_digest": entry["outbound_packet_digest"],
                            "iteration": entry["iteration"],
                            "fencing_epoch": entry["fencing_epoch"],
                        },
                        strict_receipt,
                    )
                except PlanningFailure as exc:
                    provider_policy.provider_gate(exc.code)
                current_entry.update({
                    "provider_receipt": receipt_state["provider_receipt"],
                    "status": "running", "lifecycle_state": "running",
                    "running_at": execution.clock(), "started_at": running_at,
                    "heartbeat_at": execution.clock(), "transitioned_at": execution.clock(),
                })
                provider_policy.validate_invocation_transition(entry, current_entry)
                state_effects.replace_provider_invocation(dispatch_state, current_entry)
                dispatch_state["updated_at"] = execution.clock()
                _repo_invoke_receipt.save(dispatch_state)
                entry = current_entry
        exit_code = strict_result["returncode"]
        stdout = execution.redact(str(strict_result.get("stdout") or ""))
        stderr = execution.redact(str(strict_result.get("stderr") or ""))
    else:
        try:
            process = execution.Popen(argv, stdin=execution.PIPE, stdout=execution.PIPE, stderr=execution.PIPE, env=command_env)
        except OSError as exc:
            spawn_failed_reason = "spawn-failed"; exit_code = None; stdout = ""; stderr = execution.redact(str(exc))
            completed_at = execution.clock()
            entry.update({"status": "failed-before-start", "lifecycle_state": "terminal", "transitioned_at": completed_at,
                          "completed_at": completed_at, "reason_code": "spawn-failed",
                          "proven_no_dispatch": True})
        else:
            entry["child_pid"] = process.pid
            entry["process_identity_digest"] = execution.value_digest({"invocation_id": entry["invocation_id"], "pid": process.pid, "running_at": running_at})
            _repo_invoke_proc = _make_repo_invoke(":proc")
            with _repo_invoke_proc.transaction():
                dispatch_state = _repo_invoke_proc.load()
                if not getattr(_repo_invoke_proc, "operation_replayed", False):
                    current_entry = dict(provider_policy.invocation_by_id(dispatch_state, entry["invocation_id"]))
                    if current_entry.get("status") != "dispatch-unknown":
                        process.terminate(); process.wait(timeout=5)
                        raise CommandProviderFailure("provider-ineligible", "provider-ineligible: invocation-not-dispatch-unknown", 2)
                    try:
                        receipt_state = record_provider_receipt(
                            [current_entry],
                            {
                                "invocation_id": entry["invocation_id"],
                                "operation_id": entry["operation_id"],
                                "outbound_packet_digest": entry["outbound_packet_digest"],
                                "iteration": entry["iteration"],
                                "fencing_epoch": entry["fencing_epoch"],
                            },
                            {"kind": "process", "identity": entry["process_identity_digest"]},
                        )
                    except PlanningFailure as exc:
                        process.terminate(); process.wait(timeout=5)
                        provider_policy.provider_gate(exc.code)
                    current_entry.update({
                        "child_pid": entry["child_pid"],
                        "process_identity_digest": entry["process_identity_digest"],
                        "provider_receipt": receipt_state["provider_receipt"],
                        "status": receipt_state["status"],
                        "lifecycle_state": receipt_state["lifecycle_state"],
                        "running_at": execution.clock(), "started_at": running_at,
                        "heartbeat_at": execution.clock(), "transitioned_at": execution.clock(),
                    })
                    provider_policy.validate_invocation_transition(entry, current_entry)
                    state_effects.replace_provider_invocation(dispatch_state, current_entry)
                    dispatch_state["updated_at"] = execution.clock()
                    _repo_invoke_proc.save(dispatch_state)
                    entry = current_entry
            try:
                raw_stdout, raw_stderr = process.communicate(input=packet, timeout=timeout)
            except execution.TimeoutExpired:
                process.kill(); raw_stdout, raw_stderr = process.communicate(); raw_stderr = (raw_stderr or b"") + b"\ncommand provider timed out"
            exit_code = process.returncode
            stdout = execution.redact((raw_stdout or b"").decode("utf-8", errors="replace"))
            stderr = execution.redact((raw_stderr or b"").decode("utf-8", errors="replace"))

    if spawn_failed_reason:
        status, reason = "failed-before-start", stderr
    else:
        evidence_status, reason = _classify_command_provider_result(provider, exit_code, stdout, stderr)
        try:
            terminal = decide_provider_terminal_result(
                exit_code=exit_code, evidence_status=evidence_status, reason=reason
            )
        except PlanningFailure as exc:
            provider_policy.provider_gate(exc.code)
        status, reason = terminal.status, terminal.reason
    outcome = state_effects.command_outcome(
        request, "specialists-invoke-command",
        "ok" if status == "completed" else "external",
    )
    completed_at = execution.clock()
    entry.update({
        "status": status,
        "lifecycle_state": "terminal",
        "transitioned_at": completed_at,
        "completed_at": completed_at,
        "exit_code": exit_code,
    })
    if reason:
        entry["reason"] = reason
    evidence = (
        "# Command Provider Evidence\n\n"
        f"- provider: {entry['skill']}\n"
        f"- role: {entry['role']}\n"
        f"- command: {execution.redact(json.dumps(argv, ensure_ascii=False))}\n"
        f"- exit_code: {exit_code}\n\n"
        "## Stdout\n\n"
        f"```text\n{stdout}\n```\n\n"
        "## Stderr\n\n"
        f"```text\n{stderr}\n```\n"
    )
    _repo_invoke_result = _make_repo_invoke(":result")
    with _repo_invoke_result.transaction():
        data = _repo_invoke_result.load()
        if not getattr(_repo_invoke_result, "operation_replayed", False):
            provider_policy.validate_specialist_public_state(data)
            provider_policy.require_current_provider_application(
                data,
                provider_policy.find_provider(data, request.provider_id),
                requested_phase=request.phase,
                requested_iteration=request.iteration,
                application_kind="result-import",
                selection_source=request.selection_source,
                invocation_id=entry["invocation_id"],
                cwd=cwd,
                registry_args=request,
            )
            current = data.get("activity_current")
            if (
                isinstance(current, dict)
                and current.get("kind") == "external-wait"
                and current.get("reason") == "external-command"
                and current.get("started_at") == now
            ):
                state_effects.end_activity_segment(data, completed_at)
            data["updated_at"] = completed_at
            data = state_effects.stamp_metadata(data, cwd)
            applied_selection_source = (
                request.selection_source
                if status in provider_policy.applied_statuses
                else None
            )
            if applied_selection_source:
                entry["selection_source"] = applied_selection_source
            try:
                current_entry = provider_policy.invocation_by_id(data, entry["invocation_id"])
                provider_policy.validate_invocation_transition(current_entry, entry)
            except provider_policy.specialist_lifecycle_error as exc:
                raise CommandProviderFailure("invocation-checkpoint-invalid", f"command invocation checkpoint is invalid: {exc}", 2)
            selected_entry = None
            if applied_selection_source:
                selected_entry = state_effects.add_selected_specialist_metadata(
                    data, entry, applied_selection_source, completed_at, provider, reason
                )
            for index, item in enumerate(data["specialist_invocations"]):
                if item.get("invocation_id") == entry["invocation_id"]:
                    data["specialist_invocations"][index] = entry
                    break
            preflight_pointer = (data.get("provider_preflights") or {}).get(request.preflight_id)
            if isinstance(preflight_pointer, dict) and preflight_pointer.get("status") == "consuming":
                preflight_pointer["status"] = "consumed"
                preflight_pointer["consumed_invocation_id"] = entry["invocation_id"]
            provider_policy.validate_specialist_public_state(data)
            state_effects.append_command_outcome(data, outcome)
            state_effects.commit_specialist_state_with_save(
                cwd, data, entry, request.iteration, evidence,
                save_state=_repo_invoke_result.save,
            )
    result = {"ok": status == "completed", "outcome_kind": outcome["outcome_kind"], "outcome": outcome, "entry": entry}
    if selected_entry:
        result["selected_entry"] = selected_entry
    return result


def invoke_command_provider(request, workspace, provider_policy, state_effects, execution):
    result = _invoke_command_provider(request, workspace, provider_policy, state_effects, execution)
    if isinstance(result, CommandProviderResult):
        return result
    return CommandProviderResult(
        json.dumps(result, indent=2 if request.json_output else None, ensure_ascii=False)
    )
