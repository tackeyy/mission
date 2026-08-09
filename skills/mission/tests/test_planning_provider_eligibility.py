"""Issue #394: complexity-aware planning provider eligibility."""

import json
import importlib.util
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from provider_eligibility import (  # noqa: E402
    evaluate_provider_eligibility,
    normalize_selection_source,
)


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
    assert item["source"] == f"registry:{high}"


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

    assert selected["source"] == f"registry:{paths[0]}"
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
    assert selected["source"] == f"skill-manifest:{manifest}"
    assert any(item["kind"] == "skill-root" and item["content_digest"].startswith("sha256:") for item in inputs)
    manifest_input = next(item for item in inputs if item["canonical_identity"] == str(manifest.resolve()))
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
    ):
        assert token in reference
