"""E0b-2b-前半 (#939): write_kind classification from a base/proposed diff.

Covers only ``classify_write_kind`` and its helpers (diff comparison, the
lease-transition judgement, and the withdraw/reducer cross-check) -- never
the caller's say-so, always derived from the base/proposed diff. The
judgement half that *uses* the classification (legacy-full detection, and
``state_capacity_verdict`` itself) is a follow-up module (E0b-2b-後半/#933)
that imports this one; its own regression table is not duplicated here.

PR #938's round-1 design enumerated *which other keys* may change alongside
a halt/takeover by name (``HALT_AUX_KEYS``). Independent review (Codex and
an independent Checker) found that design simultaneously too permissive
(no bound on the admitted keys' own *values*; ``1``/``True`` treated as the
same value; a lease renewal allowed to shorten the expiry; an extra field
allowed onto an appended ``lease_history`` entry) and too restrictive (a
real writer's halt touches fields the list did not name, and was
misclassified ``normal``). #939 replaces the list with a *structural
signature* (does the diff match the shape a legitimate halt/takeover/
withdraw write produces) plus an *increment cap* (the real encode-length
increase never exceeds the Δ the reservation scheme already prices that
write at), folded directly into ``classify_write_kind`` via its new
``encoding`` parameter. This file tests that redesign; it does not keep
the old allow-list-era cases that no longer apply (e.g. a small unrelated
field riding along a halt, which the new design tolerates as long as it
stays inside Δ_halt -- the bound, not a field name, is what guards it).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import types
from datetime import datetime, timezone

import pytest

from mission_kernel.fresh_review import (
    FreshReviewProjection, FreshReviewRecord, candidate_identity,
    canonical_digest, decode_request, projection_document, reserve_request,
    withdraw_request,
)
from mission_kernel.model import FencedLease, HaltCategory, LegacyAbsentLease
from mission_kernel import state_capacity as sc

from .test_issue895_fresh_review import ADAPTER
from .test_issue936_state_capacity_reservation import (
    TS27, _flat_doc, _flat_halt_before_after, _minimal_contract,
    _mission_state_module,
)


# ---------------------------------------------------------------------------
# v5 document builders (the reservation-half fixtures only exercise v4/flat;
# write_kind classification needs both layouts for every branch).
# ---------------------------------------------------------------------------


def _v5_request(request_id="request-1", nonce="nonce-1", criterion_ids=("AC1",)):
    input_digest = canonical_digest({"input": "fixture", "id": request_id})
    commands = {f"command-{cid}": ADAPTER for cid in criterion_ids}
    bindings = [{"criterion_id": cid, "role": "verification", "command_id": f"command-{cid}",
                 "definition_digest": ADAPTER, "snapshot_digest": ADAPTER} for cid in criterion_ids]
    return decode_request({
        "schema": "mission-fresh-review-request/1", "request_id": request_id, "nonce": nonce,
        "mission_id": "mission-1", "session_id": "session-1", "requirement_digest": ADAPTER,
        "contract_digest": ADAPTER, "verifier_policy_digest": ADAPTER,
        "candidate_digest": candidate_identity(commands), "input_digest": input_digest,
        "adapter_registration_digest": ADAPTER,
        "candidate_bindings": bindings,
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
            lease_expires_at="9999-12-31T23:59:59Z", control_extra=None, extensions_extra=None, contract=None):
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
                  "lease_expires_at": lease_expires_at, "lease_history": list(lease_history)},
        "updated_at": TS27, "last_activity_at": TS27,
        "extensions": extensions,
    }


def _base(layout, *, lease_expires_at=None, **kwargs):
    if layout == "v4":
        if lease_expires_at is not None:
            kwargs["extra"] = {**(kwargs.get("extra") or {}), "lease_expires_at": lease_expires_at}
        return _flat_doc(**kwargs)
    if lease_expires_at is not None:
        kwargs["lease_expires_at"] = lease_expires_at
    return _v5_doc(**kwargs)


def _apply_control(doc, layout, **fields):
    if layout == "v4":
        return {**doc, **fields}
    out = copy.deepcopy(doc)
    out["control"].update(fields)
    return out


def _set_lease(doc, layout, *, history=None, owner=None, lease_id=None, epoch=None, expires_at=None):
    out = dict(doc) if layout == "v4" else copy.deepcopy(doc)
    target = out if layout == "v4" else out["lease"]
    for key, value in (("lease_history", history), ("owner_session_id", owner),
                        ("lease_id", lease_id), ("fencing_epoch", epoch),
                        ("lease_expires_at", expires_at)):
        if value is not None:
            target[key] = value
    return out


def _entry(owner, lease_id, epoch, reason="lease-expired-takeover", at=TS27):
    return {"owner_session_id": owner, "lease_id": lease_id, "fencing_epoch": epoch,
            "reason": reason, "at": at}


def _history(n):
    return [_entry("a", f"id{i}", i + 1) for i in range(n)]


def _no_lease_base(layout):
    """A base whose lease was genuinely never acquired (fencing_epoch
    absent/empty, 0 history) -- the only shape ``_takeover_case`` admits as
    "initial"."""
    base = _base(layout)
    if layout == "v4":
        base = dict(base, owner_session_id="", lease_id="", fencing_epoch="")
    else:
        base = copy.deepcopy(base)
        base["lease"] = {"owner_session_id": "", "lease_id": "", "fencing_epoch": "", "lease_history": []}
    return base


def _set_fresh_review(doc, layout, projection):
    out = dict(doc) if layout == "v4" else copy.deepcopy(doc)
    projected = projection_document(projection)
    if layout == "v4":
        out["fresh_review"] = projected
    else:
        out["extensions"]["fresh_review"] = projected
    return out


def _mk_request(request_id, nonce, criterion_ids=("AC1",)):
    return _v5_request(request_id=request_id, nonce=nonce, criterion_ids=criterion_ids)


# ===========================================================================
# 1. Real writers: every scenario a *legitimate* diff must classify as.
# ===========================================================================


@pytest.mark.parametrize("category", [c.value for c in HaltCategory])
def test_v4_real_halt_writer_is_stop_halt_for_every_category(category):
    before, after = _flat_halt_before_after(category)
    assert sc.classify_write_kind(before, after, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.STOP_HALT


def _v5_halt_before_after(category, *, reason="\x01" * sc.HALT_REASON_MAX_CHARS):
    from mission_application.compatibility import compatibility_delta
    from mission_kernel import decode_snapshot, encode_v5_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["control"]["halt_reason"] = ""
    payload["control"]["loop_active"] = True
    payload["control"]["phase"] = "executing"
    payload["control"]["terminal_outcome"] = None
    snap = decode_snapshot(canonical_json_bytes(payload))
    before_doc = json.loads(encode_v5_snapshot(snap))

    compat = compatibility_delta(payload, payload, exclude=set())
    command = MarkHalt(HaltCategory(category), reason, legacy_reason=reason,
                        compatibility=compat, at="2026-01-01T00:00:00Z")
    decision = decide(snap.state, command)
    assert decision.accepted, (category, decision.rejection)
    new_state = dataclasses.replace(decision.transition.new_state, snapshot_provenance=snap.provenance)
    object.__setattr__(new_state, "_snapshot_binding", snap.guidance._snapshot_binding)
    new_snap = dataclasses.replace(snap, state=new_state)
    after_doc = json.loads(encode_v5_snapshot(new_snap))
    return before_doc, after_doc


@pytest.mark.parametrize("category", [c.value for c in HaltCategory])
def test_v5_real_halt_writer_is_stop_halt_for_every_category(category):
    before, after = _v5_halt_before_after(category)
    assert sc.classify_write_kind(before, after, encoding=sc.StateEncoding.CANONICAL) == sc.WriteKind.STOP_HALT


def test_v4_real_halt_writer_at_max_dispatch_fields_is_still_stop_halt():
    """``routed-goal`` with every ``goal_dispatch_*`` field worst-cased at
    its own bound -- the dominant real-writer shape ``STATE_CAPACITY_HALT_
    DELTA`` is measured from.
    """
    before, after = _flat_halt_before_after("routed-goal")
    assert sc.classify_write_kind(before, after, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.STOP_HALT


def test_v4_real_lease_initial_acquisition_is_stop_takeover():
    mod = _mission_state_module()
    base = {"schema_version": 4, "phase": "executing", "loop_active": True, "halt_reason": ""}
    after = copy.deepcopy(base)
    mod.acquire_or_verify_lease(after, "owner-1")
    assert sc.classify_write_kind(base, after, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.STOP_TAKEOVER


def test_v4_real_lease_renewal_is_stop_takeover():
    mod = _mission_state_module()
    base = {
        "schema_version": 4, "phase": "executing", "loop_active": True, "halt_reason": "",
        "owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 5,
        "lease_expires_at": "2020-01-01T00:00:00Z", "lease_history": [],
    }
    after = copy.deepcopy(base)
    mod.acquire_or_verify_lease(after, "owner-1", lease_id="lease-1")
    assert sc.classify_write_kind(base, after, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.STOP_TAKEOVER


def test_v4_real_lease_first_and_second_takeover_are_stop_takeover():
    mod = _mission_state_module()
    base = {
        "schema_version": 4, "phase": "executing", "loop_active": True, "halt_reason": "",
        "owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 5,
        "lease_expires_at": "2000-01-01T00:00:00Z", "lease_history": [],
    }
    after1 = copy.deepcopy(base)
    mod.acquire_or_verify_lease(after1, "owner-2", lease_id="lease-2", reason="lease-expired-takeover")
    assert sc.classify_write_kind(base, after1, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.STOP_TAKEOVER

    after1["lease_expires_at"] = "2000-01-01T00:00:00Z"  # force expiry so a second takeover is admitted
    after2 = copy.deepcopy(after1)
    mod.acquire_or_verify_lease(after2, "owner-3", lease_id="lease-3", reason="lease-expired-takeover")
    assert sc.classify_write_kind(after1, after2, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.STOP_TAKEOVER


def _v5_lease_doc(lease_mapping):
    return {
        "schema_version": 5,
        "control": {"phase": "executing", "loop_active": True, "terminal_outcome": None, "halt_category": None,
                    "halt_reason": None},
        "lease": lease_mapping,
        "extensions": {},
    }


def test_v5_real_lease_initial_acquisition_is_stop_takeover():
    from mission_persistence.fenced_commit import admit_lease, _lease_document

    req = types.SimpleNamespace(lease_owner_session_id="owner-1", presented_lease_id=None)
    pending = admit_lease(req, LegacyAbsentLease(), datetime(2026, 1, 1, tzinfo=timezone.utc), 900)
    base_doc = _v5_lease_doc(_lease_document(LegacyAbsentLease()))
    after_doc = _v5_lease_doc(_lease_document(pending.target))
    assert sc.classify_write_kind(base_doc, after_doc, encoding=sc.StateEncoding.CANONICAL) == sc.WriteKind.STOP_TAKEOVER


def test_v5_real_lease_renewal_is_stop_takeover():
    from mission_persistence.fenced_commit import admit_lease, _lease_document

    base_lease = FencedLease("owner-1", "lease-1", 5, "2020-01-01T00:00:00Z", ())
    req = types.SimpleNamespace(lease_owner_session_id="owner-1", presented_lease_id="lease-1")
    pending = admit_lease(req, base_lease, datetime(2026, 1, 1, tzinfo=timezone.utc), 900)
    base_doc = _v5_lease_doc(_lease_document(base_lease))
    after_doc = _v5_lease_doc(_lease_document(pending.target))
    assert sc.classify_write_kind(base_doc, after_doc, encoding=sc.StateEncoding.CANONICAL) == sc.WriteKind.STOP_TAKEOVER


def test_v5_real_lease_takeover_is_stop_takeover():
    from mission_persistence.fenced_commit import admit_lease, _lease_document

    base_lease = FencedLease("owner-1", "lease-1", 5, "2000-01-01T00:00:00Z", ())
    req = types.SimpleNamespace(lease_owner_session_id="owner-2", presented_lease_id=None)
    pending = admit_lease(req, base_lease, datetime(2026, 1, 1, tzinfo=timezone.utc), 900)
    base_doc = _v5_lease_doc(_lease_document(base_lease))
    after_doc = _v5_lease_doc(_lease_document(pending.target))
    assert sc.classify_write_kind(base_doc, after_doc, encoding=sc.StateEncoding.CANONICAL) == sc.WriteKind.STOP_TAKEOVER


def _withdraw_v5_docs(*, lease_before, lease_after):
    req = _mk_request("request-1", "nonce-1")
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1",
                                  fencing_epoch=lease_after.get("fencing_epoch"))
    base_doc = {
        "schema_version": 5,
        "control": {"phase": "executing", "loop_active": True, "terminal_outcome": None, "halt_category": None,
                    "halt_reason": None},
        "lease": lease_before,
        "extensions": {"fresh_review": projection_document(proj)},
    }
    after_doc = {
        "schema_version": 5,
        "control": dict(base_doc["control"]),
        "lease": lease_after,
        "extensions": {"fresh_review": projection_document(withdrawn)},
    }
    return base_doc, after_doc


def test_withdraw_real_reducer_output_with_fence_bump_is_withdraw():
    lease_before = {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 1,
                     "lease_expires_at": "2026-01-01T00:00:00Z", "lease_history": []}
    lease_after = {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 2,
                    "lease_expires_at": "2026-01-01T00:00:01Z", "lease_history": []}
    base_doc, after_doc = _withdraw_v5_docs(lease_before=lease_before, lease_after=lease_after)
    assert sc.classify_write_kind(base_doc, after_doc, encoding=sc.StateEncoding.CANONICAL) == sc.WriteKind.WITHDRAW


def test_withdraw_real_reducer_output_with_plain_renewal_is_withdraw():
    lease_before = {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 1,
                     "lease_expires_at": "2026-01-01T00:00:00Z", "lease_history": []}
    lease_after = {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 1,
                    "lease_expires_at": "2026-01-01T00:00:01Z", "lease_history": []}
    base_doc, after_doc = _withdraw_v5_docs(lease_before=lease_before, lease_after=lease_after)
    assert sc.classify_write_kind(base_doc, after_doc, encoding=sc.StateEncoding.CANONICAL) == sc.WriteKind.WITHDRAW


def test_withdraw_v4_flat_save_encode_length_decreases():
    rec_a = _v5_pending_record(request_id="r1", nonce="n1")
    rec_b = _v5_pending_record(request_id="r2", nonce="n2")
    proj = _project(rec_a, rec_b)
    withdrawn = withdraw_request(proj, request_id="r1", operation_id="wd-1", fencing_epoch=1)
    base = _flat_doc(requests=projection_document(proj)["requests"])
    proposed = _set_fresh_review(base, "v4", withdrawn)
    assert sc.classify_write_kind(base, proposed, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.WITHDRAW


def test_withdraw_real_output_at_many_criteria_is_still_withdraw():
    """A tombstone replacing a pending record with 10 criterion_ids (so the
    shared ``criterion_ids`` field itself is large) is still strictly
    smaller than the pending record it replaces.
    """
    criterion_ids = tuple(f"AC{i}" for i in range(10))
    req = _v5_request(criterion_ids=criterion_ids)
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1", fencing_epoch=1)
    base = _v5_doc(requests=[rec])
    proposed = _set_fresh_review(base, "v5", withdrawn)
    assert sc.classify_write_kind(base, proposed, encoding=sc.StateEncoding.CANONICAL) == sc.WriteKind.WITHDRAW


def test_genesis_both_layouts():
    assert sc.classify_write_kind(None, _flat_doc()) == sc.WriteKind.GENESIS
    assert sc.classify_write_kind(None, _v5_doc()) == sc.WriteKind.GENESIS


# ===========================================================================
# 2. Decoys: diffs that must NOT be granted a stop-kind/withdraw exemption.
# ===========================================================================


def test_second_halt_write_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, halt_reason="already-halted")
        proposed = _base(layout, halt_reason="different-reason")
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_halt_and_takeover_mixed_in_one_diff_is_normal():
    doc = _flat_doc()
    proposed = dict(doc)
    proposed["halt_reason"] = "stagnation"
    proposed["phase"] = "halted"
    proposed["lease_history"] = list(doc["lease_history"]) + [_entry("owner-1", "lease-1", 1)]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 2
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_halt_with_lease_identity_change_mixed_in_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        proposed = _set_lease(proposed, layout, owner="other-owner")
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_halt_increment_over_delta_halt_is_normal():
    doc = _flat_doc()
    proposed = {**doc, "halt_reason": "\x01" * (sc.HALT_REASON_MAX_CHARS * 20),
                "phase": "halted", "loop_active": False}
    assert sc.classify_write_kind(doc, proposed, encoding=sc.StateEncoding.LEGACY_PRETTY) == sc.WriteKind.NORMAL


@pytest.mark.parametrize("mutator", [
    lambda d: {**d, "halt_reason": "\x01" * (sc.HALT_REASON_MAX_CHARS + 1), "phase": "halted", "loop_active": False},
    lambda d: {**d, "halt_reason": "x", "phase": "halted", "loop_active": False,
               "goal_dispatch_effective": "g" * (sc.GOAL_DISPATCH_REASON_MAX_CHARS + 1)},
])
def test_over_bound_halt_value_is_classified_normal(mutator):
    doc = _flat_doc(lease_history=[])
    assert sc.classify_write_kind(doc, mutator(doc)) == sc.WriteKind.NORMAL


@pytest.mark.parametrize("mutator", [
    lambda d: {**d, "lease_history": [_entry("owner-1", "lease-1", 1)], "owner_session_id": "bad owner with spaces",
               "lease_id": "lease-2", "fencing_epoch": 2},
    lambda d: {**d, "lease_history": [_entry("owner-1", "lease-1", 1)], "owner_session_id": "owner-2",
               "lease_id": "lease-2", "fencing_epoch": sc.LEASE_EPOCH_MAX + 1},
])
def test_over_bound_takeover_value_is_classified_normal(mutator):
    doc = _flat_doc(lease_history=[])
    assert sc.classify_write_kind(doc, mutator(doc)) == sc.WriteKind.NORMAL


def test_lease_history_jump_by_two_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(2))
        proposed = _set_lease(base, layout, history=_history(2) + _history(2))
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_lease_history_shrink_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(5))
        proposed = _set_lease(base, layout, history=_history(2))
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_lease_history_existing_entry_rewritten_including_1_to_true_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(3))
        base = _set_lease(base, layout, epoch=4)
        tampered = _history(3)
        tampered[0] = dict(tampered[0], fencing_epoch=True)  # 1 -> True: must not read as unchanged
        proposed = _set_lease(base, layout, history=tampered + [_entry("a", "lease-1", 4)],
                               owner="owner-2", lease_id="lease-2", epoch=5)
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_takeover_appended_entry_with_extra_field_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=[])
        entry = dict(_entry("owner-1", "lease-1", 1), extra_field="x")
        proposed = _set_lease(base, layout, history=[entry], owner="owner-2", lease_id="lease-2", epoch=2)
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_takeover_appended_entry_mismatching_displaced_lease_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=[])
        proposed = _set_lease(base, layout, history=[_entry("totally-different-owner", "totally-different-id", 999)],
                               owner="owner-2", lease_id="lease-2", epoch=2)
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_takeover_new_epoch_not_old_plus_one_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=[])
        proposed = _set_lease(base, layout, history=[_entry("a", "b", 1)], owner="owner-2", lease_id="lease-2",
                               epoch=99)
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_partially_missing_lease_identity_change_is_normal():
    """Owner present, epoch missing/empty: neither a genuine first
    acquisition (identity is not *fully* absent) nor an ordinary renewal
    (the identity itself changes). Independent review's "部分欠損の lease
    からの identity 変更" finding.
    """
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=[])
        base = _set_lease(base, layout, owner="owner-1", lease_id="", epoch="")
        proposed = _set_lease(base, layout, owner="owner-2", lease_id="lease-2", epoch=1)
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_base_epoch_infinity_does_not_raise_and_is_normal():
    doc = _flat_doc(lease_history=[])
    doc["fencing_epoch"] = float("inf")
    proposed = dict(doc)
    proposed["lease_history"] = [_entry("owner-1", "lease-1", float("inf"))]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 1
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_tombstone_criterion_ids_changed_is_not_withdraw():
    req = _v5_request()
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1", fencing_epoch=1)
    tampered_requests = [dict(r) for r in projection_document(withdrawn)["requests"]]
    tampered_requests[0]["criterion_ids"] = ["FORGED"]
    base = _v5_doc(requests=[rec])
    proposed = copy.deepcopy(base)
    proposed["extensions"]["fresh_review"] = {**proposed["extensions"]["fresh_review"],
                                               "requests": tampered_requests}
    assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_tombstone_request_digest_changed_is_not_withdraw():
    req = _v5_request()
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1", fencing_epoch=1)
    tampered_requests = [dict(r) for r in projection_document(withdrawn)["requests"]]
    tampered_requests[0]["request_digest"] = "sha256:" + "f" * 64
    base = _v5_doc(requests=[rec])
    proposed = copy.deepcopy(base)
    proposed["extensions"]["fresh_review"] = {**proposed["extensions"]["fresh_review"],
                                               "requests": tampered_requests}
    assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_withdraw_with_other_control_or_extensions_change_mixed_in_is_normal():
    req = _v5_request()
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1", fencing_epoch=1)
    base = _v5_doc(requests=[rec])
    proposed_control = _set_fresh_review(base, "v5", withdrawn)
    proposed_control["control"]["loop_active"] = False
    assert sc.classify_write_kind(base, proposed_control) == sc.WriteKind.NORMAL

    proposed_ext = _set_fresh_review(base, "v5", withdrawn)
    proposed_ext["extensions"]["unrelated_junk"] = "J" * 500
    assert sc.classify_write_kind(base, proposed_ext) == sc.WriteKind.NORMAL


def test_withdraw_with_other_record_advance_mixed_in_is_normal():
    rec_a = _v5_pending_record(request_id="r1", nonce="n1")
    rec_b = _v5_pending_record(request_id="r2", nonce="n2")
    two_proj = _project(rec_a, rec_b)
    withdrawn = withdraw_request(two_proj, request_id="r1", operation_id="wd-1", fencing_epoch=1)
    mixed = reserve_request(withdrawn, rec_b.request, operation_id="d-r2",
                             intent_digest=ADAPTER, payload_digest=ADAPTER)
    for layout in ("v4", "v5"):
        base = (
            _base(layout, requests=projection_document(two_proj)["requests"])
            if layout == "v4" else _base(layout, requests=[rec_a, rec_b])
        )
        proposed_mixed = _set_fresh_review(base, layout, mixed)
        assert sc.classify_write_kind(base, proposed_mixed) == sc.WriteKind.NORMAL


def test_withdraw_does_not_shrink_is_normal():
    """A forged "withdraw" whose encode length does not strictly decrease
    (padding onto an otherwise-valid tombstone shape is not representable
    through the real reducer, so this pins the guard via a non-withdraw
    diff instead -- the encode-length check must still reject it).
    """
    rec_a = _v5_pending_record(request_id="r1", nonce="n1")
    proj = _project(rec_a)
    base = _v5_doc(requests=[rec_a])
    withdrawn = withdraw_request(proj, request_id="r1", operation_id="wd-1", fencing_epoch=1)
    proposed = _set_fresh_review(base, "v5", withdrawn)
    # Pad extensions so the proposed document is no longer smaller than base
    # despite the projection itself shrinking correctly.
    proposed["extensions"]["padding"] = "P" * 100000
    assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


# ===========================================================================
# 3. ``_strict_equal``/``_diff_keys``: type-aware comparison.
# ===========================================================================


def test_strict_equal_distinguishes_bool_from_int():
    assert sc._strict_equal(1, True) is False
    assert sc._strict_equal(True, 1) is False
    assert sc._strict_equal(0, False) is False
    assert sc._strict_equal(True, True) is True


def test_strict_equal_distinguishes_int_from_float():
    assert sc._strict_equal(1, 1.0) is False
    assert sc._strict_equal(1.0, 1) is False
    assert sc._strict_equal(1.5, 1.5) is True


def test_strict_equal_nan_equals_nan():
    assert sc._strict_equal(float("nan"), float("nan")) is True


def test_strict_equal_recurses_into_nested_structures():
    assert sc._strict_equal({"a": [1, {"b": 1}]}, {"a": [1, {"b": True}]}) is False
    assert sc._strict_equal({"a": [1, {"b": 1}]}, {"a": [1, {"b": 1}]}) is True
    assert sc._strict_equal([1, 2], (1, 2)) is False  # list vs tuple are not interchangeable


def test_diff_keys_counts_a_key_that_appears_with_an_explicit_none_value():
    base = {"a": 1}
    proposed = {"a": 1, "sneaky": None}
    assert sc._diff_keys(base, proposed) == frozenset({"sneaky"})


def test_diff_keys_counts_a_key_that_disappears_from_an_explicit_none_value():
    base = {"a": 1, "sneaky": None}
    proposed = {"a": 1}
    assert sc._diff_keys(base, proposed) == frozenset({"sneaky"})


def test_diff_keys_distinguishes_1_from_true():
    assert sc._diff_keys({"a": 1}, {"a": True}) == frozenset({"a"})


def test_nan_field_does_not_permanently_block_classification():
    """v4 can carry non-finite floats; a NaN field must not permanently
    mark the document "changed" and block stop-halt classification."""
    doc = _flat_doc()
    doc["custom_score"] = float("nan")
    proposed = dict(doc)
    proposed.update(halt_reason="x", phase="halted", loop_active=False)
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.STOP_HALT


# ===========================================================================
# 4. Zero-effective-change diffs must not be misread as a lease mutation.
# ===========================================================================


def test_unrelated_field_alone_with_lease_untouched_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, loop_active=False)
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_empty_diff_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout)
        assert sc.classify_write_kind(base, copy.deepcopy(base)) == sc.WriteKind.NORMAL


# ===========================================================================
# 5. Legitimate lease-only mutations the hand-built fixtures still pin
#    directly (structural shape, independent of a specific real writer).
# ===========================================================================


def test_lease_extension_zero_history_growth_is_stop_takeover():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(3), lease_expires_at="2026-01-01T00:00:00Z")
        base = _set_lease(base, layout, epoch=4)
        proposed = _set_lease(base, layout, expires_at="2026-01-01T00:00:01Z")
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.STOP_TAKEOVER


def test_lease_extension_that_shortens_expiry_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(3), lease_expires_at="2026-01-01T00:00:01Z")
        base = _set_lease(base, layout, epoch=4)
        proposed = _set_lease(base, layout, expires_at="2026-01-01T00:00:00Z")
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_lease_owner_change_without_history_growth_is_normal():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(3))
        base = _set_lease(base, layout, epoch=4)
        proposed = _set_lease(base, layout, owner="renewed-owner")
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_lease_initial_acquisition_structural_shape_is_stop_takeover():
    for layout in ("v4", "v5"):
        base = _no_lease_base(layout)
        proposed = _set_lease(base, layout, owner="first-owner", lease_id="first-lease", epoch=1)
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.STOP_TAKEOVER


def test_halt_carries_a_lease_renewal_expiry_only_and_stays_stop_halt():
    for layout in ("v4", "v5"):
        base = _base(layout, lease_expires_at="2026-01-01T00:00:00Z")
        renewed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        renewed = _set_lease(renewed, layout, expires_at="2026-01-01T00:00:01Z")
        assert sc.classify_write_kind(base, renewed) == sc.WriteKind.STOP_HALT

        swapped = _set_lease(renewed, layout, lease_id="other-lease")
        assert sc.classify_write_kind(base, swapped) == sc.WriteKind.NORMAL


def test_unknown_halt_category_and_long_goal_dispatch_are_still_stop_halt():
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False,
                                   halt_category="totally-unknown-category-xyz")
        assert sc.classify_write_kind(base, proposed) == sc.WriteKind.STOP_HALT

        goal_fields = {"goal_dispatch_effective": "g" * 128, "goal_dispatch_host": "g" * 128,
                       "goal_dispatch_fallback_reason": "g" * 128}
        base2 = _base(layout)
        proposed2 = _apply_control(base2, layout, halt_reason="x", phase="halted", loop_active=False,
                                    **(goal_fields if layout == "v4" else {}))
        if layout == "v5":
            proposed2["extensions"].update(goal_fields)
        assert sc.classify_write_kind(base2, proposed2) == sc.WriteKind.STOP_HALT


def test_halt_reason_at_exactly_the_bound_is_still_stop_halt():
    doc = _flat_doc()
    proposed = _apply_control(doc, "v4", halt_reason="\x01" * sc.HALT_REASON_MAX_CHARS,
                               phase="halted", loop_active=False)
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.STOP_HALT


def test_withdraw_epoch_mismatch_is_normal():
    single_proj = _project(_v5_pending_record())
    base = _v5_doc(requests=[_v5_pending_record()])
    tomb_mismatch = withdraw_request(single_proj, request_id="request-1", operation_id="wd-2", fencing_epoch=999)
    proposed = _set_fresh_review(base, "v5", tomb_mismatch)
    assert sc.classify_write_kind(base, proposed) == sc.WriteKind.NORMAL


def test_withdraw_still_rejects_before_already_withdrawn():
    rec_a = _v5_pending_record(request_id="ra", nonce="na")
    rec_b = _v5_pending_record(request_id="rb", nonce="nb")
    proj = _project(rec_a, rec_b)
    withdrawn_b = withdraw_request(proj, request_id="rb", operation_id="wd-b", fencing_epoch=1)
    doc = _flat_doc(requests=list(projection_document(withdrawn_b)["requests"]))
    proposed = dict(doc)
    requests = [dict(r) for r in proposed["fresh_review"]["requests"]]
    for r in requests:
        if r.get("request_id") == "rb":
            r["withdraw_fencing_epoch"] = 999
    proposed["fresh_review"] = {**proposed["fresh_review"], "requests": requests}
    assert sc.classify_write_kind(doc, proposed) != sc.WriteKind.WITHDRAW


def test_classification_table_has_at_least_40_cases():
    collected = [
        item for item in globals().values()
        if callable(item) and getattr(item, "__name__", "").startswith("test_")
    ]
    assert len(collected) >= 40, len(collected)
