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


def test_halt_delta_is_bounded_by_the_real_production_writers():
    """Δ_halt must bound the real v4 and v5 halt writers, not a hand builder.

    v4: drives ``activity_segments.record_activity_event`` /
    ``close_activity_for_terminal`` -- the same functions
    ``mission_application.lifecycle.mark_halt``'s real ``mutate()`` closure
    calls -- directly on a flat document, across both the ``routed-goal``
    (goal_dispatch_* fields) and ``awaiting-approval`` (extra activity
    close) branches.

    v5: drives ``mission_kernel.transitions.decide`` with a real
    ``MarkHalt`` command end-to-end, encoding the result with the real
    ``encode_v5_snapshot``. Its ``compatibility`` upserts model exactly
    what ``mission_application.compatibility.compatibility_delta`` would
    compute from the same before/after flat-document shapes (transitions.py
    mirrors ``halt_reason`` into both ``control`` *and* ``extensions``,
    which is why v5 dominates -- see the Δ_halt docstring).
    """
    import copy
    import dataclasses
    import importlib.util

    from activity_segments import (
        close_activity_for_terminal,
        record_activity_event,
        start_activity_segment,
    )
    from mission_kernel import decode_snapshot, encode_v5_snapshot
    from mission_kernel.commands import CompatibilityPayload, MarkHalt
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    goal_dispatch_upserts = {
        "goal_dispatch_effective": GOAL_FIELD_MAX,
        "goal_dispatch_host": GOAL_FIELD_MAX,
        "goal_dispatch_fallback_reason": GOAL_FIELD_MAX,
    }
    activity_close_upserts = {
        "activity_segments": [dict(CLOSED_SEGMENT)],
        "activity_rollup": {
            "observed_total_sec": CLOSED_SEGMENT["duration_sec"],
            "closed_segment_count": 1,
            "activity_duration_totals_sec": {CLOSED_SEGMENT["kind"]: CLOSED_SEGMENT["duration_sec"]},
            "phase_activity_duration_totals_sec": {
                CLOSED_SEGMENT["phase"]: {CLOSED_SEGMENT["kind"]: CLOSED_SEGMENT["duration_sec"]}
            },
            "wait_reason_totals_sec": {CLOSED_SEGMENT["kind"]: {CLOSED_SEGMENT["reason"]: CLOSED_SEGMENT["duration_sec"]}},
        },
    }

    # --- v4: real activity_segments writer, both halt-category branches ---
    def v4_delta(category, extra_upserts):
        base = {
            "schema_version": 4, "phase": "executing", "loop_active": True,
            "iteration": 5, "updated_at": TS27, "last_activity_at": TS27,
            "halt_reason": "",
        }
        start_activity_segment(
            base, CLOSED_SEGMENT["kind"], CLOSED_SEGMENT["reason"], TS27,
            detail=CLOSED_SEGMENT["detail"],
        )
        after = copy.deepcopy(base)
        if category == "awaiting-approval":
            record_activity_event(after, "awaiting-approval", TS27)
        close_activity_for_terminal(after, TS27, trusted_boundary=False)
        after["halt_reason"] = HALT_REASON_MAX
        after["halt_category"] = category
        after["loop_active"] = False
        after["phase"] = "halted"
        after["updated_at"] = TS27
        after["last_activity_at"] = TS27
        after.update(extra_upserts)
        return legacy(after) - legacy(base), canonical(after) - canonical(base)

    v4_routed = v4_delta("routed-goal", goal_dispatch_upserts)
    v4_awaiting = v4_delta("awaiting-approval", {})
    v4_max = max(v4_routed + v4_awaiting)
    assert v4_max <= sc.STATE_CAPACITY_HALT_DELTA

    # --- v5: real kernel decide()/MarkHalt, both dominant branches ---
    def v5_delta(category, compat_upserts):
        payload = current_v5_open_state()
        payload["control"]["halt_reason"] = ""
        payload["control"]["loop_active"] = True
        payload["control"]["phase"] = "executing"
        payload["control"]["terminal_outcome"] = None
        snap = decode_snapshot(canonical_json_bytes(payload))
        base_bytes = encode_v5_snapshot(snap)
        command = MarkHalt(
            HaltCategory(category),
            HALT_REASON_MAX,
            compatibility=CompatibilityPayload(upserts=freeze_json_value(compat_upserts)),
        )
        decision = decide(snap.state, command)
        assert decision.accepted, decision.rejection
        new_state = dataclasses.replace(
            decision.transition.new_state, snapshot_provenance=snap.provenance
        )
        object.__setattr__(new_state, "_snapshot_binding", snap.guidance._snapshot_binding)
        new_snap = dataclasses.replace(snap, state=new_state)
        after_bytes = encode_v5_snapshot(new_snap)
        return len(after_bytes) - len(base_bytes)

    both_upserts = dict(goal_dispatch_upserts)
    both_upserts.update(activity_close_upserts)
    v5_routed_with_close = v5_delta("routed-goal", both_upserts)
    v5_awaiting_with_close = v5_delta("awaiting-approval", activity_close_upserts)
    v5_max = max(v5_routed_with_close, v5_awaiting_with_close)
    assert v5_max <= sc.STATE_CAPACITY_HALT_DELTA
    # v5 is the dominant encoding (halt_reason is mirrored into both
    # control and extensions); this would fail if a future change made v4
    # dominate without the constant being revisited.
    assert v5_max > v4_max

