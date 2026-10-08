"""E0b-2b (#933): capacity-judgement kernel (write_kind, legacy-full, verdict).

Covers only the judgement half of the capacity scheme. The reservation half
(Delta constants, lineage variable part, system share, the two boolean
predicates) is tested in the prerequisite E0b-2a module
(test_issue936_state_capacity_reservation.py); this file reuses that
module's fixture builders rather than redefining them.
"""
from __future__ import annotations

import copy

import pytest

from mission_kernel.fresh_review import (
    FreshReviewProjection, FreshReviewRecord, consume_request,
    decode_request, canonical_digest, candidate_identity,
    projection_document, reserve_request, withdraw_request,
)
from mission_kernel import state_capacity as sc

from .test_issue895_fresh_review import ADAPTER, _pure_projection
from .test_issue936_state_capacity_reservation import (
    TS27, canonical, legacy, encode, _flat_doc, _minimal_contract,
    _pad_document, _pending_record,
)

# v5 document builders (the reservation-half tests only exercise v4/flat;
# the judgement half needs both layouts for every write_kind branch).

def _v5_request(request_id="request-1", nonce="nonce-1", criterion_ids=("AC1",)):
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

def _project(*records):
    return FreshReviewProjection(tuple(records))

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

def _big_pending(size):
    pending = _pending_record()
    doc = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    doc = dict(doc)
    doc["request"] = dict(doc["request"])
    doc["request"]["perspective"] = "p" * size
    return doc

def _over_capacity_doc(excess=100):
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _flat_doc(requests=[_big_pending(1)], halt_reason="")
    doc["padding"] = "p" * max(0, threshold - canonical(doc) + excess)
    while canonical(doc) < threshold + excess:
        doc["padding"] += "p"
    return doc

def _push_over_capacity(doc, encoding, *, pad_key="zzz_padding_zzz"):
    """Grow ``doc`` (deep-copied) with junk padding until over capacity."""
    doc = copy.deepcopy(doc)
    for _ in range(40):
        if sc.is_over_capacity(doc, encode(doc, encoding), encoding=encoding):
            return doc
        doc[pad_key] = doc.get(pad_key, "") + "p" * 200000
    assert sc.is_over_capacity(doc, encode(doc, encoding), encoding=encoding), "fixture did not reach over-capacity"
    return doc

# PROBE_TABLE compaction helpers: build a (base, encoding) pair for one
# layout, and apply a halt/lease mutation across both layouts uniformly.

def _base(layout, **kwargs):
    if layout == "v4":
        return _flat_doc(**kwargs), sc.StateEncoding.LEGACY_PRETTY
    return _v5_doc(**kwargs), sc.StateEncoding.CANONICAL

def _apply_control(doc, layout, **fields):
    if layout == "v4":
        return {**doc, **fields}
    out = copy.deepcopy(doc)
    out["control"].update(fields)
    return out

def _set_lease(doc, layout, *, history=None, owner=None, lease_id=None, epoch=None):
    out = dict(doc) if layout == "v4" else copy.deepcopy(doc)
    target = out if layout == "v4" else out["lease"]
    for key, value in (("lease_history", history), ("owner_session_id", owner),
                        ("lease_id", lease_id), ("fencing_epoch", epoch)):
        if value is not None:
            target[key] = value
    return out

def _entry(owner, lease_id, epoch, reason="lease-expired-takeover", at=TS27):
    return {"owner_session_id": owner, "lease_id": lease_id, "fencing_epoch": epoch,
            "reason": reason, "at": at}

def _no_lease_base(layout):
    """A base whose lease was genuinely never acquired (fencing_epoch
    absent/empty) -- the only shape ``_takeover_case`` admits as "initial".

    ``lease_expires_at`` is kept present (not dropped): main's redesigned
    ``_takeover_case`` requires the "initial" branch's *proposed* expiry to
    parse, and an initial acquisition only ever mutates identity fields, not
    the expiry -- so the acquisition's own expiry comes from this base.
    """
    base, encoding = _base(layout)
    if layout == "v4":
        base = dict(base, owner_session_id="", lease_id="", fencing_epoch="")
    else:
        base = copy.deepcopy(base)
        base["lease"] = {
            "owner_session_id": "", "lease_id": "", "fencing_epoch": "", "lease_history": [],
            "lease_expires_at": base["lease"]["lease_expires_at"],
        }
    return base, encoding

def _fencing_epoch(doc, layout):
    return (doc if layout == "v4" else doc["lease"])["fencing_epoch"]

def _set_fresh_review(doc, layout, projection):
    out = dict(doc) if layout == "v4" else copy.deepcopy(doc)
    projected = projection_document(projection)
    if layout == "v4":
        out["fresh_review"] = projected
    else:
        out["extensions"]["fresh_review"] = projected
    return out

# Lineage reserve boundary via the verdict (reservation-half pins the
# formula itself; this exercises it through state_capacity_verdict).

def _reserved_proposed(doc, pending):
    reserved = reserve_request(FreshReviewProjection((pending,)), pending.request,
                                operation_id="dispatch-1", intent_digest=ADAPTER, payload_digest=ADAPTER)
    proposed = dict(doc)
    proposed["fresh_review"] = projection_document(reserved)
    return proposed

