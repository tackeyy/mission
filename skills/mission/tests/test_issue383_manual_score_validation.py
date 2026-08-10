"""#383: manual score values stay typed at capture, write, and pass boundaries."""

import hashlib
import json
from pathlib import Path

import pytest

from scoring_provenance import digest


AXES = ("mission_achievement", "accuracy", "completeness", "usability")
VALID_ITEMS = {axis: 4.5 for axis in AXES}
INVALID_SCORES = (
    pytest.param(True, id="bool"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param("4.5", id="string"),
    pytest.param(-0.01, id="below-range"),
    pytest.param(5.01, id="above-range"),
)
INVALID_OPEN_HIGH = (
    pytest.param(True, id="bool"),
    pytest.param(0.5, id="float"),
    pytest.param("0", id="string"),
    pytest.param(-1, id="negative"),
)


def _manual_payload(**replacements):
    """Build one canonical typed manual score, then deliberately corrupt one field."""
    payload = {
        "schema": "mission-manual-score/1",
        "session_id": "test",
        "mission_id": "abc12345",
        "iteration": 1,
        "items": dict(VALID_ITEMS),
        "composite": 4.5,
        "min_item": 4.5,
        "review_agreement": 4.5,
        "open_high": 0,
        "revision_scope": {"kind": "not-applicable", "reason_code": "non-git"},
        "source_evidence_ref": {
            "kind": "manual-source-evidence",
            "ref": "sha256:" + "1" * 64,
            "digest": "sha256:" + "1" * 64,
        },
        "imported_at": "2026-08-10T00:00:00Z",
    }
    payload.update(replacements)
    unsigned = dict(payload)
    payload["input_digest"] = digest(unsigned)
    return payload


def _with_invalid_score(field, value):
    if field == "items":
        return _manual_payload(items={axis: value for axis in AXES})
    return _manual_payload(**{field: value})


def _tree_bytes(root: Path):
    if not root.exists():
        return {}
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_json(path: Path, payload):
    path.write_text(json.dumps(payload, allow_nan=True, sort_keys=True), encoding="utf-8")


def _capture(run_cli, state_dir, tmp_path, payload):
    source = tmp_path / "manual-source.json"
    output = tmp_path / "manual-scoring.json"
    _write_json(source, payload)
    result = run_cli(
        "manual-score-capture", "--input", str(source), "--out", str(output),
        cwd=state_dir.parent,
    )
    return result, output


def _capture_valid(run_cli, state_dir, tmp_path):
    result, scoring = _capture(run_cli, state_dir, tmp_path, _manual_payload())
    assert result.returncode == 0, result.stderr
    return scoring


def _rewrite_manual_archive(root: Path, scoring_path: Path, payload):
    """Model a forged historical archive with its own matching content digest."""
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    reference = scoring["score_provenance"]["manual_evidence_ref"]
    archive = root / reference["path"]
    content = json.dumps(payload, allow_nan=True, sort_keys=True).encode("utf-8")
    archive.write_bytes(content)
    digest_hex = hashlib.sha256(content).hexdigest()
    reference["digest"] = "sha256:" + digest_hex
    reference["generation"] = digest_hex[:16]
    scoring["items"] = payload["items"]
    scoring["review_agreement"] = payload["review_agreement"]
    scoring["open_high"] = payload["open_high"]
    _write_json(scoring_path, scoring)
    return reference


@pytest.mark.parametrize("field", ("items", "composite", "min_item", "review_agreement"))
@pytest.mark.parametrize("value", INVALID_SCORES)
def test_manual_capture_rejects_noncanonical_score_values_without_writes(
        state_dir, run_cli, tmp_path, field, value):
    payload = _with_invalid_score(field, value)
    session = state_dir / "sessions" / "test.json"
    before_state = session.read_bytes()
    before_archive = _tree_bytes(state_dir / "archive")

    result, output = _capture(run_cli, state_dir, tmp_path, payload)

    assert result.returncode == 2
    assert "manual score" in result.stderr
    assert session.read_bytes() == before_state
    assert _tree_bytes(state_dir / "archive") == before_archive
    assert not output.exists()


@pytest.mark.parametrize("value", INVALID_OPEN_HIGH)
def test_manual_capture_rejects_noninteger_open_high_without_writes(state_dir, run_cli, tmp_path, value):
    session = state_dir / "sessions" / "test.json"
    before_state = session.read_bytes()
    before_archive = _tree_bytes(state_dir / "archive")

    result, output = _capture(run_cli, state_dir, tmp_path, _manual_payload(open_high=value))

    assert result.returncode == 2
    assert "open_high" in result.stderr
    assert session.read_bytes() == before_state
    assert _tree_bytes(state_dir / "archive") == before_archive
    assert not output.exists()


@pytest.mark.parametrize("field", ("items", "composite", "min_item", "review_agreement"))
@pytest.mark.parametrize("value", INVALID_SCORES)
def test_push_score_revalidates_manual_score_values_without_writes(
        state_dir, run_cli, tmp_path, field, value):
    scoring = _capture_valid(run_cli, state_dir, tmp_path)
    _rewrite_manual_archive(state_dir.parent, scoring, _with_invalid_score(field, value))
    session = state_dir / "sessions" / "test.json"
    before_state = session.read_bytes()
    before_archive = _tree_bytes(state_dir / "archive")
    before_scoring = scoring.read_bytes()

    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent)

    assert result.returncode == 2
    expected_boundary = "--scoring-json" if field in {"items", "review_agreement"} else "provenance"
    assert expected_boundary in result.stderr
    assert session.read_bytes() == before_state
    assert _tree_bytes(state_dir / "archive") == before_archive
    assert scoring.read_bytes() == before_scoring


