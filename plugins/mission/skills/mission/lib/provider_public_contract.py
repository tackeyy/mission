"""Declarative public-state hygiene for specialist provider records."""

from __future__ import annotations

import ipaddress
import math
import re
from typing import Any
from urllib.parse import urlsplit


PORTABLE_PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
OPAQUE_PROVIDER_ID = re.compile(r"provider:sha256:[0-9a-f]{64}\Z")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}\Z")
HTTP_URL = re.compile(r"(?<![A-Za-z0-9+.-])https?://[^\s'\"`]+", re.IGNORECASE)
DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
HEX_ESCAPE = re.compile(r"[0-9A-Fa-f]{2}\Z")
RFC3986_PCHAR = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=:@%"
)
RFC3986_PATH_CHAR = RFC3986_PCHAR | frozenset("/")
RFC3986_QUERY_FRAGMENT_CHAR = RFC3986_PATH_CHAR | frozenset("?")
LOCAL_LOCATOR = re.compile(
    r"(?:"
    r"(?<![A-Za-z0-9+.-])file:[^\s'\"`]*|"
    r"(?<![A-Za-z0-9:])//[^\s'\"`]+|"
    r"(?<![A-Za-z0-9\\])\\\\[^\s'\"`]+|"
    r"(?<![A-Za-z0-9\\])\\(?!\\)[^\s'\"`]+|"
    r"(?<![A-Za-z0-9._+-])~(?:[A-Za-z0-9._-]+|[+-])?(?:[\\/][^\s'\"`]*)?(?![A-Za-z0-9._+-])|"
    r"(?<![A-Za-z0-9])[A-Za-z]:[^\s'\"`]+|"
    r"(?<![A-Za-z0-9/])/(?!/)[^\s'\"`]*"
    r")",
    re.IGNORECASE,
)
PRIVATE_PATH = LOCAL_LOCATOR
VALID_PHASES = {"planning", "execution", "review", "scoring", "critic", "synthesis"}
VALID_COMPLEXITIES = {"Simple", "Standard", "Complex", "Critical"}
VALID_SELECTION_SOURCES = {
    "automatic",
    "confirmed-user",
    "user-instruction",
    "manual",
    "task-required",
}
VALID_INVOCATION_MODES = {
    "core-loop",
    "skill-tool",
    "command-provider",
    "codex-inline",
    "natural-language",
    "fallback-core",
}
VALID_INVOCATION_STATUSES = {
    "selected",
    "started",
    "completed",
    "prepared",
    "awaiting-input",
    "inline-applied",
    "skill-tool-applied",
    "skipped",
    "unavailable",
    "failed",
}
VALID_PROJECTION_STATES = {
    "eligible",
    "conflict",
    "disabled",
    "tombstone",
    "invalid-input-barrier",
}

CANDIDATE_FIELDS = frozenset({
    "provider_id", "id", "role", "skill", "kind", "command", "args", "env", "timeout",
    "task_profiles", "phases", "required", "bounded_use", "bounded_purpose_required",
    "source", "registry_version", "registry_entry_digest", "registry_projection_digest",
    "context_digest", "activation_digest", "score", "installed", "available", "status",
    "first_use", "eligibility_reason", "matched_conditions", "selection_source",
    "selection_source_raw", "eligibility_selection_source", "normalized_activation",
    "risk_confirmation_required", "result_contract_digest", "reason", "selection_id",
})
DIAGNOSTIC_FIELDS = frozenset({
    "provider_id", "source", "reason_code", "field_code", "blocked_config_class",
    "registry_entry_digest", "registry_projection_digest", "context_digest",
    "activation_digest", "selection_source", "selection_source_raw", "requested_phase",
    "current_complexity", "minimum_complexity", "registry_input",
})
ORDERED_INPUT_FIELDS = frozenset({
    "kind", "source", "canonical_identity", "version", "precedence_tier", "order",
    "status", "content_digest", "resolution_mode",
})
BARRIER_FIELDS = frozenset({
    "kind", "source", "canonical_identity", "precedence_tier", "order", "content_digest",
})
EFFECTIVE_FIELDS = frozenset({
    "provider_id", "source", "registry_version", "registry_entry_digest",
    "projection_state", "content_digest",
})
PROJECTION_FIELDS = frozenset({
    "schema", "ordered_inputs", "precedence_barriers", "effective_entries",
    "effective_projection_digest",
})
DECISION_FIELDS = frozenset({
    "policy", "action", "reason", "reason_code", "prompted_user", "user_specified",
    "decision", "lifecycle_state", "selection_id", "confirmation_resolved",
})
PHASE_PLAN_FIELDS = frozenset({"phase", "roles", "providers", "max_providers"})
INVOCATION_FIELDS = frozenset({
    "iteration", "phase", "role", "skill", "mode", "status", "timestamp", "started_at",
    "completed_at", "provider_kind", "exit_code", "timeout", "reason_code",
    "selection_source", "bounded_purpose", "evidence_path", "reason", "notes", "command", "kind",
    "selection_id", "invocation_id", "lifecycle_state", "transitioned_at",
    "host_run_id", "root_run_id", "parent_run_id", "child_run_id", "logical_group_id",
})
ACTIVATION_FIELDS = frozenset({
    "min_complexity", "auto_select_if", "explicit_below_min", "when_any",
})


