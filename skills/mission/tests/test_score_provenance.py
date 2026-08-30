"""Issue #383: immutable, structured scoring provenance."""

import json
import importlib.util
import sys
import os
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


LIB_ROOT = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.5,
    "completeness": 4.0,
    "usability": 4.0,
}


def _load_state_module():
    script = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("state_for_timeout_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ignore_term_and_hang(_request):
    import signal
    import time

    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(0.01)


def test_approval_verifier_timeout_kills_a_term_ignoring_callback_within_bound(monkeypatch):
    """#383: callback timeouts cannot convert a pass attempt into a parent hang."""
    import time

    module = _load_state_module()
    monkeypatch.setattr(module, "_APPROVAL_VERIFIER_TIMEOUT_SEC", 0.05)
    monkeypatch.setattr(module, "_APPROVAL_VERIFIER_TERMINATE_GRACE_SEC", 0.05)
    # 検出したいのは「親が hang して返らない」ことであり、実行の速さではない。
    # 設定値 0.1 秒に対し境界 1 秒は余裕が 10 倍しかなく、負荷が乗るだけで落ちうる
    # （#703: 同じ形の予算が 2 度破綻している）。hang だけを捕える値へ広げる。
    _HANG_WATCHDOG_SECONDS = 60
    started = time.monotonic()
    with pytest.raises(ValueError, match="timed out"):
        module._run_approval_verifier(_ignore_term_and_hang, {})
    elapsed = time.monotonic() - started
    assert elapsed < _HANG_WATCHDOG_SECONDS, (
        f"parent did not return: elapsed={elapsed:.3f}s "
        f"watchdog={_HANG_WATCHDOG_SECONDS}s"
    )


def test_terminal_state_projection_binds_gate_relevant_fields_only():
    """Force approval receipts must be portable yet non-transferable state bindings."""
    from scoring_provenance import terminal_state_digest

    state = {
        "session_id": "session-a", "mission_id": "mission-a", "iteration": 2,
        "threshold": 4.0, "score_history": [{"iteration": 2, "composite": 3.0}],
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "passes": False, "loop_active": True, "terminal_outcome": None,
        "updated_at": "2026-08-10T00:00:00Z",
    }
    expected = terminal_state_digest(state)
    assert expected == terminal_state_digest({**state, "updated_at": "2026-08-12T00:00:00Z", "future_note": "ignored"})
    for field, replacement in (
        ("session_id", "session-b"), ("mission_id", "mission-b"), ("iteration", 3),
        ("threshold", 4.5), ("score_history", []), ("terminal_outcome", "completed_pass"),
    ):
        changed = {**state, field: replacement}
        assert terminal_state_digest(changed) != expected, field


def test_review_aggregate_reducer_rejects_duplicate_perspective_and_non_finite_score():
    """Canonical re-derivation must fail closed before a claim is compared."""
    from scoring_provenance import reduce_review_aggregate

    review = {
        "perspective": "neutral", "scores": ITEMS, "findings": [],
        "same_score_note": None,
    }
    with pytest.raises(ValueError, match="duplicate"):
        reduce_review_aggregate([review, review])
    invalid = {**review, "scores": {**ITEMS, "accuracy": float("inf")}}
    with pytest.raises(ValueError, match="finite"):
        reduce_review_aggregate([invalid])


def test_review_aggregate_reducer_keeps_consensus_out_of_score_items():
    """Agreement is metadata: every newly derived score has exactly four axes."""
    from scoring_provenance import reduce_review_aggregate

    first = {
        "perspective": "first", "scores": ITEMS, "findings": [],
        "same_score_note": None,
    }
    second = {
        "perspective": "second", "scores": ITEMS, "findings": [],
        "same_score_note": None,
    }

    single = reduce_review_aggregate([first])
    multiple = reduce_review_aggregate([first, second])

    assert set(single["items"]) == set(ITEMS)
    assert set(multiple["items"]) == set(ITEMS)
    assert multiple["review_agreement"] == 5.0
    assert multiple["composite"] == 4.25


@pytest.mark.parametrize("iteration", [None, True, False, 1.0, "1", 0, -1])
def test_review_aggregate_reducer_requires_canonical_review_iteration(iteration):
    """Untrusted archive inputs cannot be replayed across scoring iterations."""
    from scoring_provenance import reduce_review_aggregate

    review = {
        "schema": "mission-review/1", "iteration": iteration, "perspective": "neutral",
        "scores": ITEMS, "findings": [], "same_score_note": "independent evidence supports each axis",
    }
    with pytest.raises(ValueError, match="iteration"):
        reduce_review_aggregate([review], expected_iteration=1)


def test_review_aggregate_reducer_rejects_mixed_review_iterations():
    from scoring_provenance import reduce_review_aggregate

    first = {"schema": "mission-review/1", "iteration": 1, "perspective": "first", "scores": ITEMS, "findings": [], "same_score_note": "independent evidence supports each axis"}
    second = {"schema": "mission-review/1", "iteration": 2, "perspective": "second", "scores": ITEMS, "findings": [], "same_score_note": "independent evidence supports each axis"}
    with pytest.raises(ValueError, match="iteration"):
        reduce_review_aggregate([first, second], expected_iteration=2)


def _review(path):
    payload = {
        "schema": "mission-review/1", "perspective": "neutral",
        "iteration": 1, "scores": ITEMS, "findings": [],
        "same_score_note": None, "notes": "neutral fixture",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_new_scoring_json_requires_complete_provenance(state_dir, run_cli, tmp_path):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    scoring = tmp_path / "score.json"
    scoring.write_text(json.dumps({"items": ITEMS}), encoding="utf-8")
    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent)
    assert result.returncode == 2
    assert "provenance" in result.stderr


def test_manual_score_capture_uses_its_own_typed_archive_and_revalidates_it(state_dir, run_cli, read_state, tmp_path):
    """#383: manual imports never borrow review aggregate evidence."""
    from scoring_provenance import digest

    unsigned = {
        "schema": "mission-manual-score/1", "session_id": "test", "mission_id": "abc12345",
        "iteration": 1, "items": ITEMS, "composite": 4.25, "min_item": 4.0,
        "review_agreement": 4.5,
        "open_high": 0, "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "source_evidence_ref": {"kind": "manual-source-evidence", "ref": "sha256:" + "1" * 64,
                                "digest": "sha256:" + "1" * 64},
        "imported_at": "2026-08-10T00:00:00Z",
    }
    manual = {**unsigned, "input_digest": digest(unsigned)}
    source = tmp_path / "manual.json"
    source.write_text(json.dumps(manual), encoding="utf-8")
    scoring = tmp_path / "manual-scoring.json"
    captured = run_cli("manual-score-capture", "--input", str(source), "--out", str(scoring), cwd=state_dir.parent)
    assert captured.returncode == 0, captured.stderr
    payload = json.loads(scoring.read_text())
    provenance = payload["score_provenance"]
    assert provenance["score_source"] == "manual-import"
    assert "manual_evidence_ref" in provenance
    assert "review_evidence_ref" not in provenance
    pushed = run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent)
    assert pushed.returncode == 0, pushed.stderr
    entry = read_state(state_dir)["score_history"][-1]
    archive = state_dir.parent / entry["manual_evidence_ref"]["path"]
    archive.write_text("{}", encoding="utf-8")
    rejected = run_cli("mark-passes", cwd=state_dir.parent)
    assert rejected.returncode == 2
    assert "digest mismatch" in rejected.stderr


