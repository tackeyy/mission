"""Pure planning-provider activation and eligibility contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any


COMPLEXITY_ORDER = {"Simple": 1, "Standard": 2, "Complex": 3, "Critical": 4}
VALID_PHASES = {"planning", "execution", "review", "scoring", "critic", "synthesis"}
CANONICAL_SELECTION_SOURCES = {
    "automatic",
    "confirmed-user",
    "user-instruction",
    "manual",
    "task-required",
}
SELECTION_SOURCE_ALIASES = {
    "auto": "automatic",
    "user-specified": "user-instruction",
}
KNOWN_TASK_PROFILES = {
    "architecture",
    "documentation",
    "frontend",
    "backend",
    "database",
    "security",
    "testing",
    "infra",
    "product",
    "research",
    "strategy",
    "financial",
    "risk",
    "general",
}
JSON_NUMBER_PATTERN = re.compile(
    r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z"
)
MAX_REGISTRY_NUMBER_TOKEN = 128
MAX_SAFE_INTEGER = 9_007_199_254_740_991


class RegistryContractError(ValueError):
    """A versioned registry cannot be safely interpreted."""

    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        super().__init__(detail or code)


def _reject_duplicate_key(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryContractError("duplicate-registry-key", f"duplicate registry key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise RegistryContractError("invalid-registry-number")


def _check_number_token(token: str) -> None:
    if len(token) > MAX_REGISTRY_NUMBER_TOKEN:
        raise RegistryContractError("number-limit")


def _parse_registry_int(token: str) -> int:
    _check_number_token(token)
    if token.startswith("-") and token.lstrip("-").strip("0") == "":
        raise RegistryContractError("invalid-registry-number")
    try:
        value = int(token)
    except ValueError as error:
        raise RegistryContractError("invalid-registry-number") from error
    if abs(value) > MAX_SAFE_INTEGER:
        raise RegistryContractError("number-limit")
    return value


def _parse_registry_float(token: str) -> float:
    _check_number_token(token)
    try:
        decimal_value = Decimal(token)
    except InvalidOperation as error:
        raise RegistryContractError("invalid-registry-number") from error
    if not decimal_value.is_finite() or (decimal_value.is_zero() and decimal_value.is_signed()):
        raise RegistryContractError("invalid-registry-number")
    value = float(decimal_value)
    if not math.isfinite(value) or (value == 0.0 and not decimal_value.is_zero()):
        raise RegistryContractError("number-limit")
    if Decimal.from_float(value) != decimal_value:
        raise RegistryContractError("number-limit")
    return value


def _strict_json_loads(text: str, *, object_pairs_hook=None) -> Any:
    kwargs = {
        "parse_int": _parse_registry_int,
        "parse_float": _parse_registry_float,
        "parse_constant": _reject_json_constant,
    }
    if object_pairs_hook is not None:
        kwargs["object_pairs_hook"] = object_pairs_hook
    return json.loads(text, **kwargs)


def _validate_v2_document(document: Any) -> list[dict[str, Any]]:
    if not isinstance(document, dict):
        raise RegistryContractError("invalid-registry-contract", "registry document must be an object")
    schema = document.get("schema")
    if schema is None:
        raise RegistryContractError("missing-registry-schema")
    if schema != "mission-specialist-registry/2":
        raise RegistryContractError("unknown-registry-major")
    if "specialists" in document:
        raise RegistryContractError("mixed-registry-version")
    if set(document) != {"schema", "specialists_v2"}:
        raise RegistryContractError("invalid-v2-registry-root")
    candidates = document.get("specialists_v2")
    if not isinstance(candidates, list) or any(not isinstance(item, dict) for item in candidates):
        raise RegistryContractError(
            "invalid-registry-contract", "specialists_v2 must be a list of objects"
        )
    for candidate in candidates:
        for value in candidate.values():
            if isinstance(value, dict):
                if any(isinstance(nested, dict) for nested in value.values()):
                    raise RegistryContractError("unsupported-registry-depth")
                if any(
                    isinstance(nested, list)
                    and any(isinstance(item, (dict, list)) for item in nested)
                    for nested in value.values()
                ):
                    raise RegistryContractError("unsupported-registry-depth")
            elif isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
                raise RegistryContractError("unsupported-registry-depth")
        _validate_v2_candidate_types(candidate)
    return [dict(item) for item in candidates]


def _invalid_candidate_type(field: str) -> None:
    raise RegistryContractError(
        "invalid-v2-candidate-type", f"invalid version 2 candidate field type: {field}"
    )


def _is_exact_bool(value: Any) -> bool:
    return type(value) is bool


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_v2_candidate_types(candidate: dict[str, Any]) -> None:
    if "enabled" in candidate:
        _invalid_candidate_type("enabled")
    for field in ("provider_id", "role", "skill", "name", "kind", "command", "unavailable", "notes"):
        if field in candidate and not isinstance(candidate[field], str):
            _invalid_candidate_type(field)
    for field in ("task_profiles", "profiles", "phases", "args"):
        if field in candidate and not _is_string_list(candidate[field]):
            _invalid_candidate_type(field)
    for field in (
        "required",
        "confirm",
        "bounded",
        "bounded_use",
        "broad_orchestrator",
        "bounded_purpose_required",
        "install_hint",
        "disabled",
    ):
        if field in candidate and not _is_exact_bool(candidate[field]):
            _invalid_candidate_type(field)
    for field in ("env", "risk", "result_contract", "auto_use"):
        if field in candidate and not isinstance(candidate[field], dict):
            _invalid_candidate_type(field)
    if "env" in candidate and any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in candidate["env"].items()
    ):
        _invalid_candidate_type("env")
    if "timeout" in candidate:
        timeout = candidate["timeout"]
        if type(timeout) is not int or not 1 <= timeout <= 86400:
            _invalid_candidate_type("timeout")
    if "max_calls_per_iteration" in candidate:
        calls = candidate["max_calls_per_iteration"]
        if type(calls) is not int or not 1 <= calls <= 1000:
            _invalid_candidate_type("max_calls_per_iteration")
    if "activation" in candidate:
        activation = candidate["activation"]
        if not isinstance(activation, dict):
            _invalid_candidate_type("activation")
        allowed = {"min_complexity", "auto_select_if", "when_any", "explicit_below_min"}
        if set(activation) - allowed:
            _invalid_candidate_type("activation")
        if (
            "min_complexity" in activation
            and not isinstance(activation["min_complexity"], str)
        ):
            _invalid_candidate_type("activation.min_complexity")
        for field in ("auto_select_if", "when_any"):
            if field in activation and not _is_string_list(activation[field]):
                _invalid_candidate_type(f"activation.{field}")
        if (
            "explicit_below_min" in activation
            and not isinstance(activation["explicit_below_min"], str)
        ):
            _invalid_candidate_type("activation.explicit_below_min")


def _strip_yaml_comment(raw: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(raw):
        if quote == '"' and escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "#":
            return raw[:index]
    return raw


def _split_yaml_flow_items(value: str) -> list[str]:
    items: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if quote == '"' and escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == ",":
            items.append(value[start:index].strip())
            start = index + 1
    items.append(value[start:].strip())
    return [item for item in items if item]


def _yaml_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [_yaml_scalar(item) for item in _split_yaml_flow_items(inner)]
    if value.startswith('"'):
        try:
            parsed = _strict_json_loads(value)
        except json.JSONDecodeError as error:
            raise RegistryContractError("invalid-registry-contract", str(error)) from error
        if not isinstance(parsed, str):
            raise RegistryContractError("invalid-registry-contract")
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            raise RegistryContractError("invalid-registry-contract")
        return value[1:-1].replace("''", "'")
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null":
        return None
    if JSON_NUMBER_PATTERN.fullmatch(value):
        return _strict_json_loads(value)
    return value


def _parse_v2_registry_yaml(text: str) -> dict[str, Any]:
    document: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    nested_key: str | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise RegistryContractError("unsupported-registry-depth", f"tabs at line {number}")
        line = _strip_yaml_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent not in {0, 2, 4, 6}:
            raise RegistryContractError("unsupported-registry-depth", f"line {number}")
        if indent == 0:
            if ":" not in stripped:
                raise RegistryContractError("invalid-registry-contract", f"line {number}")
            key, raw_value = (part.strip() for part in stripped.split(":", 1))
            if key in document:
                raise RegistryContractError("duplicate-registry-key", f"duplicate root key: {key}")
            if key == "specialists_v2":
                if raw_value:
                    raise RegistryContractError("invalid-registry-contract")
                document[key] = candidates
            else:
                document[key] = _yaml_scalar(raw_value)
            current = None
            nested_key = None
            continue
        if indent == 2 and stripped.startswith("- "):
            current = {}
            candidates.append(current)
            nested_key = None
            rest = stripped[2:].strip()
            if ":" not in rest:
                raise RegistryContractError("invalid-registry-contract", f"line {number}")
            key, raw_value = (part.strip() for part in rest.split(":", 1))
            current[key] = _yaml_scalar(raw_value)
            continue
        if current is None or ":" not in stripped:
            raise RegistryContractError("invalid-registry-contract", f"line {number}")
        key, raw_value = (part.strip() for part in stripped.split(":", 1))
        if indent == 4:
            if key in current:
                raise RegistryContractError("duplicate-registry-key", f"candidate key: {key}")
            if raw_value:
                current[key] = _yaml_scalar(raw_value)
                nested_key = None
            else:
                current[key] = {}
                nested_key = key
            continue
        if indent == 6:
            if nested_key is None or not isinstance(current.get(nested_key), dict):
                raise RegistryContractError("unsupported-registry-depth", f"line {number}")
            nested = current[nested_key]
            if key in nested:
                raise RegistryContractError("duplicate-registry-key", f"nested key: {key}")
            nested[key] = _yaml_scalar(raw_value)
            continue
    return document


def parse_v2_registry(text: str) -> list[dict[str, Any]]:
    if text.lstrip().startswith(("{", "[")):
        try:
            document = _strict_json_loads(
                text, object_pairs_hook=_reject_duplicate_key
            )
        except RegistryContractError:
            raise
        except json.JSONDecodeError as error:
            raise RegistryContractError("invalid-registry-contract", str(error)) from error
    else:
        document = _parse_v2_registry_yaml(text)
    return _validate_v2_document(document)


def parse_v2_registry_json(text: str) -> list[dict[str, Any]]:
    """Compatibility alias retained for the first v2 integration seam."""
    try:
        return parse_v2_registry(text)
    except RegistryContractError:
        raise


def detect_registry_version(text: str) -> int:
    """Classify an explicit registry from its document root only.

    This intentionally does not search raw text: nested keys and scalar documentation
    must not turn a version 1 document into version 2. Malformed JSON is sent through
    the strict version 2 parser so it fails closed instead of reaching the legacy
    permissive loader.
    """
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            document = _strict_json_loads(
                text, object_pairs_hook=_reject_duplicate_key
            )
        except RegistryContractError:
            raise
        except json.JSONDecodeError:
            return 2
        if not isinstance(document, dict):
            return 2
        root_keys = set(document)
        schema = document.get("schema")
    else:
        root_keys: set[str] = set()
        schema = None
        for raw in text.splitlines():
            line = _strip_yaml_comment(raw).rstrip()
            if not line.strip() or line.startswith((" ", "\t")) or ":" not in line:
                continue
            key, raw_value = (part.strip() for part in line.split(":", 1))
            if key in root_keys:
                raise RegistryContractError(
                    "duplicate-registry-key", f"duplicate root key: {key}"
                )
            root_keys.add(key)
            if key == "schema":
                schema = _yaml_scalar(raw_value)
    if "specialists_v2" in root_keys:
        return 2
    if isinstance(schema, str) and schema.startswith("mission-specialist-registry/"):
        return 2
    return 1


def _context_digest(context: dict[str, Any]) -> str:
    payload = json.dumps(
        context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def value_digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RegistryContractError("invalid-registry-number") from error
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def registry_entry_digest(candidate: dict[str, Any]) -> str:
    public = {
        key: value
        for key, value in candidate.items()
        if key not in {"source", "registry_entry_digest"} and not key.startswith("_")
    }
    return value_digest(public)


def normalize_selection_source(raw: Any) -> dict[str, str]:
    if not isinstance(raw, str) or not raw:
        raise ValueError("selection source is required")
    canonical = SELECTION_SOURCE_ALIASES.get(raw, raw)
    if canonical not in CANONICAL_SELECTION_SOURCES:
        raise ValueError(f"unknown selection source: {raw}")
    return {"selection_source": canonical, "selection_source_raw": raw}


def _normalize_activation(candidate: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    raw_activation = candidate.get("activation")
    raw_legacy = candidate.get("auto_use")
    if (
        candidate.get("registry_version") == 2
        and (
            candidate.get("_v2_auto_use_present") is True
            or (
                "_v2_auto_use_present" not in candidate
                and "auto_use" in candidate
            )
        )
    ):
        return {}, "conflicting-activation-config"
    if raw_activation is not None and raw_legacy:
        return {}, "conflicting-activation-config"
    if raw_activation is not None and not isinstance(raw_activation, dict):
        return {}, "conflicting-activation-config"
    if raw_legacy is not None and not isinstance(raw_legacy, dict):
        return {}, "conflicting-activation-config"

    if isinstance(raw_activation, dict):
        activation = dict(raw_activation)
    elif isinstance(raw_legacy, dict):
        activation = {
            "min_complexity": raw_legacy.get("min_complexity"),
            "auto_select_if": ["profile"],
            "explicit_below_min": "deny",
        }
        if "when" in raw_legacy:
            activation["when_any"] = raw_legacy.get("when")
    else:
        activation = {
            "min_complexity": None,
            "auto_select_if": ["profile"],
            "explicit_below_min": "deny",
        }

    activation.setdefault("auto_select_if", ["profile"])
    activation.setdefault("explicit_below_min", "deny")
    minimum = activation.get("min_complexity")
    triggers = activation.get("auto_select_if")
    if minimum is not None and minimum not in COMPLEXITY_ORDER:
        return activation, "unknown-complexity"
    if (
        not isinstance(triggers, list)
        or not triggers
        or any(trigger not in {"profile", "complexity"} for trigger in triggers)
        or activation.get("explicit_below_min") != "deny"
    ):
        return activation, "conflicting-activation-config"
    if "when_any" in activation:
        predicates = activation["when_any"]
        if not isinstance(predicates, list) or any(not isinstance(value, str) for value in predicates):
            return activation, "unsupported-activation-predicate"
        if any(value not in KNOWN_TASK_PROFILES | {"stalled_iteration"} for value in predicates):
            return activation, "unsupported-activation-predicate"
    return activation, None


def _result(
    eligible: bool,
    reason_code: str,
    matched: list[str],
    context: dict[str, Any],
    activation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "eligible": eligible,
        "reason_code": reason_code,
        "matched_conditions": matched,
        "context_digest": _context_digest(context),
        "activation_digest": value_digest(activation),
        "normalized_activation": activation,
    }


def evaluate_provider_eligibility(
    candidate: dict[str, Any],
    mission_context: dict[str, Any],
    requested_phase: str | None,
    selection_source: str,
) -> dict[str, Any]:
    """Evaluate registry activation without performing I/O or mutating state."""
    source = normalize_selection_source(selection_source)["selection_source"]
    if candidate.get("_registry_error"):
        return _result(
            False,
            str(candidate["_registry_error"]),
            [],
            mission_context,
            {},
        )
    activation, activation_error = _normalize_activation(candidate)
    if activation_error:
        return _result(False, activation_error, [], mission_context, activation)

    phases = candidate.get("phases")
    if (
        not isinstance(phases, list)
        or not phases
        or any(not isinstance(phase, str) or phase not in VALID_PHASES for phase in phases)
    ):
        return _result(False, "invalid-phase-allow-list", [], mission_context, activation)
    if requested_phase is not None and requested_phase not in phases:
        return _result(False, "phase-not-allowed", [], mission_context, activation)
    minimum = activation.get("min_complexity")
    current = mission_context.get("complexity")
    if minimum and current not in COMPLEXITY_ORDER:
        return _result(False, "unknown-complexity", [], mission_context, activation)
    if minimum and COMPLEXITY_ORDER[current] < COMPLEXITY_ORDER.get(minimum, 10**6):
        return _result(False, "below-min-complexity", [], mission_context, activation)

    profile = mission_context.get("task_profile") or {}
    active_profiles = {profile.get("primary"), *(profile.get("secondary") or [])} - {None}
    configured_profiles = set(candidate.get("task_profiles") or [])
    profile_match = bool(active_profiles & configured_profiles)
    complexity_match = bool(
        minimum
        and current in COMPLEXITY_ORDER
        and COMPLEXITY_ORDER[current] >= COMPLEXITY_ORDER[minimum]
    )
    matched = []
    triggers = activation["auto_select_if"]
    if "profile" in triggers and profile_match:
        matched.append("profile")
    if "complexity" in triggers and complexity_match:
        matched.append("complexity")

    if source == "automatic":
        if not matched:
            return _result(
                False, "activation-predicate-not-matched", [], mission_context, activation
            )
        if "when_any" in activation:
            predicates = activation["when_any"]
            stalled = (
                mission_context.get("iteration", 1) >= 2
                and mission_context.get("previous_iteration_passed") is False
            )
            when_matches = [
                value
                for value in predicates
                if value in active_profiles or (value == "stalled_iteration" and stalled)
            ]
            if not when_matches:
                return _result(
                    False,
                    "activation-predicate-not-matched",
                    matched,
                    mission_context,
                    activation,
                )
            matched.extend(f"when:{value}" for value in when_matches)
    elif not matched:
        matched.append("explicit")

    if candidate.get("available") is False:
        return _result(False, "provider-unavailable", matched, mission_context, activation)

    reason = "eligible-complexity" if "complexity" in matched else "eligible-profile"
    return _result(True, reason, matched, mission_context, activation)
