"""#878: registered verification policies bind acceptance imports to execution."""
import hashlib
import json


def _policy():
    return {
        "schema": "mission-verifier-policy/1",
        "commands": [{
            "id": "project-test", "argv": ["python3", "-c", "print('ok')"],
            "relative_cwd": ".", "timeout_sec": 5, "output_limit": 4096,
            "kind": "command", "env": {}, "declared_untracked": [],
        }],
    }


def _contract(mission_id):
    text = "Run the registered verifier."
    return {
        "schema": "mission-acceptance-contract/2", "mission_id": mission_id,
        "requirement_text": text,
        "requirement_digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        "revision": 1, "review_policy": "fresh-required",
        "requirements": [{"id": "R1", "start": 0, "end": len(text), "text": text, "classification": "obligation"}],
        "criteria": [{"id": "AC1", "requirement_ids": ["R1"], "expected": "registered verifier ran", "required": True,
                      "prohibited_side_effects": [], "verification_kind": "command", "target_path": "reports/result.json", "command_id": "project-test"}],
        "coverage": {"status": "pending"}, "verifier_policy_digest": "",
    }


def test_import_freezes_registered_project_verifier_policy(tmp_path, run_cli):
    run_cli("init", "verification runner", "--force-mission", cwd=tmp_path, check=True)
    policy = _policy()
    policy_dir = tmp_path / ".mission"; policy_dir.mkdir()
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    (policy_dir / "verifiers.json").write_bytes(encoded)
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")

    imported = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)

    assert imported.returncode == 0, imported.stderr
    stored = json.loads(run_cli("get", cwd=tmp_path).stdout)["acceptance_contract"]
    assert stored["verifier_policy"]["digest"] == contract["verifier_policy_digest"]
    assert stored["verifier_policy"]["commands"]["project-test"]["argv"] == policy["commands"][0]["argv"]