def test_manual_score_capture_rejects_same_size_path_swap_during_single_fd_read(
        state_dir, tmp_path, monkeypatch):
    """A pathname replacement during capture cannot detach the input binding."""
    from scoring_provenance import digest

    unsigned = {
        "schema": "mission-manual-score/1", "session_id": "test", "mission_id": "abc12345",
        "iteration": 1, "items": ITEMS, "composite": 4.25, "min_item": 4.0,
        "review_agreement": 4.5,
        "open_high": 0, "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "source_evidence_ref": {"kind": "manual-source-evidence", "ref": "sha256:" + "1" * 64,
                                "digest": "sha256:" + "1" * 64},
        "imported_at": "2026-08-10T00:00:00Z",
    }
    source = tmp_path / "manual.json"
    source.write_text(json.dumps({**unsigned, "input_digest": digest(unsigned)}), encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    swapped_unsigned = {**unsigned, "source_evidence_ref": {
        "kind": "manual-source-evidence", "ref": "sha256:" + "2" * 64,
        "digest": "sha256:" + "2" * 64,
    }}
    replacement.write_text(
        json.dumps({**swapped_unsigned, "input_digest": digest(swapped_unsigned)}), encoding="utf-8"
    )
    assert replacement.stat().st_size == source.stat().st_size
    before = (state_dir / "sessions" / "test.json").read_bytes()
    module = _load_state_module()
    original_read = module.os.read
    swapped = False

    def read_then_swap(fd, size):
        nonlocal swapped
        if not swapped:
            swapped = True
            replacement.replace(source)
        return original_read(fd, size)

    monkeypatch.setattr(module.os, "read", read_then_swap)
    monkeypatch.chdir(state_dir.parent)
    with pytest.raises(SystemExit) as exc:
        module.cmd_manual_score_capture(SimpleNamespace(input=str(source), out=str(tmp_path / "score.json")))
    assert exc.value.code == 2
    assert (state_dir / "sessions" / "test.json").read_bytes() == before
    assert not (tmp_path / "score.json").exists()


@pytest.mark.parametrize("case", ["symlink", "fifo", "hardlink", "oversize", "cross-session", "mixed-iteration"])
def test_manual_score_capture_rejects_untrusted_input_without_mutating_state(state_dir, run_cli, tmp_path, case):
    """Manual import fails closed for hostile files and incompatible typed claims."""
    from scoring_provenance import digest

    unsigned = {
        "schema": "mission-manual-score/1", "session_id": "test", "mission_id": "abc12345",
        "iteration": 1, "items": ITEMS, "composite": 4.25, "min_item": 4.0,
        "review_agreement": 4.5, "open_high": 0,
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "source_evidence_ref": {"kind": "manual-source-evidence", "ref": "sha256:" + "2" * 64,
                                "digest": "sha256:" + "2" * 64},
        "imported_at": "2026-08-10T00:00:00Z",
    }
    if case == "cross-session":
        unsigned["session_id"] = "other"
    if case == "mixed-iteration":
        unsigned["iteration"] = 2
    payload = {**unsigned, "input_digest": digest(unsigned)}
    source = tmp_path / "manual.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    if case == "symlink":
        link = tmp_path / "manual-link.json"
        link.symlink_to(source)
        source = link
    elif case == "hardlink":
        linked = tmp_path / "manual-hardlink.json"
        os.link(source, linked)
        source = linked
    elif case == "fifo":
        fifo = tmp_path / "manual.fifo"
        os.mkfifo(fifo)
        source = fifo
    elif case == "oversize":
        source.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
    before = (state_dir / "sessions" / "test.json").read_bytes()
    result = run_cli("manual-score-capture", "--input", str(source), "--out", str(tmp_path / "out.json"), cwd=state_dir.parent)
    assert result.returncode == 2
    assert (state_dir / "sessions" / "test.json").read_bytes() == before


def test_review_finalize_records_immutable_evidence_refs(state_dir, run_cli, read_state, tmp_path):
    review = _review(tmp_path / "review.json")
    result = run_cli("review-finalize", "--iteration", "1", "--input", str(review), cwd=state_dir.parent)
    assert result.returncode == 0, result.stderr
    entry = read_state(state_dir)["score_history"][0]
    assert entry["score_provenance"]["score_source"] == "scoring-json"
    assert entry["review_evidence_ref"]["kind"] == "review-aggregate"
    assert entry["review_evidence_ref"]["digest"].startswith("sha256:")
    assert entry["revision_scope"]["kind"] == "not-applicable"
    assert "generation" in entry["review_evidence_ref"]


def test_boolean_only_force_pass_is_rejected(state_dir, run_cli):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    result = run_cli("mark-passes", "--force", "--reason", "test", "--approved-by-user", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "approval-evidence-ref" in result.stderr


def test_legacy_state_remains_readable_without_rewrite(state_dir, run_cli, read_state):
    before = (state_dir / "sessions" / "test.json").read_bytes()
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert (state_dir / "sessions" / "test.json").read_bytes() == before


def test_mark_passes_rejects_tampered_review_evidence(state_dir, run_cli, read_state, tmp_path):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    assert run_cli("review-finalize", "--iteration", "1", "--input", str(review), cwd=state_dir.parent).returncode == 0
    ref = read_state(state_dir)["score_history"][0]["review_evidence_ref"]
    with open(state_dir.parent / ref["path"], "ab") as handle:
        handle.write(b"tamper")
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "digest mismatch" in result.stderr


def test_mark_passes_rejects_score_values_that_disagree_with_aggregate_derivation(state_dir, run_cli, read_state, tmp_path):
    """A content-addressed aggregate must bind the values the gate consumes."""
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    out = tmp_path / "score.json"
    assert run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review), "--out", str(out), cwd=state_dir.parent).returncode == 0
    payload = json.loads(out.read_text())
    payload["items"] = {key: 5.0 for key in payload["items"]}
    out.write_text(json.dumps(payload))
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(out), cwd=state_dir.parent).returncode == 2


