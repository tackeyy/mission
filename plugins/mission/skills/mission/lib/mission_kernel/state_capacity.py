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
import json
from typing import Mapping, Optional

from .identifiers import TOKEN128_RE
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
# #918 obligations: writer-side enforcement this module assumes but does
# not perform itself. Each bound has its own constant/docstring; this is
# the one place listing them together.
#
# 1. Raw, stored halt reason (v5's ``legacy_reason``, mirrored into both
#    ``control.halt_reason`` and ``extensions.halt_reason``; v4's flat
#    ``halt_reason``) <= ``HALT_REASON_MAX_CHARS`` (2048). Today only the
#    semantic, stripped ``reason`` is capped (``_reason``); the raw value
#    can be padded past that cap (``.strip()`` treats C0 control chars
#    U+001C-U+001F as whitespace) while still matching ``reason``.
# 2. Each ``goal_dispatch_*`` field <= ``GOAL_DISPATCH_REASON_MAX_CHARS``
#    (128). Unenforced today.
# 3. A lease's ``owner_session_id``/``lease_id``/history ``reason`` must
#    match ``LEASE_TOKEN_PATTERN``. v5's fenced-commit admission enforces
#    this for *new* tokens; v4's ``acquire_or_verify_lease`` and both
#    decoders (for already-persisted state) do not.
# 4. A lease's ``fencing_epoch`` must be an ``int`` in
#    ``[0, LEASE_EPOCH_MAX]``. v5's decoder only checks ``positive``; v4's
#    writer normalizes via ``int(value)`` (``acquire_or_verify_lease``,
#    around L1287) without capping the result.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# System share: halt + lease-takeover reservation (design doc "検査式").
# ---------------------------------------------------------------------------

#: One sixteenth of the physical state limit, reserved so that a session can
#: always still record a halt and a bounded number of lease takeovers even
#: while D request reservations consume the rest of the budget.
STATE_CAPACITY_SYSTEM_SHARE = STATE_LIMIT // 16

#: Kernel-level upper bound for the *raw, stored* halt reason: v5's
#: ``legacy_reason`` (mirrored into both ``control.halt_reason`` *and*
#: ``extensions.halt_reason``, see ``STATE_CAPACITY_HALT_DELTA``) and v4's
#: flat ``halt_reason``.
#:
#: Not already enforced. ``transitions.py``'s ``_reason`` caps only the
#: *semantic* ``reason`` (post-``.strip()``) at 2048 chars; ``_mark_halt``
#: separately requires ``legacy_reason.strip() == reason``, but Python's
#: ``.strip()`` treats the C0 control characters U+001C-U+001F as
#: whitespace, so ``legacy_reason = "\x1c" * N + reason`` still strips
#: down to ``reason`` for any ``N``. Measured:
#: ``decide(MarkHalt(HaltCategory("other"), "\x01"*2048,
#: legacy_reason="\x1c"*20000+"\x01"*2048))`` is accepted and grows the
#: encoded v5 state by 264,635 bytes -- ~10x ``STATE_CAPACITY_HALT_DELTA``.
#: This constant is measured *assuming* a future writer caps the raw
#: stored reason at this many (control-character-worst-cased) characters;
#: enforcing that for every caller is #918's job (see "#918 obligations").
HALT_REASON_MAX_CHARS = 2048

#: Conservative upper bound for each ``goal_dispatch_*`` string a
#: ``routed-goal`` halt may add (``goal_dispatch_effective`` /
#: ``goal_dispatch_host`` / ``goal_dispatch_fallback_reason``, written by
#: ``bin/mission-state.py``'s ``_goal_dispatch_route_fields``). Like
#: ``HALT_REASON_MAX_CHARS``, unenforced and charset-unrestricted today, so
#: ``STATE_CAPACITY_HALT_DELTA`` worst-cases each with control characters.
GOAL_DISPATCH_REASON_MAX_CHARS = 128

