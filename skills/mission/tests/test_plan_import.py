import hashlib
import json
import os


def _contract():
    return {"envelope_schema": "mission-provider-result/1", "artifact_schema": "mission-plan/1", "cardinality": "exactly-one", "required_capability_class": "deep-planning", "require_exact_variant": True}


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
    invocation = "inv_" + "a" * 32; preflight = "pf_test"; outbound = "sha256:" + "b" * 64
    state["specialist_invocations"] = [{"invocation_id":invocation,"iteration":1,"phase":"planning","role":"deep-planning","skill":"portable-plan-provider","mode":"command-provider","status":"completed","lifecycle_state":"terminal","timestamp":"2026-01-01T00:00:00Z"}]
    state["provider_preflights"] = {preflight:{"invocation_id":invocation,"outbound_packet_digest":outbound,"status":"consumed"}}
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
    assert not (tmp_path / ".mission-state" / "plans").exists()