def test_push_score_rederives_claim_from_archived_inputs_not_self_consistent_claim(state_dir, run_cli, tmp_path):
    """A rehashed archive cannot pair low review inputs with a high claim."""
    review, out = _review(tmp_path / "review.json"), tmp_path / "score.json"
    assert run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review), "--out", str(out), cwd=state_dir.parent).returncode == 0
    payload = json.loads(out.read_text())
    ref = payload["score_provenance"]["review_evidence_ref"]
    archive = state_dir.parent / ref["path"]
    evidence = json.loads(archive.read_text())
    evidence["inputs"][0]["scores"] = {
        "mission_achievement": 3.0, "accuracy": 3.1,
        "completeness": 3.0, "usability": 3.2,
    }
    content = (json.dumps(evidence, ensure_ascii=False, indent=2) + "\n").encode()
    archive.write_bytes(content)
    ref["digest"] = "sha256:" + hashlib.sha256(content).hexdigest()
    ref["generation"] = ref["digest"][7:23]
    out.write_text(json.dumps(payload))
    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(out), cwd=state_dir.parent)
    assert result.returncode == 2
    assert "derived from inputs" in result.stderr


def test_active_legacy_state_cannot_mark_passes_with_unprovenanced_score(state_dir, run_cli):
    """Schema version never relaxes the active pass gate."""
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state["schema_version"] = 2
    state["score_history"] = [{"iteration": 1, "composite": 5.0, "min_item": 5.0,
                               "items": ITEMS, "open_high": 0}]
    before = json.dumps(state, sort_keys=True).encode()
    state_path.write_text(json.dumps(state))
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "provenance" in result.stderr
    assert json.dumps(json.loads(state_path.read_text()), sort_keys=True).encode() == before


