"""Review evidence is imported through one strict, durable boundary."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _stdin_import(payload, cwd, *extra):
    env = {key: value for key, value in os.environ.items() if not key.startswith("MISSION_")}
    env.update({"MISSION_SESSION_ID": "test", "MISSION_LEASE_ID": "test-lease"})
    return subprocess.run(
        [
            sys.executable, str(MISSION_STATE_PY), "review-import", "--iteration", "1",
            "--stdin", *extra,
        ],
        cwd=cwd, input=payload, capture_output=True, env=env,
    )


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


INVALID_REVIEW_ITERATIONS = [True, 1.0, False, 0, -1, "1", None]
WHITESPACE_PERSPECTIVES = [" quality ", "\tquality", "quality\n", "\u2003quality"]


def _archive_bytes(state_dir):
    archive = state_dir / "archive"
    return {
        path.relative_to(archive): path.read_bytes()
        for path in archive.rglob("*")
        if path.is_file()
    } if archive.exists() else {}


def _assert_invalid_once_and_atomic(result, state_dir, state_before, archive_before, event_id):
    stdout = result.stdout.decode("utf-8") if isinstance(result.stdout, bytes) else result.stdout
    assert result.returncode == 2
    payload = json.loads(stdout)
    assert payload["outcome_kind"] == "invalid-input"
    assert payload["outcome"]["event_id"] == event_id
    assert (state_dir / "sessions" / "test.json").read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    sidecar = next((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    assert json.loads(sidecar.read_text(encoding="utf-8"))["records"] == [payload["outcome"]]
    assert not list(state_dir.rglob(".*.tmp"))


@pytest.mark.parametrize("perspective", WHITESPACE_PERSPECTIVES, ids=repr)
@pytest.mark.parametrize("source_mode", ["file", "stdin"])
def test_review_import_rejects_whitespace_padded_perspective_atomically(
    state_dir, run_cli, tmp_path, perspective, source_mode,
):
    content = _review_bytes(perspective=perspective)
    state_file = state_dir / "sessions" / "test.json"
    state_before = state_file.read_bytes()
    archive_before = _archive_bytes(state_dir)
    event_id = f"import-whitespace-{source_mode}"
    if source_mode == "stdin":
        result = _stdin_import(
            content, state_dir.parent, "--event-id", event_id,
        )
    else:
        source = tmp_path / "review.json"
        source.write_bytes(content)
        result = run_cli(
            "review-import", "--iteration", "1", "--input", str(source),
            "--event-id", event_id, cwd=state_dir.parent,
        )

    _assert_invalid_once_and_atomic(
        result, state_dir, state_before, archive_before, event_id,
    )


@pytest.mark.parametrize("perspective", WHITESPACE_PERSPECTIVES, ids=repr)
@pytest.mark.parametrize("command", ["aggregate-reviews", "review-finalize"])
def test_legacy_review_consumer_rejects_whitespace_padded_perspective_atomically(
    state_dir, run_cli, tmp_path, perspective, command,
):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes(perspective=perspective))
    state_file = state_dir / "sessions" / "test.json"
    state_before = state_file.read_bytes()
    archive_before = _archive_bytes(state_dir)
    event_id = f"legacy-whitespace-{command}"
    args = [
        command, "--iteration", "1", "--input", str(source),
        "--event-id", event_id,
    ]
    if command == "aggregate-reviews":
        args.append("--json")

    result = run_cli(*args, cwd=state_dir.parent)

    _assert_invalid_once_and_atomic(
        result, state_dir, state_before, archive_before, event_id,
    )


@pytest.mark.parametrize("perspective", WHITESPACE_PERSPECTIVES, ids=repr)
@pytest.mark.parametrize("command", ["aggregate-reviews", "review-finalize"])
def test_import_ref_consumer_rejects_whitespace_padded_perspective_atomically(
    state_dir, run_cli, tmp_path, perspective, command,
):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    imported = run_cli(
        "review-import", "--iteration", "1", "--input", str(source),
        cwd=state_dir.parent,
    )
    assert imported.returncode == 0, imported.stderr
    reference = json.loads(imported.stdout)["review_evidence_ref"]
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    evidence = state_dir.parent / reference["path"]
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["perspective"] = perspective
    changed = (json.dumps(document) + "\n").encode("utf-8")
    evidence.write_bytes(changed)
    state["review_evidence_refs"][0].update({
        "perspective": perspective,
        "digest": "sha256:" + hashlib.sha256(changed).hexdigest(),
        "size": len(changed),
    })
    state_file.write_text(json.dumps(state) + "\n", encoding="utf-8")
    state_before = state_file.read_bytes()
    archive_before = _archive_bytes(state_dir)
    event_id = f"ref-whitespace-{command}"
    args = [
        command, "--iteration", "1", "--input-ref", reference["path"],
        "--event-id", event_id,
    ]
    if command == "aggregate-reviews":
        args.append("--json")

    result = run_cli(*args, cwd=state_dir.parent)

    _assert_invalid_once_and_atomic(
        result, state_dir, state_before, archive_before, event_id,
    )


@pytest.mark.parametrize("iteration", INVALID_REVIEW_ITERATIONS, ids=repr)
@pytest.mark.parametrize("source_mode", ["file", "stdin"])
def test_review_import_rejects_non_integer_document_iteration_once_and_atomically(
    state_dir, run_cli, tmp_path, iteration, source_mode,
):
    content = _review_bytes(iteration=iteration)
    state_file = state_dir / "sessions" / "test.json"
    state_before = state_file.read_bytes()
    archive_before = _archive_bytes(state_dir)
    if source_mode == "stdin":
        result = _stdin_import(content, state_dir.parent)
        stdout = result.stdout.decode("utf-8")
    else:
        source = tmp_path / "review.json"
        source.write_bytes(content)
        result = run_cli(
            "review-import", "--iteration", "1", "--input", str(source),
            cwd=state_dir.parent,
        )
        stdout = result.stdout

    assert result.returncode == 2
    payload = json.loads(stdout)
    assert payload["outcome_kind"] == "invalid-input"
    assert state_file.read_bytes() == state_before
    assert _archive_bytes(state_dir) == archive_before
    sidecars = list((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    assert len(sidecars) == 1
    assert json.loads(sidecars[0].read_text(encoding="utf-8"))["records"] == [payload["outcome"]]
    assert not list(state_dir.rglob(".*.tmp"))


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


@pytest.mark.parametrize("command", ["aggregate-reviews", "review-finalize"])
@pytest.mark.parametrize("target", ["document", "reference"])
@pytest.mark.parametrize("invalid_iteration", [True, 1.0], ids=repr)
def test_review_consumers_reject_resigned_non_integer_import_iteration(
    state_dir, run_cli, tmp_path, command, target, invalid_iteration,
):
    source = tmp_path / "source-review.json"
    source.write_bytes(_review_bytes())
    imported = run_cli(
        "review-import", "--iteration", "1", "--input", str(source),
        cwd=state_dir.parent,
    )
    assert imported.returncode == 0, imported.stderr
    reference = json.loads(imported.stdout)["review_evidence_ref"]
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    stored = state["review_evidence_refs"][0]
    if target == "reference":
        stored["iteration"] = invalid_iteration
    else:
        evidence = state_dir.parent / reference["path"]
        document = json.loads(evidence.read_text(encoding="utf-8"))
        document["iteration"] = invalid_iteration
        changed = (json.dumps(document) + "\n").encode("utf-8")
        evidence.write_bytes(changed)
        stored.update(
            {
                "digest": "sha256:" + hashlib.sha256(changed).hexdigest(),
                "size": len(changed),
            }
        )
    state_file.write_text(json.dumps(state) + "\n", encoding="utf-8")
    before = state_file.read_bytes()
    output = tmp_path / f"{command}.json"
    args = [
        command, "--iteration", "1", "--input-ref", reference["path"],
        "--out", str(output), "--event-id", f"{command}-{target}",
    ]
    if command == "aggregate-reviews":
        args.append("--json")

    result = run_cli(*args, cwd=state_dir.parent)

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "invalid-input"
    assert payload["outcome"]["command"] == command
    assert state_file.read_bytes() == before
    assert not output.exists()
    sidecar = next((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    assert json.loads(sidecar.read_text(encoding="utf-8"))["records"] == [payload["outcome"]]


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


@pytest.mark.parametrize("failure", ["min-reviewers", "invalid-review"])
def test_aggregate_failure_emits_one_typed_outcome_without_state_mutation(
    state_dir, run_cli, tmp_path, failure,
):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes() if failure == "min-reviewers" else b'{"schema":"wrong"}')
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()
    args = [
        "aggregate-reviews", "--iteration", "1", "--input", str(source),
        "--json", "--event-id", f"aggregate-{failure}",
    ]
    if failure == "min-reviewers":
        args.extend(("--min-reviewers", "2"))

    result = run_cli(*args, cwd=state_dir.parent)

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    expected = "expected-gate" if failure == "min-reviewers" else "invalid-input"
    assert payload["outcome_kind"] == expected
    assert payload["outcome"]["command"] == "aggregate-reviews"
    assert state_file.read_bytes() == before
    sidecar = next((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    assert json.loads(sidecar.read_text(encoding="utf-8"))["records"] == [payload["outcome"]]


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


@pytest.mark.parametrize("mode", ["symlink", "hardlink"])
def test_review_import_rejects_linked_source_without_state_or_archive_mutation(state_dir, run_cli, tmp_path, mode):
    source = tmp_path / "review.json"
    target = tmp_path / "target.json"
    target.write_bytes(_review_bytes())
    if mode == "symlink":
        source.symlink_to(target)
    else:
        source.hardlink_to(target)
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli("review-import", "--iteration", "1", "--input", str(source), cwd=state_dir.parent)

    assert result.returncode == 2
    assert state_file.read_bytes() == before
    archive = state_dir / "archive"
    assert not archive.exists() or not list(archive.iterdir())


def test_review_import_rejects_symlinked_archive_parent_without_external_or_state_write(
    state_dir, run_cli, tmp_path,
):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    external = tmp_path / "external-archive"
    external.mkdir()
    archive = state_dir / "archive"
    archive.symlink_to(external, target_is_directory=True)
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli(
        "review-import", "--iteration", "1", "--input", str(source),
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert state_file.read_bytes() == before
    assert not list(external.iterdir())


def test_review_import_archive_write_is_atomic_and_leaves_no_temp_evidence(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())

    result = run_cli("review-import", "--iteration", "1", "--input", str(source), cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    archive = state_dir / "archive"
    assert not list(archive.glob(".*.tmp"))


def test_review_import_accepts_valid_stdin_and_rejects_truncated_stdin_atomically(state_dir):
    valid = _stdin_import(_review_bytes(), state_dir.parent)
    assert valid.returncode == 0, valid.stderr.decode()
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()
    truncated = _stdin_import(b'{"schema":"mission-review/1"', state_dir.parent)
    assert truncated.returncode == 2
    assert json.loads(truncated.stdout)["outcome_kind"] == "invalid-input"
    assert state_file.read_bytes() == before


def test_review_import_rejects_oversize_stdin_before_state_or_archive_write(state_dir):
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()
    result = _stdin_import(b"{" + b" " * (4 * 1024 * 1024), state_dir.parent)
    assert result.returncode == 2
    assert json.loads(result.stdout)["outcome_kind"] == "invalid-input"
    assert state_file.read_bytes() == before


def test_review_import_input_and_stdin_are_mutually_exclusive_before_any_write(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()
    result = run_cli("review-import", "--iteration", "1", "--input", str(source), "--stdin", cwd=state_dir.parent)
    assert result.returncode == 2
    assert state_file.read_bytes() == before


def test_review_import_fifo_is_nonblocking_rejection_without_state_or_archive_mutation(state_dir, run_cli, tmp_path):
    source = tmp_path / "review.fifo"
    os.mkfifo(source)
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()
    result = run_cli("review-import", "--iteration", "1", "--input", str(source), cwd=state_dir.parent)
    assert result.returncode == 2
    assert state_file.read_bytes() == before


def test_review_input_single_descriptor_rejects_same_size_path_swap_identity(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("mission_state_review_swap", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    original = module.os.lstat

    def swapped(path):
        metadata = original(path)
        values = list(metadata)
        values[1] += 1  # same byte length, different final pathname identity
        return os.stat_result(values)

    monkeypatch.setattr(module.os, "lstat", swapped)
    with pytest.raises(ValueError, match="changed while being read"):
        module._read_strict_review_file(source)


def test_review_archive_parent_replacement_before_publish_is_fail_closed(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("mission_state_review_archive_swap", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    cwd = tmp_path / "project"
    archive = cwd / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    detached = cwd / ".mission-state" / "detached-archive"
    original_verify = module._verify_review_archive_directory
    calls = 0

    def swap_after_open(directory_fd, named_parent):
        nonlocal calls
        calls += 1
        if calls == 2:
            archive.rename(detached)
            archive.mkdir()
            (archive / "sentinel").write_bytes(b"replacement")
        return original_verify(directory_fd, named_parent)

    monkeypatch.setattr(module, "_verify_review_archive_directory", swap_after_open)
    with pytest.raises(ValueError, match="directory changed"):
        module._publish_review_import_evidence(cwd, "review.json", _review_bytes())

    assert (archive / "sentinel").read_bytes() == b"replacement"
    assert not (archive / "review.json").exists()
    assert not (detached / "review.json").exists()


@pytest.mark.parametrize(
    ("owner", "lease_id"),
    [("foreign-owner", "foreign-lease"), ("test", "wrong-owner-token")],
    ids=["foreign-owner", "same-owner-wrong-token"],
)
def test_review_import_rejects_lease_before_archive_publish_exactly_once(
    state_dir, run_cli, tmp_path, owner, lease_id,
):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    archive = state_dir / "archive"
    archive.mkdir()
    (archive / "preexisting.bin").write_bytes(b"archive-before")
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state.update({
        "owner_session_id": owner,
        "lease_id": lease_id,
        "fencing_epoch": 3,
        "lease_expires_at": "2099-01-01T00:00:00Z",
    })
    state_file.write_text(json.dumps(state) + "\n", encoding="utf-8")
    state_before = state_file.read_bytes()
    score_before = json.dumps(state["score_history"], sort_keys=True)
    archive_before = _archive_bytes(state_dir)

    result = run_cli(
        "review-import", "--iteration", "1", "--input", str(source),
        "--event-id", f"lease-{owner}", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome_kind"] == "expected-gate"
    assert payload["outcome"]["command"] == "review-import"
    assert payload["outcome"]["event_id"] == f"lease-{owner}"
    assert result.stdout.count('"outcome_kind"') == 2  # envelope + one record
    assert "MISSION_LEASE_CARRIER" not in result.stderr
    assert state_file.read_bytes() == state_before
    assert json.dumps(json.loads(state_file.read_text())["score_history"], sort_keys=True) == score_before
    assert _archive_bytes(state_dir) == archive_before
    sidecar = next((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    assert json.loads(sidecar.read_text(encoding="utf-8"))["records"] == [payload["outcome"]]
    assert not list(state_dir.rglob(".*.tmp"))


def test_existing_review_evidence_parent_swap_is_rejected_before_fast_path_return(
    monkeypatch, tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        "mission_state_existing_review_archive_swap", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    cwd = tmp_path / "project"
    archive = cwd / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    content = _review_bytes()
    (archive / "review.json").write_bytes(content)
    detached = cwd / ".mission-state" / "detached-archive"
    original_verify = module._verify_review_archive_directory
    calls = 0

    def swap_existing_parent(directory_fd, named_parent):
        nonlocal calls
        calls += 1
        if calls == 2:
            archive.rename(detached)
            archive.mkdir()
            (archive / "review.json").write_bytes(content)
            (archive / "sentinel").write_bytes(b"replacement-parent")
        return original_verify(directory_fd, named_parent)

    monkeypatch.setattr(
        module, "_verify_review_archive_directory", swap_existing_parent,
    )
    with pytest.raises(ValueError, match="directory changed"):
        module._publish_review_import_evidence(cwd, "review.json", content)

    assert (archive / "review.json").read_bytes() == content
    assert (archive / "sentinel").read_bytes() == b"replacement-parent"
    assert (detached / "review.json").read_bytes() == content
    assert not list(cwd.rglob(".*.tmp"))


@pytest.mark.parametrize("mode", ["symlink", "hardlink", "fifo"])
def test_review_archive_publisher_rejects_unsafe_existing_entry(
    mode, tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        f"mission_state_review_archive_{mode}", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    cwd = tmp_path / "project"
    archive = cwd / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    entry = archive / "review.json"
    external = tmp_path / "external.json"
    external.write_bytes(b"external-object")
    if mode == "symlink":
        entry.symlink_to(external)
    elif mode == "hardlink":
        entry.hardlink_to(external)
    else:
        os.mkfifo(entry)

    with pytest.raises(ValueError, match="unsafe"):
        module._publish_review_import_evidence(cwd, entry.name, _review_bytes())

    assert external.read_bytes() == b"external-object"
    if mode == "fifo":
        assert stat.S_ISFIFO(entry.lstat().st_mode)
    else:
        assert entry.exists()


@pytest.mark.parametrize("same_content", [True, False], ids=["same-content", "collision"])
def test_review_archive_concurrent_publish_never_overwrites_other_writer(
    same_content, monkeypatch, tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        f"mission_state_review_archive_concurrent_{same_content}", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    cwd = tmp_path / "project"
    archive = cwd / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    content = _review_bytes()
    competitor = content if same_content else b"competitor-object"
    original_link = module.os.link

    def concurrent_publish(src, dst, **kwargs):
        (archive / dst).write_bytes(competitor)
        raise FileExistsError("simulated concurrent publish")

    monkeypatch.setattr(module.os, "link", concurrent_publish)
    if same_content:
        published = module._publish_review_archive_transaction(cwd, "review.json", content)
        assert published.created is False
        module._rollback_published_file(published)
    else:
        with pytest.raises(ValueError, match="collision"):
            module._publish_review_archive_transaction(cwd, "review.json", content)
    monkeypatch.setattr(module.os, "link", original_link)

    assert (archive / "review.json").read_bytes() == competitor
    assert not list(archive.glob(".*.tmp"))


def test_review_archive_post_publish_fsync_failure_self_rolls_back(
    monkeypatch, tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        "mission_state_review_archive_fsync_failure", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    cwd = tmp_path / "project"
    archive = cwd / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    original_fsync = module.os.fsync
    rejected = False

    def fail_directory_fsync(fd):
        nonlocal rejected
        if stat.S_ISDIR(module.os.fstat(fd).st_mode) and not rejected:
            rejected = True
            raise OSError("simulated post-publish directory fsync failure")
        return original_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_directory_fsync)

    with pytest.raises(ValueError, match="publish failed"):
        module._publish_review_archive_transaction(cwd, "review.json", _review_bytes())

    assert rejected is True
    assert not (archive / "review.json").exists()
    assert not list(archive.glob(".*.tmp"))
    assert not list(archive.glob(".*.rollback"))


@pytest.mark.parametrize("fault", ["verify", "return-boundary"])
@pytest.mark.parametrize("preexisting", [False, True], ids=["new", "existing"])
def test_review_archive_finish_fault_rolls_back_only_new_object(
    fault, preexisting, monkeypatch, tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        f"mission_state_review_archive_finish_{fault}_{preexisting}", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    cwd = tmp_path / "project"
    archive = cwd / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    content = _review_bytes()
    if preexisting:
        module._publish_review_import_evidence(cwd, "review.json", content)
    archive_before = _archive_bytes(archive.parent)
    rejected = False
    if fault == "verify":
        original = module._verify_published_file

        def fail_once(published):
            nonlocal rejected
            if not rejected:
                rejected = True
                raise OSError("simulated finish verification failure")
            return original(published)

        monkeypatch.setattr(module, "_verify_published_file", fail_once)
    else:
        original = module._finish_published_file

        def fail_once(published):
            nonlocal rejected
            result = original(published)
            if not rejected:
                rejected = True
                raise OSError("simulated return-boundary failure")
            return result

        monkeypatch.setattr(module, "_finish_published_file", fail_once)

    with pytest.raises(ValueError, match="publish failed"):
        module._publish_review_archive_transaction(cwd, "review.json", content)

    assert rejected is True
    assert _archive_bytes(archive.parent) == archive_before
    assert not list(archive.glob(".*.tmp"))
    assert not list(archive.glob(".*.rollback"))


def test_review_import_success_reuses_one_lease_decision_and_emits_one_carrier(
    monkeypatch, capsys, state_dir, tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        "mission_state_review_import_single_lease", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    original_enforce = module._enforce_session_lease_for_write
    lease_checks = 0

    def counted_enforce(path, data):
        nonlocal lease_checks
        lease_checks += 1
        return original_enforce(path, data)

    monkeypatch.setattr(module, "_enforce_session_lease_for_write", counted_enforce)
    module.cmd_review_import(SimpleNamespace(
        iteration=1, input=str(source), stdin=False, event_id="success-once",
        root_event_id=None, attempt=1, retry_of=None, json=True,
    ))

    captured = capsys.readouterr()
    assert lease_checks == 1
    assert captured.err.count(module.LEASE_CARRIER_PREFIX) == 1
    payload = json.loads(captured.out)
    assert payload["outcome_kind"] == "ok"
    state = json.loads((state_dir / "sessions" / "test.json").read_text())
    assert state["owner_session_id"] == "test"
    assert state["lease_id"] == "test-lease"
    assert state["fencing_epoch"] == 1


@pytest.mark.parametrize("preexisting", [False, True], ids=["new", "existing"])
def test_review_import_state_publish_failure_rolls_back_new_evidence_and_emits_internal_outcome(
    preexisting, monkeypatch, capsys, state_dir, tmp_path,
):
    spec = importlib.util.spec_from_file_location(
        "mission_state_review_import_state_failure", MISSION_STATE_PY,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    state_path = state_dir / "sessions" / "test.json"
    if preexisting:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        module._publish_review_import_evidence(
            state_dir.parent,
            f"iter-1-abc12345-review-input-{digest[:16]}.json",
            source.read_bytes(),
        )
    state_before = state_path.read_bytes()
    archive_before = _archive_bytes(state_dir)
    score_before = json.loads(state_before)["score_history"]
    original_write = module.atomic_write_json

    def fail_import_state_publish(path, data, **kwargs):
        if path == state_path and data.get("review_evidence_refs"):
            raise OSError("simulated review import state publish failure")
        return original_write(path, data, **kwargs)

    monkeypatch.setattr(module, "atomic_write_json", fail_import_state_publish)
    monkeypatch.setattr(sys, "argv", [
        str(MISSION_STATE_PY), "review-import", "--iteration", "1",
        "--input", str(source), "--event-id", "review-import-state-fault",
    ])

    with pytest.raises(SystemExit) as stopped:
        module.main()

    assert stopped.value.code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome_kind"] == "internal-error"
    assert payload["outcome"]["command"] == "review-import"
    assert state_path.read_bytes() == state_before
    assert json.loads(state_path.read_text())["score_history"] == score_before
    assert _archive_bytes(state_dir) == archive_before
    assert not list(state_dir.rglob(".*.tmp"))
    sidecar = next((state_dir / "telemetry" / "command-outcomes").glob("*.json"))
    assert json.loads(sidecar.read_text())["records"] == [payload["outcome"]]