def test_lineage_reserve_is_enforced_exactly_at_the_boundary_for_a_long_criterion():
    pending = _pending_record()
    long_contract = _minimal_contract(
        requirement_ids=["r" * 128] * 10, prohibited_side_effects=["p" * 64] * 10)
    rec = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    doc = _pad_document(status_records=[rec], contract=long_contract)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.lineage_stage_delta(doc, pending.request) > sc.FRESH_REVIEW_LINEAGE_STAGE_DELTA

    proposed = _reserved_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted, verdict

    over = dict(proposed)
    over["padding"] = over["padding"] + "q" * (sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA + 1)
    verdict = sc.state_capacity_verdict(base, over, canonical(over), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-invariant-broken"

def test_reserved_item_still_advances_after_halt_and_full_takeovers():
    pending = _pending_record()
    doc = _pad_document(status_records=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    doc["halt_reason"] = "stagnation"
    doc["lease_history"] = _history(sc.STATE_CAPACITY_TAKEOVER_LIMIT)
    doc["fencing_epoch"] = sc.STATE_CAPACITY_TAKEOVER_LIMIT + 1
    doc["lease_id"] = "current"
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.system_remaining(doc) == 0

    proposed = _reserved_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted, verdict
    assert verdict.write_kind == "normal"

def test_growth_past_the_pinned_stage_delta_is_invariant_broken_not_exhausted():
    pending = _pending_record()
    doc = _pad_document(status_records=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))

    proposed = _reserved_proposed(doc, pending)
    proposed["padding"] = proposed["padding"] + "q" * (sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA + 1)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-invariant-broken"

# The N_L-1/N_L/N_L+1 boundary through the verdict is pinned by
# PROBE_TABLE's "takeover_boundary_*" rows below (both layouts), so it is
# not duplicated here as a standalone test.

# Legacy-full.

def _pad_to(doc, target_len, encode_fn=canonical):
    doc = dict(doc)
    doc["padding"] = ""
    while encode_fn(doc) < target_len:
        doc["padding"] += "p" * (target_len - encode_fn(doc))
    while encode_fn(doc) > target_len:
        doc["padding"] = doc["padding"][: len(doc["padding"]) - (encode_fn(doc) - target_len)]
    return doc

def test_legacy_full_boundary_both_sides():
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _pad_to(_flat_doc(requests=[], halt_reason=""), threshold)
    assert sc._is_legacy_full(doc, canonical(doc), encode=canonical) is False

    over = dict(doc)
    over["padding"] = over["padding"] + "q"
    assert sc._is_legacy_full(over, canonical(over), encode=canonical) is True

def test_legacy_full_is_escaped_once_withdrawing_pendings_clears_the_threshold():
    pending = _pending_record()
    rec = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _pad_to(_flat_doc(requests=[rec], halt_reason=""), threshold + 1)
    assert sc._is_legacy_full(doc, canonical(doc), encode=canonical) is False

def test_legacy_full_rejects_every_write_kind():
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _flat_doc(requests=[_big_pending(threshold)], halt_reason="")
    base_len = canonical(doc)
    assert base_len > threshold
    base = sc.CapacityBase(document=doc, encoded_len=base_len)
    halted = _apply_control(doc, "v4", halt_reason="stagnation", phase="halted")
    verdict = sc.state_capacity_verdict(
        base, halted, canonical(halted), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-legacy-full"

def test_halt_written_normal_state_is_not_legacy_full_at_the_boundary():
    doc = _flat_doc(requests=[], halt_reason="stagnation")
    doc = _pad_to(doc, sc.STATE_LIMIT - sc.system_remaining(doc))
    assert sc.is_over_capacity(doc, canonical(doc)) is False

# Over-capacity stop-system behaviour.

def _with_pending(doc, pending):
    out = dict(doc)
    out["fresh_review"] = {"schema": "mission-fresh-review/1",
                            "requests": [projection_document(FreshReviewProjection((pending,)))["requests"][0]]}
    return out

def _withdrawn_proposed(doc, pending, epoch=1):
    withdrawn = withdraw_request(FreshReviewProjection((pending,)), request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=epoch)
    out = dict(doc)
    out["fresh_review"] = projection_document(withdrawn)
    return out

def test_over_capacity_halt_allowed_up_to_state_limit():
    doc = _over_capacity_doc(excess=10)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.is_over_capacity(doc, base.encoded_len)
    halted = _apply_control(doc, "v4", halt_reason="stagnation", phase="halted")
    verdict = sc.state_capacity_verdict(
        base, halted, canonical(halted), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "stop-halt"

def test_over_capacity_dispatch_is_rejected():
    pending = _pending_record()
    doc = _with_pending(_over_capacity_doc(excess=10), pending)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    proposed = _reserved_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"

def test_over_capacity_withdraw_is_allowed():
    pending = _pending_record()
    doc = _with_pending(_over_capacity_doc(excess=10), pending)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    proposed = _withdrawn_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "withdraw"

# Withdraw judgement (not-needed / invariant-broken).

def test_withdraw_not_needed_when_base_within_capacity():
    pending = _pending_record()
    doc = _flat_doc(requests=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    proposed = _withdrawn_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-withdraw-not-needed"

# A withdraw diff that genuinely fails to shrink is no longer reachable as
# WriteKind.WITHDRAW at all: main's redesigned classify_write_kind (#940)
# requires _encode_strictly_smaller as part of _is_withdraw_diff itself, so
# such a diff now classifies NORMAL and is rejected as
# state-capacity-exhausted before state_capacity_verdict's own "withdraw but
# not smaller" invariant-broken branch is ever reached (that branch catches
# only a caller-supplied encoded_len that disagrees with the real document,
# pinned by test_withdraw_invariant_broken_when_proposed_is_exactly_the_same_size
# below). The classify_write_kind-level shrink boundary itself is pinned by
# test_issue939_write_kind.py's test_withdraw_rejects_when_encode_length_is_exactly_equal.

# write_kind classification purity (mixed diffs, bogus fields, bound
# checks, undecodable-base fail-closed) is covered by
# test_issue939_write_kind.py (#940); not duplicated here. Only the
# verdict-level behaviour (accepted/code/write_kind through
# state_capacity_verdict) is pinned in this file.

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



def test_takeover_v4_undecodable_base_projection_fails_closed_to_an_advance():
    doc = _flat_doc()
    doc["fresh_review"] = "not-a-mapping"
    proposed = dict(doc)
    proposed["fresh_review"] = {"schema": "mission-fresh-review/1", "requests": []}
    assert sc._advances_a_d_stage(doc, proposed) is True


def test_legacy_full_is_never_returned_for_a_normal_state_within_capacity():
    # halt already written + every takeover already recorded => system_remaining
    # is 0, so a doc padded *past* the halt threshold can still be within
    # capacity (budget is the full STATE_LIMIT) -- exactly the shape that
    # distinguishes "guarded by base_over_capacity" from "always probed".
    doc = _flat_doc(requests=[], halt_reason="stagnation",
                     lease_history=_history(sc.STATE_CAPACITY_TAKEOVER_LIMIT))
    doc["fencing_epoch"] = sc.STATE_CAPACITY_TAKEOVER_LIMIT + 1
    assert sc.system_remaining(doc) == 0
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _pad_to(doc, threshold + 1, encode_fn=canonical)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.is_over_capacity(doc, base.encoded_len) is False
    proposed = dict(doc)
    proposed["last_activity_at"] = "9999-12-31T23:59:58Z"
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.code != "state-capacity-legacy-full"


def test_over_capacity_stop_halt_accepted_at_exactly_state_limit():
    doc = _flat_doc()
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    base = sc.CapacityBase(document=doc, encoded_len=threshold)
    assert sc.is_over_capacity(doc, base.encoded_len)
    halted = _apply_control(doc, "v4", halt_reason="stagnation", phase="halted")
    verdict = sc.state_capacity_verdict(base, halted, sc.STATE_LIMIT, encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "stop-halt"


def test_over_capacity_stop_takeover_accepted_at_exactly_the_halt_threshold():
    doc = _flat_doc(lease_history=_history(3))
    doc["fencing_epoch"] = 4
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    base = sc.CapacityBase(document=doc, encoded_len=threshold - 5)
    assert sc.is_over_capacity(doc, base.encoded_len)
    proposed = dict(doc)
    # Padding must stay a valid, later instant (not a raw digit appended to
    # the "Z" tail -- main's redesigned _lease_expiry_not_shortened fails
    # closed on an unparseable expiry).
    expiry = proposed["lease_expires_at"]
    proposed["lease_expires_at"] = expiry[:-1] + ".9" + "Z"
    verdict = sc.state_capacity_verdict(base, proposed, threshold, encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "stop-takeover"


def test_withdraw_invariant_broken_when_proposed_is_exactly_the_same_size():
    pending = _pending_record()
    doc = _with_pending(_over_capacity_doc(excess=10), pending)
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    proposed = _withdrawn_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, proposed, base.encoded_len, encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-invariant-broken"


def test_legacy_pretty_rejects_a_freshly_appended_non_pending_record():
    pending = _pending_record()
    reserved = reserve_request(FreshReviewProjection((pending,)), pending.request, operation_id="d1",
                                intent_digest=ADAPTER, payload_digest=ADAPTER)
    rec = projection_document(reserved)["requests"][0]
    doc = _flat_doc(requests=[])
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))
    proposed = dict(doc)
    proposed["fresh_review"] = {"schema": "mission-fresh-review/1", "requests": [rec]}
    verdict = sc.state_capacity_verdict(
        base, proposed, legacy(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


def test_physical_limit_check_does_not_misfire_at_exactly_state_limit():
    doc = _flat_doc(halt_reason="stagnation", lease_history=_history(sc.STATE_CAPACITY_TAKEOVER_LIMIT))
    doc["fencing_epoch"] = sc.STATE_CAPACITY_TAKEOVER_LIMIT + 1
    assert sc.system_remaining(doc) == 0
    verdict = sc.state_capacity_verdict(None, doc, sc.STATE_LIMIT, encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted


@pytest.mark.parametrize("layout", ["v4", "v5"])
@pytest.mark.parametrize("excess", [0, 1])
def test_legacy_full_withdraw_boundary_uses_current_epoch_and_maximum_id(layout, excess):
    pending = _v5_pending_record()
    doc, encoding = _base(layout)
    doc = _set_lease(_set_fresh_review(doc, layout, _project(pending)), layout, epoch=10**17)
    size = lambda value: encode(value, encoding)
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    # Independent oracle: the real reducer, at the current epoch and ID bound.
    withdrawn = withdraw_request(_project(pending), request_id=pending.request.request_id,
                                  operation_id="w" * 128, fencing_epoch=_fencing_epoch(doc, layout))
    reference_doc = _set_fresh_review(doc, layout, withdrawn)
    savings = size(doc) - size(reference_doc)
    assert savings > 0
    padded = _pad_to(doc, threshold + savings + excess, encode_fn=size)
    proposed = _set_fresh_review(padded, layout, withdrawn)
    assert size(proposed) == threshold + excess
    assert sc._withdraw_all_pending_len(padded, size(padded), encode=size) >= size(proposed)
    assert sc._is_legacy_full(padded, size(padded), encode=size) is bool(excess)
    verdict = sc.state_capacity_verdict(sc.CapacityBase(padded, size(padded)), proposed,
                                        size(proposed), encoding=encoding)
    assert verdict.accepted is (excess == 0), verdict
    assert verdict.code == ("state-capacity-legacy-full" if excess else None)


def test_dispatch_and_consume_still_advance_after_halt_and_full_takeovers():
    pending = _pending_record()
    doc = _pad_document(status_records=[projection_document(FreshReviewProjection((pending,)))["requests"][0]])
    doc["halt_reason"] = "stagnation"
    doc["lease_history"] = _history(sc.STATE_CAPACITY_TAKEOVER_LIMIT)
    doc["fencing_epoch"] = sc.STATE_CAPACITY_TAKEOVER_LIMIT + 1
    doc["lease_id"] = "current"
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.system_remaining(doc) == 0

    reserved_proposed = _reserved_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, reserved_proposed, canonical(reserved_proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted, verdict

    reserved_base = sc.CapacityBase(document=reserved_proposed, encoded_len=canonical(reserved_proposed))
    projection = FreshReviewProjection((pending,))
    reserved = reserve_request(projection, pending.request, operation_id="dispatch-1",
                                intent_digest=ADAPTER, payload_digest=ADAPTER)
    consumed = consume_request(reserved, pending.request, operation_id="dispatch-1",
                                intent_digest=ADAPTER, payload_digest=ADAPTER, result={"status": "ok"})
    consumed_proposed = dict(reserved_proposed)
    consumed_proposed["fresh_review"] = projection_document(consumed)
    verdict = sc.state_capacity_verdict(
        reserved_base, consumed_proposed, canonical(consumed_proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted, verdict
    assert verdict.write_kind == "normal"


def test_stop_takeover_capped_at_the_halt_threshold_not_the_physical_limit():
    """ex_other_thr_high: a non-halt stop kind only ever gets STATE_LIMIT -
    Δ_halt of extra headroom on an over-capacity base, never the full
    STATE_LIMIT a stop-halt gets."""
    doc = _flat_doc(lease_history=_history(3))
    doc["fencing_epoch"] = 4
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _pad_to(doc, threshold, encode_fn=canonical)
    assert canonical(doc) == threshold
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    assert sc.is_over_capacity(doc, base.encoded_len)
    proposed = dict(doc)
    proposed["lease_expires_at"] = proposed["lease_expires_at"] + "9"
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


def test_genesis_rejects_a_document_that_does_not_satisfy_capacity():
    doc = _flat_doc()
    budget = sc.STATE_LIMIT - sc.system_remaining(doc) - sc.residual_reservation(doc)
    over = _pad_to(doc, budget + 1, encode_fn=canonical)
    assert canonical(over) == budget + 1
    verdict = sc.state_capacity_verdict(
        None, over, canonical(over), encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


def test_physical_limit_still_rejects_withdraw_that_shrank_but_stays_over_the_limit():
    """phys_off/phys_ge: the STATE_LIMIT ceiling is an absolute cap, not just
    a consequence of satisfies_capacity -- a withdraw that genuinely shrank
    (smaller than an already-impossible base) must still be rejected if the
    proposed document itself is still physically too large."""
    pending = _pending_record()
    doc = _with_pending(_over_capacity_doc(excess=10), pending)
    base = sc.CapacityBase(document=doc, encoded_len=sc.STATE_LIMIT + 1000)
    proposed = _withdrawn_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, proposed, sc.STATE_LIMIT + 1, encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"


# A halt/takeover diff whose own values already violate the #918 bound
# (oversized halt reason, non-conformant token) must not be granted the
# stop-halt/stop-takeover exemption; this classification-purity property is
# covered by test_issue939_write_kind.py (#940). Pinned through the full
# verdict here by PROBE_TABLE's "*_on_over_capacity_base_*" rows instead.

# Idempotent / must-not-stop cases.

def test_withdraw_resend_is_idempotent_and_not_rejected_by_kernel_identity():
    pending = _pending_record()
    withdrawn = withdraw_request(FreshReviewProjection((pending,)), request_id=pending.request.request_id,
                                  operation_id="withdraw-1", fencing_epoch=1)
    resent = withdraw_request(withdrawn, request_id=pending.request.request_id,
                               operation_id="withdraw-1", fencing_epoch=1)
    assert resent == withdrawn

def test_lease_renewal_with_zero_growth_is_accepted_at_zero_headroom():
    doc = _pad_document()
    base = sc.CapacityBase(document=doc, encoded_len=canonical(doc))
    proposed = dict(doc)
    proposed["lease_expires_at"] = "9999-12-31T23:59:58Z"
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted

def test_genesis_with_no_lease_history_and_no_lease_is_accepted():
    doc = _flat_doc()
    verdict = sc.state_capacity_verdict(
        None, doc, canonical(doc), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted
    assert verdict.write_kind == "genesis"

def test_encoded_len_over_physical_limit_is_always_exhausted():
    doc = _flat_doc()
    verdict = sc.state_capacity_verdict(
        None, doc, sc.STATE_LIMIT + 1, encoding=sc.StateEncoding.CANONICAL)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"

# 判定の順序 8 (LEGACY_PRETTY): v4 flat D requests never advance; v4 D
# items reserve nothing; halt / takeover / prepare still go through.

def test_legacy_pretty_rejects_pending_to_reserved_dispatch():
    pending = _pending_record()
    rec = projection_document(FreshReviewProjection((pending,)))["requests"][0]
    doc = _flat_doc(requests=[rec])
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))
    proposed = _reserved_proposed(doc, pending)
    verdict = sc.state_capacity_verdict(
        base, proposed, legacy(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert not verdict.accepted
    assert verdict.code == "state-capacity-exhausted"

def test_legacy_pretty_still_allows_halt_takeover_and_prepare():
    doc = _flat_doc()
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))

    halted = _apply_control(doc, "v4", halt_reason="stagnation", phase="halted")
    verdict = sc.state_capacity_verdict(
        base, halted, legacy(halted), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted, verdict
    assert verdict.write_kind == "stop-halt"

    takeover = _set_lease(doc, "v4", history=list(doc["lease_history"]) + [_entry("owner-1", "lease-1", 1)],
                           owner="owner-2", lease_id="lease-2", epoch=2)
    verdict = sc.state_capacity_verdict(
        base, takeover, legacy(takeover), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted, verdict
    assert verdict.write_kind == "stop-takeover"

    prepared = _with_pending(doc, _pending_record())
    verdict = sc.state_capacity_verdict(
        base, prepared, legacy(prepared), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted, verdict
    assert verdict.metrics.reserved == 0

# Table-driven accept matrix.

def _basic_accept_case():
    doc = _flat_doc()
    return sc.CapacityBase(document=doc, encoded_len=canonical(doc)), dict(doc)

@pytest.mark.parametrize("mutator", [
    lambda d: d,
    lambda d: {**d, "updated_at": TS27},
    lambda d: {**d, "halt_reason": "x", "phase": "halted", "loop_active": False},
    lambda d: {**d, "owner_session_id": "owner-1", "lease_id": "lease-1"},
    lambda d: {**d, "last_activity_at": TS27},
    lambda d: {**d, "halt_reason": "a" * 2048},
    lambda d: {**d, "halt_category": "evidence-submitted"},
    lambda d: {**d, "goal_dispatch_effective": "g" * 128},
    lambda d: {**d, "lease_expires_at": "9999-12-31T23:59:58Z"},
    lambda d: {**d, "unrelated_benign_field": 1},
])
def test_normal_mutations_within_headroom_are_accepted(mutator):
    base, doc = _basic_accept_case()
    proposed = mutator(doc)
    verdict = sc.state_capacity_verdict(
        base, proposed, canonical(proposed), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted

# ======================================================================
# Probe-derived regression table (抜け穴探索で見つかった反例の固定化).
#
# v4 (flat/LEGACY_PRETTY) and v5 (nested/CANONICAL) are both exercised for
# every scenario that applies to both.
# ======================================================================

def _case(name, base, proposed, encoding, *, accept, code=None, write_kind=None):
    return pytest.param(base, proposed, encoding, accept, code, write_kind, id=name)

def _history(n):
    return [_entry("a", f"id{i}", i + 1) for i in range(n)]

def _probe_table():
    cases = []

    # --- 1. pure halt: accepted + stop-halt, both layouts ----------------
    for layout in ("v4", "v5"):
        base, encoding = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        cases.append(_case(f"pure_halt_{layout}", base, proposed, encoding, accept=True, write_kind="stop-halt"))

    # --- 2. halt + fresh_review record advance mixed in: must NOT be
    #        stop-halt (base pushed over capacity -- a misclassification
    #        would let the write through up to STATE_LIMIT). -------------
    rec = _v5_pending_record()
    for layout in ("v4", "v5"):
        raw_base, encoding = (
            (_flat_doc(requests=[projection_document(_project(rec))["requests"][0]]), sc.StateEncoding.LEGACY_PRETTY)
            if layout == "v4" else (_v5_doc(requests=[rec]), sc.StateEncoding.CANONICAL)
        )
        base = _push_over_capacity(raw_base, encoding)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        reserved = reserve_request(_project(rec), rec.request, operation_id="d1",
                                    intent_digest=ADAPTER, payload_digest=ADAPTER)
        proposed = _set_fresh_review(proposed, layout, reserved)
        cases.append(_case(f"halt+fresh_review_advance_mixed_{layout}", base, proposed, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

    # --- 3. halt + unrelated junk key mixed in: must NOT be stop-halt ----
    for layout in ("v4", "v5"):
        base, encoding = _base(layout)
        base = _push_over_capacity(base, encoding)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        if layout == "v4":
            proposed["unrelated_junk_field"] = "J" * 5000
        else:
            proposed["extensions"]["unrelated_junk"] = "J" * 5000
        cases.append(_case(f"halt+unrelated_junk_{layout}", base, proposed, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

    # --- 4. junk alone (no halt fields touched) --------------------------
    base_v5_only_junk = _push_over_capacity(_v5_doc(), sc.StateEncoding.CANONICAL)
    proposed_v5_only_junk = copy.deepcopy(base_v5_only_junk)
    proposed_v5_only_junk["extensions"]["unrelated_junk"] = "J" * 5000
    cases.append(_case("junk_only_no_halt_v5", base_v5_only_junk, proposed_v5_only_junk,
                        sc.StateEncoding.CANONICAL, accept=False, code="state-capacity-exhausted",
                        write_kind="normal"))

    # --- 5. loop_active-only change must not be stop-halt ----------------
    for layout in ("v4", "v5"):
        base, encoding = _base(layout)
        proposed = _apply_control(base, layout, loop_active=False)
        cases.append(_case(f"loop_active_only_{layout}", base, proposed, encoding, accept=True, write_kind="normal"))

    # --- 6. halt_slot already written: second write is "normal" ---------
    for layout in ("v4", "v5"):
        base, encoding = _base(layout, halt_reason="already-halted")
        proposed, _ = _base(layout, halt_reason="different-reason")
        cases.append(_case(f"halt_already_written_second_write_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 7. takeover + unrelated junk in extensions (v5): must NOT be
    #        stop-takeover -----------------------------------------------
    base_v5_to, encoding_to = _base("v5", lease_history=[])
    base_v5_to = _push_over_capacity(base_v5_to, encoding_to)
    proposed_v5_to_junk = _set_lease(base_v5_to, "v5", history=[_entry("a", "b", 1)],
                                      owner="c", lease_id="d", epoch=2)
    proposed_v5_to_junk["extensions"]["unrelated_junk"] = "J" * 5000
    cases.append(_case("takeover+junk_extensions_v5", base_v5_to, proposed_v5_to_junk,
                        sc.StateEncoding.CANONICAL, accept=False, code="state-capacity-exhausted",
                        write_kind="normal"))

    # --- 8. unknown halt_category / long goal_dispatch_* must not crash
    #        and must still be stop-halt, both layouts --------------------
    for layout in ("v4", "v5"):
        base, encoding = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False,
                                   halt_category="totally-unknown-category-xyz")
        cases.append(_case(f"unknown_halt_category_{layout}", base, proposed, encoding,
                            accept=True, write_kind="stop-halt"))

        goal_fields = {"goal_dispatch_effective": "g" * 128, "goal_dispatch_host": "g" * 128,
                       "goal_dispatch_fallback_reason": "g" * 128}
        base2, _ = _base(layout)
        proposed2 = _apply_control(base2, layout, halt_reason="x", phase="halted", loop_active=False,
                                    **(goal_fields if layout == "v4" else {}))
        if layout == "v5":
            proposed2["extensions"].update(goal_fields)
        cases.append(_case(f"long_goal_dispatch_{layout}", base2, proposed2, encoding,
                            accept=True, write_kind="stop-halt"))

    # --- 9. withdraw: pure (accept, over-capacity base) vs mixed with
    #        unrelated junk (reject) and a second record's advance
    #        (reject), both layouts. ---------------------------------------
    rec_a = _v5_pending_record(request_id="r1", nonce="n1")
    rec_b = _v5_pending_record(request_id="r2", nonce="n2")
    two_proj = _project(rec_a, rec_b)
    base, encoding = _base("v4", requests=projection_document(two_proj)["requests"])
    for survivor in (rec_a, rec_b):
        reserved = reserve_request(_project(survivor), survivor.request, operation_id="dispatch",
                                    intent_digest=ADAPTER, payload_digest=ADAPTER)
        cases.append(_case(f"v4_shrink_and_reserve_{survivor.request.request_id}", base,
                            _set_fresh_review(base, "v4", reserved), encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))
    cases.append(_case("v4_two_pending_stop_halt", base,
                        _apply_control(base, "v4", halt_reason="x", phase="halted"), encoding,
                        accept=True, write_kind="stop-halt"))
    for layout in ("v4", "v5"):
        if layout == "v4":
            base, encoding = _base(layout, requests=projection_document(two_proj)["requests"])
        else:
            base, encoding = _base(layout, requests=[rec_a, rec_b])
        base = _push_over_capacity(base, encoding)
        withdrawn = withdraw_request(two_proj, request_id="r1", operation_id="wd-1",
                                      fencing_epoch=_fencing_epoch(base, layout))
        proposed = _set_fresh_review(base, layout, withdrawn)
        cases.append(_case(f"withdraw_pure_{layout}", base, proposed, encoding,
                            accept=True, write_kind="withdraw"))

        proposed_junk = _set_fresh_review(base, layout, withdrawn)
        if layout == "v4":
            proposed_junk["unrelated_junk_field"] = "J" * 5000
        else:
            proposed_junk["extensions"]["unrelated_junk"] = "J" * 5000
        cases.append(_case(f"withdraw+junk_{layout}", base, proposed_junk, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

        mixed = reserve_request(withdrawn, rec_b.request, operation_id="d-r2",
                                 intent_digest=ADAPTER, payload_digest=ADAPTER)
        proposed_mixed = _set_fresh_review(base, layout, mixed)
        cases.append(_case(f"withdraw+other_record_advance_{layout}", base, proposed_mixed, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))

    # --- 10. withdraw_fencing_epoch mismatch: must not be withdraw ------
    single_proj = _project(_v5_pending_record())
    base_v5_epoch = _push_over_capacity(_v5_doc(requests=[_v5_pending_record()]), sc.StateEncoding.CANONICAL)
    tomb_mismatch = withdraw_request(single_proj, request_id="request-1", operation_id="wd-2",
                                      fencing_epoch=999)
    proposed_v5_epoch = _set_fresh_review(base_v5_epoch, "v5", tomb_mismatch)
    cases.append(_case("withdraw_epoch_mismatch_v5", base_v5_epoch, proposed_v5_epoch,
                        sc.StateEncoding.CANONICAL, accept=False, code="state-capacity-exhausted",
                        write_kind="normal"))

    # --- 11. takeover boundary (N_L-1 / N_L / N_L+1), both layouts ------
    n_l = sc.STATE_CAPACITY_TAKEOVER_LIMIT
    for count, label, expect_accept in [
        (n_l - 1, "below_limit", True), (n_l, "at_limit", False), (n_l + 1, "above_limit", False),
    ]:
        for layout in ("v4", "v5"):
            base, encoding = _base(layout, lease_history=_history(count))
            base = _set_lease(base, layout, epoch=count + 1)
            proposed = _set_lease(base, layout, history=_history(count) + [_entry("z", "zz", count + 1)],
                                    owner="z", lease_id="zz", epoch=count + 2)
            code = None if expect_accept else "state-capacity-exhausted"
            cases.append(_case(f"takeover_boundary_{label}_{layout}", base, proposed, encoding,
                                accept=expect_accept, code=code))

    # --- 12. lease extension (lease_expires_at only, zero history growth):
    #         accept, stop-takeover. The expiry must move *later*, not
    #         earlier (main's redesigned _lease_is_pure_renewal rejects a
    #         shortened expiry outright). ------------------------------------
    for layout in ("v4", "v5"):
        base, encoding = _base(layout, lease_history=_history(3))
        base = _set_lease(base, layout, epoch=4)
        (base if layout == "v4" else base["lease"])["lease_expires_at"] = "2026-01-01T00:00:00Z"
        if layout == "v4":
            proposed = {**base, "lease_expires_at": "9999-12-31T23:59:59Z"}
        else:
            proposed = copy.deepcopy(base)
            proposed["lease"]["lease_expires_at"] = "9999-12-31T23:59:59Z"
        cases.append(_case(f"lease_extension_zero_growth_{layout}", base, proposed, encoding,
                            accept=True, write_kind="stop-takeover"))

    # --- 12b. an owner/epoch change without history growth, when base
    #          already has a real lease, is NOT a legitimate stop-takeover
    #          mutation (it is neither an extension nor a takeover). -------
    for layout in ("v4", "v5"):
        base, encoding = _base(layout, lease_history=_history(3))
        base = _set_lease(base, layout, epoch=4)
        proposed = _set_lease(base, layout, owner="renewed-owner")
        cases.append(_case(f"lease_owner_change_without_history_growth_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 13. lease history SHRINK must not be stop-takeover -------------
    for layout in ("v4", "v5"):
        base, encoding = _base(layout, lease_history=_history(5))
        proposed = _set_lease(base, layout, history=_history(2))
        cases.append(_case(f"lease_history_shrink_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 14. lease history truncate-and-replace must not be stop-takeover
    replaced = [_entry("x", "yy", 99, reason="forged")] * 3
    for layout in ("v4", "v5"):
        base, encoding = _base(layout, lease_history=_history(3))
        proposed = _set_lease(base, layout, history=replaced)
        cases.append(_case(f"lease_history_replace_same_length_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 15. lease history jump by more than 1 entry must not be stop-takeover
    for layout in ("v4", "v5"):
        base, encoding = _base(layout, lease_history=_history(2))
        proposed = _set_lease(base, layout, history=_history(2) + _history(2))
        cases.append(_case(f"lease_history_jump_by_two_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 16. initial lease acquisition (base genuinely has no lease yet:
    #         fencing_epoch absent/empty, 0 history): accept, stop-takeover -
    for layout in ("v4", "v5"):
        base, encoding = _no_lease_base(layout)
        proposed = _set_lease(base, layout, owner="first-owner", lease_id="first-lease", epoch=1)
        cases.append(_case(f"lease_initial_acquisition_{layout}", base, proposed, encoding,
                            accept=True, write_kind="stop-takeover"))

    # --- 17. legacy-full: even a stop-halt attempt is rejected -----------
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc_legacy_full = _pad_to(_flat_doc(), threshold + 1, encode_fn=legacy)
    halt_attempt_on_legacy_full = _apply_control(doc_legacy_full, "v4", halt_reason="x", phase="halted",
                                                  loop_active=False)
    cases.append(_case("legacy_full_rejects_even_stop_halt", doc_legacy_full, halt_attempt_on_legacy_full,
                        sc.StateEncoding.LEGACY_PRETTY, accept=False, code="state-capacity-legacy-full"))

    # --- 18. genesis, both layouts ---------------------------------------
    for layout in ("v4", "v5"):
        doc = _flat_doc() if layout == "v4" else _v5_doc()
        encoding = sc.StateEncoding.LEGACY_PRETTY if layout == "v4" else sc.StateEncoding.CANONICAL
        cases.append(_case(f"genesis_{layout}", None, doc, encoding, accept=True, write_kind="genesis"))

    # --- 19. halt mixed with an actual lease change: still not stop-halt -
    for layout in ("v4", "v5"):
        base, encoding = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        proposed = _set_lease(proposed, layout, owner="other-owner")
        cases.append(_case(f"halt+lease_change_mixed_{layout}", base, proposed, encoding,
                            accept=True, write_kind="normal"))

    # --- 20. withdraw idempotent resend (zero diff), both layouts -------
    rec_c = _v5_pending_record(request_id="r3", nonce="n3")
    withdrawn_once = withdraw_request(_project(rec_c), request_id="r3", operation_id="wd-3", fencing_epoch=1)
    for layout in ("v4", "v5"):
        base, encoding = _base(layout)
        base = _set_fresh_review(base, layout, withdrawn_once)
        proposed = _set_fresh_review(base, layout, withdrawn_once)
        cases.append(_case(f"withdraw_resend_idempotent_zero_diff_{layout}", base, proposed, encoding,
                            accept=True))

    # --- 21. consume at exactly max_output_bytes: ordinary stage advance -
    rec_d = _v5_pending_record(request_id="r4", nonce="n4")
    reserved_d = reserve_request(_project(rec_d), rec_d.request, operation_id="d4",
                                  intent_digest=ADAPTER, payload_digest=ADAPTER)
    consumed_d = consume_request(reserved_d, rec_d.request, operation_id="d4",
                                  intent_digest=ADAPTER, payload_digest=ADAPTER,
                                  result={"blob": "r" * (262144 - 32)})
    for layout, expect_accept in (("v4", False), ("v5", True)):
        base, encoding = _base(layout)
        base = _set_fresh_review(base, layout, reserved_d)
        proposed = _set_fresh_review(base, layout, consumed_d)
        cases.append(_case(f"consume_at_max_output_bytes_{layout}", base, proposed, encoding,
                            accept=expect_accept,
                            code=None if expect_accept else "state-capacity-exhausted",
                            write_kind="normal" if expect_accept else None))

    # --- 22. padding with distinct halt_category literals ---------------
    for category in ("partial-done", "stagnation"):
        base, encoding = _base("v4")
        proposed = _apply_control(base, "v4", halt_reason="x", halt_category=category,
                                   phase="halted", loop_active=False)
        cases.append(_case(f"halt_category_{category}_v4", base, proposed, encoding,
                            accept=True, write_kind="stop-halt"))

    # --- 23. new in #933: an over-bound halt/takeover diff on an
    #         over-capacity base must be rejected (classified normal, not
    #         granted the stop-halt/stop-takeover over-capacity exemption)
    base_v4_bound = _push_over_capacity(_flat_doc(), sc.StateEncoding.LEGACY_PRETTY)
    proposed_v4_bound = _apply_control(base_v4_bound, "v4", halt_reason="\x01" * (sc.HALT_REASON_MAX_CHARS + 1),
                                        phase="halted", loop_active=False)
    cases.append(_case("oversized_halt_reason_on_over_capacity_base_v4",
                        base_v4_bound, proposed_v4_bound, sc.StateEncoding.LEGACY_PRETTY,
                        accept=False, code="state-capacity-exhausted", write_kind="normal"))

    base_v5_to_bound = _push_over_capacity(_v5_doc(lease_history=[]), sc.StateEncoding.CANONICAL)
    proposed_v5_to_bound = _set_lease(base_v5_to_bound, "v5", history=[_entry("a", "b", 1)],
                                       owner="not a valid token!!", lease_id="d", epoch=2)
    cases.append(_case("non_conformant_takeover_token_on_over_capacity_base_v5",
                        base_v5_to_bound, proposed_v5_to_bound, sc.StateEncoding.CANONICAL,
                        accept=False, code="state-capacity-exhausted", write_kind="normal"))

    for layout in ("v4", "v5"):
        for defect in ("displaced_owner", "new_lease_id"):
            base, encoding = _base(layout)
            base = _set_lease(base, layout, owner="bad owner" if defect == "displaced_owner" else "a", lease_id="b")
            base = _push_over_capacity(base, encoding)
            old_owner = (base if layout == "v4" else base["lease"])["owner_session_id"]
            proposed = _set_lease(base, layout, owner="c", epoch=2,
                                  lease_id="bad lease" if defect == "new_lease_id" else "d",
                                  history=[_entry(old_owner, "b", 1)])
            cases.append(_case(f"takeover_non_conformant_{defect}_{layout}", base, proposed, encoding,
                                accept=False, code="state-capacity-exhausted", write_kind="normal"))

    # A halt carries the command's lease renewal (expiry only) and stays a halt on an
    # over-capacity base; a lease identity change with it does not. An empty diff is no stop.
    for layout in ("v4", "v5"):
        base, encoding = _base(layout)
        base = _push_over_capacity(base, encoding)
        # Give the base an expiry with room to extend (main's redesigned
        # _lease_is_pure_renewal/_takeover_case now reject a *shortened*
        # expiry outright -- see docs/design/880-repair-lineage.md's
        # "許しすぎ" round-1 fix -- so the renewal below must move the
        # expiry *later*, not earlier).
        (base if layout == "v4" else base["lease"])["lease_expires_at"] = "2026-01-01T00:00:00Z"
        renewed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        (renewed if layout == "v4" else renewed["lease"])["lease_expires_at"] = "9999-12-31T23:59:59Z"
        cases.append(_case(f"halt+lease_renewal_{layout}", base, renewed, encoding, accept=True, write_kind="stop-halt"))
        swapped = _set_lease(renewed, layout, lease_id="other-lease")
        cases.append(_case(f"halt+lease_swap_{layout}", base, swapped, encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))
        cases.append(_case(f"empty_diff_{layout}", base, copy.deepcopy(base), encoding,
                            accept=False, code="state-capacity-exhausted", write_kind="normal"))
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

def test_nan_containing_v4_document_does_not_crash_and_classifies_halt():
    rec = _v5_pending_record()
    doc = _flat_doc(requests=[projection_document(_project(rec))["requests"][0]])
    doc["custom_score"] = float("nan")
    threshold = sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA
    doc = _pad_to(doc, threshold + 10, encode_fn=legacy)
    base = sc.CapacityBase(document=doc, encoded_len=legacy(doc))
    proposed = _apply_control(doc, "v4", halt_reason="stagnation", phase="halted", loop_active=False)
    verdict = sc.state_capacity_verdict(
        base, proposed, legacy(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)
    assert verdict.accepted
    assert verdict.write_kind == "stop-halt"

# NaN-field classification purity (the standalone classify_write_kind
# property) is covered by test_issue939_write_kind.py (#940); the verdict
# half's NaN handling is pinned above by
# test_nan_containing_v4_document_does_not_crash_and_classifies_halt.
