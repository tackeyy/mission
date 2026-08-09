"""Issue #394: complexity-aware planning provider eligibility."""

import json
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from provider_eligibility import (  # noqa: E402
    RegistryContractError,
    detect_registry_version,
    evaluate_provider_eligibility,
    normalize_selection_source,
    parse_v2_registry,
)


def _load_mission_state_module(name: str):
    state_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, state_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _project_identity(path: Path, project: Path) -> str:
    return f"$PROJECT/{path.resolve().relative_to(project.resolve()).as_posix()}"


def test_complexity_trigger_is_eligible_without_profile_match():
    candidate = {
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
        "activation": {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
            "explicit_below_min": "deny",
        },
        "available": True,
    }
    context = {
        "complexity": "Complex",
        "task_profile": {"primary": "general", "secondary": [], "confidence": 0.3},
        "iteration": 1,
        "previous_iteration_passed": None,
    }

    result = evaluate_provider_eligibility(
        candidate,
        context,
        requested_phase="planning",
        selection_source="automatic",
    )

    assert result["eligible"] is True
    assert result["reason_code"] == "eligible-complexity"
    assert result["matched_conditions"] == ["complexity"]


@pytest.mark.parametrize(
    ("complexity", "reason_code"),
    [
        ("Simple", "below-min-complexity"),
        ("Standard", "below-min-complexity"),
        ("Unknown", "unknown-complexity"),
        (None, "unknown-complexity"),
        ("complex", "unknown-complexity"),
    ],
)
def test_complexity_floor_is_a_hard_gate_for_explicit_selection(complexity, reason_code):
    candidate = {
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
        "activation": {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
            "explicit_below_min": "deny",
        },
        "available": True,
    }
    result = evaluate_provider_eligibility(
        candidate,
        {
            "complexity": complexity,
            "task_profile": {"primary": "architecture", "secondary": [], "confidence": 0.9},
            "iteration": 1,
            "previous_iteration_passed": None,
        },
        requested_phase="planning",
        selection_source="user-instruction",
    )

    assert result["eligible"] is False
    assert result["reason_code"] == reason_code


def test_profile_trigger_remains_the_default():
    result = evaluate_provider_eligibility(
        {
            "skill": "documentation-provider",
            "task_profiles": ["documentation"],
            "phases": ["planning", "review"],
            "available": True,
        },
        {
            "complexity": "Standard",
            "task_profile": {"primary": "documentation", "secondary": [], "confidence": 0.8},
            "iteration": 1,
            "previous_iteration_passed": None,
        },
        requested_phase="planning",
        selection_source="automatic",
    )

    assert result["eligible"] is True
    assert result["reason_code"] == "eligible-profile"


@pytest.mark.parametrize(
    ("when_any", "profile", "iteration", "previous_passed", "eligible"),
    [
        (["architecture", "stalled_iteration"], "architecture", 1, None, True),
        (["architecture", "stalled_iteration"], "general", 2, False, True),
        (["architecture", "stalled_iteration"], "general", 2, True, False),
        ([], "architecture", 2, False, False),
    ],
)
def test_when_any_is_or_internally_and_a_gate_for_automatic_selection(
    when_any, profile, iteration, previous_passed, eligible
):
    result = evaluate_provider_eligibility(
        {
            "skill": "deep-planning-provider",
            "task_profiles": ["architecture"],
            "phases": ["planning"],
            "activation": {
                "min_complexity": "Complex",
                "auto_select_if": ["complexity"],
                "when_any": when_any,
                "explicit_below_min": "deny",
            },
            "available": True,
        },
        {
            "complexity": "Complex",
            "task_profile": {"primary": profile, "secondary": [], "confidence": 0.3},
            "iteration": iteration,
            "previous_iteration_passed": previous_passed,
        },
        requested_phase="planning",
        selection_source="automatic",
    )

    assert result["eligible"] is eligible
    if not eligible:
        assert result["reason_code"] == "activation-predicate-not-matched"


@pytest.mark.parametrize(
    ("candidate", "reason_code"),
    [
        (
            {
                "skill": "deep-planning-provider",
                "task_profiles": ["architecture"],
                "phases": ["review"],
                "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
                "available": True,
            },
            "phase-not-allowed",
        ),
        (
            {
                "skill": "deep-planning-provider",
                "task_profiles": ["architecture"],
                "phases": [],
                "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
                "available": True,
            },
            "invalid-phase-allow-list",
        ),
        (
            {
                "skill": "deep-planning-provider",
                "task_profiles": ["architecture"],
                "phases": ["planning"],
                "activation": {
                    "min_complexity": "Complex",
                    "auto_select_if": ["complexity"],
                    "when_any": ["unknown-predicate"],
                },
                "available": True,
            },
            "unsupported-activation-predicate",
        ),
        (
            {
                "skill": "deep-planning-provider",
                "task_profiles": ["architecture"],
                "phases": ["planning"],
                "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
                "auto_use": {"min_complexity": "Complex"},
                "available": True,
            },
            "conflicting-activation-config",
        ),
    ],
)
def test_invalid_activation_and_phase_contracts_fail_closed(candidate, reason_code):
    result = evaluate_provider_eligibility(
        candidate,
        {
            "complexity": "Complex",
            "task_profile": {"primary": "architecture", "secondary": [], "confidence": 0.9},
            "iteration": 1,
            "previous_iteration_passed": None,
        },
        requested_phase="planning",
        selection_source="automatic",
    )

    assert result["eligible"] is False
    assert result["reason_code"] == reason_code


