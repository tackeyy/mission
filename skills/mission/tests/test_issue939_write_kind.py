"""E0b-2b-前半 (#939): write_kind classification from a base/proposed diff.

Covers only ``classify_write_kind`` and its helpers (diff comparison, the
lease-transition judgement, the withdraw/reducer cross-check, and the
halt/takeover field-bound guards) -- never the caller's say-so, always
derived from the base/proposed diff. The judgement half that *uses* the
classification (legacy-full detection, the increment-cap check, and
``state_capacity_verdict`` itself) is a follow-up module (E0b-2b-後半/#933)
that imports this one; its own regression table is not duplicated here.
"""
from __future__ import annotations

import copy

import pytest

from mission_kernel.fresh_review import (
    FreshReviewProjection, FreshReviewRecord, candidate_identity,
    canonical_digest, decode_request, projection_document, reserve_request,
    withdraw_request,
)
from mission_kernel import state_capacity as sc

from .test_issue895_fresh_review import ADAPTER
from .test_issue936_state_capacity_reservation import TS27, _flat_doc, _minimal_contract


# ---------------------------------------------------------------------------
# v5 document builders (the reservation-half fixtures only exercise v4/flat;
# write_kind classification needs both layouts for every branch).
# ---------------------------------------------------------------------------


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


def _base(layout, **kwargs):
    if layout == "v4":
        return _flat_doc(**kwargs)
    return _v5_doc(**kwargs)


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


def _history(n):
    return [_entry("a", f"id{i}", i + 1) for i in range(n)]


def _no_lease_base(layout):
    """A base whose lease was genuinely never acquired (fencing_epoch
    absent/empty) -- the only shape ``_takeover_case`` admits as "initial"."""
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


# ---------------------------------------------------------------------------
# Mixed diffs must not be misclassified as a pure stop kind.
# ---------------------------------------------------------------------------


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
    proposed["unrelated_extra_field"] = "x"
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


# PR #938 round-1 review follow-ups: classification must stay inside the
# "許される遷移だけ" boundary even when the allow-listed key set alone
# cannot distinguish a legitimate mutation from a piggybacked one.


def test_halt_v5_rejects_bogus_control_field_mixed_in():
    doc = _v5_doc()
    proposed = copy.deepcopy(doc)
    proposed["control"].update(halt_reason="x", phase="halted", loop_active=False)
    proposed["control"]["bogus_control_field"] = "x"
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_halt_v5_rejects_bogus_top_level_field_mixed_in():
    doc = _v5_doc()
    proposed = copy.deepcopy(doc)
    proposed["control"].update(halt_reason="x", phase="halted", loop_active=False)
    proposed["bogus_top_field"] = "x"
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_halt_v5_rejects_oversized_extensions_mirror_even_when_control_is_fine():
    doc = _v5_doc()
    proposed = copy.deepcopy(doc)
    proposed["control"].update(halt_reason="x", phase="halted", loop_active=False)
    proposed["extensions"]["halt_reason"] = "\x01" * (sc.HALT_REASON_MAX_CHARS + 1)
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_halt_reason_at_exactly_the_bound_is_still_stop_halt():
    doc = _flat_doc()
    proposed = _apply_control(doc, "v4", halt_reason="\x01" * sc.HALT_REASON_MAX_CHARS,
                               phase="halted", loop_active=False)
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.STOP_HALT


