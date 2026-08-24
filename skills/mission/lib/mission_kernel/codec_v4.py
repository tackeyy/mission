"""Missing/v1-v4 normalization and legacy compatibility projection."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from mission_common import derive_terminal_outcome

from .errors import MissionStateDecodeError
from .json_codec import (
    decode_json_object,
    encode_json_object,
    freeze_json_value,
    thaw_json_object,
)
from .model import (
    AbsentHandoff,
    AbsentPlan,
    BoundScore,
    ConsumedHandoff,
    ConsumingHandoff,
    ContentAddressedRef,
    CorePlan,
    FencedLease,
    FindingSeverity,
    FrozenJsonObject,
    FrozenJsonValue,
    GitRevisionScope,
    HaltCategory,
    HandoffStatus,
    LegacyAbsentLease,
    LegacyFindingsUnloaded,
    LegacyScore,
    LeaseHistoryEntry,
    ManualScoreRef,
    MaterializedFindings,
    MissionControl,
    MissionIdentity,
    MissionState,
    NotApplicableRevisionScope,
    OpenFinding,
    Phase,
    PlanRecord,
    PlanSource,
    PreparedHandoff,
    ProviderPlan,
    RejectedHandoff,
    ReviewAggregateRef,
    ReviewInputRef,
    ReviewRef,
    RevisionScope,
    RevisionScopeKind,
    SchemaOrigin,
    Score,
    ScoreSource,
    SessionRole,
    TerminalOutcome,
)
from .versions import read_schema_version

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PHASES = {item.value: item for item in Phase}


def _fail(code: str, path: str, detail: str) -> MissionStateDecodeError:
    return MissionStateDecodeError(code, path, detail)


def _object(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("invalid-type", path, "expected an object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _fail("invalid-type", path, "expected an array")
    return value


def _string(
    value: Any,
    path: str,
    *,
    nonempty: bool = False,
    trimmed: bool = False,
    maximum: int | None = None,
) -> str:
    if not isinstance(value, str):
        raise _fail("invalid-type", path, "expected a string")
    if nonempty and not value:
        raise _fail("invalid-value", path, "string must be non-empty")
    if trimmed and value != value.strip():
        raise _fail("invalid-value", path, "string must be trimmed")
    if maximum is not None and len(value) > maximum:
        raise _fail("invalid-value", path, f"string exceeds {maximum} characters")
    return value


def _optional_legacy_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _integer(
    value: Any,
    path: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> int:
    if type(value) is not int:
        raise _fail("invalid-type", path, "expected an integer")
    if positive and value < 1:
        raise _fail("invalid-value", path, "integer must be positive")
    if non_negative and value < 0:
        raise _fail("invalid-value", path, "integer must be non-negative")
    return value


def _optional_integer(
    value: Any,
    path: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> int | None:
    if value is None:
        return None
    return _integer(value, path, positive=positive, non_negative=non_negative)


def _number(value: Any, path: str) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        raise _fail("invalid-type", path, "expected a number")
    result = float(value)
    if not math.isfinite(result):
        raise _fail("non-finite-number", path, "number must be finite")
    return result


def _optional_number(value: Any, path: str) -> float | None:
    if value is None:
        return None
    return _number(value, path)


def _boolean(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise _fail("invalid-type", path, "expected a boolean")
    return value


def _enum(enum_type: Any, value: Any, path: str):
    if not isinstance(value, str):
        raise _fail("invalid-type", path, "expected a string variant")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise _fail("unknown-variant", path, f"unknown variant {value!r}") from exc


def _digest(value: Any, path: str) -> str:
    result = _string(value, path)
    if _DIGEST.fullmatch(result) is None:
        raise _fail("invalid-value", path, "expected sha256:<64 lowercase hex>")
    return result


def _aware_time(value: Any, path: str) -> str:
    raw = _string(value, path, nonempty=True, trimmed=True)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail("invalid-value", path, "expected a timezone-aware timestamp") from exc
    if parsed.tzinfo is None:
        raise _fail("invalid-value", path, "expected a timezone-aware timestamp")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _freeze_object(document: Mapping[str, Any]) -> FrozenJsonObject:
    frozen = freeze_json_value(dict(document))
    assert isinstance(frozen, FrozenJsonObject)
    return frozen


def _decode_identity(document: Mapping[str, Any], *, v5: bool = False) -> MissionIdentity:
    if v5:
        identity = _object(document.get("identity"), "$.identity")
        return MissionIdentity(
            _string(identity.get("mission"), "$.identity.mission", nonempty=True, trimmed=True, maximum=64 * 1024),
            _string(identity.get("mission_id"), "$.identity.mission_id", nonempty=True, trimmed=True, maximum=128),
            _string(identity.get("session_id"), "$.identity.session_id", nonempty=True, trimmed=True, maximum=128),
        )
    return MissionIdentity(
        _optional_legacy_string(document.get("mission"), "$.mission"),
        _optional_legacy_string(document.get("mission_id"), "$.mission_id"),
        _optional_legacy_string(document.get("session_id"), "$.session_id"),
    )


def _legacy_terminal_projection(document: Mapping[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {
        "passes": document.get("passes", False),
        "loop_active": document.get("loop_active", False),
        "halt_reason": document.get("halt_reason", ""),
        "session_role": document.get("session_role", "implementer"),
    }
    for key in ("halt_category", "terminal_outcome", "resolution_status"):
        if key in document:
            projection[key] = document[key]
    return projection


def _project_terminal_outcome_control(control: Mapping[str, Any]) -> dict[str, Any]:
    projected = {
        "passes": control["passes"],
        "loop_active": control["loop_active"],
        "halt_reason": control["halt_reason"],
        "session_role": control["session_role"],
    }
    if control.get("halt_category") is not None:
        projected["halt_category"] = control["halt_category"]
    if control.get("terminal_outcome") is not None:
        projected["terminal_outcome"] = control["terminal_outcome"]
    return projected


def _legacy_terminal(document: Mapping[str, Any]) -> TerminalOutcome | None:
    outcome = derive_terminal_outcome(_legacy_terminal_projection(document))
    return None if outcome is None else TerminalOutcome(outcome)


def _decode_legacy_control(document: Mapping[str, Any]) -> MissionControl:
    terminal = _legacy_terminal(document)
    raw_phase = document.get("phase")
    if raw_phase in (None, ""):
        if terminal is None:
            phase = Phase.PLANNING
        elif terminal in {TerminalOutcome.COMPLETED_PASS, TerminalOutcome.COMPLETED_EVIDENCE}:
            phase = Phase.DONE
        else:
            phase = Phase.HALTED
    else:
        phase = _enum(Phase, raw_phase, "$.phase")
    halt_category = document.get("halt_category")
    return MissionControl(
        phase=phase,
        terminal_outcome=terminal,
        iteration=_integer(document.get("iteration", 1), "$.iteration", non_negative=True),
        max_iter=_optional_integer(document.get("max_iter"), "$.max_iter", positive=True),
        threshold=_optional_number(document.get("threshold"), "$.threshold"),
        reviewer_count=_integer(document.get("reviewer_count", 2), "$.reviewer_count", positive=True),
        stagnation_count=_integer(document.get("stagnation_count", 0), "$.stagnation_count", non_negative=True),
        loop_active=_boolean(document.get("loop_active", False), "$.loop_active"),
        passes=_boolean(document.get("passes", False), "$.passes"),
        halt_reason=_string(document.get("halt_reason", ""), "$.halt_reason"),
        halt_category=None if halt_category is None else _enum(HaltCategory, halt_category, "$.halt_category"),
        session_role=_enum(SessionRole, document.get("session_role", "implementer"), "$.session_role"),
    )


def _decode_plan_record(value: Mapping[str, Any], path: str, *, require_validated: bool) -> PlanRecord:
    required = {
        "schema",
        "path",
        "digest",
        "source",
        "source_id",
        "source_digest",
        "selection_source",
        "iteration",
        "generation",
    }
    if require_validated:
        required.add("validated_at")
    missing = required - set(value)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required plan field is missing")
    source = _enum(PlanSource, value["source"], f"{path}.source")
    fields = {
        "schema": _string(value["schema"], f"{path}.schema", nonempty=True),
        "path": _string(value["path"], f"{path}.path", nonempty=True),
        "digest": _digest(value["digest"], f"{path}.digest"),
        "source_id": _string(value["source_id"], f"{path}.source_id", nonempty=True),
        "source_digest": _digest(value["source_digest"], f"{path}.source_digest"),
        "selection_source": _string(value["selection_source"], f"{path}.selection_source", nonempty=True),
        "iteration": _integer(value["iteration"], f"{path}.iteration", non_negative=True),
        "generation": _integer(value["generation"], f"{path}.generation", positive=True),
        "validated_at": _aware_time(value.get("validated_at"), f"{path}.validated_at") if require_validated else "",
    }
    return CorePlan(**fields) if source is PlanSource.CORE else ProviderPlan(**fields)


def _decode_legacy_plan(document: Mapping[str, Any]):
    raw = document.get("canonical_plan")
    if raw in (None, {}):
        return AbsentPlan()
    return _decode_plan_record(_object(raw, "$.canonical_plan"), "$.canonical_plan", require_validated=True)


def _handoff_common(handoff: Mapping[str, Any], plan: PlanRecord) -> dict[str, Any]:
    path = "$.executor_handoff"
    required = {
        "schema",
        "handoff_id",
        "plan_path",
        "plan_digest",
        "plan_generation",
        "plan_source",
        "source_id",
        "selection_source",
        "iteration",
        "step_ids",
        "status",
    }
    missing = required - set(handoff)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required handoff field is missing")
    step_ids = _list(handoff["step_ids"], f"{path}.step_ids")
    ordered = tuple(_string(item, f"{path}.step_ids", nonempty=True) for item in step_ids)
    bindings = {
        "plan_path": plan.path,
        "plan_digest": plan.digest,
        "plan_generation": plan.generation,
        "plan_source": plan.source.value,
        "source_id": plan.source_id,
        "selection_source": plan.selection_source,
        "iteration": plan.iteration,
    }
    for field, expected in bindings.items():
        if handoff[field] != expected:
            raise _fail("invariant-violation", f"{path}.{field}", "handoff does not match canonical plan")
    return {
        "schema": _string(handoff["schema"], f"{path}.schema", nonempty=True),
        "handoff_id": _string(handoff["handoff_id"], f"{path}.handoff_id", nonempty=True),
        "plan": plan,
        "ordered_step_ids": ordered,
    }


def _decode_legacy_handoff(document: Mapping[str, Any], plan):
    raw = document.get("executor_handoff")
    if raw in (None, {}):
        return AbsentHandoff()
    if isinstance(plan, AbsentPlan):
        raise _fail("invariant-violation", "$.executor_handoff", "handoff requires canonical plan")
    handoff = _object(raw, "$.executor_handoff")
    status = _enum(HandoffStatus, handoff.get("status"), "$.executor_handoff.status")
    common = _handoff_common(handoff, plan)
    if status is HandoffStatus.PREPARED:
        return PreparedHandoff(**common)
    if status is HandoffStatus.CONSUMING:
        return ConsumingHandoff(
            **common,
            begun_at=_aware_time(handoff.get("begun_at"), "$.executor_handoff.begun_at"),
        )
    if status is HandoffStatus.CONSUMED:
        return ConsumedHandoff(
            **common,
            begun_at=_aware_time(handoff.get("begun_at"), "$.executor_handoff.begun_at"),
            consumed_at=_aware_time(handoff.get("consumed_at"), "$.executor_handoff.consumed_at"),
        )
    return RejectedHandoff(
        **common,
        rejected_reason=_string(
            handoff.get("rejected_reason"),
            "$.executor_handoff.rejected_reason",
            nonempty=True,
        ),
        begun_at=None
        if handoff.get("begun_at") is None
        else _aware_time(handoff["begun_at"], "$.executor_handoff.begun_at"),
    )


def _decode_revision_scope(value: Any, path: str, *, v5: bool = False) -> RevisionScope:
    scope = _object(value, path)
    kind = _enum(RevisionScopeKind, scope.get("kind"), f"{path}.kind")
    required = (
        {"kind", "reason_code"}
        if kind is RevisionScopeKind.NOT_APPLICABLE
        else {"kind", "base_sha", "head_sha"}
    )
    if v5:
        missing = required - set(scope)
        if missing:
            field = sorted(missing)[0]
            raise _fail("missing-key", f"{path}.{field}", "required revision scope field is missing")
        if set(scope) - required:
            raise _fail("unknown-key", path, "revision scope contains unknown fields")
    if kind is RevisionScopeKind.NOT_APPLICABLE:
        return NotApplicableRevisionScope(
            _string(scope.get("reason_code"), f"{path}.reason_code", nonempty=True)
        )
    return GitRevisionScope(
        base_sha=_string(scope.get("base_sha"), f"{path}.base_sha", nonempty=True),
        head_sha=_string(scope.get("head_sha"), f"{path}.head_sha", nonempty=True),
    )


def _decode_legacy_review_input(value: Any, index: int) -> ReviewInputRef:
    path = f"$.review_evidence_refs[{index}]"
    reference = _object(value, path)
    required = {"path", "digest", "size", "iteration", "perspective"}
    missing = required - set(reference)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required review input field is missing")
    if reference.get("kind", "review-input") != "review-input":
        raise _fail("unknown-variant", f"{path}.kind", "expected review-input")
    return ReviewInputRef(
        relative_path=_string(reference["path"], f"{path}.path", nonempty=True),
        digest=_digest(reference["digest"], f"{path}.digest"),
        size=_integer(reference["size"], f"{path}.size", positive=True),
        iteration=_integer(reference["iteration"], f"{path}.iteration", positive=True),
        perspective=_string(reference["perspective"], f"{path}.perspective", nonempty=True),
    )


def _decode_legacy_review_inputs(document: Mapping[str, Any]) -> tuple[ReviewInputRef, ...]:
    raw = document.get("review_evidence_refs", [])
    return tuple(
        _decode_legacy_review_input(item, index)
        for index, item in enumerate(_list(raw, "$.review_evidence_refs"))
    )


def _decode_aggregate_ref(value: Any, path: str, *, v5: bool = False) -> ReviewAggregateRef:
    reference = _object(value, path)
    relative_key = "relative_path" if v5 else "path"
    required = {"kind", relative_key, "digest", "generation", "revision_scope"}
    if v5:
        required.update({"size", "iteration"})
    missing = required - set(reference)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required review aggregate field is missing")
    if reference["kind"] != "review-aggregate":
        raise _fail("unknown-variant", f"{path}.kind", "expected review-aggregate")
    lineage_fields = {"review_group_id", "review_generation", "base_sha", "head_sha"}
    if v5 and set(reference) - required - lineage_fields:
        raise _fail("unknown-key", path, "review aggregate contains unknown fields")
    lineage_presence = [field in reference for field in lineage_fields]
    if any(lineage_presence) and not all(lineage_presence):
        raise _fail("partial-lineage", path, "review aggregate lineage must be all-or-none")
    lineage = (
        reference.get("review_group_id"),
        reference.get("review_generation"),
        reference.get("base_sha"),
        reference.get("head_sha"),
    )
    if all(lineage_presence) and not all(item is not None for item in lineage):
        raise _fail("partial-lineage", path, "review aggregate lineage must be all-or-none")
    return ReviewAggregateRef(
        relative_path=_string(reference[relative_key], f"{path}.{relative_key}", nonempty=True),
        digest=_digest(reference["digest"], f"{path}.digest"),
        generation=_string(reference["generation"], f"{path}.generation", nonempty=True),
        revision_scope=_decode_revision_scope(
            reference["revision_scope"], f"{path}.revision_scope", v5=v5
        ),
        size=None if not v5 else _integer(reference["size"], f"{path}.size", positive=True),
        iteration=None if not v5 else _integer(reference["iteration"], f"{path}.iteration", positive=True),
        review_group_id=None if lineage[0] is None else _string(lineage[0], f"{path}.review_group_id", nonempty=True),
        review_generation=None if lineage[1] is None else _integer(lineage[1], f"{path}.review_generation", positive=True),
        base_sha=None if lineage[2] is None else _string(lineage[2], f"{path}.base_sha", nonempty=True),
        head_sha=None if lineage[3] is None else _string(lineage[3], f"{path}.head_sha", nonempty=True),
    )


def _decode_content_ref(
    value: Any,
    path: str,
    *,
    require_size: bool,
    v5: bool = False,
    expected_kind: str | None = None,
) -> ContentAddressedRef:
    reference = _object(value, path)
    relative_key = "relative_path" if v5 or "relative_path" in reference else "path"
    required = {"kind", relative_key, "digest"}
    if require_size:
        required.add("size")
    missing = required - set(reference)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required evidence field is missing")
    if v5 and set(reference) != required:
        raise _fail("unknown-key", path, "evidence reference contains unknown fields")
    if expected_kind is not None and reference["kind"] != expected_kind:
        raise _fail("unknown-variant", f"{path}.kind", f"expected {expected_kind}")
    return ContentAddressedRef(
        kind=_string(reference["kind"], f"{path}.kind", nonempty=True),
        relative_path=_string(reference[relative_key], f"{path}.{relative_key}", nonempty=True),
        digest=_digest(reference["digest"], f"{path}.digest"),
        size=_integer(reference["size"], f"{path}.size", positive=True) if require_size else None,
    )


def _decode_manual_ref(value: Any, path: str, *, v5: bool = False) -> ManualScoreRef:
    reference = _object(value, path)
    relative_key = "relative_path" if v5 else "path"
    required = {"kind", relative_key, "digest", "generation", "revision_scope"}
    if v5:
        required.add("size")
    missing = required - set(reference)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required manual evidence field is missing")
    if v5 and set(reference) != required:
        raise _fail("unknown-key", path, "manual evidence reference contains unknown fields")
    if reference["kind"] != "manual-score":
        raise _fail("unknown-variant", f"{path}.kind", "expected manual-score")
    return ManualScoreRef(
        relative_path=_string(reference[relative_key], f"{path}.{relative_key}", nonempty=True),
        digest=_digest(reference["digest"], f"{path}.digest"),
        generation=_string(reference["generation"], f"{path}.generation", nonempty=True),
        revision_scope=_decode_revision_scope(
            reference["revision_scope"], f"{path}.revision_scope", v5=v5
        ),
        size=_integer(reference["size"], f"{path}.size", positive=True) if v5 else None,
    )


def _score_scalars(entry: Mapping[str, Any], path: str) -> None:
    for field in ("composite", "min_item"):
        if field in entry:
            _number(entry[field], f"{path}.{field}")
    if "review_agreement" in entry and entry["review_agreement"] is not None:
        _number(entry["review_agreement"], f"{path}.review_agreement")
    if "open_high" in entry:
        _integer(entry["open_high"], f"{path}.open_high", non_negative=True)
    items = entry.get("items")
    if items is not None:
        for key, value in _object(items, f"{path}.items").items():
            _number(value, f"{path}.items.{key}")


def _decode_legacy_scores(document: Mapping[str, Any]) -> tuple[tuple[Score, ...], tuple[ReviewAggregateRef, ...]]:
    raw = _list(document.get("score_history", []), "$.score_history")
    scores: list[Score] = []
    aggregate_refs: list[ReviewAggregateRef] = []
    for index, item in enumerate(raw):
        path = f"$.score_history[{index}]"
        entry = _object(item, path)
        _score_scalars(entry, path)
        provenance = entry.get("score_provenance")
        source_value = provenance.get("score_source") if isinstance(provenance, Mapping) else None
        source_reference_key = (
            "manual_evidence_ref" if source_value == ScoreSource.MANUAL_IMPORT.value else "review_evidence_ref"
        )
        complete = (
            isinstance(provenance, Mapping)
            and "score_source" in provenance
            and source_reference_key in provenance
            and "scoring_evidence_ref" in provenance
            and "revision_scope" in provenance
        )
        payload = _freeze_object(entry)
        if not complete:
            scores.append(LegacyScore(authoritative=False, payload=payload))
            continue
        source = _enum(ScoreSource, provenance["score_source"], f"{path}.score_provenance.score_source")
        if source is ScoreSource.LEGACY_UNVERIFIED:
            scores.append(LegacyScore(authoritative=False, payload=payload))
            continue
        if source is ScoreSource.MANUAL_IMPORT:
            source_reference = _decode_manual_ref(
                provenance["manual_evidence_ref"],
                f"{path}.score_provenance.manual_evidence_ref",
            )
        else:
            source_reference = _decode_aggregate_ref(
                provenance["review_evidence_ref"],
                f"{path}.score_provenance.review_evidence_ref",
            )
        scope = _decode_revision_scope(
            provenance["revision_scope"], f"{path}.score_provenance.revision_scope"
        )
        scoring = _decode_content_ref(
            provenance["scoring_evidence_ref"],
            f"{path}.score_provenance.scoring_evidence_ref",
            require_size=False,
        )
        scores.append(
            BoundScore(
                authoritative=False,
                source=source,
                payload=payload,
                source_evidence_ref=source_reference,
                scoring_evidence_ref=scoring,
                revision_scope=scope,
            )
        )
        if isinstance(source_reference, ReviewAggregateRef):
            aggregate_refs.append(source_reference)
    return tuple(scores), tuple(aggregate_refs)


def _decode_history(
    raw: Any,
    path: str,
    current_epoch: int,
    current_lease_id: str,
    *,
    v5: bool = False,
) -> tuple[LeaseHistoryEntry, ...]:
    entries: list[LeaseHistoryEntry] = []
    epochs: list[int] = []
    lease_ids: list[str] = []
    for index, item in enumerate(_list(raw, path)):
        item_path = f"{path}[{index}]"
        record = _object(item, item_path)
        required = {"owner_session_id", "lease_id", "fencing_epoch", "reason", "at"}
        if v5:
            missing = required - set(record)
            if missing:
                field = sorted(missing)[0]
                raise _fail("missing-key", f"{item_path}.{field}", "required lease history field is missing")
            if set(record) - required:
                raise _fail("unknown-key", item_path, "lease history contains unknown fields")
        entry = LeaseHistoryEntry(
            owner_session_id=_string(record.get("owner_session_id"), f"{item_path}.owner_session_id", nonempty=True, trimmed=True),
            lease_id=_string(record.get("lease_id"), f"{item_path}.lease_id", nonempty=True, trimmed=True),
            fencing_epoch=_integer(record.get("fencing_epoch"), f"{item_path}.fencing_epoch", positive=True),
            reason=_string(record.get("reason"), f"{item_path}.reason", nonempty=True, trimmed=True),
            at=_aware_time(record.get("at"), f"{item_path}.at"),
        )
        if v5 and record.get("at") != entry.at:
            raise _fail("invalid-value", f"{item_path}.at", "timestamp must use canonical UTC Z form")
        entries.append(entry)
        epochs.append(entry.fencing_epoch)
        lease_ids.append(entry.lease_id)
    if epochs != sorted(set(epochs)) or any(epoch >= current_epoch for epoch in epochs):
        raise _fail("invariant-violation", path, "history epochs must strictly increase below current epoch")
    if len(lease_ids) != len(set(lease_ids)) or current_lease_id in lease_ids:
        raise _fail("invariant-violation", path, "history lease ids must be unique and retired")
    return tuple(entries)


def _decode_legacy_lease(document: Mapping[str, Any]):
    names = ("owner_session_id", "lease_id", "fencing_epoch", "lease_expires_at")
    present = [document.get(name) not in (None, "") for name in names]
    if not any(present):
        return LegacyAbsentLease()
    if not all(present):
        raise _fail("partial-lease", "$", "legacy lease must be all-present or all-absent")
    owner = _string(document["owner_session_id"], "$.owner_session_id", nonempty=True, trimmed=True)
    lease_id = _string(document["lease_id"], "$.lease_id", nonempty=True, trimmed=True)
    epoch = _integer(document["fencing_epoch"], "$.fencing_epoch", positive=True)
    return FencedLease(
        owner_session_id=owner,
        lease_id=lease_id,
        fencing_epoch=epoch,
        lease_expires_at=_aware_time(document["lease_expires_at"], "$.lease_expires_at"),
        lease_history=_decode_history(document.get("lease_history") or [], "$.lease_history", epoch, lease_id),
    )


def _decode_v4_object(document: Mapping[str, Any], frozen: FrozenJsonObject) -> MissionState:
    schema_origin = read_schema_version(document, max_reader_version=4)
    control = _decode_legacy_control(document)
    plan = _decode_legacy_plan(document)
    handoff = _decode_legacy_handoff(document, plan)
    input_refs = _decode_legacy_review_inputs(document)
    scores, aggregate_refs = _decode_legacy_scores(document)
    reviews: tuple[ReviewRef, ...] = (*input_refs, *aggregate_refs)
    return MissionState(
        schema_origin=schema_origin,
        identity=_decode_identity(document),
        control=control,
        plan=plan,
        handoff=handoff,
        reviews=reviews,
        findings=LegacyFindingsUnloaded(reviews),
        scores=scores,
        lease=_decode_legacy_lease(document),
        extensions=FrozenJsonObject(()),
        legacy_passthrough=frozen,
    )


def _decode_v4_state(source: bytes) -> MissionState:
    frozen = decode_json_object(source)
    return _decode_v4_object(thaw_json_object(frozen), frozen)


def decode_mission_state(source: bytes) -> MissionState:
    frozen = decode_json_object(source)
    document = thaw_json_object(frozen)
    schema_origin = read_schema_version(document, max_reader_version=5)
    if schema_origin is SchemaOrigin.V5:
        from .codec_v5 import _decode_v5_object

        return _decode_v5_object(document)
    return _decode_v4_object(document, frozen)


def decode_legacy_review_evidence(source: bytes, reference: ReviewRef) -> MaterializedFindings:
    document = thaw_json_object(decode_json_object(source))
    if document.get("schema") != "mission-review/1":
        raise _fail("unknown-variant", "$.schema", "expected mission-review/1")
    findings = []
    for index, value in enumerate(_list(document.get("findings"), "$.findings")):
        path = f"$.findings[{index}]"
        item = _object(value, path)
        finding_id = _string(item.get("id"), f"{path}.id", nonempty=True)
        severity = _enum(FindingSeverity, item.get("severity"), f"{path}.severity")
        findings.append(
            OpenFinding(
                id=finding_id,
                generation=1,
                iteration=_integer(item.get("iteration", getattr(reference, "iteration", 1)), f"{path}.iteration", positive=True),
                reviewer=_string(
                    item.get("perspective") or item.get("reviewer") or getattr(reference, "perspective", ""),
                    f"{path}.perspective",
                    nonempty=True,
                ),
                severity=severity,
                axis=_string(item.get("axis"), f"{path}.axis", nonempty=True),
                summary=_string(item.get("summary") or item.get("claim"), f"{path}.summary", nonempty=True),
                recommendation=_string(item.get("recommendation"), f"{path}.recommendation", nonempty=True),
                evidence_ref=reference,
                legacy_payload=_freeze_object(item),
            )
        )
    return MaterializedFindings(tuple(findings), (reference,))


def _plan_json(plan: PlanRecord) -> dict[str, Any]:
    return {
        "schema": plan.schema,
        "path": plan.path,
        "digest": plan.digest,
        "source": plan.source.value,
        "source_id": plan.source_id,
        "source_digest": plan.source_digest,
        "selection_source": plan.selection_source,
        "iteration": plan.iteration,
        "generation": plan.generation,
        "validated_at": plan.validated_at,
    }


def _handoff_json(handoff: Any) -> dict[str, Any]:
    value = {
        # The typed command uses the canonical handoff union name while this
        # projector emits the established v4 compatibility wire name.
        "schema": "mission-executor-handoff/1",
        "handoff_id": handoff.handoff_id,
        "plan_path": handoff.plan.path,
        "plan_digest": handoff.plan.digest,
        "plan_generation": handoff.plan.generation,
        "plan_source": handoff.plan.source.value,
        "source_id": handoff.plan.source_id,
        "selection_source": handoff.plan.selection_source,
        "iteration": handoff.plan.iteration,
        "step_ids": list(handoff.ordered_step_ids),
        "status": handoff.kind.value,
    }
    if isinstance(handoff, (ConsumingHandoff, ConsumedHandoff)):
        value["begun_at"] = handoff.begun_at
    if isinstance(handoff, ConsumedHandoff):
        value["consumed_at"] = handoff.consumed_at
    if isinstance(handoff, RejectedHandoff):
        value["rejected_reason"] = handoff.rejected_reason
        if handoff.begun_at is not None:
            value["begun_at"] = handoff.begun_at
    return value


def _revision_scope_json(scope: RevisionScope) -> dict[str, Any]:
    if isinstance(scope, NotApplicableRevisionScope):
        return {"kind": scope.kind.value, "reason_code": scope.reason_code}
    return {"kind": scope.kind.value, "base_sha": scope.base_sha, "head_sha": scope.head_sha}


def _aggregate_ref_json(reference: ReviewAggregateRef) -> dict[str, Any]:
    value = {
        "kind": reference.kind.value,
        "path": reference.relative_path,
        "digest": reference.digest,
        "generation": reference.generation,
        "revision_scope": _revision_scope_json(reference.revision_scope),
    }
    for key in ("review_group_id", "review_generation", "base_sha", "head_sha"):
        item = getattr(reference, key)
        if item is not None:
            value[key] = item
    if reference.size is not None:
        value["size"] = reference.size
    if reference.iteration is not None:
        value["iteration"] = reference.iteration
    return value


def _manual_ref_json(reference: ManualScoreRef) -> dict[str, Any]:
    value = {
        "kind": reference.kind,
        "path": reference.relative_path,
        "digest": reference.digest,
        "generation": reference.generation,
        "revision_scope": _revision_scope_json(reference.revision_scope),
    }
    if reference.size is not None:
        value["size"] = reference.size
    return value


def _content_ref_json(reference: ContentAddressedRef) -> dict[str, Any]:
    value = {
        "kind": reference.kind,
        "path": reference.relative_path,
        "digest": reference.digest,
    }
    if reference.size is not None:
        value["size"] = reference.size
    return value


def _legacy_score_json(score: Score) -> dict[str, Any]:
    value = score.payload.thaw()
    if isinstance(score, LegacyScore):
        return value
    provenance = dict(value.get("score_provenance") or {})
    provenance["score_source"] = score.source.value
    provenance["scoring_evidence_ref"] = _content_ref_json(score.scoring_evidence_ref)
    provenance["revision_scope"] = _revision_scope_json(score.revision_scope)
    if score.review_evidence_ref is not None:
        source_reference = _aggregate_ref_json(score.review_evidence_ref)
        provenance["review_evidence_ref"] = source_reference
        provenance.pop("manual_evidence_ref", None)
        value["review_evidence_ref"] = source_reference
        value.pop("manual_evidence_ref", None)
    else:
        source_reference = _manual_ref_json(score.manual_evidence_ref)
        provenance["manual_evidence_ref"] = source_reference
        provenance.pop("review_evidence_ref", None)
        value["manual_evidence_ref"] = source_reference
        value.pop("review_evidence_ref", None)
    value["score_source"] = score.source.value
    value["revision_scope"] = provenance["revision_scope"]
    value["score_provenance"] = provenance
    return value


def project_legacy_document(state: MissionState) -> bytes:
    if state.legacy_passthrough is None:
        raise _fail("invalid-value", "$", "legacy passthrough is unavailable")
    document = state.legacy_passthrough.thaw()
    identity = state.identity
    for key, value in (
        ("mission", identity.mission),
        ("mission_id", identity.mission_id),
        ("session_id", identity.session_id),
    ):
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
    control = state.control
    projected_control = {
        "phase": control.phase.value,
        "iteration": control.iteration,
        "reviewer_count": control.reviewer_count,
        "stagnation_count": control.stagnation_count,
        "loop_active": control.loop_active,
        "passes": control.passes,
        "halt_reason": control.halt_reason,
        "session_role": control.session_role.value,
    }
    for key, value in projected_control.items():
        if key in document:
            document[key] = value
    for key, value in (
        ("max_iter", control.max_iter),
        ("threshold", control.threshold),
    ):
        if key not in document:
            continue
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
    for key, value in (
        ("halt_category", None if control.halt_category is None else control.halt_category.value),
        ("terminal_outcome", None if state.terminal_outcome is None else state.terminal_outcome.value),
    ):
        if value is None:
            document.pop(key, None)
        else:
            document[key] = value
    if isinstance(state.plan, AbsentPlan):
        document.pop("canonical_plan", None)
    else:
        projected_plan = _plan_json(state.plan)
        existing_plan = document.get("canonical_plan")
        if state.schema_origin is not SchemaOrigin.V5 and isinstance(existing_plan, dict):
            presence_preserved = dict(existing_plan)
            for key in set(existing_plan) & set(projected_plan):
                presence_preserved[key] = projected_plan[key]
            projected_plan = presence_preserved
        document["canonical_plan"] = projected_plan
    if isinstance(state.handoff, AbsentHandoff):
        document.pop("executor_handoff", None)
    else:
        document["executor_handoff"] = _handoff_json(state.handoff)
    input_refs = [reference for reference in state.reviews if isinstance(reference, ReviewInputRef)]
    if (
        state.schema_origin is SchemaOrigin.V5
        or "review_evidence_refs" not in document
    ) and input_refs:
        document["review_evidence_refs"] = [
            {
                "kind": "review-input",
                "path": reference.relative_path,
                "digest": reference.digest,
                "size": reference.size,
                "iteration": reference.iteration,
                "perspective": reference.perspective,
            }
            for reference in input_refs
        ]
    elif state.schema_origin is SchemaOrigin.V5 and "review_evidence_refs" in document:
        document["review_evidence_refs"] = []
    raw_scores = ()
    if state.schema_origin is not SchemaOrigin.V5 and "score_history" in document:
        raw_scores, _raw_aggregates = _decode_legacy_scores(document)
    if state.schema_origin is SchemaOrigin.V5 or state.scores != raw_scores:
        projected_scores = [_legacy_score_json(score) for score in state.scores]
        if state.schema_origin is SchemaOrigin.V5:
            for score in projected_scores:
                score.setdefault("iteration", control.iteration)
        document["score_history"] = projected_scores
    if isinstance(state.lease, LegacyAbsentLease):
        for key in ("owner_session_id", "lease_id", "fencing_epoch", "lease_expires_at", "lease_history"):
            document.pop(key, None)
    else:
        document.update(
            {
                "owner_session_id": state.lease.owner_session_id,
                "lease_id": state.lease.lease_id,
                "fencing_epoch": state.lease.fencing_epoch,
                "lease_expires_at": state.lease.lease_expires_at,
            }
        )
        lease_history = [
            {
                "owner_session_id": item.owner_session_id,
                "lease_id": item.lease_id,
                "fencing_epoch": item.fencing_epoch,
                "reason": item.reason,
                "at": item.at,
            }
            for item in state.lease.lease_history
        ]
        if lease_history or "lease_history" in document:
            document["lease_history"] = lease_history
    return encode_json_object(_freeze_object(document))
