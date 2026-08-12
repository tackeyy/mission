import json
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from plan_contract import PlanContractError, canonical_plan_bytes, parse_provider_result  # noqa: E402


def _result(*, document=None):
    document = document or {
        "objective": "Create a bounded plan",
        "scope": {
            "resources": [{"type": "path", "identifier": "docs/new.md", "access": "write", "constraints": []}],
            "actions": [{"type": "write", "effect_class": "reversible"}],
        },
        "assumptions": [{"id": "a1", "statement": "input is available", "validation": "inspect it"}],
        "steps": [{"id": "s1", "action": "write", "inputs": [], "outputs": [], "depends_on": [], "acceptance_checks": ["file exists"], "risk": "low", "rollback": "remove file"}],
        "global_acceptance": ["plan is bounded"],
        "stop_conditions": ["required input unavailable"],
    }
    return {
        "schema": "mission-provider-result/1",
        "binding": {"invocation_id": "inv_" + "a" * 32, "preflight_id": "pf_1", "outbound_packet_digest": "sha256:" + "b" * 64, "selection_id": "sel_1", "selection_source": "automatic", "iteration": 1},
        "capability_attestation": {"requested_class": "deep-planning", "effective_class": "deep-planning", "requested_variant": "portable-v1", "effective_variant": "portable-v1"},
        "artifacts": [{"schema": "mission-plan/1", "document": document}],
    }


def test_minimal_envelope_accepts_exactly_one_bounded_plan():
    result = parse_provider_result(
        json.dumps(_result()).encode(),
        expected_binding=_result()["binding"],
        result_contract={"envelope_schema": "mission-provider-result/1", "artifact_schema": "mission-plan/1", "cardinality": "exactly-one", "required_capability_class": "deep-planning", "require_exact_variant": True},
        workspace=Path.cwd(),
    )
    assert result["document"]["objective"] == "Create a bounded plan"


@pytest.mark.parametrize("mutate", [
    lambda value: value.update({"artifacts": []}),
    lambda value: value.update({"artifacts": value["artifacts"] * 2}),
    lambda value: value.update({"artifacts": [*value["artifacts"], {"schema": "other/1", "document": {}}]}),
])
def test_artifact_cardinality_is_fail_closed(mutate):
    value = _result(); mutate(value)
    with pytest.raises(PlanContractError, match="artifact-cardinality"):
        parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={"required_capability_class": "deep-planning", "require_exact_variant": True}, workspace=Path.cwd())


def test_reserved_mission_authority_is_rejected():
    value = _result(); value["artifacts"][0]["document"]["mission_metadata"] = {}
    with pytest.raises(PlanContractError, match="mission-authority-field-injection"):
        parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={"required_capability_class": "deep-planning", "require_exact_variant": True}, workspace=Path.cwd())


def test_canonical_bytes_sort_keys_but_keep_array_order():
    left = {"b": 1, "a": ["first", "second"]}
    same = {"a": ["first", "second"], "b": 1}
    changed = {"a": ["second", "first"], "b": 1}
    assert canonical_plan_bytes(left) == canonical_plan_bytes(same)
    assert canonical_plan_bytes(left) != canonical_plan_bytes(changed)


@pytest.mark.parametrize("mutate", [
    lambda d: d["scope"]["resources"][0].pop("constraints"),
    lambda d: d["scope"]["actions"][0].update({"type": "unknown"}),
    lambda d: d["steps"][0].pop("inputs"),
    lambda d: d["steps"][0].pop("rollback"),
])
def test_typed_scope_and_complete_step_fields_are_required(mutate):
    value = _result(); mutate(value["artifacts"][0]["document"])
    with pytest.raises(PlanContractError):
        parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={"required_capability_class": "deep-planning", "require_exact_variant": True}, workspace=Path.cwd())


@pytest.mark.parametrize("raw", [
    b'{"schema":"mission-provider-result/1","schema":"mission-provider-result/1"}',
    b'{"schema":NaN}', b'\xff\xfe', b"{" + b" " * (4 * 1024 * 1024) + b"}",
])
def test_hostile_json_is_rejected(raw):
    with pytest.raises(PlanContractError):
        parse_provider_result(raw, expected_binding=_result()["binding"], result_contract={}, workspace=Path.cwd())


@pytest.mark.parametrize("mutate", [
    lambda d: d["steps"][0].update({"id": "s2"}) or d["steps"].append({**d["steps"][0], "id": "s2"}),
    lambda d: d["steps"][0].update({"depends_on": ["missing"]}),
    lambda d: d["steps"][0].update({"depends_on": ["s2"]}) or d["steps"].append({**d["steps"][0], "id": "s2", "depends_on": ["s1"]}),
    lambda d: d["steps"][0].update({"acceptance_checks": []}),
])
def test_dag_and_observable_acceptance_are_required(mutate):
    value = _result(); mutate(value["artifacts"][0]["document"])
    with pytest.raises(PlanContractError):
        parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={}, workspace=Path.cwd())


@pytest.mark.parametrize("identifier", ["/absolute", "../escape", "bad\x00path"])
def test_unsafe_paths_are_rejected(identifier):
    value = _result(); value["artifacts"][0]["document"]["scope"]["resources"][0]["identifier"] = identifier
    with pytest.raises(PlanContractError):
        parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={}, workspace=Path.cwd())


@pytest.mark.parametrize("field", ["passes", "score", "phase", "state_path", "authority", "provenance", "mission_metadata", "selection_verified"])
def test_nested_mission_control_fields_are_rejected(field):
    value = _result(); value["artifacts"][0]["document"]["steps"][0][field] = True
    with pytest.raises(PlanContractError, match="mission-authority-field-injection"):
        parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={}, workspace=Path.cwd())


def test_symlink_component_is_rejected_even_when_it_resolves_inside(tmp_path):
    (tmp_path / "real").mkdir(); (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)
    value = _result(); value["artifacts"][0]["document"]["scope"]["resources"][0]["identifier"] = "link/file.md"
    with pytest.raises(PlanContractError, match="path-symlink-escape"):
        parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={}, workspace=tmp_path)


@pytest.mark.parametrize("resource", [
    {"type": "uri", "identifier": "https://example.test/input", "access": "read", "constraints": []},
    {"type": "record", "identifier": "record-42", "access": "read", "constraints": []},
    {"type": "dataset", "identifier": "dataset-42", "access": "read", "constraints": []},
    {"type": "other", "identifier": "opaque-resource-42", "access": "read", "constraints": []},
])
def test_typed_non_file_resources_are_valid(resource):
    value = _result(); value["artifacts"][0]["document"]["scope"]["resources"] = [resource]
    parsed = parse_provider_result(json.dumps(value).encode(), expected_binding=value["binding"], result_contract={}, workspace=Path.cwd())
    assert parsed["document"]["scope"]["resources"] == [resource]