#: ``[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`` -- :mod:`mission_kernel.identifiers`'s
#: ``TOKEN128_RE`` (shared with v5's fenced-commit admission,
#: mission_persistence/fenced_commit.py's ``_token``/``_session_id``) and
#: :mod:`mission_kernel.fresh_review`'s ``_ID``. A lease takeover's
#: identifier fields -- ``owner_session_id``, ``lease_id``, a
#: ``lease_history`` entry's ``reason`` -- are bound by this ASCII
#: pattern, not merely a character count: v4's ``acquire_or_verify_lease``
#: does not restrict a new token's charset, and 128 control characters
#: (6-byte ``\uXXXX`` escapes) encode far larger than 128
#: pattern-conformant ASCII characters (1 byte each) -- measured 1,830
#: bytes vs. the 550-byte pattern-conformant worst case
#: ``STATE_CAPACITY_TAKEOVER_DELTA`` is measured from. Enforcing this for
#: every new token is #918's job; see ``next_takeover_cost`` for how an
#: already-non-conformant persisted lease is handled until then.
LEASE_TOKEN_PATTERN = TOKEN128_RE

#: The pattern's own maximum length (``1 + 127``).
LEASE_TOKEN_MAX_CHARS = 128

#: 0 <= fencing_epoch <= this. v5's decoder only checks ``positive`` (see
#: codec_v5.py's ``_decode_lease``), so a pre-existing state's epoch is not
#: upper-bounded at decode time either; see ``next_takeover_cost``.
LEASE_EPOCH_MAX = 2 ** 63 - 1

#: Maximum encode-length increase of a single halt / mark-halt write.
#: Measured (see test_issue936_state_capacity_reservation.py::
#: test_halt_delta_is_bounded_by_the_real_production_writers) across
#: *every* ``HaltCategory``, taking the max, by driving:
#:
#: - v4: the real ``_transition_phase`` (closes the open activity segment,
#:   accrues ``phase_durations_sec``, sets ``resume_target_phase`` for
#:   ``stale``) plus, for ``awaiting-approval``,
#:   ``activity_segments.record_activity_event`` (opens and lets the
#:   terminal close re-close a bare second segment).
#: - v5: the same before/after v4 documents fed through the real
#:   ``mission_application.compatibility.compatibility_delta`` (what
#:   ``lifecycle.mark_halt`` itself uses) into
#:   ``mission_kernel.transitions.decide`` with a real ``MarkHalt``
#:   command (``legacy_reason``/``at`` set), encoded via
#:   ``encode_v5_snapshot``.
#:
#: ``legacy_reason`` and each ``goal_dispatch_*`` field are worst-cased at
#: their character bound (``HALT_REASON_MAX_CHARS`` /
#: ``GOAL_DISPATCH_REASON_MAX_CHARS``) filled with the control character
#: ``"\x01"`` (6 bytes as ``\u0001`` under both encoders, vs. 1 for ASCII).
#:
#: v5 dominates v4: ``_mark_halt`` mirrors ``legacy_reason`` into both
#: ``control.halt_reason`` and (via ``_apply_compatibility``'s
#: ``dedicated_upserts``, transitions.py L597-605) ``extensions.halt_reason``,
#: paying for ``HALT_REASON_MAX_CHARS`` of control characters twice.
#: ``routed-goal`` dominates every other category (activity close plus the
#: three ``goal_dispatch_*`` fields).
#:
#: Measured maxima across all 9 categories: v5 canonical 28,118 bytes
#: (``routed-goal``), v4 legacy-pretty 15,049 bytes (``routed-goal``). The
#: literal below adds a documented 500-byte slack for fields this
#: measurement may not bundle (a future ``HaltCategory`` branch, a
#: compatibility field, or #918's own validation-rejection paths).
STATE_CAPACITY_HALT_DELTA = 28618

