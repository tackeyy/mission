"""Provider approval use case; the CLI only supplies dependency ports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from pathlib import Path
import hashlib
import json
import re
import sys

from .approval_budget import admit_approval, settle_approval, settle_rejected_approval

@dataclass(frozen=True)
class ProviderApprovalServices:
    resolve_state_file: Callable
    gate: Callable
    compatibility_arguments: Callable
    canonical_operation: Callable
    repository: Callable
    state_dir: Callable
    resolve_verifier: Callable
    execute_verifier: Callable
    atomic_write_bytes: Callable
    now: Callable
    inspect_repository_bytes: Callable
    v5_format: object
    selection_error: type[Exception]


def run_verify_provider_approval(args, services):
    """Ask only a host-registered verifier to turn evidence into a receipt."""
    cwd = Path.cwd()
    sf = services.resolve_state_file(cwd)
    if not sf.exists():
        services.gate("state-missing")
    session_id = sf.stem
    _command_name_verify = "specialists-verify-approval"
    _command_arguments_verify = {
        "preflight_id": str(args.preflight_id),
        "evidence_ref": str(args.evidence_ref),
        "approval_verifier": str(args.approval_verifier),
    }
    try:
        _target_bytes_verify = sf.read_bytes()
        _inspected_verify = services.inspect_repository_bytes(_target_bytes_verify, expected_session_id=session_id)
        _target_digest_verify = "sha256:" + hashlib.sha256(_target_bytes_verify).hexdigest()
        _caller_op_verify, _op_args_verify = services.compatibility_arguments(
            _command_arguments_verify, target_digest=_target_digest_verify,
            require_caller=_inspected_verify.format is services.v5_format,
        )
        _op_id_verify, _op_cmd_verify = services.canonical_operation(
            session_id, _command_name_verify, _op_args_verify,
            caller_operation_id=_caller_op_verify,
        )
    except (OSError, services.selection_error, ValueError) as _err_verify:
        print(f"ERROR: {_err_verify}", file=sys.stderr)
        sys.exit(2)
    _repo_verify = services.repository(
        cwd, sf, stamp=True, strict_read=True, session_id=session_id,
        operation_id=_op_id_verify, operation_command=_op_cmd_verify,
        operation_command_type=_command_name_verify,
    )
    # Observe operation replay before creating a new dispatch reservation.
    with _repo_verify.transaction():
        _repo_verify.load()
        if getattr(_repo_verify, 'operation_replayed', False):
            print(json.dumps({'ok': True, 'preflight_id': args.preflight_id, 'status': 'approved'}, ensure_ascii=False))
            return
    budget_repository = services.repository(cwd, sf, stamp=True, strict_read=True, session_id=session_id)
    try:
        budget = admit_approval(budget_repository, 'verify-approval', args.preflight_id,
                               evidence_ref=args.evidence_ref, verifier_name=args.approval_verifier)
    except KeyError:
        services.gate('approval-evidence-invalid')
    except ValueError as error:
        services.gate(str(error))
    try:
        with _repo_verify.transaction():
            data = _repo_verify.load()
            replayed = bool(getattr(_repo_verify, "operation_replayed", False))
            if not replayed:
                pointer = (data.get("provider_preflights") or {}).get(args.preflight_id)
                if not isinstance(pointer, dict) or pointer.get("status") != "awaiting-approval":
                    services.gate("preflight-not-awaiting-approval")
                try:
                    packet_path = services.state_dir(cwd) / str(pointer["artifact_path"])
                    packet_bytes = packet_path.read_bytes()
                    if "sha256:" + hashlib.sha256(packet_bytes).hexdigest() != pointer["outbound_packet_digest"]:
                        services.gate("preflight-artifact-invalid")
                    packet = json.loads(packet_bytes)
                    request = {
                        "schema": "mission-provider-approval-request/1", "preflight_id": args.preflight_id,
                        "session_id": packet["session_id"], "mission_id": packet["mission_id"],
                        "outbound_context_digest": packet["outbound_context_digest"], "invocation_id": packet["invocation_id"],
                        "outbound_packet_digest": pointer["outbound_packet_digest"],
                        "registry_entry_digest": packet["provider"]["registry_entry_digest"],
                        "selection_id": packet["selection"]["id"], "selection_source": packet["selection"]["source"],
                        "iteration": packet["iteration"], "phase": packet["phase"], "risk_scopes": packet["risk_scopes"],
                        "evidence_ref": args.evidence_ref,
                    }
                    descriptor = services.resolve_verifier(cwd, args.approval_verifier)
                    if descriptor is None:
                        services.gate("verifier-untrusted")
                    options = {'budget': budget} if budget is not None else {}
                    evidence = services.execute_verifier(descriptor, request, cwd=cwd, **options)
                    if not isinstance(evidence, dict) or evidence.get("schema") != "approval-evidence/1":
                        services.gate("approval-evidence-invalid")
                    if evidence.get("verifier_id") != args.approval_verifier:
                        services.gate("verifier-untrusted")
                    for key, value in request.items():
                        if key != "schema" and evidence.get(key) != value:
                            services.gate("approval-evidence-binding-mismatch")
                    expires_at = evidence.get("expires_at")
                    nonce = evidence.get("single_use_nonce")
                    if not isinstance(expires_at, str) or not isinstance(nonce, str) or not re.fullmatch(r"[0-9A-Za-z_-]{32,128}", nonce):
                        services.gate("approval-evidence-invalid")
                    receipt = {
                        "schema": "mission-provider-approval-receipt/1",
                        **{key: request[key] for key in request if key not in {"schema", "risk_scopes", "evidence_ref"}},
                        "approved_scopes": request["risk_scopes"], "expires_at": expires_at,
                        "single_use_nonce": nonce, "approval_provenance": {
                            "issuer_id": evidence.get("issuer_id"), "verifier_id": args.approval_verifier,
                            "verifier_version": evidence.get("verifier_version"), "proof_kind": evidence.get("proof_kind"),
                            "proof_digest": evidence.get("proof_digest"), "actor_kind": evidence.get("actor_kind"),
                            "actor_id": evidence.get("actor_id"),
                        },
                    }
                    receipt_bytes = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                    receipt_dir = services.state_dir(cwd) / "private-receipts"; receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                    receipt_file = receipt_dir / f"{args.preflight_id}.json"; services.atomic_write_bytes(receipt_file, receipt_bytes)
                    pointer["receipt"] = {"artifact_path": str(receipt_file.resolve().relative_to(services.state_dir(cwd).resolve())),
                                          "digest": "sha256:" + hashlib.sha256(receipt_bytes).hexdigest()}
                    pointer["status"] = "approved"
                    data["updated_at"] = services.now()
                    settle_approval(data, budget, completed=True)
                    _repo_verify.save(data)
                except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
                    # Exception type is bounded and contains no evidence values.
                    print(f"ERROR: provider approval verification failed: {type(error).__name__}", file=sys.stderr)
                    services.gate("approval-evidence-invalid")
    except BaseException:
        settle_rejected_approval(budget_repository, budget)
        raise
    if replayed:
        settle_rejected_approval(budget_repository, budget)
    print(json.dumps({"ok": True, "preflight_id": args.preflight_id, "status": "approved"}, ensure_ascii=False))

