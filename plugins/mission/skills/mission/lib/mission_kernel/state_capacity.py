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
kernel dependency this module itself imports is :mod:`mission_kernel.json_codec`
and :mod:`mission_kernel.fresh_review`. It deliberately does *not* import
:mod:`mission_kernel.fresh_review_receipts`: the D terminal shapes below are
duplicated as closed literals so this module stays import-light and so the
Delta constants stay pinned to *this* module's own measurement, independent
of any future change to the receipts module's shapes. (Only the test suite
imports ``fresh_review_receipts``, to cross-check those literals against its
``FRESH_REVIEW_MAX_ENCODED_BYTES``.)
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
# Shared state encoding (also used by the verdict half, #933).
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

#: Kernel-level upper bound for a halt's ``reason``/``halt_reason`` value.
#: ``transitions.py``'s ``_reason`` validator already enforces this for v5
#: ``MarkHalt`` (``len(value) > 2048`` is rejected), so this name documents
#: an existing invariant rather than a new one. Writer-side enforcement for
#: every *other* field this module bounds (``goal_dispatch_*``, lease
#: tokens) is #918's job -- see the module docstring.
HALT_REASON_MAX_CHARS = 2048

#: Conservative upper bound for each ``goal_dispatch_*`` string a
#: ``routed-goal`` halt may add (``goal_dispatch_effective`` /
#: ``goal_dispatch_host`` / ``goal_dispatch_fallback_reason``, written by
#: ``bin/mission-state.py``'s ``_goal_dispatch_route_fields``). No writer
#: enforces this today; #918 must add it before this bound is load-bearing.
GOAL_DISPATCH_REASON_MAX_CHARS = 128

#: Conservative upper bound for a lease's ``owner_session_id``/``lease_id``
#: and a ``lease_history`` entry's ``reason``. v5's fenced-commit admission
#: already enforces this for *new* tokens via ``Token128``/``_session_id``
#: (mission_persistence/fenced_commit.py), but neither v4's
#: ``acquire_or_verify_lease`` nor either schema's *decoder* enforce it for
#: already-persisted state, so a takeover that copies an oversized current
#: lease into history can exceed what ``STATE_CAPACITY_TAKEOVER_DELTA``
#: assumed -- see ``next_takeover_cost``. Writer-side enforcement of new
#: tokens for every caller is #918's job.
LEASE_TOKEN_MAX_CHARS = 128

#: 0 <= fencing_epoch <= this. v5's decoder only checks ``positive`` (see
#: codec_v5.py's ``_decode_lease``), so a pre-existing state's epoch is not
#: upper-bounded at decode time either; see ``next_takeover_cost``.
LEASE_EPOCH_MAX = 2 ** 63 - 1

#: Maximum encode-length increase of a single halt / mark-halt write.
#: Measured (see test_issue936_state_capacity_reservation.py::
#: test_halt_delta_is_bounded_by_the_real_production_writers) by driving the
#: *real* production writers -- v4's ``activity_segments.*`` helpers plus
#: the flat-document ``mutate()`` shape used by
#: ``mission_application.lifecycle.mark_halt``, and v5's
#: ``mission_kernel.transitions.decide`` with a ``MarkHalt`` command whose
#: ``compatibility`` payload carries the same activity-close fields -- from
#: a base state with an already-open, maximum-shape activity segment to a
#: halted state, across every ``HaltCategory`` branch.
#:
#: v5 dominates v4 here and is the reason this constant roughly doubled
#: from the v4-only measurement this module shipped with originally:
#: ``_mark_halt`` (transitions.py) writes ``legacy_reason`` into
#: ``control.halt_reason`` *and* ``_apply_compatibility``'s
#: ``dedicated_upserts`` (transitions.py L597-605) mirrors the same value
#: into ``extensions.halt_reason``, so a v5 halt pays for
#: ``HALT_REASON_MAX_CHARS`` *twice* (once per storage location) where v4's
#: single flat document pays for it once. The dominant branch is
#: ``routed-goal`` (one activity-segment close from the unconditional
#: terminal-phase close in ``_transition_phase``/``close_activity_for_terminal``,
#: *plus* the three ``goal_dispatch_*`` fields); ``awaiting-approval``'s
#: extra activity-segment close (``record_activity_event`` opens and
#: immediately re-closes a bare segment) is smaller because that second
#: segment carries no ``detail``.
#:
#: Measured maxima: v5 canonical 26,156 bytes, v4 legacy-pretty 13,120
#: bytes. The literal below adds a documented 500-byte slack for fields
#: this measurement may not have bundled (e.g. any future ``HaltCategory``
#: branch or compatibility field).
STATE_CAPACITY_HALT_DELTA = 26656

