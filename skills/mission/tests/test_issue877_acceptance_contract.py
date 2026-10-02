"""Acceptance contracts are immutable opt-in evidence, not verification history."""
import json


def _contract(mission_id="41e85745139e4040"):
    text = "Ship the command and do not write outside reports."
    import hashlib
    return {
        "schema": "mission-acceptance-contract/1", "mission_id": mission_id,
        "requirement_text": text,
        "requirement_digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        "revision": 1, "review_policy": "fresh-required",
        "requirements": [
            {"id": "R1", "start": 0, "end": 21, "text": text[:21], "classification": "obligation"},
            {"id": "R2", "start": 21, "end": len(text), "text": text[21:], "classification": "obligation"},
        ],
        "criteria": [
            {"id": "AC1", "requirement_ids": ["R1", "R2"], "expected": "command exists", "required": True,
             "prohibited_side_effects": ["outside reports"], "verification_kind": "command", "target_path": "reports/result.json", "command_id": "project-test"}
        ], "coverage": {"status": "pending"},
    }


def test_import_status_and_legacy_verification_are_separate(tmp_path, run_cli, read_state):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    contract = _contract()
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract))
    result = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert state["acceptance_contract"]["coverage"] == {"status": "pending"}
    assert "verification_history" not in state
    status = run_cli("acceptance-contract", "status", cwd=tmp_path)
    assert json.loads(status.stdout)["present"] is True


def test_rejects_unmapped_obligation_without_writing(tmp_path, run_cli, read_state):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    contract = _contract()
    contract["criteria"][0]["requirement_ids"] = ["R1"]
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract))
    result = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert result.returncode != 0
    assert "acceptance_contract" not in json.loads(run_cli("get", cwd=tmp_path).stdout)


def test_contract_is_not_replaceable(tmp_path, run_cli, read_state):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    source = tmp_path / "contract.json"; source.write_text(json.dumps(_contract()))
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode != 0
