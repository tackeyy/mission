"""Issue #625: C1/A5 remaining commands stay outside session authority."""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile

import pytest


MISSION_ROOT = Path(__file__).resolve().parents[1]
MISSION_STATE_SOURCE = MISSION_ROOT / "bin" / "mission-state.py"

SCOPED_LEAF_HANDLERS = {
    "handoff await": "cmd_handoff_await",
    "handoff publish": "cmd_handoff_publish",
    "handoff verify": "cmd_handoff_verify",
    "parallel-closeout": "cmd_parallel_closeout",
    "parallel-status": "cmd_parallel_status",
    "pregate check": "cmd_pregate",
    "pregate digest": "cmd_pregate",
    "pregate record": "cmd_pregate",
    "queue enqueue": "cmd_queue",
    "queue mark": "cmd_queue",
    "queue next": "cmd_queue",
    "queue status": "cmd_queue",
    "queue verify": "cmd_queue",
    "stop-guard-observe": "cmd_stop_guard_observe",
}

READ_ONLY_LEAVES = frozenset(
    {
        "handoff await",
        "handoff verify",
        "parallel-status",
        "pregate check",
        "pregate digest",
        "queue next",
        "queue status",
    }
)

SEPARATE_WRITER_LEAVES = frozenset(SCOPED_LEAF_HANDLERS) - READ_ONLY_LEAVES

U5_WRITER_PROTOCOLS = {
    "AppendEvidenceHandoff": ("evidence_handoff.py", "publish"),
    "ReplacePregateRecord": ("pregate_cache.py", "record"),
    "UpdateMergeQueue": ("merge_queue.py", "_write_queue_unlocked"),
    "CloseParallelGroup": ("mission-state.py", "_replace_parallel_manifest"),
    "RecordStopObservation": ("mission-state.py", "_write_stop_guard_state"),
    "AppendCommandOutcome": ("command_outcomes.py", "_atomic_json_at"),
}

AUTHORITY_FIELDS = frozenset(
    {
        "terminal_outcome",
        "phase",
        "passes",
        "loop_active",
        "halt_reason",
        "halt_category",
        "score_history",
    }
)

ALLOWED_BOUNDARIES = frozenset(
    {
        "publish_evidence_handoff",
        "record_pregate_cache",
        "enqueue_merge_queue",
        "mark_merge_queue",
        "verify_merge_queue",
        "_replace_parallel_manifest",
        "observe_stop_guard",
    }
)


