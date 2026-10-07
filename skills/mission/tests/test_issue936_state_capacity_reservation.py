"""E0b-2a (#936): capacity-reservation kernel Delta constants and derivation.

Covers only the reservation half of the capacity scheme (constants, the
lineage variable part, the halt-slot and lease-takeover system share, and
the two boolean capacity predicates). The verdict half (``write_kind``,
legacy-full detection, ``state_capacity_verdict`` itself, and the
PROBE_TABLE regression suite) is tested in the follow-up E0b-2b module.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mission_kernel.json_codec import encode_json_value, freeze_json_value
from mission_kernel import state_capacity as sc

from .test_issue895_fresh_review import _pure_projection

_HERE = Path(__file__).resolve().parent


TS27 = "9999-12-31T23:59:59.999999Z"
ID128 = "x" * 128
HALT_REASON_MAX = "\x01" * 2048
GOAL_FIELD_MAX = "g" * 128
DETAIL_MAX = "あ" * 160
CLOSED_SEGMENT = {
    "kind": "subagent-wait", "reason": "implementation-provider",
    "started_at": TS27, "ended_at": TS27, "duration_sec": 123456789.123456,
    "phase": "reviewing", "detail": DETAIL_MAX,
}


def canonical(document):
    return len(encode_json_value(freeze_json_value(document)))

def legacy(document):
    return len(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8"))

def encode(document, encoding):
    return canonical(document) if encoding is sc.StateEncoding.CANONICAL else legacy(document)

def _flat_halt_base(*, halt_reason_absent):
    doc = {
        "schema_version": 4, "phase": "executing", "loop_active": True,
        "activity_segments": [],
        "activity_rollup": {
            "observed_total_sec": 0.0, "closed_segment_count": 0,
            "activity_duration_totals_sec": {}, "phase_activity_duration_totals_sec": {},
            "wait_reason_totals_sec": {},
        },
        "updated_at": TS27, "last_activity_at": TS27,
        "fresh_review": {"schema": "mission-fresh-review/1", "requests": []},
    }
    if not halt_reason_absent:
        doc["halt_reason"] = ""
    return doc

def _flat_halt_after(base):
    doc = dict(base)
    doc.update({
        "terminal_outcome": "completed_evidence", "halt_category": "routed-goal",
        "halt_reason": HALT_REASON_MAX, "loop_active": False, "phase": "halted",
        "updated_at": TS27, "last_activity_at": TS27,
        "goal_dispatch_effective": GOAL_FIELD_MAX, "goal_dispatch_host": GOAL_FIELD_MAX,
        "goal_dispatch_fallback_reason": GOAL_FIELD_MAX,
    })
    doc["activity_segments"] = list(base.get("activity_segments", [])) + [CLOSED_SEGMENT]
    rollup = dict(base["activity_rollup"])
    rollup["closed_segment_count"] += 1
    rollup["observed_total_sec"] += CLOSED_SEGMENT["duration_sec"]
    rollup["activity_duration_totals_sec"] = dict(
        rollup["activity_duration_totals_sec"], **{CLOSED_SEGMENT["kind"]: CLOSED_SEGMENT["duration_sec"]})
    rollup["phase_activity_duration_totals_sec"] = dict(
        rollup["phase_activity_duration_totals_sec"], **{CLOSED_SEGMENT["phase"]: CLOSED_SEGMENT["duration_sec"]})
    rollup["wait_reason_totals_sec"] = dict(
        rollup["wait_reason_totals_sec"], **{CLOSED_SEGMENT["reason"]: CLOSED_SEGMENT["duration_sec"]})
    doc["activity_rollup"] = rollup
    return doc


def _minimal_contract(*, criterion_ids=("AC1",), requirement_ids=(), prohibited_side_effects=()):
    """A contract document with just enough for ``lineage_variable_part`` to
    resolve each criterion_id without falling back to the fail-closed
    sentinel. The kernel never validates digests/schema shape -- it only
    reads ``criteria``/``requirement_ids``/``prohibited_side_effects`` -- so
    this fixture omits everything acceptance_contract.py's own validator
    would otherwise require.
    """
    return {
        "schema": "mission-acceptance-contract/2",
        "criteria": [
            {"id": cid, "requirement_ids": list(requirement_ids),
             "prohibited_side_effects": list(prohibited_side_effects)}
            for cid in criterion_ids
        ],
    }

def _flat_doc(*, requests=(), halt_reason="", lease_history=(), extra=None, contract=None):
    doc = {
        "schema_version": 4, "phase": "executing", "loop_active": True,
        "halt_reason": halt_reason,
        "owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 1,
        "lease_expires_at": "9999-12-31T23:59:59Z", "lease_history": list(lease_history),
        "updated_at": TS27, "last_activity_at": TS27,
        "fresh_review": {"schema": "mission-fresh-review/1", "requests": list(requests)},
        "acceptance_contract": contract if contract is not None else _minimal_contract(),
    }
    if extra:
        doc.update(extra)
    return doc

def _pad_document(*, status_records=(), padding_key="padding", contract=None):
    """A flat document with ``status_records`` requests, padded so its
    canonical headroom is exactly zero under ``satisfies_capacity``.
    """
    doc = _flat_doc(requests=status_records, contract=contract)
    deficit = sc.STATE_LIMIT - sc.system_remaining(doc) - sc.residual_reservation(doc) - canonical(doc)
    assert deficit >= 1, deficit
    doc[padding_key] = "p" * (deficit - len(('"' + padding_key + '":"","",').encode()))
    # Trim/grow until exact (string encoding adds 2 quote bytes; the key
    # itself is already counted once the first assignment adds it).
    while True:
        current = canonical(doc)
        want = sc.STATE_LIMIT - sc.system_remaining(doc) - sc.residual_reservation(doc)
        if current == want:
            break
        diff = want - current
        doc[padding_key] = doc[padding_key] + ("p" * diff if diff > 0 else "")
        if diff < 0:
            doc[padding_key] = doc[padding_key][:diff]
    return doc

def _pending_record():
    return _pure_projection().requests[0]


@pytest.mark.parametrize("halt_reason_absent", [True, False])
@pytest.mark.parametrize("encoding_fn", [canonical, legacy], ids=["canonical", "legacy"])
def test_halt_delta_pins_maximum_from_absent_and_empty_slot(halt_reason_absent, encoding_fn):
    """A hand-built v4 flat fixture stays comfortably under the pinned Δ.

    This is *not* the measurement that pins ``STATE_CAPACITY_HALT_DELTA``
    (see ``test_halt_delta_is_bounded_by_the_real_production_writers``
    below, which drives the real v4 and v5 production writers and is the
    actual upper-bound proof); it only exercises this one hand-built v4
    shape as an extra regression fixture.
    """
    base = _flat_halt_base(halt_reason_absent=halt_reason_absent)
    after = _flat_halt_after(base)
    delta = encoding_fn(after) - encoding_fn(base)
    assert delta <= sc.STATE_CAPACITY_HALT_DELTA


#: Worst-byte-per-character value for the *free text* fields
#: ``HALT_REASON_MAX_CHARS``/``GOAL_DISPATCH_REASON_MAX_CHARS`` bound by
#: character count only: a C0 control character, 6 bytes as ``\u00XX``
#: under both the canonical and legacy-pretty encoders.
_HALT_REASON_WORST = "\x01" * sc.HALT_REASON_MAX_CHARS
_GOAL_DISPATCH_WORST = "\x01" * sc.GOAL_DISPATCH_REASON_MAX_CHARS

#: Declared slack folded into ``STATE_CAPACITY_HALT_DELTA`` on top of the
#: measured real-writer maximum (see ``test_halt_delta_is_bounded_by_the_
#: real_production_writers``), for fields this measurement may not bundle.
_HALT_DELTA_SLACK = 500


def _flat_halt_before_after(category):
    """Build before/after v4 flat documents by driving the *real*
    ``_transition_phase`` (phase close, activity close, ``resume_target_phase``
    for ``stale``) and, for ``awaiting-approval``, the real
    ``record_activity_event`` -- the same functions
    ``mission_application.lifecycle.mark_halt``'s ``mutate()`` closure
    calls. ``legacy_reason``/``goal_dispatch_*`` are worst-cased with
    control characters (see module docstring on why that is the correct
    assumption, not an exact reproduction of any single real caller).
    """
    import importlib.util

    from activity_segments import record_activity_event, start_activity_segment

    mission_state_py = str(_HERE.parent / "bin" / "mission-state.py")
    spec = importlib.util.spec_from_file_location(
        "mission_state_issue936_halt_" + category.replace("-", "_"), mission_state_py
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    before = {
        "schema_version": 4, "phase": "executing", "loop_active": True,
        "halt_reason": "", "phase_started_at": TS27, "updated_at": TS27,
    }
    start_activity_segment(before, CLOSED_SEGMENT["kind"], CLOSED_SEGMENT["reason"], TS27,
                            detail=CLOSED_SEGMENT["detail"])
    import copy
    after = copy.deepcopy(before)
    if category == "awaiting-approval":
        record_activity_event(after, "awaiting-approval", TS27)
    mod._transition_phase(after, "halted", TS27, terminal_trusted_boundary=(category == "stale"))
    after["halt_reason"] = _HALT_REASON_WORST
    after["halt_category"] = category
    after["loop_active"] = False
    after["updated_at"] = TS27
    if category == "routed-goal":
        after["goal_dispatch_effective"] = _GOAL_DISPATCH_WORST
        after["goal_dispatch_host"] = _GOAL_DISPATCH_WORST
        after["goal_dispatch_fallback_reason"] = _GOAL_DISPATCH_WORST
    return before, after


def test_halt_delta_is_bounded_by_the_real_production_writers():
    """Δ_halt must equal the measured real-writer maximum plus the
    declared slack, across *every* ``HaltCategory`` value -- not merely
    bound it, and not merely the two branches a hand-picked pair of
    categories happens to touch.

    v4: the real ``_transition_phase``/``record_activity_event`` pipeline
    (``_flat_halt_before_after``).

    v5: the exact same before/after v4 documents, fed through the real
    ``mission_application.compatibility.compatibility_delta`` (what
    ``mission_application.lifecycle.mark_halt`` itself uses to derive the
    kernel's ``compatibility`` payload) and then
    ``mission_kernel.transitions.decide`` with a real ``MarkHalt`` command
    (``legacy_reason``/``at`` set, matching production), encoded end-to-end
    with ``encode_v5_snapshot``.
    """
    import dataclasses

    from mission_application.compatibility import compatibility_delta
    from mission_kernel import decode_snapshot, encode_v5_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    v4_max = 0
    v5_max = 0
    for category in [c.value for c in HaltCategory]:
        before, after = _flat_halt_before_after(category)
        v4_max = max(v4_max, legacy(after) - legacy(before), canonical(after) - canonical(before))

        payload = current_v5_open_state()
        payload["control"]["halt_reason"] = ""
        payload["control"]["loop_active"] = True
        payload["control"]["phase"] = "executing"
        payload["control"]["terminal_outcome"] = None
        snap = decode_snapshot(canonical_json_bytes(payload))
        base_bytes = encode_v5_snapshot(snap)
        compat = compatibility_delta(before, after, exclude={
            "phase", "loop_active", "halt_reason", "halt_category", "terminal_outcome", "updated_at",
        })
        command = MarkHalt(HaltCategory(category), _HALT_REASON_WORST,
                            legacy_reason=_HALT_REASON_WORST, compatibility=compat, at=TS27)
        decision = decide(snap.state, command)
        assert decision.accepted, (category, decision.rejection)
        new_state = dataclasses.replace(decision.transition.new_state, snapshot_provenance=snap.provenance)
        object.__setattr__(new_state, "_snapshot_binding", snap.guidance._snapshot_binding)
        new_snap = dataclasses.replace(snap, state=new_state)
        after_bytes = encode_v5_snapshot(new_snap)
        v5_max = max(v5_max, len(after_bytes) - len(base_bytes))

    measured_max = max(v4_max, v5_max)
    assert measured_max + _HALT_DELTA_SLACK == sc.STATE_CAPACITY_HALT_DELTA
    # v5 (double-storage of the raw reason) must stay the dominant branch;
    # this would fail if a future change made v4 dominate without the
    # constant's own derivation being revisited.
    assert v5_max > v4_max

#: Declared slack folded into ``STATE_CAPACITY_TAKEOVER_DELTA`` on top of
#: the measured real-writer maximum, for ``lease_history`` entry fields
#: this measurement may not bundle (its schema is not fixed anywhere).
_TAKEOVER_DELTA_SLACK = 50


def _mission_state_module():
    import importlib.util

    mission_state_py = str(_HERE.parent / "bin" / "mission-state.py")
    spec = importlib.util.spec_from_file_location("mission_state_issue936_takeover", mission_state_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_takeover_delta_is_bounded_by_the_real_production_writers():
    """Δ_takeover must equal the measured real-writer maximum plus the
    declared slack, for the *pattern-conformant* worst case (the bound
    #918 must enforce -- see ``LEASE_TOKEN_PATTERN``).

    v4: drives ``bin/mission-state.py``'s real ``acquire_or_verify_lease``.
    v5: ``mission_persistence.fenced_commit.admit_lease``'s "taken-over"
    branch writes the exact same field names/values (only the v5
    ``"lease"`` wrapper key differs, which does not change the delta since
    that key is present in both the base and proposed document); this
    reuses the v4-measured field values reshaped under that wrapper key
    rather than re-deriving them, since constructing a full
    ``ExecutionRequest`` is out of scope for this module's own test.
    """
    import copy

    mod = _mission_state_module()

    old_lease = {
        "owner_session_id": ID128, "lease_id": "b" * 128, "fencing_epoch": 2 ** 63 - 2,
        "lease_expires_at": "2000-01-01T00:00:00Z", "lease_history": [],
    }
    before = copy.deepcopy(old_lease)
    after = copy.deepcopy(old_lease)
    mod.acquire_or_verify_lease(after, "c" * 128, lease_id="c" * 128, reason="r" * 128)
    assert sc.LEASE_TOKEN_PATTERN.fullmatch(after["owner_session_id"])
    assert sc.LEASE_TOKEN_PATTERN.fullmatch(after["lease_id"])

    d_legacy = legacy(after) - legacy(before)
    d_canonical = canonical(after) - canonical(before)

    v5_before = {"lease": {"kind": "fenced", **old_lease}}
    v5_after = {"lease": {"kind": "fenced", **after}}
    d_v5_canonical = canonical(v5_after) - canonical(v5_before)

    measured_max = max(d_legacy, d_canonical, d_v5_canonical)
    assert measured_max + _TAKEOVER_DELTA_SLACK == sc.STATE_CAPACITY_TAKEOVER_DELTA


def test_a_non_conformant_new_token_exceeds_the_takeover_delta():
    """Documents the #918 gap the ``LEASE_TOKEN_PATTERN`` docstring cites:
    nothing stops ``acquire_or_verify_lease`` from accepting a new owner/
    lease ID made of control characters today, and doing so exceeds
    ``STATE_CAPACITY_TAKEOVER_DELTA`` (which is only measured assuming
    future pattern enforcement). This is an accepted, documented gap, not
    a regression this module can close on its own -- see "#918
    obligations" in state_capacity.py.
    """
    import copy

    mod = _mission_state_module()
    old_lease = {
        "owner_session_id": ID128, "lease_id": "b" * 128, "fencing_epoch": 2 ** 63 - 2,
        "lease_expires_at": "2000-01-01T00:00:00Z", "lease_history": [],
    }
    before = copy.deepcopy(old_lease)
    after = copy.deepcopy(old_lease)
    control_token = "\x01" * 128
    mod.acquire_or_verify_lease(after, control_token, lease_id=control_token, reason="r" * 128)
    assert not sc.LEASE_TOKEN_PATTERN.fullmatch(after["owner_session_id"])
    measured = max(legacy(after) - legacy(before), canonical(after) - canonical(before))
    assert measured > sc.STATE_CAPACITY_TAKEOVER_DELTA


def test_next_takeover_cost_charges_the_excess_of_an_oversized_current_lease():
    within_bound = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a" * sc.LEASE_TOKEN_MAX_CHARS,
        "lease_id": "b" * sc.LEASE_TOKEN_MAX_CHARS,
        "fencing_epoch": sc.LEASE_EPOCH_MAX,
    })
    assert sc.next_takeover_cost(within_bound) == sc.STATE_CAPACITY_TAKEOVER_DELTA

    oversized_owner = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a" * (sc.LEASE_TOKEN_MAX_CHARS + 37),
        "lease_id": "b",
        "fencing_epoch": 1,
    })
    assert sc.next_takeover_cost(oversized_owner) == sc.STATE_CAPACITY_TAKEOVER_DELTA + 37

    oversized_epoch = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a", "lease_id": "b",
        "fencing_epoch": sc.LEASE_EPOCH_MAX * 1000,
    })
    excess_digits = len(str(sc.LEASE_EPOCH_MAX * 1000)) - len(str(sc.LEASE_EPOCH_MAX))
    assert sc.next_takeover_cost(oversized_epoch) == sc.STATE_CAPACITY_TAKEOVER_DELTA + excess_digits

    # Encoded bytes, not characters: a control character costs 6 bytes and a
    # 3-byte UTF-8 character 3, even within the character cap.
    for token, cost in (("\x01" * sc.LEASE_TOKEN_MAX_CHARS, 5 * sc.LEASE_TOKEN_MAX_CHARS),
                        ("\u3042" * sc.LEASE_TOKEN_MAX_CHARS, 2 * sc.LEASE_TOKEN_MAX_CHARS)):
        escaped = _flat_doc(lease_history=[], extra={
            "owner_session_id": token, "lease_id": "b", "fencing_epoch": 1})
        assert sc.next_takeover_cost(escaped) == sc.STATE_CAPACITY_TAKEOVER_DELTA + cost

    # A non-string token is still copied into history; one that is not JSON
    # cannot be bounded and charges the whole limit.
    listed = _flat_doc(lease_history=[], extra={
        "owner_session_id": ["x"] * 200, "lease_id": "b", "fencing_epoch": 1})
    assert sc.next_takeover_cost(listed) > sc.STATE_CAPACITY_TAKEOVER_DELTA
    unbounded = _flat_doc(lease_history=[], extra={
        "owner_session_id": float("nan"), "lease_id": "b", "fencing_epoch": 1})
    assert sc.next_takeover_cost(unbounded) >= sc.STATE_LIMIT


def test_next_takeover_cost_charges_lease_id_excess_independently_of_owner():
    """Isolates ``lease_id`` from ``owner_session_id``, so a mutation that
    drops the ``lease_id`` term from ``next_takeover_cost`` (while still
    charging the owner's excess) cannot hide behind a combined assertion."""
    oversized_lease_id = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a", "lease_id": "b" * (sc.LEASE_TOKEN_MAX_CHARS + 41),
        "fencing_epoch": 1,
    })
    assert sc.next_takeover_cost(oversized_lease_id) == sc.STATE_CAPACITY_TAKEOVER_DELTA + 41


def test_takeover_reserve_uses_next_takeover_cost_not_the_bare_constant():
    """A mutation that makes ``takeover_reserve`` use
    ``STATE_CAPACITY_TAKEOVER_DELTA`` directly instead of calling
    ``next_takeover_cost`` would under-reserve for an oversized current
    lease; this fixes that gap and would itself fail if it crept back."""
    oversized = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a" * (sc.LEASE_TOKEN_MAX_CHARS + 50), "lease_id": "b",
        "fencing_epoch": 1,
    })
    remaining = sc.remaining_takeovers(oversized)
    assert remaining >= 1
    expected = sc.next_takeover_cost(oversized) + (remaining - 1) * sc.STATE_CAPACITY_TAKEOVER_DELTA
    assert sc.takeover_reserve(oversized) == expected
    assert sc.takeover_reserve(oversized) > remaining * sc.STATE_CAPACITY_TAKEOVER_DELTA