def test_takeover_v5_rejects_bogus_lease_field_mixed_in():
    doc = _v5_doc(lease_history=[])
    proposed = copy.deepcopy(doc)
    proposed["lease"]["lease_history"] = [_entry("a", "b", 1)]
    proposed["lease"]["owner_session_id"] = "c"
    proposed["lease"]["lease_id"] = "d"
    proposed["lease"]["fencing_epoch"] = 2
    proposed["lease"]["bogus_lease_field"] = "x"
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_takeover_rejects_new_current_epoch_over_the_bound():
    doc = _flat_doc(lease_history=[])
    doc["fencing_epoch"] = sc.LEASE_EPOCH_MAX
    proposed = dict(doc)
    proposed["lease_history"] = [_entry("owner-1", "lease-1", sc.LEASE_EPOCH_MAX)]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = sc.LEASE_EPOCH_MAX + 1
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_takeover_rejects_non_conformant_entry_reason():
    doc = _flat_doc(lease_history=[])
    doc["fencing_epoch"] = 5
    proposed = dict(doc)
    proposed["lease_history"] = [_entry("owner-1", "lease-1", 5, reason="bad reason with spaces")]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 6
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_takeover_rejects_non_conformant_displaced_owner_mirrored_in_entry():
    doc = _flat_doc(lease_history=[])
    doc["owner_session_id"] = "bad owner with spaces"
    doc["fencing_epoch"] = 5
    proposed = dict(doc)
    proposed["lease_history"] = [_entry("bad owner with spaces", "lease-1", 5)]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 6
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_takeover_rejects_when_an_earlier_history_entry_was_altered():
    doc = _flat_doc(lease_history=_history(3))
    doc["fencing_epoch"] = 4
    proposed = dict(doc)
    altered = _history(3)
    altered[0] = _entry("tampered", "tampered", 1)
    proposed["lease_history"] = altered + [_entry("owner-1", "lease-1", 4)]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 5
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_takeover_rejects_an_entry_that_does_not_match_the_displaced_lease():
    doc = _flat_doc(lease_history=[])
    doc["fencing_epoch"] = 5
    proposed = dict(doc)
    # Pattern-conformant, but not the writer's own normalization of the
    # lease that was just displaced (owner-1/lease-1/epoch 5).
    proposed["lease_history"] = [_entry("totally-different-owner", "totally-different-id", 999)]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "lease-2"
    proposed["fencing_epoch"] = 6
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_takeover_rejects_non_conformant_new_current_lease_id():
    doc = _flat_doc(lease_history=[])
    doc["fencing_epoch"] = 5
    proposed = dict(doc)
    proposed["lease_history"] = [_entry("owner-1", "lease-1", 5)]
    proposed["owner_session_id"] = "owner-2"
    proposed["lease_id"] = "bad new lease id"
    proposed["fencing_epoch"] = 6
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_withdraw_match_does_not_crash_when_before_is_already_withdrawn():
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


# New in #939 (ported from the #933 draft): a halt/takeover diff whose own
# values already violate the #918 bound must not be granted the
# stop-halt/stop-takeover exemption (fail-closed -- see
# ``_halt_value_bounds_ok``/``_takeover_value_bounds_ok``).

_ONE_ENTRY_HISTORY = [{"owner_session_id": "owner-1", "lease_id": "lease-1", "fencing_epoch": 1,
                       "reason": "lease-expired-takeover", "at": "9999-12-31T23:59:59Z"}]


@pytest.mark.parametrize("mutator", [
    lambda d: {**d, "halt_reason": "\x01" * (sc.HALT_REASON_MAX_CHARS + 1), "phase": "halted", "loop_active": False},
    lambda d: {**d, "halt_reason": "x", "phase": "halted", "loop_active": False,
               "goal_dispatch_effective": "g" * (sc.GOAL_DISPATCH_REASON_MAX_CHARS + 1)},
    lambda d: {**d, "lease_history": _ONE_ENTRY_HISTORY, "owner_session_id": "bad owner with spaces",
               "lease_id": "lease-2", "fencing_epoch": 2},
    lambda d: {**d, "lease_history": _ONE_ENTRY_HISTORY, "owner_session_id": "owner-2",
               "lease_id": "lease-2", "fencing_epoch": sc.LEASE_EPOCH_MAX + 1},
])
def test_over_bound_halt_or_takeover_diff_is_classified_normal(mutator):
    doc = _flat_doc(lease_history=[])
    assert sc.classify_write_kind(doc, mutator(doc)) == sc.WriteKind.NORMAL


def test_nan_field_does_not_permanently_block_classification():
    """v4 can carry non-finite floats; a NaN field must not permanently
    mark the document "changed" and block stop-halt classification."""
    doc = _flat_doc()
    doc["custom_score"] = float("nan")
    proposed = dict(doc)
    proposed.update(halt_reason="x", phase="halted", loop_active=False)
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.STOP_HALT


