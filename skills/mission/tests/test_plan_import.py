import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
LIB_DIR = MISSION_STATE_PY.parent.parent / "lib"
sys.path.insert(0, str(LIB_DIR))
from specialist_accounting import candidate_accounting_report


def _contract():
    return {"envelope_schema": "mission-provider-result/1", "artifact_schema": "mission-plan/1", "cardinality": "exactly-one", "required_capability_class": "deep-planning", "required_capability_variant": "portable-v1", "require_exact_variant": True}


def _document():
    return {"objective":"bounded", "scope":{"resources":[],"actions":[{"type":"analyze","effect_class":"reversible"}]}, "assumptions":[{"id":"a","statement":"s","validation":"v"}], "steps":[{"id":"s","action":"analyze","inputs":[],"outputs":[],"depends_on":[],"acceptance_checks":["observable"],"risk":"low","rollback":"none"}], "global_acceptance":["done"],"stop_conditions":["blocked"]}


def _setup(run_cli, tmp_path):
    commands = tmp_path / "commands"; commands.mkdir()
    command = commands / "portable-plan-provider"; command.write_text("#!/bin/sh\n", encoding="utf-8"); command.chmod(0o700)
    env = {"PATH": f"{commands}{os.pathsep}{os.environ.get('PATH', '')}"}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema":"mission-specialist-registry/2","specialists_v2":[{"provider_id":"portable-plan-provider","role":"deep-planning","skill":"portable-plan-provider","kind":"command","command":"portable-plan-provider","args":[],"env":{},"task_profiles":["architecture"],"phases":["planning"],"activation":{"min_complexity":"Complex","auto_select_if":["complexity"]},"result_contract":_contract()}]}))
    run_cli("init", "plan import", "--complexity", "Complex", cwd=tmp_path, check=True)
    recommended = run_cli("specialists", "recommend", "--no-default-skill-roots", "--task", "Review architecture", "--registry", str(registry), "--complexity", "Complex", "--record-state", "--json", cwd=tmp_path, env_extra=env)
    assert recommended.returncode == 0, recommended.stderr
    state_file = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_file.read_text())
    state["iteration"] = 1
    state["phase"] = "planning"
    assert state["specialists_candidates"], recommended.stdout
    selected = dict(state["specialists_candidates"][0])
    selected["selection_id"] = state["specialists_decision"]["selection_id"]
    selected["eligibility_selection_source"] = "automatic"
    state["specialists_selected"] = [selected]
    state["specialists_decision"] = {"policy":"provider-primary","action":"select","prompted_user":False,"decision":"selected","selection_id":selected["selection_id"]}
    invocation = "inv_" + "a" * 32; preflight = "pf_test"
    state["specialist_invocations"] = [{"invocation_id":invocation,"iteration":1,"phase":"planning","role":"deep-planning","skill":"portable-plan-provider","mode":"command-provider","status":"completed","lifecycle_state":"terminal","timestamp":"2026-01-01T00:00:00Z"}]
    private = tmp_path / ".mission-state" / "private-preflights"; private.mkdir()
    packet = private / f"{preflight}.json"; packet.write_text("packet")
    outbound = "sha256:" + hashlib.sha256(packet.read_bytes()).hexdigest()
    receipts = tmp_path / ".mission-state" / "private-receipts"; receipts.mkdir()
    receipt = receipts / f"{preflight}.json"; receipt.write_text("receipt")
    state["provider_preflights"] = {preflight:{"invocation_id":invocation,"consumed_invocation_id":invocation,"outbound_packet_digest":outbound,"status":"consumed","artifact_path":str(packet.relative_to(tmp_path / ".mission-state")),"receipt":{"artifact_path":str(receipt.relative_to(tmp_path / ".mission-state")),"digest":"sha256:"+hashlib.sha256(receipt.read_bytes()).hexdigest()}}}
    state_file.write_text(json.dumps(state))
    binding = {"invocation_id":invocation,"preflight_id":preflight,"outbound_packet_digest":outbound,"selection_id":selected["selection_id"],"selection_source":"automatic","iteration":1}
    result = {"schema":"mission-provider-result/1","binding":binding,"capability_attestation":{"requested_class":"deep-planning","effective_class":"deep-planning","requested_variant":"portable-v1","effective_variant":"portable-v1"},"artifacts":[{"schema":"mission-plan/1","document":_document()}]}
    return registry, state_file, result, invocation, env