def test_next_takeover_cost_normalizes_the_epoch_like_the_real_writer():
    """``bin/mission-state.py``'s ``acquire_or_verify_lease`` (around
    L1287) normalizes ``fencing_epoch`` via ``int(value)`` before using
    it; ``next_takeover_cost`` must mirror that, not merely check
    ``isinstance(epoch, int)``."""
    numeric_string_epoch = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a", "lease_id": "b",
        "fencing_epoch": str(sc.LEASE_EPOCH_MAX * 1000),
    })
    excess_digits = len(str(sc.LEASE_EPOCH_MAX * 1000)) - len(str(sc.LEASE_EPOCH_MAX))
    assert sc.next_takeover_cost(numeric_string_epoch) == sc.STATE_CAPACITY_TAKEOVER_DELTA + excess_digits

    unparseable_epoch = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a", "lease_id": "b", "fencing_epoch": "not-a-number",
    })
    assert sc.next_takeover_cost(unparseable_epoch) == sc.STATE_LIMIT

    non_scalar_epoch = _flat_doc(lease_history=[], extra={
        "owner_session_id": "a", "lease_id": "b", "fencing_epoch": {"not": "a-number"},
    })
    assert sc.next_takeover_cost(non_scalar_epoch) == sc.STATE_LIMIT


