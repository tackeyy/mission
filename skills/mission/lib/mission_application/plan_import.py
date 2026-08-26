"""Application use case for importing one provider plan result."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re


@dataclass(frozen=True)
class PlanImportRequest:
    input_path: str
    invocation_id: str
    registry: object
    json_output: bool


@dataclass(frozen=True)
class PlanImportWorkspaceServices:
    current_directory: object
    resolve_state_file: object
    path_exists: object
    path_from_string: object
    read_strict_file: object
    read_bytes: object
    inspect_repository_bytes: object
    v5_format: object
    state_dir: object
    join_path: object
    path_is_absolute: object
    path_parts: object
    relative_path: object


@dataclass(frozen=True)
class PlanImportPolicyServices:
    provider_gate: object
    enforce_session_lease_for_write: object
    invocation_by_id: object
    find_provider: object
    require_current_provider_application: object
    require_current_primary_planning_binding: object
    validate_provider_plan_import: object
    planning_failure: object
    sha256_reference_match: object


@dataclass(frozen=True)
class PlanImportStateEffectsServices:
    compatibility_operation_arguments: object
    canonical_compatibility_operation: object
    repository_factory: object
    published_files_transaction: object
    publish_review_archive_transaction: object
    publish_output_transaction: object
    verify_published_file: object
    canonical_plan_bytes: object
    stamp_metadata: object
    clock: object


@dataclass(frozen=True)
class PlanImportResult:
    rendered: str


class PlanImportFailure(ValueError):
    """A repository-selection failure rendered by the CLI adapter."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.exit_code = 2


