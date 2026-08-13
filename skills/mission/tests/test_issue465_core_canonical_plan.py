import hashlib
import json

import pytest


def _document(*, objective="bounded core plan"):
    return {
        "objective": objective,
        "scope": {
            "resources": [],
            "actions": [{"type": "analyze", "effect_class": "reversible"}],
        },
        "assumptions": [
            {"id": "assumption-1", "statement": "input is available", "validation": "inspect input"}
        ],
        "steps": [
            {
                "id": "inspect",
                "action": "analyze",
                "inputs": [],
                "outputs": ["findings"],
                "depends_on": [],
                "acceptance_checks": ["findings are recorded"],
                "risk": "low",
                "rollback": "none",
            },
            {
                "id": "summarize",
                "action": "write",
                "inputs": ["findings"],
                "outputs": ["summary"],
                "depends_on": ["inspect"],
                "acceptance_checks": ["summary is complete"],
                "risk": "low",
                "rollback": "remove summary",
            },
        ],
        "global_acceptance": ["all steps complete"],
        "stop_conditions": ["required input is unavailable"],
    }


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _state_file(root):
    return root / ".mission-state" / "sessions" / "test.json"


def _read_state(root):
    return json.loads(_state_file(root).read_text(encoding="utf-8"))


def _write_document(root, document=None, *, name="plan.json"):
    path = root / name
    path.write_bytes(_canonical_bytes(document or _document()))
    return path