@pytest.mark.parametrize("missing_epoch_value", ["absent", None, ""])
def test_next_takeover_cost_treats_a_never_acquired_lease_as_epoch_zero(missing_epoch_value):
    """A freshly ``init``ed session (before its first lease-acquiring
    write) has no ``fencing_epoch`` at all -- this is the normal shape of
    a brand new document, not a malformed one, and must not be charged
    the fail-closed sentinel (a regression this exact wording guards:
    treating "absent" the same as "present but unparseable" would make
    every fresh session's capacity check fail closed forever)."""
    doc = _flat_doc(lease_history=[], extra={"owner_session_id": "a", "lease_id": "b"})
    if missing_epoch_value == "absent":
        del doc["fencing_epoch"]
    else:
        doc["fencing_epoch"] = missing_epoch_value
    assert sc.next_takeover_cost(doc) == sc.STATE_CAPACITY_TAKEOVER_DELTA


def test_takeover_limit_is_largest_integer_within_system_share():
    assert sc.STATE_CAPACITY_SYSTEM_SHARE == sc.STATE_LIMIT // 16 if hasattr(sc, "STATE_LIMIT") else True
    n_l = sc.STATE_CAPACITY_TAKEOVER_LIMIT
    assert n_l >= 1
    assert (sc.STATE_CAPACITY_HALT_DELTA + n_l * sc.STATE_CAPACITY_TAKEOVER_DELTA
            <= sc.STATE_CAPACITY_SYSTEM_SHARE)
    assert (sc.STATE_CAPACITY_HALT_DELTA + (n_l + 1) * sc.STATE_CAPACITY_TAKEOVER_DELTA
            > sc.STATE_CAPACITY_SYSTEM_SHARE)