#: Maximum encode-length increase of a single lease takeover: one
#: ``lease_history`` entry at maximum field lengths, from a *current*
#: lease already at maximum, ``LEASE_TOKEN_PATTERN``-conformant field
#: lengths (``owner_session_id``/``lease_id``/history ``reason`` at
#: ``LEASE_TOKEN_MAX_CHARS``, ``fencing_epoch`` at ``LEASE_EPOCH_MAX``) to
#: a *new* lease also at maximum, pattern-conformant lengths. Measured by
#: driving v4's real ``acquire_or_verify_lease`` and v5's field values
#: reshaped under the ``"lease"`` wrapper key (``admit_lease``'s
#: "taken-over" branch writes identical field names/values; the wrapper
#: key does not change the delta since it exists in both base and
#: proposed) on an expired base lease with a foreign presented token.
#:
#: Pattern conformance matters, not just length: every
#: ``LEASE_TOKEN_PATTERN`` character is single-byte ASCII, so this worst
#: case costs the same in canonical or legacy-pretty encoding (550 bytes
#: legacy-pretty / 497 canonical). A *non*-conformant new token (128
#: control characters, which nothing stops ``acquire_or_verify_lease``
#: from accepting today) costs far more -- measured 1,830 bytes -- which
#: is why the pattern, not a bare character count, is what #918 must
#: enforce (see ``next_takeover_cost`` for the already-persisted case).
#:
#: The literal adds a documented 50-byte slack (measured 550 + slack) for
#: ``lease_history`` entry fields this measurement may not bundle (its
#: schema is not fixed by any design table).
STATE_CAPACITY_TAKEOVER_DELTA = 600

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


#: New owner/lease_id a simulated takeover assigns: the pattern's own
#: maximum length (the bound #918 must enforce for *new* tokens -- see
#: ``LEASE_TOKEN_PATTERN``).
_TAKEOVER_SIM_TOKEN = "9" * LEASE_TOKEN_MAX_CHARS
_TAKEOVER_SIM_AT = "9999-12-31T23:59:59Z"


def next_takeover_cost(document: Mapping) -> int:
    """Upper bound of the next single takeover's encode-length increase.

    Simulates ``bin/mission-state.py``'s ``acquire_or_verify_lease``
    "taken-over" branch itself (``str(owner)``/``str(lease_id)``/
    ``int(epoch)`` normalized into a new ``lease_history`` entry; new
    current fields at maximal pattern-conformant identifiers and
    ``epoch + 1``) and measures the real encode-length increase, rather
    than approximating per field: a per-field approximation undercounts
    an oversized epoch, which costs bytes *both* in the new current field
    and in the history entry the old value is copied into (see
    test_issue936_state_capacity_reservation.py::
    test_next_takeover_cost_bounds_the_real_writer, which reproduces the
    Codex-found undercounts).

    A lease that was never acquired (``fencing_epoch`` absent/empty, the
    normal shape of a freshly ``init``ed session) has no takeover to
    simulate, so this returns the bare constant. A present but
    unparseable epoch, or any value this cannot encode at all, fails the
    whole result closed to ``STATE_LIMIT`` -- matching the real writer's
    own rejection of that write.
    """
    lease = _lease_mapping(document)
    before = {
        "owner_session_id": lease.get("owner_session_id"), "lease_id": lease.get("lease_id"),
        "fencing_epoch": lease.get("fencing_epoch"), "lease_expires_at": lease.get("lease_expires_at"),
        "lease_history": lease.get("lease_history") if isinstance(lease.get("lease_history"), list) else [],
    }
    if before["fencing_epoch"] in (None, ""):
        return STATE_CAPACITY_TAKEOVER_DELTA
    try:
        owner, lease_id = str(before["owner_session_id"]), str(before["lease_id"])
        epoch = int(before["fencing_epoch"])
        before_c = encode_json_value(freeze_json_value(before))
        before_l = json.dumps(before, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
        after = dict(before, owner_session_id=_TAKEOVER_SIM_TOKEN, lease_id=_TAKEOVER_SIM_TOKEN,
                     fencing_epoch=epoch + 1)
        after["lease_history"] = before["lease_history"] + [{
            "owner_session_id": owner, "lease_id": lease_id, "fencing_epoch": epoch,
            "reason": _TAKEOVER_SIM_TOKEN, "at": _TAKEOVER_SIM_AT,
        }]
        after_c = encode_json_value(freeze_json_value(after))
        after_l = json.dumps(after, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except Exception:
        return STATE_LIMIT
    increase = max(len(after_c) - len(before_c), len(after_l) - len(before_l))
    return max(STATE_CAPACITY_TAKEOVER_DELTA, increase)


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

