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


def test_git_revision_scope_requires_exact_pair(state_dir, run_cli, tmp_path):
    review = _review(tmp_path / "review.json")
    result = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review),
                     "--base-sha", "bad", cwd=state_dir.parent)
    assert result.returncode == 2
    assert "exact" in result.stderr


def test_resubmit_preserves_prior_content_addressed_archive(state_dir, run_cli, read_state, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"items": ITEMS}), encoding="utf-8")
    second.write_text(json.dumps({"items": dict(ITEMS, accuracy=4.4)}), encoding="utf-8")
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(first), cwd=state_dir.parent).returncode == 0
    old_path = read_state(state_dir)["score_history"][0]["scoring_evidence_path"]
    old_bytes = open(old_path, "rb").read()
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(second),
                   "--resubmit-reason", "neutral correction", cwd=state_dir.parent).returncode == 0
    assert open(old_path, "rb").read() == old_bytes


def test_manual_import_source_is_preserved_with_typed_evidence_ref(state_dir, run_cli, read_state, tmp_path):
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
    assert result.returncode == 0, result.stderr
    assert read_state(state_dir)["score_history"][0]["score_source"] == "manual-import"


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
    module.register_approval_verifier(
        "fixture-verifier",
        lambda request: module.ApprovalVerification(verified=True, verifier="fixture-verifier"),
    )
    result = module.verify_force_approval({
        "approval_evidence_ref": "sha256:" + "a" * 64,
        "approved_actor": "role:owner",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "reason_code": "user-override",
    }, "fixture-verifier")
    assert result.verified is True
    assert result.verifier == "fixture-verifier"