def _load_mission_state_module():
    spec = importlib.util.spec_from_file_location("mission_state_issue625", MISSION_STATE_SOURCE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _leaf_handlers(parser: argparse.ArgumentParser, prefix=()) -> dict[str, str]:
    actions = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    if not actions:
        handler = parser._defaults.get("func")
        assert handler is not None
        return {" ".join(prefix): handler.__name__}
    output: dict[str, str] = {}
    for action in actions:
        for name, child in action.choices.items():
            output.update(_leaf_handlers(child, prefix + (name,)))
    return output


def _no_session_write_violations(source: str, entrypoints: set[str]) -> list[str]:
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    bindings: dict[str, dict[str, ast.AST]] = {}
    for name, function in functions.items():
        local: dict[str, ast.AST] = {}
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        local[target.id] = node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                local[node.target.id] = node.value
        bindings[name] = local

    violations: set[str] = set()
    visited: set[str] = set()

    def _constant_key(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    def _assigned_keys(target: ast.AST) -> set[str]:
        if isinstance(target, ast.Subscript):
            key = _constant_key(target.slice)
            return {key} if key is not None else set()
        if isinstance(target, (ast.List, ast.Tuple)):
            return {key for child in target.elts for key in _assigned_keys(child)}
        return set()

    def _callable_names(expression: ast.AST, owner: str, resolving=frozenset()) -> set[str]:
        if isinstance(expression, ast.Name):
            key = (owner, expression.id)
            if key in resolving:
                return set()
            bound = bindings.get(owner, {}).get(expression.id)
            if bound is not None:
                return _callable_names(bound, owner, resolving | {key})
            return {expression.id}
        if isinstance(expression, ast.Attribute):
            return {expression.attr}
        if isinstance(expression, ast.Lambda):
            return _callable_names(expression.body, owner, resolving)
        if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
            return {
                name
                for item in expression.elts
                for name in _callable_names(item, owner, resolving)
            }
        if isinstance(expression, ast.Dict):
            return {
                name
                for item in expression.values
                for name in _callable_names(item, owner, resolving)
            }
        if isinstance(expression, ast.Subscript):
            return _callable_names(expression.value, owner, resolving)
        if isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name):
            if expression.func.id in {"eval", "exec", "getattr"}:
                return {"<dynamic>"}
        return set()

    def _literal_authority_keys(expression: ast.AST) -> set[str]:
        if not isinstance(expression, ast.Dict):
            return set()
        return {
            key
            for item in expression.keys
            if (key := _constant_key(item)) in AUTHORITY_FIELDS
        }

    def _visit(name: str) -> None:
        if name in visited or name in ALLOWED_BOUNDARIES:
            return
        visited.add(name)
        function = functions.get(name)
        if function is None:
            return
        for node in ast.walk(function):
            if isinstance(node, ast.Assign):
                if any(_assigned_keys(target) & AUTHORITY_FIELDS for target in node.targets):
                    violations.add("authority-field-write")
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                if _assigned_keys(node.target) & AUTHORITY_FIELDS:
                    violations.add("authority-field-write")
            if not isinstance(node, ast.Call):
                continue
            names = _callable_names(node.func, name)
            if "<dynamic>" in names or names & {"eval", "exec"}:
                violations.add("dynamic-writer-resolution")
            if names & {"StateLock", "atomic_write_json", "atomic_write_bytes"}:
                violations.add("session-write-sink")
            if names & {"save", "execute", "stage"}:
                violations.add("session-repository-write")
            if names & {"write_text", "write_bytes"}:
                violations.add("session-path-write")
            if "open" in names:
                mode = None
                if len(node.args) > 1:
                    mode = _constant_key(node.args[1])
                for keyword in node.keywords:
                    if keyword.arg == "mode":
                        mode = _constant_key(keyword.value)
                if mode is not None and set(mode) & {"w", "a", "x", "+"}:
                    violations.add("session-path-write")
            if names & {"update", "setdefault"}:
                if any(_literal_authority_keys(argument) for argument in node.args):
                    violations.add("authority-field-write")
                if names == {"setdefault"} and node.args:
                    if _constant_key(node.args[0]) in AUTHORITY_FIELDS:
                        violations.add("authority-field-write")
            if "setattr" in names and len(node.args) >= 2:
                if _constant_key(node.args[1]) in AUTHORITY_FIELDS:
                    violations.add("authority-field-write")
            for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
                callbacks = _callable_names(argument, name)
                if callbacks & {"atomic_write_json", "atomic_write_bytes"}:
                    violations.add("session-write-sink")
                if "<dynamic>" in callbacks:
                    violations.add("dynamic-writer-resolution")
            for called_name in names:
                _visit(called_name)

    for entrypoint in entrypoints:
        _visit(entrypoint)
    return sorted(violations)


def test_scoped_parser_leaf_and_handler_inventory_is_exact():
    handlers = _leaf_handlers(_load_mission_state_module()._build_parser())

    assert {path: handlers[path] for path in SCOPED_LEAF_HANDLERS} == SCOPED_LEAF_HANDLERS
    assert len(SCOPED_LEAF_HANDLERS) == 14
    assert len(set(SCOPED_LEAF_HANDLERS.values())) == 8
    assert READ_ONLY_LEAVES | SEPARATE_WRITER_LEAVES == frozenset(SCOPED_LEAF_HANDLERS)
    assert READ_ONLY_LEAVES.isdisjoint(SEPARATE_WRITER_LEAVES)
    assert "queue verify" in SEPARATE_WRITER_LEAVES


def test_scoped_owner_inventory_is_c1_thirteen_and_a5_one():
    from mission_application.command_owners import COMMAND_OWNER_REGISTRY

    owners = {path: COMMAND_OWNER_REGISTRY[path] for path in SCOPED_LEAF_HANDLERS}
    previously_adjudicated = {
        "archive-worktree",
        "cleanup-empty",
        "parallel-init",
        "permission-preflight",
        "resolve-archive",
    }
    remaining = {
        path
        for path, owner in COMMAND_OWNER_REGISTRY.items()
        if owner in {"C1.separate-aggregate", "A5.runtime-guard"}
    } - previously_adjudicated

    assert list(owners.values()).count("C1.separate-aggregate") == 13
    assert list(owners.values()).count("A5.runtime-guard") == 1
    assert owners["stop-guard-observe"] == "A5.runtime-guard"
    assert remaining == set(SCOPED_LEAF_HANDLERS)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            'def handler(state):\n    state["loop_active"] = False\n',
            "authority-field-write",
        ),
        (
            "def handler(state):\n"
            "    writer = atomic_write_json\n"
            "    writer(resolve_state_file(), state)\n",
            "session-write-sink",
        ),
        (
            "def helper(state):\n"
            "    atomic_write_json(resolve_state_file(), state)\n\n"
            "def handler(state):\n"
            "    helper(state)\n",
            "session-write-sink",
        ),
        (
            "def handler(repository, state):\n    repository.save(state)\n",
            "session-repository-write",
        ),
        (
            "def session_file():\n    return resolve_state_file()\n\n"
            "def handler(payload):\n"
            "    session_file().write_text(payload)\n",
            "session-path-write",
        ),
        (
            "def handler(repository, name, state):\n"
            "    getattr(repository, name)(state)\n",
            "dynamic-writer-resolution",
        ),
        (
            "def handler(state):\n"
            "    writers = [atomic_write_json]\n"
            "    writers[0](resolve_state_file(), state)\n",
            "session-write-sink",
        ),
        (
            "def handler(state):\n"
            "    writer = lambda path, value: atomic_write_json(path, value)\n"
            "    writer(resolve_state_file(), state)\n",
            "session-write-sink",
        ),
        (
            "def invoke(callback):\n"
            "    callback()\n\n"
            "def handler():\n"
            "    invoke(atomic_write_bytes)\n",
            "session-write-sink",
        ),
        (
            "def handler(state):\n"
            "    state.update({'loop_active': False})\n",
            "authority-field-write",
        ),
        (
            "def handler(state):\n"
            "    setattr(state, 'halt_reason', 'forged')\n",
            "authority-field-write",
        ),
    ],
)
def test_no_session_write_guard_detects_synthetic_violations(source, expected):
    assert expected in _no_session_write_violations(source, {"handler"})