def test_git_revision_scope_requires_exact_pair(state_dir, run_cli, tmp_path):
    review = _review(tmp_path / "review.json")
    result = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review),
                     "--base-sha", "bad", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "exact" in result.stderr


def test_resubmit_rejects_score_change_without_a_new_aggregate(state_dir, run_cli, read_state, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    review = _review(tmp_path / "review.json")
    assert run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review), "--out", str(first), cwd=state_dir.parent).returncode == 0
    payload = json.loads(first.read_text())
    payload["items"]["accuracy"] = 4.4
    second.write_text(json.dumps(payload), encoding="utf-8")
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(first), cwd=state_dir.parent).returncode == 0
    old_path = read_state(state_dir)["score_history"][0]["scoring_evidence_path"]
    old_bytes = (state_dir.parent / old_path).read_bytes()
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(second),
                   "--resubmit-reason", "neutral correction", cwd=state_dir.parent).returncode == 2
    assert (state_dir.parent / old_path).read_bytes() == old_bytes


def test_manual_import_cannot_relabel_a_review_aggregate(state_dir, run_cli, read_state, tmp_path):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    out = tmp_path / "score.json"
    assert run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review), "--out", str(out), cwd=state_dir.parent).returncode == 0
    payload = json.loads(out.read_text())
    payload["score_provenance"]["score_source"] = "manual-import"
    out.write_text(json.dumps(payload))
    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(out), cwd=state_dir.parent)
    assert result.returncode == 2
    assert "manual-import" in result.stderr