#: Maximum encode-length increase of a single lease takeover: one
#: ``lease_history`` entry at its maximum field lengths, from a *current*
#: lease that is already at its maximum field lengths (``owner_session_id``/
#: ``lease_id`` at ``LEASE_TOKEN_MAX_CHARS``, ``fencing_epoch`` at
#: ``LEASE_EPOCH_MAX``) to a *new* lease also at maximum field lengths.
#: Measured by driving the real production functions -- v4's
#: ``bin/mission-state.py``'s ``acquire_or_verify_lease`` and v5's
#: ``mission_persistence.fenced_commit.admit_lease`` (same field names/
#: values; only the v5 ``"lease"`` wrapper key differs, which does not
#: change the delta since that key already exists in both the base and
#: proposed document) -- on an expired base lease with a foreign presented
#: token. Measured maxima: 550 bytes (legacy-pretty) / 497 bytes
#: (canonical); this constant keeps the pre-existing 822-byte literal,
#: which already safely bounds the measured maxima with headroom for the
#: ``lease_history``'s ``reason`` field assuming the conservative
#: ``LEASE_TOKEN_MAX_CHARS``-length bound rather than the single short
#: constant (``"lease-expired-takeover"``) every current real caller
#: actually passes.
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

#: lineage introduction: E1's ``FindingLineage`` type does not exist yet
#: (docs/design/880-repair-lineage.md §2's type table, "FindingLineage"
#: row). This pins a closed placeholder shape with one representative field
#: per *named* sub-type the design table lists -- ``lineage_id``
#: (digest-shaped, 71 chars), ``criterion_id`` (_ID, 128 chars),
#: ``severity``/``lifecycle`` (closed enums), a ``FreshFindingRef``
#: (mission/session/original-request/local-finding ids plus
#: terminal/output digests), an original terminal receipt ref and an
#: original replay evidence ref (each a ``ContentAddressedRef``, or that
#: ref's theoretical-absent variant if larger -- see §2's "original replay
#: ... 理由付き absent variant"), a ``ReproBinding`` (command id plus four
#: digests), a ``CandidateBinding`` (four digests plus an iteration int),
#: and empty ``observations``/``attempts`` lists (E0 introduces lineage
#: with zero of either -- §9's E1 row: "導入時に置く件数...書かれていなければ
#: 0件"). ``requirement_ids``/``prohibited_side_effect_ids`` and the
#: command->snapshot map are the *variable* part (``lineage_variable_part``)
#: and are excluded here. E2/E3's own disposition/duplicate-of cost is
#: also excluded (stubbed to zero below; must not be folded into this
#: constant without revisiting it).
#:
#: The design table does not fix exact field names for the nested
#: sub-types yet (that is E1's job); this shape is this module's own
#: conservative over-approximation, not a contract E1 must match
#: byte-for-byte. Because it over-approximates (more fields, at longer
#: bounds, than the eventual real type is likely to need), the resulting
#: constant is safe to be *larger* than the real type's eventual fixed
#: part, never smaller. Measured standalone (not inside an array) at its
#: maximum field lengths (see test_issue936_state_capacity_reservation.py::
#: test_lineage_fixed_part_matches_the_design_table_shape); the ``+16`` is
#: a per-array-element separator/indentation allowance (the same
#: "+separator" pattern the design doc applies to the finding ref shape:
#: "最大形で 238 bytes。区切りを含め 239 bytes").
FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES = 2890 + 16

