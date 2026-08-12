"""Strict, portable structured result contract for planning providers."""
from __future__ import annotations

import hashlib
import json
import math
import os
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

MAX_PLAN_RESULT_BYTES = 4 * 1024 * 1024
RESERVED_DOCUMENT_FIELDS = {"provenance", "authority", "mission_metadata", "passes", "score", "phase", "state_path", "selection_verified"}
RESOURCE_TYPES = {"path", "uri", "record", "dataset", "other"}
EFFECT_CLASSES = {"reversible", "irreversible", "external"}
ACTION_TYPES = {"read", "write", "analyze", "research", "decide", "communicate"}

class PlanContractError(ValueError):
    pass

def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise PlanContractError("duplicate-json-key")
        out[key] = value
    return out

def _constant(_): raise PlanContractError("invalid-json-number")

def canonical_plan_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanContractError("canonical-json-invalid") from exc

def _strict_load(raw: bytes) -> dict:
    if len(raw) > MAX_PLAN_RESULT_BYTES: raise PlanContractError("result-too-large")
    try: text = raw.decode("utf-8")
    except UnicodeDecodeError as exc: raise PlanContractError("invalid-utf8") from exc
    try: value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (json.JSONDecodeError, PlanContractError) as exc: raise PlanContractError(str(exc) or "invalid-json") from exc
    if not isinstance(value, dict): raise PlanContractError("result-not-object")
    return value

def _require(value, name, typ=None):
    if name not in value or (typ is not None and not isinstance(value[name], typ)):
        raise PlanContractError(f"missing-or-invalid-{name}")
    return value[name]

def _validate_path(identifier: object, workspace: Path):
    if not isinstance(identifier, str) or not identifier or "\0" in identifier: raise PlanContractError("path-invalid")
    path = Path(identifier)
    if path.is_absolute() or ".." in path.parts: raise PlanContractError("path-outside-workspace")
    cursor = workspace
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink(): raise PlanContractError("path-symlink-escape")
    target = (workspace / path).resolve(strict=False)
    try: target.relative_to(workspace.resolve())
    except ValueError as exc: raise PlanContractError("path-outside-workspace") from exc
    # Existing links must not escape; new paths need only syntactic containment.
    if (workspace / path).exists() and target != (workspace / path).absolute() and not str(target).startswith(str(workspace.resolve()) + os.sep):
        raise PlanContractError("path-symlink-escape")

def _validate_document(doc: object, workspace: Path):
    if not isinstance(doc, dict): raise PlanContractError("plan-document-invalid")
    def has_reserved(value):
        if isinstance(value, dict):
            return bool(RESERVED_DOCUMENT_FIELDS & set(value)) or any(has_reserved(child) for child in value.values())
        if isinstance(value, list): return any(has_reserved(child) for child in value)
        return False
    if has_reserved(doc): raise PlanContractError("mission-authority-field-injection")
    for name in ("objective", "scope", "assumptions", "steps", "global_acceptance", "stop_conditions"):_require(doc, name)
    if not isinstance(doc["objective"], str) or not doc["objective"].strip(): raise PlanContractError("objective-invalid")
    scope = doc["scope"]
    if not isinstance(scope, dict) or not isinstance(scope.get("resources"), list) or not isinstance(scope.get("actions"), list): raise PlanContractError("scope-invalid")
    for resource in scope["resources"]:
        if not isinstance(resource, dict) or resource.get("type") not in RESOURCE_TYPES or not isinstance(resource.get("identifier"), str) or not isinstance(resource.get("access"), str) or not isinstance(resource.get("constraints"), list) or not all(isinstance(x, str) for x in resource["constraints"]): raise PlanContractError("resource-invalid")
        if resource["type"] == "path": _validate_path(resource["identifier"], workspace)
        elif resource["type"] == "uri":
            parsed = urlsplit(resource["identifier"])
            if parsed.scheme not in {"https", "http"} or not parsed.netloc: raise PlanContractError("uri-invalid")
        elif resource["type"] in {"record", "dataset", "other"}:
            if not resource["identifier"].strip() or any(char.isspace() for char in resource["identifier"]): raise PlanContractError("resource-identifier-invalid")
    for action in scope["actions"]:
        if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES or action.get("effect_class") not in EFFECT_CLASSES: raise PlanContractError("action-invalid")
    if not isinstance(doc["assumptions"], list) or any(not isinstance(x, dict) or not all(isinstance(x.get(k), str) and x[k] for k in ("id","statement","validation")) for x in doc["assumptions"]): raise PlanContractError("assumption-invalid")
    steps = doc["steps"]
    if not isinstance(steps, list) or not steps: raise PlanContractError("steps-invalid")
    ids=set(); edges={}
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("id"), str) or not step["id"] or step["id"] in ids: raise PlanContractError("duplicate-step-id")
        ids.add(step["id"]); edges[step["id"]]=step.get("depends_on")
        if step.get("action") not in ACTION_TYPES or not isinstance(step.get("inputs"), list) or not isinstance(step.get("outputs"), list) or not isinstance(step.get("risk"), str) or not isinstance(step.get("rollback"), str) or not isinstance(step.get("acceptance_checks"), list) or not step["acceptance_checks"] or not all(isinstance(x,str) and x.strip() for x in step["acceptance_checks"]): raise PlanContractError("step-invalid")
        if not isinstance(edges[step["id"]], list) or not all(isinstance(x,str) for x in edges[step["id"]]): raise PlanContractError("dependency-invalid")
        if step.get("risk") in {"irreversible", "external"} and (not step.get("rollback") or not doc["stop_conditions"]): raise PlanContractError("risk-rollback-required")
    if any(dep not in ids for deps in edges.values() for dep in deps): raise PlanContractError("unknown-dependency")
    visiting=set(); done=set()
    def visit(node):
        if node in visiting: raise PlanContractError("dependency-cycle")
        if node not in done:
            visiting.add(node)
            for dep in edges[node]: visit(dep)
            visiting.remove(node); done.add(node)
    for node in ids: visit(node)
    return doc

def parse_provider_result(raw: bytes, *, expected_binding: dict, result_contract: dict, workspace: Path) -> dict:
    result=_strict_load(raw)
    if result.get("schema") != result_contract.get("envelope_schema", "mission-provider-result/1"): raise PlanContractError("envelope-schema-invalid")
    binding=_require(result,"binding",dict)
    if binding != expected_binding: raise PlanContractError("binding-mismatch")
    attestation=_require(result,"capability_attestation",dict); required=result_contract.get("required_capability_class")
    if required and (attestation.get("requested_class") != required or attestation.get("effective_class") != required): raise PlanContractError("capability-class-mismatch")
    if result_contract.get("require_exact_variant") and attestation.get("requested_variant") != attestation.get("effective_variant"): raise PlanContractError("capability-variant-mismatch")
    artifacts=_require(result,"artifacts",list); schema=result_contract.get("artifact_schema","mission-plan/1")
    if len(artifacts)!=1 or not isinstance(artifacts[0],dict) or artifacts[0].get("schema") != schema: raise PlanContractError("artifact-cardinality")
    return {"document":_validate_document(artifacts[0].get("document"),workspace), "raw_result_digest":"sha256:"+hashlib.sha256(raw).hexdigest()}
