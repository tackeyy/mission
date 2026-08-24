"""Closed v5 state-generation decoder and pure canonical encoder."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from mission_common import derive_terminal_outcome

from .codec_v4 import (
    _aware_time,
    _boolean,
    _decode_aggregate_ref,
    _decode_content_ref,
    _decode_history,
    _decode_manual_ref,
    _decode_plan_record,
    _decode_revision_scope,
    _digest,
    _enum,
    _fail,
    _freeze_object,
    _integer,
    _list,
    _number,
    _object,
    _project_terminal_outcome_control,
    _score_scalars,
    _string,
)
from .errors import MissionStateDecodeError
from .json_codec import decode_json_object, encode_json_object, freeze_json_value, thaw_json_object
from .model import (
    AbsentHandoff,
    AbsentPlan,
    BoundScore,
    ConsumedHandoff,
    ConsumingHandoff,
    ContentAddressedRef,
    CorePlan,
    FencedLease,
    FindingIdentity,
    FindingResolutionRef,
    FindingSeverity,
    FindingStatus,
    FrozenJsonObject,
    GitRevisionScope,
    HaltCategory,
    LegacyAbsentLease,
    LeaseHistoryEntry,
    MaterializedFindings,
    MissionControl,
    MissionIdentity,
    MissionState,
    NotApplicableRevisionScope,
    OpenFinding,
    Phase,
    PlanRecord,
    PreparedHandoff,
    ProviderPlan,
    RejectedHandoff,
    ResolvedFinding,
    ReviewAggregateRef,
    ReviewInputRef,
    ReviewKind,
    RevisionScope,
    SchemaOrigin,
    ScoreSource,
    SessionRole,
    SnapshotProvenance,
    TerminalOutcome,
)
from .versions import read_schema_version

_TOP_LEVEL = {
    "schema_version",
    "identity",
    "control",
    "plan",
    "handoff",
    "reviews",
    "findings",
    "scores",
    "lease",
    "guidance",
    "extensions",
}
_CONTROL = {
    "phase",
    "terminal_outcome",
    "iteration",
    "max_iter",
    "threshold",
    "reviewer_count",
    "stagnation_count",
    "loop_active",
    "passes",
    "halt_reason",
    "halt_category",
    "session_role",
}


def _exact(
    value: Mapping[str, Any],
    required: set[str],
    path: str,
    *,
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    missing = required - set(value)
    if missing:
        field = sorted(missing)[0]
        raise _fail("missing-key", f"{path}.{field}", "required field is missing")
    extras = set(value) - allowed
    if extras:
        raise _fail("unknown-key", path, f"unknown key(s): {sorted(extras)!r}")


def _canonical_time(value: Any, path: str) -> str:
    normalized = _aware_time(value, path)
    if value != normalized:
        raise _fail("invalid-value", path, "timestamp must use UTC seconds precision with Z")
    return normalized


def _decode_identity(document: Mapping[str, Any]) -> MissionIdentity:
    identity = _object(document["identity"], "$.identity")
    required = {"mission", "mission_id", "session_id"}
    _exact(identity, required, "$.identity")
    return MissionIdentity(
        _string(identity["mission"], "$.identity.mission", nonempty=True, trimmed=True, maximum=64 * 1024),
        _string(identity["mission_id"], "$.identity.mission_id", nonempty=True, trimmed=True, maximum=128),
        _string(identity["session_id"], "$.identity.session_id", nonempty=True, trimmed=True, maximum=128),
    )


def _decode_control(document: Mapping[str, Any]) -> MissionControl:
    raw = _object(document["control"], "$.control")
    _exact(raw, _CONTROL, "$.control")
    phase = _enum(Phase, raw["phase"], "$.control.phase")
    terminal = (
        None
        if raw["terminal_outcome"] is None
        else _enum(TerminalOutcome, raw["terminal_outcome"], "$.control.terminal_outcome")
    )
    iteration = _integer(raw["iteration"], "$.control.iteration", non_negative=True)
    max_iter = (
        None
        if raw["max_iter"] is None
        else _integer(raw["max_iter"], "$.control.max_iter", positive=True)
    )
    threshold = _number(raw["threshold"], "$.control.threshold")
    if threshold < 0 or threshold > 5:
        raise _fail("invalid-value", "$.control.threshold", "threshold must be within 0..5")
    reviewer_count = _integer(raw["reviewer_count"], "$.control.reviewer_count", positive=True)
    stagnation_count = _integer(raw["stagnation_count"], "$.control.stagnation_count", non_negative=True)
    loop_active = _boolean(raw["loop_active"], "$.control.loop_active")
    passes = _boolean(raw["passes"], "$.control.passes")
    halt_reason = _string(raw["halt_reason"], "$.control.halt_reason")
    halt_category = (
        None
        if raw["halt_category"] is None
        else _enum(HaltCategory, raw["halt_category"], "$.control.halt_category")
    )
    session_role = _enum(SessionRole, raw["session_role"], "$.control.session_role")
    projection = _project_terminal_outcome_control(
        {
            "passes": passes,
            "loop_active": loop_active,
            "halt_reason": halt_reason,
            "halt_category": None if halt_category is None else halt_category.value,
            "session_role": session_role.value,
            "terminal_outcome": None if terminal is None else terminal.value,
        }
    )
    derived = derive_terminal_outcome(projection)
    expected = None if terminal is None else terminal.value
    if derived != expected:
        raise _fail("invariant-violation", "$.control.terminal_outcome", "terminal outcome contradicts control")
    if terminal is None and phase in {Phase.DONE, Phase.HALTED}:
        raise _fail("invariant-violation", "$.control.phase", "active control requires a nonterminal phase")
    if terminal in {TerminalOutcome.COMPLETED_PASS, TerminalOutcome.COMPLETED_EVIDENCE} and phase is not Phase.DONE:
        raise _fail("invariant-violation", "$.control.phase", "completion requires done phase")
    if terminal is not None and terminal not in {TerminalOutcome.COMPLETED_PASS, TerminalOutcome.COMPLETED_EVIDENCE} and phase is not Phase.HALTED:
        raise _fail("invariant-violation", "$.control.phase", "non-completion terminal requires halted phase")
    return MissionControl(
        phase=phase,
        terminal_outcome=terminal,
        iteration=iteration,
        max_iter=max_iter,
        threshold=threshold,
        reviewer_count=reviewer_count,
        stagnation_count=stagnation_count,
        loop_active=loop_active,
        passes=passes,
        halt_reason=halt_reason,
        halt_category=halt_category,
        session_role=session_role,
    )


def _decode_plan(document: Mapping[str, Any]):
    raw = _object(document["plan"], "$.plan")
    if raw.get("kind") == "absent":
        _exact(raw, {"kind"}, "$.plan")
        return AbsentPlan()
    required = {
        "kind",
        "schema",
        "path",
        "digest",
        "source",
        "source_id",
        "source_digest",
        "selection_source",
        "iteration",
        "generation",
        "validated_at",
    }
    _exact(raw, required, "$.plan")
    kind = raw["kind"]
    if kind not in {"core", "provider"}:
        raise _fail("unknown-variant", "$.plan.kind", f"unknown plan kind {kind!r}")
    if raw["source"] not in {"core", "provider"}:
        raise _fail("unknown-variant", "$.plan.source", f"unknown plan source {raw['source']!r}")
    if raw["source"] != kind:
        raise _fail("invariant-violation", "$.plan.source", "plan kind and source must match")
    normalized = dict(raw)
    normalized.pop("kind")
    plan = _decode_plan_record(normalized, "$.plan", require_validated=True)
    canonical_time = _canonical_time(raw["validated_at"], "$.plan.validated_at")
    if plan.validated_at != canonical_time:
        raise _fail("invalid-value", "$.plan.validated_at", "timestamp is not canonical")
    return plan


def _decode_handoff_plan(raw: Any) -> PlanRecord:
    path = "$.handoff.plan"
    plan = _object(raw, path)
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
    _exact(plan, required, path)
    return _decode_plan_record(plan, path, require_validated=False)


def _plan_binding(plan: PlanRecord) -> tuple[Any, ...]:
    return (
        plan.schema,
        plan.path,
        plan.digest,
        plan.source,
        plan.source_id,
        plan.source_digest,
        plan.selection_source,
        plan.iteration,
        plan.generation,
    )


def _decode_handoff(document: Mapping[str, Any], plan: Any):
    raw = _object(document["handoff"], "$.handoff")
    if raw.get("kind") == "absent":
        _exact(raw, {"kind"}, "$.handoff")
        return AbsentHandoff()
    common = {"kind", "schema", "handoff_id", "plan", "ordered_step_ids"}
    kind = raw.get("kind")
    optional_by_kind = {
        "prepared": set(),
        "consuming": {"begun_at"},
        "consumed": {"begun_at", "consumed_at"},
        "rejected": {"rejected_reason"},
    }
    if kind not in optional_by_kind:
        raise _fail("unknown-variant", "$.handoff.kind", f"unknown handoff kind {kind!r}")
    required = common | optional_by_kind[kind]
    optional = {"begun_at"} if kind == "rejected" else set()
    _exact(raw, required, "$.handoff", optional=optional)
    handoff_plan = _decode_handoff_plan(raw["plan"])
    if isinstance(plan, AbsentPlan):
        raise _fail("invariant-violation", "$.handoff.plan", "handoff requires canonical plan")
    binding_fields = (
        "schema",
        "path",
        "digest",
        "source",
        "source_id",
        "source_digest",
        "selection_source",
        "iteration",
        "generation",
    )
    for field in binding_fields:
        if getattr(handoff_plan, field) != getattr(plan, field):
            raise _fail(
                "invariant-violation",
                f"$.handoff.plan.{field}",
                "handoff plan must match canonical plan",
            )
    steps = tuple(
        _string(item, "$.handoff.ordered_step_ids", nonempty=True)
        for item in _list(raw["ordered_step_ids"], "$.handoff.ordered_step_ids")
    )
    common_fields = {
        "schema": _string(raw["schema"], "$.handoff.schema", nonempty=True),
        "handoff_id": _string(raw["handoff_id"], "$.handoff.handoff_id", nonempty=True),
        "plan": handoff_plan,
        "ordered_step_ids": steps,
    }
    if kind == "prepared":
        return PreparedHandoff(**common_fields)
    if kind == "consuming":
        return ConsumingHandoff(
            **common_fields,
            begun_at=_canonical_time(raw["begun_at"], "$.handoff.begun_at"),
        )
    if kind == "consumed":
        return ConsumedHandoff(
            **common_fields,
            begun_at=_canonical_time(raw["begun_at"], "$.handoff.begun_at"),
            consumed_at=_canonical_time(raw["consumed_at"], "$.handoff.consumed_at"),
        )
    return RejectedHandoff(
        **common_fields,
        rejected_reason=_string(raw["rejected_reason"], "$.handoff.rejected_reason", nonempty=True),
        begun_at=None
        if raw.get("begun_at") is None
        else _canonical_time(raw["begun_at"], "$.handoff.begun_at"),
    )


def _decode_review(value: Any, index: int):
    path = f"$.reviews[{index}]"
    raw = _object(value, path)
    kind = raw.get("kind")
    if kind == "review-input":
        required = {"kind", "relative_path", "digest", "size", "iteration", "perspective"}
        _exact(raw, required, path)
        return ReviewInputRef(
            relative_path=_string(raw["relative_path"], f"{path}.relative_path", nonempty=True),
            digest=_digest(raw["digest"], f"{path}.digest"),
            size=_integer(raw["size"], f"{path}.size", positive=True),
            iteration=_integer(raw["iteration"], f"{path}.iteration", positive=True),
            perspective=_string(raw["perspective"], f"{path}.perspective", nonempty=True),
        )
    if kind == "review-aggregate":
        required = {
            "kind",
            "relative_path",
            "digest",
            "size",
            "iteration",
            "generation",
            "revision_scope",
        }
        lineage = {"review_group_id", "review_generation", "base_sha", "head_sha"}
        _exact(raw, required, path, optional=lineage)
        return _decode_aggregate_ref(raw, path, v5=True)
    raise _fail("unknown-variant", f"{path}.kind", f"unknown review kind {kind!r}")


def _decode_finding_evidence(value: Any, path: str):
    raw = _object(value, path)
    kind = raw.get("kind")
    if kind == "review-input":
        required = {"kind", "relative_path", "digest", "size", "iteration", "perspective"}
        _exact(raw, required, path)
        return ReviewInputRef(
            relative_path=_string(raw["relative_path"], f"{path}.relative_path", nonempty=True),
            digest=_digest(raw["digest"], f"{path}.digest"),
            size=_integer(raw["size"], f"{path}.size", positive=True),
            iteration=_integer(raw["iteration"], f"{path}.iteration", positive=True),
            perspective=_string(raw["perspective"], f"{path}.perspective", nonempty=True),
        )
    if kind == "review-aggregate":
        return _decode_aggregate_ref(raw, path, v5=True)
    raise _fail("unknown-variant", f"{path}.kind", "finding evidence must be a review reference")


def _decode_finding(value: Any, index: int):
    path = f"$.findings[{index}]"
    raw = _object(value, path)
    common = {
        "id",
        "generation",
        "iteration",
        "reviewer",
        "severity",
        "axis",
        "summary",
        "recommendation",
        "evidence_ref",
        "status",
    }
    status = raw.get("status")
    if status not in {"open", "resolved"}:
        raise _fail("finding-status-invalid", f"{path}.status", f"invalid finding status {status!r}")
    resolution = {"prior_identity", "resolution_evidence_ref", "resolved_at"}
    if status == "open":
        _exact(raw, common, path)
    else:
        _exact(raw, common | resolution, path)
    finding_id = _string(raw["id"], f"{path}.id", nonempty=True)
    generation = _integer(raw["generation"], f"{path}.generation", positive=True)
    common_fields = {
        "id": finding_id,
        "generation": generation,
        "iteration": _integer(raw["iteration"], f"{path}.iteration", positive=True),
        "reviewer": _string(raw["reviewer"], f"{path}.reviewer", nonempty=True),
        "severity": _enum(FindingSeverity, raw["severity"], f"{path}.severity"),
        "axis": _string(raw["axis"], f"{path}.axis", nonempty=True),
        "summary": _string(raw["summary"], f"{path}.summary", nonempty=True),
        "recommendation": _string(raw["recommendation"], f"{path}.recommendation", nonempty=True),
        "evidence_ref": _decode_finding_evidence(raw["evidence_ref"], f"{path}.evidence_ref"),
    }
    if status == "open":
        return OpenFinding(**common_fields)
    prior = _object(raw["prior_identity"], f"{path}.prior_identity")
    _exact(prior, {"id", "generation"}, f"{path}.prior_identity")
    prior_id = _string(prior["id"], f"{path}.prior_identity.id", nonempty=True)
    prior_generation = _integer(prior["generation"], f"{path}.prior_identity.generation", positive=True)
    if prior_id != finding_id:
        raise _fail("invariant-violation", f"{path}.prior_identity.id", "prior id must match finding id")
    if prior_generation >= generation:
        raise _fail("invariant-violation", f"{path}.prior_identity.generation", "prior generation must be lower")
    resolution_ref = _object(raw["resolution_evidence_ref"], f"{path}.resolution_evidence_ref")
    _exact(
        resolution_ref,
        {"kind", "relative_path", "digest", "size"},
        f"{path}.resolution_evidence_ref",
    )
    if resolution_ref["kind"] != "finding-resolution":
        raise _fail("unknown-variant", f"{path}.resolution_evidence_ref.kind", "expected finding-resolution")
    return ResolvedFinding(
        **common_fields,
        prior_identity=FindingIdentity(prior_id, prior_generation),
        resolution_evidence_ref=FindingResolutionRef(
            relative_path=_string(
                resolution_ref["relative_path"],
                f"{path}.resolution_evidence_ref.relative_path",
                nonempty=True,
            ),
            digest=_digest(resolution_ref["digest"], f"{path}.resolution_evidence_ref.digest"),
            size=_integer(resolution_ref["size"], f"{path}.resolution_evidence_ref.size", positive=True),
        ),
        resolved_at=_canonical_time(raw["resolved_at"], f"{path}.resolved_at"),
    )


def _decode_score(value: Any, index: int) -> BoundScore:
    path = f"$.scores[{index}]"
    raw = _object(value, path)
    source = _enum(ScoreSource, raw.get("source"), f"{path}.source")
    if source is ScoreSource.LEGACY_UNVERIFIED:
        raise _fail("unknown-variant", f"{path}.source", "v5 does not allow legacy score")
    required = {
        "source",
        "items",
        "composite",
        "min_item",
        "agreement",
        "open_high",
        "scoring_evidence_ref",
        "revision_scope",
    }
    source_reference_key = (
        "manual_evidence_ref" if source is ScoreSource.MANUAL_IMPORT else "review_evidence_ref"
    )
    required.add(source_reference_key)
    _exact(raw, required, path)
    _score_scalars(
        {
            "items": raw["items"],
            "composite": raw["composite"],
            "min_item": raw["min_item"],
            "review_agreement": raw["agreement"],
            "open_high": raw["open_high"],
        },
        path,
    )
    if source is ScoreSource.MANUAL_IMPORT:
        source_reference = _decode_manual_ref(
            raw["manual_evidence_ref"], f"{path}.manual_evidence_ref", v5=True
        )
    else:
        source_reference = _decode_aggregate_ref(
            raw["review_evidence_ref"], f"{path}.review_evidence_ref", v5=True
        )
    scoring = _decode_content_ref(
        raw["scoring_evidence_ref"],
        f"{path}.scoring_evidence_ref",
        require_size=True,
        v5=True,
        expected_kind="scoring-artifact",
    )
    scope = _decode_revision_scope(raw["revision_scope"], f"{path}.revision_scope", v5=True)
    if source_reference.revision_scope != scope:
        raise _fail("invariant-violation", f"{path}.revision_scope", "source evidence scope must match score scope")
    return BoundScore(False, source, _freeze_object(raw), source_reference, scoring, scope)


def _decode_lease(document: Mapping[str, Any]) -> FencedLease:
    raw = _object(document["lease"], "$.lease")
    required = {
        "kind",
        "owner_session_id",
        "lease_id",
        "fencing_epoch",
        "lease_expires_at",
        "lease_history",
    }
    _exact(raw, required, "$.lease")
    if raw["kind"] != "fenced":
        raise _fail("unknown-variant", "$.lease.kind", "v5 lease must be fenced")
    owner = _string(raw["owner_session_id"], "$.lease.owner_session_id", nonempty=True, trimmed=True)
    lease_id = _string(raw["lease_id"], "$.lease.lease_id", nonempty=True, trimmed=True)
    epoch = _integer(raw["fencing_epoch"], "$.lease.fencing_epoch", positive=True)
    return FencedLease(
        owner_session_id=owner,
        lease_id=lease_id,
        fencing_epoch=epoch,
        lease_expires_at=_canonical_time(raw["lease_expires_at"], "$.lease.lease_expires_at"),
        lease_history=_decode_history(
            raw["lease_history"], "$.lease.lease_history", epoch, lease_id, v5=True
        ),
    )


def _decode_v5_object(document: Mapping[str, Any]) -> MissionState:
    _exact(document, _TOP_LEVEL, "$")
    if read_schema_version(document, max_reader_version=5) is not SchemaOrigin.V5:
        raise _fail("unsupported-schema-version", "$.schema_version", "expected schema_version 5")
    identity = _decode_identity(document)
    from .guidance import decode_v5_guidance

    decode_v5_guidance(
        document["guidance"],
        SnapshotProvenance(
            schema_origin=SchemaOrigin.V5,
            session_id=identity.session_id,
            document_digest="sha256:" + "0" * 64,
        ),
    )
    control = _decode_control(document)
    plan = _decode_plan(document)
    handoff = _decode_handoff(document, plan)
    reviews = tuple(
        _decode_review(value, index)
        for index, value in enumerate(_list(document["reviews"], "$.reviews"))
    )
    findings = tuple(
        _decode_finding(value, index)
        for index, value in enumerate(_list(document["findings"], "$.findings"))
    )
    scores = tuple(
        _decode_score(value, index)
        for index, value in enumerate(_list(document["scores"], "$.scores"))
    )
    extensions = _freeze_object(_object(document["extensions"], "$.extensions"))
    from .codec_v4 import _decode_a4_projection

    return MissionState(
        schema_origin=SchemaOrigin.V5,
        identity=identity,
        control=control,
        plan=plan,
        handoff=handoff,
        reviews=reviews,
        findings=MaterializedFindings(findings, reviews),
        scores=scores,
        lease=_decode_lease(document),
        extensions=extensions,
        legacy_passthrough=None,
        a4=_decode_a4_projection(extensions.thaw(), handoff, "$.extensions"),
    )


def _decode_v5_state(source: bytes) -> MissionState:
    return _decode_v5_object(thaw_json_object(decode_json_object(source)))


def _revision_scope_json(scope: RevisionScope) -> dict[str, Any]:
    if isinstance(scope, NotApplicableRevisionScope):
        return {"kind": scope.kind.value, "reason_code": scope.reason_code}
    return {"kind": scope.kind.value, "base_sha": scope.base_sha, "head_sha": scope.head_sha}


def _review_json(reference: Any) -> dict[str, Any]:
    if isinstance(reference, ReviewInputRef):
        return {
            "kind": reference.kind.value,
            "relative_path": reference.relative_path,
            "digest": reference.digest,
            "size": reference.size,
            "iteration": reference.iteration,
            "perspective": reference.perspective,
        }
    value = {
        "kind": reference.kind.value,
        "relative_path": reference.relative_path,
        "digest": reference.digest,
        "size": reference.size,
        "iteration": reference.iteration,
        "generation": reference.generation,
        "revision_scope": _revision_scope_json(reference.revision_scope),
    }
    for key in ("review_group_id", "review_generation", "base_sha", "head_sha"):
        item = getattr(reference, key)
        if item is not None:
            value[key] = item
    return value


def _plan_json(plan: PlanRecord, *, handoff: bool = False) -> dict[str, Any]:
    value = {
        "schema": plan.schema,
        "path": plan.path,
        "digest": plan.digest,
        "source": plan.source.value,
        "source_id": plan.source_id,
        "source_digest": plan.source_digest,
        "selection_source": plan.selection_source,
        "iteration": plan.iteration,
        "generation": plan.generation,
    }
    if not handoff:
        value["kind"] = plan.kind
        value["validated_at"] = plan.validated_at
    return value


def _handoff_json(handoff: Any) -> dict[str, Any]:
    if isinstance(handoff, AbsentHandoff):
        return {"kind": "absent"}
    value = {
        "kind": handoff.kind.value,
        "schema": handoff.schema,
        "handoff_id": handoff.handoff_id,
        "plan": _plan_json(handoff.plan, handoff=True),
        "ordered_step_ids": list(handoff.ordered_step_ids),
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


def _finding_json(finding: Any) -> dict[str, Any]:
    value = {
        "id": finding.id,
        "generation": finding.generation,
        "iteration": finding.iteration,
        "reviewer": finding.reviewer,
        "severity": finding.severity.value,
        "axis": finding.axis,
        "summary": finding.summary,
        "recommendation": finding.recommendation,
        "evidence_ref": _review_json(finding.evidence_ref),
        "status": finding.status.value,
    }
    if isinstance(finding, ResolvedFinding):
        value.update(
            {
                "prior_identity": {
                    "id": finding.prior_identity.id,
                    "generation": finding.prior_identity.generation,
                },
                "resolution_evidence_ref": {
                    "kind": finding.resolution_evidence_ref.kind,
                    "relative_path": finding.resolution_evidence_ref.relative_path,
                    "digest": finding.resolution_evidence_ref.digest,
                    "size": finding.resolution_evidence_ref.size,
                },
                "resolved_at": finding.resolved_at,
            }
        )
    return value


def _score_json(score: BoundScore) -> dict[str, Any]:
    return score.payload.thaw()


def _lease_json(lease: FencedLease) -> dict[str, Any]:
    return {
        "kind": lease.kind.value,
        "owner_session_id": lease.owner_session_id,
        "lease_id": lease.lease_id,
        "fencing_epoch": lease.fencing_epoch,
        "lease_expires_at": lease.lease_expires_at,
        "lease_history": [
            {
                "owner_session_id": item.owner_session_id,
                "lease_id": item.lease_id,
                "fencing_epoch": item.fencing_epoch,
                "reason": item.reason,
                "at": item.at,
            }
            for item in lease.lease_history
        ],
    }


def _state_payload(state: MissionState, guidance: Any) -> dict[str, Any]:
    from .guidance import guidance_payload
    from .a4 import project_v4_a4

    if isinstance(state.plan, AbsentPlan):
        plan = {"kind": "absent"}
    else:
        plan = _plan_json(state.plan)
    if not isinstance(state.findings, MaterializedFindings):
        raise _fail("invalid-value", "$.findings", "v5 requires materialized findings")
    if not isinstance(state.lease, FencedLease):
        raise _fail("unknown-variant", "$.lease.kind", "v5 requires fenced lease")
    extensions = state.extensions.thaw()
    project_v4_a4(
        extensions,
        state.a4,
        state.handoff,
    )
    return {
        "schema_version": 5,
        "identity": {
            "mission": state.identity.mission,
            "mission_id": state.identity.mission_id,
            "session_id": state.identity.session_id,
        },
        "control": {
            "phase": state.control.phase.value,
            "terminal_outcome": None if state.control.terminal_outcome is None else state.control.terminal_outcome.value,
            "iteration": state.control.iteration,
            "max_iter": state.control.max_iter,
            "threshold": state.control.threshold,
            "reviewer_count": state.control.reviewer_count,
            "stagnation_count": state.control.stagnation_count,
            "loop_active": state.control.loop_active,
            "passes": state.control.passes,
            "halt_reason": state.control.halt_reason,
            "halt_category": None if state.control.halt_category is None else state.control.halt_category.value,
            "session_role": state.control.session_role.value,
        },
        "plan": plan,
        "handoff": _handoff_json(state.handoff),
        "reviews": [_review_json(reference) for reference in state.reviews],
        "findings": [_finding_json(finding) for finding in state.findings.findings],
        "scores": [_score_json(score) for score in state.scores],
        "lease": _lease_json(state.lease),
        "guidance": guidance_payload(guidance),
        "extensions": extensions,
    }


def encode_v5_state(state: MissionState, guidance: Any) -> bytes:
    if not isinstance(state, MissionState):
        raise _fail("invalid-type", "$", "encode_v5_state expects MissionState")
    if state.schema_origin is not SchemaOrigin.V5:
        raise _fail("unsupported-schema-version", "$.schema_version", "encode_v5_state requires v5 state")
    if state.legacy_passthrough is not None:
        raise _fail("invalid-value", "$", "v5 state cannot carry legacy passthrough")
    payload = _state_payload(state, guidance)
    validated = _decode_v5_object(payload)
    if validated != replace(state, snapshot_provenance=None):
        raise _fail("invariant-violation", "$", "model does not equal its closed v5 projection")
    return encode_json_object(_freeze_object(payload))