class SpecialistPublicContractError(ValueError):
    """A specialist record cannot cross a public persistence boundary."""

    def __init__(self, field_path: str):
        self.field_path = field_path
        super().__init__("unsafe-legacy-specialist-record")


def _reject(path: str) -> None:
    raise SpecialistPublicContractError(path)


def _reject_unknown(record: dict[str, Any], allowed: frozenset[str], base: str) -> None:
    unknown = sorted(set(record) - allowed)
    if unknown:
        _reject(f"{base}/{unknown[0]}")


def _safe_plain_text(value: object, *, maximum: int = 2048) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _safe_text(value: object, *, maximum: int = 2048) -> bool:
    return _safe_plain_text(value, maximum=maximum) and not contains_local_locator(value)


def contains_local_locator(value: object) -> bool:
    if not isinstance(value, str):
        return False
    cursor = 0
    for start, end, protected in _http_url_candidates(value):
        if LOCAL_LOCATOR.search(value, cursor, start) is not None:
            return True
        if not protected:
            return True
        cursor = end
    return LOCAL_LOCATOR.search(value, cursor) is not None


def redact_local_locators(text: str, replacement: str = "[REDACTED_PATH]") -> str:
    chunks: list[str] = []
    cursor = 0
    for start, end, protected in _http_url_candidates(text):
        chunks.append(LOCAL_LOCATOR.sub(replacement, text[cursor:start]))
        chunks.append(text[start:end] if protected else replacement)
        cursor = end
    chunks.append(LOCAL_LOCATOR.sub(replacement, text[cursor:]))
    return "".join(chunks)


def _http_url_candidates(text: str) -> list[tuple[int, int, bool]]:
    candidates: list[tuple[int, int, bool]] = []
    for match in HTTP_URL.finditer(text):
        candidates.append((*match.span(), _is_strict_http_url(match.group(0))))
    return candidates


def _is_strict_http_url(candidate: str) -> bool:
    if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
        return False
    for index, char in enumerate(candidate):
        if char == "%" and (
            index + 2 >= len(candidate)
            or HEX_ESCAPE.fullmatch(candidate[index + 1:index + 3]) is None
        ):
            return False
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if not (
        _is_strict_uri_component(parsed.path, RFC3986_PATH_CHAR)
        and _is_strict_uri_component(parsed.query, RFC3986_QUERY_FRAGMENT_CHAR)
        and _is_strict_uri_component(parsed.fragment, RFC3986_QUERY_FRAGMENT_CHAR)
    ):
        return False
    authority = candidate.split("://", 1)[1].split("/", 1)[0]
    authority = authority.split("?", 1)[0].split("#", 1)[0]
    if (
        authority != parsed.netloc
        or any(char in authority for char in "@,=\\%")
    ):
        return False
    host: str
    port_text = ""
    if authority.startswith("["):
        close = authority.find("]")
        if close < 0:
            return False
        host = authority[1:close]
        remainder = authority[close + 1:]
        if remainder:
            if not remainder.startswith(":"):
                return False
            port_text = remainder[1:]
        try:
            ipaddress.IPv6Address(host)
        except ipaddress.AddressValueError:
            return False
    else:
        if "[" in authority or "]" in authority or authority.count(":") > 1:
            return False
        host, separator, port_text = authority.rpartition(":")
        if not separator:
            host = authority
            port_text = ""
        if not _is_strict_dns_or_ipv4(host):
            return False
    if port_text and (
        not port_text.isascii()
        or not port_text.isdigit()
        or not 1 <= int(port_text) <= 65535
    ):
        return False
    if authority.endswith(":"):
        return False
    try:
        return parsed.hostname is not None and parsed.port == (
            int(port_text) if port_text else None
        )
    except ValueError:
        return False