def test_plan_import_publishes_raw_canonical_and_bound_state(run_cli, tmp_path):
    registry, state_file, result, invocation, env = _setup(run_cli, tmp_path)
    source = tmp_path / "result.json"; source.write_text(json.dumps(result))
    response = run_cli("specialists", "plan-import", "--input", str(source), "--invocation-id", invocation, "--registry", str(registry), "--json", cwd=tmp_path, env_extra=env)
    assert response.returncode == 0, response.stderr
    record = json.loads(response.stdout)["plan_import"]
    state = json.loads(state_file.read_text()); stored = state["provider_plan_imports"][invocation]
    assert stored == record and (tmp_path / record["raw_result_path"]).read_bytes() == source.read_bytes()
    candidate = json.loads((tmp_path / record["candidate_path"]).read_text())
    assert candidate["schema"] == "mission-plan/1" and record["generation"] == 1
    assert candidate["mission_metadata"]["provenance"]["invocation_id"] == invocation
    assert record["candidate_digest"] == "sha256:" + hashlib.sha256((tmp_path / record["candidate_path"]).read_bytes()).hexdigest()


def test_invalid_plan_input_preserves_state_and_no_candidate(run_cli, tmp_path):
    registry, state_file, result, invocation, env = _setup(run_cli, tmp_path)
    result["binding"]["iteration"] = 2
    source = tmp_path / "invalid.json"; source.write_text(json.dumps(result))
    before = state_file.read_bytes()
    response = run_cli("specialists", "plan-import", "--input", str(source), "--invocation-id", invocation, "--registry", str(registry), "--json", cwd=tmp_path, env_extra=env)
    assert response.returncode == 2
    assert state_file.read_bytes() == before
    assert not list((tmp_path / ".mission-state" / "plans").glob("*.json"))


def test_plan_import_state_publish_fault_rolls_back_raw_candidate_and_state(monkeypatch, capsys, run_cli, tmp_path):
    registry, state_file, result, invocation, env = _setup(run_cli, tmp_path)
    source = tmp_path / "result.json"; source.write_text(json.dumps(result))
    spec = importlib.util.spec_from_file_location("mission_state_plan_import_fault", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None; spec.loader.exec_module(module)
    state_before = state_file.read_bytes()
    archive = tmp_path / ".mission-state" / "archive"
    archive_before = {path.relative_to(archive): path.read_bytes() for path in archive.rglob("*") if path.is_file()} if archive.exists() else {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    original_write = module.atomic_write_json
    def fail_state_publish(path, data, **kwargs):
        if path == state_file and data.get("provider_plan_imports"):
            raise OSError("simulated plan import state publish failure")
        return original_write(path, data, **kwargs)
    monkeypatch.setattr(module, "atomic_write_json", fail_state_publish)
    monkeypatch.setattr(sys, "argv", [str(MISSION_STATE_PY), "specialists", "plan-import", "--input", str(source), "--invocation-id", invocation, "--registry", str(registry), "--json"])
    with pytest.raises(SystemExit) as stopped: module.main()
    assert stopped.value.code == 1
    assert state_file.read_bytes() == state_before
    assert not list((tmp_path / ".mission-state" / "plans").glob("*.json"))
    archive_after = {path.relative_to(archive): path.read_bytes() for path in archive.rglob("*") if path.is_file()} if archive.exists() else {}
    assert archive_after == archive_before


def test_uncontracted_exit_zero_is_terminal_but_not_applied_required_evidence():
    spec = importlib.util.spec_from_file_location("mission_state_unvalidated_evidence", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None; spec.loader.exec_module(module)
    status, _ = module._classify_command_provider_result({}, 0, "substantive output", "")
    assert status == "unvalidated-evidence"
    report = candidate_accounting_report({
        "specialists_candidates": [{"skill": "portable-provider", "role": "planning", "required": True}],
        "specialist_invocations": [{"skill": "portable-provider", "status": status}],
    })
    assert report["result_required_unmet_candidates"][0]["skill"] == "portable-provider"


@pytest.mark.parametrize("mutate", [
    lambda state, invocation: state["specialist_invocations"][0].update({"status": "started", "lifecycle_state": "invoked"}),
    lambda state, invocation: state["specialist_invocations"][0].update({"iteration": 0}),
    lambda state, invocation: next(iter(state["provider_preflights"].values())).update({"status": "approved"}),
    lambda state, invocation: next(iter(state["provider_preflights"].values())).pop("receipt"),
    lambda state, invocation: next(iter(state["provider_preflights"].values())).pop("artifact_path"),
])
def test_import_rejects_noncurrent_invocation_or_unproven_consumed_preflight(mutate, run_cli, tmp_path):
    registry, state_file, result, invocation, env = _setup(run_cli, tmp_path)
    state = json.loads(state_file.read_text()); mutate(state, invocation); state_file.write_text(json.dumps(state))
    source = tmp_path / "result.json"; source.write_text(json.dumps(result)); before = state_file.read_bytes()
    response = run_cli("specialists", "plan-import", "--input", str(source), "--invocation-id", invocation, "--registry", str(registry), "--json", cwd=tmp_path, env_extra=env)
    assert response.returncode == 2
    assert state_file.read_bytes() == before
    assert not list((tmp_path / ".mission-state" / "plans").glob("*.json"))