@pytest.mark.parametrize("attack", ["absolute", "traversal", "nul", "symlink", "fifo", "oversize", "utf8", "same-size", "swap"])
def test_push_score_rejects_adversarial_evidence_refs(state_dir, run_cli, tmp_path, attack):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    review, out = _review(tmp_path / "review.json"), tmp_path / "score.json"
    assert run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review), "--out", str(out), cwd=state_dir.parent).returncode == 0
    payload = json.loads(out.read_text())
    ref = payload["score_provenance"]["review_evidence_ref"]
    target = state_dir.parent / ref["path"]
    if attack == "absolute":
        ref["path"] = str(target)
    elif attack == "traversal":
        ref["path"] = ".mission-state/archive/../sessions/test.json"
    elif attack == "nul":
        ref["path"] = ".mission-state/archive/x\x00.json"
    else:
        target.unlink()
        if attack == "symlink":
            target.symlink_to(tmp_path / "review.json")
        elif attack == "fifo":
            os.mkfifo(target)
        elif attack == "oversize":
            target.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        elif attack == "utf8":
            target.write_bytes(b"\xff")
        elif attack == "same-size":
            target.write_bytes(b" " * len(json.dumps({"schema": "mission-review-aggregate/1"})))
        else:  # replacement after unlink models a pathname swap, not a retained FD.
            target.write_text(json.dumps({"schema": "mission-review-aggregate/1"}), encoding="utf-8")
    out.write_text(json.dumps(payload))
    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(out), cwd=state_dir.parent)
    assert result.returncode == 2
    assert "provenance" in result.stderr


@pytest.mark.parametrize("attack", ["absolute", "traversal", "nul", "symlink", "fifo", "oversize", "utf8", "same-size", "swap"])
def test_mark_passes_rejects_adversarial_evidence_refs(state_dir, run_cli, read_state, tmp_path, attack):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    assert run_cli("review-finalize", "--iteration", "1", "--input", str(review), cwd=state_dir.parent).returncode == 0
    ref = read_state(state_dir)["score_history"][0]["review_evidence_ref"]
    target = state_dir.parent / ref["path"]
    if attack == "absolute": ref["path"] = str(target)
    elif attack == "traversal": ref["path"] = ".mission-state/archive/../sessions/test.json"
    elif attack == "nul": ref["path"] = ".mission-state/archive/x\x00.json"
    else:
        target.unlink()
        if attack == "symlink": target.symlink_to(tmp_path / "review.json")
        elif attack == "fifo": os.mkfifo(target)
        elif attack == "oversize": target.write_bytes(b"x" * (4 * 1024 * 1024 + 1))
        elif attack == "utf8": target.write_bytes(b"\xff")
        else: target.write_text(json.dumps({"schema": "mission-review-aggregate/1"}), encoding="utf-8")
    session = state_dir / "sessions" / "test.json"
    document = json.loads(session.read_text())
    document["score_history"][0]["review_evidence_ref"]["path"] = ref["path"]
    document["score_history"][0]["score_provenance"]["review_evidence_ref"]["path"] = ref["path"]
    session.write_text(json.dumps(document))
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "provenance" in result.stderr


def test_force_pass_has_no_builtin_trusted_verifier(state_dir, run_cli):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    result = run_cli(
        "mark-passes", "--force", "--reason", "bounded override", "--approved-by-user",
        "--approval-evidence-ref", "sha256:" + "a" * 64,
        "--approved-actor", "role:owner", "--approved-at", datetime.now(timezone.utc).isoformat(),
        "--reason-code", "user-override", "--approval-verifier", "neutral-test",
        cwd=state_dir.parent,
    )
    assert result.returncode == 2
    assert "not configured" in result.stderr


