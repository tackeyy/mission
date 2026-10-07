"""E0b-2a (#936): capacity-reservation kernel Delta constants and derivation.

Covers only the reservation half of the capacity scheme (constants, the
lineage variable part, the halt-slot and lease-takeover system share, and
the two boolean capacity predicates). The verdict half (``write_kind``,
legacy-full detection, ``state_capacity_verdict`` itself, and the
PROBE_TABLE regression suite) is tested in the follow-up E0b-2b module.
"""
from __future__ import annotations

import json

import pytest

from mission_kernel.json_codec import encode_json_value, freeze_json_value
from mission_kernel import state_capacity as sc

from .test_issue895_fresh_review import _pure_projection


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
    base = _flat_halt_base(halt_reason_absent=halt_reason_absent)
    after = _flat_halt_after(base)
    delta = encoding_fn(after) - encoding_fn(base)
    assert delta <= sc.STATE_CAPACITY_HALT_DELTA
    # The absent + legacy-pretty combination is the one that actually
    # reaches the pinned maximum; every other combination stays below it.
    if halt_reason_absent and encoding_fn is legacy:
        assert delta == sc.STATE_CAPACITY_HALT_DELTA

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