@pytest.mark.parametrize("encoding_fn", [canonical, legacy], ids=["canonical", "legacy"])
def test_takeover_delta_pins_min_to_max_lease_replacement(encoding_fn):
    base = _lease_min([])
    after = _lease_max_after(base)
    delta = encoding_fn(after) - encoding_fn(base)
    assert delta <= sc.STATE_CAPACITY_TAKEOVER_DELTA
    if encoding_fn is legacy:
        assert delta == sc.STATE_CAPACITY_TAKEOVER_DELTA

def test_takeover_delta_is_bounded_by_the_real_production_writers():
    """Δ_takeover must bound the real v4 and v5 takeover writers.

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
    import importlib.util

    mission_state_py = str(_HERE.parent / "bin" / "mission-state.py")
    spec = importlib.util.spec_from_file_location(
        "mission_state_issue936_takeover", mission_state_py
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    old_lease = {
        "owner_session_id": ID128, "lease_id": "b" * 128, "fencing_epoch": 2 ** 63 - 2,
        "lease_expires_at": "2000-01-01T00:00:00Z", "lease_history": [],
    }
    before = copy.deepcopy(old_lease)
    after = copy.deepcopy(old_lease)
    mod.acquire_or_verify_lease(after, "c" * 128, lease_id="c" * 128, reason="r" * 128)

    d_legacy = legacy(after) - legacy(before)
    d_canonical = canonical(after) - canonical(before)
    assert max(d_legacy, d_canonical) <= sc.STATE_CAPACITY_TAKEOVER_DELTA

    v5_before = {"lease": {"kind": "fenced", **old_lease}}
    v5_after = {"lease": {"kind": "fenced", **after}}
    d_v5_canonical = canonical(v5_after) - canonical(v5_before)
    assert d_v5_canonical <= sc.STATE_CAPACITY_TAKEOVER_DELTA


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


def test_takeover_limit_is_largest_integer_within_system_share():
    assert sc.STATE_CAPACITY_SYSTEM_SHARE == sc.STATE_LIMIT // 16 if hasattr(sc, "STATE_LIMIT") else True
    n_l = sc.STATE_CAPACITY_TAKEOVER_LIMIT
    assert n_l >= 1
    assert (sc.STATE_CAPACITY_HALT_DELTA + n_l * sc.STATE_CAPACITY_TAKEOVER_DELTA
            <= sc.STATE_CAPACITY_SYSTEM_SHARE)
    assert (sc.STATE_CAPACITY_HALT_DELTA + (n_l + 1) * sc.STATE_CAPACITY_TAKEOVER_DELTA
            > sc.STATE_CAPACITY_SYSTEM_SHARE)

@pytest.mark.parametrize("stage", ["dispatch", "consume", "terminal"])
@pytest.mark.parametrize("encoding_name", ["canonical", "legacy"])
def test_stage_deltas_embed_e0a_shapes(stage, encoding_name):
    """Each stage Δ must bound a real record's encode-length growth from a
    minimal (record-just-accepted) state to the E0a-pinned maximum shape
    for that stage, measured under the ``encoding_name`` encoding -- not
    merely be larger than the shapes' own standalone bytes (``>`` against a
    literal does not confirm the constant accounts for the record's
    *reserved-slot key* and envelope growth, which is why this was flagged;
    see the docstring on ``_FRESH_REVIEW_KEY_SLACK``).

    ``legacy`` (v4 flat) is informational only for dispatch/consume/
    terminal: ``residual_reservation`` already returns 0 for *every* D item
    under ``StateEncoding.LEGACY_PRETTY`` (#918's "v4 の D item の予約は0"
    decision -- v4 is rejected before any D request can advance past
    ``pending``, so these stage Deltas are never actually consulted under
    legacy encoding). Asserting the bound there anyway would force this
    constant to grow for a code path ``residual_reservation`` never
    reaches, so legacy failures are reported, not asserted.
    """
    import warnings

    from .test_issue917_fresh_review_bounds import maximum_intent, maximum_running, maximum_terminal

    encode = canonical if encoding_name == "canonical" else legacy

    def record_doc(dispatch=None, running=None, terminal_receipt=None, result=None):
        # Top-level reserved-slot keys (``dispatch`` / ``running`` /
        # ``terminal_receipt``), matching ``_FRESH_REVIEW_KEY_SLACK``'s own
        # naming, rather than an extra nesting level this test invented --
        # an extra level would inflate legacy-pretty indentation beyond
        # what the real embedding (inside one array element of the D
        # request projection) actually costs.
        return _flat_doc(contract=_minimal_contract(), extra={
            "dispatch": dispatch, "running": running,
            "terminal_receipt": terminal_receipt, "result": result,
        })

    minimal = record_doc()
    if stage == "dispatch":
        after = record_doc(dispatch=maximum_intent(), running=maximum_running())
        delta_const = sc.FRESH_REVIEW_DISPATCH_STAGE_DELTA
    elif stage == "consume":
        after = record_doc(result="r" * 262144)
        delta_const = sc.FRESH_REVIEW_CONSUME_STAGE_DELTA
    else:
        largest_terminal = max(
            (maximum_terminal(outcome) for outcome in ("completed", "failed", "blocked", "abandoned-unknown")),
            key=lambda shape: encode(record_doc(terminal_receipt=shape)),
        )
        after = record_doc(terminal_receipt=largest_terminal)
        delta_const = sc.FRESH_REVIEW_TERMINAL_STAGE_DELTA

    measured = encode(after) - encode(minimal)
    if encoding_name == "legacy":
        if measured > delta_const:
            warnings.warn(
                f"{stage} legacy-pretty delta {measured} exceeds {delta_const}; "
                "informational only, see test docstring"
            )
        return
    assert measured <= delta_const, (stage, encoding_name, measured, delta_const)

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
