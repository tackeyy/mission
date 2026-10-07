"""E0b-2a (#936): pure kernel derivation of the state capacity reservation.

This module holds the *reservation* half of the capacity scheme decided in
``docs/design/880-repair-lineage.md`` sections "決定（容量予約の再設計...）"
through "決定（pending request の取下げ...）": the Delta constants, the
lineage variable part, the halt-slot and lease-takeover system share, the
D request projection reader, and the two boolean capacity predicates
(``satisfies_capacity`` / ``is_over_capacity``).

The *verdict* half -- ``write_kind`` derivation, legacy-full detection, and
``state_capacity_verdict`` itself -- is a follow-up module (E0b-2b) that
imports this one. No writer lives here (that is D2c/#918).

Pure function only: no ``os``/``pathlib``/clock/random imports. The only
kernel dependencies are :mod:`mission_kernel.json_codec`,
:mod:`mission_kernel.fresh_review` and
:mod:`mission_kernel.fresh_review_receipts` (the last is imported only for
its shape constants, not called directly -- the D terminal shapes below are
duplicated as closed literals so this module stays import-light and so the
Delta constants stay pinned to *this* module's own measurement, independent
of any future change to the receipts module's shapes).
"""
from __future__ import annotations

from enum import Enum
from typing import Mapping, Optional

from .json_codec import STATE_LIMIT, encode_json_value, freeze_json_value
from . import fresh_review as _fresh_review
from .fresh_review import (
    FreshReviewError,
    FreshReviewProjection,
    FreshReviewRecord,
    WithdrawnFreshReviewRecord,
)


# ---------------------------------------------------------------------------
# Shared state encoding (also used by the verdict half, #937).
# ---------------------------------------------------------------------------


class StateEncoding(str, Enum):
    CANONICAL = "canonical"
    LEGACY_PRETTY = "legacy-pretty"


# ---------------------------------------------------------------------------
# System share: halt + lease-takeover reservation (design doc "検査式").
# ---------------------------------------------------------------------------

#: One sixteenth of the physical state limit, reserved so that a session can
#: always still record a halt and a bounded number of lease takeovers even
#: while D request reservations consume the rest of the budget.
STATE_CAPACITY_SYSTEM_SHARE = STATE_LIMIT // 16

#: Maximum encode-length increase of a single halt / mark-halt write.
#: Measured (see test_issue933_state_capacity_kernel.py::test_halt_delta_pins_*)
#: from the "field absent" and "field empty" base variants, across both the
#: v5 (``control`` + mirrored ``extensions``) and v4 flat layouts, and both
#: the canonical and legacy-pretty encodings. The maximal single write bundles
#: every field a halt can touch across any ``HaltCategory`` branch (the
#: ``routed-goal`` ``goal_dispatch_*`` fields *and* an ``awaiting-approval``
#: activity-segment close + rollup-key growth) even though a single real
#: call only ever takes one category's branch -- summing every branch is a
#: deliberate, documented over-provisioning, not a measurement of one call.
STATE_CAPACITY_HALT_DELTA = 13779

#: Maximum encode-length increase of a single lease takeover: one
#: ``lease_history`` entry at its maximum field lengths, plus the increase
#: from the smallest possible current-lease fields to the largest possible
#: ones. Measured from the smallest ("single ASCII character ids") base
#: lease to the largest ("128-character ids, max epoch, max-length history
#: entry") proposed lease.
STATE_CAPACITY_TAKEOVER_DELTA = 822

#: Largest integer N_L such that
#: ``STATE_CAPACITY_HALT_DELTA + N_L * STATE_CAPACITY_TAKEOVER_DELTA <=
#: STATE_CAPACITY_SYSTEM_SHARE``.
STATE_CAPACITY_TAKEOVER_LIMIT = (
    STATE_CAPACITY_SYSTEM_SHARE - STATE_CAPACITY_HALT_DELTA
) // STATE_CAPACITY_TAKEOVER_DELTA

