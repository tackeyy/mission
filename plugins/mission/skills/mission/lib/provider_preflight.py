"""Exact, side-effect-free provider preflight and approval receipt contracts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterable, Mapping


MAX_INPUT_BYTES = 1024 * 1024
REQUIRED_STRICT_CAPABILITIES = frozenset({
    "filesystem-namespace", "readonly-mount", "env-reset", "network-policy",
})
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[0-9A-Za-z_-]{32,128}\Z")
_SECRET_ASSIGNMENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[^\s\r\n]+")


class ProviderPreflightError(ValueError):
    """A preflight, approval, or live revalidation contract is unsafe."""


def canonical_json_bytes(value: object) -> bytes:
    """Canonical UTF-8 JSON bytes used for both display projection and stdin."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _redacted_argv(argv: object) -> list[str]:
    """Keep switches but never serialize argument values into packets."""
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise ProviderPreflightError("argv-invalid")
    safe = []
    for index, item in enumerate(argv):
        if index == 0 or (item.startswith("-") and "=" not in item):
            safe.append(item)
        elif re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*_REF\}", item):
            safe.append(item)
        elif item.startswith("--") and "=" in item:
            safe.append(item.split("=", 1)[0] + "=${ARG_%d_REF}" % index)
        else:
            safe.append("${ARG_%d_REF}" % index)
    return safe