def test_legacy_auto_use_is_normalized_without_being_ignored():
    result = evaluate_provider_eligibility(
        {
            "skill": "deep-planning-provider",
            "task_profiles": ["architecture"],
            "phases": ["planning"],
            "auto_use": {
                "min_complexity": "Complex",
                "when": ["architecture", "stalled_iteration"],
            },
            "available": True,
        },
        {
            "complexity": "Complex",
            "task_profile": {"primary": "architecture", "secondary": [], "confidence": 0.9},
            "iteration": 1,
            "previous_iteration_passed": None,
        },
        requested_phase="planning",
        selection_source="automatic",
    )

    assert result["eligible"] is True
    assert result["normalized_activation"]["min_complexity"] == "Complex"
    assert result["normalized_activation"]["when_any"] == ["architecture", "stalled_iteration"]


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("automatic", "automatic"),
        ("confirmed-user", "confirmed-user"),
        ("user-instruction", "user-instruction"),
        ("manual", "manual"),
        ("task-required", "task-required"),
        ("user-specified", "user-instruction"),
        ("auto", "automatic"),
    ],
)
def test_selection_source_has_one_canonical_mapping(raw, canonical):
    assert normalize_selection_source(raw) == {
        "selection_source": canonical,
        "selection_source_raw": raw,
    }


@pytest.mark.parametrize("raw", [None, "", "other"])
def test_missing_or_unknown_selection_source_fails_closed(raw):
    with pytest.raises(ValueError):
        normalize_selection_source(raw)


def test_v2_complexity_trigger_selects_general_low_confidence_task(run_cli, tmp_path):
    registry = tmp_path / "specialists-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                            "explicit_below_min": "deny",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Coordinate a multi-step effort with unclear domain signals",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--installed-skills",
        "deep-planning-provider",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    selected = data["specialists_selected"][0]
    assert data["task_profile"]["confidence"] < 0.5
    assert data["specialists_decision"]["policy"] == "auto"
    assert selected["selection_source"] == "automatic"
    assert selected["selection_source_raw"] == "automatic"
    assert selected["provider_id"] == "deep-planning-provider"
    assert selected["registry_entry_digest"].startswith("sha256:")
    assert selected["activation_digest"].startswith("sha256:")
    assert selected["context_digest"].startswith("sha256:")


def test_record_state_rejects_cli_complexity_that_disagrees_with_authoritative_state(
    run_cli, state_dir
):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    preserved = {
        "specialists_candidates": [{"skill": "existing-candidate"}],
        "specialists_selected": [{"skill": "existing-selection"}],
        "specialists_unavailable": [{"skill": "existing-unavailable"}],
        "specialists_ineligible": [{"provider_id": "existing-ineligible"}],
        "specialist_registry_projection": {"schema": "existing-projection"},
        "specialists_decision": {"policy": "existing-decision"},
        "specialists_phase_plan": [{"phase": "existing-phase"}],
        "specialists_mode": "existing-mode",
    }
    state.update({"complexity": "Simple", "iteration": 1, **preserved})
    state_path.write_text(json.dumps(state), encoding="utf-8")
    registry = state_dir.parent / "specialists-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Coordinate a multi-step effort", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider",
        "--record-state", "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["reason_code"] == "state-context-mismatch"
    assert output["specialists_candidates"] == []
    assert output["specialists_selected"] == []
    assert output["specialists_phase_plan"] == []
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert {key: after[key] for key in preserved} == preserved
    assert after["updated_at"] == state["updated_at"]


