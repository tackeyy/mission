"""Acceptance contracts are immutable opt-in evidence, not verification history."""
import json
import pytest


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
    rendered = json.loads(status.stdout)
    assert rendered["present"] is True
    assert rendered["requirement_text"] == contract["requirement_text"]
    assert rendered["requirements"] == contract["requirements"]
    assert rendered["criteria"] == contract["criteria"]


def test_preserves_unmapped_obligation_as_pending(tmp_path, run_cli, read_state):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    contract = _contract()
    contract["criteria"][0]["requirement_ids"] = ["R1"]
    contract["criteria"][0]["required"] = False
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract))
    result = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    state = json.loads(run_cli("get", cwd=tmp_path).stdout)
    assert state["acceptance_contract"]["requirements"] == contract["requirements"]
    assert state["acceptance_contract"]["criteria"] == contract["criteria"]
    assert state["acceptance_contract"]["coverage"] == {"status": "pending"}


def test_contract_is_not_replaceable(tmp_path, run_cli, read_state):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    source = tmp_path / "contract.json"; source.write_text(json.dumps(_contract()))
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode != 0


def test_rejects_lone_surrogate_requirement_text_without_writing(tmp_path, run_cli):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    contract = _contract()
    contract["requirement_text"] = "invalid\ud800"
    source = tmp_path / "contract.json"
    source.write_text(json.dumps(contract, ensure_ascii=True), encoding="utf-8")
    result = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert result.returncode != 0
    assert "internal-error" not in result.stdout
    assert "acceptance_contract" not in json.loads(run_cli("get", cwd=tmp_path).stdout)


@pytest.mark.parametrize("invalid", [[], {}, True, 1])
def test_rejects_non_string_requirement_classification_without_writing(tmp_path, run_cli, invalid):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    contract = _contract()
    contract["requirements"][0]["classification"] = invalid
    source = tmp_path / "contract.json"
    source.write_text(json.dumps(contract), encoding="utf-8")
    result = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert result.returncode != 0
    assert "internal-error" not in result.stdout
    assert "acceptance_contract" not in json.loads(run_cli("get", cwd=tmp_path).stdout)


def test_kernel_refuses_a_contract_for_another_mission():
    from acceptance_contract import validate
    from mission_kernel.commands import ImportAcceptanceContract
    from mission_kernel.evidence import EvidenceRuleError, apply_acceptance_contract
    from mission_kernel.json_codec import freeze_json_value

    contract = _contract(mission_id="foreign")
    with pytest.raises(EvidenceRuleError, match="acceptance-contract-mission-mismatch"):
        apply_acceptance_contract(
            {"mission_id": "local"},
            ImportAcceptanceContract("2026-10-03T00:00:00Z", freeze_json_value(validate(contract))),
        )


def test_public_schema_explains_ledger_criterion_and_policy_binding(tmp_path, run_cli):
    result = run_cli("schema", "--contract", "acceptance-contract-import", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    schema = json.loads(result.stdout)
    assert "requirements[].classification" in schema["enums"]
    assert "criteria[].command_id" in schema["fields"]
    assert "coverage pending" in schema["fields"]["criteria[]"]


@pytest.mark.parametrize("path", [".", "./out", "a//out", "reports/./out", "..\\secret", "C:\\secret"])
def test_rejects_noncanonical_or_windows_target_path_without_writing(tmp_path, run_cli, path):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    contract = _contract(); contract["criteria"][0]["target_path"] = path
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    result = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert result.returncode != 0
    assert "acceptance_contract" not in json.loads(run_cli("get", cwd=tmp_path).stdout)


def test_accepts_canonical_unicode_space_and_hidden_target_path(tmp_path, run_cli):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    contract = _contract(); contract["criteria"][0]["target_path"] = "成果物/.hidden file.json"
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0


def test_codepoint_spans_accept_non_bmp_and_combining_text(tmp_path, run_cli):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    text = "😀e\u0301"; contract = _contract(); contract["requirement_text"] = text
    contract["requirement_digest"] = "sha256:" + __import__("hashlib").sha256(text.encode()).hexdigest()
    contract["requirements"] = [{"id": "R1", "start": 0, "end": 1, "text": "😀", "classification": "obligation"}, {"id": "R2", "start": 1, "end": 3, "text": "e\u0301", "classification": "context"}]
    contract["criteria"][0]["requirement_ids"] = ["R1"]
    source = tmp_path / "contract.json"; source.write_text(json.dumps(contract), encoding="utf-8")
    assert run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path).returncode == 0


def test_rejects_5000_digit_json_number_as_controlled_input(tmp_path, run_cli):
    run_cli("init", "acceptance contract", "--force-mission", cwd=tmp_path, check=True)
    raw = json.dumps(_contract()).replace('"revision": 1', '"revision": ' + '9' * 5000)
    source = tmp_path / "contract.json"; source.write_text(raw, encoding="utf-8")
    result = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path)
    assert result.returncode != 0
    assert "internal-error" not in result.stdout
