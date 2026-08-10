"""File-aware score-provenance validation stays identical in audit and stats."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from skills.mission.tests.conftest import canonical_review, write_canonical_review_aggregate


LIB_ROOT = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))


def _write_valid_score(root: Path) -> tuple[dict[str, object], dict[str, object], Path]:
    """Build one current score whose aggregate and artifact bind the same claim."""
    _path, review_ref, claim = write_canonical_review_aggregate(
        root,
        [canonical_review({"mission_achievement": 4.5, "accuracy": 4.0, "completeness": 4.0, "usability": 4.5})],
        iteration=1,
    )
    entry: dict[str, object] = {
        **claim,
        "review_evidence_ref": review_ref,
        "revision_scope": review_ref["revision_scope"],
        "score_provenance": {
            "score_source": "scoring-json",
            "review_evidence_ref": review_ref,
            "revision_scope": review_ref["revision_scope"],
        },
    }
    binding = {
        "session_id": "session", "mission_id": "mission", "iteration": entry["iteration"],
        "items": entry["items"], "composite": entry["composite"], "min_item": entry["min_item"],
        "revision_scope": review_ref["revision_scope"], "review_generation": review_ref["generation"],
        "review_evidence_ref": review_ref,
    }
    artifact = {"schema": "mission-scoring-artifact/1", "binding": binding}
    payload = json.dumps(artifact, sort_keys=True).encode("utf-8")
    artifact_path = root / ".mission-state" / "archive" / "scoring.json"
    artifact_path.write_bytes(payload)
    entry["score_provenance"]["scoring_evidence_ref"] = {
        "kind": "scoring-artifact", "path": str(artifact_path.relative_to(root)),
        "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
    }
    state = {
        "mission": "fixture", "mission_id": "mission", "session_id": "session",
        "loop_active": False, "score_history": [entry],
    }
    return state, entry, _path


def test_file_aware_classifier_rejects_escaped_linked_changed_and_non_utf8_evidence(tmp_path: Path):
    from scoring_provenance import classify_score_provenance

    state, entry, evidence = _write_valid_score(tmp_path)
    assert classify_score_provenance(entry, terminal=False, project_root=tmp_path, state=state) == "verified"

    outside = tmp_path.parent / "outside-review.json"
    outside.write_bytes(evidence.read_bytes())
    escaped = copy.deepcopy(entry)
    escaped["score_provenance"]["review_evidence_ref"]["path"] = str(outside)
    assert classify_score_provenance(escaped, terminal=False, project_root=tmp_path, state=state) == "invalid"

    linked = evidence.with_name("linked.json")
    linked.symlink_to(evidence)
    symlinked = copy.deepcopy(entry)
    symlinked["score_provenance"]["review_evidence_ref"]["path"] = str(linked.relative_to(tmp_path))
    assert classify_score_provenance(symlinked, terminal=False, project_root=tmp_path, state=state) == "invalid"

    os.link(evidence, tmp_path / "hardlinked-review.json")
    assert classify_score_provenance(entry, terminal=False, project_root=tmp_path, state=state) == "invalid"
    (tmp_path / "hardlinked-review.json").unlink()

    evidence.write_bytes(b"not utf8: \xff")
    changed = copy.deepcopy(entry)
    changed["score_provenance"]["review_evidence_ref"]["digest"] = "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()
    changed["score_provenance"]["review_evidence_ref"]["generation"] = changed["score_provenance"]["review_evidence_ref"]["digest"][7:23]
    assert classify_score_provenance(changed, terminal=False, project_root=tmp_path, state=state) == "invalid"


def test_file_reader_rejects_oversize_and_same_size_path_swap(tmp_path: Path, monkeypatch):
    import scoring_provenance

    root = tmp_path / "project"
    evidence = root / ".mission-state" / "archive" / "evidence.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(b"x" * (scoring_provenance.MAX_SCORING_EVIDENCE_BYTES + 1))
    with pytest.raises(ValueError, match="bounded"):
        scoring_provenance.read_score_provenance_evidence(root, ".mission-state/archive/evidence.json")

    evidence.write_bytes(b'{"same":"before"}')
    replacement = evidence.with_name("replacement.json")
    replacement.write_bytes(b'{"same":"after!"}')
    assert evidence.stat().st_size == replacement.stat().st_size
    original_read = scoring_provenance.os.read
    swapped = False

    def read_then_swap(fd: int, size: int) -> bytes:
        nonlocal swapped
        if not swapped:
            swapped = True
            replacement.replace(evidence)
        return original_read(fd, size)

    monkeypatch.setattr(scoring_provenance.os, "read", read_then_swap)
    with pytest.raises(ValueError, match="changed"):
        scoring_provenance.read_score_provenance_evidence(root, ".mission-state/archive/evidence.json")


def _load_state_module():
    script = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("state_stats_file_awareness", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_audit_module():
    script = Path(__file__).resolve().parents[3] / "scripts" / "mission-audit.py"
    spec = importlib.util.spec_from_file_location("audit_stats_file_awareness", script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_audit_and_stats_share_semantic_file_validation(tmp_path: Path):
    state, entry, evidence = _write_valid_score(tmp_path)
    state_path = tmp_path / ".mission-state" / "sessions" / "session.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state["_mission_source_path"] = str(state_path)

    state_module = _load_state_module()
    audit = _load_audit_module()
    record = audit.StateRecord(state_path, {key: value for key, value in state.items() if key != "_mission_source_path"})

    assert state_module._score_provenance_counts([state]) == {
        "verified": 1, "legacy-unverifiable": 0, "invalid": 0,
    }
    assert audit.score_provenance_item(record)["classifications"] == [
        {"index": 1, "iteration": 1, "classification": "verified"}
    ]

    evidence.write_bytes(b"tampered")
    assert state_module._score_provenance_counts([state]) == {
        "verified": 0, "legacy-unverifiable": 0, "invalid": 1,
    }
    assert audit.score_provenance_item(record)["classifications"] == [
        {"index": 1, "iteration": 1, "classification": "invalid"}
    ]