def _sha256_ref(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _plan_import(request, workspace, policy, state_effects):
    """Validate, publish, verify, then persist one inert plan candidate."""
    cwd = workspace.current_directory()
    sf = workspace.resolve_state_file(cwd)
    if not workspace.path_exists(sf):
        policy.provider_gate("state-missing")
    if not re.fullmatch(r"inv_[0-9a-f]{32}", request.invocation_id):
        policy.provider_gate("invocation-id-invalid")
    try:
        raw = workspace.read_strict_file(workspace.path_from_string(request.input_path))
    except ValueError:
        policy.provider_gate("plan-input-unreadable")

    session_id = sf.stem
    command_name = "specialists-plan-import"
    command_arguments = {"invocation_id": str(request.invocation_id)}
    try:
        target_bytes = workspace.read_bytes(sf)
        inspected = workspace.inspect_repository_bytes(
            target_bytes, expected_session_id=session_id
        )
        target_digest = _sha256_ref(target_bytes)
        caller_operation, operation_arguments = state_effects.compatibility_operation_arguments(
            command_arguments,
            target_digest=target_digest,
            require_caller=inspected.format is workspace.v5_format,
        )
        operation_id, operation_command = state_effects.canonical_compatibility_operation(
            session_id,
            command_name,
            operation_arguments,
            caller_operation_id=caller_operation,
        )
    except (OSError, ValueError) as exc:
        raise PlanImportFailure(str(exc))

    repository = state_effects.repository_factory(
        cwd,
        sf,
        stamp=True,
        strict_read=True,
        session_id=session_id,
        operation_id=operation_id,
        operation_command=operation_command,
        operation_command_type=command_name,
    )
    # Keep repository transaction outermost: published evidence rolls back before
    # a repository transaction can release its state lock.
    with repository.transaction(), state_effects.published_files_transaction() as published_files:
        data = repository.load()
        if getattr(repository, "operation_replayed", False):
            existing = (data.get("provider_plan_imports") or {}).get(request.invocation_id)
            return {"ok": True, "plan_import": existing}
        policy.enforce_session_lease_for_write(sf, data)
        invocation = policy.invocation_by_id(data, request.invocation_id)
        if (
            data.get("planning_policy_version") == 1
            and data.get("planning_strategy") == "provider-primary"
        ):
            policy.require_current_primary_planning_binding(data)
        if not isinstance(invocation, dict):
            policy.provider_gate("invocation-not-found")
        if (
            invocation.get("iteration") != data.get("iteration")
            or invocation.get("phase") != "planning"
            or invocation.get("status") != "completed"
            or invocation.get("lifecycle_state") != "terminal"
        ):
            policy.provider_gate("invocation-not-current-completed-plan")
        provider = policy.find_provider(
            data, str(invocation.get("skill") or invocation.get("role") or "")
        )
        current = policy.require_current_provider_application(
            data,
            provider,
            requested_phase="planning",
            requested_iteration=data.get("iteration"),
            application_kind="result-import",
            selection_source=invocation.get("selection_source"),
            invocation_id=request.invocation_id,
            cwd=cwd,
            registry_args=request,
        )
        contract = current.get("result_contract") if isinstance(current.get("result_contract"), dict) else {}
        if not contract:
            policy.provider_gate("missing-structured-result-contract")
        pointers = data.get("provider_preflights") if isinstance(data.get("provider_preflights"), dict) else {}
        matches = [
            (key, value)
            for key, value in pointers.items()
            if isinstance(value, dict) and value.get("invocation_id") == request.invocation_id
        ]
        if len(matches) != 1:
            policy.provider_gate("preflight-binding-missing")
        preflight_id, pointer = matches[0]
        if pointer.get("status") != "consumed" or pointer.get("consumed_invocation_id") != request.invocation_id:
            policy.provider_gate("preflight-not-consumed")
        artifact_path = pointer.get("artifact_path")
        receipt = pointer.get("receipt") if isinstance(pointer.get("receipt"), dict) else {}
        receipt_path, receipt_digest = receipt.get("artifact_path"), receipt.get("digest")
        try:
            for relative in (artifact_path, receipt_path):
                if not isinstance(relative, str) or not relative:
                    raise ValueError
                relative_path = workspace.path_from_string(relative)
                if workspace.path_is_absolute(relative_path) or ".." in workspace.path_parts(relative_path):
                    raise ValueError
            if not isinstance(receipt_digest, str) or policy.sha256_reference_match(receipt_digest) is None:
                raise ValueError
            packet_bytes = workspace.read_strict_file(
                workspace.join_path(workspace.state_dir(cwd), artifact_path)
            )
            if _sha256_ref(packet_bytes) != pointer.get("outbound_packet_digest"):
                raise ValueError
            receipt_bytes = workspace.read_strict_file(
                workspace.join_path(workspace.state_dir(cwd), receipt_path)
            )
            if _sha256_ref(receipt_bytes) != receipt_digest:
                raise ValueError
        except ValueError:
            policy.provider_gate("consumed-preflight-evidence-invalid")
        expected = {
            "invocation_id": request.invocation_id,
            "preflight_id": preflight_id,
            "outbound_packet_digest": pointer.get("outbound_packet_digest"),
            "selection_id": current.get("selection_id"),
            "selection_source": current.get("eligibility_selection_source") or "automatic",
            "iteration": data.get("iteration"),
        }
        try:
            parsed = policy.validate_provider_plan_import(
                raw,
                expected_binding=expected,
                result_contract=contract,
                workspace=cwd,
            )
        except policy.planning_failure as exc:
            policy.provider_gate(exc.code)
        digest = parsed["raw_result_digest"]
        metadata = {
            "authority": {
                "owner": "mission",
                "may_write_state": False,
                "may_decide_review": False,
                "may_decide_score": False,
                "may_decide_completion": False,
            },
            "provenance": {
                "provider_id": current.get("provider_id"),
                "registry_entry_digest": current.get("registry_entry_digest"),
                "selection_id": expected["selection_id"],
                "selection_source": expected["selection_source"],
                "invocation_id": request.invocation_id,
                "iteration": expected["iteration"],
                "input_outbound_packet_digest": expected["outbound_packet_digest"],
                "raw_result_digest": digest,
            },
            "capability_verification": {
                "selection_verified": True,
                "class_exact_match": True,
                "variant_exact_match": True,
            },
        }
        candidate = {"schema": "mission-plan/1", **parsed["document"], "mission_metadata": metadata}
        canonical = state_effects.canonical_plan_bytes(candidate)
        canonical_digest = _sha256_ref(canonical)
        mission8 = str(data.get("mission_id") or "unknown")[:8]
        raw_name = "plan-result-{}-{}.json".format(mission8, digest[7:23])
        raw_file = published_files.add(
            state_effects.publish_review_archive_transaction(cwd, raw_name, raw)
        )
        candidate_path = workspace.join_path(
            workspace.join_path(workspace.state_dir(cwd), "plans"),
            canonical_digest[7:23] + ".json",
        )
        candidate_file = published_files.add(
            state_effects.publish_output_transaction(candidate_path, canonical)
        )
        previous = (data.get("provider_plan_imports") or {}).get(request.invocation_id)
        generation = (
            previous.get("generation", 0)
            if isinstance(previous, dict) and previous.get("candidate_digest") != canonical_digest
            else 0
        )
        if not generation:
            generation = (previous.get("generation", 0) if isinstance(previous, dict) else 0) or 1
        else:
            generation += 1
        reference = {
            "raw_result_path": str(workspace.relative_path(raw_file.path, cwd)),
            "raw_result_digest": digest,
            "candidate_path": str(workspace.relative_path(candidate_file.path, cwd)),
            "candidate_digest": canonical_digest,
            "invocation_id": request.invocation_id,
            "preflight_id": preflight_id,
            "generation": generation,
        }
        data.setdefault("provider_plan_imports", {})[request.invocation_id] = reference
        state_effects.verify_published_file(raw_file)
        state_effects.verify_published_file(candidate_file)
        data["updated_at"] = state_effects.clock()
        repository.save(state_effects.stamp_metadata(data, cwd))
    return {"ok": True, "plan_import": reference}


def run_plan_import(request, workspace, policy, state_effects):
    result = _plan_import(request, workspace, policy, state_effects)
    return PlanImportResult(
        json.dumps(result, indent=2 if request.json_output else None, ensure_ascii=False)
    )