if STATE_CAPACITY_TAKEOVER_LIMIT < 1:
    raise RuntimeError(
        "state-capacity-takeover-limit-exhausted: STATE_CAPACITY_HALT_DELTA "
        "and STATE_CAPACITY_TAKEOVER_DELTA leave no room for any lease "
        "takeover inside STATE_CAPACITY_SYSTEM_SHARE; the Delta constants "
        "or the 1/16 ratio must be revisited before this module may load."
    )

# ---------------------------------------------------------------------------
# D request stage reservations ("D request の段の Δ").
# ---------------------------------------------------------------------------

#: One reserved record's key name slack (D2c has not fixed the field names
#: yet; this bounds any single key growing by this many bytes per reserved
#: slot name: ``dispatch`` / ``running`` / ``terminal_receipt``).
_FRESH_REVIEW_KEY_SLACK = 32
_FRESH_REVIEW_RESERVED_SLOT_COUNT = 3

#: Envelope growth (``updated_at`` / ``last_activity_at`` / lease refresh)
#: that rides along with any stage-advancing write.
_FRESH_REVIEW_ENVELOPE_ALLOWANCE = 128

#: dispatch (reserve): three null->value fields (operation_id 128 chars,
#: two 71-char digests) + the E0a-pinned maximum intent (1,797 bytes) and
#: maximum running record (3,455 bytes) + the request status value's
#: longest delta + the reserved-slot key slack + the envelope allowance.
FRESH_REVIEW_DISPATCH_STAGE_DELTA = (
    (130 - 4) + 2 * ((71 + 2) - 4)
    + 1797 + 3455
    + 20
    + _FRESH_REVIEW_KEY_SLACK * _FRESH_REVIEW_RESERVED_SLOT_COUNT
    + _FRESH_REVIEW_ENVELOPE_ALLOWANCE
)

#: consume: the frozen ``result`` (bounded by ``request.max_output_bytes``,
#: 256 KiB) plus fixed wrapper overhead.
FRESH_REVIEW_CONSUME_STAGE_DELTA = 262144 + 64

#: terminal: the larger of the four D2b terminal-variant maxima pinned by
#: E0a (completed 17,925 is the largest) plus the reserved-slot key slack
#: for ``terminal_receipt`` and the envelope allowance.
FRESH_REVIEW_TERMINAL_STAGE_DELTA = (
    17925 + _FRESH_REVIEW_KEY_SLACK + _FRESH_REVIEW_ENVELOPE_ALLOWANCE
)

#: lineage introduction: E1's FindingLineage type does not exist yet. This
#: pins a closed placeholder shape (lineage_id/finding_digest/
#: origin_request_id/disposition, each at ASCII-128 or digest-71 bound),
#: *excluding* E2/E3's own disposition/duplicate-of cost (stubbed to zero
#: below; must not be folded into this constant without revisiting it).
#: Measured standalone (not inside an array) at its maximum field lengths;
#: the ``+16`` is a per-array-element separator/indentation allowance
#: (the same "+separator" pattern the design doc applies to the finding
#: ref shape: "最大形で 238 bytes。区切りを含め 239 bytes") sized from the
#: measured gap between 61 standalone records (549 bytes each) and one
#: 61-element legacy-pretty array of them (34,345 bytes; gap ~14/record).
FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES = 549 + 16

#: Single "unimported-findings" lineage record's maximum encode length
#: (standalone; it is never placed in a per-finding array).
FRESH_REVIEW_UNIMPORTED_LINEAGE_MAX_BYTES = 442

#: F_MAX: the D terminal findings-count limit (also K, the lineage count
#: limit; see docs/design/880-repair-lineage.md "D 終端の findings 上限").
FRESH_REVIEW_FINDINGS_LINEAGE_LIMIT = _fresh_review.FRESH_REVIEW_FINDINGS_LIMIT

