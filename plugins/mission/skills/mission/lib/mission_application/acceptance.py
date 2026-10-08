"""Application boundary for immutable acceptance-contract import."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from acceptance_contract import AcceptanceContractError, canonical_contract_digest, digest, load, status
from mission_application.evidence import PreparedEvidenceOperation, execute_evidence_operation
from mission_application.cli_operation import CliOperationRejected, prepare_cli_operation
from mission_application.artifact import EvidenceFailure
from mission_kernel.commands import ImportAcceptanceContract
from mission_kernel.json_codec import freeze_json_value
from mission_application.verifier_policy import VerifierPolicyError, freeze as freeze_verifier_policy


@dataclass(frozen=True)
class AcceptanceContractImportRequest:
    now: object
    raw: object
    verifier_policy: object = None


@dataclass(frozen=True)
class AcceptanceContractCliServices:
    resolve_state_file: object
    repository: object
    now: object
    fail: object
    compatibility_arguments: object
    canonical_operation: object
    load_verifier_policy: object
    capacity_status: object = None
    commit_errors: tuple = ()


def prepare_acceptance_contract_import(state: object, *, now: object, raw: object, verifier_policy=None) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    if not isinstance(raw, bytes):
        raise EvidenceFailure("acceptance-contract-input-invalid")
    try:
        contract = load(raw)
    except AcceptanceContractError as exc:
        raise EvidenceFailure(str(exc)) from exc
    if contract["mission_id"] != (state.get("mission_id") or state.get("session_id")):
        raise EvidenceFailure("acceptance-contract-mission-mismatch")
    if contract["schema"] == "mission-acceptance-contract/2":
        try:
            contract["verifier_policy"] = freeze_verifier_policy(
                verifier_policy, command_ids=[item["command_id"] for item in contract["criteria"]],
                expected_digest=contract["verifier_policy_digest"],
            )
        except VerifierPolicyError as exc:
            raise EvidenceFailure(str(exc)) from exc
    frozen = freeze_json_value(contract)
    command = ImportAcceptanceContract(now, frozen)
    result = {"acceptance_contract": {**copy.deepcopy(contract), "digest": canonical_contract_digest(contract)}}
    return PreparedEvidenceOperation(command, (), result, volatile_fields=("imported_at",))


def run_acceptance_contract_import(request: AcceptanceContractImportRequest, repository: object) -> dict:
    return execute_evidence_operation(repository, lambda state: prepare_acceptance_contract_import(state, now=request.now, raw=request.raw, verifier_policy=request.verifier_policy))


def prepare_acceptance_contract_import_operation(raw, *, session_id, compatibility_arguments, canonical_operation):
    try:
        contract = load(raw)
    except AcceptanceContractError as exc:
        raise CliOperationRejected(str(exc)) from exc
    return prepare_cli_operation(
        "acceptance-contract-import", {"contract_digest": digest(contract)},
        session_id=session_id, compatibility_arguments=compatibility_arguments,
        canonical_operation=canonical_operation,
    )


def acceptance_contract_status(state: object) -> dict:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    if "acceptance_contract" not in state:
        return status(None)
    contract = state["acceptance_contract"]
    if not isinstance(contract, dict):
        raise EvidenceFailure("acceptance-contract-invalid")
    try:
        result = status(contract)
    except AcceptanceContractError as exc:
        raise EvidenceFailure(str(exc)) from exc
    result["imported_at"] = contract.get("imported_at")
    result["verifier_policy"] = contract.get("verifier_policy")
    return result


def _state_file(cwd, services):
    state_file = services.resolve_state_file(cwd)
    if not state_file.exists():
        services.fail("acceptance-contract-state-missing", 1)
    return state_file


def _render(result: dict) -> str:
    return json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2)


def run_acceptance_contract_import_cli(args, services) -> str:
    cwd = Path.cwd()
    state_file = _state_file(cwd, services)
    try:
        raw = Path(getattr(args, "input")).read_bytes()
    except OSError as exc:
        services.fail(str(exc), 2)
    try:
        identity = prepare_acceptance_contract_import_operation(
            raw, session_id=state_file.stem,
            compatibility_arguments=services.compatibility_arguments,
            canonical_operation=services.canonical_operation,
        )
        policy = services.load_verifier_policy(cwd) if load(raw)["schema"] == "mission-acceptance-contract/2" else None
        result = run_acceptance_contract_import(
            AcceptanceContractImportRequest(services.now(), raw, policy),
            services.repository(cwd, state_file, stamp=True, pre_admit_lease=True,
                                session_id=state_file.stem, operation_id=identity.operation_id,
                                operation_command=identity.operation_command,
                                operation_command_type=identity.command_type),
        )
    except (EvidenceFailure, CliOperationRejected, VerifierPolicyError) as exc:
        services.fail(getattr(exc, "code", str(exc)), 2)
    return _render(result)


def run_acceptance_contract_status_cli(args, services) -> str:
    cwd = Path.cwd()
    state_file = _state_file(cwd, services)
    repository = services.repository(cwd, state_file, stamp=False, strict_read=True)
    try:
        with repository.transaction():
            data = repository.load()
        return json.dumps(acceptance_contract_status(data), ensure_ascii=False, indent=2)
    except EvidenceFailure as exc:
        services.fail(exc.code, 2)
    except AcceptanceContractError as exc:
        services.fail(str(exc), 2)
