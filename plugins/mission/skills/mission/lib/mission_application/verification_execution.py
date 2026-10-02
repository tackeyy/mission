"""Public acceptance-verifier execution bound to frozen contract evidence."""
from __future__ import annotations

from mission_application.artifact import EvidenceFailure
from mission_application.verification_runner import (
    VerificationRunnerError,
    capture_candidate,
    execute_candidate,
    verifier_definition_digest,
)
from acceptance_contract import digest as contract_digest


def run_contract_verifier(state, *, project_root, criterion_id):
    """Execute one contract criterion without accepting caller-declared results."""
    if not isinstance(state, dict):
        raise EvidenceFailure("verification-state-invalid")
    contract = state.get("acceptance_contract")
    if not isinstance(contract, dict) or contract.get("schema") != "mission-acceptance-contract/2":
        raise EvidenceFailure("verification-contract-unavailable")
    criteria = [item for item in contract.get("criteria", []) if isinstance(item, dict) and item.get("id") == criterion_id]
    if len(criteria) != 1:
        raise EvidenceFailure("verification-criterion-unavailable")
    policy = contract.get("verifier_policy")
    commands = policy.get("commands") if isinstance(policy, dict) else None
    command = commands.get(criteria[0].get("command_id")) if isinstance(commands, dict) else None
    if not isinstance(command, dict):
        raise EvidenceFailure("verification-command-unregistered")
    try:
        candidate = capture_candidate(project_root, declared_untracked=command["declared_untracked"])
        outcome = execute_candidate(candidate, command, relative_cwd=command["relative_cwd"])
        # The source must still be the candidate after process execution.  A
        # mutable worktree never receives a successful receipt.
        current = capture_candidate(project_root, declared_untracked=command["declared_untracked"])
    except (KeyError, VerificationRunnerError) as exc:
        raise EvidenceFailure(str(exc)) from exc
    if current.digest != candidate.digest:
        outcome["status"] = "blocked"
    return {
        "schema": "mission-verification-receipt/1",
        "contract_digest": contract_digest({key: value for key, value in contract.items() if key not in {"imported_at", "verifier_policy"}}),
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
        "status": outcome["status"],
        "runner_provenance": "mission-public-cli/1",
    }