@pytest.mark.parametrize(
    "source",
    [
        'def handler(state):\n    return state.get("loop_active")\n',
        "def handler(cwd, payload):\n"
        "    return publish_evidence_handoff(cwd, 'topic', payload)\n",
    ],
)
def test_no_session_write_guard_accepts_reads_and_allowlisted_sidecars(source):
    assert _no_session_write_violations(source, {"handler"}) == []


def test_scoped_handlers_have_no_reachable_session_writer():
    source = MISSION_STATE_SOURCE.read_text(encoding="utf-8")

    assert _no_session_write_violations(
        source, set(SCOPED_LEAF_HANDLERS.values())
    ) == []


def test_stop_observation_repository_save_has_one_sidecar_sink():
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))
    repository = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "_LegacyStopObservationRepository"
    )
    save = next(
        node
        for node in repository.body
        if isinstance(node, ast.FunctionDef) and node.name == "save"
    )
    calls = {
        node.func.id
        for node in ast.walk(save)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert calls & {"_write_stop_guard_state", "atomic_write_json", "atomic_write_bytes"} == {
        "_write_stop_guard_state"
    }


def test_global_failure_telemetry_boundary_is_explicit():
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))
    adapter = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_record_command_outcome_only"
    )
    adapter_calls = {
        node.func.id
        for node in ast.walk(adapter)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    internal_error_handlers = [
        node
        for node in ast.walk(main)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "Exception"
    ]

    assert "append_command_outcome_sidecar" in adapter_calls
    assert any(
        isinstance(call.func, ast.Name)
        and call.func.id == "_record_command_outcome_only"
        for handler in internal_error_handlers
        for call in ast.walk(handler)
        if isinstance(call, ast.Call)
    )


def _assert_session_unchanged(run_cli, state_path: Path, visited: list[str], path: str, *args):
    before = state_path.read_bytes()
    result = run_cli(*args, cwd=state_path.parents[2])
    assert result.returncode == 0, result.stderr
    assert state_path.read_bytes() == before
    visited.append(path)
    return result


@pytest.mark.parametrize("schema_version", [4, 5])
def test_all_scoped_leaf_paths_preserve_session_bytes(
    raw_run_cli, tmp_path, schema_version
):
    root = tmp_path / f"schema-{schema_version}"
    sessions = root / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    if schema_version == 4:
        state = {
            "schema_version": 4,
            "mission": "issue625 authority sentinel",
            "mission_id": "issue625",
            "session_id": "test",
            "phase": "review",
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "score_history": [
                {
                    "score_provenance": {
                        "revision_scope": {
                            "base_sha": "a" * 40,
                            "head_sha": "b" * 40,
                        }
                    }
                }
            ],
        }
    else:
        from .mission_state_fixture_corpus import current_v5_open_state

        state = current_v5_open_state()
    state_path = sessions / "test.json"
    state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    visited: list[str] = []

    payload_path = root / "payload.json"
    payload_path.write_text('{"evidence":"bounded"}', encoding="utf-8")
    published = _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "handoff publish",
        "handoff",
        "publish",
        "--topic",
        "issue-625",
        "--input",
        str(payload_path),
    )
    handoff_path = json.loads(published.stdout)["path"]
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "handoff await",
        "handoff",
        "await",
        "--topic",
        "issue-625",
        "--after-seq",
        "0",
        "--timeout-sec",
        "0",
    )
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "handoff verify",
        "handoff",
        "verify",
        "--path",
        handoff_path,
    )

    digest = "sha256:" + hashlib.sha256(b"issue625-subject").hexdigest()
    evaluation = {
        "schema": "mission-pregate-evaluation/1",
        "issue_ref": "625",
        "subject_digest": digest,
        "evaluated_at": "2026-08-23T00:00:00Z",
        "ttl_hours": 72,
        "verdict": "accepted",
        "gate_id": "issue625-check",
        "evidence_refs": [{"kind": "path", "value": "reports/625.md"}],
        "producer_session": "test",
        "payload": {"subject": "issue625-subject"},
    }
    evaluation_path = root / "pregate.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "pregate digest",
        "pregate",
        "digest",
        "--input",
        str(payload_path),
    )
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "pregate record",
        "pregate",
        "record",
        "--issue-ref",
        "625",
        "--input",
        str(evaluation_path),
    )
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "pregate check",
        "pregate",
        "check",
        "--issue-ref",
        "625",
        "--subject-digest",
        digest,
    )

    enqueue_args = [
        "queue",
        "enqueue",
        "--issue-ref",
        "625",
        "--pr-ref",
        "pr-625",
    ]
    if schema_version == 4:
        enqueue_args.append("--from-state")
    else:
        enqueue_args.extend(("--head-sha", "b" * 40, "--base-sha", "a" * 40))
    enqueued = _assert_session_unchanged(
        raw_run_cli, state_path, visited, "queue enqueue", *enqueue_args
    )
    queue_id = json.loads(enqueued.stdout)["queue_id"]
    _assert_session_unchanged(
        raw_run_cli, state_path, visited, "queue status", "queue", "status"
    )
    _assert_session_unchanged(
        raw_run_cli, state_path, visited, "queue next", "queue", "next"
    )
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "queue verify",
        "queue",
        "verify",
        "--queue-id",
        queue_id,
        "--current-base-sha",
        "a" * 40,
    )
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "queue mark",
        "queue",
        "mark",
        "--queue-id",
        queue_id,
        "--status",
        "merged",
    )

    initialized = raw_run_cli(
        "parallel-init",
        "--group-id",
        "issue625-group",
        "--issue-ref",
        "626",
        cwd=root,
    )
    assert initialized.returncode == 0, initialized.stderr
    child = {
        "schema_version": 4,
        "mission": "issue625 child",
        "mission_id": "issue625-child",
        "session_id": "issue625-child",
        "logical_group_id": "issue625-group",
        "issue_ref": "626",
        "loop_active": False,
        "passes": True,
        "halt_reason": "",
        "lease_expires_at": "2000-01-01T00:00:00Z",
    }
    (sessions / "issue625-child.json").write_text(json.dumps(child), encoding="utf-8")
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "parallel-status",
        "parallel-status",
        "--group-id",
        "issue625-group",
    )
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "parallel-closeout",
        "parallel-closeout",
        "--group-id",
        "issue625-group",
    )
    _assert_session_unchanged(
        raw_run_cli,
        state_path,
        visited,
        "stop-guard-observe",
        "stop-guard-observe",
        "--session-id",
        "test",
        "--digest",
        "c" * 64,
        "--now-epoch",
        "100",
        "--ttl-seconds",
        "60",
    )

    assert frozenset(visited) == frozenset(SCOPED_LEAF_HANDLERS)