#: Per-stage slack folded into the stage Delta constants on top of the
#: measured real-shape growth below, for the record's *reserved-slot key*
#: names (not yet fixed by any design table; D2c's job) and any other
#: field this measurement does not bundle. Declared here, by name, so the
#: exact-match assertions below document what is measured versus what is
#: slack, instead of asserting a bare ``<=``.
_DISPATCH_STAGE_SLACK = 243
_CONSUME_STAGE_SLACK = 66
_TERMINAL_STAGE_SLACK = 164


def _fresh_review_record_doc(**fields):
    # Top-level reserved-slot keys (``operation_id``/``intent_digest``/
    # ``payload_digest``/``dispatch``/``running``/``terminal_receipt``/
    # ``result``/``status``), matching ``_FRESH_REVIEW_KEY_SLACK``'s own
    # naming, rather than an extra nesting level -- an extra level would
    # inflate legacy-pretty indentation beyond what the real embedding
    # (inside one array element of the D request projection) costs.
    base = {
        "operation_id": None, "intent_digest": None, "payload_digest": None,
        "dispatch": None, "running": None, "terminal_receipt": None,
        "result": None, "status": "pending",
    }
    base.update(fields)
    return _flat_doc(contract=_minimal_contract(), extra=base)


def test_dispatch_stage_delta_matches_the_measured_breakdown():
    """``FRESH_REVIEW_DISPATCH_STAGE_DELTA`` must equal the real growth
    from an accepted-but-undispatched record to a dispatched one --
    ``operation_id``/``intent_digest``/``payload_digest`` going from null
    to their maximum identifiers, ``status`` advancing to its longest
    value, and the E0a-pinned maximum ``dispatch``/``running`` shapes --
    plus the declared slack, exactly (not merely ``<=``, so a mutation
    that lowers the constant is caught by remeasuring, not by comparing
    two literals)."""
    from .test_issue917_fresh_review_bounds import maximum_intent, maximum_running

    minimal = _fresh_review_record_doc()
    maximal = _fresh_review_record_doc(
        operation_id="o" * 128, intent_digest="sha256:" + "0" * 64,
        payload_digest="sha256:" + "0" * 64, status="dispatch-unknown",
        dispatch=maximum_intent(), running=maximum_running(),
    )
    measured = canonical(maximal) - canonical(minimal)
    assert measured + _DISPATCH_STAGE_SLACK == sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA


