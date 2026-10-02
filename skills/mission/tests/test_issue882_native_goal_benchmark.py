"""#882: native Goal benchmark observations are explicit and lossless."""

import importlib.util
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "benchmarks" / "mission-vs-goal" / "native_goal_benchmark.py"


def _load():
    spec = importlib.util.spec_from_file_location("native_goal_benchmark", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_probe():
    path = ROOT / "benchmarks" / "mission-vs-goal" / "run_native_goal_probe.py"
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location("run_native_goal_probe", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_event_without_completed_goal_is_not_native_completion():
    module = _load()
    set_goal = {"goal": {"threadId": "t-1", "objective": "inspect", "status": "active", "createdAt": 1}}
    get_goal = {"goal": {"threadId": "t-1", "objective": "inspect", "status": "active", "createdAt": 1}}

    result = module.observe_codex_goal(
        "t-1", "inspect", set_goal, get_goal, [{"method": "turn/completed", "params": {"threadId": "t-1"}}],
    )

    assert result["native_goal_observed"] is True
    assert result["fidelity"] == "unverified"
    assert result["outcome"] == "failed"
    assert result["reason"] == "turn_not_completed"


def test_completed_goal_requires_matching_set_and_get_identity():
    module = _load()
    set_goal = {"goal": {"threadId": "t-1", "objective": "inspect", "status": "active"}}
    get_goal = {"goal": {"threadId": "other", "objective": "inspect", "status": "complete"}}

    result = module.observe_codex_goal("t-1", "inspect", set_goal, get_goal, [{"method": "turn/completed"}])

    assert result["fidelity"] == "unverified"
    assert result["outcome"] == "failed"
    assert result["reason"] == "goal_identity_mismatch"


def test_completed_goal_and_completed_turn_produce_verified_fidelity():
    module = _load()
    goal = {"threadId": "t-1", "objective": "inspect", "status": "complete", "tokenBudget": 20, "tokensUsed": 9, "createdAt": 1}

    result = module.observe_codex_goal(
        "t-1", "inspect", {"goal": {**goal, "status": "active"}}, {"goal": goal},
        [{"method": "turn/started", "params": {"threadId": "t-1", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t-1", "turnId": "turn"}}], {"turn"},
    )

    assert result["fidelity"] == "verified"
    assert result["outcome"] == "completed"
    assert result["goal_thread_id"] == "t-1"
    assert result["goal_status"] == "complete"
    assert result["tokens_used"] == 9


def test_codex_goal_records_malformed_status_as_a_failed_observation():
    module = _load()
    active = {"threadId": "t", "objective": "o", "status": "active", "createdAt": 1}
    malformed = {"threadId": "t", "objective": "o", "status": [], "createdAt": 1}

    result = module.observe_codex_goal(
        "t", "o", {"goal": active}, {"goal": malformed},
        [{"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}}], {"turn"},
    )

    assert result["fidelity"] == "unverified"
    assert result["outcome"] == "failed"
    assert result["reason"] == "goal_status_malformed"


def test_budget_limited_native_goal_is_a_verified_blocked_outcome():
    module = _load()
    active = {"threadId": "t-1", "objective": "inspect", "status": "active", "createdAt": 1}
    limited = {"threadId": "t-1", "objective": "inspect", "status": "budgetLimited", "createdAt": 1}

    result = module.observe_codex_goal("t-1", "inspect", {"goal": active}, {"goal": limited}, [{"method": "turn/started", "params": {"threadId": "t-1", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t-1", "turnId": "turn"}}], {"turn"})

    assert result["fidelity"] == "verified"
    assert result["outcome"] == "blocked"
    assert result["reason"] == "goal_budget_limited"


def test_claude_requires_official_goal_invocation_and_terminal_observation():
    module = _load()

    result = module.observe_claude_goal("/goal inspect", 0, {"session_id": "s-1"})
    assert result["fidelity"] == "unverified"
    assert result["outcome"] == "failed"

    result = module.observe_claude_goal("/goal inspect", 0, {"session_id": "s-1", "is_error": False, "result": "done"})
    assert result["fidelity"] == "unverified"
    assert result["outcome"] == "failed"


def test_codex_events_must_start_then_complete_on_the_target_thread():
    module = _load()
    active = {"threadId": "t", "objective": "o", "status": "active"}
    complete = {"threadId": "t", "objective": "o", "status": "complete"}
    for events in (
        [{"method": "turn/completed", "params": {"threadId": "t"}}],
        [{"method": "turn/completed", "params": {"threadId": "t"}}, {"method": "turn/started", "params": {"threadId": "t"}}],
        [{"method": "turn/started", "params": {"threadId": "other"}}, {"method": "turn/completed", "params": {"threadId": "other"}}],
    ):
        result = module.observe_codex_goal("t", "o", {"goal": active}, {"goal": complete}, events)
        assert result["fidelity"] == "unverified"
        assert result["outcome"] == "failed"


def test_codex_goal_generation_and_turn_identity_must_match():
    module = _load()
    created = {"threadId": "t", "objective": "o", "status": "active", "createdAt": 1}
    observed = {"threadId": "t", "objective": "o", "status": "complete", "createdAt": 2}
    result = module.observe_codex_goal("t", "o", {"goal": created}, {"goal": observed}, [])
    assert result["reason"] == "goal_identity_mismatch"
    observed["createdAt"] = 1
    events = [{"method": "turn/started", "params": {"threadId": "t", "turnId": "other"}}, {"method": "turn/completed", "params": {"threadId": "t", "turnId": "other"}}]
    result = module.observe_codex_goal("t", "o", {"goal": created}, {"goal": observed}, events, {"expected"})
    assert result["reason"] == "turn_not_completed"
    for invalid in (True, 1.0):
        result = module.observe_codex_goal("t", "o", {"goal": created}, {"goal": {**observed, "createdAt": invalid}}, events, {"other"})
        assert result["reason"] == "goal_identity_mismatch"
        result = module.observe_codex_goal("t", "o", {"goal": {**created, "createdAt": invalid}}, {"goal": observed}, events, {"other"})
        assert result["reason"] == "goal_identity_mismatch"


def test_codex_goal_rejects_missing_created_at_and_turn_identity():
    module = _load()
    goal = {"threadId": "t", "objective": "o", "status": "complete"}
    result = module.observe_codex_goal("t", "o", {"goal": {**goal, "status": "active"}}, {"goal": goal}, [])
    assert result["reason"] == "goal_identity_mismatch"
    goal["createdAt"] = 1
    result = module.observe_codex_goal("t", "o", {"goal": {**goal, "status": "active"}}, {"goal": goal}, [{"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}}])
    assert result["reason"] == "turn_not_completed"


def test_assignment_outcomes_keep_every_assigned_cell_and_failure():
    module = _load()
    plan = [("a-goal", "task-a", "codex_native_goal"), ("a-mission", "task-a", "mission"), ("b-mission", "task-b", "mission")]
    records = [{"assignment_id": "a-goal", "task_id": "task-a", "arm": "codex_native_goal", "outcome": "unsupported"}]

    outcomes = module.preserve_assignment_outcomes(plan, records)

    assert [item["outcome"] for item in outcomes] == ["unsupported", "not_started", "not_started"]
    assert outcomes[1]["reason"] == "missing_assignment_record"


def test_assignment_outcomes_preserve_repeated_attempts_for_one_cell():
    module = _load()
    outcomes = module.preserve_assignment_outcomes(
        [("one", "task-a", "mission"), ("two", "task-a", "mission")],
        [{"assignment_id": "two", "task_id": "task-a", "arm": "mission", "outcome": "completed"}, {"assignment_id": "one", "task_id": "task-a", "arm": "mission", "outcome": "failed"}],
    )
    assert [(item["assignment_id"], item["outcome"]) for item in outcomes] == [("one", "failed"), ("two", "completed")]


def test_assignment_outcomes_rejects_duplicate_or_unplanned_assignment_ids():
    module = _load()
    try:
        module.preserve_assignment_outcomes([("one", "task", "mission"), ("one", "task", "mission")], [])
    except ValueError as exc:
        assert "distinct assignment_id" in str(exc)
    else:
        raise AssertionError("duplicate plan ids must not duplicate an outcome")
    try:
        module.preserve_assignment_outcomes([("one", "task", "mission")], [{"assignment_id": "other", "task_id": "task", "arm": "mission"}])
    except ValueError as exc:
        assert "outside" in str(exc)
    else:
        raise AssertionError("an unplanned record must not disappear")


def test_immutable_manifest_binds_commit_package_and_source_digests(tmp_path):
    module = _load()
    source = tmp_path / "source"
    source.mkdir()
    (source / "skill.md").write_text("v1", encoding="utf-8")
    package = tmp_path / "package.tar"
    package.write_bytes(b"package-v1")

    manifest = module.immutable_manifest("a" * 40, source, package, {"model": "test", "timeout_seconds": 30})

    assert manifest["schema"] == "native-goal-benchmark-manifest/1"
    assert manifest["starting_commit"] == "a" * 40
    assert manifest["source"]["sha256"].startswith("sha256:")
    assert manifest["package"]["sha256"].startswith("sha256:")
    assert manifest["conditions"]["timeout_seconds"] == 30


def test_manifest_rejects_non_immutable_commit(tmp_path):
    module = _load()
    source = tmp_path / "source"
    source.mkdir()
    (source / "source.txt").write_text("source", encoding="utf-8")
    package = tmp_path / "package.tar"
    package.write_bytes(b"package")

    try:
        module.immutable_manifest("main", source, package, {})
    except ValueError as exc:
        assert "commit" in str(exc)
    else:
        raise AssertionError("branch name must not become an immutable manifest")

def test_immutable_package_uses_a_pinned_commit_and_leaves_no_partial_output(tmp_path, monkeypatch):
    module = _load()
    output = tmp_path / "mission.tar"
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        kwargs["stdout"].write(b"package")
        return type("Result", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.create_immutable_package(tmp_path, "c" * 40, output)

    assert output.read_bytes() == b"package"
    assert calls == [["git", "archive", "--format=tar", "c" * 40, "plugins/mission", "skills/mission"]]
    assert not list(tmp_path.glob(".mission-package-*.tmp"))


def test_worker_export_requires_an_allowlist_and_excludes_unlisted_files(tmp_path, monkeypatch):
    module = _load()
    source = tmp_path / "source"; source.mkdir()
    destination = tmp_path / "worker"
    def fake_run(_command, **kwargs):
        with tarfile.open(fileobj=kwargs["stdout"], mode="w") as archive:
            for name, content in (("answer-data.json", b"secret"), ("fixture.txt", b"safe")):
                member = tarfile.TarInfo(name); member.size = len(content)
                import io
                archive.addfile(member, io.BytesIO(content))
        return type("Result", (), {"returncode": 0, "stderr": b""})()
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    try:
        module.create_worker_export(source, "a" * 40, destination, [])
    except ValueError:
        pass
    else:
        raise AssertionError("a source checkout cannot be passed through without an allowlist contract")
    module.create_worker_export(source, "a" * 40, destination, ["fixture.txt"])
    assert not (destination / "answer-data.json").exists()
    assert (destination / "fixture.txt").read_text() == "safe"
    assert module.worker_export_manifest(destination)["sha256"].startswith("sha256:")


def test_worker_export_rejects_link_members_before_extracting(tmp_path, monkeypatch):
    module = _load()
    def fake_run(_command, **kwargs):
        with tarfile.open(fileobj=kwargs["stdout"], mode="w") as archive:
            member = tarfile.TarInfo("fixture-link"); member.type = tarfile.SYMTYPE; member.linkname = "outside"
            archive.addfile(member)
        return type("Result", (), {"returncode": 0, "stderr": b""})()
    monkeypatch.setattr(module.subprocess, "run", fake_run)
    try:
        module.create_worker_export(tmp_path, "a" * 40, tmp_path / "worker", ["fixture-link"])
    except ValueError as exc:
        assert "unsafe archive" in str(exc)
    else:
        raise AssertionError("link archive entries must not reach the worker")


def test_worker_export_manifest_rejects_post_run_external_link(tmp_path):
    module = _load()
    worker = tmp_path / "worker"; worker.mkdir(); (worker / "fixture.txt").write_text("safe", encoding="utf-8")
    (worker / "outside").symlink_to("/etc/hosts")
    try:
        module.worker_export_manifest(worker)
    except ValueError as exc:
        assert "link or special" in str(exc)
    else:
        raise AssertionError("post-run links must not be digested")


def test_worker_export_initializes_one_commit_without_source_history(tmp_path):
    import subprocess
    module = _load()
    worker = tmp_path / "worker"; worker.mkdir(); (worker / "fixture.txt").write_text("safe", encoding="utf-8")
    commit = module.initialize_worker_export_repository(worker)
    count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=worker, text=True, capture_output=True, check=True).stdout.strip()
    assert len(commit) == 40 and count == "1"


def test_worker_export_repository_satisfies_mission_revision_scope(tmp_path):
    module = _load()
    worker = tmp_path / "worker"; worker.mkdir(); (worker / "fixture.txt").write_text("safe", encoding="utf-8")
    commit = module.initialize_worker_export_repository(worker)
    state_path = ROOT / "skills" / "mission" / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_export_scope", state_path)
    state = importlib.util.module_from_spec(spec); spec.loader.exec_module(state)
    state._validate_revision_scope(worker, {"kind": "git", "base_sha": commit, "head_sha": commit})


def test_write_record_rejects_personal_paths_before_persisting(tmp_path):
    module = _load()
    path = tmp_path / "record.json"

    try:
        module.write_record(path, {"trace": "/.codex/memories/private"})
    except ValueError as exc:
        assert "unsafe trace" in str(exc)
    else:
        raise AssertionError("unsafe traces must not be persisted")
    assert not path.exists()


def test_write_record_redacts_assignment_text_and_sensitive_literals(tmp_path):
    module = _load()
    path = tmp_path / "record.json"
    module.write_record(path, {"manifest": {"conditions": {"objective": "private task", "acceptance_criterion": "private acceptance"}}, "error": "Bearer dummy-value person@example.net"})
    stored = path.read_text(encoding="utf-8")
    assert "private task" not in stored and "private acceptance" not in stored
    assert "dummy-value" not in stored and "person@example.net" not in stored
    assert "sha256:" in stored and "[redacted-bearer]" in stored


def test_write_record_never_replaces_an_earlier_assignment_outcome(tmp_path):
    module = _load()
    path = tmp_path / "record.json"
    module.write_record(path, {"outcome": "failed"})
    try:
        module.write_record(path, {"outcome": "completed"})
    except FileExistsError:
        pass
    else:
        raise AssertionError("a second run must choose a distinct output path")
    assert json.loads(path.read_text()) == {"outcome": "failed"}


def test_codex_probe_uses_goal_protocol_and_preserves_budget_limited_outcome(tmp_path, monkeypatch):
    probe = _load_probe()

    class FakeRpc:
        def __init__(self, *_args):
            self.events = [{"method": "turn/started", "params": {"threadId": "thread-1", "turnId": "turn-1"}}, {"method": "turn/completed", "params": {"threadId": "thread-1", "turnId": "turn-1"}}]
            self.calls = []
        def request(self, method, params):
            self.calls.append((method, params))
            if method == "thread/start": return {"thread": {"id": "thread-1"}, "model": "model-a", "reasoningEffort": "high", "activePermissionProfile": {"id": "workspace"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "thread-1", "objective": params["objective"], "status": "active", "createdAt": 1}}
            if method == "thread/goal/get": return {"goal": {"threadId": "thread-1", "objective": fake.calls[2][1]["objective"], "status": "budgetLimited", "tokenBudget": 5, "createdAt": 1}}
            if method == "turn/start": return {"turn": {"id": "turn-1"}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, method, *_ids): return method == "turn/completed"
        def close(self): pass

    fake = FakeRpc()
    monkeypatch.setattr(probe, "RpcProcess", lambda *_args: fake)
    result = probe.probe_codex(tmp_path, "do work", "artifact exists", 1, 5, 2, "model-a", "high", "workspace")

    assert [name for name, _params in fake.calls] == ["initialize", "thread/start", "thread/goal/set", "turn/start", "thread/goal/get", "thread/goal/clear"]
    assert fake.calls[0][1]["capabilities"] == {"experimentalApi": True}
    assert fake.calls[1][1]["config"] == {"model_reasoning_effort": "high"}
    assert fake.calls[2][1]["tokenBudget"] == 5
    assert result["outcome"] == "blocked"
    assert result["reason"] == "goal_budget_limited"
    assert "Acceptance criterion: artifact exists" in fake.calls[2][1]["objective"]
    assert fake.calls[3][1]["model"] == "model-a"
    assert fake.calls[3][1]["effort"] == "high"


def test_claude_probe_passes_official_goal_and_fixed_plugin_package(tmp_path, monkeypatch):
    probe = _load_probe()
    package = tmp_path / "package" / "plugins" / "mission"
    package.mkdir(parents=True)
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"session_id": "s", "is_error": False, "result": "done"})})()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    result = probe.probe_claude(tmp_path, tmp_path / "package", "do work", "artifact exists", 1, 0.5)

    assert result["outcome"] == "failed"
    assert seen["command"][:5] == ["claude", "--max-budget-usd", "0.5", "--plugin-dir", str(package)]
    assert seen["command"][-1].startswith("/goal do work")
    assert "Acceptance criterion: artifact exists" in seen["command"][-1]


def test_claude_mission_probe_uses_the_same_fixed_plugin_package(tmp_path, monkeypatch):
    probe = _load_probe()
    package = tmp_path / "package" / "plugins" / "mission"
    package.mkdir(parents=True)
    seen = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return type("Result", (), {"returncode": 0, "stdout": json.dumps({"session_id": "s", "is_error": False, "result": "done"})})()

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    result = probe.probe_claude(tmp_path, tmp_path / "package", "do work", "artifact exists", 1, 0.5, "mission")

    assert result["outcome"] == "failed"
    assert result["reason"] == "mission_state_unobserved"
    assert str(package) in seen["command"]
    assert seen["command"][-1].startswith("/mission do work")


def test_mission_state_requires_the_current_codex_session_binding(tmp_path):
    probe = _load_probe()
    sessions = tmp_path / ".mission-state" / "sessions"; sessions.mkdir(parents=True)
    fresh_ns = 10_000_000_000
    stale_ns = 9_000_000_000

    def write_state(name, payload, mtime_ns):
        path = sessions / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(path, ns=(mtime_ns, mtime_ns))
        return path, path.stat().st_mtime_ns

    current, current_mtime = write_state(
        "cx-thread.json",
        {"session_id": "cx-thread", "mission_id": "m", "passes": True, "loop_active": False},
        fresh_ns,
    )
    assert probe._fresh_mission_state(tmp_path, current_mtime, "thread") is not None
    current.unlink()

    stale, stale_mtime = write_state(
        "cx-thread.json",
        {"session_id": "cx-thread", "mission_id": "m", "passes": True},
        stale_ns,
    )
    assert stale_mtime < current_mtime
    assert probe._fresh_mission_state(tmp_path, current_mtime, "thread") is None
    stale.unlink()

    other, other_mtime = write_state(
        "cx-other.json",
        {"session_id": "cx-other", "mission_id": "other", "passes": True},
        fresh_ns,
    )
    assert probe._fresh_mission_state(tmp_path, other_mtime, "thread") is None
    other.unlink()

    _wrong_name, wrong_name_mtime = write_state(
        "cx-unrelated.json",
        {"session_id": "cx-thread", "mission_id": "m", "passes": True},
        fresh_ns,
    )
    assert probe._fresh_mission_state(tmp_path, wrong_name_mtime, "thread") is None


def test_mission_state_ignores_malformed_session_id(tmp_path):
    probe = _load_probe()
    sessions = tmp_path / ".mission-state" / "sessions"; sessions.mkdir(parents=True)
    (sessions / "cx-thread.json").write_text(json.dumps({"session_id": [], "passes": True}), encoding="utf-8")

    assert probe._fresh_mission_state(tmp_path, 0, "thread") is None


def test_claude_mission_state_ignores_malformed_session_id(tmp_path):
    probe = _load_probe()
    root = tmp_path / ".mission-state" / "sessions"; root.mkdir(parents=True)
    state = root / "thread.json"
    state.write_text(json.dumps({"session_id": [], "passes": True}), encoding="utf-8")

    assert probe._current_mission_state(tmp_path, "thread", 0) is None


def test_probe_binds_completed_event_and_retains_malformed_clear(tmp_path, monkeypatch):
    probe = _load_probe()
    class FakeRpc:
        def __init__(self, *_args):
            self.events = [{"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}}]
            self.wait_calls = []
        def request(self, method, params):
            if method == "thread/start": return {"thread": {"id": "t"}, "model": "m", "reasoningEffort": "low", "activePermissionProfile": {"id": "p"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": params["objective"], "status": "active", "createdAt": 1}}
            if method == "turn/start": return {"turn": {"id": "turn"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": "o\n\nAcceptance criterion: a", "status": "complete", "createdAt": 1}}
            if method == "thread/goal/clear": return []
            return {}
        def wait_for_event(self, *args): self.wait_calls.append(args); return True
        def close(self): pass
    fake = FakeRpc(); monkeypatch.setattr(probe, "RpcProcess", lambda *_args: fake)
    result = probe.probe_codex(tmp_path, "o", "a", 1, None, 1, "m", "low", "p")
    assert fake.wait_calls == [("turn/completed", "t", "turn")]
    assert result["outcome"] == "completed"
    assert result["cleanup_error"] == "malformed_clear_response"


def test_probe_records_malformed_goal_status_without_losing_the_failure(tmp_path, monkeypatch):
    probe = _load_probe()
    class FakeRpc:
        def __init__(self, *_args):
            self.events = [{"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}}]
        def request(self, method, params):
            if method == "thread/start": return {"thread": {"id": "t"}, "model": "m", "reasoningEffort": "low", "activePermissionProfile": {"id": "p"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": params["objective"], "status": "active", "createdAt": 1}}
            if method == "turn/start": return {"turn": {"id": "turn"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": "o\n\nAcceptance criterion: a", "status": [], "createdAt": 1}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, *_args): return True
        def close(self): pass
    monkeypatch.setattr(probe, "RpcProcess", FakeRpc)

    result = probe.probe_codex(tmp_path, "o", "a", 1, None, 1, "m", "low", "p")

    assert result["outcome"] == "failed"
    assert result["reason"] == "goal_status_malformed"


def test_probe_preserves_unobserved_completion_failure_when_goal_remains_active(tmp_path, monkeypatch):
    probe = _load_probe()
    class FakeRpc:
        def __init__(self, *_args): self.events = []
        def request(self, method, params):
            if method == "thread/start": return {"thread": {"id": "t"}, "model": "m", "reasoningEffort": "low", "activePermissionProfile": {"id": "p"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": params["objective"], "status": "active", "createdAt": 1}}
            if method == "turn/start": return {"turn": {"id": "turn"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": "o\n\nAcceptance criterion: a", "status": "active", "createdAt": 1}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, *_args): return False
        def close(self): pass
    monkeypatch.setattr(probe, "RpcProcess", FakeRpc)
    result = probe.probe_codex(tmp_path, "o", "a", 1, None, 1, "m", "low", "p")
    assert result["fidelity"] == "unverified"
    assert result["outcome"] == "failed"
    assert result["reason"] == "turn_completion_unobserved"
    assert result["turn_completed"] is False


def test_probe_reports_turn_limit_after_observed_active_turn(tmp_path, monkeypatch):
    probe = _load_probe()
    class FakeRpc:
        def __init__(self, *_args):
            self.events = [
                {"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}},
                {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}},
            ]
        def request(self, method, params):
            if method == "thread/start": return {"thread": {"id": "t"}, "model": "m", "reasoningEffort": "low", "activePermissionProfile": {"id": "p"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": params["objective"], "status": "active", "createdAt": 1}}
            if method == "turn/start": return {"turn": {"id": "turn"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": "o\n\nAcceptance criterion: a", "status": "active", "createdAt": 1}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, *_args): return True
        def close(self): pass
    monkeypatch.setattr(probe, "RpcProcess", FakeRpc)

    result = probe.probe_codex(tmp_path, "o", "a", 1, None, 1, "m", "low", "p")

    assert result["turn_completed"] is True
    assert result["fidelity"] == "verified"
    assert result["outcome"] == "blocked"
    assert result["reason"] == "assignment_turn_limit"


@pytest.mark.parametrize(
    ("events", "observed_created_at", "expected_reason"),
    [
        ([
            {"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}},
            {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}},
        ], 2, "goal_identity_mismatch"),
        ([
            {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}},
            {"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}},
        ], 1, "turn_not_completed"),
    ],
    ids=["generation-mismatch", "reverse-turn-events"],
)
def test_probe_does_not_promote_invalid_active_goal_to_turn_limit(tmp_path, monkeypatch, events, observed_created_at, expected_reason):
    probe = _load_probe()
    class FakeRpc:
        def __init__(self, *_args): self.events = events
        def request(self, method, params):
            if method == "thread/start": return {"thread": {"id": "t"}, "model": "m", "reasoningEffort": "low", "activePermissionProfile": {"id": "p"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": params["objective"], "status": "active", "createdAt": 1}}
            if method == "turn/start": return {"turn": {"id": "turn"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": "o\n\nAcceptance criterion: a", "status": "active", "createdAt": observed_created_at}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, *_args): return True
        def close(self): pass
    monkeypatch.setattr(probe, "RpcProcess", FakeRpc)

    result = probe.probe_codex(tmp_path, "o", "a", 1, None, 1, "m", "low", "p")

    assert result["fidelity"] == "unverified"
    assert result["outcome"] == "failed"
    assert result["reason"] == expected_reason


def test_codex_mission_uses_listed_fixed_skill_as_a_native_input(tmp_path, monkeypatch):
    probe = _load_probe()
    package = tmp_path / "package"
    skill = package / "skills" / "mission" / "SKILL.md"
    skill.parent.mkdir(parents=True); skill.write_text("skill", encoding="utf-8")

    class FakeRpc:
        def __init__(self, *_args): self.events = [{"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}}]; self.calls = []
        def request(self, method, params):
            self.calls.append((method, params))
            if method == "thread/start": return {"thread": {"id": "t"}, "model": "m", "reasoningEffort": "high", "activePermissionProfile": {"id": "p"}}
            if method == "skills/list": return {"data": [{"skills": [{"path": str(skill)}]}]}
            if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": params["objective"], "status": "active"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": self.calls[4][1]["objective"], "status": "complete"}}
            if method == "turn/start": return {"turn": {"id": "turn"}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, _method, *_ids): return True
        def close(self): pass
    fake = FakeRpc(); monkeypatch.setattr(probe, "RpcProcess", lambda *_args: fake)
    result = probe.probe_codex(tmp_path, "work", "accepted", 1, None, 1, "m", "high", "p", "mission", package)
    assert result["outcome"] == "failed"
    assert result["package_delivery"] == "skill_input"
    assert result["reason"] == "mission_state_unobserved"
    roots = next(params for name, params in fake.calls if name == "skills/extraRoots/set")
    assert roots == {"extraRoots": [str(package / "skills")]}
    turn = next(params for name, params in fake.calls if name == "turn/start")
    assert turn["input"][0] == {"type": "skill", "name": "mission", "path": str(skill)}
    assert not any(name.startswith("thread/goal/") for name, _ in fake.calls)


def test_codex_config_mismatch_is_not_verified_even_when_goal_completes(tmp_path, monkeypatch):
    probe = _load_probe()
    class FakeRpc:
        def __init__(self, *_args): self.events = [{"method": "turn/started", "params": {"threadId": "t", "turnId": "turn"}}, {"method": "turn/completed", "params": {"threadId": "t", "turnId": "turn"}}]
        def request(self, method, params):
            if method == "thread/start": return {"thread": {"id": "t"}, "model": "other", "reasoningEffort": "low", "activePermissionProfile": {"id": "p"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": params["objective"], "status": "active", "createdAt": 1}}
            if method == "turn/start": return {"turn": {"id": "turn"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": "work\n\nAcceptance criterion: accepted", "status": "complete", "createdAt": 1}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, _, *_ids): return True
        def close(self): pass
    monkeypatch.setattr(probe, "RpcProcess", FakeRpc)
    result = probe.probe_codex(tmp_path, "work", "accepted", 1, 1, 1, "requested", "low", "p")
    assert result["outcome"] == "completed"
    assert result["fidelity"] == "unverified"
    assert result["reason"] == "execution_config_mismatch"
    assert result["budget_enforcement"] == "goal_native"


def test_new_schema_accepts_unsupported_and_keeps_historical_schema_separate():
    module = _load()
    schema = json.loads((ROOT / "benchmarks" / "mission-vs-goal" / "native_goal_result.schema.json").read_text())

    assert schema["properties"]["outcome"]["enum"] == ["completed", "failed", "blocked", "unsupported", "not_started"]
    assert module.HISTORICAL_RESULT_SCHEMA.name == "result.schema.json"


def test_schema_rejects_incomplete_or_inconsistent_comparable_evidence():
    import jsonschema
    schema = json.loads((ROOT / "benchmarks" / "mission-vs-goal" / "native_goal_result.schema.json").read_text())
    digest = "sha256:" + "a" * 64
    record = {"schema": "native-goal-benchmark-result/1", "run_id": "run", "assignment_id": "assignment", "task_id": "task", "arm": "mission", "outcome": "completed", "fidelity": "verified", "config_matches": True, "package_prepared": True,
              "manifest": {"package": {"sha256": digest}, "source": {"sha256": digest}, "provider_version": "v1", "task_snapshot": {"expected": "a" * 40, "observed": "a" * 40, "clean": True, "matches": True}, "conditions": {"model_id": "m", "effort": "low", "permissions": "p"}, "worker_export": {"source_commit": "a" * 40, "export_commit": "b" * 40, "allowlist_count": 1, "initial_sha256": digest, "candidate_sha256": digest}}}
    jsonschema.validate(record, schema)
    for path, value in ((["manifest", "package"], None), (["manifest", "package", "sha256"], ""), (["manifest", "task_snapshot", "clean"], False), (["config_matches"], False)):
        invalid = json.loads(json.dumps(record)); target = invalid
        for key in path[:-1]: target = target[key]
        target[path[-1]] = value
        try:
            jsonschema.validate(invalid, schema)
        except jsonschema.ValidationError:
            pass
        else:
            raise AssertionError(f"comparable record accepted invalid {path}")

    unverified_completion = json.loads(json.dumps(record))
    unverified_completion.update({"fidelity": "unverified", "config_matches": False, "reason": "execution_config_mismatch"})
    jsonschema.validate(unverified_completion, schema)


def test_protocol_error_classification_requires_goal_method_and_jsonrpc_code():
    probe = _load_probe()
    assert probe._unsupported_goal_protocol(probe.RpcProtocolError("thread/goal/set", -32601, "Method not found")) is True
    assert probe._unsupported_goal_protocol(probe.RpcProtocolError("thread/start", -32601, "Method not found")) is False
    assert probe._unsupported_goal_protocol(OSError("-32601")) is False


def test_cli_main_rejects_nonfinite_values_and_accepts_finite_control(tmp_path, monkeypatch):
    probe = _load_probe()
    base = ["probe", "--host", "codex", "--objective", "o", "--task-id", "t", "--assignment-id", "assignment", "--acceptance-criterion", "a", "--starting-commit", "a" * 40, "--mission-source-repo", str(tmp_path), "--mission-source-commit", "a" * 40, "--model-id", "m", "--effort", "low", "--permissions", "p", "--worktree", str(tmp_path), "--output", str(tmp_path / "record.json")]
    for flag, value in (("--timeout-seconds", "nan"), ("--timeout-seconds", "inf"), ("--max-budget-usd", "nan"), ("--max-budget-usd", "inf")):
        monkeypatch.setattr(sys, "argv", [*base, flag, value])
        try: probe.main()
        except SystemExit as exc: assert exc.code == 2
        else: raise AssertionError("nonfinite CLI value must be rejected")
    monkeypatch.setattr(probe, "_task_snapshot", lambda _path: {"observed": "a" * 40, "clean": True})
    monkeypatch.setattr(sys, "argv", [*base, "--timeout-seconds", "1", "--max-budget-usd", "0.1"])
    assert probe.main() == 0
    record = json.loads((tmp_path / "record.json").read_text())
    assert record["outcome"] == "failed"
    assert record["assignment_id"] == "assignment"


def test_cli_main_records_goal_protocol_unavailable_separately_from_runtime_error(tmp_path, monkeypatch):
    probe = _load_probe()
    def package(_repo, _commit, output): output.write_bytes(b"tar"); return output
    def unpack(_archive, destination, **_kwargs): (Path(destination) / "plugins" / "mission" / "skills" / "mission").mkdir(parents=True); (Path(destination) / "plugins" / "mission" / "skills" / "mission" / "SKILL.md").write_text("x")
    monkeypatch.setattr(probe, "create_immutable_package", package); monkeypatch.setattr(probe.shutil, "unpack_archive", unpack)
    monkeypatch.setattr(probe, "create_worker_export", lambda source, _commit, destination, _allow: source)
    monkeypatch.setattr(probe, "initialize_worker_export_repository", lambda _root: "b" * 40)
    monkeypatch.setattr(probe, "worker_export_manifest", lambda _root: {"schema": "mission-worker-export/1", "sha256": "sha256:test"})
    monkeypatch.setattr(probe, "_task_snapshot", lambda _path: {"observed": "a" * 40, "clean": True}); monkeypatch.setattr(probe, "_codex_version", lambda: "test")
    base = ["probe", "--host", "codex", "--objective", "o", "--task-id", "t", "--assignment-id", "assignment", "--acceptance-criterion", "a", "--starting-commit", "a" * 40, "--mission-source-repo", str(tmp_path), "--mission-source-commit", "a" * 40, "--model-id", "m", "--effort", "low", "--permissions", "p", "--worktree", str(tmp_path), "--worker-allow-path", "fixture"]
    for name, error, outcome in (("unsupported", probe.RpcProtocolError("thread/goal/set", -32601, "Method not found"), "unsupported"), ("runtime", OSError("-32601"), "failed")):
        monkeypatch.setattr(probe, "probe_codex", lambda *_args, error=error: (_ for _ in ()).throw(error))
        output = tmp_path / f"{name}.json"; monkeypatch.setattr(sys, "argv", [*base, "--output", str(output)])
        assert probe.main() == 0
        assert json.loads(output.read_text())["outcome"] == outcome


def test_cli_main_keeps_candidate_worker_tree_after_provider_returns(tmp_path, monkeypatch):
    probe = _load_probe()
    def package(_repo, _commit, output): output.write_bytes(b"tar"); return output
    def unpack(_archive, destination, **_kwargs):
        skill = Path(destination) / "plugins" / "mission" / "skills" / "mission"; skill.mkdir(parents=True); (skill / "SKILL.md").write_text("x")
    def worker(_source, _commit, destination, _allow): destination.mkdir(parents=True); (destination / "fixture.txt").write_text("before"); return destination
    digests = iter(["sha256:" + "a" * 64, "sha256:" + "b" * 64])
    def run_worker(destination, *_args): (destination / "candidate.md").write_text("after"); return {"outcome": "failed", "fidelity": "unverified", "reason": "fixture"}
    monkeypatch.setattr(probe, "create_immutable_package", package); monkeypatch.setattr(probe.shutil, "unpack_archive", unpack)
    monkeypatch.setattr(probe, "create_worker_export", worker); monkeypatch.setattr(probe, "worker_export_manifest", lambda _root: {"sha256": next(digests)})
    monkeypatch.setattr(probe, "probe_codex", run_worker); monkeypatch.setattr(probe, "_codex_version", lambda: "test")
    monkeypatch.setattr(probe, "_task_snapshot", lambda _path: {"observed": "a" * 40, "clean": True})
    output = tmp_path / "record.json"
    argv = ["probe", "--host", "codex", "--objective", "o", "--task-id", "t", "--assignment-id", "assignment", "--acceptance-criterion", "a", "--starting-commit", "a" * 40, "--mission-source-repo", str(tmp_path), "--mission-source-commit", "a" * 40, "--model-id", "m", "--effort", "low", "--permissions", "p", "--worktree", str(tmp_path), "--worker-allow-path", "fixture.txt", "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    assert probe.main() == 0
    candidate = tmp_path / "candidates" / "record" / "candidate.md"
    assert candidate.read_text() == "after"
    manifest = json.loads(output.read_text())["manifest"]["worker_export"]
    assert manifest["candidate_path"] == "candidates/record" and manifest["initial_sha256"] != manifest["candidate_sha256"]


def test_cli_main_keeps_invalid_candidate_but_marks_snapshot_stale(tmp_path, monkeypatch):
    probe = _load_probe()
    def package(_repo, _commit, output): output.write_bytes(b"tar"); return output
    def unpack(_archive, destination, **_kwargs):
        skill = Path(destination) / "plugins" / "mission" / "skills" / "mission"; skill.mkdir(parents=True); (skill / "SKILL.md").write_text("x")
    def worker(_source, _commit, destination, _allow): destination.mkdir(parents=True); (destination / "fixture.txt").write_text("safe"); return destination
    def run_worker(destination, *_args): (destination / "outside").symlink_to("/etc/hosts"); return {"outcome": "completed", "fidelity": "verified", "reason": None, "config_matches": True}
    monkeypatch.setattr(probe, "create_immutable_package", package); monkeypatch.setattr(probe.shutil, "unpack_archive", unpack)
    monkeypatch.setattr(probe, "create_worker_export", worker); monkeypatch.setattr(probe, "probe_codex", run_worker); monkeypatch.setattr(probe, "_codex_version", lambda: "test")
    monkeypatch.setattr(probe, "_task_snapshot", lambda _path: {"observed": "a" * 40, "clean": True})
    output = tmp_path / "record.json"
    argv = ["probe", "--host", "codex", "--objective", "o", "--task-id", "t", "--assignment-id", "assignment", "--acceptance-criterion", "a", "--starting-commit", "a" * 40, "--mission-source-repo", str(tmp_path), "--mission-source-commit", "a" * 40, "--model-id", "m", "--effort", "low", "--permissions", "p", "--worktree", str(tmp_path), "--worker-allow-path", "fixture.txt", "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    assert probe.main() == 0
    record = json.loads(output.read_text())
    assert record["outcome"] == "failed" and record["reason"] == "candidate_snapshot_invalid"
    assert record["manifest"]["worker_export"]["candidate_state"] == "stale"
    assert (tmp_path / "candidates" / "record" / "outside").is_symlink()
