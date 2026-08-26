"""Application use case for specialist-registry discovery.

The adapter owns filesystem snapshots and manifest enumeration.  This module
owns the deterministic interpretation of those snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


@dataclass(frozen=True, init=False)
class SpecialistRegistryDiscoveryRequest:
    registry: tuple
    no_default_skill_roots: bool
    skills_dir: object

    def __init__(self, *, raw_registry, no_default_skill_roots, skills_dir):
        values = raw_registry if isinstance(raw_registry, list) else [raw_registry]
        object.__setattr__(self, "registry", tuple(value for value in values if value))
        object.__setattr__(self, "no_default_skill_roots", no_default_skill_roots)
        object.__setattr__(self, "skills_dir", skills_dir)


@dataclass(frozen=True)
class SpecialistRegistryDiscoveryServices:
    path_from_string: object
    path_expanduser: object
    current_directory: object
    home_directory: object
    join_path: object
    path_is_directory: object
    path_glob: object
    environment_get: object
    path_separator: str
    registry_source: object
    read_registry_input: object
    detect_registry_version: object
    contract_error: object
    registry_contract_error: object
    parse_v1_registry: object
    parse_v2_registry: object
    registry_entry_digest: object
    portable_registry_identity: object
    value_digest: object


def provider_id(candidate: dict) -> str:
    return str(
        candidate.get("provider_id")
        or candidate.get("skill")
        or candidate.get("role")
        or candidate.get("name")
        or candidate.get("command")
        or ""
    )


def portable_provider_identifier(value: object) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", str(value or "")))


def safe_provider_reference(identity: object) -> str:
    canonical_identity = str(identity or "")
    if re.fullmatch(r"provider:sha256:[0-9a-f]{64}", canonical_identity):
        return canonical_identity
    if portable_provider_identifier(canonical_identity):
        return canonical_identity
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    return "provider:sha256:" + digest


def _split_csv(value) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _registry_paths(request, services) -> list:
    paths = []
    for raw_value in request.registry:
        paths.extend(
            services.path_expanduser(services.path_from_string(value))
            for value in _split_csv(raw_value)
        )
    return paths


def _skill_roots(request, services) -> list:
    roots = [
        services.path_expanduser(services.path_from_string(value))
        for value in _split_csv(request.skills_dir)
    ]
    environment_value = services.environment_get("MISSION_SKILL_ROOTS")
    if environment_value:
        roots.extend(
            services.path_expanduser(services.path_from_string(value))
            for value in environment_value.split(services.path_separator)
            if value
        )
    if not request.no_default_skill_roots:
        home = services.home_directory()
        roots.extend(
            [
                services.join_path(home, ".codex", "skills"),
                services.join_path(home, ".claude", "skills"),
            ]
        )
    result = []
    seen = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def _load_v1_registry_candidates(raw: bytes, source: str, services) -> list[dict]:
    try:
        items = services.parse_v1_registry(raw.decode("utf-8"))
    except UnicodeError:
        items = []
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate["source"] = source
        candidate["registry_version"] = 1
        if "activation" in candidate or "disabled" in candidate:
            candidate["_registry_error"] = "mixed-registry-version"
        candidate["registry_entry_digest"] = services.registry_entry_digest(candidate)
        candidates.append(candidate)
    return candidates


def _load_v2_registry_candidates(raw: bytes, source: str, services):
    try:
        items = services.parse_v2_registry(raw.decode("utf-8"))
    except (UnicodeError, services.registry_contract_error) as error:
        return [], [{
            "provider_id": "<registry>",
            "source": source,
            "reason_code": getattr(error, "code", "invalid-registry-contract"),
            "detail": str(error),
        }]
    candidates = []
    for item in items:
        candidate = dict(item)
        candidate["source"] = source
        candidate["registry_version"] = 2
        candidate["_v2_auto_use_present"] = "auto_use" in item
        candidate["registry_entry_digest"] = services.registry_entry_digest(candidate)
        candidates.append(candidate)
    return candidates, []


def _resolve_registry_precedence(candidates: list[dict], diagnostics: list[dict]):
    ordered = sorted(
        candidates,
        key=lambda item: (
            int(item.get("_precedence_tier", 99)),
            int(item.get("_input_order", 0)),
            str(item.get("_input_identity") or ""),
            int(item.get("_candidate_order", 0)),
        ),
    )
    conflict_groups: dict = {}
    for item in ordered:
        identity = provider_id(item)
        tier = int(item.get("_precedence_tier", 99))
        explicit_subrank = int(item.get("_input_order", 0)) if tier in {0, 1} else 0
        conflict_groups.setdefault((tier, explicit_subrank, identity), []).append(item)
    conflicted = {key for key, items in conflict_groups.items() if key[2] and len(items) > 1}
    resolved: list[dict] = []
    decided: set = set()
    for item in ordered:
        identity = provider_id(item)
        if not identity or identity in decided:
            continue
        tier = int(item.get("_precedence_tier", 99))
        explicit_subrank = int(item.get("_input_order", 0)) if tier in {0, 1} else 0
        group_key = (tier, explicit_subrank, identity)
        decided.add(identity)
        if group_key in conflicted:
            diagnostics.append({
                "provider_id": safe_provider_reference(identity),
                "source": item.get("source"),
                "reason_code": "same-tier-identity-conflict",
                "registry_entry_digest": item.get("registry_entry_digest"),
            })
            resolved.append({**item, "enabled": False, "_projection_state": "conflict"})
        elif item.get("disabled") is True or item.get("enabled") is False:
            projection_state = "tombstone" if item.get("registry_version") == 2 and item.get("disabled") is True else "disabled"
            diagnostics.append({
                "provider_id": safe_provider_reference(identity),
                "source": item.get("source"),
                "reason_code": "provider-disabled",
                "registry_entry_digest": item.get("registry_entry_digest"),
            })
            resolved.append({**item, "enabled": False, "_projection_state": projection_state})
        else:
            resolved.append(item)
    return resolved, diagnostics


def discover_specialist_registry_candidates(request, services):
    """Classify captured registry snapshots in deterministic precedence order."""
    candidates: list[dict] = []
    diagnostics: list[dict] = []
    inputs: list[dict] = []
    invalid_barriers: list[dict] = []

    def register(path, source, kind, version, tier, order, detection_error=None, snapshot=None):
        record, raw, _physical_identity, snapshot_error = snapshot or services.read_registry_input(
            path, source, kind, version, tier, order
        )
        record.update({"source": source, "kind": kind, "version": version, "precedence_tier": tier, "order": order})
        inputs.append(record)
        if record["status"] == "missing":
            return record
        if snapshot_error is not None:
            loaded, invalid = [], [{"provider_id": "<registry>", "source": source, "reason_code": snapshot_error.code, "detail": str(snapshot_error)}]
        elif detection_error is not None:
            loaded, invalid = [], [{"provider_id": "<registry>", "source": source, "reason_code": detection_error.code, "detail": str(detection_error)}]
        elif raw is not None and version == 2:
            loaded, invalid = _load_v2_registry_candidates(raw, source, services)
        elif raw is not None:
            try:
                loaded, invalid = _load_v1_registry_candidates(raw, source, services), []
            except services.registry_contract_error as error:
                loaded, invalid = [], [{"provider_id": "<registry>", "source": source, "reason_code": error.code}]
        else:
            loaded, invalid = [], []
        if invalid:
            record["status"] = "invalid"
            barrier = {"source": source, "canonical_identity": record["canonical_identity"], "content_digest": record["content_digest"], "kind": kind, "precedence_tier": tier, "order": order}
            invalid_barriers.append(barrier)
            diagnostics.extend({**item, "registry_input": barrier} for item in invalid)
        for candidate_order, candidate in enumerate(loaded):
            candidate["_precedence_tier"] = tier
            candidate["_input_order"] = order
            candidate["_input_identity"] = record["canonical_identity"]
            candidate["_candidate_order"] = candidate_order
            candidates.append(candidate)
        return record

    explicit: list[tuple] = []
    for order, path in enumerate(_registry_paths(request, services)):
        source = services.registry_source(path, "explicit")
        snapshot = services.read_registry_input(path, source, "explicit", 0, 99, order)
        raw_bytes = snapshot[1]
        if snapshot[3] is not None:
            version, detection_error = 2, snapshot[3]
        else:
            try:
                raw = raw_bytes.decode("utf-8") if raw_bytes is not None else ""
                version, detection_error = services.detect_registry_version(raw), None
            except UnicodeError as error:
                version, detection_error = 2, services.contract_error("invalid-registry-contract", str(error))
            except services.registry_contract_error as error:
                version, detection_error = 2, error
        explicit.append((path, version, order, detection_error, snapshot, snapshot[2]))
    duplicate_explicit = {identity for identity in {item[5] for item in explicit if item[5] is not None} if sum(item[5] == identity for item in explicit) > 1}
    for path, version, order, detection_error, snapshot, physical_identity in sorted(explicit, key=lambda item: (-item[1], item[2])):
        if physical_identity in duplicate_explicit:
            detection_error = services.contract_error("duplicate-registry-input")
        register(path, snapshot[0]["source"], "explicit", version, 0 if version == 2 else 1, order, detection_error, snapshot)

    project_root = services.join_path(services.current_directory(), ".mission")
    register(services.join_path(project_root, "specialists-v2.yml"), "project:.mission/specialists-v2.yml", "project", 2, 2, 0)
    register(services.join_path(project_root, "specialists.yml"), "project:.mission/specialists.yml", "project", 1, 3, 0)
    if not request.no_default_skill_roots:
        user_root = services.join_path(services.home_directory(), ".config", "mission")
        register(services.join_path(user_root, "specialists-v2.yml"), "user:~/.config/mission/specialists-v2.yml", "user", 2, 4, 0)
        register(services.join_path(user_root, "specialists.yml"), "user:~/.config/mission/specialists.yml", "user", 1, 5, 0)
    for root_order, root in enumerate(_skill_roots(request, services)):
        if not services.path_is_directory(root):
            continue
        v2_manifests = sorted(services.path_glob(root, "*/mission-specialist-v2.yml"))
        v1_manifests = sorted(services.path_glob(root, "*/mission-specialist.yml"))
        inventory: list[dict] = []
        for version, tier, manifests in ((2, 6, v2_manifests), (1, 7, v1_manifests)):
            for manifest_order, manifest in enumerate(manifests):
                record = register(manifest, services.registry_source(manifest, "installed"), "installed", version, tier, manifest_order)
                inventory.append({"identity": record["canonical_identity"], "digest": record["content_digest"]})
        inputs.append({"kind": "skill-root", "source": services.registry_source(root, "skill-root"), "canonical_identity": services.portable_registry_identity(root), "version": 0, "precedence_tier": 6, "order": root_order, "status": "present", "content_digest": services.value_digest(inventory)})

    active_barrier = None
    if invalid_barriers:
        active_barrier = min(invalid_barriers, key=lambda item: (int(item["precedence_tier"]), int(item["order"]) if item["kind"] == "explicit" else -1))
        candidates = [candidate for candidate in candidates if int(candidate.get("_precedence_tier", 99)) < int(active_barrier["precedence_tier"]) or (active_barrier["kind"] == "explicit" and int(candidate.get("_precedence_tier", 99)) == int(active_barrier["precedence_tier"]) and int(candidate.get("_input_order", 0)) < int(active_barrier["order"]))]
    resolved, diagnostics = _resolve_registry_precedence(candidates, diagnostics)
    if active_barrier is not None:
        resolved.append({"_blocks_builtin_candidates": True, "_projection_state": "invalid-input-barrier", "source": active_barrier["source"], "_precedence_tier": active_barrier["precedence_tier"], "_input_order": active_barrier["order"]})
    effective = [{"provider_id": safe_provider_reference(provider_id(candidate)), "source": candidate.get("source"), "registry_version": candidate.get("registry_version"), "registry_entry_digest": candidate.get("registry_entry_digest"), "projection_state": candidate.get("_projection_state", "eligible")} for candidate in resolved if provider_id(candidate)]
    if active_barrier is not None:
        effective.append({"provider_id": "<registry>", "source": active_barrier["source"], "projection_state": "invalid-input-barrier", "content_digest": active_barrier["content_digest"]})
    ordered_inputs = sorted(inputs, key=lambda item: (int(item.get("precedence_tier", 99)), int(item.get("order", 0)), str(item.get("canonical_identity") or "")))
    ordered_barriers = sorted(invalid_barriers, key=lambda item: (int(item.get("precedence_tier", 99)), int(item.get("order", 0)), str(item.get("canonical_identity") or "")))
    projection_payload = {"schema": "mission-specialist-registry-projection/1", "ordered_inputs": ordered_inputs, "precedence_barriers": ordered_barriers, "effective_entries": effective}
    projection = {**projection_payload, "effective_projection_digest": services.value_digest(projection_payload)}
    for candidate in resolved:
        candidate["registry_projection_digest"] = projection["effective_projection_digest"]
    return resolved, diagnostics, projection