def test_consume_stage_delta_matches_the_measured_breakdown():
    minimal = _fresh_review_record_doc(status="reserved")
    maximal = _fresh_review_record_doc(status="consumed", result="r" * 262144)
    measured = canonical(maximal) - canonical(minimal)
    assert measured + _CONSUME_STAGE_SLACK == sc.FRESH_REVIEW_CONSUME_STAGE_DELTA


def test_terminal_stage_delta_matches_the_measured_breakdown():
    from .test_issue917_fresh_review_bounds import maximum_terminal

    minimal = _fresh_review_record_doc()
    largest_terminal = max(
        (maximum_terminal(outcome) for outcome in ("completed", "failed", "blocked", "abandoned-unknown")),
        key=lambda shape: canonical(_fresh_review_record_doc(terminal_receipt=shape)),
    )
    maximal = _fresh_review_record_doc(terminal_receipt=largest_terminal)
    measured = canonical(maximal) - canonical(minimal)
    assert measured + _TERMINAL_STAGE_SLACK == sc.FRESH_REVIEW_TERMINAL_STAGE_DELTA


def test_stage_deltas_are_not_consulted_under_legacy_pretty():
    """v4 flat (``StateEncoding.LEGACY_PRETTY``) never advances a D request
    past ``pending`` (#918's "v4 の D item の予約は0" decision --
    ``residual_reservation`` returns 0 for every D item under that
    encoding), so the stage Delta constants are only ever consulted under
    canonical encoding. This is a regression guard on that routing, not a
    bound on the stage Deltas themselves."""
    doc = _flat_doc(contract=_minimal_contract())
    assert sc.residual_reservation(doc, encoding=sc.StateEncoding.LEGACY_PRETTY) == 0