def test_record_state_rechecks_complexity_and_iteration_inside_write_lock(
    monkeypatch, capsys, state_dir
):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "complexity": "Complex",
            "iteration": 1,
            "specialists_selected": [{"skill": "existing-selection"}],
            "specialists_phase_plan": [{"phase": "existing-phase"}],
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")
    registry = state_dir.parent / "specialists-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    module_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_issue394_toc", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    original_rank = module.rank_specialist_candidates

    def rank_then_drift(*args, **kwargs):
        ranked = original_rank(*args, **kwargs)
        drifted = json.loads(state_path.read_text(encoding="utf-8"))
        drifted["iteration"] = 2
        state_path.write_text(json.dumps(drifted), encoding="utf-8")
        return ranked

    monkeypatch.setattr(module, "rank_specialist_candidates", rank_then_drift)
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    args = SimpleNamespace(
        task="Coordinate a multi-step effort",
        files=None,
        registry=[str(registry)],
        skills_dir=None,
        no_default_skill_roots=True,
        installed_skills="deep-planning-provider",
        first_use=None,
        consent_file=None,
        complexity="Complex",
        record_state=True,
        user_specified=None,
        json=True,
    )

    with pytest.raises(SystemExit) as raised:
        module.cmd_specialists(args)

    assert raised.value.code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["reason_code"] == "state-context-mismatch"
    assert output["specialists_selected"] == []
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["iteration"] == 2
    assert after["specialists_selected"] == [{"skill": "existing-selection"}]
    assert after["specialists_phase_plan"] == [{"phase": "existing-phase"}]


