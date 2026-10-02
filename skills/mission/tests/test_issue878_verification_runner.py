"""#878: registered verification policies bind acceptance imports to execution."""
import hashlib
import json
import subprocess


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


def test_public_runner_records_process_bound_receipt(tmp_path, run_cli):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("candidate", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    run_cli("init", "verification runner", "--force-mission", cwd=tmp_path, check=True)
    policy_dir = tmp_path / ".mission"; policy_dir.mkdir()
    encoded = json.dumps(_policy(), sort_keys=True, separators=(",", ":")).encode()
    (policy_dir / "verifiers.json").write_bytes(encoded)
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0

    result = run_cli("verification", "run", "--criterion", "AC1", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)["receipt"]
    assert receipt["schema"] == "mission-verification-receipt/1"
    assert receipt["status"] == "passed"
    assert receipt["argv"] == _policy()["commands"][0]["argv"]
    stored = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert stored["verification_receipts"][-1] == receipt
