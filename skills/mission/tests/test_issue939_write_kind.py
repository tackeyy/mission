"""E0b-2b-前半 (#939/#940): write_kind classification from a base/proposed
diff. ``classify_write_kind`` requires three things together: allow-list
(diff keys confined to what the real writer touches), structural signature
(shape of a legitimate halt/takeover/withdraw write), increment cap (real
encode-length increase within the Δ the reservation scheme prices it at).
See the module-level comment above ``WriteKind`` in state_capacity.py for
the full design and its round-1/round-2 history. This file is table-driven:
each row id names the scenario; builders (``_halt``/``_takeover``/
``_withdraw``/``_extension``) construct (base, proposed) in 1-2 lines.
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
    canonical_digest, consume_request, decode_request, projection_document,
    reserve_request, withdraw_request,
)
from mission_kernel.model import FencedLease, HaltCategory, LegacyAbsentLease
from mission_kernel import state_capacity as sc

from .test_issue895_fresh_review import ADAPTER
from .test_issue936_state_capacity_reservation import (
    TS27, _flat_doc, _flat_halt_before_after, _minimal_contract,
    _mission_state_module,
)

LAYOUTS = ("v4", "v5")
N = sc.WriteKind.NORMAL
H_ = sc.WriteKind.STOP_HALT
T_ = sc.WriteKind.STOP_TAKEOVER
W_ = sc.WriteKind.WITHDRAW


# ---------------------------------------------------------------------------
# Document builders (both layouts; write_kind needs v5 fixtures the
# reservation-half tests don't exercise).
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
        "adapter_registration_digest": ADAPTER, "candidate_bindings": bindings,
        "criterion_ids": list(criterion_ids), "iteration": 1, "perspective": "counterexamples",
        "allowed_tools": [], "wall_time_sec": 300, "max_tool_calls": 64, "max_replays": 16,
        "max_output_bytes": 262144, "max_packet_bytes": 1048576, "created_at": "2026-01-01T00:00:00+00:00",
        "input_ref": {"kind": "fresh-review-input",
                      "relative_path": "evidence/fresh-review/" + input_digest[7:] + ".json",
                      "digest": input_digest, "size": 19},
    })


def _v5_pending_record(request_id="request-1", nonce="nonce-1"):
    return FreshReviewRecord(_v5_request(request_id=request_id, nonce=nonce), "prep-" + request_id, ADAPTER, ADAPTER)


def _project(*records):
    return FreshReviewProjection(tuple(records))


def _v5_doc(*, requests=(), halt_reason="", halt_absent=False, lease_history=(),
            lease_expires_at="9999-12-31T23:59:59Z", control_extra=None, extensions_extra=None, contract=None):
    control = {"phase": "executing", "loop_active": True, "terminal_outcome": None, "halt_category": None}
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
        "updated_at": TS27, "last_activity_at": TS27, "extensions": extensions,
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
                        ("lease_id", lease_id), ("fencing_epoch", epoch), ("lease_expires_at", expires_at)):
        if value is not None:
            target[key] = value
    return out


def _entry(owner, lease_id, epoch, reason="lease-expired-takeover", at=TS27):
    return {"owner_session_id": owner, "lease_id": lease_id, "fencing_epoch": epoch, "reason": reason, "at": at}


def _history(n):
    return [_entry("a", f"id{i}", i + 1) for i in range(n)]


def _no_lease_base(layout):
    """Lease genuinely never acquired: fencing_epoch absent/empty, 0 history."""
    base = _base(layout)
    if layout == "v4":
        return dict(base, owner_session_id="", lease_id="", fencing_epoch="")
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


# ---------------------------------------------------------------------------
# Scenario builders: each returns a structurally *legitimate* (base,
# proposed) pair for its kind, so a test only needs to say what breaks it.
# ---------------------------------------------------------------------------


def _halt(layout, *, lease_expires_at="2026-01-01T00:00:00Z", base=None, top_extra=None, ext_extra=None,
          **control_fields):
    b = base if base is not None else _base(layout, lease_expires_at=lease_expires_at)
    p = _apply_control(b, layout, halt_reason="x", phase="halted", loop_active=False, **control_fields)
    if top_extra:
        p.update(top_extra)
    if layout == "v5" and ext_extra:
        p["extensions"].update(ext_extra)
    return b, p


def _takeover(layout, *, entry=None, cur=None, base=None):
    b = base if base is not None else _base(layout, lease_history=[])
    c = sc._lease_mapping(b)
    e = dict(_entry(c["owner_session_id"], c["lease_id"], c["fencing_epoch"]), **(entry or {}))
    cu = dict(owner="new-owner", lease_id="new-lease", epoch=c["fencing_epoch"] + 1, **(cur or {}))
    p = _set_lease(b, layout, history=[e], owner=cu["owner"], lease_id=cu["lease_id"], epoch=cu["epoch"])
    return b, p


def _extension(layout, *, lease_history=(), lease_expires_at="2026-01-01T00:00:00Z",
                new_expires_at="2026-01-01T01:00:00Z", epoch=4, base=None):
    b = base if base is not None else _set_lease(
        _base(layout, lease_history=lease_history, lease_expires_at=lease_expires_at), layout, epoch=epoch)
    return b, _set_lease(b, layout, expires_at=new_expires_at)


def _withdraw(layout="v5", *, lease=None, base=None, request_id="r1", lease_expires_at="2026-01-01T00:00:00Z"):
    rec = _v5_pending_record(request_id=request_id, nonce="n-" + request_id)
    proj = _project(rec)
    b = base if base is not None else (
        _flat_doc(requests=projection_document(proj)["requests"]) if layout == "v4"
        else _v5_doc(requests=[rec], lease_expires_at=lease_expires_at)
    )
    fencing_epoch = (lease or {}).get("fencing_epoch", 1)
    withdrawn = withdraw_request(proj, request_id=request_id, operation_id="wd-" + request_id,
                                  fencing_epoch=fencing_epoch)
    p = _set_fresh_review(b, layout, withdrawn)
    if layout == "v5" and lease:
        p["lease"].update(lease)
    return b, p


# ===========================================================================
# 1. Real writers (production code, not the builders above): every scenario
#    a legitimate diff must classify as.
# ===========================================================================


@pytest.mark.parametrize("category", [c.value for c in HaltCategory])
def test_v4_real_halt_writer_is_stop_halt(category):
    before, after = _flat_halt_before_after(category)
    assert sc.classify_write_kind(before, after, encoding=sc.StateEncoding.LEGACY_PRETTY) == H_


def _v5_halt_before_after(category, *, reason="\x01" * sc.HALT_REASON_MAX_CHARS):
    from mission_application.compatibility import compatibility_delta
    from mission_kernel import decode_snapshot, encode_v5_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.transitions import decide
    from .mission_state_fixture_corpus import canonical_json_bytes, current_v5_open_state

    payload = current_v5_open_state()
    payload["control"].update(halt_reason="", loop_active=True, phase="executing", terminal_outcome=None)
    snap = decode_snapshot(canonical_json_bytes(payload))
    before_doc = json.loads(encode_v5_snapshot(snap))
    compat = compatibility_delta(payload, payload, exclude=set())
    command = MarkHalt(HaltCategory(category), reason, legacy_reason=reason, compatibility=compat,
                        at="2026-01-01T00:00:00Z")
    decision = decide(snap.state, command)
    assert decision.accepted, (category, decision.rejection)
    new_state = dataclasses.replace(decision.transition.new_state, snapshot_provenance=snap.provenance)
    object.__setattr__(new_state, "_snapshot_binding", snap.guidance._snapshot_binding)
    after_doc = json.loads(encode_v5_snapshot(dataclasses.replace(snap, state=new_state)))
    return before_doc, after_doc


@pytest.mark.parametrize("category", [c.value for c in HaltCategory])
def test_v5_real_halt_writer_is_stop_halt(category):
    before, after = _v5_halt_before_after(category)
    assert sc.classify_write_kind(before, after, encoding=sc.StateEncoding.CANONICAL) == H_


def test_v4_real_halt_writer_at_max_dispatch_fields_is_still_stop_halt():
    """``routed-goal`` worst-cased: the shape ``STATE_CAPACITY_HALT_DELTA`` is measured from."""
    before, after = _flat_halt_before_after("routed-goal")
    assert sc.classify_write_kind(before, after, encoding=sc.StateEncoding.LEGACY_PRETTY) == H_


@pytest.mark.parametrize("owner,lease_id,extra", [
    ("owner-1", None, {}),
    ("owner-1", "lease-1", {"owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 5,
                             "lease_expires_at": "2020-01-01T00:00:00Z", "lease_history": []}),
], ids=["initial", "renewal"])
def test_v4_real_lease_writer_is_stop_takeover(owner, lease_id, extra):
    mod = _mission_state_module()
    base = {"schema_version": 4, "phase": "executing", "loop_active": True, "halt_reason": "", **extra}
    after = copy.deepcopy(base)
    mod.acquire_or_verify_lease(after, owner, lease_id=lease_id)
    assert sc.classify_write_kind(base, after, encoding=sc.StateEncoding.LEGACY_PRETTY) == T_


def test_v4_real_lease_first_and_second_takeover_are_stop_takeover():
    mod = _mission_state_module()
    base = {"schema_version": 4, "phase": "executing", "loop_active": True, "halt_reason": "",
            "owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 5,
            "lease_expires_at": "2000-01-01T00:00:00Z", "lease_history": []}
    after1 = copy.deepcopy(base)
    mod.acquire_or_verify_lease(after1, "owner-2", lease_id="lease-2", reason="lease-expired-takeover")
    assert sc.classify_write_kind(base, after1, encoding=sc.StateEncoding.LEGACY_PRETTY) == T_
    after1["lease_expires_at"] = "2000-01-01T00:00:00Z"  # force expiry so a second takeover is admitted
    after2 = copy.deepcopy(after1)
    mod.acquire_or_verify_lease(after2, "owner-3", lease_id="lease-3", reason="lease-expired-takeover")
    assert sc.classify_write_kind(after1, after2, encoding=sc.StateEncoding.LEGACY_PRETTY) == T_


def _v5_lease_doc(lease_mapping):
    return {"schema_version": 5,
            "control": {"phase": "executing", "loop_active": True, "terminal_outcome": None,
                        "halt_category": None, "halt_reason": None},
            "lease": lease_mapping, "extensions": {}}


@pytest.mark.parametrize("base_lease,owner,presented,now", [
    (LegacyAbsentLease(), "owner-1", None, datetime(2026, 1, 1, tzinfo=timezone.utc)),
    (FencedLease("owner-1", "lease-1", 5, "2020-01-01T00:00:00Z", ()), "owner-1", "lease-1",
     datetime(2026, 1, 1, tzinfo=timezone.utc)),
    (FencedLease("owner-1", "lease-1", 5, "2000-01-01T00:00:00Z", ()), "owner-2", None,
     datetime(2026, 1, 1, tzinfo=timezone.utc)),
], ids=["initial", "renewal", "takeover"])
def test_v5_real_lease_writer_is_stop_takeover(base_lease, owner, presented, now):
    from mission_persistence.fenced_commit import admit_lease, _lease_document
    req = types.SimpleNamespace(lease_owner_session_id=owner, presented_lease_id=presented)
    pending = admit_lease(req, base_lease, now, 900)
    base_doc = _v5_lease_doc(_lease_document(base_lease))
    after_doc = _v5_lease_doc(_lease_document(pending.target))
    assert sc.classify_write_kind(base_doc, after_doc, encoding=sc.StateEncoding.CANONICAL) == T_


def _withdraw_v5_docs(*, lease_before, lease_after):
    req = _v5_request()
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1",
                                  fencing_epoch=lease_after.get("fencing_epoch"))
    control = {"phase": "executing", "loop_active": True, "terminal_outcome": None, "halt_category": None,
               "halt_reason": None}
    base_doc = {"schema_version": 5, "control": control, "lease": lease_before,
                "extensions": {"fresh_review": projection_document(proj)}}
    after_doc = {"schema_version": 5, "control": dict(control), "lease": lease_after,
                 "extensions": {"fresh_review": projection_document(withdrawn)}}
    return base_doc, after_doc


@pytest.mark.parametrize("fencing_epoch", [2, 1], ids=["fence_bump", "plain_renewal"])
def test_withdraw_real_reducer_output_is_withdraw(fencing_epoch):
    lease_before = {"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 1,
                     "lease_expires_at": "2026-01-01T00:00:00Z", "lease_history": []}
    lease_after = {**lease_before, "fencing_epoch": fencing_epoch, "lease_expires_at": "2026-01-01T00:00:01Z"}
    base_doc, after_doc = _withdraw_v5_docs(lease_before=lease_before, lease_after=lease_after)
    assert sc.classify_write_kind(base_doc, after_doc, encoding=sc.StateEncoding.CANONICAL) == W_


def test_withdraw_v4_flat_save_encode_length_decreases():
    base, proposed = _withdraw("v4")
    assert sc.classify_write_kind(base, proposed, encoding=sc.StateEncoding.LEGACY_PRETTY) == W_


def test_withdraw_real_output_at_many_criteria_is_still_withdraw():
    """10 criterion_ids (large shared field): tombstone is still strictly smaller than the pending record."""
    criterion_ids = tuple(f"AC{i}" for i in range(10))
    req = _v5_request(criterion_ids=criterion_ids)
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1", fencing_epoch=1)
    base = _v5_doc(requests=[rec])
    proposed = _set_fresh_review(base, "v5", withdrawn)
    assert sc.classify_write_kind(base, proposed, encoding=sc.StateEncoding.CANONICAL) == W_


@pytest.mark.parametrize("layout", LAYOUTS)
def test_genesis(layout):
    assert sc.classify_write_kind(None, _base(layout)) == sc.WriteKind.GENESIS


# ===========================================================================
# 2. Table-driven classification: decoys (NORMAL) and legitimate shapes
#    (STOP_HALT/STOP_TAKEOVER), all via ``classify_write_kind(base,
#    proposed, **kw) == expected``. Independent review's and the Checker's
#    findings are each pinned as one row; see the row id for what it tests.
# ===========================================================================


def _rows():
    rows = []
    LP = {"encoding": sc.StateEncoding.LEGACY_PRETTY}
    CA = {"encoding": sc.StateEncoding.CANONICAL}

    for L in LAYOUTS:
        # -- legitimate shapes --------------------------------------------
        rows.append((f"halt_pure_{L}", *_halt(L), H_, {}))
        rows.append((f"halt_unknown_category_{L}", *_halt(L, halt_category="totally-unknown-category-xyz"), H_, {}))
        goal = {"goal_dispatch_effective": "g" * 128, "goal_dispatch_host": "g" * 128,
                "goal_dispatch_fallback_reason": "g" * 128}
        rows.append((f"halt_long_goal_dispatch_{L}",
                      *_halt(L, top_extra=goal if L == "v4" else None, ext_extra=goal if L == "v5" else None),
                      H_, {}))
        base, renewed = _halt(L)
        renewed = _set_lease(renewed, L, expires_at="2026-01-01T01:00:00Z")
        rows.append((f"halt_with_lease_renewal_{L}", base, renewed, H_, {}))
        rows.append((f"halt_with_lease_swap_after_renewal_{L}", base,
                      _set_lease(renewed, L, lease_id="other-lease"), N, {}))
        rows.append((f"takeover_pure_{L}", *_takeover(L), T_, {}))
        rows.append((f"takeover_initial_acquisition_{L}",
                      _no_lease_base(L), _set_lease(_no_lease_base(L), L, owner="first-owner",
                                                     lease_id="first-lease", epoch=1), T_, {}))
        rows.append((f"extension_zero_growth_{L}", *_extension(L), T_, {}))
        rows.append((f"extension_shortens_expiry_{L}",
                      *_extension(L, lease_expires_at="2026-01-01T00:00:01Z",
                                  new_expires_at="2026-01-01T00:00:00Z"), N, {}))
        base_ext, _ = _extension(L)
        rows.append((f"extension_owner_change_no_growth_{L}", base_ext,
                      _set_lease(base_ext, L, owner="renewed-owner"), N, {}))

        # -- decoys: mixing / bounds / structural tampering ----------------
        rows.append((f"second_halt_write_{L}", _base(L, halt_reason="already-halted"),
                      _base(L, halt_reason="different-reason"), N, {}))
        hb, hp = _halt(L)
        rows.append((f"halt_with_lease_identity_change_{L}", hb, _set_lease(hp, L, owner="other-owner"), N, {}))
        rows.append((f"lease_history_jump_by_two_{L}", _base(L, lease_history=_history(2)),
                      _set_lease(_base(L, lease_history=_history(2)), L, history=_history(2) + _history(2)), N, {}))
        rows.append((f"lease_history_shrink_{L}", _base(L, lease_history=_history(5)),
                      _set_lease(_base(L, lease_history=_history(5)), L, history=_history(2)), N, {}))
        tb = _set_lease(_base(L, lease_history=_history(3)), L, epoch=4)
        tampered = _history(3)
        tampered[0] = dict(tampered[0], fencing_epoch=True)  # 1 -> True must not read as unchanged
        rows.append((f"lease_history_rewrite_1_to_true_{L}", tb,
                      _set_lease(tb, L, history=tampered + [_entry("a", "lease-1", 4)],
                                 owner="owner-2", lease_id="lease-2", epoch=5), N, {}))
        tkb = _base(L, lease_history=[])
        rows.append((f"takeover_entry_extra_field_{L}", tkb,
                      _set_lease(tkb, L, history=[dict(_entry("owner-1", "lease-1", 1), extra_field="x")],
                                 owner="owner-2", lease_id="lease-2", epoch=2), N, {}))
        rows.append((f"takeover_entry_mismatches_displaced_lease_{L}", tkb,
                      _set_lease(tkb, L, history=[_entry("totally-different-owner", "totally-different-id", 999)],
                                 owner="owner-2", lease_id="lease-2", epoch=2), N, {}))
        rows.append((f"takeover_new_epoch_not_plus_one_{L}", tkb,
                      _set_lease(tkb, L, history=[_entry("a", "b", 1)], owner="owner-2", lease_id="lease-2",
                                 epoch=99), N, {}))
        pmb = _set_lease(_base(L, lease_history=[]), L, owner="owner-1", lease_id="", epoch="")
        rows.append((f"partially_missing_identity_change_{L}", pmb,
                      _set_lease(pmb, L, owner="owner-2", lease_id="lease-2", epoch=1), N, {}))
        eb, ep = _takeover(L)
        tampered_prefix = _history(3)
        tb2 = _set_lease(_base(L, lease_history=_history(3)), L, epoch=4)
        current = sc._lease_mapping(tb2)
        correct_entry = _entry(current["owner_session_id"], current["lease_id"], current["fencing_epoch"])
        tampered_prefix[1] = dict(tampered_prefix[1], owner_session_id="tampered")
        rows.append((f"takeover_altered_prefix_correct_new_entry_{L}", tb2,
                      _set_lease(tb2, L, history=tampered_prefix + [correct_entry], owner="new-owner",
                                 lease_id="new-lease", epoch=5), N, {}))
        tb3 = _base(L, lease_history=[])
        c3 = sc._lease_mapping(tb3)
        correct3 = _entry(c3["owner_session_id"], c3["lease_id"], c3["fencing_epoch"])
        rows.append((f"takeover_history_growth_by_two_{L}", tb3,
                      _set_lease(tb3, L, history=[_entry("injected", "injected", 1), correct3],
                                 owner="new-owner", lease_id="new-lease", epoch=c3["fencing_epoch"] + 1), N, {}))
        rows.append((f"takeover_partial_identity_{L}", *_takeover_partial_identity(L), N, {}))
        rows.append((f"takeover_entry_reason_invalid_pattern_{L}", *_takeover(L, entry={"reason": "bad reason"}),
                      N, {}))
        stale_base = _set_lease(_no_lease_base(L), L, history=_history(2))
        rows.append((f"initial_requires_empty_history_{L}", stale_base,
                      _set_lease(stale_base, L, owner="first-owner", lease_id="first-lease", epoch=1), N, {}))
        xb = _set_lease(_base(L, lease_history=[]), L, owner="owner-1", lease_id="", epoch="")
        rows.append((f"extension_partial_identity_{L}", xb,
                      _set_lease(xb, L, expires_at="9999-12-31T23:59:59Z"), N, {}))

    # -- over-bound value checks (both directions, each kills a distinct guard) --
    for L in ("v4",):
        d = _flat_doc(lease_history=[])
        rows.append((f"halt_reason_over_bound_{L}", d,
                      {**d, "halt_reason": "\x01" * (sc.HALT_REASON_MAX_CHARS + 1), "phase": "halted",
                       "loop_active": False}, N, {}))
        rows.append((f"goal_dispatch_effective_over_bound_{L}", d,
                      {**d, "halt_reason": "x", "phase": "halted", "loop_active": False,
                       "goal_dispatch_effective": "g" * (sc.GOAL_DISPATCH_REASON_MAX_CHARS + 1)}, N, {}))
        rows.append((f"goal_dispatch_host_over_bound_{L}", d,
                      {**d, "halt_reason": "x", "phase": "halted", "loop_active": False,
                       "goal_dispatch_host": "g" * (sc.GOAL_DISPATCH_REASON_MAX_CHARS + 1)}, N, {}))
        rows.append((f"goal_dispatch_fallback_over_bound_{L}", d,
                      {**d, "halt_reason": "x", "phase": "halted", "loop_active": False,
                       "goal_dispatch_fallback_reason": "g" * (sc.GOAL_DISPATCH_REASON_MAX_CHARS + 1)}, N, {}))
        rows.append((f"takeover_owner_pattern_over_bound_{L}", d,
                      {**d, "lease_history": [_entry("owner-1", "lease-1", 1)],
                       "owner_session_id": "bad owner with spaces", "lease_id": "lease-2", "fencing_epoch": 2},
                      N, {}))
        rows.append((f"takeover_epoch_over_max_{L}", d,
                      {**d, "lease_history": [_entry("owner-1", "lease-1", 1)], "owner_session_id": "owner-2",
                       "lease_id": "lease-2", "fencing_epoch": sc.LEASE_EPOCH_MAX + 1}, N, {}))
        rows.append((f"halt_increment_over_delta_{L}", d,
                      {**d, "halt_reason": "\x01" * (sc.HALT_REASON_MAX_CHARS * 20), "phase": "halted",
                       "loop_active": False}, N, LP))
        rows.append((f"halt_at_exactly_the_bound_{L}",
                      d, _apply_control(d, L, halt_reason="\x01" * sc.HALT_REASON_MAX_CHARS, phase="halted",
                                        loop_active=False), H_, {}))

    rows.append(("halt_takeover_mixed_v4", *_halt_takeover_mixed(), N, {}))
    rows.append(("epoch_infinity_base_v4", *_epoch_infinity(), N, {}))

    # -- withdraw: legitimate + decoys (v5 primary; v4 top-level separately) --
    rows.append(("withdraw_pure_fence_bump_v5",
                  *_withdraw(lease={"owner_session_id": "a", "lease_id": "b", "fencing_epoch": 2,
                                     "lease_expires_at": "2026-01-01T00:00:01Z"}), W_, CA))
    rows.append(("withdraw_pure_plain_renewal_v5",
                  *_withdraw(lease={"lease_expires_at": "2026-01-01T00:00:01Z"}), W_, CA))
    wb, wp = _withdraw()
    rows.append(("withdraw_epoch_mismatch_v5", wb,
                  _set_fresh_review(wb, "v5",
                                     withdraw_request(_project(_v5_pending_record("r1", "n-r1")),
                                                       request_id="r1", operation_id="wd-2", fencing_epoch=999)),
                  N, CA))
    rows.append(("withdraw_control_change_mixed_v5", *_withdraw_control_mixed(), N, CA))
    rows.append(("withdraw_extensions_junk_mixed_v5", *_withdraw_ext_junk_mixed(), N, CA))
    rows.append(("withdraw_criterion_ids_tampered_v5", *_withdraw_tombstone_tampered("criterion_ids"), N, CA))
    rows.append(("withdraw_request_digest_tampered_v5", *_withdraw_tombstone_tampered("request_digest"), N, CA))
    rows.append(("withdraw_identity_change_not_fence_v5", *_withdraw_identity_change(), N, CA))
    rows.append(("withdraw_epoch_decreased_v5", *_withdraw_epoch_decreased(), N, CA))
    for L in LAYOUTS:
        rows.append((f"withdraw_other_record_advance_mixed_{L}", *_withdraw_other_record_mixed(L), N, {}))
    rows.append(("withdraw_v4_unrelated_top_level_field_changed", *_withdraw_v4_top_level_changed(), N, LP))

    # -- v5-only structural guards (no v4 analogue) --
    rows.append(("halt_v5_top_level_sibling_key", *_halt_v5_top_sibling(), N, {}))
    rows.append(("halt_v5_bogus_control_field", *_halt("v5", bogus_control_field="x"), N, {}))
    rows.append(("halt_v5_extensions_mirrored_reason_over_bound", *_halt_v5_ext_reason_over_bound(), N, {}))
    rows.append(("takeover_v5_bogus_lease_key", *_takeover_v5_bogus_key(), N, {}))

    # -- epoch boundary (both directions, both layouts) --
    for L in LAYOUTS:
        rows.append((f"takeover_epoch_upper_boundary_{L}", *_takeover_epoch_boundary(L, sc.LEASE_EPOCH_MAX), N, {}))
        rows.append((f"takeover_epoch_negative_{L}", *_takeover_epoch_boundary(L, -5), N, {}))
        rows.append((f"extension_naive_timestamps_{L}", *_extension_naive(L), N, {}))

    # -- per-field appended-entry mismatch (parametrized separately below for ids) --
    return rows


def _takeover_partial_identity(layout):
    base = _base(layout, lease_history=[])
    base = _set_lease(base, layout, epoch=5)
    if layout == "v5":
        base["lease"]["owner_session_id"] = None
        base["lease"]["lease_id"] = None
    else:
        base["owner_session_id"] = None
        base["lease_id"] = None
    entry = _entry("None", "None", 5)
    proposed = _set_lease(base, layout, history=[entry], owner="new-owner", lease_id="new-lease", epoch=6)
    return base, proposed


def _halt_takeover_mixed():
    doc = _flat_doc()
    proposed = {**doc, "halt_reason": "stagnation", "phase": "halted",
                "lease_history": list(doc["lease_history"]) + [_entry("owner-1", "lease-1", 1)],
                "owner_session_id": "owner-2", "lease_id": "lease-2", "fencing_epoch": 2}
    return doc, proposed


def _epoch_infinity():
    doc = _flat_doc(lease_history=[])
    doc["fencing_epoch"] = float("inf")
    proposed = {**doc, "lease_history": [_entry("owner-1", "lease-1", float("inf"))],
                "owner_session_id": "owner-2", "lease_id": "lease-2", "fencing_epoch": 1}
    return doc, proposed


def _withdraw_control_mixed():
    base, proposed = _withdraw()
    proposed["control"]["loop_active"] = False
    return base, proposed


def _withdraw_ext_junk_mixed():
    base, proposed = _withdraw()
    proposed["extensions"]["unrelated_junk"] = "J" * 500
    return base, proposed


def _withdraw_tombstone_tampered(field):
    req = _v5_request()
    rec = FreshReviewRecord(req, "prep-1", ADAPTER, ADAPTER)
    proj = _project(rec)
    withdrawn = withdraw_request(proj, request_id="request-1", operation_id="wd-1", fencing_epoch=1)
    tampered_requests = [dict(r) for r in projection_document(withdrawn)["requests"]]
    tampered_requests[0][field] = ["FORGED"] if field == "criterion_ids" else "sha256:" + "f" * 64
    base = _v5_doc(requests=[rec])
    proposed = copy.deepcopy(base)
    proposed["extensions"]["fresh_review"] = {**proposed["extensions"]["fresh_review"], "requests": tampered_requests}
    return base, proposed


def _withdraw_identity_change():
    base, proposed = _withdraw()
    proposed["lease"]["owner_session_id"] = "someone-else"
    return base, proposed


def _withdraw_epoch_decreased():
    rec = _v5_pending_record("r1", "n-r1")
    proj = _project(rec)
    base = _v5_doc(requests=[rec])
    base["lease"]["fencing_epoch"] = 5
    withdrawn = withdraw_request(proj, request_id="r1", operation_id="wd-1", fencing_epoch=3)
    proposed = _set_fresh_review(base, "v5", withdrawn)
    proposed["lease"]["fencing_epoch"] = 3
    return base, proposed


def _withdraw_other_record_mixed(layout):
    rec_a, rec_b = _v5_pending_record("r1", "n1"), _v5_pending_record("r2", "n2")
    two_proj = _project(rec_a, rec_b)
    withdrawn = withdraw_request(two_proj, request_id="r1", operation_id="wd-1", fencing_epoch=1)
    mixed = reserve_request(withdrawn, rec_b.request, operation_id="d-r2", intent_digest=ADAPTER,
                             payload_digest=ADAPTER)
    base = (_base(layout, requests=projection_document(two_proj)["requests"]) if layout == "v4"
            else _base(layout, requests=[rec_a, rec_b]))
    return base, _set_fresh_review(base, layout, mixed)


def _withdraw_v4_top_level_changed():
    base, proposed = _withdraw("v4")
    proposed["phase"] = "reviewing"
    return base, proposed


def _halt_v5_top_sibling():
    base, proposed = _halt("v5")
    proposed["score_history"] = [{"s": "x"}]  # top-level sibling, not nested in extensions
    return base, proposed


def _halt_v5_ext_reason_over_bound():
    base, proposed = _halt("v5")
    proposed["extensions"]["halt_reason"] = "\x01" * (sc.HALT_REASON_MAX_CHARS + 1)
    return base, proposed


def _takeover_v5_bogus_key():
    base, proposed = _takeover("v5")
    proposed["lease"]["bogus_lease_field"] = "x"
    return base, proposed


def _takeover_epoch_boundary(layout, epoch):
    base = _set_lease(_base(layout, lease_history=[]), layout, epoch=epoch)
    c = sc._lease_mapping(base)
    entry = _entry(c["owner_session_id"], c["lease_id"], epoch)
    proposed = _set_lease(base, layout, history=[entry], owner="owner-2", lease_id="lease-2", epoch=epoch + 1)
    return base, proposed


def _extension_naive(layout):
    """No UTC offset on either side: a naive comparison would see "later" and accept it; must fail closed."""
    base = _set_lease(_base(layout, lease_history=_history(3), lease_expires_at="2026-01-01T00:00:00"),
                       layout, epoch=4)
    return base, _set_lease(base, layout, expires_at="2026-01-01T01:00:00")


ROWS = _rows()


@pytest.mark.parametrize("case_id,base,proposed,expected,kw", ROWS, ids=[r[0] for r in ROWS])
def test_classification_table(case_id, base, proposed, expected, kw):
    assert sc.classify_write_kind(base, proposed, **kw) == expected, case_id


def test_classification_table_has_at_least_60_rows():
    assert len(ROWS) >= 60, len(ROWS)


@pytest.mark.parametrize("field", ["owner_session_id", "lease_id", "fencing_epoch"])
@pytest.mark.parametrize("layout", LAYOUTS)
def test_takeover_appended_entry_single_field_mismatch_is_normal(layout, field):
    """Entry correct in 2 of 3 identity fields, wrong in exactly the one under test."""
    current = sc._lease_mapping(_base(layout, lease_history=[]))
    entry = {"owner_session_id": field != "owner_session_id" and current["owner_session_id"] or "wrong-value",
             "lease_id": field != "lease_id" and current["lease_id"] or "wrong-value",
             "fencing_epoch": field != "fencing_epoch" and current["fencing_epoch"] or 999999}
    base, proposed = _takeover(layout, entry=entry)
    assert sc.classify_write_kind(base, proposed) == N


def test_withdraw_still_rejects_before_already_withdrawn():
    rec_a, rec_b = _v5_pending_record("ra", "na"), _v5_pending_record("rb", "nb")
    proj = _project(rec_a, rec_b)
    withdrawn_b = withdraw_request(proj, request_id="rb", operation_id="wd-b", fencing_epoch=1)
    doc = _flat_doc(requests=list(projection_document(withdrawn_b)["requests"]))
    proposed = dict(doc)
    requests = [dict(r) for r in proposed["fresh_review"]["requests"]]
    for r in requests:
        if r.get("request_id") == "rb":
            r["withdraw_fencing_epoch"] = 999
    proposed["fresh_review"] = {**proposed["fresh_review"], "requests": requests}
    assert sc.classify_write_kind(doc, proposed) != W_


def test_classify_write_kind_never_raises_on_a_malformed_proposed_type():
    """``proposed`` not even a mapping must fail closed to normal, not propagate (kills ``catch_all_off``)."""
    base = _flat_doc()
    assert sc.classify_write_kind(base, 12345) == N
    assert sc.classify_write_kind(base, ["not", "a", "mapping"]) == N


# ===========================================================================
# 3. ``_strict_equal``/``_diff_keys``: type-aware comparison (direct unit tests).
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


@pytest.mark.parametrize("base,proposed,expected", [
    ({"a": 1}, {"a": 1, "sneaky": None}, frozenset({"sneaky"})),
    ({"a": 1, "sneaky": None}, {"a": 1}, frozenset({"sneaky"})),
    ({"a": 1}, {"a": True}, frozenset({"a"})),
], ids=["appears_with_explicit_none", "disappears_from_explicit_none", "1_vs_true"])
def test_diff_keys_presence_and_type(base, proposed, expected):
    assert sc._diff_keys(base, proposed) == expected


def test_nan_field_does_not_permanently_block_classification():
    """A NaN field (v4 only; canonical v5 rejects it) must not mark the document permanently "changed"."""
    doc = _flat_doc()
    doc["custom_score"] = float("nan")
    proposed = dict(doc)
    proposed.update(halt_reason="x", phase="halted", loop_active=False)
    assert sc._diff_keys(doc, proposed) == frozenset({"halt_reason", "phase", "loop_active"})
    assert sc.classify_write_kind(doc, proposed, encoding=sc.StateEncoding.LEGACY_PRETTY) == H_


# ===========================================================================
# 4. Zero-effective-change diffs must not be misread as a lease mutation.
# ===========================================================================


@pytest.mark.parametrize("layout", LAYOUTS)
def test_unrelated_field_alone_with_lease_untouched_is_normal(layout):
    base = _base(layout)
    assert sc.classify_write_kind(base, _apply_control(base, layout, loop_active=False)) == N


@pytest.mark.parametrize("layout", LAYOUTS)
def test_empty_diff_is_normal(layout):
    base = _base(layout)
    assert sc.classify_write_kind(base, copy.deepcopy(base)) == N


# ===========================================================================
# 5. 同乗（riding-along）: structurally-small mutations must not slip through
#    just because they are smaller than Δ_halt/Δ_takeover (independent
#    Checker's probe_ride.py/probe_ride2.py).
# ===========================================================================


def _riding_along_table():
    rec, rec2 = _v5_pending_record(), _v5_pending_record(request_id="r2", nonce="n2")
    proj = _project(rec)
    reserved = reserve_request(proj, rec.request, operation_id="d1", intent_digest=ADAPTER, payload_digest=ADAPTER)
    consumed = consume_request(reserved, rec.request, operation_id="d1", intent_digest=ADAPTER,
                                payload_digest=ADAPTER, result={"ok": True})
    two_pending = _project(rec, rec2)
    cases = []
    for layout in LAYOUTS:
        base = (_flat_doc(requests=[projection_document(proj)["requests"][0]]) if layout == "v4"
                else _v5_doc(requests=[rec]))
        base = _set_lease(base, layout, expires_at="2026-01-01T00:00:00Z")
        halted = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        extended = _set_lease(base, layout, expires_at="2026-01-01T01:00:00Z")

        cases.append((f"halt+dispatch_{layout}", base, _set_fresh_review(halted, layout, reserved)))
        cases.append((f"halt+new_pending_request_{layout}", base, _set_fresh_review(halted, layout, two_pending)))

        base_reserved = _set_fresh_review(base, layout, reserved)
        halted_reserved = _apply_control(base_reserved, layout, halt_reason="x", phase="halted", loop_active=False)
        cases.append((f"halt+consume_{layout}", base_reserved, _set_fresh_review(halted_reserved, layout, consumed)))

        reactivated = copy.deepcopy(halted)
        (reactivated if layout == "v4" else reactivated["extensions"])["reactivation_history"] = [{"at": TS27}]
        cases.append((f"halt+reactivation_history_{layout}", base, reactivated))

        cases.append((f"extension+phase_advance_{layout}", base, _apply_control(extended, layout, phase="reviewing")))

        arbitrary = copy.deepcopy(extended)
        (arbitrary if layout == "v4" else arbitrary["extensions"])["score_history"] = [{"s": "J" * 400}]
        cases.append((f"extension+arbitrary_field_{layout}", base, arbitrary))

        cases.append((f"extension+dispatch_{layout}", base, _set_fresh_review(extended, layout, reserved)))
        cases.append((f"extension+consume_{layout}", base_reserved,
                       _set_lease(_set_fresh_review(base_reserved, layout, consumed), layout,
                                  expires_at="2026-01-01T01:00:00Z")))

        contract_replaced = copy.deepcopy(extended)
        (contract_replaced if layout == "v4" else contract_replaced["extensions"])["acceptance_contract"] = {"x": 1}
        cases.append((f"extension+acceptance_contract_replaced_{layout}", base, contract_replaced))

        lb = sc._lease_mapping(base_reserved)
        takeover_plus_consume = _set_lease(
            _set_fresh_review(base_reserved, layout, consumed), layout,
            history=list(lb.get("lease_history") or []) + [_entry(lb["owner_session_id"], lb["lease_id"],
                                                                    lb["fencing_epoch"])],
            owner="new-owner", lease_id="new-lease", epoch=lb["fencing_epoch"] + 1,
            expires_at="2026-01-01T01:00:00Z")
        cases.append((f"takeover+consume_{layout}", base_reserved, takeover_plus_consume))
    return cases


RIDING_ALONG_TABLE = _riding_along_table()


@pytest.mark.parametrize("name,base,proposed", RIDING_ALONG_TABLE, ids=[c[0] for c in RIDING_ALONG_TABLE])
def test_riding_along_combinations_are_normal(name, base, proposed):
    assert sc.classify_write_kind(base, proposed) == N, name


def test_riding_along_table_has_at_least_18_cases():
    assert len(RIDING_ALONG_TABLE) >= 18, len(RIDING_ALONG_TABLE)


# ===========================================================================
# 6. Increment cap, isolated: every other condition holds; only the
#    encode-length comparison, via an *allowed* field, must stop it.
# ===========================================================================


def test_halt_increment_alone_exceeds_delta_via_allowed_ride_along_field_is_normal():
    for layout, encoding in (("v4", sc.StateEncoding.LEGACY_PRETTY), ("v5", sc.StateEncoding.CANONICAL)):
        base, proposed = _halt(layout)
        padding = [{"kind": "subagent-wait", "detail": "D" * (sc.STATE_CAPACITY_HALT_DELTA + 1000)}]
        (proposed if layout == "v4" else proposed["extensions"])["activity_segments"] = padding
        assert sc.classify_write_kind(base, proposed, encoding=encoding) == N


def test_takeover_increment_alone_exceeds_cost_via_envelope_field_is_normal():
    for layout, encoding in (("v4", sc.StateEncoding.LEGACY_PRETTY), ("v5", sc.StateEncoding.CANONICAL)):
        base, proposed = _takeover(layout)
        huge = "U" * (sc.next_takeover_cost(base) + 5000)
        (proposed if layout == "v4" else proposed["extensions"])["updated_at"] = huge
        assert sc.classify_write_kind(base, proposed, encoding=encoding) == N


def test_withdraw_does_not_shrink_via_allowed_envelope_field_is_normal():
    base, proposed = _withdraw()
    proposed["extensions"]["updated_at"] = "U" * 100000  # allowed key, but makes it not shrink
    assert sc.classify_write_kind(base, proposed, encoding=sc.StateEncoding.CANONICAL) == N


def test_withdraw_rejects_when_proposed_fails_to_encode():
    """NaN in the allowed envelope field: encode fails on one side only (kills ``encode_smaller_none_check_off``)."""
    base, proposed = _withdraw()
    proposed["extensions"]["updated_at"] = float("nan")
    assert sc._encode_len_for(base, sc.StateEncoding.CANONICAL) is not None
    assert sc._encode_len_for(proposed, sc.StateEncoding.CANONICAL) is None
    assert sc.classify_write_kind(base, proposed, encoding=sc.StateEncoding.CANONICAL) == N


def test_withdraw_rejects_when_encode_length_is_exactly_equal():
    """Pad an allowed field one char at a time until lengths are exactly equal, not smaller (kills ``wd_shrink_lt_le``)."""
    base, proposed = _withdraw()
    base_len = sc._encode_len_for(base, sc.StateEncoding.CANONICAL)
    padding, current = 0, None
    while current is None or current < base_len:
        proposed["extensions"]["updated_at"] = "P" * padding
        current = sc._encode_len_for(proposed, sc.StateEncoding.CANONICAL)
        padding += 1
    assert current == base_len, (current, base_len)
    assert sc.classify_write_kind(base, proposed, encoding=sc.StateEncoding.CANONICAL) == N


def test_encoding_choice_changes_the_verdict_for_the_same_diff():
    """Same diff: stop-halt under LEGACY_PRETTY (NaN-representable), normal under CANONICAL (kills ``enc_always_canonical``)."""
    doc = _flat_doc()
    doc["custom_score"] = float("nan")
    proposed = dict(doc)
    proposed.update(halt_reason="x", phase="halted", loop_active=False)
    assert sc.classify_write_kind(doc, proposed, encoding=sc.StateEncoding.LEGACY_PRETTY) == H_
    assert sc.classify_write_kind(doc, proposed, encoding=sc.StateEncoding.CANONICAL) == N


# ===========================================================================
# 7. Drift detection: bin/mission-state.py's real halt writer stays inside
#    ``_HALT_RIDE_ALONG_FIELDS`` (``_TIMING_ACTIVITY_FIELDS``, imported from
#    the kernel, not hand-copied).
# ===========================================================================


def test_v4_real_halt_writer_fields_are_inside_the_allow_list():
    for category in [c.value for c in HaltCategory]:
        before, after = _flat_halt_before_after(category)
        touched = sc._diff_keys(before, after) - sc._HALT_FIELD_NAMES - sc._ENVELOPE_KEYS
        assert touched <= sc._HALT_RIDE_ALONG_FIELDS, (category, touched - sc._HALT_RIDE_ALONG_FIELDS)