def test_v2_json_and_yaml_normalize_to_the_same_registry_entry(run_cli, tmp_path):
    json_registry = tmp_path / "one.json"
    yaml_registry = tmp_path / "two.yml"
    document = {
        "schema": "mission-specialist-registry/2",
        "specialists_v2": [
            {
                "role": "deep-planning",
                "skill": "deep-planning-provider",
                "task_profiles": ["architecture"],
                "phases": ["planning"],
                "activation": {
                    "min_complexity": "Complex",
                    "auto_select_if": ["complexity"],
                    "when_any": ["architecture", "stalled_iteration"],
                    "explicit_below_min": "deny",
                },
            }
        ],
    }
    json_registry.write_text(json.dumps(document), encoding="utf-8")
    yaml_registry.write_text(
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - role: deep-planning",
                "    skill: deep-planning-provider",
                "    task_profiles: [architecture]",
                "    phases: [planning]",
                "    activation:",
                "      min_complexity: Complex",
                "      auto_select_if: [complexity]",
                "      when_any: [architecture, stalled_iteration]",
                "      explicit_below_min: deny",
            ]
        ),
        encoding="utf-8",
    )

    digests = []
    activations = []
    for registry in (json_registry, yaml_registry):
        result = run_cli(
            "specialists",
            "recommend",
            "--no-default-skill-roots",
            "--task",
            "Review the architecture of a multi-step system",
            "--registry",
            str(registry),
            "--complexity",
            "Complex",
            "--installed-skills",
            "deep-planning-provider",
            "--json",
            cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        candidate = json.loads(result.stdout)["specialists_candidates"][0]
        digests.append(candidate["registry_entry_digest"])
        activations.append(candidate["normalized_activation"])

    assert len(set(digests)) == 1
    assert activations[0] == activations[1]


@pytest.mark.parametrize(
    "content",
    [
        json.dumps(
            {
                "version": 1,
                "note": "the literal specialists_v2 is documentation only",
                "specialists": [],
            }
        ),
        json.dumps(
            {
                "version": 1,
                "metadata": {"specialists_v2": []},
                "specialists": [],
            }
        ),
        "\n".join(
            [
                "version: 1",
                "metadata: specialists_v2",
                "specialists:",
            ]
        ),
    ],
)
def test_explicit_registry_version_detection_uses_only_root_keys_and_schema(content):
    assert detect_registry_version(content) == 1


@pytest.mark.parametrize(
    ("content", "reason_code"),
    [
        (
            '{"schema":"mission-specialist-registry/3","specialists_v2":[]}',
            "unknown-registry-major",
        ),
        ('{"specialists_v2":[]}', "missing-registry-schema"),
        (
            '{"schema":"mission-specialist-registry/2","schema":"mission-specialist-registry/2","specialists_v2":[]}',
            "duplicate-registry-key",
        ),
        (
            "\n".join(
                [
                    "schema: mission-specialist-registry/2",
                    "specialists_v2:",
                    "  - role: deep-planning",
                    "    activation:",
                    "        min_complexity: Complex",
                ]
            ),
            "unsupported-registry-depth",
        ),
        (
            '{"schema":"mission-specialist-registry/2","specialists_v2":[{"role":"deep-planning","activation":{"nested":{"min_complexity":"Complex"}}}]}',
            "unsupported-registry-depth",
        ),
        (
            '{"schema":"mission-specialist-registry/2","specialists_v2":[],"specialists":[]}',
            "mixed-registry-version",
        ),
    ],
)
def test_invalid_v2_registry_is_reported_without_candidates(
    content, reason_code, run_cli, tmp_path
):
    registry = tmp_path / "invalid-v2.yml"
    registry.write_text(content, encoding="utf-8")

    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Coordinate an unclear multi-step effort",
        "--registry",
        str(registry),
        "--complexity",
        "Complex",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_ineligible"][0]["reason_code"] == reason_code


def test_unparseable_higher_input_blocks_lower_registry_and_builtin_candidates(
    run_cli, tmp_path
):
    high = tmp_path / "high-v2.json"
    low = tmp_path / "low-v1.json"
    high.write_text(
        '{"schema":"mission-specialist-registry/2",'
        '"specialists_v2":[{"role":"deep-planning"}],'
        '"specialists_v2":[]}',
        encoding="utf-8",
    )
    low.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "doc-writer",
                        "skill": "documentation-provider",
                        "task_profiles": ["documentation"],
                        "phases": ["planning"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Update README documentation", "--registry", str(low),
        "--registry", str(high), "--complexity", "Complex",
        "--installed-skills", "documentation-provider", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_phase_plan"] == []
    invalid = next(
        item for item in data["specialists_ineligible"]
        if item["reason_code"] == "duplicate-registry-key"
    )
    assert invalid["source"] == f"registry:{_project_identity(high, tmp_path)}"
    projection = data["specialist_registry_projection"]
    invalid_input = next(
        item for item in projection["ordered_inputs"]
        if item["canonical_identity"] == _project_identity(high, tmp_path)
    )
    assert invalid_input["status"] == "invalid"
    assert projection["precedence_barriers"][0]["source"] == (
        f"registry:{_project_identity(high, tmp_path)}"
    )


def test_invalid_explicit_input_preserves_only_earlier_valid_explicit_input(
    run_cli, tmp_path
):
    valid = tmp_path / "first-v2.json"
    invalid = tmp_path / "second-v2.json"
    low = tmp_path / "low-v1.json"
    valid.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    invalid.write_text(
        '{"schema":"mission-specialist-registry/2",'
        '"specialists_v2":[],"specialists_v2":[]}',
        encoding="utf-8",
    )
    low.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "doc-writer",
                        "skill": "documentation-provider",
                        "task_profiles": ["documentation"],
                        "phases": ["planning"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Coordinate a multi-step effort", "--registry", str(valid),
        "--registry", str(invalid), "--registry", str(low),
        "--complexity", "Complex",
        "--installed-skills", "deep-planning-provider,documentation-provider",
        "--json", cwd=tmp_path,
    )

    data = json.loads(result.stdout)
    assert [item["provider_id"] for item in data["specialists_candidates"]] == [
        "deep-planning-provider"
    ]
    assert data["specialists_selected"][0]["provider_id"] == "deep-planning-provider"
    assert data["specialists_selected"][0]["source"] == (
        f"registry:{_project_identity(valid, tmp_path)}"
    )
    assert data["specialists_phase_plan"][0]["providers"] == ["deep-planning-provider"]


def test_explicit_v2_precedes_explicit_v1_and_records_projection(run_cli, tmp_path):
    v1 = tmp_path / "first-v1.json"
    v2 = tmp_path / "second-v2.json"
    v1.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["review"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    v2.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                            "explicit_below_min": "deny",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--task",
        "Coordinate an unclear multi-step effort",
        "--registry",
        str(v1),
        "--registry",
        str(v2),
        "--complexity",
        "Complex",
        "--installed-skills",
        "deep-planning-provider",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    selected = data["specialists_selected"][0]
    projection = data["specialist_registry_projection"]
    assert selected["registry_version"] == 2
    assert selected["phases"] == ["planning"]
    assert selected["registry_projection_digest"] == projection["effective_projection_digest"]
    assert [item["version"] for item in projection["ordered_inputs"][:2]] == [2, 1]
    assert all(item["canonical_identity"] for item in projection["ordered_inputs"])
    assert all(item["content_digest"].startswith("sha256:") for item in projection["ordered_inputs"][:2])


def test_invalid_higher_entry_does_not_fall_back_to_lower_v1(run_cli, tmp_path):
    high = tmp_path / "high-v2.json"
    low = tmp_path / "low-v1.json"
    high.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
                        "auto_use": {"min_complexity": "Complex"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    low.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(low), "--registry", str(high),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider", "--json",
        cwd=tmp_path,
    )
    data = json.loads(result.stdout)

    assert data["specialists_selected"] == []
    item = next(entry for entry in data["specialists_ineligible"] if entry["provider_id"] == "deep-planning-provider")
    assert item["reason_code"] == "conflicting-activation-config"
    assert item["source"] == f"registry:{_project_identity(high, tmp_path)}"


def test_v2_empty_auto_use_mixed_with_activation_fails_closed(run_cli, tmp_path):
    registry = tmp_path / "mixed-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                        "auto_use": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider",
        "--json", cwd=tmp_path,
    )

    data = json.loads(result.stdout)
    assert data["specialists_selected"] == []
    rejected = next(
        item for item in data["specialists_ineligible"]
        if item["provider_id"] == "deep-planning-provider"
    )
    assert rejected["reason_code"] == "conflicting-activation-config"


def test_v2_disabled_tombstone_suppresses_lower_builtin(run_cli, tmp_path):
    registry = tmp_path / ".mission" / "specialists-v2.yml"
    registry.parent.mkdir()
    registry.write_text(
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - role: doc-writer",
                "    skill: documentation-provider",
                "    disabled: true",
            ]
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Update README documentation", "--installed-skills", "documentation-provider",
        "--json", cwd=tmp_path,
    )
    data = json.loads(result.stdout)

    assert all(item["skill"] != "documentation-provider" for item in data["specialists_candidates"])
    disabled = next(item for item in data["specialists_ineligible"] if item["provider_id"] == "documentation-provider")
    assert disabled["reason_code"] == "provider-disabled"


def test_same_file_duplicate_identity_is_a_conflict(run_cli, tmp_path):
    registry = tmp_path / "duplicates-v2.json"
    entry = {
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
        "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
    }
    registry.write_text(
        json.dumps({"schema": "mission-specialist-registry/2", "specialists_v2": [entry, entry]}),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider", "--json",
        cwd=tmp_path,
    )
    data = json.loads(result.stdout)

    assert data["specialists_selected"] == []
    conflict = next(item for item in data["specialists_ineligible"] if item["provider_id"] == "deep-planning-provider")
    assert conflict["reason_code"] == "same-tier-identity-conflict"


def test_multiple_explicit_v2_paths_use_argument_order(run_cli, tmp_path):
    paths = [tmp_path / "first.json", tmp_path / "second.json"]
    for path, phases in zip(paths, (["planning"], ["review"])):
        path.write_text(
            json.dumps(
                {
                    "schema": "mission-specialist-registry/2",
                    "specialists_v2": [
                        {
                            "role": "deep-planning",
                            "skill": "deep-planning-provider",
                            "task_profiles": ["architecture"],
                            "phases": phases,
                            "activation": {
                                "min_complexity": "Complex",
                                "auto_select_if": ["complexity"],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(paths[0]), "--registry", str(paths[1]),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider", "--json",
        cwd=tmp_path,
    )
    selected = json.loads(result.stdout)["specialists_selected"][0]

    assert selected["source"] == f"registry:{_project_identity(paths[0], tmp_path)}"
    assert selected["phases"] == ["planning"]


@pytest.mark.parametrize("complexity", ["Simple", "Standard", "Unknown"])
def test_cli_reports_complexity_floor_rejection_for_explicit_selection(
    complexity, run_cli, tmp_path
):
    registry = tmp_path / "specialists-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--user-specified", "deep-planning-provider", "--complexity", complexity,
        "--installed-skills", "deep-planning-provider", "--json", cwd=tmp_path,
    )
    data = json.loads(result.stdout)

    assert data["specialists_selected"] == []
    rejected = next(item for item in data["specialists_ineligible"] if item["provider_id"] == "deep-planning-provider")
    assert rejected["reason_code"] == (
        "unknown-complexity" if complexity == "Unknown" else "below-min-complexity"
    )
    assert rejected["selection_source"] == "user-instruction"
    assert rejected["selection_source_raw"] == "user-specified"


def test_stalled_iteration_predicate_uses_current_state(run_cli, state_dir):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update({"iteration": 2, "complexity": "Complex", "passes": False})
    state_path.write_text(json.dumps(state), encoding="utf-8")
    registry = state_dir.parent / "specialists-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                            "when_any": ["stalled_iteration"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Coordinate an unclear effort", "--registry", str(registry),
        "--installed-skills", "deep-planning-provider", "--json", cwd=state_dir.parent,
    )
    selected = json.loads(result.stdout)["specialists_selected"][0]

    assert "when:stalled_iteration" in selected["matched_conditions"]


def test_legacy_v1_loader_ignores_v2_root_when_explicitly_given(tmp_path):
    registry = tmp_path / "specialists-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [{"role": "deep-planning", "skill": "deep-planning-provider"}],
            }
        ),
        encoding="utf-8",
    )
    state_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_issue394", state_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module._load_specialist_registry(str(registry)) == []


def test_runtime_cli_cannot_claim_automatic_selection_source(run_cli, tmp_path):
    result = run_cli(
        "specialists", "log-invocation", "--iteration", "1", "--phase", "planning",
        "--role", "deep-planning", "--skill", "deep-planning-provider",
        "--mode", "codex-inline", "--status", "completed",
        "--selection-source", "automatic", cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr


@pytest.mark.parametrize(
    ("phases", "reason_code"),
    [([], "invalid-phase-allow-list"), (["review"], "phase-not-allowed")],
)
def test_v2_complexity_provider_requires_an_allowed_planning_phase(
    phases, reason_code, run_cli, tmp_path
):
    registry = tmp_path / "specialists-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": phases,
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider", "--json",
        cwd=tmp_path,
    )
    data = json.loads(result.stdout)

    assert data["specialists_selected"] == []
    rejected = next(item for item in data["specialists_ineligible"] if item["provider_id"] == "deep-planning-provider")
    assert rejected["reason_code"] == reason_code
    assert all(
        "deep-planning-provider" not in phase["providers"]
        for phase in data["specialists_phase_plan"]
    )


def test_installed_v2_manifest_is_in_projection_inventory(run_cli, tmp_path):
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "deep-planning-provider"
    skill_dir.mkdir(parents=True)
    manifest = skill_dir / "mission-specialist-v2.yml"
    manifest.write_text(
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - role: deep-planning",
                "    skill: deep-planning-provider",
                "    task_profiles: [architecture]",
                "    phases: [planning]",
                "    activation:",
                "      min_complexity: Complex",
                "      auto_select_if: [complexity]",
            ]
        ),
        encoding="utf-8",
    )
    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--skills-dir", str(skill_root),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider", "--json",
        cwd=tmp_path,
    )
    data = json.loads(result.stdout)
    inputs = data["specialist_registry_projection"]["ordered_inputs"]

    selected = data["specialists_selected"][0]
    assert selected["source"] == (
        f"skill-manifest:{_project_identity(manifest, tmp_path)}"
    )
    assert any(item["kind"] == "skill-root" and item["content_digest"].startswith("sha256:") for item in inputs)
    manifest_input = next(
        item for item in inputs
        if item["canonical_identity"] == _project_identity(manifest, tmp_path)
    )
    assert manifest_input["version"] == 2
    assert manifest_input["content_digest"].startswith("sha256:")