#: Mirrors ``state_capacity``'s own conservative FindingLineage placeholder
#: shape (docs/design/880-repair-lineage.md §2's type table). Kept here,
#: not imported from the module, so this test independently re-derives the
#: literal the module pins rather than trivially re-asserting it.
_DIGEST71 = "sha256:" + "0" * 64
_CARef_present = {"kind": "fresh-review-finding", "relative_path": "x" * 91, "digest": _DIGEST71, "size": 262144}
_CARef_absent = {"absent": True, "reason": "r" * 128}


def _finding_lineage_fixed_part_shape():
    return {
        "lineage_id": _DIGEST71,
        "criterion_id": ID128,
        "severity": "Critical",
        "lifecycle": "repairing",
        "original_finding_ref": {
            "mission_id": ID128, "session_id": ID128, "original_request_id": ID128,
            "terminal_digest": _DIGEST71, "output_digest": _DIGEST71, "local_finding_id": ID128,
        },
        "original_terminal_receipt_ref": _CARef_present,
        "original_replay_evidence_ref": max(
            [_CARef_present, _CARef_absent], key=lambda v: canonical(v)
        ),
        "repro_binding": {
            "command_id": ID128, "verifier_definition_digest": _DIGEST71,
            "repro_input_ref": _CARef_present, "repro_input_digest": _DIGEST71,
            "repro_digest": _DIGEST71, "runner_repro_digest": _DIGEST71,
        },
        "introduced_candidate": {
            "canonical_command_map_digest": _DIGEST71, "contract_digest": _DIGEST71,
            "requirement_policy_digest": _DIGEST71, "policy_digest": _DIGEST71,
            "iteration": 2 ** 31 - 1,
        },
        "observations": [],
        "attempts": [],
    }


def test_lineage_fixed_part_matches_the_design_table_shape():
    measured = canonical(_finding_lineage_fixed_part_shape())
    assert sc.FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES == measured + 16
    assert sc.FRESH_REVIEW_UNIMPORTED_LINEAGE_MAX_BYTES == measured + 16


def test_lineage_and_disposition_shapes_pin_deltas():
    assert sc.FRESH_REVIEW_FINDINGS_LINEAGE_LIMIT == 61
    assert sc.FRESH_REVIEW_LINEAGE_STAGE_DELTA == max(
        61 * sc.FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES,
        sc.FRESH_REVIEW_UNIMPORTED_LINEAGE_MAX_BYTES,
    )
    # 61 full-shape finding-lineage records dominate the single
    # unimported-findings record by roughly 61x; this is the invariant
    # ``lineage_stage_delta`` relies on to treat the unimported bound as
    # non-binding in practice (see its module-level docstring).
    assert sc.FRESH_REVIEW_LINEAGE_STAGE_DELTA == 61 * sc.FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES
    assert sc.repair_attempt_reserve({}) == 0
    assert sc.disposition_reserve({}) == 0
    assert (sc.FRESH_REVIEW_PENDING_RESERVE
            == sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA + sc.FRESH_REVIEW_CONSUME_STAGE_DELTA
            + sc.FRESH_REVIEW_TERMINAL_STAGE_DELTA + sc.FRESH_REVIEW_LINEAGE_STAGE_DELTA)
    assert sc.FRESH_REVIEW_WITHDRAWN_RESERVE == 0

def test_contract_absent_charges_the_fail_closed_sentinel():
    pending = _pending_record()
    assert sc.lineage_variable_part({}, pending.request) == sc.STATE_LIMIT
    assert sc.lineage_variable_part({"schema_version": 5, "control": {}, "extensions": {}}, pending.request) == sc.STATE_LIMIT

def test_long_requirement_ids_and_prohibited_side_effects_increase_the_variable_part_and_reserve():
    pending = _pending_record()
    short_doc = _flat_doc(contract=_minimal_contract())
    long_doc = _flat_doc(contract=_minimal_contract(
        requirement_ids=["r" * 128] * 20, prohibited_side_effects=["p" * 64] * 20))
    short_variable = sc.lineage_variable_part(short_doc, pending.request)
    long_variable = sc.lineage_variable_part(long_doc, pending.request)
    assert long_variable > short_variable
    assert sc.lineage_stage_delta(long_doc, pending.request) > sc.lineage_stage_delta(short_doc, pending.request)
    # Growth must be exactly F_MAX times the per-criterion variable-part growth.
    assert (sc.lineage_stage_delta(long_doc, pending.request)
            - sc.lineage_stage_delta(short_doc, pending.request)
            == sc.FRESH_REVIEW_FINDINGS_LINEAGE_LIMIT * (long_variable - short_variable))