#: lineage Δ with a zero-length criterion variable part (no contract
#: consulted). Kept for callers that only need a document-independent
#: baseline; real residual-reservation computation calls
#: ``lineage_stage_delta`` with the actual document and record instead,
#: since the variable part depends on that request's own criteria.
FRESH_REVIEW_LINEAGE_STAGE_DELTA = max(
    FRESH_REVIEW_FINDINGS_LINEAGE_LIMIT * FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES,
    FRESH_REVIEW_UNIMPORTED_LINEAGE_MAX_BYTES,
)

#: Fail-closed sentinel used when the lineage variable part cannot be
#: bounded at all (no contract document reachable; see
#: ``lineage_variable_part``). Charging the full physical limit as the
#: reserve forces ``satisfies_capacity`` to reject rather than silently
#: under-reserve.
_LINEAGE_VARIABLE_PART_UNBOUNDED = STATE_LIMIT

#: Residual reservation by D request projection status *excluding*
#: lineage (design doc "残りの予約"). The lineage addend is computed per
#: record by ``lineage_stage_delta`` because its variable part depends on
#: that request's own criteria. ``withdrawn`` reserves nothing at all.
_FRESH_REVIEW_FIXED_RESERVE_BY_STATUS = {
    "pending": FRESH_REVIEW_DISPATCH_STAGE_DELTA + FRESH_REVIEW_CONSUME_STAGE_DELTA
    + FRESH_REVIEW_TERMINAL_STAGE_DELTA,
    "reserved": FRESH_REVIEW_CONSUME_STAGE_DELTA + FRESH_REVIEW_TERMINAL_STAGE_DELTA,
    "consumed": FRESH_REVIEW_TERMINAL_STAGE_DELTA,
}

#: Baselines kept for backward-compatible direct comparison in tests; a
#: real record's residual also adds its own ``lineage_stage_delta``.
FRESH_REVIEW_PENDING_RESERVE = (
    _FRESH_REVIEW_FIXED_RESERVE_BY_STATUS["pending"] + FRESH_REVIEW_LINEAGE_STAGE_DELTA
)
FRESH_REVIEW_RESERVED_RESERVE = (
    _FRESH_REVIEW_FIXED_RESERVE_BY_STATUS["reserved"] + FRESH_REVIEW_LINEAGE_STAGE_DELTA
)
FRESH_REVIEW_CONSUMED_RESERVE = (
    _FRESH_REVIEW_FIXED_RESERVE_BY_STATUS["consumed"] + FRESH_REVIEW_LINEAGE_STAGE_DELTA
)
FRESH_REVIEW_WITHDRAWN_RESERVE = 0


def _command_snapshot_map(request) -> dict:
    return {binding.command_id: binding.snapshot_digest for binding in request.candidate_bindings}


def _contract_document(document: Mapping) -> Optional[Mapping]:
    if _is_v5(document):
        extensions = document.get("extensions")
        contract = extensions.get("acceptance_contract") if isinstance(extensions, Mapping) else None
    else:
        contract = document.get("acceptance_contract")
    return contract if isinstance(contract, Mapping) else None


def _encode_len_safe(value: object) -> int:
    try:
        return len(encode_json_value(freeze_json_value(value)))
    except Exception:
        return 0