def _sidecar_root(tmp_path: Path) -> Path:
    root = tmp_path / "sidecar-root"
    sessions = root / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "test.json").write_bytes(b'{"authority":"unchanged"}')
    return root


def _assert_authority_sentinel(root: Path) -> None:
    assert (
        root / ".mission-state" / "sessions" / "test.json"
    ).read_bytes() == b'{"authority":"unchanged"}'


def test_handoff_validates_generated_envelope_before_publish(monkeypatch, tmp_path):
    import evidence_handoff

    root = _sidecar_root(tmp_path)
    calls: list[tuple[dict, Path]] = []
    original = evidence_handoff._validate_envelope

    def recording_validator(document, *, path):
        calls.append((document, path))
        return original(document, path=path)

    monkeypatch.setattr(evidence_handoff, "_validate_envelope", recording_validator)

    result = evidence_handoff.publish(root, "issue-625-validate", {"ok": True})

    assert len(calls) == 1
    assert calls[0][1] == Path(result["path"])
    assert calls[0][0]["payload"] == {"ok": True}


def test_handoff_publish_rejects_destination_appearance_without_overwrite(
    monkeypatch, tmp_path
):
    import evidence_handoff

    root = _sidecar_root(tmp_path)
    topic = "issue-625-conflict"
    payload = {"evidence": "original"}
    digest = evidence_handoff.payload_digest(payload)
    topic_dir = root / ".mission-state" / "handoff" / topic
    final_path = topic_dir / f"1-{digest.removeprefix('sha256:')[:8]}.json"
    attacker_bytes = b'{"external":"must-stay"}'
    original_named_temporary_file = tempfile.NamedTemporaryFile

    class _AppearingDestination:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self.wrapped.__enter__()

        def __exit__(self, *args):
            result = self.wrapped.__exit__(*args)
            final_path.write_bytes(attacker_bytes)
            return result

    def appearing_destination(*args, **kwargs):
        return _AppearingDestination(original_named_temporary_file(*args, **kwargs))

    monkeypatch.setattr(evidence_handoff.tempfile, "NamedTemporaryFile", appearing_destination)

    with pytest.raises(evidence_handoff.EvidenceHandoffError, match="appeared"):
        evidence_handoff.publish(root, topic, payload)

    assert final_path.read_bytes() == attacker_bytes
    assert not list(topic_dir.glob(".tmp-*"))
    _assert_authority_sentinel(root)