def test_requirement_ids_alone_increase_the_variable_part():
    """Isolates ``requirement_ids`` from ``prohibited_side_effects`` and the
    command->snapshot map, so a mutation that drops ``requirement_ids``
    from the variable-part payload (while still growing from the other
    two) cannot hide behind the combined-growth assertion above."""
    pending = _pending_record()
    short_doc = _flat_doc(contract=_minimal_contract())
    long_doc = _flat_doc(contract=_minimal_contract(requirement_ids=["r" * 128] * 20))
    assert sc.lineage_variable_part(long_doc, pending.request) > sc.lineage_variable_part(short_doc, pending.request)


def test_prohibited_side_effects_alone_increase_the_variable_part():
    pending = _pending_record()
    short_doc = _flat_doc(contract=_minimal_contract())
    long_doc = _flat_doc(contract=_minimal_contract(prohibited_side_effects=["p" * 64] * 20))
    assert sc.lineage_variable_part(long_doc, pending.request) > sc.lineage_variable_part(short_doc, pending.request)


def test_command_snapshot_map_alone_increases_the_variable_part():
    """Isolates the request's own ``candidate_bindings``-derived
    command->snapshot map, independent of the contract's
    ``requirement_ids``/``prohibited_side_effects``, so a mutation that
    drops ``_command_snapshot_map`` from the variable-part payload cannot
    hide behind a contract-only growth assertion."""
    from mission_kernel.fresh_review import (
        canonical_digest, candidate_identity, decode_request,
    )

    from .test_issue895_fresh_review import ADAPTER

    def _request_with_binding_count(count):
        criterion_ids = [f"AC{i}" for i in range(count)]
        bindings = [
            {"criterion_id": criterion_ids[i], "role": "verification", "command_id": f"command-{i}",
             "definition_digest": ADAPTER, "snapshot_digest": "sha256:" + format(i, "064x")}
            for i in range(count)
        ]
        snapshots = {binding["command_id"]: binding["snapshot_digest"] for binding in bindings}
        return decode_request({
            "schema": "mission-fresh-review-request/1", "request_id": f"request-{count}",
            "nonce": f"nonce-{count}", "mission_id": "mission-1", "session_id": "session-1",
            "requirement_digest": ADAPTER, "contract_digest": ADAPTER,
            "verifier_policy_digest": ADAPTER, "candidate_digest": candidate_identity(snapshots),
            "input_digest": canonical_digest({"input": "fixture"}),
            "adapter_registration_digest": ADAPTER, "candidate_bindings": bindings,
            "criterion_ids": criterion_ids, "iteration": 1, "perspective": "counterexamples",
            "allowed_tools": [], "wall_time_sec": 300, "max_tool_calls": 64, "max_replays": 16,
            "max_output_bytes": 262144, "max_packet_bytes": 1048576,
            "created_at": "2026-01-01T00:00:00+00:00",
            "input_ref": {
                "kind": "fresh-review-input",
                "relative_path": "evidence/fresh-review/" + canonical_digest({"input": "fixture"})[7:] + ".json",
                "digest": canonical_digest({"input": "fixture"}), "size": 19,
            },
        })

    doc = _flat_doc(contract=_minimal_contract())
    few_bindings = _request_with_binding_count(1)
    many_bindings = _request_with_binding_count(20)
    assert sc.lineage_variable_part(doc, many_bindings) > sc.lineage_variable_part(doc, few_bindings)


def test_criterion_absent_from_contract_falls_back_to_the_largest_criterion():
    pending = _pending_record()
    # pending.request.criterion_ids == ('AC1',), absent from this contract.
    contract = _minimal_contract(criterion_ids=("OTHER",), requirement_ids=["r" * 128])
    doc = _flat_doc(contract=contract)
    variable = sc.lineage_variable_part(doc, pending.request)
    # ``variable > 0`` alone would still pass if the fallback list
    # comprehension were removed entirely, because ``candidates`` would
    # then be empty and the function's own ``if not candidates: return
    # command_map_bytes`` branch already returns a positive value (the
    # request always has >= 1 candidate binding). Assert the fallback
    # criterion's own requirement_ids are actually included, by comparing
    # against the command map alone.
    command_map_bytes = sc._encode_len_safe(sc._command_snapshot_map(pending.request))
    assert variable > command_map_bytes


def test_encode_len_safe_fails_closed_not_to_zero():
    class Unencodable:
        pass

    assert sc._encode_len_safe(Unencodable()) == sc.STATE_LIMIT


def test_lineage_variable_part_fails_closed_when_criteria_is_not_a_list():
    pending = _pending_record()
    doc = _flat_doc(contract={"schema": "mission-acceptance-contract/2", "criteria": "not-a-list"})
    assert sc.lineage_variable_part(doc, pending.request) == sc.STATE_LIMIT


def test_lineage_variable_part_with_zero_criteria_is_not_zero():
    """An empty (but well-typed) ``criteria`` list must still charge at
    least the request's own command->snapshot map, not 0: a mutation that
    special-cased "0 criteria" to return 0 would under-reserve."""
    pending = _pending_record()
    doc = _flat_doc(contract={"schema": "mission-acceptance-contract/2", "criteria": []})
    command_map_bytes = sc._encode_len_safe(sc._command_snapshot_map(pending.request))
    assert sc.lineage_variable_part(doc, pending.request) == command_map_bytes
    assert sc.lineage_variable_part(doc, pending.request) > 0