def test_v1_registry_rejects_v2_activation_field_mixing(run_cli, tmp_path):
    registry = tmp_path / "specialists.yml"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider", "--json",
        cwd=tmp_path,
    )
    data = json.loads(result.stdout)

    assert data["specialists_selected"] == []
    rejected = next(item for item in data["specialists_ineligible"] if item["provider_id"] == "deep-planning-provider")
    assert rejected["reason_code"] == "mixed-registry-version"


def test_registry_docs_define_v2_activation_and_selection_provenance():
    reference = (
        Path(__file__).resolve().parents[1] / "refs" / "specialist-registry.md"
    ).read_text(encoding="utf-8")

    for token in (
        ".mission/specialists-v2.yml",
        "mission-specialist-registry/2",
        "specialists_v2:",
        "auto_select_if: [complexity]",
        "when_any",
        "unknown-complexity",
        "selection_source_raw",
        "mission-specialist-registry-projection/1",
        "effective_projection_digest",
        "precedence_barriers",
        "state-context-mismatch",
        "duplicate-registry-input",
        "invalid-v2-candidate-type",
        "invalid-json-number",
        "$PROJECT",
        "$HOME",
        "projection_state",
        "single byte snapshot",
    ):
        assert token in reference


def test_duplicate_schema_cannot_downgrade_explicit_v2_to_v1(run_cli, tmp_path):
    registry = tmp_path / "duplicate-schema.json"
    registry.write_text(
        '{"schema":"mission-specialist-registry/2","schema":"legacy",'
        '"specialists":[{"role":"doc-writer","skill":"documentation-provider",'
        '"task_profiles":["documentation"],"phases":["planning"]}]}',
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Update README documentation", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "documentation-provider",
        "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_phase_plan"] == []
    assert any(
        item["reason_code"] == "duplicate-registry-key"
        for item in data["specialists_ineligible"]
    )