def _write_pretty_document(root, document=None, *, name="plan.json"):
    path = root / name
    path.write_text(
        json.dumps(document or _document(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _init_core(run_cli, root, *, complexity="Complex"):
    response = run_cli("init", "core planning", "--complexity", complexity, cwd=root)
    assert response.returncode == 0, response.stderr
    state = _read_state(root)
    state["iteration"] = 1
    state["phase"] = "planning"
    state.pop("planning_strategy", None)
    state.pop("planning_provider_required", None)
    _state_file(root).write_text(json.dumps(state), encoding="utf-8")


def _adopt(run_cli, root, source, *, source_id=None):
    args = ["planning", "adopt-core", "--input", str(source), "--json"]
    if source_id is not None:
        args.extend(["--source-id", source_id])
    return run_cli(*args, cwd=root)


@pytest.mark.parametrize(
    ("complexity", "next_action"),
    [("Standard", "plan-inline"), ("Complex", "run-planner")],
)
def test_next_hint_and_sequence_require_core_adoption_before_execution(
    run_cli, tmp_path, complexity, next_action
):
    _init_core(run_cli, tmp_path, complexity=complexity)

    guidance = json.loads(run_cli("next", cwd=tmp_path).stdout)

    assert guidance["next_action"] == next_action
    assert "planning adopt-core --input <plan.json>" in guidance["command_hint"]
    assert any(
        "planning adopt-core --input <plan.json>" in step
        for step in guidance["command_sequence"]
    )


def test_core_adoption_records_canonical_plan_and_exact_source_binding(run_cli, tmp_path):
    _init_core(run_cli, tmp_path)
    source = _write_document(tmp_path)

    response = _adopt(run_cli, tmp_path, source, source_id="core-fixture")

    assert response.returncode == 0, response.stderr
    state = _read_state(tmp_path)
    plan = state["canonical_plan"]
    assert plan["source"] == "core"
    assert plan["source_id"] == "core-fixture"
    assert plan["source_digest"] == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    assert plan["selection_source"] == "core"
    assert plan["iteration"] == 1
    assert plan["generation"] == 1
    assert set(state["planning_source_records"]["core:core-fixture"]) == {
        "generation", "source", "source_id", "selection_source", "iteration"
    }
    stored = tmp_path / plan["path"]
    assert stored.read_bytes() == _canonical_bytes(json.loads(stored.read_text(encoding="utf-8")))
    assert plan["digest"] == "sha256:" + hashlib.sha256(stored.read_bytes()).hexdigest()


def test_core_adoption_normalizes_canonical_and_pretty_json_to_the_same_plan(
    run_cli, tmp_path
):
    adopted = []
    for name, writer in (("canonical", _write_document), ("pretty", _write_pretty_document)):
        root = tmp_path / name
        root.mkdir()
        _init_core(run_cli, root)
        source = writer(root)

        response = _adopt(run_cli, root, source, source_id="core-fixture")

        assert response.returncode == 0, response.stderr
        plan = _read_state(root)["canonical_plan"]
        stored = root / plan["path"]
        assert stored.read_bytes() == _canonical_bytes(
            json.loads(stored.read_text(encoding="utf-8"))
        )
        adopted.append(plan)

    assert adopted[0]["digest"] == adopted[1]["digest"]
    assert adopted[0]["source_digest"] != adopted[1]["source_digest"]


def test_adopted_core_plan_advances_with_ordered_executor_handoff(run_cli, tmp_path):
    _init_core(run_cli, tmp_path)
    source = _write_document(tmp_path)
    assert _adopt(run_cli, tmp_path, source).returncode == 0

    response = run_cli("advance", "--phase", "executing", cwd=tmp_path)

    assert response.returncode == 0, response.stderr
    handoff = _read_state(tmp_path)["executor_handoff"]
    assert handoff["plan_source"] == "core"
    assert handoff["step_ids"] == ["inspect", "summarize"]


def test_provider_primary_rejects_core_adoption_without_state_change(run_cli, tmp_path):
    _init_core(run_cli, tmp_path)
    state = _read_state(tmp_path)
    state["planning_strategy"] = "provider-primary"
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()

    response = _adopt(run_cli, tmp_path, _write_document(tmp_path))

    assert response.returncode != 0
    assert "planning-strategy-not-core" in response.stderr
    assert _state_file(tmp_path).read_bytes() == before


def test_required_provider_rejects_core_adoption_without_state_change(run_cli, tmp_path):
    _init_core(run_cli, tmp_path)
    state = _read_state(tmp_path)
    state["planning_provider_required"] = True
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()

    response = _adopt(run_cli, tmp_path, _write_document(tmp_path))

    assert response.returncode != 0
    assert "planning-provider-required" in response.stderr
    assert _state_file(tmp_path).read_bytes() == before


def test_non_planning_phase_rejects_core_adoption_without_state_change(
    run_cli, tmp_path
):
    _init_core(run_cli, tmp_path)
    state = _read_state(tmp_path)
    state["phase"] = "executing"
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()

    response = _adopt(run_cli, tmp_path, _write_document(tmp_path))

    assert response.returncode != 0
    assert "planning-policy-not-active" in response.stderr
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("iteration", [None, 0, -1, True, "1"])
def test_invalid_core_iteration_is_rejected_without_state_change(
    run_cli, tmp_path, iteration
):
    _init_core(run_cli, tmp_path)
    state = _read_state(tmp_path)
    state["iteration"] = iteration
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()

    response = _adopt(run_cli, tmp_path, _write_document(tmp_path))

    assert response.returncode != 0
    assert "core-iteration-invalid" in response.stderr
    assert _state_file(tmp_path).read_bytes() == before


def _invalid_document(case):
    document = _document()
    if case == "empty-steps":
        document["steps"] = []
    elif case == "cycle":
        document["steps"][0]["depends_on"] = ["summarize"]
    elif case == "unknown-dependency":
        document["steps"][1]["depends_on"] = ["absent"]
    elif case == "reserved-field":
        document["steps"][0]["mission_metadata"] = {"authority": "caller"}
    elif case == "schema-mismatch":
        # 入力が schema を偽装しても candidate の schema を乗っ取れないこと (#465 review)
        document["schema"] = "evil-plan/1"
    return document


@pytest.mark.parametrize(
    "case",
    [
        "empty-steps",
        "cycle",
        "unknown-dependency",
        "reserved-field",
        "schema-mismatch",
        "invalid-utf8",
        "oversize",
    ],
)
def test_invalid_core_documents_fail_closed_without_state_change(run_cli, tmp_path, case):
    _init_core(run_cli, tmp_path)
    source = tmp_path / f"{case}.json"
    if case == "invalid-utf8":
        source.write_bytes(b"\xff")
    elif case == "oversize":
        source.write_bytes(b"{" + b" " * (4 * 1024 * 1024) + b"}")
    else:
        source.write_bytes(_canonical_bytes(_invalid_document(case)))
    before = _state_file(tmp_path).read_bytes()

    response = _adopt(run_cli, tmp_path, source)

    assert response.returncode != 0
    expected_reason = {
        "empty-steps": "steps-invalid",
        "cycle": "dependency-cycle",
        "unknown-dependency": "unknown-dependency",
        "reserved-field": "mission-authority-field-injection",
        "schema-mismatch": "core-plan-schema-invalid",
        "invalid-utf8": "invalid-utf8",
        "oversize": "result-too-large",
    }[case]
    assert expected_reason in response.stderr
    assert _state_file(tmp_path).read_bytes() == before
    plans = tmp_path / ".mission-state" / "plans"
    assert not plans.exists() or not list(plans.iterdir())


def test_repeated_core_adoption_increments_generation_and_repoints_canonical_plan(run_cli, tmp_path):
    _init_core(run_cli, tmp_path)
    first = _write_document(tmp_path, _document(objective="first"), name="first.json")
    second = _write_document(tmp_path, _document(objective="second"), name="second.json")

    assert _adopt(run_cli, tmp_path, first, source_id="core-fixture").returncode == 0
    first_plan = dict(_read_state(tmp_path)["canonical_plan"])
    assert _adopt(run_cli, tmp_path, second, source_id="core-fixture").returncode == 0

    second_plan = _read_state(tmp_path)["canonical_plan"]
    assert first_plan["generation"] == 1
    assert second_plan["generation"] == 2
    assert second_plan["path"] != first_plan["path"]
    assert second_plan["digest"] != first_plan["digest"]


def test_next_guides_core_adoption_then_returns_executor_after_adoption(run_cli, tmp_path):
    _init_core(run_cli, tmp_path)

    before = json.loads(run_cli("next", cwd=tmp_path).stdout)
    rejected = run_cli("advance", "--phase", "executing", cwd=tmp_path)

    assert any("planning adopt-core --input <plan.json>" in step for step in before["command_sequence"])
    assert "planning adopt-core --input <plan.json>" in rejected.stderr
    assert "planning reselect" not in rejected.stderr
    source = _write_document(tmp_path)
    assert _adopt(run_cli, tmp_path, source).returncode == 0
    after = json.loads(run_cli("next", cwd=tmp_path).stdout)
    assert after["next_action"] == "run-executor"


def test_tampered_core_plan_is_rejected_by_advance_digest_gate(run_cli, tmp_path):
    _init_core(run_cli, tmp_path)
    source = _write_document(tmp_path)
    assert _adopt(run_cli, tmp_path, source).returncode == 0
    state = _read_state(tmp_path)
    canonical = tmp_path / state["canonical_plan"]["path"]
    canonical.write_bytes(canonical.read_bytes() + b" ")

    response = run_cli("advance", "--phase", "executing", cwd=tmp_path)

    assert response.returncode != 0
    assert "canonical-plan-digest-drift" in response.stderr
    assert _read_state(tmp_path)["phase"] == "planning"


@pytest.mark.parametrize("source_id", ["", "x" * 129, "invalid/source"])
def test_invalid_core_source_id_is_rejected_without_state_change(run_cli, tmp_path, source_id):
    _init_core(run_cli, tmp_path)
    before = _state_file(tmp_path).read_bytes()

    response = _adopt(run_cli, tmp_path, _write_document(tmp_path), source_id=source_id)

    assert response.returncode != 0
    assert "core-source-id-invalid" in response.stderr
    assert _state_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("generation", [True, 0, -1, 1.5, "1"])
def test_invalid_existing_core_generation_fails_closed(run_cli, tmp_path, generation):
    _init_core(run_cli, tmp_path)
    state = _read_state(tmp_path)
    state["planning_source_records"] = {
        "core:core-fixture": {
            "generation": generation,
            "source": "core",
            "source_id": "core-fixture",
            "selection_source": "core",
            "iteration": 1,
        }
    }
    _state_file(tmp_path).write_text(json.dumps(state), encoding="utf-8")
    before = _state_file(tmp_path).read_bytes()

    response = _adopt(
        run_cli,
        tmp_path,
        _write_document(tmp_path),
        source_id="core-fixture",
    )

    assert response.returncode != 0
    assert "core-source-generation-invalid" in response.stderr
    assert _state_file(tmp_path).read_bytes() == before
