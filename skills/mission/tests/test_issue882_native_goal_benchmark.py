"""#882: native Goal benchmark observations are explicit and lossless."""

import importlib.util
import json
import sys
from pathlib import Path


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


def test_codex_schema_requires_native_goal_and_turn_operations():
    module = _load()
    schema = {"methods": ["thread/goal/set", "thread/goal/get", "thread/goal/clear", "turn/start"]}

    observation = module.detect_codex_capability(schema, "0.158.0")

    assert observation["supported"] is True
    assert observation["missing_operations"] == []
    assert observation["provider_version"] == "0.158.0"


def test_codex_missing_native_operation_is_unsupported_not_prompt_fallback():
    module = _load()

    observation = module.detect_codex_capability({"methods": ["turn/start"]}, "0.158.0")

    assert observation["supported"] is False
    assert observation["fallback"] is None
    assert "thread/goal/set" in observation["missing_operations"]


def test_event_without_completed_goal_is_not_native_completion():
    module = _load()
    set_goal = {"goal": {"threadId": "t-1", "objective": "inspect", "status": "active"}}
    get_goal = {"goal": {"threadId": "t-1", "objective": "inspect", "status": "active"}}

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
    goal = {"threadId": "t-1", "objective": "inspect", "status": "complete", "tokenBudget": 20, "tokensUsed": 9}

    result = module.observe_codex_goal(
        "t-1", "inspect", {"goal": {**goal, "status": "active"}}, {"goal": goal},
        [{"method": "turn/started", "params": {"threadId": "t-1"}}, {"method": "turn/completed", "params": {"threadId": "t-1"}}],
    )

    assert result["fidelity"] == "verified"
    assert result["outcome"] == "completed"
    assert result["goal_thread_id"] == "t-1"
    assert result["goal_status"] == "complete"
    assert result["tokens_used"] == 9


def test_budget_limited_native_goal_is_a_verified_blocked_outcome():
    module = _load()
    active = {"threadId": "t-1", "objective": "inspect", "status": "active"}
    limited = {"threadId": "t-1", "objective": "inspect", "status": "budgetLimited"}

    result = module.observe_codex_goal("t-1", "inspect", {"goal": active}, {"goal": limited}, [{"method": "turn/started", "params": {"threadId": "t-1"}}, {"method": "turn/completed", "params": {"threadId": "t-1"}}])

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


def test_assignment_outcomes_keep_every_assigned_cell_and_failure():
    module = _load()
    plan = [("task-a", "codex_native_goal"), ("task-a", "mission"), ("task-b", "mission")]
    records = [{"task_id": "task-a", "arm": "codex_native_goal", "outcome": "unsupported"}]

    outcomes = module.preserve_assignment_outcomes(plan, records)

    assert [item["outcome"] for item in outcomes] == ["unsupported", "not_started", "not_started"]
    assert outcomes[1]["reason"] == "missing_assignment_record"


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
            self.events = [{"method": "turn/started", "params": {"threadId": "thread-1"}}, {"method": "turn/completed", "params": {"threadId": "thread-1"}}]
            self.calls = []
        def request(self, method, params):
            self.calls.append((method, params))
            if method == "thread/start": return {"thread": {"id": "thread-1"}}
            if method == "thread/goal/set": return {"goal": {"threadId": "thread-1", "objective": params["objective"], "status": "active"}}
            if method == "thread/goal/get": return {"goal": {"threadId": "thread-1", "objective": fake.calls[2][1]["objective"], "status": "budgetLimited", "tokenBudget": 5}}
            if method == "thread/goal/clear": return {"cleared": True}
            return {}
        def wait_for_event(self, method): return method == "turn/completed"
        def close(self): pass

    fake = FakeRpc()
    monkeypatch.setattr(probe, "RpcProcess", lambda *_args: fake)
    result = probe.probe_codex(tmp_path, "do work", "artifact exists", 1, 5, 2)

    assert [name for name, _params in fake.calls] == ["initialize", "thread/start", "thread/goal/set", "turn/start", "thread/goal/get", "thread/goal/clear"]
    assert fake.calls[2][1]["tokenBudget"] == 5
    assert result["outcome"] == "blocked"
    assert result["reason"] == "goal_budget_limited"
    assert "Acceptance criterion: artifact exists" in fake.calls[2][1]["objective"]


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


def test_clear_failure_is_recorded_after_terminal_observation():
    module = _load()
    def rpc(method, _params):
        if method == "thread/goal/set": return {"goal": {"threadId": "t", "objective": "o", "status": "active"}}
        if method == "thread/goal/get": return {"goal": {"threadId": "t", "objective": "o", "status": "complete"}}
        if method == "thread/goal/clear": raise RuntimeError("clear")
        return {}
    result = module.run_codex_adapter(rpc, "t", "o", [{"method": "turn/started", "params": {"threadId": "t"}}, {"method": "turn/completed", "params": {"threadId": "t"}}])
    assert result["outcome"] == "completed"
    assert result["goal_cleared"] is False
    assert result["cleanup_error"] == "RuntimeError"


def test_new_schema_accepts_unsupported_and_keeps_historical_schema_separate():
    module = _load()
    schema = json.loads((ROOT / "benchmarks" / "mission-vs-goal" / "native_goal_result.schema.json").read_text())

    assert schema["properties"]["outcome"]["enum"] == ["completed", "failed", "blocked", "unsupported", "not_started"]
    assert module.HISTORICAL_RESULT_SCHEMA.name == "result.schema.json"