def _require_digest(value: object, code: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise ProviderPreflightError(code)
    return value


def _relative_regular_path(source: object, root: object) -> tuple[Path, str]:
    try:
        raw = os.fspath(source)
    except TypeError as error:
        raise ProviderPreflightError("input-path-invalid") from error
    if "\x00" in raw:
        raise ProviderPreflightError("input-path-invalid")
    root_path = Path(root).resolve(strict=True)
    path = Path(raw)
    if not path.is_absolute():
        path = root_path / path
    try:
        relative = path.resolve(strict=True).relative_to(root_path)
    except (OSError, ValueError) as error:
        raise ProviderPreflightError("input-path-outside-root") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ProviderPreflightError("input-path-invalid")
    return path, relative.as_posix()


def _redact_text(value: str) -> str:
    return _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=${{REDACTED}}", value)


def safe_input_snapshot(source: object, *, root: object, maximum_bytes: int = MAX_INPUT_BYTES) -> dict[str, Any]:
    """Read one bounded regular file once and preserve only a safe snapshot."""
    path, relative = _relative_regular_path(source, root)
    try:
        before = os.lstat(path)
    except OSError as error:
        raise ProviderPreflightError("input-unreadable") from error
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_nlink != 1:
        raise ProviderPreflightError("input-not-regular")
    if before.st_size > maximum_bytes:
        raise ProviderPreflightError("input-too-large")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ProviderPreflightError("input-open-failed") from error
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > maximum_bytes:
            raise ProviderPreflightError("input-not-regular")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise ProviderPreflightError("input-too-large")
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if (before.st_dev, before.st_ino, before.st_size) != (opened.st_dev, opened.st_ino, opened.st_size):
        raise ProviderPreflightError("input-identity-drift")
    if (opened.st_dev, opened.st_ino, opened.st_size) != (after.st_dev, after.st_ino, after.st_size):
        raise ProviderPreflightError("input-changed-while-read")
    content = b"".join(chunks)
    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProviderPreflightError("input-not-utf8") from error
    redacted = _redact_text(decoded)
    redacted_bytes = redacted.encode("utf-8")
    return {
        "path": relative,
        "digest": _digest_bytes(redacted_bytes),
        "size": len(redacted_bytes),
        "disposition": "include",
        "redacted_content": redacted,
        "identity": {"device": opened.st_dev, "inode": opened.st_ino},
    }


def validate_execution_context(context: object, *, approved_scopes: Iterable[str] = ()) -> None:
    if not isinstance(context, Mapping):
        raise ProviderPreflightError("execution-context-invalid")
    isolation = context.get("isolation")
    ambient = context.get("ambient_scopes")
    if not isinstance(ambient, list) or not all(isinstance(item, str) and item for item in ambient):
        raise ProviderPreflightError("execution-context-invalid")
    if isolation == "declared-ambient":
        if not set(ambient).issubset(set(approved_scopes)):
            raise ProviderPreflightError("ambient-scope-unapproved")
        return
    if isolation != "strict":
        raise ProviderPreflightError("execution-isolation-invalid")
    isolator = context.get("isolator")
    if not isinstance(isolator, Mapping) or isolator.get("schema") != "execution-isolator/1":
        raise ProviderPreflightError("isolator-unavailable")
    if isolator.get("host_support") is not True:
        raise ProviderPreflightError("isolator-untrusted")
    _require_digest(isolator.get("policy_digest"), "isolator-policy-invalid")
    capabilities = isolator.get("enforced_capabilities")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise ProviderPreflightError("isolator-capability-missing")
    if not REQUIRED_STRICT_CAPABILITIES.issubset(set(capabilities)):
        raise ProviderPreflightError("isolator-capability-missing")
    if context.get("cwd") != "session-local-empty" or context.get("network_destination_policy") != "verified":
        raise ProviderPreflightError("strict-context-invalid")
    if context.get("resource_mounts") != [] or not isinstance(context.get("env_allowlist"), list):
        raise ProviderPreflightError("strict-context-invalid")


def strict_spawn(attestation: object, packet: bytes, backend) -> object:
    """Call only a host-supplied strict backend after complete attestation.

    The portable core intentionally ships no backend.  A host adapter must
    supply this callable and enforce the attested namespace, mounts, env reset,
    and network policy; plain subprocess is never a strict backend.
    """
    context = {
        "isolation": "strict", "cwd": "session-local-empty", "resource_mounts": [],
        "env_allowlist": [], "ambient_scopes": [], "network_destination_policy": "verified",
        "isolator": attestation,
    }
    validate_execution_context(context)
    if not isinstance(packet, bytes) or not callable(backend):
        raise ProviderPreflightError("isolator-unavailable")
    return backend(packet, dict(attestation))


def dispatch_prepared_packet(
    execution_context: object,
    expected_policy_digest: object,
    packet: bytes,
    ambient_callable,
    strict_backend_resolver,
) -> object:
    """Dispatch exact bytes; a strict packet can only reach its host backend."""
    if not isinstance(execution_context, Mapping):
        raise ProviderPreflightError("execution-context-invalid")
    if execution_context.get("isolation") != "strict":
        validate_execution_context(execution_context, approved_scopes=execution_context.get("ambient_scopes") or ())
        if not callable(ambient_callable):
            raise ProviderPreflightError("execution-context-invalid")
        return ambient_callable(packet)
    attestation = execution_context.get("isolator")
    _require_digest(expected_policy_digest, "isolator-policy-invalid")
    if not isinstance(attestation, Mapping) or attestation.get("policy_digest") != expected_policy_digest:
        raise ProviderPreflightError("isolator-drift")
    if not callable(strict_backend_resolver):
        raise ProviderPreflightError("isolator-unavailable")
    current, backend = strict_backend_resolver(dict(attestation))
    if current is None or backend is None:
        raise ProviderPreflightError("isolator-unavailable")
    if not isinstance(current, Mapping) or dict(current) != dict(attestation):
        raise ProviderPreflightError("isolator-drift")
    return strict_spawn(current, packet, backend)


def _packet_projection(subject: Mapping[str, Any], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    required = (
        "session_id", "mission_id", "mission", "provider_id", "registry_entry_digest", "selection_id",
        "selection_source", "invocation_id", "iteration", "phase", "destination", "risk_scopes",
        "quota_mode", "effective_argv", "env_keys", "execution_context",
    )
    if any(key not in subject for key in required):
        raise ProviderPreflightError("preflight-subject-incomplete")
    if not isinstance(subject["mission"], str) or not isinstance(subject["provider_id"], str):
        raise ProviderPreflightError("preflight-subject-invalid")
    _require_digest(subject["registry_entry_digest"], "registry-digest-invalid")
    if not isinstance(subject["risk_scopes"], list) or not all(isinstance(item, str) and item for item in subject["risk_scopes"]):
        raise ProviderPreflightError("risk-scope-invalid")
    effective_argv = _redacted_argv(subject["effective_argv"])
    if not isinstance(subject["env_keys"], list) or not all(isinstance(item, str) for item in subject["env_keys"]):
        raise ProviderPreflightError("env-keys-invalid")
    # A declared-ambient preflight is permitted to describe its complete
    # ambient scope; live execution later requires those scopes in a receipt.
    execution_context = subject["execution_context"]
    validate_execution_context(
        execution_context,
        approved_scopes=(execution_context.get("ambient_scopes") or []),
    )
    if not isinstance(subject["destination"], Mapping) or set(subject["destination"]) != {"kind", "display_name"}:
        raise ProviderPreflightError("destination-unverified")
    packets = []
    manifests = []
    for item in inputs:
        if not isinstance(item, Mapping) or item.get("disposition") != "include":
            raise ProviderPreflightError("input-manifest-invalid")
        digest = _require_digest(item.get("digest"), "input-digest-invalid")
        text = item.get("redacted_content")
        if not isinstance(text, str) or not isinstance(item.get("path"), str) or type(item.get("size")) is not int:
            raise ProviderPreflightError("input-manifest-invalid")
        if _digest_bytes(text.encode("utf-8")) != digest or len(text.encode("utf-8")) != item["size"]:
            raise ProviderPreflightError("input-snapshot-drift")
        manifests.append({key: item[key] for key in ("path", "digest", "size", "disposition")})
        packets.append({**manifests[-1], "content": text})
    manifests.sort(key=lambda item: item["path"])
    packets.sort(key=lambda item: item["path"])
    execution = dict(subject["execution_context"])
    execution["env_allowlist"] = sorted(execution["env_allowlist"])
    execution["ambient_scopes"] = sorted(execution["ambient_scopes"])
    execution["resource_mounts"] = sorted(execution["resource_mounts"])
    return dict(sorted({
        "schema": "mission-provider-outbound-packet/1",
        "session_id": subject["session_id"], "mission_id": subject["mission_id"], "mission": subject["mission"],
        "provider": {"id": subject["provider_id"], "registry_entry_digest": subject["registry_entry_digest"], "kind": "command"},
        "selection": {"id": subject["selection_id"], "source": subject["selection_source"]},
        "invocation_id": subject["invocation_id"], "iteration": subject["iteration"], "phase": subject["phase"],
        "destination": dict(subject["destination"]), "inputs": packets,
        "redaction_policy_version": "1", "risk_scopes": sorted(set(subject["risk_scopes"])),
        "quota_mode": subject["quota_mode"], "effective_argv": effective_argv,
        "env_keys": sorted(set(subject["env_keys"])), "execution_context": execution,
    }.items())), manifests


def build_preflight(subject: Mapping[str, Any], inputs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a pure preflight record without spawning a provider or browser."""
    packet, manifest = _packet_projection(subject, [dict(item) for item in inputs])
    context = {key: packet[key] for key in packet if key not in {"schema", "inputs"}}
    context["input_manifest"] = manifest
    context_digest = _digest_bytes(canonical_json_bytes(context))
    packet["outbound_context_digest"] = context_digest
    packet = dict(sorted(packet.items()))
    bytes_ = canonical_json_bytes(packet)
    digest = _digest_bytes(bytes_)
    preflight_id = "pf_" + hashlib.sha256((context_digest + digest).encode("ascii")).hexdigest()[:32]
    return {
        "schema": "mission-provider-preflight/1", "preflight_id": preflight_id,
        "session_id": packet["session_id"], "mission_id": packet["mission_id"],
        "outbound_context_digest": context_digest, "invocation_id": packet["invocation_id"],
        "provider_id": packet["provider"]["id"], "registry_entry_digest": packet["provider"]["registry_entry_digest"],
        "selection_id": packet["selection"]["id"], "selection_source": packet["selection"]["source"],
        "iteration": packet["iteration"], "phase": packet["phase"], "outbound_packet_digest": digest,
        "destination": packet["destination"], "input_manifest": manifest, "risk_scopes": packet["risk_scopes"],
        "quota_mode": packet["quota_mode"],
        "command_preview": {"argv_redacted": packet["effective_argv"], "env_keys": packet["env_keys"]},
        "execution_context": packet["execution_context"], "live_effects": ["provider-process", "external-send"],
        "requires_approval": bool(packet["risk_scopes"]), "outbound_packet": packet,
        "outbound_packet_bytes": bytes_,
    }


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ProviderPreflightError("receipt-expiry-invalid")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ProviderPreflightError("receipt-expiry-invalid") from error


def validate_receipt(preflight: Mapping[str, Any], receipt: Mapping[str, Any], *, trusted_verifiers: Mapping[str, str], now: object) -> None:
    if receipt.get("schema") != "mission-provider-approval-receipt/1":
        raise ProviderPreflightError("receipt-schema-invalid")
    binding_fields = ("preflight_id", "session_id", "mission_id", "outbound_context_digest", "invocation_id", "outbound_packet_digest", "registry_entry_digest", "selection_id", "selection_source", "iteration", "phase")
    if any(receipt.get(field) != preflight.get(field) for field in binding_fields):
        raise ProviderPreflightError("receipt-binding-mismatch")
    approved = receipt.get("approved_scopes")
    if not isinstance(approved, list) or not set(preflight.get("risk_scopes") or []).issubset(set(approved)):
        raise ProviderPreflightError("receipt-scope-insufficient")
    if not isinstance(receipt.get("single_use_nonce"), str) or not _NONCE.fullmatch(receipt["single_use_nonce"]):
        raise ProviderPreflightError("receipt-nonce-invalid")
    if _parse_time(receipt.get("expires_at")) <= _parse_time(now):
        raise ProviderPreflightError("receipt-expired")
    provenance = receipt.get("approval_provenance")
    if not isinstance(provenance, Mapping):
        raise ProviderPreflightError("receipt-provenance-invalid")
    verifier_id = provenance.get("verifier_id")
    if not isinstance(verifier_id, str) or trusted_verifiers.get(verifier_id) != provenance.get("verifier_version"):
        raise ProviderPreflightError("verifier-untrusted")
    if provenance.get("proof_kind") not in {"signed-attestation", "opaque-host-event"}:
        raise ProviderPreflightError("approval-proof-invalid")
    _require_digest(provenance.get("proof_digest"), "approval-proof-invalid")


def consume_receipt(preflight: Mapping[str, Any], receipt: Mapping[str, Any], *, used_nonces: set[str], trusted_verifiers: Mapping[str, str], now: object) -> dict[str, str]:
    validate_receipt(preflight, receipt, trusted_verifiers=trusted_verifiers, now=now)
    nonce = str(receipt["single_use_nonce"])
    if nonce in used_nonces:
        raise ProviderPreflightError("receipt-replayed")
    used_nonces.add(nonce)
    return {"status": "consumed", "nonce": nonce}


def verify_live_packet(preflight: Mapping[str, Any], receipt: Mapping[str, Any], subject: Mapping[str, Any], inputs: Iterable[dict[str, Any]], *, trusted_verifiers: Mapping[str, str], now: object) -> bytes:
    """Rebuild and compare every digest before giving immutable bytes to stdin."""
    rebuilt = build_preflight(subject, inputs)
    if rebuilt["outbound_context_digest"] != preflight.get("outbound_context_digest") or rebuilt["outbound_packet_digest"] != preflight.get("outbound_packet_digest") or rebuilt["outbound_packet_bytes"] != preflight.get("outbound_packet_bytes"):
        raise ProviderPreflightError("payload-drift")
    validate_receipt(preflight, receipt, trusted_verifiers=trusted_verifiers, now=now)
    return preflight["outbound_packet_bytes"]