@pytest.mark.parametrize(
    ("stage", "published_count"),
    [
        ("before-temp", 0),
        ("after-temp-fsync", 0),
        ("before-link", 0),
        ("after-link", 1),
    ],
)
def test_handoff_publish_kill_points_leave_zero_or_one_complete_envelope(
    monkeypatch, tmp_path, stage, published_count
):
    import evidence_handoff

    root = _sidecar_root(tmp_path)
    topic = f"issue-625-{stage}"
    if stage == "before-temp":
        monkeypatch.setattr(
            evidence_handoff.tempfile,
            "NamedTemporaryFile",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("before temp")),
        )
    elif stage == "after-temp-fsync":
        original_fsync = evidence_handoff.os.fsync

        def crash_after_fsync(fd):
            original_fsync(fd)
            raise OSError("after temp fsync")

        monkeypatch.setattr(evidence_handoff.os, "fsync", crash_after_fsync)
    elif stage == "before-link":
        monkeypatch.setattr(
            evidence_handoff.os,
            "link",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("before link")),
        )
    else:
        monkeypatch.setattr(
            evidence_handoff,
            "_fsync_directory",
            lambda _path: (_ for _ in ()).throw(OSError("after link")),
        )

    with pytest.raises(evidence_handoff.EvidenceHandoffError, match="publish failed"):
        evidence_handoff.publish(root, topic, {"stage": stage})

    topic_dir = root / ".mission-state" / "handoff" / topic
    envelopes = [path for path in topic_dir.glob("*.json") if not path.name.startswith(".tmp-")]
    assert len(envelopes) == published_count
    assert not list(topic_dir.glob(".tmp-*"))
    for envelope in envelopes:
        evidence_handoff.verify_handoff(envelope)
    _assert_authority_sentinel(root)


def test_handoff_concurrent_publish_keeps_every_unique_envelope(tmp_path):
    import evidence_handoff

    root = _sidecar_root(tmp_path)
    topic = "issue-625-concurrent"
    payloads = [{"index": index} for index in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(lambda payload: evidence_handoff.publish(root, topic, payload), payloads)
        )

    assert sorted(result["seq"] for result in results) == list(range(1, 9))
    paths = [Path(result["path"]) for result in results]
    assert len(set(paths)) == 8
    assert all(path.exists() for path in paths)
    assert {evidence_handoff.verify_handoff(path)["payload_digest"] for path in paths} == {
        evidence_handoff.payload_digest(payload) for payload in payloads
    }
    _assert_authority_sentinel(root)


def _pregate_evaluation(issue_ref: str, marker: str) -> dict:
    return {
        "schema": "mission-pregate-evaluation/1",
        "issue_ref": issue_ref,
        "subject_digest": "sha256:" + marker * 64,
        "evaluated_at": "2026-08-23T00:00:00Z",
        "ttl_hours": 72,
        "verdict": "accepted",
        "gate_id": f"gate-{marker}",
        "evidence_refs": [{"kind": "path", "value": "reports/evidence.md"}],
        "producer_session": "test",
        "payload": {"marker": marker},
    }


@pytest.mark.parametrize("existing", [False, True], ids=["absent", "present"])
def test_pregate_record_rejects_identity_drift_without_overwrite(
    monkeypatch, tmp_path, existing
):
    import pregate_cache

    root = _sidecar_root(tmp_path)
    final_path = root / ".mission-state" / "pregate" / "625.json"
    if existing:
        pregate_cache.record(root, _pregate_evaluation("625", "a"), issue_ref="625")
    detached = final_path.with_suffix(".captured")
    attacker_bytes = b'{"external":"must-stay"}'
    original_named_temporary_file = tempfile.NamedTemporaryFile

    class _DriftingDestination:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self.wrapped.__enter__()

        def __exit__(self, *args):
            result = self.wrapped.__exit__(*args)
            if existing:
                final_path.replace(detached)
            final_path.write_bytes(attacker_bytes)
            return result

    def drifting_destination(*args, **kwargs):
        return _DriftingDestination(original_named_temporary_file(*args, **kwargs))

    monkeypatch.setattr(pregate_cache.tempfile, "NamedTemporaryFile", drifting_destination)

    with pytest.raises(pregate_cache.PregateCacheError, match="changed|appeared"):
        pregate_cache.record(root, _pregate_evaluation("625", "b"), issue_ref="625")

    assert final_path.read_bytes() == attacker_bytes
    if existing:
        assert b'"gate_id":"gate-a"' in detached.read_bytes()
    assert not list(final_path.parent.glob(".tmp-*"))
    _assert_authority_sentinel(root)