# Presence, not just value, must be part of the diff: a key absent from
# base and present-with-None on proposed (or vice versa) looks unchanged
# under a ``.get()``-based comparison, which would silently hide an
# injected key from every allow-list check above.


def test_diff_keys_counts_a_key_that_appears_with_an_explicit_none_value():
    base = {"a": 1}
    proposed = {"a": 1, "sneaky": None}
    assert sc._diff_keys(base, proposed) == frozenset({"sneaky"})


def test_diff_keys_counts_a_key_that_disappears_from_an_explicit_none_value():
    base = {"a": 1, "sneaky": None}
    proposed = {"a": 1}
    assert sc._diff_keys(base, proposed) == frozenset({"sneaky"})


def test_halt_v4_rejects_a_key_that_appears_with_an_explicit_none_value():
    doc = _flat_doc()
    proposed = _apply_control(doc, "v4", halt_reason="x", phase="halted", loop_active=False)
    proposed["sneaky_injected_field"] = None
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


def test_halt_v5_rejects_a_control_key_that_appears_with_an_explicit_none_value():
    doc = _v5_doc()
    proposed = copy.deepcopy(doc)
    proposed["control"].update(halt_reason="x", phase="halted", loop_active=False)
    proposed["control"]["sneaky_injected_field"] = None
    assert sc.classify_write_kind(doc, proposed) == sc.WriteKind.NORMAL


# ======================================================================
# Classification regression table (抜け穴探索で見つかった反例の固定化).
#
# Ported from PR #938's PROBE_TABLE (#933 draft): only the rows that pin
# an expected write_kind are kept here, and each is checked directly
# through ``classify_write_kind`` rather than through
# ``state_capacity_verdict`` (over-capacity/accept/code assertions are
# #933's responsibility, not classification's -- classify_write_kind takes
# no ``encoded_len`` and cannot see capacity at all). v4 (flat) and v5
# (nested) are both exercised for every scenario that applies to both.
# ======================================================================


def _case(name, base, proposed, expect_write_kind):
    return pytest.param(base, proposed, expect_write_kind, id=name)


