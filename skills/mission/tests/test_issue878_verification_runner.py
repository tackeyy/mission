"""#878: registered verification policies bind acceptance imports to execution."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _policy():
    executable = Path(sys.executable).resolve()
    return {
        "schema": "mission-verifier-policy/2",
        "commands": [{
            "id": "project-test", "argv": [str(executable), "-c", "print('ok')"],
            "relative_cwd": ".", "timeout_sec": 5, "output_limit": 4096,
            "kind": "command", "env": {}, "declared_untracked": [],
            "toolchain": {"path": str(executable), "digest": "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest()},
            "external_inputs": [],
        }],
    }


def _replay_policy():
    policy = _policy()
    policy["commands"][0]["replay"] = {
        "command_id": "replay-test", "max_bytes": 64,
        "allowed_artifact_kinds": ["counterexample"], "relative_path": "repro.json",
    }
    policy["commands"].append({
        "id": "replay-test",
        "argv": _policy()["commands"][0]["argv"][:1] + ["-c", "from pathlib import Path; assert Path('repro.json').read_text() == 'proof'"],
        "relative_cwd": ".", "timeout_sec": 5, "output_limit": 4096,
        "kind": "command", "env": {}, "declared_untracked": [],
        "toolchain": _policy()["commands"][0]["toolchain"], "external_inputs": [],
    })
    return policy


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


def test_legacy_verifier_policy_schema_is_explicitly_unsupported():
    import pytest
    from mission_application.verifier_policy import VerifierPolicyError, validate

    with pytest.raises(VerifierPolicyError, match="verifier-policy-unsupported"):
        validate({"schema": "mission-verifier-policy/1", "commands": []})


def test_policy_rejects_unbounded_test_count_pattern_before_process_execution():
    import pytest
    from mission_application.verifier_policy import VerifierPolicyError, validate

    policy = _policy()
    policy["commands"][0]["kind"] = "test"
    policy["commands"][0]["executed_count_pattern"] = "("

    with pytest.raises(VerifierPolicyError, match="verifier-policy-test-adapter-invalid"):
        validate(policy)


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
    imported = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert imported.returncode == 0

    result = run_cli("verification", "run", "--criterion", "AC1", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)["receipt"]
    assert receipt["schema"] == "mission-verification-receipt/1"
    assert receipt["status"] == "passed"
    assert receipt["argv"] == _policy()["commands"][0]["argv"]
    import_digest = json.loads(imported.stdout)["acceptance_contract"]["digest"]
    status_digest = json.loads(run_cli("acceptance-contract", "status", cwd=tmp_path).stdout)["digest"]
    assert import_digest == status_digest == receipt["contract_digest"]
    stored = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert stored["verification_receipts"][-1] == receipt


def test_public_runner_binds_replay_input_to_frozen_replay_command_and_receipt(tmp_path, run_cli):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("candidate", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    run_cli("init", "verification runner", "--force-mission", cwd=tmp_path, check=True)
    policy_dir = tmp_path / ".mission"; policy_dir.mkdir()
    policy = _replay_policy()
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    (policy_dir / "verifiers.json").write_bytes(encoded)
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0
    repro = tmp_path / "repro.json"
    repro.write_text(json.dumps({"artifact_kind": "counterexample", "content": "proof"}), encoding="utf-8")

    result = run_cli("verification", "run", "--criterion", "AC1", "--repro-input", str(repro), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)["receipt"]
    assert receipt["status"] == "passed"
    assert receipt["argv"] == policy["commands"][1]["argv"]
    assert receipt["repro_input_digest"] == "sha256:" + hashlib.sha256(b"repro.json\0proof").hexdigest()
    assert receipt["block_reason"] is None

    repro.write_text(json.dumps({"artifact_kind": "counterexample", "content": "x" * 65}), encoding="utf-8")
    rejected = run_cli("verification", "run", "--criterion", "AC1", "--repro-input", str(repro), cwd=tmp_path)
    assert rejected.returncode == 0, rejected.stderr
    assert json.loads(rejected.stdout)["receipt"]["status"] == "blocked"
    assert json.loads(rejected.stdout)["receipt"]["block_reason"] == "replay-input-invalid"


def test_public_runner_blocks_replay_path_prefix_collision(tmp_path, run_cli):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("candidate", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    run_cli("init", "verification runner", "--force-mission", cwd=tmp_path, check=True)
    policy_dir = tmp_path / ".mission"; policy_dir.mkdir()
    policy = _replay_policy()
    policy["commands"][0]["replay"]["relative_path"] = "tracked.txt/repro.json"
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    (policy_dir / "verifiers.json").write_bytes(encoded)
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0
    repro = tmp_path / "repro.json"
    repro.write_text(json.dumps({"artifact_kind": "counterexample", "content": "proof"}), encoding="utf-8")

    result = run_cli("verification", "run", "--criterion", "AC1", "--repro-input", str(repro), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)["receipt"]
    assert receipt["status"] == "blocked"
    assert receipt["block_reason"] == "replay-input-path-conflict"


def test_receipt_record_refuses_to_overwrite_a_non_history_value():
    import pytest
    from mission_kernel.commands import RecordVerificationReceipt
    from mission_kernel.evidence import EvidenceRuleError, apply_verification_receipt
    from mission_kernel.json_codec import freeze_json_value

    digest = "sha256:" + "0" * 64
    receipt = {
        "schema": "mission-verification-receipt/1", "contract_digest": digest,
        "criterion_id": "AC1", "candidate_digest": digest,
        "verifier_policy_digest": digest, "verifier_definition_digest": digest,
        "argv": ["true"], "relative_cwd": ".", "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:00Z", "exit_code": 0, "timed_out": False,
        "executed_count": None, "output_digest": digest, "status": "passed",
        "runner_provenance": "mission-public-cli/1", "repro_input_digest": None,
        "block_reason": None,
    }

    with pytest.raises(EvidenceRuleError, match="verification-receipt-history-invalid"):
        apply_verification_receipt(
            {"verification_receipts": {"caller": "declared"}},
            RecordVerificationReceipt("2026-01-01T00:00:00Z", freeze_json_value(receipt)),
        )


def test_public_runner_rejects_live_policy_drift_before_creating_a_receipt(tmp_path, run_cli):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("candidate", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    run_cli("init", "verification runner", "--force-mission", cwd=tmp_path, check=True)
    policy_dir = tmp_path / ".mission"; policy_dir.mkdir()
    encoded = json.dumps(_policy(), sort_keys=True, separators=(",", ":")).encode()
    policy_path = policy_dir / "verifiers.json"; policy_path.write_bytes(encoded)
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0
    policy_path.write_bytes(encoded + b"\n")

    result = run_cli("verification", "run", "--criterion", "AC1", cwd=tmp_path)

    assert result.returncode != 0
    assert "verifier-policy-stale" in result.stderr
    assert "verification_receipts" not in json.loads(run_cli("get", cwd=tmp_path).stdout)


def test_public_runner_records_blocked_receipt_when_registered_binary_is_unavailable(tmp_path, run_cli):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("candidate", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    run_cli("init", "verification runner", "--force-mission", cwd=tmp_path, check=True)
    policy = _policy(); policy["commands"][0]["argv"] = ["/missing/mission-neutral-binary"]
    policy["commands"][0]["toolchain"] = {"path": "/missing/mission-neutral-binary", "digest": "sha256:" + "0" * 64}
    policy_dir = tmp_path / ".mission"; policy_dir.mkdir()
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    (policy_dir / "verifiers.json").write_bytes(encoded)
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0

    result = run_cli("verification", "run", "--criterion", "AC1", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)["receipt"]
    assert receipt["status"] == "blocked"
    assert receipt["block_reason"] == "toolchain-stale"
    assert receipt["exit_code"] is None and receipt["timed_out"] is False


def test_same_operation_retry_runs_the_verifier_again_and_rejects_a_different_receipt(tmp_path, run_cli):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.txt").write_text("candidate", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    run_cli("init", "verification runner", "--force-mission", cwd=tmp_path, check=True)
    counter = tmp_path / "process-count.txt"
    policy = _policy()
    policy["commands"][0]["argv"] = [policy["commands"][0]["toolchain"]["path"], "-c", f"from pathlib import Path; p=Path({str(counter)!r}); n=int(p.read_text() if p.exists() else '0') + 1; p.write_text(str(n)); print(n)"]
    policy_dir = tmp_path / ".mission"; policy_dir.mkdir()
    encoded = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    (policy_dir / "verifiers.json").write_bytes(encoded)
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    contract = _contract(state["mission_id"])
    contract["verifier_policy_digest"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0
    retry_env = {"MISSION_OPERATION_ID": "receipt-retry"}

    first = run_cli("verification", "run", "--criterion", "AC1", cwd=tmp_path, env_extra=retry_env)
    second = run_cli("verification", "run", "--criterion", "AC1", cwd=tmp_path, env_extra=retry_env)

    assert first.returncode == 0, first.stderr
    assert second.returncode != 0
    assert "verification-receipt-projection-mismatch" in second.stderr
    assert counter.read_text(encoding="utf-8") == "2"
    assert len(json.loads(run_cli("get", cwd=tmp_path).stdout)["verification_receipts"]) == 1