def _is_strict_dns_or_ipv4(host: str) -> bool:
    if not host or len(host) > 253 or not host.isascii():
        return False
    try:
        return ipaddress.ip_address(host).version == 4
    except ValueError:
        if all(char in "0123456789." for char in host):
            return False
    dns_host = host[:-1] if host.endswith(".") else host
    return bool(dns_host) and all(
        DNS_LABEL.fullmatch(label) is not None for label in dns_host.split(".")
    )


def _is_strict_uri_component(value: str, allowed_ascii: frozenset[str]) -> bool:
    return all(ord(char) >= 128 or char in allowed_ascii for char in value)


def _safe_score(value: object) -> bool:
    if type(value) is int:
        return 0 <= value <= 1
    if type(value) is float:
        return math.isfinite(value) and 0.0 <= value <= 1.0
    return False


def _safe_identity(value: object) -> bool:
    text = str(value or "")
    return bool(PORTABLE_PROVIDER_ID.fullmatch(text) or OPAQUE_PROVIDER_ID.fullmatch(text))


def _safe_source(value: object) -> bool:
    if not _safe_plain_text(value, maximum=512):
        return False
    text = str(value)
    if ".." in text.replace("\\", "/").split("/"):
        return False
    locator = r"(?:\$PROJECT(?:/[^\s]+)?|\$HOME(?:/[^\s]+)?|\$EXTERNAL/sha256:[0-9a-f]{64})"
    return bool(
        re.fullmatch(rf"(?:registry|skill-manifest|skill-root):{locator}", text)
        or re.fullmatch(r"project:\.mission/[A-Za-z0-9._+/-]+", text)
        or re.fullmatch(r"user:~/.config/mission/[A-Za-z0-9._+/-]+", text)
        or re.fullmatch(r"preset:[A-Za-z0-9._+-]+", text)
        or re.fullmatch(
            r"(?:automatic|confirmed-user|user-instruction|manual|task-required):log-invocation",
            text,
        )
        or text == "registry"
    )


def _safe_digest(value: object, *, optional: bool = False) -> bool:
    return (optional and value is None) or (
        isinstance(value, str) and DIGEST.fullmatch(value) is not None
    )


def _safe_string_list(value: object, *, identities: bool = False) -> bool:
    if not isinstance(value, list) or len(value) > 128:
        return False
    checker = _safe_identity if identities else lambda item: _safe_text(item, maximum=128)
    return all(checker(item) for item in value)


def _safe_relative_path(value: object) -> bool:
    if not _safe_text(value, maximum=1024):
        return False
    text = str(value)
    return (
        bool(text)
        and not text.startswith(("/", "~", "@"))
        and not re.match(r"^[A-Za-z]:[\\/]", text)
        and ".." not in text.replace("\\", "/").split("/")
    )


def _validate_activation(value: object, base: str) -> None:
    if not isinstance(value, dict):
        _reject(base)
    _reject_unknown(value, ACTIVATION_FIELDS, base)
    minimum = value.get("min_complexity")
    if minimum is not None and minimum not in VALID_COMPLEXITIES:
        _reject(f"{base}/min_complexity")
    triggers = value.get("auto_select_if")
    if triggers is not None and (
        not isinstance(triggers, list)
        or any(item not in {"profile", "complexity"} for item in triggers)
    ):
        _reject(f"{base}/auto_select_if")
    if "explicit_below_min" in value and value["explicit_below_min"] != "deny":
        _reject(f"{base}/explicit_below_min")
    if "when_any" in value and not _safe_string_list(value["when_any"]):
        _reject(f"{base}/when_any")