@pytest.mark.parametrize("value", INVALID_OPEN_HIGH)
def test_push_score_revalidates_manual_open_high_without_writes(state_dir, run_cli, tmp_path, value):
    scoring = _capture_valid(run_cli, state_dir, tmp_path)
    _rewrite_manual_archive(state_dir.parent, scoring, _manual_payload(open_high=value))
    session = state_dir / "sessions" / "test.json"
    before_state = session.read_bytes()
    before_archive = _tree_bytes(state_dir / "archive")
    before_scoring = scoring.read_bytes()

    result = run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent)

    assert result.returncode == 2
    assert "--scoring-json" in result.stderr
    assert session.read_bytes() == before_state
    assert _tree_bytes(state_dir / "archive") == before_archive
    assert scoring.read_bytes() == before_scoring


def _rewrite_pushed_entry(state_dir: Path, reference: dict):
    session = state_dir / "sessions" / "test.json"
    state = json.loads(session.read_text(encoding="utf-8"))
    entry = state["score_history"][-1]
    # Keep the accepted score claim intact.  This isolates the later
    # mark-passes check to the forged archived evidence boundary.
    entry["manual_evidence_ref"] = reference
    entry["score_provenance"]["manual_evidence_ref"] = reference
    _write_json(session, state)


@pytest.mark.parametrize("field", ("items", "composite", "min_item", "review_agreement"))
@pytest.mark.parametrize("value", INVALID_SCORES)
def test_mark_passes_revalidates_manual_score_values_without_writes(
        state_dir, run_cli, tmp_path, field, value):
    scoring = _capture_valid(run_cli, state_dir, tmp_path)
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent).returncode == 0
    payload = _with_invalid_score(field, value)
    reference = _rewrite_manual_archive(state_dir.parent, scoring, payload)
    _rewrite_pushed_entry(state_dir, reference)
    session = state_dir / "sessions" / "test.json"
    before_state = session.read_bytes()
    before_archive = _tree_bytes(state_dir / "archive")
    before_scoring = scoring.read_bytes()

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "provenance" in result.stderr
    assert session.read_bytes() == before_state
    assert _tree_bytes(state_dir / "archive") == before_archive
    assert scoring.read_bytes() == before_scoring


@pytest.mark.parametrize("value", INVALID_OPEN_HIGH)
def test_mark_passes_revalidates_manual_open_high_without_writes(state_dir, run_cli, tmp_path, value):
    scoring = _capture_valid(run_cli, state_dir, tmp_path)
    assert run_cli("push-score", "--iteration", "1", "--scoring-json", str(scoring), cwd=state_dir.parent).returncode == 0
    payload = _manual_payload(open_high=value)
    reference = _rewrite_manual_archive(state_dir.parent, scoring, payload)
    _rewrite_pushed_entry(state_dir, reference)
    session = state_dir / "sessions" / "test.json"
    before_state = session.read_bytes()
    before_archive = _tree_bytes(state_dir / "archive")
    before_scoring = scoring.read_bytes()

    result = run_cli("mark-passes", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "provenance" in result.stderr
    assert session.read_bytes() == before_state
    assert _tree_bytes(state_dir / "archive") == before_archive
    assert scoring.read_bytes() == before_scoring