def test_yaml_version_detector_rejects_duplicate_root_schema():
    with pytest.raises(RegistryContractError) as raised:
        detect_registry_version(
            "\n".join(
                [
                    "schema: mission-specialist-registry/2",
                    "schema: legacy",
                    "specialists:",
                ]
            )
        )

    assert raised.value.code == "duplicate-registry-key"


def test_official_project_v2_path_cannot_contain_v1_document(run_cli, tmp_path):
    registry = tmp_path / ".mission" / "specialists-v2.yml"
    registry.parent.mkdir()
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "doc-writer",
                        "skill": "documentation-provider",
                        "task_profiles": ["documentation"],
                        "phases": ["planning"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Update README documentation", "--complexity", "Complex",
        "--installed-skills", "documentation-provider", "--json", cwd=tmp_path,
    )

    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert any(
        item["reason_code"] == "missing-registry-schema"
        for item in data["specialists_ineligible"]
    )


@pytest.mark.parametrize("location", ["user", "manifest"])
def test_official_user_and_manifest_v2_paths_cannot_downgrade_to_v1(
    location, run_cli, tmp_path
):
    fake_home = tmp_path / "home"
    args = [
        "specialists", "recommend", "--task", "Update README documentation",
        "--complexity", "Complex", "--installed-skills", "documentation-provider",
        "--json",
    ]
    if location == "user":
        registry = fake_home / ".config" / "mission" / "specialists-v2.yml"
        registry.parent.mkdir(parents=True)
    else:
        skill_root = tmp_path / "skills"
        registry = skill_root / "documentation-provider" / "mission-specialist-v2.yml"
        registry.parent.mkdir(parents=True)
        args.extend(["--no-default-skill-roots", "--skills-dir", str(skill_root)])
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "role": "doc-writer",
                        "skill": "documentation-provider",
                        "task_profiles": ["documentation"],
                        "phases": ["planning"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(*args, cwd=tmp_path, env_extra={"HOME": str(fake_home)})

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert any(
        item["reason_code"] == "missing-registry-schema"
        for item in data["specialists_ineligible"]
    )


@pytest.mark.parametrize(
    "override",
    [
        {"provider_id": 42},
        {"skill": 42},
        {"task_profiles": "architecture"},
        {"phases": "planning"},
        {"disabled": 1},
        {"activation": []},
        {"activation": {"min_complexity": 3, "auto_select_if": ["complexity"]}},
    ],
    ids=[
        "provider-id",
        "skill",
        "profiles",
        "phases",
        "disabled-bool",
        "activation-map",
        "activation-member",
    ],
)
def test_v2_candidate_schema_rejects_wrong_exact_types(
    override, run_cli, tmp_path
):
    registry = tmp_path / "typed-v2.json"
    candidate = {
        "provider_id": "deep-planning-provider",
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
        "disabled": False,
        "activation": {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
            "explicit_below_min": "deny",
        },
    }
    candidate.update(override)
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [candidate],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider",
        "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert any(
        item["reason_code"] == "invalid-v2-candidate-type"
        for item in data["specialists_ineligible"]
    )


