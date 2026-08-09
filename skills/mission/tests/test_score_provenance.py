"""Issue #383: immutable, structured scoring provenance."""

import json
import importlib.util
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest


ITEMS = {
    "mission_achievement": 4.5,
    "accuracy": 4.5,
    "completeness": 4.0,
    "usability": 4.0,
}


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


@pytest.mark.parametrize("schema", [None, 3, "4", 4.0])
def test_push_score_requires_provenance_despite_schema_downgrade(state_dir, run_cli, schema):
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
    assert "provenance" in result.stderr
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