def _validate_candidate(record: object, base: str) -> None:
    if not isinstance(record, dict):
        _reject(base)
    _reject_unknown(record, CANDIDATE_FIELDS, base)
    if "provider_id" in record and not _safe_identity(record["provider_id"]):
        _reject(f"{base}/provider_id")
    if "id" in record and not _safe_identity(record["id"]):
        _reject(f"{base}/id")
    for field in ("role", "skill"):
        if field in record and not _safe_text(record[field], maximum=128):
            _reject(f"{base}/{field}")
    if "reason" in record and not _safe_text(record["reason"], maximum=1024):
        _reject(f"{base}/reason")
    kind = record.get("kind")
    if kind is not None and kind not in {"skill", "command"}:
        _reject(f"{base}/kind")
    if "command" in record and not (
        kind == "command" and PORTABLE_PROVIDER_ID.fullmatch(str(record["command"]))
    ):
        _reject(f"{base}/command")
    if "args" in record and record["args"] != []:
        _reject(f"{base}/args")
    if "env" in record and (kind != "command" or record["env"] != {}):
        _reject(f"{base}/env")
    if "timeout" in record and (
        type(record["timeout"]) is not int or not 1 <= record["timeout"] <= 86400
    ):
        _reject(f"{base}/timeout")
    for field in ("task_profiles", "matched_conditions"):
        if field in record and not _safe_string_list(record[field]):
            _reject(f"{base}/{field}")
    if "phases" in record and (
        not isinstance(record["phases"], list)
        or any(item not in VALID_PHASES for item in record["phases"])
    ):
        _reject(f"{base}/phases")
    for field in (
        "required", "bounded_use", "bounded_purpose_required", "installed", "available",
        "first_use", "risk_confirmation_required",
    ):
        if field in record and type(record[field]) is not bool:
            _reject(f"{base}/{field}")
    if "source" in record and not _safe_source(record["source"]):
        _reject(f"{base}/source")
    if "registry_version" in record and record["registry_version"] not in {1, 2}:
        _reject(f"{base}/registry_version")
    for field in (
        "registry_entry_digest", "registry_projection_digest", "context_digest",
        "activation_digest", "result_contract_digest",
    ):
        if field in record and not _safe_digest(record[field]):
            _reject(f"{base}/{field}")
    if "score" in record and not _safe_score(record["score"]):
        _reject(f"{base}/score")
    for field in ("status", "eligibility_reason"):
        if field in record and not (
            isinstance(record[field], str) and TOKEN.fullmatch(record[field])
        ):
            _reject(f"{base}/{field}")
    for field in (
        "selection_source", "selection_source_raw", "eligibility_selection_source",
    ):
        if field in record and record[field] not in VALID_SELECTION_SOURCES | {"auto", "user-specified"}:
            _reject(f"{base}/{field}")
    if "normalized_activation" in record:
        _validate_activation(record["normalized_activation"], f"{base}/normalized_activation")


def _validate_registry_input(record: object, base: str, *, barrier: bool = False) -> None:
    if not isinstance(record, dict):
        _reject(base)
    _reject_unknown(record, BARRIER_FIELDS if barrier else ORDERED_INPUT_FIELDS, base)
    if record.get("kind") not in {"explicit", "project", "user", "installed", "skill-root"}:
        _reject(f"{base}/kind")
    if not _safe_source(record.get("source")):
        _reject(f"{base}/source")
    identity = record.get("canonical_identity")
    if not (
        isinstance(identity, str)
        and len(identity) <= 512
        and identity.startswith(("$PROJECT", "$HOME", "$EXTERNAL/sha256:"))
        and PRIVATE_PATH.search(identity) is None
    ):
        _reject(f"{base}/canonical_identity")
    for field in ("precedence_tier", "order"):
        if type(record.get(field)) is not int or record[field] < 0:
            _reject(f"{base}/{field}")
    if not barrier:
        if record.get("version") not in {0, 1, 2}:
            _reject(f"{base}/version")
        if record.get("status") not in {"missing", "present", "invalid"}:
            _reject(f"{base}/status")
        if "resolution_mode" in record and record["resolution_mode"] != "explicit-resupply-required":
            _reject(f"{base}/resolution_mode")
    if not _safe_digest(record.get("content_digest"), optional=True):
        _reject(f"{base}/content_digest")


def _validate_diagnostic(record: object, base: str) -> None:
    if not isinstance(record, dict):
        _reject(base)
    _reject_unknown(record, DIAGNOSTIC_FIELDS, base)
    identity = record.get("provider_id")
    if identity != "<registry>" and not _safe_identity(identity):
        _reject(f"{base}/provider_id")
    if "source" in record and not _safe_source(record["source"]):
        _reject(f"{base}/source")
    for field in ("reason_code", "field_code", "blocked_config_class"):
        if field in record and not (
            isinstance(record[field], str) and TOKEN.fullmatch(record[field])
        ):
            _reject(f"{base}/{field}")
    for field in (
        "registry_entry_digest", "registry_projection_digest", "context_digest", "activation_digest",
    ):
        if field in record and not _safe_digest(record[field]):
            _reject(f"{base}/{field}")
    for field in ("selection_source", "selection_source_raw"):
        if field in record and record[field] not in VALID_SELECTION_SOURCES | {"auto", "user-specified"}:
            _reject(f"{base}/{field}")
    if "requested_phase" in record and record["requested_phase"] not in VALID_PHASES:
        _reject(f"{base}/requested_phase")
    for field in ("current_complexity", "minimum_complexity"):
        if field in record and record[field] not in VALID_COMPLEXITIES:
            _reject(f"{base}/{field}")
    if "registry_input" in record:
        _validate_registry_input(record["registry_input"], f"{base}/registry_input", barrier=True)