def test_v2_json_nan_is_rejected_before_candidate_selection(run_cli, tmp_path):
    registry = tmp_path / "nan-v2.json"
    registry.write_text(
        '{"schema":"mission-specialist-registry/2","specialists_v2":['
        '{"role":"deep-planning","skill":"deep-planning-provider",'
        '"task_profiles":["architecture"],"phases":["planning"],'
        '"timeout":NaN,"activation":{"min_complexity":"Complex",'
        '"auto_select_if":["complexity"]}}]}',
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider",
        "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert any(
        item["reason_code"] == "invalid-json-number"
        for item in data["specialists_ineligible"]
    )


def test_v2_yaml_quoted_hash_comma_and_escaped_quote_match_json():
    document = {
        "schema": "mission-specialist-registry/2",
        "specialists_v2": [
            {
                "role": "deep#planning",
                "skill": "deep,planning-provider",
                "task_profiles": ["architecture", "risk"],
                "phases": ["planning"],
                "notes": 'say "plan, then #review"',
                "activation": {
                    "min_complexity": "Complex",
                    "auto_select_if": ["complexity"],
                },
            }
        ],
    }
    yaml_text = "\n".join(
        [
            "schema: mission-specialist-registry/2",
            "specialists_v2:",
            '  - role: "deep#planning"',
            '    skill: "deep,planning-provider"',
            '    task_profiles: ["architecture", "risk"]',
            "    phases: [planning]",
            '    notes: "say \\"plan, then #review\\""',
            "    activation:",
            "      min_complexity: Complex",
            "      auto_select_if: [complexity]",
        ]
    )

    assert parse_v2_registry(yaml_text) == parse_v2_registry(json.dumps(document))


def test_generated_selection_and_projection_do_not_persist_home_paths(
    run_cli, tmp_path
):
    fake_home = tmp_path / "Users" / "private-user"
    explicit = fake_home / "registries" / "planning-v2.json"
    explicit.parent.mkdir(parents=True)
    explicit.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    invalid = fake_home / "registries" / "invalid-v2.json"
    invalid.write_text(
        '{"schema":"mission-specialist-registry/2",'
        '"specialists_v2":[],"specialists_v2":[]}',
        encoding="utf-8",
    )
    skill_root = fake_home / "skills"
    manifest = skill_root / "review-provider" / "mission-specialist-v2.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - role: reviewer",
                "    skill: review-provider",
                "    task_profiles: [architecture]",
                "    phases: [review]",
            ]
        ),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    env = {"HOME": str(fake_home)}
    run_cli(
        "init", "portable planning state", "--complexity", "Complex",
        cwd=project, check=True, env_extra=env,
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(explicit),
        "--registry", str(invalid),
        "--skills-dir", str(skill_root), "--complexity", "Complex",
        "--installed-skills", "deep-planning-provider,review-provider",
        "--record-state", "--json", cwd=project, env_extra=env,
    )

    assert result.returncode == 0, result.stderr
    state = (project / ".mission-state" / "sessions" / "test.json").read_text(
        encoding="utf-8"
    )
    assert str(fake_home) not in result.stdout
    assert str(fake_home) not in state