def test_force_pass_bootstraps_only_a_registered_entry_point(state_dir, run_cli, tmp_path):
    """A fresh CLI may load a verifier only through the fixed registry + entry-point contract."""
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    package_root = tmp_path / "providers"
    package_root.mkdir()
    (package_root / "fixture_provider.py").write_text(
        "import hashlib, json\n"
        "from datetime import datetime, timezone\n"
        "def verify(request):\n"
        " p = '.mission-state/archive/fixture-receipt.json'\n"
        " doc = {'schema':'mission-force-approval-receipt/1','session_id':request['session_id'],'mission_id':request['mission_id'],'revision_scope':request['revision_scope'],'terminal_object_digest':request['terminal_object_digest'],'event_nonce':request['event_nonce'],'request_digest':request['request_digest']}\n"
        " raw = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()\n"
        " open(p, 'wb').write(raw)\n"
        " ref = {'kind':'approval-receipt','path':p,'digest':'sha256:'+hashlib.sha256(raw).hexdigest()}\n"
        " return {'schema':'mission-force-approval-response/1','decision':'approved','verifier_id':'fixture-verifier','request_digest':request['request_digest'],'receipt_ref':ref,'verified_at':datetime.now(timezone.utc).isoformat()}\n",
        encoding="utf-8",
    )
    dist_info = package_root / "fixture_provider-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: fixture-provider\nVersion: 1.0\n")
    (dist_info / "entry_points.txt").write_text("[mission.approval_verifiers]\nfixture-entry = fixture_provider:verify\n")
    registry = tmp_path / "host-config" / "mission"
    registry.mkdir(parents=True)
    (state_dir / "archive").mkdir()
    (registry / "approval-verifiers.json").write_text(json.dumps({
        "schema": "mission-approval-verifier-registry/2",
        "verifiers": [{
            "id": "fixture-verifier", "entry_point": "fixture-entry",
            "distribution": "fixture-provider", "version": "1.0",
            "source_digest": "sha256:" + hashlib.sha256((package_root / "fixture_provider.py").read_bytes()).hexdigest(),
        }],
    }))
    result = run_cli(
        "mark-passes", "--force", "--reason", "bounded override", "--approved-by-user",
        "--approval-evidence-ref", "sha256:" + "a" * 64,
        "--approved-actor", "role:owner", "--approved-at", datetime.now(timezone.utc).isoformat(),
        "--reason-code", "user-override", "--approval-verifier", "fixture-verifier",
        cwd=state_dir.parent, env_extra={"PYTHONPATH": str(package_root), "XDG_CONFIG_HOME": str(tmp_path / "host-config")},
    )
    assert result.returncode == 0, result.stderr
    recorded = json.loads((state_dir / "sessions" / "test.json").read_text())
    assert recorded["passes"] is True
    assert recorded["force_approval"]["response"]["verifier_id"] == "fixture-verifier"


@pytest.mark.parametrize("mode", ["load-error", "callback-error", "callback-hang"])
def test_force_pass_verifier_failures_leave_state_bytes_unchanged(state_dir, run_cli, tmp_path, mode):
    """#383: provider load/callback failures are bounded and precede every state write."""
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    state_path = state_dir / "sessions" / "test.json"
    state_path.write_text(json.dumps(state))
    package_root = tmp_path / "providers"; package_root.mkdir()
    body = {
        "load-error": "raise RuntimeError('load fails')\n",
        "callback-error": "def verify(request):\n raise RuntimeError('callback fails')\n",
        "callback-hang": "import signal, time\ndef verify(request):\n signal.signal(signal.SIGTERM, signal.SIG_IGN)\n while True: time.sleep(.01)\n",
    }[mode]
    (package_root / "fixture_provider.py").write_text(body)
    dist_info = package_root / "fixture_provider-1.0.dist-info"; dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: fixture-provider\nVersion: 1.0\n")
    (dist_info / "entry_points.txt").write_text("[mission.approval_verifiers]\nfixture-entry = fixture_provider:verify\n")
    registry = tmp_path / "host-config" / "mission"; registry.mkdir(parents=True)
    (registry / "approval-verifiers.json").write_text(json.dumps({"schema": "mission-approval-verifier-registry/2", "verifiers": [{"id": "fixture-verifier", "entry_point": "fixture-entry", "distribution": "fixture-provider", "version": "1.0", "source_digest": "sha256:" + hashlib.sha256((package_root / "fixture_provider.py").read_bytes()).hexdigest()}]}))
    before = state_path.read_bytes()
    result = run_cli("mark-passes", "--force", "--reason", "bounded override", "--approved-by-user", "--approval-evidence-ref", "sha256:" + "a" * 64, "--approved-actor", "role:owner", "--approved-at", datetime.now(timezone.utc).isoformat(), "--reason-code", "user-override", "--approval-verifier", "fixture-verifier", cwd=state_dir.parent, env_extra={"PYTHONPATH": str(package_root), "XDG_CONFIG_HOME": str(tmp_path / "host-config")})
    assert result.returncode == 2
    assert state_path.read_bytes() == before


