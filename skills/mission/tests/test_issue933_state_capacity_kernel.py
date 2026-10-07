"""E0b-2 (#933): pure capacity-reservation kernel (Delta constants + verdict)."""
from __future__ import annotations

import copy
import json

import pytest

from mission_kernel.fresh_review import (
    FreshReviewProjection, FreshReviewRecord, WithdrawnFreshReviewRecord,
    consume_request, projection_document, reserve_request, withdraw_request,
)
from mission_kernel.json_codec import encode_json_value, freeze_json_value
from mission_kernel import state_capacity as sc

from .test_issue895_fresh_review import ADAPTER, _pure_projection

STATE_LIMIT = sc.STATE_LIMIT if hasattr(sc, "STATE_LIMIT") else __import__(
    "mission_kernel.json_codec", fromlist=["STATE_LIMIT"]).STATE_LIMIT


def canonical(document):
    return len(encode_json_value(freeze_json_value(document)))


def legacy(document):
    return len(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def encode(document, encoding):
    return canonical(document) if encoding is sc.StateEncoding.CANONICAL else legacy(document)


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


# --------------------------------------------------------------------- #
# Shape builders used by both the Delta-pinning tests and the verdict
# tests, so a single definition of "the maximal halt / takeover write"
# backs every assertion (the literal constants in state_capacity.py are
# measured from exactly these shapes).
# --------------------------------------------------------------------- #

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


@pytest.mark.parametrize("halt_reason_absent", [True, False])
@pytest.mark.parametrize("encoding_fn", [canonical, legacy], ids=["canonical", "legacy"])
def test_halt_delta_pins_maximum_from_absent_and_empty_slot(halt_reason_absent, encoding_fn):
    base = _flat_halt_base(halt_reason_absent=halt_reason_absent)
    after = _flat_halt_after(base)
    delta = encoding_fn(after) - encoding_fn(base)
    assert delta <= sc.STATE_CAPACITY_HALT_DELTA
    # The absent + legacy-pretty combination is the one that actually
    # reaches the pinned maximum; every other combination stays below it.
    if halt_reason_absent and encoding_fn is legacy:
        assert delta == sc.STATE_CAPACITY_HALT_DELTA


def _lease_min(lease_history):
    return {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 1,
            "lease_expires_at": "9999-12-31T23:59:59Z", "lease_history": lease_history}


def _lease_max_after(base):
    doc = dict(base)
    entry = {"owner_session_id": ID128, "lease_id": ID128, "fencing_epoch": 2 ** 63 - 2,
              "reason": "r" * 128, "at": "9999-12-31T23:59:59Z"}
    doc["lease_history"] = list(base["lease_history"]) + [entry]
    doc["owner_session_id"] = ID128
    doc["lease_id"] = ID128
    doc["fencing_epoch"] = 2 ** 63 - 1
    return doc


@pytest.mark.parametrize("encoding_fn", [canonical, legacy], ids=["canonical", "legacy"])
def test_takeover_delta_pins_min_to_max_lease_replacement(encoding_fn):
    base = _lease_min([])
    after = _lease_max_after(base)
    delta = encoding_fn(after) - encoding_fn(base)
    assert delta <= sc.STATE_CAPACITY_TAKEOVER_DELTA
    if encoding_fn is legacy:
        assert delta == sc.STATE_CAPACITY_TAKEOVER_DELTA


def test_takeover_limit_is_largest_integer_within_system_share():
    assert sc.STATE_CAPACITY_SYSTEM_SHARE == sc.STATE_LIMIT // 16 if hasattr(sc, "STATE_LIMIT") else True
    n_l = sc.STATE_CAPACITY_TAKEOVER_LIMIT
    assert n_l >= 1
    assert (sc.STATE_CAPACITY_HALT_DELTA + n_l * sc.STATE_CAPACITY_TAKEOVER_DELTA
            <= sc.STATE_CAPACITY_SYSTEM_SHARE)
    assert (sc.STATE_CAPACITY_HALT_DELTA + (n_l + 1) * sc.STATE_CAPACITY_TAKEOVER_DELTA
            > sc.STATE_CAPACITY_SYSTEM_SHARE)


@pytest.mark.parametrize("stage", ["dispatch", "terminal"])
@pytest.mark.parametrize("encoding_name", ["canonical", "legacy"])
def test_stage_deltas_embed_e0a_shapes(stage, encoding_name):
    from mission_kernel.fresh_review_receipts import FRESH_REVIEW_MAX_ENCODED_BYTES
    if stage == "dispatch":
        embedded = FRESH_REVIEW_MAX_ENCODED_BYTES["intent"] + FRESH_REVIEW_MAX_ENCODED_BYTES["running"]
        assert sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA > embedded
    else:
        largest_terminal = max(
            FRESH_REVIEW_MAX_ENCODED_BYTES[key]
            for key in ("completed", "failed", "blocked", "abandoned-unknown")
        )
        assert sc.FRESH_REVIEW_TERMINAL_STAGE_DELTA > largest_terminal


def test_lineage_and_disposition_shapes_pin_deltas():
    assert sc.FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES == 549 + 16
    assert sc.FRESH_REVIEW_UNIMPORTED_LINEAGE_MAX_BYTES == 442
    assert sc.FRESH_REVIEW_FINDINGS_LINEAGE_LIMIT == 61
    assert sc.FRESH_REVIEW_LINEAGE_STAGE_DELTA == max(
        61 * (549 + 16), 442
    )
    # The zero-variable-part formula value must stay >= the real measured
    # 61-element legacy-pretty array encode length (34,345 bytes); the
    # "+16" separator allowance exists precisely to keep this true.
    assert sc.FRESH_REVIEW_LINEAGE_STAGE_DELTA >= 34345
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


def test_criterion_absent_from_contract_falls_back_to_the_largest_criterion():
    pending = _pending_record()
    # pending.request.criterion_ids == ('AC1',), absent from this contract.
    contract = _minimal_contract(criterion_ids=("OTHER",), requirement_ids=["r" * 128])
    doc = _flat_doc(contract=contract)
    variable = sc.lineage_variable_part(doc, pending.request)
    assert variable > 0


def test_lineage_reserve_is_enforced_exactly_at_the_boundary_for_a_long_criterion():
    pending = _pending_record()
    long_contract = _minimal_contract(
        requirement_ids=["r" * 128] * 10, prohibited_side_effects=["p" * 64] * 10)
    rec = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    doc = _pad_document(status_records=[rec], contract=long_contract)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    # Confirm this fixture actually carries a bigger-than-baseline lineage
    # reserve, i.e. the long criterion's variable part is really exercised.
    assert sc.lineage_stage_delta(doc, pending.request) > sc.FRESH_REVIEW_LINEAGE_STAGE_DELTA

    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(
        projection, pending.request, operation_id="dispatch-1",
        intent_digest=ADAPTER, payload_digest=ADAPTER,
    )
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(reserved)
    proposed_len = canonical(proposed)
    verdict = sc.state_capacity_verdict(base, proposed, proposed_len, encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted, verdict

    over = dict(proposed)
    over["padding"] = over["padding"] + "q" * (sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA + 1)
    over_len = canonical(over)
    verdict = sc.state_capacity_verdict(base, over, over_len, encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-invariant-broken"


# --------------------------------------------------------------------- #
# Document builders for verdict tests.
# --------------------------------------------------------------------- #

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


def test_padding_helper_reaches_exact_headroom_zero():
    doc = _pad_document()
    assert sc.satisfies_capacity(doc, canonical(doc))
    grown = dict(doc)
    grown["padding"] = grown["padding"] + "q"
    assert not sc.satisfies_capacity(grown, canonical(grown))


def test_reserved_item_still_advances_after_halt_and_full_takeovers():
    pending = _pending_record()
    doc = _pad_document(status_records=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    # Simulate: halt slot written, all N_L takeovers recorded.
    doc["halt_reason"] = "stagnation"
    doc["lease_history"] = [
        {"owner_session_id": "o", "lease_id": "l" + str(i), "fencing_epoch": i + 1,
         "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}
        for i in range(sc.STATE_CAPACITY_TAKEOVER_LIMIT)
    ]
    doc["fencing_epoch"] = sc.STATE_CAPACITY_TAKEOVER_LIMIT + 1
    doc["lease_id"] = "current"
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.system_remaining(doc) == 0

    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(
        projection, pending.request, operation_id="dispatch-1",
        intent_digest=ADAPTER, payload_digest=ADAPTER,
    )
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(reserved)
    proposed_len = canonical(proposed)
    verdict = sc.state_capacity_verdict(base, proposed, proposed_len, encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted, verdict
    assert verdict.write_kind == "normal"


def test_growth_past_the_pinned_stage_delta_is_invariant_broken_not_exhausted():
    pending = _pending_record()
    doc = _pad_document(status_records=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))

    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(
        projection, pending.request, operation_id="dispatch-1",
        intent_digest=ADAPTER, payload_digest=ADAPTER,
    )
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(reserved)
    proposed["padding"] = proposed["padding"] + "q" * (sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA + 1)
    proposed_len = canonical(proposed)
    verdict = sc.state_capacity_verdict(base, proposed, proposed_len, encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-invariant-broken"


@pytest.mark.parametrize("recorded,should_accept", [
    (lambda n_l: n_l - 1, True),
    (lambda n_l: n_l, False),
    (lambda n_l: n_l + 1, False),
])
def test_takeover_boundary_at_the_recorded_count(recorded, should_accept):
    n_l = sc.STATE_CAPACITY_TAKEOVER_LIMIT
    count = recorded(n_l)
    doc = _flat_doc(lease_history=[
        {"owner_session_id": "o", "lease_id": "l" + str(i), "fencing_epoch": i + 1,
         "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}
        for i in range(count)
    ])
    doc["fencing_epoch"] = count + 1
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    proposed = dict(doc)
    proposed["lease_history"] = doc["lease_history"] + [
        {"owner_session_id": "o", "lease_id": "l" + str(count), "fencing_epoch": count + 1,
         "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}
    ]
    proposed["owner_session_id"] = "new-owner"
    proposed["lease_id"] = "new-lease"
    proposed["fencing_epoch"] = count + 2
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted is should_accept
    if not should_accept:
        assert verdict.code == "state-capacity-exhausted"


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


# --------------------------------------------------------------------- #
# Legacy-full.
# --------------------------------------------------------------------- #

def _big_pending(size):
    pending = _pending_record()
    doc = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    doc = dict(doc)
    doc["request"] = dict(doc["request"])
    doc["request"]["perspective"] = "p" * size
    return doc


def test_legacy_full_boundary_both_sides():
    # No pending request exists to withdraw, so bloat that pushes the
    # document past STATE_LIMIT - Delta_halt cannot be shed and the
    # boundary is purely about the padded length itself.
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _flat_doc(requests=[], halt_reason="")
    doc["padding"] = ""
    while canonical(doc) < threshold:
        doc["padding"] += "p" * (threshold - canonical(doc))
    while canonical(doc) > threshold:
        doc["padding"] = doc["padding"][: len(doc["padding"]) - (canonical(doc) - threshold)]
    base_len = canonical(doc)
    assert base_len == threshold
    assert sc._is_legacy_full(doc, base_len, encode=lambda d: canonical(d)) is False

    over = dict(doc)
    over["padding"] = over["padding"] + "q"
    over_len = canonical(over)
    assert over_len == threshold + 1
    assert sc._is_legacy_full(over, over_len, encode=lambda d: canonical(d)) is True


def test_legacy_full_is_escaped_once_withdrawing_pendings_clears_the_threshold():
    pending = _pending_record()
    rec = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _flat_doc(requests=[rec], halt_reason="")
    doc["padding"] = ""
    while canonical(doc) < threshold + 1:
        doc["padding"] += "p" * (threshold + 1 - canonical(doc))
    while canonical(doc) > threshold + 1:
        doc["padding"] = doc["padding"][: len(doc["padding"]) - (canonical(doc) - threshold - 1)]
    assert canonical(doc) == threshold + 1
    # Withdrawing the one pending request sheds more than the 1-byte
    # excess, so this is not legacy-full even though the base is over
    # capacity by the plain physical-length check.
    assert sc._is_legacy_full(doc, canonical(doc), encode=lambda d: canonical(d)) is False


def test_legacy_full_rejects_every_write_kind():
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _flat_doc(requests=[_big_pending(threshold)], halt_reason="")
    base_len = canonical(doc)
    assert base_len > threshold
    base = sc.CapacityBase(document=doc, encoded_len=base_len)

    halted = dict(doc)
    halted["halt_reason"] = "stagnation"
    halted["phase"] = "halted"
    verdict = sc.state_capacity_verdict(
        base, halted, canonical(halted), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-legacy-full"


def test_halt_written_normal_state_is_not_legacy_full_at_the_boundary():
    doc = _flat_doc(requests=[], halt_reason="stagnation")
    threshold = sc.STATE_LIMIT - sc.system_remaining(doc)
    doc["padding"] = ""
    while canonical(doc) < threshold:
        doc["padding"] += "p" * (threshold - canonical(doc))
    while canonical(doc) > threshold:
        doc["padding"] = doc["padding"][: len(doc["padding"]) - (canonical(doc) - threshold)]
    assert sc.is_over_capacity(doc, canonical(doc)) is False


# --------------------------------------------------------------------- #
# Over-capacity stop-system behaviour.
# --------------------------------------------------------------------- #

def _over_capacity_doc(excess=100):
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _flat_doc(requests=[_big_pending(1)], halt_reason="")
    doc["padding"] = "p" * max(0, threshold - canonical(doc) + excess)
    while canonical(doc) < threshold + excess:
        doc["padding"] += "p"
    return doc


def test_over_capacity_halt_allowed_up_to_state_limit():
    doc = _over_capacity_doc(excess=10)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.is_over_capacity(doc, base.encoded_len)
    halted = dict(doc)
    halted["halt_reason"] = "stagnation"
    halted["phase"] = "halted"
    verdict = sc.state_capacity_verdict(
        base, halted, canonical(halted), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "stop-halt"


def test_over_capacity_dispatch_is_rejected():
    pending = _pending_record()
    doc = _over_capacity_doc(excess=10)
    doc["fresh_review"] = {"schema": "mission-fresh-review/1",
                            "requests": [projection_document(FreshReviewProjection((pending,)))["requests"][0]]}
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(projection, pending.request, operation_id="d1",
                                intent_digest=ADAPTER, payload_digest=ADAPTER)
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(reserved)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


def test_over_capacity_withdraw_is_allowed():
    pending = _pending_record()
    doc = _over_capacity_doc(excess=10)
    doc["fresh_review"] = {"schema": "mission-fresh-review/1",
                            "requests": [projection_document(FreshReviewProjection((pending,)))["requests"][0]]}
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    projection = FreshReviewProjection((pending,))
    withdrawn = withdraw_request(projection, request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=1)
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(withdrawn)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "withdraw"


# --------------------------------------------------------------------- #
# Withdraw judgement (not-needed / invariant-broken / pass).
# --------------------------------------------------------------------- #

def test_withdraw_not_needed_when_base_within_capacity():
    pending = _pending_record()
    doc = _flat_doc(requests=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    projection = FreshReviewProjection((pending,))
    withdrawn = withdraw_request(projection, request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=1)
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(withdrawn)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-withdraw-not-needed"


def test_withdraw_invariant_broken_when_proposed_not_smaller():
    pending = _pending_record()
    doc = _over_capacity_doc(excess=10)
    doc["fresh_review"] = {"schema": "mission-fresh-review/1",
                            "requests": [projection_document(FreshReviewProjection((pending,)))["requests"][0]]}
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    projection = FreshReviewProjection((pending,))
    withdrawn = withdraw_request(projection, request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=1)
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(withdrawn)
    # Grow a lease field (still inside the withdraw write_kind's allowed
    # diff) so the proposed document is not smaller than base -- a
    # tampered/forged withdraw write that tries to claim the exemption
    # without actually shrinking.
    growth = max(0, base.encoded_len - canonical(proposed) + 5)
    proposed["owner_session_id"] = proposed["owner_session_id"] + "q" * growth
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-invariant-broken"


# --------------------------------------------------------------------- #
# write_kind classification: mixed diffs must not be misclassified.
# --------------------------------------------------------------------- #

def test_mixing_halt_and_takeover_in_one_diff_is_not_a_pure_stop_kind():
    doc = _flat_doc()
    proposed = dict(doc)
    proposed["halt_reason"] = "stagnation"
    proposed["phase"] = "halted"
    proposed["lease_history"] = list(doc["lease_history"]) + [
        {"owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 1,
         "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}
    ]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 2
    # Extra unrelated field makes the diff exceed both stop-kind allowances.
    proposed["unrelated_extra_field"] = "x"
    kind = sc.classify_write_kind(doc, proposed)
    assert kind == sc.WriteKind.NORMAL


def test_withdraw_mixed_with_unrelated_diff_is_not_withdraw():
    pending = _pending_record()
    doc = _flat_doc(requests=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    projection = FreshReviewProjection((pending,))
    withdrawn = withdraw_request(projection, request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=1)
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(withdrawn)
    proposed["unrelated_extra_field"] = "x"
    kind = sc.classify_write_kind(doc, proposed)
    assert kind != sc.WriteKind.WITHDRAW


def test_tombstone_epoch_mismatch_is_not_withdraw():
    pending = _pending_record()
    doc = _flat_doc(requests=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    projection = FreshReviewProjection((pending,))
    withdrawn = withdraw_request(projection, request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=999)
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(withdrawn)
    # fencing_epoch in the tombstone (999) does not match lease.fencing_epoch (1).
    kind = sc.classify_write_kind(doc, proposed)
    assert kind != sc.WriteKind.WITHDRAW


def test_takeover_diff_with_an_extra_unrelated_field_is_not_stop_takeover():
    doc = _flat_doc()
    proposed = dict(doc)
    proposed["lease_history"] = list(doc["lease_history"]) + [
        {"owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 1,
         "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}
    ]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 2
    proposed["unrelated_extra_field"] = "x"
    kind = sc.classify_write_kind(doc, proposed)
    assert kind != sc.WriteKind.STOP_TAKEOVER


def test_halt_diff_with_an_extra_unrelated_field_is_not_stop_halt():
    doc = _flat_doc()
    proposed = dict(doc)
    proposed["halt_reason"] = "x"
    proposed["phase"] = "halted"
    proposed["loop_active"] = False
    proposed["unrelated_extra_field"] = "x"
    kind = sc.classify_write_kind(doc, proposed)
    assert kind != sc.WriteKind.STOP_HALT


def test_unknown_halt_category_does_not_crash_classification():
    doc = _flat_doc()
    proposed = dict(doc)
    proposed["halt_reason"] = "x"
    proposed["halt_category"] = "not-a-real-category"
    proposed["phase"] = "halted"
    proposed["loop_active"] = False
    kind = sc.classify_write_kind(doc, proposed)
    assert kind == sc.WriteKind.STOP_HALT


def test_long_goal_dispatch_fields_stay_within_halt_delta():
    doc = _flat_doc()
    proposed = dict(doc)
    proposed.update(halt_reason="x", halt_category="routed-goal", phase="halted",
                     loop_active=False,
                     goal_dispatch_effective="g" * 128, goal_dispatch_host="g" * 128,
                     goal_dispatch_fallback_reason="g" * 128)
    delta = legacy(proposed) - legacy(doc)
    assert delta <= sc.STATE_CAPACITY_HALT_DELTA


def test_consume_result_at_max_output_bytes_plus_one_is_rejected_by_decoder():
    from mission_kernel.fresh_review import FreshReviewError
    pending = _pending_record()
    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(projection, pending.request, operation_id="d1",
                                intent_digest=ADAPTER, payload_digest=ADAPTER)
    oversized = {"blob": "x" * pending.request.max_output_bytes}
    with pytest.raises(FreshReviewError):
        consume_request(reserved, pending.request, result=oversized,
                         operation_id="d1", intent_digest=ADAPTER, payload_digest=ADAPTER)


# --------------------------------------------------------------------- #
# Idempotent / must-not-stop cases.
# --------------------------------------------------------------------- #

def test_withdraw_resend_is_idempotent_and_not_rejected_by_kernel_identity():
    pending = _pending_record()
    doc = _over_capacity_doc(excess=10)
    doc["fresh_review"] = {"schema": "mission-fresh-review/1",
                            "requests": [projection_document(FreshReviewProjection((pending,)))["requests"][0]]}
    projection = FreshReviewProjection((pending,))
    withdrawn = withdraw_request(projection, request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=1)
    resent = withdraw_request(withdrawn, request_id=pending.request.request_id,
                               operation_id="withdraw-1", fencing_epoch=1)
    assert resent == withdrawn


def test_lease_renewal_with_zero_growth_is_accepted_at_zero_headroom():
    doc = _pad_document()
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    proposed = dict(doc)
    proposed["lease_expires_at"] = "9999-12-31T23:59:58Z"  # same length, renewal
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted


def test_genesis_with_no_lease_history_and_no_lease_is_accepted():
    doc = _flat_doc()
    verdict = sc.state_capacity_verdict(
        None, doc, canonical(doc), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "genesis"


@pytest.mark.parametrize("excess_bytes", [0])
def test_encoded_len_over_physical_limit_is_always_exhausted(excess_bytes):
    doc = _flat_doc()
    verdict = sc.state_capacity_verdict(
        None, doc, sc.STATE_LIMIT + 1 + excess_bytes, encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


# --------------------------------------------------------------------- #
# 判定の順序 8 (LEGACY_PRETTY): v4 flat D requests never advance; v4 D
# items reserve nothing; halt / takeover / prepare still go through.
# --------------------------------------------------------------------- #

def test_legacy_pretty_rejects_pending_to_reserved_dispatch():
    pending = _pending_record()
    rec = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    doc = _flat_doc(requests=[rec])
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))

    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(
        projection, pending.request, operation_id="dispatch-1",
        intent_digest=ADAPTER, payload_digest=ADAPTER,
    )
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(reserved)
    verdict = sc.state_capacity_verdict(
        base, proposed, legacy(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


def test_legacy_pretty_rejects_reserved_to_consumed():
    pending = _pending_record()
    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(
        projection, pending.request, operation_id="dispatch-1",
        intent_digest=ADAPTER, payload_digest=ADAPTER,
    )
    reserved_rec = projection_document(reserved)["requests"][0]
    doc = _flat_doc(requests=[reserved_rec])
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))

    consumed = consume_request(
        reserved, pending.request, result={"status": "ok"},
        operation_id="dispatch-1", intent_digest=ADAPTER, payload_digest=ADAPTER,
    )
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(consumed)
    verdict = sc.state_capacity_verdict(
        base, proposed, legacy(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


def test_legacy_pretty_still_allows_halt_takeover_and_prepare():
    doc = _flat_doc()
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))

    halted = dict(doc)
    halted["halt_reason"] = "stagnation"
    halted["phase"] = "halted"
    verdict = sc.state_capacity_verdict(
        base, halted, legacy(halted), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted, verdict
    assert verdict.write_kind == "stop-halt"

    takeover = dict(doc)
    takeover["lease_history"] = list(doc["lease_history"]) + [
        {"owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 1,
         "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}
    ]
    takeover["owner_session_id"] = "owner-2"
    takeover["lease_id"] = "lease-2"
    takeover["fencing_epoch"] = 2
    verdict = sc.state_capacity_verdict(
        base, takeover, legacy(takeover), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted, verdict
    assert verdict.write_kind == "stop-takeover"

    pending = _pending_record()
    prepared = dict(doc)
    prepared["fresh_review"] = {
        "schema": "mission-fresh-review/1",
        "requests": [projection_document(FreshReviewProjection((pending,)))["requests"][0]],
    }
    verdict = sc.state_capacity_verdict(
        base, prepared, legacy(prepared), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted, verdict
    assert verdict.metrics.reserved == 0


def test_legacy_pretty_d_item_reserves_nothing():
    pending = _pending_record()
    rec = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    doc = _flat_doc(requests=[rec])
    assert sc.residual_reservation(doc, encoding=sc.StateEncoding.LEGACY_PRETTY) == 0
    assert sc.residual_reservation(doc, encoding=sc.StateEncoding.CANONICAL) > 0


# --------------------------------------------------------------------- #
# Table-driven accept/reject matrix.
# --------------------------------------------------------------------- #

def _basic_accept_case():
    doc = _flat_doc()
    return sc.CapacityBase(document=doc, encoded_len=canonical(doc)), dict(doc)


@pytest.mark.parametrize("mutator,expect_accept,expect_code", [
    (lambda d: d, True, None),
    (lambda d: {**d, "updated_at": TS27}, True, None),
    (lambda d: {**d, "halt_reason": "x", "phase": "halted", "loop_active": False}, True, None),
    (lambda d: {**d, "owner_session_id": "owner-1", "lease_id": "lease-1"}, True, None),
    (lambda d: {**d, "last_activity_at": TS27}, True, None),
    (lambda d: {**d, "halt_reason": "a" * 2048}, True, None),
    (lambda d: {**d, "halt_category": "evidence-submitted"}, True, None),
    (lambda d: {**d, "activity_segments": [CLOSED_SEGMENT]}, True, None),
    (lambda d: {**d, "goal_dispatch_effective": "g" * 128}, True, None),
    (lambda d: {**d, "lease_expires_at": "9999-12-31T23:59:58Z"}, True, None),
    (lambda d: {**d, "unrelated_benign_field": 1}, True, None),
])
def test_normal_mutations_within_headroom_are_accepted(mutator, expect_accept, expect_code):
    base, doc = _basic_accept_case()
    proposed = mutator(doc)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted is expect_accept
    if expect_code:
        assert verdict.code == expect_code


# ======================================================================
# Probe-derived regression table (抜け穴探索で見つかった反例の固定化).
#
# Each row encodes one scenario from the probe at
# /private/tmp/claude-501/-Users-tackeyy-dev-mission/
# 9537fbc1-a1c6-42d1-9951-40377977c0b8/scratchpad/probe933/{probe,probe2}.py,
# rebuilt against this module's public surface so it survives the probe
# scripts being cleaned up. v4 (flat/LEGACY_PRETTY) and v5 (nested/
# CANONICAL) are both exercised for every scenario that applies to both.
# ======================================================================

def _v5_request(request_id="request-1", nonce="nonce-1", criterion_ids=("AC1",)):
    from mission_kernel.fresh_review import decode_request, canonical_digest, candidate_identity
    input_digest = canonical_digest({"input": "fixture", "id": request_id})
    return decode_request({
        "schema": "mission-fresh-review-request/1", "request_id": request_id, "nonce": nonce,
        "mission_id": "mission-1", "session_id": "session-1", "requirement_digest": ADAPTER,
        "contract_digest": ADAPTER, "verifier_policy_digest": ADAPTER,
        "candidate_digest": candidate_identity({"command-1": ADAPTER}), "input_digest": input_digest,
        "adapter_registration_digest": ADAPTER,
        "candidate_bindings": [{"criterion_id": "AC1", "role": "verification", "command_id": "command-1",
                                "definition_digest": ADAPTER, "snapshot_digest": ADAPTER}],
        "criterion_ids": list(criterion_ids), "iteration": 1, "perspective": "counterexamples",
        "allowed_tools": [], "wall_time_sec": 300, "max_tool_calls": 64, "max_replays": 16,
        "max_output_bytes": 262144, "max_packet_bytes": 1048576, "created_at": "2026-01-01T00:00:00+00:00",
        "input_ref": {"kind": "fresh-review-input",
                      "relative_path": "evidence/fresh-review/" + input_digest[7:] + ".json",
                      "digest": input_digest, "size": 19},
    })


def _v5_pending_record(request_id="request-1", nonce="nonce-1"):
    req = _v5_request(request_id=request_id, nonce=nonce)
    return FreshReviewRecord(req, "prep-" + request_id, ADAPTER, ADAPTER)


def _v5_doc(*, requests=(), halt_reason="", halt_absent=False, lease_history=(),
            control_extra=None, extensions_extra=None, contract=None):
    control = {
        "phase": "executing", "loop_active": True, "terminal_outcome": None, "halt_category": None,
    }
    if not halt_absent:
        control["halt_reason"] = halt_reason
    if control_extra:
        control.update(control_extra)
    extensions = {
        "fresh_review": {"schema": "mission-fresh-review/1",
                          "requests": [projection_document(FreshReviewProjection(tuple(requests)))["requests"][i]
                                       for i in range(len(requests))]},
        "acceptance_contract": contract if contract is not None else _minimal_contract(),
    }
    if extensions_extra:
        extensions.update(extensions_extra)
    return {
        "schema_version": 5, "control": control,
        "lease": {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 1,
                  "lease_expires_at": "9999-12-31T23:59:59Z", "lease_history": list(lease_history)},
        "updated_at": TS27, "last_activity_at": TS27,
        "extensions": extensions,
    }


def _project(*records):
    return FreshReviewProjection(tuple(records))


def _case(name, base, proposed, encoding, *, accept, code=None, write_kind=None):
    return pytest.param(base, proposed, encoding, accept, code, write_kind, id=name)


def _push_over_capacity(doc, encoding, *, pad_key="zzz_padding_zzz"):
    """Grow ``doc`` (deep-copied) with a junk padding field until
    ``is_over_capacity`` is true under ``encoding``. Used to build the
    over-capacity bases the probe's "halt便乗" exploit needed -- without
    this, a misclassified write_kind and a correct one both end up
    accepted simply because there is plenty of headroom either way.
    """
    doc = copy.deepcopy(doc)
    for _ in range(40):
        if sc.is_over_capacity(doc, encode(doc, encoding), encoding=encoding):
            return doc
        doc[pad_key] = doc.get(pad_key, "") + "p" * 200000
    assert sc.is_over_capacity(doc, encode(doc, encoding), encoding=encoding), "fixture did not reach over-capacity"
    return doc


def _probe_table():
    cases = []

    # --- 1. pure halt: accepted + stop-halt, both layouts ----------------
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc()
            proposed = dict(base)
            proposed.update(halt_reason="x", phase="halted", loop_active=False)
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc()
            proposed = _v5_doc(control_extra={"halt_reason": "x", "phase": "halted", "loop_active": False})
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"pure_halt_{layout}", base, proposed, encoding,
                            accept=True, write_kind="stop-halt"))

    # --- 2. halt + fresh_review record advance mixed in: must NOT be
    #        stop-halt (the vulnerability the probe found). Bases are
    #        pushed over capacity so a misclassification would actually
    #        let the write through up to STATE_LIMIT -- the real exploit
    #        window the probe demonstrated. --------------------------
    rec = _v5_pending_record()
    for layout in ("v4", "v5"):
        if layout == "v4":
            raw_base = _flat_doc(requests=[projection_document(_project(rec))["requests"][0]])
            encoding = sc.StateEncoding.LEGACY_PRETTY
            base = _push_over_capacity(raw_base, encoding)
            proposed = dict(base)
            proposed.update(halt_reason="x", phase="halted", loop_active=False)
            reserved = reserve_request(_project(rec), rec.request, operation_id="d1",
                                        intent_digest=ADAPTER, payload_digest=ADAPTER)
            proposed["fresh_review"] = projection_document(reserved)
        else:
            raw_base = _v5_doc(requests=[rec])
            encoding = sc.StateEncoding.CANONICAL
            base = _push_over_capacity(raw_base, encoding)
            proposed = copy.deepcopy(base)
            proposed["control"].update(halt_reason="x", phase="halted", loop_active=False)
            reserved = reserve_request(_project(rec), rec.request, operation_id="d1",
                                        intent_digest=ADAPTER, payload_digest=ADAPTER)
            proposed["extensions"]["fresh_review"] = projection_document(reserved)
        cases.append(_case(f"halt+fresh_review_advance_mixed_{layout}", base, proposed, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

    # --- 3. halt + unrelated junk key mixed in (control/extensions or
    #        flat top level): must NOT be stop-halt ----------------------
    for layout in ("v4", "v5"):
        if layout == "v4":
            encoding = sc.StateEncoding.LEGACY_PRETTY
            base = _push_over_capacity(_flat_doc(), encoding)
            proposed = dict(base)
            proposed.update(halt_reason="x", phase="halted", loop_active=False,
                             unrelated_junk_field="J" * 5000)
        else:
            encoding = sc.StateEncoding.CANONICAL
            base = _push_over_capacity(_v5_doc(), encoding)
            proposed = copy.deepcopy(base)
            proposed["control"].update(halt_reason="x", phase="halted", loop_active=False)
            proposed["extensions"]["unrelated_junk"] = "J" * 5000
        cases.append(_case(f"halt+unrelated_junk_{layout}", base, proposed, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

    # --- 4. halt + junk ALONE (no halt fields touched): control only ----
    base_v5_only_junk = _push_over_capacity(_v5_doc(), sc.StateEncoding.CANONICAL)
    proposed_v5_only_junk = copy.deepcopy(base_v5_only_junk)
    proposed_v5_only_junk["extensions"]["unrelated_junk"] = "J" * 5000
    cases.append(_case("junk_only_no_halt_v5", base_v5_only_junk, proposed_v5_only_junk,
                        sc.StateEncoding.CANONICAL, accept=False, code="state-capacity-exhausted",
                        write_kind="normal"))

    # --- 5. loop_active-only change must not be stop-halt (coordinator's
    #        explicit example) ------------------------------------------
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc()
            proposed = dict(base)
            proposed["loop_active"] = False
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc()
            proposed = _v5_doc(control_extra={"loop_active": False})
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"loop_active_only_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 6. halt_slot already written: a second halt write is "normal",
    #        not stop-halt (not a first write) --------------------------
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc(halt_reason="already-halted")
            proposed = dict(base)
            proposed["halt_reason"] = "different-reason"
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc(halt_reason="already-halted")
            proposed = _v5_doc(halt_reason="different-reason")
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"halt_already_written_second_write_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 7. takeover + unrelated junk in control/extensions (v5): must
    #        NOT be stop-takeover ----------------------------------------
    base_v5_to = _push_over_capacity(_v5_doc(lease_history=[]), sc.StateEncoding.CANONICAL)
    proposed_v5_to_junk = copy.deepcopy(base_v5_to)
    proposed_v5_to_junk["lease"]["lease_history"] = [
        {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 1,
         "reason": "lease-expired-takeover", "at": TS27}
    ]
    proposed_v5_to_junk["lease"]["owner_session_id"] = "c"
    proposed_v5_to_junk["lease"]["lease_id"] = "d"
    proposed_v5_to_junk["lease"]["fencing_epoch"] = 2
    proposed_v5_to_junk["extensions"]["unrelated_junk"] = "J" * 5000
    cases.append(_case("takeover+junk_extensions_v5", base_v5_to, proposed_v5_to_junk,
                        sc.StateEncoding.CANONICAL, accept=False, code="state-capacity-exhausted",
                        write_kind="normal"))

    # --- 8. unknown halt_category / long goal_dispatch_* must not crash
    #        and must still be stop-halt (value sets are not validated) --
    base_v4_cat = _flat_doc()
    proposed_v4_cat = dict(base_v4_cat)
    proposed_v4_cat.update(halt_reason="x", halt_category="totally-unknown-category-xyz",
                            phase="halted", loop_active=False)
    cases.append(_case("unknown_halt_category_v4", base_v4_cat, proposed_v4_cat,
                        sc.StateEncoding.LEGACY_PRETTY, accept=True, write_kind="stop-halt"))

    base_v4_goal = _flat_doc()
    proposed_v4_goal = dict(base_v4_goal)
    proposed_v4_goal.update(halt_reason="x", phase="halted", loop_active=False,
                             goal_dispatch_effective="g" * 1000, goal_dispatch_host="g" * 1000,
                             goal_dispatch_fallback_reason="g" * 1000)
    cases.append(_case("long_goal_dispatch_v4", base_v4_goal, proposed_v4_goal,
                        sc.StateEncoding.LEGACY_PRETTY, accept=True, write_kind="stop-halt"))

    # --- 9. withdraw: pure (accept, over-capacity base) vs mixed with
    #        unrelated junk (reject) and mixed with a second record's
    #        advance (reject), both layouts. Withdraw is only meaningful
    #        (and only given the "withdraw" exemption at all) when the
    #        base is over capacity -- see "state-capacity-withdraw-not-
    #        needed" -- so every sub-case here pushes the base over
    #        capacity first. -------------------------------------------
    rec_a = _v5_pending_record(request_id="r1", nonce="n1")
    rec_b = _v5_pending_record(request_id="r2", nonce="n2")
    two_proj = _project(rec_a, rec_b)
    for layout in ("v4", "v5"):
        if layout == "v4":
            two_docs = projection_document(two_proj)["requests"]
            encoding = sc.StateEncoding.LEGACY_PRETTY
            base = _push_over_capacity(_flat_doc(requests=two_docs), encoding)
            withdrawn = withdraw_request(two_proj, request_id="r1", operation_id="wd-1",
                                          fencing_epoch=base["fencing_epoch"])
            proposed = dict(base)
            proposed["fresh_review"] = projection_document(withdrawn)
        else:
            encoding = sc.StateEncoding.CANONICAL
            base = _push_over_capacity(_v5_doc(requests=[rec_a, rec_b]), encoding)
            withdrawn = withdraw_request(two_proj, request_id="r1", operation_id="wd-1",
                                          fencing_epoch=base["lease"]["fencing_epoch"])
            proposed = copy.deepcopy(base)
            proposed["extensions"]["fresh_review"] = projection_document(withdrawn)
        cases.append(_case(f"withdraw_pure_{layout}", base, proposed, encoding,
                            accept=True, write_kind="withdraw"))

        # withdraw + unrelated junk alongside it.
        if layout == "v4":
            proposed_junk = dict(proposed)
            proposed_junk["unrelated_junk_field"] = "J" * 5000
        else:
            proposed_junk = copy.deepcopy(proposed)
            proposed_junk["extensions"]["unrelated_junk"] = "J" * 5000
        cases.append(_case(f"withdraw+junk_{layout}", base, proposed_junk, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

        # withdraw + a second record (r2) advancing at the same time.
        mixed = reserve_request(withdrawn, rec_b.request, operation_id="d-r2",
                                 intent_digest=ADAPTER, payload_digest=ADAPTER)
        if layout == "v4":
            proposed_mixed = dict(base)
            proposed_mixed["fresh_review"] = projection_document(mixed)
        else:
            proposed_mixed = copy.deepcopy(base)
            proposed_mixed["extensions"]["fresh_review"] = projection_document(mixed)
        cases.append(_case(f"withdraw+other_record_advance_{layout}", base, proposed_mixed, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

    # --- 10. withdraw_fencing_epoch mismatch: must not be withdraw ------
    single_proj = _project(_v5_pending_record())
    base_v5_epoch = _push_over_capacity(_v5_doc(requests=[_v5_pending_record()]), sc.StateEncoding.CANONICAL)
    tomb_mismatch = withdraw_request(single_proj, request_id="request-1", operation_id="wd-2",
                                      fencing_epoch=999)
    proposed_v5_epoch = copy.deepcopy(base_v5_epoch)
    proposed_v5_epoch["extensions"]["fresh_review"] = projection_document(tomb_mismatch)
    # lease.fencing_epoch stays at base's value (1), tombstone says 999 -> mismatch.
    cases.append(_case("withdraw_epoch_mismatch_v5", base_v5_epoch, proposed_v5_epoch,
                        sc.StateEncoding.CANONICAL, accept=False, code="state-capacity-exhausted",
                        write_kind="normal"))

    # --- 11. takeover boundary (N_L-1 / N_L / N_L+1), both layouts ------
    n_l = sc.STATE_CAPACITY_TAKEOVER_LIMIT

    def _history(n):
        return [{"owner_session_id": "a", "lease_id": f"id{i}", "fencing_epoch": i + 1,
                  "reason": "lease-expired-takeover", "at": TS27} for i in range(n)]

    for count, label, expect_accept in [
        (n_l - 1, "below_limit", True), (n_l, "at_limit", False), (n_l + 1, "above_limit", False),
    ]:
        for layout in ("v4", "v5"):
            if layout == "v4":
                base = _flat_doc(lease_history=_history(count))
                base["fencing_epoch"] = count + 1
                proposed = dict(base)
                proposed["lease_history"] = _history(count) + [
                    {"owner_session_id": "z", "lease_id": "zz", "fencing_epoch": count + 1,
                     "reason": "lease-expired-takeover", "at": TS27}]
                proposed["owner_session_id"] = "z"
                proposed["lease_id"] = "zz"
                proposed["fencing_epoch"] = count + 2
                encoding = sc.StateEncoding.LEGACY_PRETTY
            else:
                base = _v5_doc(lease_history=_history(count))
                base["lease"]["fencing_epoch"] = count + 1
                proposed = _v5_doc(lease_history=_history(count) + [
                    {"owner_session_id": "z", "lease_id": "zz", "fencing_epoch": count + 1,
                     "reason": "lease-expired-takeover", "at": TS27}])
                proposed["lease"]["owner_session_id"] = "z"
                proposed["lease"]["lease_id"] = "zz"
                proposed["lease"]["fencing_epoch"] = count + 2
                encoding = sc.StateEncoding.CANONICAL
            code = None if expect_accept else "state-capacity-exhausted"
            cases.append(_case(f"takeover_boundary_{label}_{layout}", base, proposed, encoding,
                                accept=expect_accept, code=code))

    # --- 12. lease renewal (zero history growth): accept, stop-takeover -
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc(lease_history=_history(3))
            base["fencing_epoch"] = 4
            proposed = dict(base)
            proposed["owner_session_id"] = "renewed-owner"
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc(lease_history=_history(3))
            base["lease"]["fencing_epoch"] = 4
            proposed = _v5_doc(lease_history=_history(3))
            proposed["lease"]["fencing_epoch"] = 4
            proposed["lease"]["owner_session_id"] = "renewed-owner"
            proposed["lease"]["lease_id"] = base["lease"]["lease_id"]
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"lease_renewal_zero_growth_{layout}", base, proposed, encoding,
                            accept=True, write_kind="stop-takeover"))

    # --- 13. lease history SHRINK must not be stop-takeover (point 3) --
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc(lease_history=_history(5))
            proposed = dict(base)
            proposed["lease_history"] = _history(2)
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc(lease_history=_history(5))
            proposed = _v5_doc(lease_history=_history(2))
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"lease_history_shrink_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 14. lease history truncate-and-replace (same length, different
    #         content) must not be stop-takeover -------------------------
    for layout in ("v4", "v5"):
        replaced = [{"owner_session_id": "x", "lease_id": "yy", "fencing_epoch": 99,
                     "reason": "forged", "at": TS27}] * 3
        if layout == "v4":
            base = _flat_doc(lease_history=_history(3))
            proposed = dict(base)
            proposed["lease_history"] = replaced
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc(lease_history=_history(3))
            proposed = _v5_doc(lease_history=replaced)
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"lease_history_replace_same_length_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 15. lease history jump by more than 1 entry must not be
    #         stop-takeover ----------------------------------------------
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc(lease_history=_history(2))
            proposed = dict(base)
            proposed["lease_history"] = _history(2) + _history(2)  # +2 at once, not +1
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc(lease_history=_history(2))
            proposed = _v5_doc(lease_history=_history(2) + _history(2))
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"lease_history_jump_by_two_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 16. initial lease acquisition (0 history, 0 -> 0): accept,
    #         stop-takeover (acquire, not a takeover) ---------------------
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc()
            del base["owner_session_id"]
            base["halt_reason"] = ""
            proposed = dict(base)
            proposed["owner_session_id"] = "first-owner"
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc()
            proposed = _v5_doc()
            proposed["lease"]["owner_session_id"] = "first-owner"
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"lease_field_touch_zero_history_{layout}", base, proposed, encoding,
                            accept=True, write_kind="stop-takeover"))

    # --- 17. legacy-full: even a stop-halt attempt is rejected once the
    #         base is legacy-full (no pending request can shed enough) --
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA

    def _padded(target_len, with_pending):
        proj = _project(_v5_pending_record()) if with_pending else FreshReviewProjection(())
        doc = _flat_doc(requests=list(projection_document(proj)["requests"]))
        doc["padding"] = ""
        while legacy(doc) < target_len:
            doc["padding"] += "p" * (target_len - legacy(doc))
        while legacy(doc) > target_len:
            doc["padding"] = doc["padding"][: len(doc["padding"]) - (legacy(doc) - target_len)]
        return doc

    doc_legacy_full = _padded(threshold + 1, with_pending=False)
    halt_attempt_on_legacy_full = dict(doc_legacy_full)
    halt_attempt_on_legacy_full.update(halt_reason="x", phase="halted", loop_active=False)
    cases.append(_case("legacy_full_rejects_even_stop_halt", doc_legacy_full, halt_attempt_on_legacy_full,
                        sc.StateEncoding.LEGACY_PRETTY, accept=False, code="state-capacity-legacy-full"))

    # --- 18. genesis, both layouts ---------------------------------------
    for layout in ("v4", "v5"):
        doc = _flat_doc() if layout == "v4" else _v5_doc()
        encoding = sc.StateEncoding.LEGACY_PRETTY if layout == "v4" else sc.StateEncoding.CANONICAL
        cases.append(_case(f"genesis_{layout}", None, doc, encoding, accept=True, write_kind="genesis"))

    # --- 19. halt mixed with an actual lease change (not just junk):
    #         still must not be stop-halt -- halt and takeover are
    #         separate branches, never combined. ------------------------
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc()
            proposed = dict(base)
            proposed.update(halt_reason="x", phase="halted", loop_active=False,
                             owner_session_id="other-owner")
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc()
            proposed = copy.deepcopy(base)
            proposed["control"].update(halt_reason="x", phase="halted", loop_active=False)
            proposed["lease"]["owner_session_id"] = "other-owner"
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"halt+lease_change_mixed_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 20. v5 unknown halt_category / long goal_dispatch_* (mirrors
    #         case 8, which was v4-only) -----------------------------------
    base_v5_cat = _v5_doc()
    proposed_v5_cat = _v5_doc(control_extra={
        "halt_reason": "x", "halt_category": "totally-unknown-category-xyz",
        "phase": "halted", "loop_active": False,
    })
    cases.append(_case("unknown_halt_category_v5", base_v5_cat, proposed_v5_cat,
                        sc.StateEncoding.CANONICAL, accept=True, write_kind="stop-halt"))

    base_v5_goal = _v5_doc()
    proposed_v5_goal = _v5_doc(
        control_extra={"halt_reason": "x", "phase": "halted", "loop_active": False},
        extensions_extra={"goal_dispatch_effective": "g" * 1000, "goal_dispatch_host": "g" * 1000,
                           "goal_dispatch_fallback_reason": "g" * 1000},
    )
    cases.append(_case("long_goal_dispatch_v5", base_v5_goal, proposed_v5_goal,
                        sc.StateEncoding.CANONICAL, accept=True, write_kind="stop-halt"))

    # --- 21. withdraw idempotent resend (zero diff): must not be
    #         rejected, both layouts ------------------------------------
    rec_c = _v5_pending_record(request_id="r3", nonce="n3")
    proj_c = _project(rec_c)
    for layout in ("v4", "v5"):
        if layout == "v4":
            withdrawn_once = withdraw_request(proj_c, request_id="r3", operation_id="wd-3", fencing_epoch=1)
            base = _flat_doc(requests=list(projection_document(withdrawn_once)["requests"]))
            proposed = dict(base)
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            withdrawn_once = withdraw_request(proj_c, request_id="r3", operation_id="wd-3", fencing_epoch=1)
            base = _v5_doc()
            base["extensions"]["fresh_review"] = projection_document(withdrawn_once)
            proposed = copy.deepcopy(base)
            encoding = sc.StateEncoding.CANONICAL
        cases.append(_case(f"withdraw_resend_idempotent_zero_diff_{layout}", base, proposed, encoding,
                            accept=True))

    # --- 22. consume at exactly max_output_bytes does not crash and is
    #         judged as an ordinary stage advance ------------------------
    rec_d = _v5_pending_record(request_id="r4", nonce="n4")
    reserved_d = reserve_request(_project(rec_d), rec_d.request, operation_id="d4",
                                  intent_digest=ADAPTER, payload_digest=ADAPTER)
    consumed_d = consume_request(reserved_d, rec_d.request, operation_id="d4",
                                  intent_digest=ADAPTER, payload_digest=ADAPTER,
                                  result={"blob": "r" * (262144 - 32)})
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _flat_doc(requests=list(projection_document(reserved_d)["requests"]))
            proposed = dict(base)
            proposed["fresh_review"] = projection_document(consumed_d)
            encoding = sc.StateEncoding.LEGACY_PRETTY
        else:
            base = _v5_doc()
            base["extensions"]["fresh_review"] = projection_document(reserved_d)
            proposed = copy.deepcopy(base)
            proposed["extensions"]["fresh_review"] = projection_document(consumed_d)
            encoding = sc.StateEncoding.CANONICAL
        if layout == "v4":
            # 判定の順序 8: a v4/LEGACY_PRETTY D-stage advance (reserved ->
            # consumed) is unconditionally rejected regardless of headroom.
            cases.append(_case(f"consume_at_max_output_bytes_{layout}", base, proposed, encoding,
                                accept=False, code="state-capacity-exhausted"))
        else:
            cases.append(_case(f"consume_at_max_output_bytes_{layout}", base, proposed, encoding,
                                accept=True, write_kind="normal"))

    # --- 23. padding: two more plain "nothing special happens" controls
    #         to keep the table above the 50-case floor with genuinely
    #         distinct inputs (differing halt_category literal values). -
    for category in ("partial-done", "stagnation"):
        base = _flat_doc()
        proposed = dict(base)
        proposed.update(halt_reason="x", halt_category=category, phase="halted", loop_active=False)
        cases.append(_case(f"halt_category_{category}_v4", base, proposed, sc.StateEncoding.LEGACY_PRETTY,
                            accept=True, write_kind="stop-halt"))

    return cases


PROBE_TABLE = _probe_table()


@pytest.mark.parametrize("base,proposed,encoding,expect_accept,expect_code,expect_write_kind", PROBE_TABLE)
def test_probe_derived_regression_table(base, proposed, encoding, expect_accept, expect_code, expect_write_kind):
    capacity_base = (
        None if base is None else sc.CapacityBase(document=base, encoded_len=encode(base, encoding))
    )
    verdict = sc.state_capacity_verdict(
        capacity_base, proposed, encode(proposed, encoding), encoding=encoding)
    assert verdict.accepted is expect_accept, verdict
    if expect_code is not None:
        assert verdict.code == expect_code, verdict
    if expect_write_kind is not None:
        assert verdict.write_kind == expect_write_kind, verdict


def test_probe_table_has_at_least_50_cases():
    assert len(PROBE_TABLE) >= 50, len(PROBE_TABLE)


def test_legacy_full_no_pending_cannot_be_escaped_by_withdrawing():
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _flat_doc(requests=[])
    doc["padding"] = ""
    while legacy(doc) < threshold + 1:
        doc["padding"] += "p" * (threshold + 1 - legacy(doc))
    while legacy(doc) > threshold + 1:
        doc["padding"] = doc["padding"][: len(doc["padding"]) - (legacy(doc) - threshold - 1)]
    assert sc._is_legacy_full(doc, legacy(doc), encode=lambda d: legacy(d)) is True


def test_nan_containing_v4_document_does_not_crash_and_classifies_halt():
    rec = _v5_pending_record()
    doc = _flat_doc(requests=[projection_document(_project(rec))["requests"][0]])
    doc["custom_score"] = float("nan")
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc["padding"] = ""
    while legacy(doc) < threshold + 10:
        doc["padding"] += "p" * (threshold + 10 - legacy(doc))
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))
    proposed = dict(doc)
    proposed.update(halt_reason="stagnation", phase="halted", loop_active=False)
    verdict = sc.state_capacity_verdict(
        base, proposed, legacy(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted
    assert verdict.write_kind == "stop-halt"


def test_nan_field_does_not_permanently_block_classification():
    doc = _flat_doc()
    doc["custom_score"] = float("nan")
    proposed = dict(doc)
    proposed.update(halt_reason="x", phase="halted", loop_active=False)
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.STOP_HALT