def test_registry_discovery_reads_each_input_once(monkeypatch, tmp_path):
    registry = tmp_path / "single-read-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [],
            }
        ),
        encoding="utf-8",
    )
    module = _load_mission_state_module("mission_state_issue394_single_read")
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    reads = 0

    def counted_read_bytes(path):
        nonlocal reads
        if path == registry:
            reads += 1
        return original_read_bytes(path)

    def counted_read_text(path, *args, **kwargs):
        nonlocal reads
        if path == registry:
            reads += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    monkeypatch.setattr(Path, "read_text", counted_read_text)
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(
        registry=[str(registry)],
        no_default_skill_roots=True,
        skills_dir=None,
    )

    module._discover_specialist_registry_candidates(args)

    assert reads == 1


def test_projection_digest_binds_ordered_discovery_inputs(run_cli, tmp_path):
    registry = tmp_path / "explicit-v2.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def projection_digest():
        result = run_cli(
            "specialists", "recommend", "--no-default-skill-roots",
            "--task", "Review the architecture", "--registry", str(registry),
            "--complexity", "Complex", "--installed-skills", "deep-planning-provider",
            "--json", cwd=tmp_path,
        )
        return json.loads(result.stdout)["specialist_registry_projection"][
            "effective_projection_digest"
        ]

    before = projection_digest()
    project_v2 = tmp_path / ".mission" / "specialists-v2.yml"
    project_v2.parent.mkdir()
    project_v2.write_text(
        "schema: mission-specialist-registry/2\nspecialists_v2:\n",
        encoding="utf-8",
    )

    assert projection_digest() != before


def test_projection_digest_and_entries_bind_conflict_semantics(run_cli, tmp_path):
    registry = tmp_path / "semantic-v2.json"
    entry = {
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
        "activation": {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
        },
    }

    def projection(document):
        registry.write_text(json.dumps(document), encoding="utf-8")
        result = run_cli(
            "specialists", "recommend", "--no-default-skill-roots",
            "--task", "Review the architecture", "--registry", str(registry),
            "--complexity", "Complex", "--installed-skills", "deep-planning-provider",
            "--json", cwd=tmp_path,
        )
        return json.loads(result.stdout)["specialist_registry_projection"]

    valid = projection(
        {"schema": "mission-specialist-registry/2", "specialists_v2": [entry]}
    )
    conflict = projection(
        {"schema": "mission-specialist-registry/2", "specialists_v2": [entry, entry]}
    )

    assert valid["effective_projection_digest"] != conflict["effective_projection_digest"]
    assert valid["effective_entries"][0]["projection_state"] == "eligible"
    assert conflict["effective_entries"][0]["projection_state"] == "conflict"


def test_projection_entries_distinguish_disabled_tombstone_and_barrier(
    run_cli, tmp_path
):
    registry = tmp_path / "semantic-registry.json"
    entry = {
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
    }

    def state_for(content: str) -> tuple[str, str]:
        registry.write_text(content, encoding="utf-8")
        result = run_cli(
            "specialists", "recommend", "--no-default-skill-roots",
            "--task", "Review the architecture", "--registry", str(registry),
            "--complexity", "Complex", "--json", cwd=tmp_path,
        )
        projection = json.loads(result.stdout)["specialist_registry_projection"]
        effective = projection["effective_entries"][0]
        return effective["projection_state"], projection["effective_projection_digest"]

    disabled = state_for(
        json.dumps({"version": 1, "specialists": [{**entry, "enabled": False}]})
    )
    tombstone = state_for(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [{**entry, "disabled": True}],
            }
        )
    )
    barrier = state_for(
        '{"schema":"mission-specialist-registry/2",'
        '"specialists_v2":[],"specialists_v2":[]}'
    )

    assert [disabled[0], tombstone[0], barrier[0]] == [
        "disabled", "tombstone", "invalid-input-barrier"
    ]
    assert len({disabled[1], tombstone[1], barrier[1]}) == 3


def test_duplicate_explicit_registry_symlink_alias_fails_closed(run_cli, tmp_path):
    registry = tmp_path / "planning-v2.json"
    alias = tmp_path / "planning-alias.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "activation": {
                            "min_complexity": "Complex",
                            "auto_select_if": ["complexity"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    alias.symlink_to(registry)

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--registry", str(alias), "--complexity", "Complex",
        "--installed-skills", "deep-planning-provider", "--json", cwd=tmp_path,
    )

    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_phase_plan"] == []
    assert any(
        item["reason_code"] == "duplicate-registry-input"
        for item in data["specialists_ineligible"]
    )


def test_build_phase_plan_rejects_below_floor_candidate_directly():
    module = _load_mission_state_module("mission_state_issue394_phase_floor")
    candidate = {
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
        "activation": {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
        },
        "installed": True,
        "available": True,
        "score": 1.0,
    }

    assert module.build_phase_plan([candidate], "Simple") == []
