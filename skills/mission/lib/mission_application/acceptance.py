"""Application boundary for immutable acceptance-contract import."""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass

from acceptance_contract import AcceptanceContractError, digest, load, status
from mission_application.evidence import PreparedEvidenceOperation, execute_evidence_operation
from mission_application.artifact import EvidenceFailure
from mission_kernel.commands import ImportAcceptanceContract
from mission_kernel.json_codec import freeze_json_value


@dataclass(frozen=True)
class AcceptanceContractImportRequest:
    now: object
    raw: object


def prepare_acceptance_contract_import(state: object, *, now: object, raw: object) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    if "acceptance_contract" in state:
        raise EvidenceFailure("acceptance-contract-already-imported")
    if not isinstance(raw, bytes):
        raise EvidenceFailure("acceptance-contract-input-invalid")
    try:
        contract = load(raw)
    except AcceptanceContractError as exc:
        raise EvidenceFailure(str(exc)) from exc
    if contract["mission_id"] != (state.get("mission_id") or state.get("session_id")):
        raise EvidenceFailure("acceptance-contract-mission-mismatch")
    frozen = freeze_json_value(contract)
    command = ImportAcceptanceContract(now, frozen)
    result = {"acceptance_contract": {**copy.deepcopy(contract), "digest": digest(contract)}}
    return PreparedEvidenceOperation(command, (), result, volatile_fields=("imported_at",))


def run_acceptance_contract_import(request: AcceptanceContractImportRequest, repository: object) -> dict:
    return execute_evidence_operation(repository, lambda state: prepare_acceptance_contract_import(state, now=request.now, raw=request.raw))


def acceptance_contract_status(state: object) -> dict:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    contract = state.get("acceptance_contract")
    if not isinstance(contract, dict):
        return status(None)
    stored = dict(contract)
    stored.pop("imported_at", None)
    result = status(stored)
    if result["present"]:
        result["imported_at"] = contract.get("imported_at")
    return result