def test_padding_helper_reaches_exact_headroom_zero():
    doc = _pad_document()
    assert sc.satisfies_capacity(doc, canonical(doc))
    grown = dict(doc)
    grown["padding"] = grown["padding"] + "q"
    assert not sc.satisfies_capacity(grown, canonical(grown))

def test_takeover_history_well_past_n_l_does_not_make_system_remaining_negative():
    n_l = sc.STATE_CAPACITY_TAKEOVER_LIMIT
    doc = _flat_doc(lease_history=[
        {"owner_session_id": "o", "lease_id": "l" + str(i), "fencing_epoch": i + 1,
         "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}
        for i in range(n_l + 5)
    ])
    assert sc.system_remaining(doc) == sc.STATE_CAPACITY_HALT_DELTA
    assert sc.remaining_takeovers(doc) == 0

@pytest.mark.parametrize("halt_value", ["absent", "", None])
def test_unwritten_halt_slot_forms_agree_on_system_remaining(halt_value):
    doc = _flat_doc()
    if halt_value == "absent":
        del doc["halt_reason"]
    else:
        doc["halt_reason"] = halt_value
    assert sc.halt_slot_written(doc) is False
    assert sc.system_remaining(doc) == sc.STATE_CAPACITY_HALT_DELTA + sc.takeover_reserve(doc)

def test_whitespace_only_halt_reason_counts_as_written():
    doc = _flat_doc(halt_reason="   ")
    assert sc.halt_slot_written(doc) is True


# --- D: residual_reservation / fail-closed behaviour ------------------

def test_undecodable_projection_charges_the_full_physical_limit():
    """An undecodable embedded projection must fail closed to STATE_LIMIT,
    not to one pending request's reserve -- it cannot even verify how many
    requests are outstanding, let alone their statuses."""
    doc = _flat_doc()
    doc["fresh_review"] = {"schema": "bogus", "requests": []}
    assert sc.residual_reservation(doc) == sc.STATE_LIMIT
    assert sc.residual_reservation(doc) > sc.FRESH_REVIEW_PENDING_RESERVE
    # satisfies_capacity must reject in this state (fail-closed), not admit.
    assert sc.is_over_capacity(doc, canonical(doc))


def _projection_fresh_review_field(projection):
    from mission_kernel.fresh_review import projection_document

    return projection_document(projection)


def test_legacy_pretty_reserves_nothing_even_with_pending_requests():
    from mission_kernel.fresh_review import FreshReviewProjection

    pending = _pending_record()
    document = _flat_doc(contract=_minimal_contract())
    document["fresh_review"] = _projection_fresh_review_field(FreshReviewProjection((pending,)))
    assert sc.residual_reservation(document, encoding=sc.StateEncoding.LEGACY_PRETTY) == 0
    # ... while the default (v5-style) encoding reserves the pending bucket,
    # so this is actually exercising the LEGACY_PRETTY branch and not a
    # degenerate "always 0" path.
    assert sc.residual_reservation(document) > 0


@pytest.mark.parametrize("status", ["pending", "reserved", "consumed"])
def test_residual_reservation_by_status_matches_the_per_status_baseline(status):
    import dataclasses

    from mission_kernel.fresh_review import FreshReviewProjection

    pending = _pending_record()
    if status == "pending":
        record = pending
    elif status == "reserved":
        record = dataclasses.replace(
            pending, status="reserved", operation_id="op-" + "0" * 10,
            intent_digest="sha256:" + "0" * 64, payload_digest="sha256:" + "0" * 64,
        )
    else:
        from mission_kernel.json_codec import freeze_json_value

        record = dataclasses.replace(
            pending, status="consumed", operation_id="op-" + "0" * 10,
            intent_digest="sha256:" + "0" * 64, payload_digest="sha256:" + "0" * 64,
            result=freeze_json_value({"ok": True}),
        )
    document = _flat_doc(contract=_minimal_contract())
    document["fresh_review"] = _projection_fresh_review_field(FreshReviewProjection((record,)))
    expected = (
        sc._FRESH_REVIEW_FIXED_RESERVE_BY_STATUS[status]
        + sc.lineage_stage_delta(document, pending.request)
    )
    assert sc.residual_reservation(document) == expected
    # Each status must pick a strictly different fixed bucket (catches a
    # mutation that collapses two statuses onto the same reserve).
    other_statuses = {"pending", "reserved", "consumed"} - {status}
    for other in other_statuses:
        assert (
            sc._FRESH_REVIEW_FIXED_RESERVE_BY_STATUS[status]
            != sc._FRESH_REVIEW_FIXED_RESERVE_BY_STATUS[other]
        )


def test_withdrawn_record_reserves_nothing():
    from mission_kernel.fresh_review import FreshReviewProjection, WithdrawnFreshReviewRecord

    pending = _pending_record()
    tombstone = WithdrawnFreshReviewRecord(
        request_id=pending.request.request_id,
        nonce=pending.request.nonce,
        prepare_operation_id=pending.prepare_operation_id,
        request_digest="sha256:" + "0" * 64,
        criterion_ids=tuple(pending.request.criterion_ids),
        withdraw_operation_id="op-" + "0" * 10,
        withdraw_fencing_epoch=1,
    )
    document = _flat_doc(contract=_minimal_contract())
    document["fresh_review"] = _projection_fresh_review_field(FreshReviewProjection((tombstone,)))
    assert sc.residual_reservation(document) == sc.FRESH_REVIEW_WITHDRAWN_RESERVE
    assert sc.residual_reservation(document) == 0
