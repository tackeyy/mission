"""Review evidence is imported through one strict, durable boundary."""

from __future__ import annotations

import hashlib
import json

import pytest


def _review_bytes(*, perspective="quality", iteration=1):
    return (json.dumps({
        "schema": "mission-review/1",
        "iteration": iteration,
        "perspective": perspective,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.2,
            "completeness": 4.1,
            "usability": 4.0,
        },
        "findings": [],
    }, ensure_ascii=False) + "\n").encode("utf-8")


def test_review_import_archives_a_strict_review_as_a_typed_reference(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.json"
    content = _review_bytes()
    source.write_bytes(content)

    result = run_cli(
        "review-import", "--iteration", "1", "--input", str(source),
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "ok"
    assert payload["outcome"]["event_id"] == payload["outcome"]["root_event_id"]
    ref = payload["review_evidence_ref"]
    assert ref == {
        "kind": "review-input",
        "path": ref["path"],
        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "iteration": 1,
        "perspective": "quality",
    }
    assert not ref["path"].startswith("/")
    assert (state_dir.parent / ref["path"]).read_bytes() == content
    state = json.loads((state_dir / "sessions" / "test.json").read_text(encoding="utf-8"))
    assert state["command_outcomes"][-1]["outcome_kind"] == "ok"


def test_aggregate_reviews_revalidates_an_import_after_its_source_is_removed(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    imported = run_cli(
        "review-import", "--iteration", "1", "--input", str(source),
        cwd=state_dir.parent,
    )
    assert imported.returncode == 0, imported.stderr
    reference = json.loads(imported.stdout)["review_evidence_ref"]
    source.unlink()
    output = tmp_path / "scoring.json"

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input-ref", reference["path"],
        "--out", str(output), "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome_kind"] == "ok"
    scoring = json.loads(output.read_text(encoding="utf-8"))
    assert scoring["items"]["mission_achievement"] == 4.5


def test_reviewer_window_rejection_is_an_expected_gate_without_state_mutation(state_dir, run_cli, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_bytes(_review_bytes(perspective="quality"))
    second.write_bytes(_review_bytes(perspective="safety"))
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "aggregate-reviews", "--iteration", "1", "--input", str(first), "--input", str(second),
        "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["outcome_kind"] == "expected-gate"
    assert state_file.read_bytes() == before


@pytest.mark.parametrize(("content", "case"), [
    (b'{"schema":"mission-review/1","schema":"mission-review/1"}', "duplicate-key"),
    (_review_bytes() + b"trailing prose\n", "trailing-prose"),
    (b'\xff\xfe', "invalid-utf8"),
    (b"{" + b" " * (4 * 1024 * 1024) + b"}", "oversize"),
], ids=["duplicate-key", "trailing-prose", "invalid-utf8", "oversize"])
def test_review_import_rejects_hostile_input_without_changing_state_or_archives(
    state_dir, run_cli, tmp_path, content, case,
):
    source = tmp_path / "hostile-review.json"
    source.write_bytes(content)
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "review-import", "--iteration", "1", "--input", str(source),
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["outcome_kind"] == "invalid-input"
    assert state_file.read_bytes() == before
    telemetry = next((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    assert json.loads(telemetry.read_text(encoding="utf-8"))["records"][-1]["outcome_kind"] == "invalid-input"
    archive = state_dir / "archive"
    assert not archive.exists() or not list(archive.iterdir())
