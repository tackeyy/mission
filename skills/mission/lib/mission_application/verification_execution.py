"""Public acceptance-verifier execution bound to frozen contract evidence."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path

from mission_application.artifact import EvidenceFailure
from mission_application.verification_runner import (
    VerificationRunnerError,
    capture_candidate,
    execute_candidate,
    verifier_definition_digest,
)
from acceptance_contract import AcceptanceContractError, canonical_contract_digest, frozen_verifier_commands


@dataclass(frozen=True)
class VerificationReceiptCliRequest:
    criterion_id: object
    repro_input_path: object = None


@dataclass(frozen=True)
class VerificationReceiptCliServices:
    resolve_state_file: object
    repository: object
    load_verifier_policy: object
    compatibility_arguments: object
    canonical_operation: object
    now: object


def run_verification_receipt_cli(request, services) -> str:
    """Run and persist one verifier receipt through injected CLI services."""
    cwd = Path.cwd()
    state_file = services.resolve_state_file(cwd)
    if not state_file.exists():
        raise EvidenceFailure("verification-state-missing")
    reader = services.repository(
        cwd, state_file, stamp=False, strict_read=True, pre_admit_lease=True,
        session_id=state_file.stem,
    )
    with reader.transaction():
        state = reader.load()
    contract = state.get("acceptance_contract") if isinstance(state, dict) else None
    if isinstance(state, dict) and "acceptance_contract" in state:
        _frozen_commands(contract)
    live_policy = services.load_verifier_policy(cwd)
    if not isinstance(contract, dict) or not isinstance(contract.get("verifier_policy"), dict) or live_policy["digest"] != contract["verifier_policy"].get("digest"):
        raise EvidenceFailure("verifier-policy-stale")
    repro_input = None
    if request.repro_input_path:
        try:
            repro_input = json.loads(Path(request.repro_input_path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EvidenceFailure("replay-input-invalid") from exc
    budget = None
    from mission_kernel.budget import decode_ledger
    if decode_ledger(state).policy is not None:
        from .verification_budget import admit_verification, execute_verification
        commands = _frozen_commands(contract)
        criteria = [c for c in contract['criteria'] if c['id'] == request.criterion_id]
        if len(criteria) != 1:
            raise EvidenceFailure('verification-criterion-unavailable')
        command = commands[criteria[0]['command_id']]
        replay = command.get('replay')
        if repro_input is not None and isinstance(replay, dict):
            command = commands[replay['command_id']]
        budget, admitted = admit_verification(services.repository(
            cwd, state_file, stamp=True, strict_read=True, pre_admit_lease=True,
            session_id=state_file.stem), request.criterion_id, command['timeout_sec'],
            # Candidate inventory itself spawns git. Reserve its supervisor
            # before observation; the supervisor applies the real-tree gate
            # before spawning the verifier. No digest is claimed before capture.
            services.now(), None, command)
        if admitted.get('acceptance_contract') != contract:
            from .verification_budget import settle_failed_verification
            settle_failed_verification(services, cwd, state_file, budget, 'verification-contract-stale')
            raise EvidenceFailure('verification-contract-stale')
        try:
            receipt = execute_verification(admitted, cwd, request.criterion_id, repro_input, budget, command, session_id=state_file.stem)
        except BaseException as exc:
            interrupted_receipt = getattr(exc, 'verification_receipt', None)
            if interrupted_receipt is not None:
                from .verification_budget import settle_collected_verification
                settle_collected_verification(services, cwd, state_file, budget, interrupted_receipt)
            raise
    else:
        receipt = run_contract_verifier(
            state, project_root=cwd, criterion_id=request.criterion_id,
            repro_input=repro_input,
        )
    try:
        return _publish_receipt(request, services, cwd, state_file, receipt, budget)
    except EvidenceFailure:
        if budget is not None:
            from .verification_budget import settle_collected_verification
            settle_collected_verification(services, cwd, state_file, budget, receipt)
        raise


def _publish_receipt(request, services, cwd, state_file, receipt, budget):
    try:
        caller_id, arguments = services.compatibility_arguments(
            {"criterion_id": request.criterion_id, "candidate_digest": receipt["candidate_digest"], "receipt_status": receipt["status"]},
            target_digest="", require_caller=False,
        )
        operation_id = operation_command = None
        if caller_id is not None:
            operation_id, operation_command = services.canonical_operation(
                state_file.stem, "verification-receipt-record", arguments,
                caller_operation_id=caller_id,
            )
    except ValueError:
        if budget is not None:
            from .verification_budget import settle_collected_verification
            settle_collected_verification(services, cwd, state_file, budget, receipt)
        raise
    from mission_application.evidence import VerificationReceiptRequest, run_verification_receipt
    result = run_verification_receipt(
        VerificationReceiptRequest(services.now(), receipt, budget),
        services.repository(
            cwd, state_file, stamp=True, pre_admit_lease=True,
            session_id=state_file.stem, operation_id=operation_id,
            operation_command=operation_command,
            operation_command_type="verification-receipt-record",
        ),
    )
    return json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2)


def run_contract_verifier(state, *, project_root, criterion_id, repro_input=None, budget_deadline=None, no_progress_candidate=None):
    """Execute one contract criterion without accepting caller-declared results."""
    if not isinstance(state, dict):
        raise EvidenceFailure("verification-state-invalid")
    if "acceptance_contract" not in state:
        raise EvidenceFailure("verification-contract-unavailable")
    contract = state["acceptance_contract"]
    commands = _frozen_commands(contract)
    if contract.get("schema") != "mission-acceptance-contract/2":
        raise EvidenceFailure("verification-contract-unavailable")
    criteria = [item for item in contract.get("criteria", []) if isinstance(item, dict) and item.get("id") == criterion_id]
    if len(criteria) != 1:
        raise EvidenceFailure("verification-criterion-unavailable")
    policy = contract.get("verifier_policy")
    command = commands.get(criteria[0].get("command_id")) if isinstance(commands, dict) else None
    if not isinstance(command, dict):
        raise EvidenceFailure("verification-command-unregistered")
    replay = command.get("replay")
    if repro_input is not None:
        if not isinstance(replay, dict):
            return _blocked_receipt(contract, policy, criterion_id, command, "replay-unsupported")
        if not isinstance(repro_input, dict) or set(repro_input) != {"artifact_kind", "content"} or repro_input["artifact_kind"] not in replay["allowed_artifact_kinds"] or not isinstance(repro_input["content"], str):
            return _blocked_receipt(contract, policy, criterion_id, command, "replay-input-invalid")
        try:
            replay_content = repro_input["content"].encode("utf-8")
        except UnicodeError as exc:
            raise EvidenceFailure("replay-input-invalid") from exc
        if len(replay_content) > replay["max_bytes"]:
            return _blocked_receipt(contract, policy, criterion_id, command, "replay-input-invalid")
        replay_command = commands.get(replay["command_id"])
        if not isinstance(replay_command, dict):
            return _blocked_receipt(contract, policy, criterion_id, command, "replay-unsupported")
        command = replay_command
    try:
        candidate = capture_candidate(project_root, declared_untracked=command["declared_untracked"], external_inputs=command["external_inputs"])
        if candidate.digest == no_progress_candidate:
            return _blocked_receipt(contract, policy, criterion_id, command, 'budget-no-new-evidence', candidate_digest=candidate.digest)
        replay_file = None if repro_input is None else (repro_input["artifact_kind"], replay["relative_path"], replay_content)
        if replay_file is not None and any(
            replay_file[1] == item.path or replay_file[1].startswith(item.path + "/") or item.path.startswith(replay_file[1] + "/")
            for item in candidate.files
        ):
            return _blocked_receipt(contract, policy, criterion_id, command, "replay-input-path-conflict", candidate_digest=candidate.digest)
        outcome = execute_candidate(candidate, command, relative_cwd=command["relative_cwd"], repro_input=replay_file, **({"budget_deadline": budget_deadline} if budget_deadline is not None else {}))
        # The source must still be the candidate after process execution.  A
        # mutable worktree never receives a successful receipt.
        current = capture_candidate(project_root, declared_untracked=command["declared_untracked"], external_inputs=command["external_inputs"])
    except (KeyError, OSError, VerificationRunnerError) as exc:
        if "outcome" not in locals() or "candidate" not in locals():
            raise EvidenceFailure(str(exc)) from exc
        outcome["status"] = "blocked"
        outcome["block_reason"] = "candidate-observation-invalid"
        current = candidate
    if current.digest != candidate.digest:
        outcome["status"] = "blocked"
        outcome["block_reason"] = "candidate-stale"
    return {
        "schema": "mission-verification-receipt/1",
        "contract_digest": canonical_contract_digest(contract),
        "criterion_id": criterion_id,
        "candidate_digest": candidate.digest,
        "verifier_policy_digest": policy["digest"],
        "verifier_definition_digest": verifier_definition_digest(command),
        "argv": list(command["argv"]),
        "relative_cwd": command["relative_cwd"],
        "started_at": outcome["started_at"],
        "finished_at": outcome["finished_at"],
        "exit_code": outcome["exit_code"],
        "timed_out": outcome["timed_out"],
        "executed_count": outcome["executed_count"],
        "output_digest": outcome["output_digest"],
        "observed_output_bytes": outcome["observed_output_bytes"],
        "output_truncated": outcome["output_truncated"],
        "status": outcome["status"],
        "runner_provenance": "mission-public-cli/1",
        "repro_input_digest": outcome["repro_input_digest"],
        "block_reason": outcome["block_reason"],
    }


def _frozen_commands(contract):
    try:
        return frozen_verifier_commands(contract)
    except AcceptanceContractError as exc:
        raise EvidenceFailure(str(exc)) from exc


def _blocked_receipt(contract, policy, criterion_id, command, reason, *, candidate_digest=None):
    import hashlib
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"schema": "mission-verification-receipt/1", "contract_digest": canonical_contract_digest(contract), "criterion_id": criterion_id, "candidate_digest": candidate_digest or "sha256:" + "0" * 64, "verifier_policy_digest": policy["digest"], "verifier_definition_digest": verifier_definition_digest(command), "argv": list(command["argv"]), "relative_cwd": command["relative_cwd"], "started_at": observed_at, "finished_at": observed_at, "exit_code": None, "timed_out": False, "executed_count": None, "output_digest": "sha256:" + hashlib.sha256(reason.encode()).hexdigest(), "observed_output_bytes": 0, "output_truncated": False, "status": "blocked", "runner_provenance": "mission-public-cli/1", "repro_input_digest": None, "block_reason": reason}