#: Single "unimported-findings" lineage record's maximum encode length
#: (standalone; it is never placed in a per-finding array). The design
#: doc does not give this variant its own field table (§2 "D 終端の
#: findings 上限" only names its state, "open | deferred | rejected" --
#: §3 "取込み上限超過の終端の disposition"); this conservatively reuses
#: ``FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES``'s own shape as an
#: upper bound rather than inventing a second, narrower one, since
#: ``lineage_stage_delta`` takes ``max(F_MAX * per_finding, this)`` and
#: ``F_MAX * per_finding`` already dominates by roughly 61x -- this value
#: is not the binding term in practice, so under-measuring it here would
#: not be noticed by that formula. A real single-finding record (without
#: F_MAX replication) is very unlikely to exceed one lineage record's own
#: fixed part, so reusing it is a safe, if generous, bound.
FRESH_REVIEW_UNIMPORTED_LINEAGE_MAX_BYTES = 2890 + 16

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
    """Encode-length of ``value``, or the fail-closed sentinel on error.

    Returning 0 on error would silently *under*-reserve (the caller sums
    this into a variable-part estimate it then trusts as an upper bound).
    Returning ``STATE_LIMIT`` instead means an encode failure can only ever
    push ``satisfies_capacity`` toward rejecting, never toward admitting a
    mutation this function could not actually measure.
    """
    try:
        return len(encode_json_value(freeze_json_value(value)))
    except Exception:
        return STATE_LIMIT


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


def _lease_token_excess(value: object) -> int:
    # Clamped per-field: a short lease_id must not cancel out a long
    # owner_session_id when the two are summed below.
    return max(0, len(value) - LEASE_TOKEN_MAX_CHARS) if isinstance(value, str) else 0


def next_takeover_cost(document: Mapping) -> int:
    """Upper bound of the next single takeover's encode-length increase.

    ``STATE_CAPACITY_TAKEOVER_DELTA`` was measured assuming the *current*
    lease's ``owner_session_id``/``lease_id`` are at most
    ``LEASE_TOKEN_MAX_CHARS`` long and its ``fencing_epoch`` is at most
    ``LEASE_EPOCH_MAX``. v5's decoder only checks that ``fencing_epoch`` is
    positive (codec_v5.py's ``_decode_lease``), and neither it nor v4's flat
    decode caps ``owner_session_id``/``lease_id`` length, so a state
    persisted before those caps are writer-enforced (#918) can already hold
    a current lease that exceeds them. A takeover copies the *current*
    lease's fields into a new ``lease_history`` entry, so such a state's
    next real takeover costs more than the constant. This conservatively
    adds the measured excess (1 byte per excess ASCII character; non-ASCII
    excess could cost more per character due to JSON escaping, which this
    approximation does not model) so capacity admission still fails closed
    rather than silently under-reserving.
    """
    lease = _lease_mapping(document)
    excess = _lease_token_excess(lease.get("owner_session_id")) + _lease_token_excess(
        lease.get("lease_id")
    )
    epoch = lease.get("fencing_epoch")
    if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch > LEASE_EPOCH_MAX:
        excess += len(str(epoch)) - len(str(LEASE_EPOCH_MAX))
    return STATE_CAPACITY_TAKEOVER_DELTA + max(0, excess)


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
    requests reserve nothing at all. This is *not* because the design doc's
    general reservation formula says so -- it is #918's own decision (issue
    body, "決めたこと" / "分割" sections,
    https://github.com/tackeyy/mission/issues/918): v4 is saved as
    indent=2-formatted JSON and a consume write's increase has no fixed
    upper bound there, so #918 rejects every D-stage advance (dispatch
    onward) for v4 and therefore its D items never reserve past the
    pending request's own already-counted bytes ("v4 の D item の予約は
    0"). The caller's physical encode length already includes the pending
    request's own bytes ("受付の段の Δ...受付時に確定している request の
    実際の encode 長とする").
    """
    if encoding is StateEncoding.LEGACY_PRETTY:
        return 0
    projection = fresh_review_projection(document)
    if projection is None:
        # An undecodable embedded projection cannot be reasoned about at
        # all (not even "how many pending requests"), so charge the full
        # physical limit -- not one pending request's reserve -- so
        # capacity admission fails closed rather than under-reserving.
        return STATE_LIMIT
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