def _validate_projection(value: object, base: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        _reject(base)
    _reject_unknown(value, PROJECTION_FIELDS, base)
    if value.get("schema") != "mission-specialist-registry-projection/1":
        _reject(f"{base}/schema")
    for field, barrier in (("ordered_inputs", False), ("precedence_barriers", True)):
        records = value.get(field)
        if not isinstance(records, list) or len(records) > 1024:
            _reject(f"{base}/{field}")
        for index, record in enumerate(records):
            _validate_registry_input(record, f"{base}/{field}/{index}", barrier=barrier)
    effective = value.get("effective_entries")
    if not isinstance(effective, list) or len(effective) > 1024:
        _reject(f"{base}/effective_entries")
    for index, record in enumerate(effective):
        item_base = f"{base}/effective_entries/{index}"
        if not isinstance(record, dict):
            _reject(item_base)
        _reject_unknown(record, EFFECTIVE_FIELDS, item_base)
        identity = record.get("provider_id")
        if identity != "<registry>" and not _safe_identity(identity):
            _reject(f"{item_base}/provider_id")
        if not _safe_source(record.get("source")):
            _reject(f"{item_base}/source")
        if "registry_version" in record and record["registry_version"] not in {1, 2}:
            _reject(f"{item_base}/registry_version")
        for field in ("registry_entry_digest", "content_digest"):
            if field in record and not _safe_digest(record[field]):
                _reject(f"{item_base}/{field}")
        if record.get("projection_state") not in VALID_PROJECTION_STATES:
            _reject(f"{item_base}/projection_state")
    if not _safe_digest(value.get("effective_projection_digest")):
        _reject(f"{base}/effective_projection_digest")


def _validate_decision(value: object, base: str) -> None:
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        _reject(base)
    _reject_unknown(value, DECISION_FIELDS, base)
    if "policy" in value and not (
        isinstance(value["policy"], str) and TOKEN.fullmatch(value["policy"])
    ):
        _reject(f"{base}/policy")
    if "action" in value and value["action"] not in {
        "continue-core", "select", "ask-user", "recommend-install",
    }:
        _reject(f"{base}/action")
    if "prompted_user" in value and type(value["prompted_user"]) is not bool:
        _reject(f"{base}/prompted_user")
    for field in ("reason", "reason_code"):
        if field in value and not _safe_text(value[field], maximum=1024):
            _reject(f"{base}/{field}")
    if "user_specified" in value and not _safe_string_list(value["user_specified"], identities=True):
        _reject(f"{base}/user_specified")
    if "decision" in value and value["decision"] not in {"none", "selected", "declined", "unavailable"}:
        _reject(f"{base}/decision")
    if "lifecycle_state" in value and value["lifecycle_state"] not in {"candidate", "selected", "terminal"}:
        _reject(f"{base}/lifecycle_state")
    if "selection_id" in value and not re.fullmatch(r"sel_[0-9a-f]{32}", str(value["selection_id"])):
        _reject(f"{base}/selection_id")
    if "confirmation_resolved" in value and type(value["confirmation_resolved"]) is not bool:
        _reject(f"{base}/confirmation_resolved")


def _validate_phase_plan(value: object, base: str) -> None:
    if not isinstance(value, list) or len(value) > len(VALID_PHASES):
        _reject(base)
    for index, record in enumerate(value):
        item_base = f"{base}/{index}"
        if not isinstance(record, dict):
            _reject(item_base)
        _reject_unknown(record, PHASE_PLAN_FIELDS, item_base)
        if record.get("phase") not in VALID_PHASES:
            _reject(f"{item_base}/phase")
        for field in ("roles", "providers"):
            if not _safe_string_list(record.get(field)):
                _reject(f"{item_base}/{field}")
        if type(record.get("max_providers")) is not int or not 1 <= record["max_providers"] <= 16:
            _reject(f"{item_base}/max_providers")


def _validate_invocation(record: object, base: str) -> None:
    if not isinstance(record, dict):
        _reject(base)
    _reject_unknown(record, INVOCATION_FIELDS, base)
    if "iteration" in record and (
        type(record["iteration"]) is not int or not 0 <= record["iteration"] <= 1_000_000
    ):
        _reject(f"{base}/iteration")
    if "phase" in record and record["phase"] not in VALID_PHASES:
        _reject(f"{base}/phase")
    for field in ("role", "skill", "reason", "notes", "bounded_purpose"):
        if field in record and not _safe_text(record[field], maximum=2048):
            _reject(f"{base}/{field}")
    if "mode" in record and record["mode"] not in VALID_INVOCATION_MODES:
        _reject(f"{base}/mode")
    if "status" in record and record["status"] not in VALID_INVOCATION_STATUSES:
        _reject(f"{base}/status")
    if "reason_code" in record and not (
        isinstance(record["reason_code"], str) and TOKEN.fullmatch(record["reason_code"])
    ):
        _reject(f"{base}/reason_code")
    for field in ("timestamp", "started_at", "completed_at", "transitioned_at"):
        if field in record and not _safe_text(record[field], maximum=64):
            _reject(f"{base}/{field}")
    if "provider_kind" in record and record["provider_kind"] not in {"skill", "command"}:
        _reject(f"{base}/provider_kind")
    if "kind" in record and record["kind"] not in {"skill", "command"}:
        _reject(f"{base}/kind")
    if "exit_code" in record and record["exit_code"] is not None and type(record["exit_code"]) is not int:
        _reject(f"{base}/exit_code")
    if "timeout" in record and (
        type(record["timeout"]) is not int or not 1 <= record["timeout"] <= 86400
    ):
        _reject(f"{base}/timeout")
    if "selection_source" in record and record["selection_source"] not in VALID_SELECTION_SOURCES:
        _reject(f"{base}/selection_source")
    if "evidence_path" in record and not _safe_relative_path(record["evidence_path"]):
        _reject(f"{base}/evidence_path")
    if "command" in record and not PORTABLE_PROVIDER_ID.fullmatch(str(record["command"])):
        _reject(f"{base}/command")
    if "selection_id" in record and not re.fullmatch(r"sel_[0-9a-f]{32}", str(record["selection_id"])):
        _reject(f"{base}/selection_id")
    if "invocation_id" in record and not re.fullmatch(r"inv_[0-9a-f]{32}", str(record["invocation_id"])):
        _reject(f"{base}/invocation_id")
    for field in ("host_run_id", "root_run_id", "parent_run_id", "child_run_id", "logical_group_id"):
        if field in record and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", str(record[field])):
            _reject(f"{base}/{field}")
    if "lifecycle_state" in record and record["lifecycle_state"] not in {"selected", "invoked", "terminal"}:
        _reject(f"{base}/lifecycle_state")


def validate_specialist_public_state(data: dict[str, Any]) -> None:
    """Validate every specialist public surface recursively without echoing values."""
    if not isinstance(data, dict):
        return
    for surface in ("specialists_candidates", "specialists_selected", "specialists_unavailable"):
        if surface not in data:
            continue
        records = data[surface]
        if not isinstance(records, list) or len(records) > 1024:
            _reject(f"/{surface}")
        for index, record in enumerate(records):
            _validate_candidate(record, f"/{surface}/{index}")
    if "specialists_ineligible" in data:
        records = data["specialists_ineligible"]
        if not isinstance(records, list) or len(records) > 1024:
            _reject("/specialists_ineligible")
        for index, record in enumerate(records):
            _validate_diagnostic(record, f"/specialists_ineligible/{index}")
    if "installed_skills" in data:
        installed = data["installed_skills"]
        if not _safe_string_list(installed, identities=True):
            _reject("/installed_skills")
    if "specialist_registry_projection" in data:
        _validate_projection(data["specialist_registry_projection"], "/specialist_registry_projection")
    if "specialists_decision" in data:
        _validate_decision(data["specialists_decision"], "/specialists_decision")
    if "specialists_phase_plan" in data:
        _validate_phase_plan(data["specialists_phase_plan"], "/specialists_phase_plan")
    if "specialist_invocations" in data:
        records = data["specialist_invocations"]
        if not isinstance(records, list) or len(records) > 10000:
            _reject("/specialist_invocations")
        for index, record in enumerate(records):
            _validate_invocation(record, f"/specialist_invocations/{index}")
