"""Immutable, portable acceptance-contract validation (#877)."""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath


SCHEMA = "mission-acceptance-contract/1"
REVIEW_POLICY = "fresh-required"


class AcceptanceContractError(ValueError):
    pass


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise AcceptanceContractError("duplicate-json-key")
        value[key] = item
    return value


def _constant(_):
    raise AcceptanceContractError("invalid-json-number")


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AcceptanceContractError("canonical-json-invalid") from exc


def load(raw: bytes) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, ValueError, AcceptanceContractError) as exc:
        raise AcceptanceContractError("contract-json-invalid") from exc
    return validate(value)


def _text(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise AcceptanceContractError(code)
    return value


def _path(value: object) -> str:
    path = _text(value, "target-path-invalid")
    if ("\\" in path or path.startswith("/") or path.startswith("//")
            or len(path) >= 2 and path[1] == ":"
            or any(part in {"", ".", ".."} for part in path.split("/"))):
        raise AcceptanceContractError("target-path-invalid")
    return path


def validate(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema", "mission_id", "requirement_text", "requirement_digest", "revision",
        "review_policy", "requirements", "criteria", "coverage",
    }:
        raise AcceptanceContractError("contract-fields-invalid")
    if value["schema"] != SCHEMA or value["review_policy"] != REVIEW_POLICY:
        raise AcceptanceContractError("contract-schema-invalid")
    _text(value["mission_id"], "mission-id-invalid")
    text = _text(value["requirement_text"], "requirement-text-invalid")
    expected = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    if value["requirement_digest"] != expected:
        raise AcceptanceContractError("requirement-digest-invalid")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise AcceptanceContractError("revision-invalid")
    requirements = value["requirements"]
    criteria = value["criteria"]
    if not isinstance(requirements, list) or not requirements or not isinstance(criteria, list) or not criteria:
        raise AcceptanceContractError("contract-empty")
    cursor = 0
    required_ids: set[str] = set()
    for item in requirements:
        if not isinstance(item, dict) or set(item) != {"id", "start", "end", "text", "classification"}:
            raise AcceptanceContractError("requirement-entry-invalid")
        identifier = _text(item["id"], "requirement-id-invalid")
        if identifier in required_ids:
            raise AcceptanceContractError("duplicate-requirement-id")
        required_ids.add(identifier)
        if type(item["start"]) is not int or type(item["end"]) is not int or item["start"] != cursor or item["end"] <= cursor or item["end"] > len(text):
            raise AcceptanceContractError("requirement-span-invalid")
        if item["text"] != text[item["start"]:item["end"]]:
            raise AcceptanceContractError("requirement-span-text-invalid")
        if (
            not isinstance(item["classification"], str)
            or item["classification"] not in {"obligation", "context"}
        ):
            raise AcceptanceContractError("requirement-classification-invalid")
        cursor = item["end"]
    if cursor != len(text):
        raise AcceptanceContractError("requirement-coverage-invalid")
    criterion_ids: set[str] = set()
    for item in criteria:
        if not isinstance(item, dict) or set(item) != {"id", "requirement_ids", "expected", "required", "prohibited_side_effects", "verification_kind", "target_path", "command_id"}:
            raise AcceptanceContractError("criterion-entry-invalid")
        identifier = _text(item["id"], "criterion-id-invalid")
        if identifier in criterion_ids:
            raise AcceptanceContractError("duplicate-criterion-id")
        criterion_ids.add(identifier)
        ids = item["requirement_ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or x not in required_ids for x in ids):
            raise AcceptanceContractError("criterion-requirements-invalid")
        _text(item["expected"], "criterion-expected-invalid")
        if type(item["required"]) is not bool or not isinstance(item["prohibited_side_effects"], list) or not all(isinstance(x, str) and x.strip() for x in item["prohibited_side_effects"]):
            raise AcceptanceContractError("criterion-fields-invalid")
        if item["verification_kind"] != "command" or not isinstance(item["command_id"], str) or not item["command_id"].strip():
            raise AcceptanceContractError("criterion-verifier-invalid")
        _path(item["target_path"])
    if value["coverage"] != {"status": "pending"}:
        raise AcceptanceContractError("coverage-invalid")
    normalized = json.loads(canonical_bytes(value))
    return normalized


def digest(contract: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(contract)).hexdigest()


def status(contract: object) -> dict:
    if not isinstance(contract, dict):
        return {"present": False}
    return {
        "present": True,
        "digest": digest(contract),
        "coverage": contract.get("coverage"),
        "requirement_text": contract.get("requirement_text"),
        "requirements": contract.get("requirements"),
        "criteria": contract.get("criteria"),
    }