@pytest.mark.parametrize("attack", ["malformed", "duplicate", "traversal", "symlink", "fifo", "hardlink", "oversize"])
def test_force_bootstrap_rejects_unsafe_registry_without_mutating_state(state_dir, run_cli, tmp_path, attack):
    registry_dir = state_dir.parent / ".mission"
    registry_dir.mkdir()
    registry = registry_dir / "approval-verifiers.json"
    if attack == "malformed":
        registry.write_text("{")
    elif attack == "duplicate":
        registry.write_text('{"schema":"mission-approval-verifier-registry/1","schema":"x","verifiers":[]}')
    elif attack == "traversal":
        registry.write_text(json.dumps({"schema": "mission-approval-verifier-registry/1", "verifiers": [{"id": "fixture-verifier", "entry_point": "../unsafe"}]}))
    elif attack == "symlink":
        registry.symlink_to(tmp_path / "elsewhere.json")
    elif attack == "fifo":
        os.mkfifo(registry)
    elif attack == "hardlink":
        source = tmp_path / "registry.json"
        source.write_text(json.dumps({"schema": "mission-approval-verifier-registry/1", "verifiers": []}))
        os.link(source, registry)
    else:
        registry.write_bytes(b"x" * (64 * 1024 + 1))
    before = (state_dir / "sessions" / "test.json").read_bytes()
    result = run_cli(
        "mark-passes", "--force", "--reason", "bounded override", "--approved-by-user",
        "--approval-evidence-ref", "sha256:" + "a" * 64,
        "--approved-actor", "role:owner", "--approved-at", datetime.now(timezone.utc).isoformat(),
        "--reason-code", "user-override", "--approval-verifier", "fixture-verifier", cwd=state_dir.parent,
    )
    assert result.returncode == 2
    assert (state_dir / "sessions" / "test.json").read_bytes() == before