@pytest.mark.parametrize("hostile_kind", ["symlink", "hardlink"])
def test_pregate_record_rejects_hostile_existing_target_without_external_write(
    tmp_path, hostile_kind
):
    import pregate_cache

    root = _sidecar_root(tmp_path)
    pregate_root = root / ".mission-state" / "pregate"
    pregate_root.mkdir()
    final_path = pregate_root / "625.json"
    external = tmp_path / "external-pregate.json"
    original = b'{"external":"must-stay"}'
    external.write_bytes(original)
    if hostile_kind == "symlink":
        final_path.symlink_to(external)
    else:
        final_path.hardlink_to(external)

    with pytest.raises(pregate_cache.PregateCacheError, match="unsafe"):
        pregate_cache.record(root, _pregate_evaluation("625", "c"), issue_ref="625")

    assert external.read_bytes() == original
    _assert_authority_sentinel(root)


def test_pregate_publish_failure_preserves_existing_bytes(monkeypatch, tmp_path):
    import pregate_cache

    root = _sidecar_root(tmp_path)
    pregate_cache.record(root, _pregate_evaluation("625", "d"), issue_ref="625")
    final_path = root / ".mission-state" / "pregate" / "625.json"
    before = final_path.read_bytes()
    monkeypatch.setattr(
        pregate_cache.os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(pregate_cache.PregateCacheError, match="publish failed"):
        pregate_cache.record(root, _pregate_evaluation("625", "e"), issue_ref="625")

    assert final_path.read_bytes() == before
    assert not list(final_path.parent.glob(".tmp-*"))
    _assert_authority_sentinel(root)


def _enqueue_queue(root: Path, issue_ref: str = "625") -> dict:
    import merge_queue

    return merge_queue.enqueue(
        root,
        issue_ref=issue_ref,
        pr_ref=f"pr-{issue_ref}",
        head_sha="b" * 40,
        base_sha="a" * 40,
        session_id="test",
    )


@pytest.mark.parametrize(
    "operation",
    ["enqueue-absent", "enqueue-present", "mark", "verify-mismatch"],
)
def test_merge_queue_writers_reject_identity_drift_without_overwrite(
    monkeypatch, tmp_path, operation
):
    import merge_queue

    root = _sidecar_root(tmp_path)
    queue_path = root / ".mission-state" / "merge-queue.json"
    queue_id = None
    if operation != "enqueue-absent":
        queue_id = _enqueue_queue(root)["queue_id"]
    detached = queue_path.with_suffix(".captured")
    attacker_bytes = b'{"external":"must-stay"}'
    original_named_temporary_file = tempfile.NamedTemporaryFile

    class _DriftingDestination:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            return self.wrapped.__enter__()

        def __exit__(self, *args):
            result = self.wrapped.__exit__(*args)
            if queue_path.exists():
                queue_path.replace(detached)
            queue_path.write_bytes(attacker_bytes)
            return result

    def drifting_destination(*args, **kwargs):
        return _DriftingDestination(original_named_temporary_file(*args, **kwargs))

    monkeypatch.setattr(merge_queue.tempfile, "NamedTemporaryFile", drifting_destination)

    with pytest.raises(merge_queue.MergeQueueError, match="changed|appeared"):
        if operation.startswith("enqueue"):
            _enqueue_queue(root, "626")
        elif operation == "mark":
            merge_queue.mark(root, queue_id=queue_id, status_value="merged")
        else:
            merge_queue.verify(
                root, queue_id=queue_id, current_base_sha="c" * 40
            )

    assert queue_path.read_bytes() == attacker_bytes
    if detached.exists():
        assert b'"schema":"mission-merge-queue/1"' in detached.read_bytes()
    assert not list(queue_path.parent.glob(".tmp-*"))
    _assert_authority_sentinel(root)


def test_merge_queue_validates_proposed_document_before_publish(tmp_path):
    import merge_queue

    root = _sidecar_root(tmp_path)

    def invalidate(queue):
        queue["entries"] = [{"not": "an entry"}]
        return {"status": "invalid"}

    with pytest.raises(merge_queue.MergeQueueError, match="entry shape"):
        merge_queue._locked_queue_update(root, invalidate)

    assert not (root / ".mission-state" / "merge-queue.json").exists()
    _assert_authority_sentinel(root)


def test_merge_queue_publish_failure_preserves_existing_bytes(monkeypatch, tmp_path):
    import merge_queue

    root = _sidecar_root(tmp_path)
    queue_id = _enqueue_queue(root)["queue_id"]
    queue_path = root / ".mission-state" / "merge-queue.json"
    before = queue_path.read_bytes()
    monkeypatch.setattr(
        merge_queue.os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(merge_queue.MergeQueueError, match="publish failed"):
        merge_queue.mark(root, queue_id=queue_id, status_value="merged")

    assert queue_path.read_bytes() == before
    assert not list(queue_path.parent.glob(".tmp-*"))
    _assert_authority_sentinel(root)


def _command_outcome(event_id: str) -> dict:
    return {
        "event_id": event_id,
        "root_event_id": "issue625-root",
        "attempt": 1,
        "command": "issue625-fixture",
        "outcome_kind": "internal-error",
    }


@pytest.mark.parametrize("existing", [False, True], ids=["absent", "present"])
def test_command_outcome_rejects_identity_drift_without_overwrite(
    monkeypatch, tmp_path, existing
):
    import command_outcomes

    root = _sidecar_root(tmp_path)
    state_directory = root / ".mission-state"
    token = "issue625"
    if existing:
        command_outcomes.append_sidecar(
            state_directory, token, _command_outcome("first")
        )
    sidecar = state_directory / "telemetry" / "command-outcomes" / f"{token}.json"
    detached = sidecar.with_suffix(".captured")
    attacker_bytes = b'{"external":"must-stay"}'
    original_verify = command_outcomes._verify_directory_identity
    verification_count = 0

    def drift_before_publish(directory_fd, named_parent):
        nonlocal verification_count
        verification_count += 1
        if verification_count == 3:
            if existing:
                sidecar.replace(detached)
            sidecar.write_bytes(attacker_bytes)
        return original_verify(directory_fd, named_parent)

    monkeypatch.setattr(
        command_outcomes, "_verify_directory_identity", drift_before_publish
    )

    with pytest.raises(command_outcomes.OutcomeStoreError, match="changed|appeared"):
        command_outcomes.append_sidecar(
            state_directory, token, _command_outcome("second")
        )

    assert sidecar.read_bytes() == attacker_bytes
    if existing:
        assert b'"event_id":"first"' in detached.read_bytes()
    assert not list(sidecar.parent.glob(".*.tmp"))
    _assert_authority_sentinel(root)


def test_command_outcome_publish_failure_preserves_existing_bytes(
    monkeypatch, tmp_path
):
    import command_outcomes

    root = _sidecar_root(tmp_path)
    state_directory = root / ".mission-state"
    token = "issue625"
    command_outcomes.append_sidecar(
        state_directory, token, _command_outcome("first")
    )
    sidecar = state_directory / "telemetry" / "command-outcomes" / f"{token}.json"
    before = sidecar.read_bytes()
    monkeypatch.setattr(
        command_outcomes.os,
        "replace",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(command_outcomes.OutcomeStoreError, match="publish failed"):
        command_outcomes.append_sidecar(
            state_directory, token, _command_outcome("second")
        )

    assert sidecar.read_bytes() == before
    assert not list(sidecar.parent.glob(".*.tmp"))
    _assert_authority_sentinel(root)


def test_command_outcome_failure_does_not_escape_best_effort_adapter(monkeypatch, tmp_path):
    module = _load_mission_state_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        module,
        "append_command_outcome_sidecar",
        lambda *args, **kwargs: (_ for _ in ()).throw(module.OutcomeStoreError("blocked")),
    )

    module._record_command_outcome_only(tmp_path, _command_outcome("rejected"))


def test_command_outcome_publish_failure_preserves_internal_error_exit(
    monkeypatch, capsys, tmp_path
):
    module = _load_mission_state_module()

    def fail_command(_args):
        raise RuntimeError("synthetic command failure")

    args = SimpleNamespace(func=fail_command, cmd="fixture")
    parser = SimpleNamespace(parse_args=lambda: args)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "_build_parser", lambda: parser)
    monkeypatch.setattr(
        module,
        "append_command_outcome_sidecar",
        lambda *args, **kwargs: (_ for _ in ()).throw(module.OutcomeStoreError("blocked")),
    )

    with pytest.raises(SystemExit) as error:
        module.main()

    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["outcome_kind"] == "internal-error"


@pytest.mark.parametrize(
    "stage", ["after-read", "after-first-check", "after-temp-fsync"]
)
def test_parallel_closeout_rejects_manifest_identity_swap_at_every_check(
    monkeypatch, tmp_path, stage
):
    module = _load_mission_state_module()
    root = tmp_path / f"parallel-{stage}"
    root.mkdir()
    with module._ParallelGroupStore(root, create=True) as store:
        (root / ".mission-state" / "sessions" / "test.json").write_bytes(
            b'{"authority":"unchanged"}'
        )
        manifest = {
            "schema": module.PARALLEL_GROUP_SCHEMA,
            "group_id": "issue625-group",
            "created_at": "2026-08-23T00:00:00Z",
            "planned_children": [{"issue_ref": "625"}],
            "status": "running",
            "coverage": {},
        }
        path = module._parallel_manifest_path(root, "issue625-group")
        module._create_parallel_manifest(store, path, manifest)
        path, captured, identity = module._parallel_manifest(store, "issue625-group")
        before = path.read_bytes()
        detached = path.with_suffix(".captured")
        attacker_bytes = b'{"external":"must-stay"}'

        def swap_manifest():
            path.replace(detached)
            path.write_bytes(attacker_bytes)

        proposed = {
            **captured,
            "status": "terminal",
            "outcome": "pass",
            "closed_at": "2026-08-23T01:00:00Z",
        }
        if stage == "after-read":
            swap_manifest()
        else:
            original_write_temp = module._write_parallel_temp

            def write_temp_then_swap(directory_fd, payload):
                if stage == "after-first-check":
                    swap_manifest()
                    return original_write_temp(directory_fd, payload)
                temporary = original_write_temp(directory_fd, payload)
                swap_manifest()
                return temporary

            monkeypatch.setattr(module, "_write_parallel_temp", write_temp_then_swap)

        with pytest.raises(ValueError, match="changed"):
            module._replace_parallel_manifest(store, path, proposed, identity)

        assert path.read_bytes() == attacker_bytes
        assert detached.read_bytes() == before
        assert not list(path.parent.glob(".parallel-*.tmp"))
        _assert_authority_sentinel(root)


def _function_publish_calls(source: Path, function_name: str) -> set[str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    return {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
        and node.func.attr in {"link", "replace"}
    }


def test_u5_separate_aggregate_writer_inventory_is_closed():
    assert set(U5_WRITER_PROTOCOLS) == {
        "AppendEvidenceHandoff",
        "ReplacePregateRecord",
        "UpdateMergeQueue",
        "CloseParallelGroup",
        "RecordStopObservation",
        "AppendCommandOutcome",
    }
    for _operation, (module_name, function_name) in U5_WRITER_PROTOCOLS.items():
        source = (
            MISSION_STATE_SOURCE
            if module_name == "mission-state.py"
            else MISSION_ROOT / "lib" / module_name
        )
        assert _function_publish_calls(source, function_name) <= {"link", "replace"}
        assert _function_publish_calls(source, function_name)


def test_queue_enqueue_mark_and_verify_share_one_serial_commit_protocol(tmp_path):
    import merge_queue

    root = _sidecar_root(tmp_path)
    entries = [_enqueue_queue(root, str(700 + index)) for index in range(6)]

    def update(index):
        if index < 2:
            merge_queue.mark(
                root,
                queue_id=entries[index]["queue_id"],
                status_value="merged",
            )
            return "merged"
        if index < 4:
            with pytest.raises(merge_queue.BaseMismatchError):
                merge_queue.verify(
                    root,
                    queue_id=entries[index]["queue_id"],
                    current_base_sha="c" * 40,
                )
            return "invalidated"
        _enqueue_queue(root, str(800 + index))
        return "enqueued"

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert sorted(executor.map(update, range(6))) == [
            "enqueued",
            "enqueued",
            "invalidated",
            "invalidated",
            "merged",
            "merged",
        ]

    queue, _identity = merge_queue._load_queue(root)
    statuses = {entry["issue_ref_key"]: entry["status"] for entry in queue["entries"]}
    assert [statuses[str(700 + index)] for index in range(6)] == [
        "merged",
        "merged",
        "invalidated",
        "invalidated",
        "queued",
        "queued",
    ]
    assert statuses["804"] == "queued"
    assert statuses["805"] == "queued"
    _assert_authority_sentinel(root)


def test_stop_guard_handler_delegates_closed_observation_and_emits_python_mode(
    monkeypatch, capsys, tmp_path
):
    module = _load_mission_state_module()
    captured = {}

    def observe(repository, request):
        captured["repository"] = repository
        captured["request"] = request
        return SimpleNamespace(
            mode="heartbeat",
            document={
                "schema": module.STOP_GUARD_SCHEMA,
                "session_id": request.session_id,
                "last_digest": request.digest,
                "last_detail_epoch": request.now_epoch,
                "block_count": 2,
                "reinjection_count": 2,
                "detail_count": 1,
                "heartbeat_count": 1,
            },
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "observe_stop_guard", observe)
    module.cmd_stop_guard_observe(
        SimpleNamespace(
            session_id="issue625-session",
            digest="d" * 64,
            now_epoch=100,
            ttl_seconds=60,
        )
    )

    output = json.loads(capsys.readouterr().out)
    assert isinstance(captured["repository"], module._LegacyStopObservationRepository)
    assert captured["request"] == module.StopObservationRequest(
        session_id="issue625-session",
        digest="d" * 64,
        now_epoch=100,
        ttl_seconds=60,
    )
    assert output["mode"] == "heartbeat"


def test_stop_guard_handler_does_not_choose_guard_decision_or_session_transition():
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_stop_guard_observe"
    )
    calls = {
        node.func.id
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "observe_stop_guard" in calls
    assert calls.isdisjoint(
        {
            "run_mark_halt",
            "run_cleanup_stale",
            "monotonic_halt_decision",
            "derive_terminal_outcome",
        }
    )
