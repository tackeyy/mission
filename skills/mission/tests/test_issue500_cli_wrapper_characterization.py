"""Characterize compatibility wrappers changed by Issue #500 K1."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"


@pytest.fixture
def run_cli(legacy_run_cli):
    """K1 wrapper characterization remains explicitly retained-v4."""
    return legacy_run_cli


def _load_state_module(name: str):
    spec = importlib.util.spec_from_file_location(name, MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", [None, 1, 2, 3, 4])
def test_schema_version_wrapper_preserves_return_values(version):
    module = _load_state_module(f"issue500_version_{version}")
    document = {} if version is None else {"schema_version": version}
    assert module._validate_schema_version(document) == version


@pytest.mark.parametrize(
    ("version", "message"),
    [
        (True, "unsupported schema_version True; expected a missing field or an integer in 1..4"),
        ("4", "unsupported schema_version '4'; expected a missing field or an integer in 1..4"),
        (4.0, "unsupported schema_version 4.0; expected a missing field or an integer in 1..4"),
        (None, "unsupported schema_version None; expected a missing field or an integer in 1..4"),
        (0, "unsupported schema_version 0; expected a missing field or an integer in 1..4"),
        (5, "unsupported schema_version 5; expected a missing field or an integer in 1..4"),
    ],
    ids=["bool", "string", "float", "null", "zero", "future"],
)
def test_schema_version_wrapper_preserves_exception_type_and_message(version, message):
    module = _load_state_module(f"issue500_version_error_{type(version).__name__}_{version}")
    with pytest.raises(module.UnsupportedSchemaVersionError) as rejected:
        module._validate_schema_version({"schema_version": version})
    assert str(rejected.value) == message


def test_review_json_pair_hook_preserves_duplicate_exception_and_unique_result():
    module = _load_state_module("issue500_pair_hook")
    assert module._reject_duplicate_review_keys([("a", 1), ("b", 2)]) == {"a": 1, "b": 2}
    with pytest.raises(module._DuplicateReviewJsonKey) as rejected:
        module._reject_duplicate_review_keys([("a", 1), ("a", 2)])
    assert str(rejected.value) == "duplicate JSON key: a"


def test_strict_review_file_wrapper_preserves_bytes_and_error_messages(tmp_path):
    module = _load_state_module("issue500_strict_file_wrapper")
    regular = tmp_path / "review.json"
    regular.write_bytes(b'{"schema":"mission-review/1"}')
    assert module._read_strict_review_file(regular) == regular.read_bytes()

    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError) as rejected:
        module._read_strict_review_file(missing)
    assert str(rejected.value) == "review input is unavailable"

    hardlink = tmp_path / "hardlink.json"
    os.link(regular, hardlink)
    with pytest.raises(ValueError) as rejected:
        module._read_strict_review_file(hardlink)
    assert str(rejected.value) == "review input must be a bounded regular non-linked file"


def test_review_import_preserves_exit_output_and_publish_order(
    monkeypatch, capsys, tmp_path, run_cli
):
    initialized = run_cli(
        "init",
        "wrapper characterization",
        "--complexity",
        "Standard",
        "--artifact-applicability",
        "not-applicable",
        cwd=tmp_path,
    )
    assert initialized.returncode == 0, initialized.stderr
    source = tmp_path / "review.json"
    source.write_text(
        json.dumps(
            {
                "schema": "mission-review/1",
                "iteration": 1,
                "perspective": "correctness",
                "scores": {
                    "mission_achievement": 4.0,
                    "accuracy": 4.0,
                    "completeness": 4.0,
                    "usability": 4.0,
                },
                "findings": [],
                "same_score_note": "characterization fixture",
            }
        ),
        encoding="utf-8",
    )
    subprocess_result = run_cli(
        "review-import",
        "--iteration",
        "1",
        "--input",
        str(source),
        cwd=tmp_path,
    )
    assert subprocess_result.returncode == 0
    assert subprocess_result.stderr == ""
    subprocess_output = json.loads(subprocess_result.stdout)
    assert set(subprocess_output) == {
        "ok",
        "outcome_kind",
        "outcome",
        "review_evidence_ref",
    }
    assert subprocess_output["ok"] is True
    assert subprocess_output["outcome_kind"] == "ok"
    reference = subprocess_output["review_evidence_ref"]
    content = source.read_bytes()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    mission_id = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text(encoding="utf-8")
    )["mission_id"]
    assert reference == {
        "kind": "review-input",
        "path": (
            f".mission-state/archive/iter-1-{mission_id[:8]}-"
            f"review-input-{digest[7:23]}.json"
        ),
        "digest": digest,
        "size": len(content),
        "iteration": 1,
        "perspective": "correctness",
    }
    module = _load_state_module("issue500_publication_order")
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    events = []
    original_publish = module._publish_review_archive_transaction
    original_verify = module._verify_published_file
    original_write = module.atomic_write_json

    def recording_publish(*arguments, **keywords):
        events.append("publish-evidence")
        return original_publish(*arguments, **keywords)

    def recording_verify(*arguments, **keywords):
        events.append("verify-evidence")
        return original_verify(*arguments, **keywords)

    def recording_write(path, data, **keywords):
        if path == state_path and data.get("review_evidence_refs"):
            events.append("publish-state")
        return original_write(path, data, **keywords)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    monkeypatch.setattr(module, "_publish_review_archive_transaction", recording_publish)
    monkeypatch.setattr(module, "_verify_published_file", recording_verify)
    monkeypatch.setattr(module, "atomic_write_json", recording_write)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(MISSION_STATE_PY),
            "review-import",
            "--iteration",
            "1",
            "--input",
            str(source),
        ],
    )

    module.main()

    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["review_evidence_ref"]["kind"] == "review-input"
    assert events == [
        "publish-evidence",
        "verify-evidence",
        "verify-evidence",
        "publish-state",
    ]
    assert json.loads(state_path.read_text(encoding="utf-8"))["review_evidence_refs"]