def _classification_table():
    cases = []

    # --- 1. pure halt: stop-halt, both layouts ---------------------------
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        cases.append(_case(f"pure_halt_{layout}", base, proposed, "stop-halt"))

    # --- 2. halt + fresh_review record advance mixed in: must NOT be
    #        stop-halt ----------------------------------------------------
    rec = _v5_pending_record()
    for layout in ("v4", "v5"):
        base = (
            _flat_doc(requests=[projection_document(_project(rec))["requests"][0]])
            if layout == "v4" else _v5_doc(requests=[rec])
        )
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        reserved = reserve_request(_project(rec), rec.request, operation_id="d1",
                                    intent_digest=ADAPTER, payload_digest=ADAPTER)
        proposed = _set_fresh_review(proposed, layout, reserved)
        cases.append(_case(f"halt+fresh_review_advance_mixed_{layout}", base, proposed, "normal"))

    # --- 3. halt + unrelated junk key mixed in: must NOT be stop-halt ----
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        if layout == "v4":
            proposed["unrelated_junk_field"] = "J" * 5000
        else:
            proposed["extensions"]["unrelated_junk"] = "J" * 5000
        cases.append(_case(f"halt+unrelated_junk_{layout}", base, proposed, "normal"))

    # --- 4. junk alone (no halt fields touched) --------------------------
    base_v5_only_junk = _v5_doc()
    proposed_v5_only_junk = copy.deepcopy(base_v5_only_junk)
    proposed_v5_only_junk["extensions"]["unrelated_junk"] = "J" * 5000
    cases.append(_case("junk_only_no_halt_v5", base_v5_only_junk, proposed_v5_only_junk, "normal"))

    # --- 5. loop_active-only change must not be stop-halt ----------------
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, loop_active=False)
        cases.append(_case(f"loop_active_only_{layout}", base, proposed, "normal"))

    # --- 6. halt_slot already written: second write is "normal" ---------
    for layout in ("v4", "v5"):
        base = _base(layout, halt_reason="already-halted")
        proposed = _base(layout, halt_reason="different-reason")
        cases.append(_case(f"halt_already_written_second_write_{layout}", base, proposed, "normal"))

    # --- 7. takeover + unrelated junk in extensions (v5): must NOT be
    #        stop-takeover -----------------------------------------------
    base_v5_to = _base("v5", lease_history=[])
    proposed_v5_to_junk = _set_lease(base_v5_to, "v5", history=[_entry("a", "b", 1)],
                                      owner="c", lease_id="d", epoch=2)
    proposed_v5_to_junk["extensions"]["unrelated_junk"] = "J" * 5000
    cases.append(_case("takeover+junk_extensions_v5", base_v5_to, proposed_v5_to_junk, "normal"))

    # --- 8. unknown halt_category / long goal_dispatch_* must not crash
    #        and must still be stop-halt, both layouts --------------------
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False,
                                   halt_category="totally-unknown-category-xyz")
        cases.append(_case(f"unknown_halt_category_{layout}", base, proposed, "stop-halt"))

        goal_fields = {"goal_dispatch_effective": "g" * 128, "goal_dispatch_host": "g" * 128,
                       "goal_dispatch_fallback_reason": "g" * 128}
        base2 = _base(layout)
        proposed2 = _apply_control(base2, layout, halt_reason="x", phase="halted", loop_active=False,
                                    **(goal_fields if layout == "v4" else {}))
        if layout == "v5":
            proposed2["extensions"].update(goal_fields)
        cases.append(_case(f"long_goal_dispatch_{layout}", base2, proposed2, "stop-halt"))

    # --- 9. withdraw: pure (accept) vs mixed with unrelated junk (reject)
    #        and a second record's advance (reject), both layouts. -------
    rec_a = _v5_pending_record(request_id="r1", nonce="n1")
    rec_b = _v5_pending_record(request_id="r2", nonce="n2")
    two_proj = _project(rec_a, rec_b)
    for layout in ("v4", "v5"):
        if layout == "v4":
            base = _base(layout, requests=projection_document(two_proj)["requests"])
        else:
            base = _base(layout, requests=[rec_a, rec_b])
        withdrawn = withdraw_request(two_proj, request_id="r1", operation_id="wd-1", fencing_epoch=1)
        proposed = _set_fresh_review(base, layout, withdrawn)
        cases.append(_case(f"withdraw_pure_{layout}", base, proposed, "withdraw"))

        proposed_junk = _set_fresh_review(base, layout, withdrawn)
        if layout == "v4":
            proposed_junk["unrelated_junk_field"] = "J" * 5000
        else:
            proposed_junk["extensions"]["unrelated_junk"] = "J" * 5000
        cases.append(_case(f"withdraw+junk_{layout}", base, proposed_junk, "normal"))

        mixed = reserve_request(withdrawn, rec_b.request, operation_id="d-r2",
                                 intent_digest=ADAPTER, payload_digest=ADAPTER)
        proposed_mixed = _set_fresh_review(base, layout, mixed)
        cases.append(_case(f"withdraw+other_record_advance_{layout}", base, proposed_mixed, "normal"))

    # --- 10. withdraw_fencing_epoch mismatch: must not be withdraw ------
    single_proj = _project(_v5_pending_record())
    base_v5_epoch = _v5_doc(requests=[_v5_pending_record()])
    tomb_mismatch = withdraw_request(single_proj, request_id="request-1", operation_id="wd-2",
                                      fencing_epoch=999)
    proposed_v5_epoch = _set_fresh_review(base_v5_epoch, "v5", tomb_mismatch)
    cases.append(_case("withdraw_epoch_mismatch_v5", base_v5_epoch, proposed_v5_epoch, "normal"))

    # --- 11. lease extension (lease_expires_at only, zero history growth):
    #         stop-takeover -----------------------------------------------
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(3))
        base = _set_lease(base, layout, epoch=4)
        if layout == "v4":
            proposed = {**base, "lease_expires_at": "9999-12-31T23:59:58Z"}
        else:
            proposed = copy.deepcopy(base)
            proposed["lease"]["lease_expires_at"] = "9999-12-31T23:59:58Z"
        cases.append(_case(f"lease_extension_zero_growth_{layout}", base, proposed, "stop-takeover"))

    # --- 11b. an owner/epoch change without history growth, when base
    #          already has a real lease, is NOT a legitimate stop-takeover
    #          mutation (it is neither an extension nor a takeover). -------
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(3))
        base = _set_lease(base, layout, epoch=4)
        proposed = _set_lease(base, layout, owner="renewed-owner")
        cases.append(_case(f"lease_owner_change_without_history_growth_{layout}", base, proposed, "normal"))

    # --- 12. lease history SHRINK must not be stop-takeover -------------
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(5))
        proposed = _set_lease(base, layout, history=_history(2))
        cases.append(_case(f"lease_history_shrink_{layout}", base, proposed, "normal"))

    # --- 13. lease history truncate-and-replace must not be stop-takeover
    replaced = [_entry("x", "yy", 99, reason="forged")] * 3
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(3))
        proposed = _set_lease(base, layout, history=replaced)
        cases.append(_case(f"lease_history_replace_same_length_{layout}", base, proposed, "normal"))

    # --- 14. lease history jump by more than 1 entry must not be
    #         stop-takeover -----------------------------------------------
    for layout in ("v4", "v5"):
        base = _base(layout, lease_history=_history(2))
        proposed = _set_lease(base, layout, history=_history(2) + _history(2))
        cases.append(_case(f"lease_history_jump_by_two_{layout}", base, proposed, "normal"))

    # --- 15. initial lease acquisition (base genuinely has no lease yet:
    #         fencing_epoch absent/empty, 0 history): stop-takeover -------
    for layout in ("v4", "v5"):
        base = _no_lease_base(layout)
        proposed = _set_lease(base, layout, owner="first-owner", lease_id="first-lease", epoch=1)
        cases.append(_case(f"lease_initial_acquisition_{layout}", base, proposed, "stop-takeover"))

    # --- 16. genesis, both layouts ---------------------------------------
    for layout in ("v4", "v5"):
        doc = _flat_doc() if layout == "v4" else _v5_doc()
        cases.append(_case(f"genesis_{layout}", None, doc, "genesis"))

    # --- 17. halt mixed with an actual lease change: still not stop-halt -
    for layout in ("v4", "v5"):
        base = _base(layout)
        proposed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        proposed = _set_lease(proposed, layout, owner="other-owner")
        cases.append(_case(f"halt+lease_change_mixed_{layout}", base, proposed, "normal"))

    # --- 18. padding with distinct halt_category literals ---------------
    for category in ("partial-done", "stagnation"):
        base = _base("v4")
        proposed = _apply_control(base, "v4", halt_reason="x", halt_category=category,
                                   phase="halted", loop_active=False)
        cases.append(_case(f"halt_category_{category}_v4", base, proposed, "stop-halt"))

    # --- 19. a halt carries the command's lease renewal (expiry only) and
    #         stays a halt; a lease identity change with it does not. An
    #         empty diff is no stop. --------------------------------------
    for layout in ("v4", "v5"):
        base = _base(layout)
        renewed = _apply_control(base, layout, halt_reason="x", phase="halted", loop_active=False)
        (renewed if layout == "v4" else renewed["lease"])["lease_expires_at"] = "9999-12-31T23:59:58Z"
        cases.append(_case(f"halt+lease_renewal_{layout}", base, renewed, "stop-halt"))
        swapped = _set_lease(renewed, layout, lease_id="other-lease")
        cases.append(_case(f"halt+lease_swap_{layout}", base, swapped, "normal"))
        cases.append(_case(f"empty_diff_{layout}", base, copy.deepcopy(base), "normal"))

    return cases


CLASSIFICATION_TABLE = _classification_table()


@pytest.mark.parametrize("base,proposed,expect_write_kind", CLASSIFICATION_TABLE)
def test_classification_regression_table(base, proposed, expect_write_kind):
    assert sc.classify_write_kind(base, proposed).value == expect_write_kind


def test_classification_table_has_at_least_40_cases():
    # Smaller than #933's PROBE_TABLE (>= 50): this table keeps only the
    # rows that pin an expected write_kind, dropping the verdict-only
    # rows (accept/code assertions, legacy-full, boundary counts).
    assert len(CLASSIFICATION_TABLE) >= 40, len(CLASSIFICATION_TABLE)