def test_approval_verifier_callback_returns_typed_verified_envelope(tmp_path):
    script = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("state_for_test", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    request = module.build_approval_request(
        session_id="test", mission_id="abc12345", revision_scope={"kind": "not-applicable", "reason_code": "non-git"},
        terminal_object_digest="sha256:" + "b" * 64, approval_evidence_ref="sha256:" + "a" * 64,
        approved_actor="role:owner", approved_at=datetime.now(timezone.utc).isoformat(),
        reason_code="user-override", event_nonce="c" * 64,
    )
    receipt = {"kind": "approval-receipt", "path": ".mission-state/archive/receipt.json", "digest": "sha256:" + "d" * 64}
    module.register_approval_verifier("fixture-verifier", lambda _: {
        "schema": "mission-force-approval-response/1", "decision": "approved", "verifier_id": "fixture-verifier",
        "request_digest": request["request_digest"], "receipt_ref": receipt,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    })
    result = module.verify_force_approval(request, "fixture-verifier")
    assert result["consumed"] is True
    assert result["response"]["verifier_id"] == "fixture-verifier"


def test_historical_approval_envelope_keeps_canonicality_after_freshness_window():
    """Audit validation is historical, whereas the writer's validation is fresh."""
    from scoring_provenance import build_request, digest, validate_recorded_envelope
    recorded_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    request = build_request(
        session_id="test", mission_id="abc12345", revision_scope={"kind": "not-applicable", "reason_code": "non-git"},
        terminal_object_digest="sha256:" + "b" * 64, approval_evidence_ref="sha256:" + "a" * 64,
        approved_actor="role:owner", approved_at=recorded_at.isoformat(), reason_code="user-override",
        event_nonce="c" * 64, now=recorded_at,
    )
    receipt = {"kind": "approval-receipt", "path": ".mission-state/archive/receipt.json", "digest": "sha256:" + "d" * 64}
    envelope = {"request": request, "response": {
        "schema": "mission-force-approval-response/1", "decision": "approved", "verifier_id": "fixture-verifier",
        "request_digest": request["request_digest"], "receipt_ref": receipt, "verified_at": recorded_at.isoformat(),
    }, "receipt_ref": receipt, "consumed": True}
    with pytest.raises(ValueError):
        validate_recorded_envelope(envelope, now=datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert validate_recorded_envelope(envelope, now=datetime(2026, 1, 3, tzinfo=timezone.utc), require_fresh=False)["consumed"]


def test_receipt_must_bind_the_exact_canonical_request(tmp_path):
    from scoring_provenance import build_request, validate_receipt_binding
    now = datetime.now(timezone.utc)
    request = build_request(
        session_id="test", mission_id="abc12345", revision_scope={"kind": "not-applicable", "reason_code": "non-git"},
        terminal_object_digest="sha256:" + "b" * 64, approval_evidence_ref="sha256:" + "a" * 64,
        approved_actor="role:owner", approved_at=now.isoformat(), reason_code="user-override", event_nonce="c" * 64,
    )
    receipt_doc = {"schema": "mission-force-approval-receipt/1", "session_id": "test", "mission_id": "abc12345",
                   "revision_scope": request["revision_scope"], "terminal_object_digest": request["terminal_object_digest"],
                   "event_nonce": request["event_nonce"], "request_digest": request["request_digest"]}
    path = tmp_path / ".mission-state" / "archive" / "receipt.json"; path.parent.mkdir(parents=True)
    payload = json.dumps(receipt_doc, sort_keys=True).encode(); path.write_bytes(payload)
    receipt = {"kind": "approval-receipt", "path": ".mission-state/archive/receipt.json",
               "digest": "sha256:" + __import__("hashlib").sha256(payload).hexdigest()}
    envelope = {"request": request, "response": {"schema": "mission-force-approval-response/1", "decision": "approved",
                "verifier_id": "fixture-verifier", "request_digest": request["request_digest"], "receipt_ref": receipt,
                "verified_at": now.isoformat()}, "receipt_ref": receipt, "consumed": True}
    validate_receipt_binding(tmp_path, envelope)
    receipt_doc["session_id"] = "other"; path.write_text(json.dumps(receipt_doc))
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_receipt_binding(tmp_path, envelope)


@pytest.mark.parametrize(
    ("schema", "expected_error"),
    [
        (None, "provenance"),
        (3, "provenance"),
        ("4", "schema_version"),
        (4.0, "schema_version"),
    ],
    ids=["missing", "v3", "string-v4", "float-v4"],
)
def test_push_score_requires_provenance_despite_schema_downgrade(
    state_dir, run_cli, schema, expected_error
):
    document = json.loads((state_dir / "sessions" / "test.json").read_text())
    if schema is None:
        document.pop("schema_version")
    else:
        document["schema_version"] = schema
    before = json.dumps(document, sort_keys=True)
    (state_dir / "sessions" / "test.json").write_text(json.dumps(document))
    score = state_dir.parent / "score.json"
    score.write_text(json.dumps({"items": ITEMS}))
    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(score), cwd=state_dir.parent)
    assert result.returncode == 2
    assert expected_error in result.stderr
    assert json.dumps(json.loads((state_dir / "sessions" / "test.json").read_text()), sort_keys=True) == before


def test_new_score_entry_binds_content_addressed_score_artifact(state_dir, run_cli, read_state, tmp_path):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    assert run_cli("review-finalize", "--iteration", "1", "--input", str(review), cwd=state_dir.parent).returncode == 0
    entry = read_state(state_dir)["score_history"][0]
    ref = entry["score_provenance"]["scoring_evidence_ref"]
    assert ref["path"].startswith(".mission-state/")
    artifact = json.loads((state_dir.parent / ref["path"]).read_text())
    assert artifact["schema"] == "mission-scoring-artifact/1"
    assert artifact["binding"]["items"] == entry["items"]
    assert artifact["binding"]["composite"] == entry["composite"]


def test_mark_passes_rejects_hardlinked_evidence(state_dir, run_cli, read_state, tmp_path):
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    state["schema_version"] = 4
    (state_dir / "sessions" / "test.json").write_text(json.dumps(state))
    review = _review(tmp_path / "review.json")
    assert run_cli("review-finalize", "--iteration", "1", "--input", str(review), cwd=state_dir.parent).returncode == 0
    entry = read_state(state_dir)["score_history"][0]
    path = state_dir.parent / entry["score_provenance"]["review_evidence_ref"]["path"]
    os.link(path, tmp_path / "linked-evidence.json")
    result = run_cli("mark-passes", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "provenance" in result.stderr


def test_git_scope_is_rejected_outside_a_git_project(state_dir, run_cli, tmp_path):
    review = _review(tmp_path / "review.json")
    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(review),
        "--base-sha", "a" * 40, "--head-sha", "b" * 40, cwd=state_dir.parent,
    )
    assert result.returncode == 2
    assert "git project" in result.stderr