def lineage_variable_part(document: Mapping, request) -> int:
    """The per-criterion variable addend to the lineage Δ for ``request``.

    ``requirement_ids``/``prohibited_side_effects`` of the request's own
    criteria, plus the request's command->snapshot map (built from
    ``candidate_bindings``), maximised across the request's criteria.
    Falls back to the contract's own largest criterion if none of the
    request's ``criterion_ids`` resolve, and to the fail-closed sentinel
    if no contract document can be found at all (the contract is not
    this module's responsibility to validate -- it is only read for a
    conservative upper bound).
    """
    contract = _contract_document(document)
    if contract is None:
        return _LINEAGE_VARIABLE_PART_UNBOUNDED
    criteria = contract.get("criteria")
    if not isinstance(criteria, list):
        return _LINEAGE_VARIABLE_PART_UNBOUNDED
    command_map_bytes = _encode_len_safe(_command_snapshot_map(request))
    by_id = {
        item.get("id"): item
        for item in criteria
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    matched = [by_id[cid] for cid in getattr(request, "criterion_ids", ()) if cid in by_id]
    candidates = matched or [item for item in criteria if isinstance(item, Mapping)]
    if not candidates:
        return command_map_bytes
    best = 0
    for criterion in candidates:
        requirement_ids = criterion.get("requirement_ids")
        prohibited = criterion.get("prohibited_side_effects")
        payload = {
            "requirement_ids": requirement_ids if isinstance(requirement_ids, list) else [],
            "prohibited_side_effects": prohibited if isinstance(prohibited, list) else [],
        }
        size = _encode_len_safe(payload) + command_map_bytes
        best = max(best, size)
    return best


def lineage_stage_delta(document: Mapping, request) -> int:
    """``max(F_MAX * (FIXED_MAX + variable), UNIMPORTED_MAX)`` for one request."""
    variable = lineage_variable_part(document, request)
    per_finding = FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES + variable
    return max(
        FRESH_REVIEW_FINDINGS_LINEAGE_LIMIT * per_finding,
        FRESH_REVIEW_UNIMPORTED_LINEAGE_MAX_BYTES,
    )


def repair_attempt_reserve(_document: Mapping) -> int:
    """E2 (#?) stub. Always zero until the repair-attempt writer exists."""
    return 0


def disposition_reserve(_document: Mapping) -> int:
    """E3 (#?) stub. Always zero until the disposition writer exists."""
    return 0


# ---------------------------------------------------------------------------
# Document layout helpers.
# ---------------------------------------------------------------------------

_HALT_FIELD_NAMES = frozenset(
    {"phase", "terminal_outcome", "loop_active", "halt_reason", "halt_category"}
)
HALT_AUX_KEYS = frozenset(
    {
        "updated_at",
        "last_activity_at",
        "goal_dispatch_effective",
        "goal_dispatch_host",
        "goal_dispatch_fallback_reason",
        "activity_current",
        "activity_segments",
        "activity_rollup",
        "activity_last_event_at",
        "activity_last_event_phase",
        "activity_anomaly_counts",
        "activity_unobserved_gap_sec",
        "activity_unobserved_gap_reasons_sec",
        "reactivation_history",
    }
)
_ENVELOPE_KEYS = frozenset({"updated_at", "last_activity_at"})
#: F's extension point (#881). No StopSlots field exists yet.
STOP_SLOT_KEYS: frozenset[str] = frozenset()


def _is_v5(document: object) -> bool:
    return (
        isinstance(document, Mapping)
        and document.get("schema_version") == 5
        and isinstance(document.get("control"), Mapping)
    )


def _halt_reason_value(document: Mapping) -> object:
    if _is_v5(document):
        control = document.get("control")
        return control.get("halt_reason") if isinstance(control, Mapping) else None
    return document.get("halt_reason")


def halt_slot_written(document: Mapping) -> bool:
    """True once the halt slot carries a real reason (not absent/None/"")."""
    value = _halt_reason_value(document)
    return value not in (None, "")


def _lease_mapping(document: Mapping) -> Mapping:
    if _is_v5(document):
        lease = document.get("lease")
        return lease if isinstance(lease, Mapping) else {}
    return document


def lease_history_length(document: Mapping) -> int:
    history = _lease_mapping(document).get("lease_history")
    return len(history) if isinstance(history, list) else 0


def next_takeover_cost(document: Mapping) -> int:
    """Upper bound of the next single takeover's encode-length increase.

    ``STATE_CAPACITY_TAKEOVER_DELTA`` was already measured as the maximum
    increase across every possible current-lease field length, so no state
    can make a real takeover cost more than this constant. This hook is
    kept distinct from the constant (per the design doc's
    ``next_takeover_cost`` naming) so a future change that lets the current
    lease carry longer legacy fields can override it without touching the
    exhaustion arithmetic.
    """
    return STATE_CAPACITY_TAKEOVER_DELTA


def remaining_takeovers(document: Mapping) -> int:
    recorded = lease_history_length(document)
    return max(0, STATE_CAPACITY_TAKEOVER_LIMIT - recorded)


def takeover_reserve(document: Mapping) -> int:
    remaining = remaining_takeovers(document)
    if remaining <= 0:
        return 0
    return next_takeover_cost(document) + (remaining - 1) * STATE_CAPACITY_TAKEOVER_DELTA


def system_remaining(document: Mapping) -> int:
    """``S_sys_remaining``: halt share (if unwritten) + remaining takeovers."""
    halt_share = 0 if halt_slot_written(document) else STATE_CAPACITY_HALT_DELTA
    return halt_share + takeover_reserve(document)


# ---------------------------------------------------------------------------
# D request projection access.
# ---------------------------------------------------------------------------


def _fresh_review_raw(document: Mapping) -> object:
    if _is_v5(document):
        extensions = document.get("extensions")
        return extensions.get("fresh_review") if isinstance(extensions, Mapping) else None
    return document.get("fresh_review")


def fresh_review_projection(document: Mapping) -> Optional[FreshReviewProjection]:
    """Decode the D request projection out of a v5 or flat document.

    Returns ``None`` if the embedded projection cannot be decoded (callers
    must then treat the residual reservation as unknown, not as zero; see
    ``_residual_reservation`` below, which folds that into a conservative
    "cannot verify" rather than admitting the mutation).
    """
    raw = _fresh_review_raw(document)
    if raw is None:
        return FreshReviewProjection()
    try:
        return _fresh_review.decode_projection({"fresh_review": raw})
    except FreshReviewError:
        return None


def residual_reservation(
    document: Mapping, *, encoding: "StateEncoding" = None
) -> int:
    """``Σ残り予約``: sum of every outstanding item's remaining-stage Δ.

    Under ``StateEncoding.LEGACY_PRETTY`` (a v4 flat on-disk document), D
    requests never advance past ``pending`` -- there is no v4 writer for
    dispatch/consume/terminal -- so they reserve nothing at all (design doc
    "v4 の D item の予約は 0"). The caller's physical encode length already
    includes the pending request's own bytes ("受付の段の Δ...受付時に確定
    している request の実際の encode 長とする").
    """
    if encoding is StateEncoding.LEGACY_PRETTY:
        return 0
    projection = fresh_review_projection(document)
    if projection is None:
        # An undecodable embedded projection cannot be reasoned about; charge
        # the maximum so capacity admission fails closed rather than open.
        return FRESH_REVIEW_PENDING_RESERVE
    total = 0
    for record in projection.requests:
        if isinstance(record, WithdrawnFreshReviewRecord):
            continue
        fixed = _FRESH_REVIEW_FIXED_RESERVE_BY_STATUS.get(
            record.status, _FRESH_REVIEW_FIXED_RESERVE_BY_STATUS["pending"]
        )
        total += fixed + lineage_stage_delta(document, record.request)
    total += repair_attempt_reserve(document) + disposition_reserve(document)
    return total


def pending_withdraw_candidates(document: Mapping) -> tuple[str, ...]:
    projection = fresh_review_projection(document)
    if projection is None:
        return ()
    return tuple(
        record.request.request_id
        for record in projection.requests
        if isinstance(record, FreshReviewRecord) and record.status == "pending"
    )


def satisfies_capacity(
    document: Mapping, encoded_len: int, *, encoding: "StateEncoding" = None
) -> bool:
    """``len(encoded) + Σ残り予約 <= STATE_LIMIT - S_sys_remaining``."""
    return (
        encoded_len + residual_reservation(document, encoding=encoding)
        <= STATE_LIMIT - system_remaining(document)
    )


def is_over_capacity(
    document: Mapping, encoded_len: int, *, encoding: "StateEncoding" = None
) -> bool:
    return not satisfies_capacity(document, encoded_len, encoding=encoding)

