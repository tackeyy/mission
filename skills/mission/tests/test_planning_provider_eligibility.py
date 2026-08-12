"""Issue #394: complexity-aware planning provider eligibility."""

import copy
import json
import importlib.util
import os
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
from provider_public_contract import (  # noqa: E402
    SpecialistPublicContractError,
    validate_specialist_public_state,
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


def test_public_candidate_schema_rejects_unknown_key_without_value_disclosure():
    marker = "opaque-unknown-value"
    state = {
        "specialists_candidates": [
            {
                "provider_id": "portable-provider",
                "kind": "skill",
                "unknown_config": marker,
            }
        ]
    }

    with pytest.raises(SpecialistPublicContractError) as caught:
        validate_specialist_public_state(state)

    assert caught.value.field_path == "/specialists_candidates/0/unknown_config"
    assert marker not in str(caught.value)


def test_atomic_session_writer_validates_public_specialist_schema_before_publish(
    monkeypatch, tmp_path
):
    module = _load_mission_state_module("mission_state_issue394_atomic_public_gate")
    published = False

    def publish_spy(path, writer):
        nonlocal published
        published = True

    monkeypatch.setattr(module, "_enforce_session_lease_for_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_atomic_write", publish_spy)
    state = {
        "mission_id": "portable-mission-id",
        "loop_active": False,
        "specialists_candidates": [
            {
                "provider_id": "portable-provider",
                "kind": "skill",
                "unknown_config": "opaque-value",
            }
        ],
    }

    with pytest.raises(module.SpecialistPublicContractError) as caught:
        module.atomic_write_json(tmp_path / "state.json", state, administrative=True)

    assert caught.value.field_path == "/specialists_candidates/0/unknown_config"
    assert published is False


def _valid_public_projection():
    digest = "sha256:" + "a" * 64
    registry_input = {
        "kind": "explicit",
        "source": "registry:$PROJECT/registry.json",
        "canonical_identity": "$PROJECT/registry.json",
        "version": 2,
        "precedence_tier": 0,
        "order": 0,
        "status": "present",
        "content_digest": digest,
    }
    return {
        "schema": "mission-specialist-registry-projection/1",
        "ordered_inputs": [registry_input],
        "precedence_barriers": [
            {
                key: value
                for key, value in registry_input.items()
                if key not in {"version", "status"}
            }
        ],
        "effective_entries": [
            {
                "provider_id": "portable-provider",
                "source": "registry:$PROJECT/registry.json",
                "registry_version": 2,
                "registry_entry_digest": digest,
                "projection_state": "eligible",
            }
        ],
        "effective_projection_digest": digest,
    }


def test_public_allowlist_rejects_unknown_keys_at_every_nested_surface():
    candidate = {"provider_id": "portable-provider", "kind": "skill"}
    diagnostic = {
        "provider_id": "portable-provider",
        "reason_code": "unknown-complexity",
    }
    decision = {
        "policy": "fallback",
        "action": "continue-core",
        "prompted_user": False,
    }
    phase_plan = {
        "phase": "planning",
        "roles": ["deep-planning"],
        "providers": ["portable-provider"],
        "max_providers": 1,
    }
    invocation = {
        "iteration": 1,
        "phase": "review",
        "role": "reviewer",
        "skill": "portable-provider",
        "mode": "command-provider",
        "status": "completed",
        "timestamp": "2026-08-10T00:00:00Z",
    }
    cases = []
    for surface in (
        "specialists_candidates",
        "specialists_selected",
        "specialists_unavailable",
    ):
        cases.append(
            ({surface: [{**candidate, "unknown": "opaque"}]}, f"/{surface}/0/unknown")
        )
    cases.extend(
        [
            (
                {"specialists_ineligible": [{**diagnostic, "unknown": "opaque"}]},
                "/specialists_ineligible/0/unknown",
            ),
            (
                {"specialists_decision": {**decision, "unknown": "opaque"}},
                "/specialists_decision/unknown",
            ),
            (
                {"specialists_phase_plan": [{**phase_plan, "unknown": "opaque"}]},
                "/specialists_phase_plan/0/unknown",
            ),
            (
                {"specialist_invocations": [{**invocation, "unknown": "opaque"}]},
                "/specialist_invocations/0/unknown",
            ),
        ]
    )
    projection_paths = (
        ((), "/specialist_registry_projection/unknown"),
        (("ordered_inputs", 0), "/specialist_registry_projection/ordered_inputs/0/unknown"),
        (("precedence_barriers", 0), "/specialist_registry_projection/precedence_barriers/0/unknown"),
        (("effective_entries", 0), "/specialist_registry_projection/effective_entries/0/unknown"),
    )
    for location, expected in projection_paths:
        projection = _valid_public_projection()
        target = projection
        for key in location:
            target = target[key]
        target["unknown"] = "opaque"
        cases.append(({"specialist_registry_projection": projection}, expected))

    for state, expected_path in cases:
        with pytest.raises(SpecialistPublicContractError) as caught:
            validate_specialist_public_state(copy.deepcopy(state))
        assert caught.value.field_path == expected_path
        assert "opaque" not in str(caught.value)


def test_public_allowlist_rejects_invalid_types_enums_sources_and_digests():
    digest = "sha256:" + "a" * 64
    cases = [
        ({"specialists_candidates": [{"provider_id": "portable", "kind": "other"}]}, "/specialists_candidates/0/kind"),
        ({"specialists_candidates": [{"provider_id": "portable", "kind": "skill", "source": "/home/private/provider"}]}, "/specialists_candidates/0/source"),
        ({"specialists_candidates": [{"provider_id": "portable", "kind": "skill", "source": "unknown:provider"}]}, "/specialists_candidates/0/source"),
        ({"specialists_candidates": [{"provider_id": "portable", "kind": "skill", "role": "x" * 129}]}, "/specialists_candidates/0/role"),
        ({"specialists_candidates": [{"provider_id": "portable", "kind": "skill", "context_digest": "invalid"}]}, "/specialists_candidates/0/context_digest"),
        ({"specialists_candidates": [{"provider_id": "portable", "kind": "skill", "normalized_activation": {"unknown": True}}]}, "/specialists_candidates/0/normalized_activation/unknown"),
        ({"installed_skills": [{"provider_id": "portable"}]}, "/installed_skills"),
        ({"specialists_decision": {"policy": "fallback", "action": "invalid", "prompted_user": False}}, "/specialists_decision/action"),
        ({"specialists_phase_plan": [{"phase": "invalid", "roles": [], "providers": [], "max_providers": 1}]}, "/specialists_phase_plan/0/phase"),
        ({"specialist_invocations": [{"command": "/root/private-provider"}]}, "/specialist_invocations/0/command"),
    ]
    projection = _valid_public_projection()
    projection["effective_projection_digest"] = "invalid"
    cases.append(({"specialist_registry_projection": projection}, "/specialist_registry_projection/effective_projection_digest"))
    projection = _valid_public_projection()
    projection["effective_entries"][0]["projection_state"] = "invalid"
    cases.append(({"specialist_registry_projection": projection}, "/specialist_registry_projection/effective_entries/0/projection_state"))

    for state, expected_path in cases:
        with pytest.raises(SpecialistPublicContractError) as caught:
            validate_specialist_public_state(state)
        assert caught.value.field_path == expected_path


def test_public_invocation_schema_accepts_safe_legacy_bare_command():
    state = {
        "specialist_invocations": [
            {
                "iteration": 1,
                "phase": "review",
                "role": "reviewer",
                "skill": "portable-provider",
                "mode": "command-provider",
                "status": "completed",
                "timestamp": "2026-08-10T00:00:00Z",
                "command": "portable-reviewer",
            }
        ]
    }

    validate_specialist_public_state(state)


@pytest.mark.parametrize(
    "private_locator",
    [
        "finding path=/home/portable-user/private.txt",
        "finding path:/root/private.txt",
        "finding path=/tmp/private.txt",
        r"finding path=C:\\Users\\portable-user\\private.txt",
        "finding path=~/private.txt",
        r"finding path=\\server\share\private.txt",
        r"finding path=\\?\C:\private\file.txt",
        r"finding path=\\.\PhysicalDrive0",
        r"finding path=\Device\HarddiskVolume1\private.txt",
        "finding path=//server/share/private.txt",
        r"finding path=C:relative\private.txt",
        "finding path=D:relative/private.txt",
        "finding metadata='E:relative/private.txt'",
        "finding path=C:private.txt",
        "finding metadata='D:.env'",
        "finding path=c:private.txt",
        "finding link=https:///C:private.txt",
        "finding link=https://example.test:bad/home/portable-user/secret.txt",
        "finding link=https://[::1/home/portable-user/secret.txt",
        "finding link=https://exa%mple/C:private.txt",
        "finding link=https://example.test,local=/home/portable-user/secret.txt",
        "finding link=https://user@example.test/C:private.txt",
        "finding link=https://example.test/%ZZ/C:private.txt",
        r"finding link=https://example.test\C:private.txt",
        r"finding link=https://example.test/path\home\portable-user\private.txt",
        "finding link=https://example.test/path|C:private.txt",
        "finding link=https://example.test/path?locator={C:private.txt}",
        "finding link=https://example.test/path#locator=[C:private.txt]",
        "finding link=https://example.test:0/C:private.txt",
        "finding link=https://example.test:65536/C:private.txt",
        "finding file=file:relative/private.txt",
        "finding file=file:/private.txt",
        "finding file=file://server/share/private.txt",
        "finding file=file:///private.txt",
        "finding home=~",
        "finding home='~portable-user'",
        "finding home=~+",
        "finding home=~-",
        "finding path=~portable-user/private.txt",
        r"finding metadata=~portable-user\private.txt",
    ],
)
@pytest.mark.parametrize(
    ("surface", "field", "expected_path"),
    [
        ("specialists_candidates", "role", "/specialists_candidates/0/role"),
        ("specialists_selected", "reason", "/specialists_selected/0/reason"),
        ("specialist_invocations", "notes", "/specialist_invocations/0/notes"),
    ],
)
def test_public_text_fields_reject_embedded_private_locators_after_any_separator(
    private_locator, surface, field, expected_path
):
    if surface == "specialist_invocations":
        record = {
            "iteration": 1,
            "phase": "planning",
            "role": "planner",
            "skill": "portable-provider",
            "mode": "skill-tool",
            "status": "completed",
            "timestamp": "2026-08-10T00:00:00Z",
        }
    else:
        record = {"provider_id": "portable-provider", "kind": "skill"}
    record[field] = private_locator

    with pytest.raises(SpecialistPublicContractError) as caught:
        validate_specialist_public_state({surface: [record]})

    assert caught.value.field_path == expected_path
    assert private_locator not in str(caught.value)


@pytest.mark.parametrize(
    "portable_text",
    [
        "finding status:portable-token",
        "finding urn:portable:value",
        "finding https://example.test/docs/path",
        "finding http://example.test/docs/path",
        "finding https://example.test/docs/a:b/c",
        "finding https://example.test/~portable-user/private.txt",
        "finding link='https://example.test/docs/a:b/~portable-user/file'",
        "finding https://example.test/C:private.txt",
        "finding https://example.test:443/docs/a:b/~portable-user/%20file",
        "finding https://example.test:65535/docs/a:b/~portable-user/%20file",
        "finding http://192.0.2.1:8080/a:b/~portable-user",
        "finding https://[2001:db8::1]:8443/a:b/~portable-user/%2F",
        "finding https://example.test/path?value=a:b#~portable-user",
        "finding https://example.test/レビュー/計画?name=実行#評価",
        "finding https://example.test/a!$&()*+,;=:@/b?x=a:b/c?d#frag:a/b?c",
        "finding C:",
        "finding version~beta",
        "finding portable~marker",
    ],
)
def test_public_text_and_redactor_preserve_safe_colon_tokens_and_urls(portable_text):
    state = {
        "specialist_invocations": [
            {
                "iteration": 1,
                "phase": "planning",
                "role": "planner",
                "skill": "portable-provider",
                "mode": "skill-tool",
                "status": "completed",
                "timestamp": "2026-08-10T00:00:00Z",
                "notes": portable_text,
            }
        ]
    }

    validate_specialist_public_state(state)
    module = _load_mission_state_module("mission_state_issue394_safe_colon")
    assert module._redact_provider_output(portable_text) == portable_text


@pytest.mark.parametrize(
    "score",
    [True, 10**4000, float("nan"), float("inf"), -1, 1.01],
)
def test_public_candidate_score_is_exact_bounded_numeric_without_overflow(score):
    state = {
        "specialists_candidates": [
            {
                "provider_id": "portable-provider",
                "kind": "skill",
                "score": score,
            }
        ]
    }

    with pytest.raises(SpecialistPublicContractError) as caught:
        validate_specialist_public_state(state)

    assert caught.value.field_path == "/specialists_candidates/0/score"


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
        "specialist_registry_projection": None,
        "specialists_decision": {},
        "specialists_phase_plan": [],
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
            "specialists_phase_plan": [],
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
    assert after["specialists_phase_plan"] == []


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
    ("timeout", "yaml_literal"),
    [(1, "1"), (960, "960"), (86400, "86400")],
)
def test_v2_yaml_integer_timeout_matches_json_object_and_entry_digest(
    timeout, yaml_literal, run_cli, tmp_path
):
    document = {
        "schema": "mission-specialist-registry/2",
        "specialists_v2": [
            {
                "role": "deep-planning",
                "skill": "deep-planning-provider",
                "task_profiles": ["architecture"],
                "phases": ["planning"],
                "timeout": timeout,
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
            "  - role: deep-planning",
            "    skill: deep-planning-provider",
            "    task_profiles: [architecture]",
            "    phases: [planning]",
            f"    timeout: {yaml_literal}",
            "    activation:",
            "      min_complexity: Complex",
            "      auto_select_if: [complexity]",
        ]
    )
    assert parse_v2_registry(yaml_text) == parse_v2_registry(json.dumps(document))

    digests = []
    for name, content in (("registry.json", json.dumps(document)), ("registry.yml", yaml_text)):
        registry = tmp_path / name
        registry.write_text(content, encoding="utf-8")
        result = run_cli(
            "specialists",
            "recommend",
            "--no-default-skill-roots",
            "--task",
            "Review a multi-step architecture",
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
        assert candidate["timeout"] == timeout
        digests.append(candidate["registry_entry_digest"])

    assert digests[0] == digests[1]


@pytest.mark.parametrize(
    ("json_literal", "yaml_literal"),
    [
        ("true", "true"),
        ("NaN", ".nan"),
        ("Infinity", ".inf"),
        ("1.5", "1.5"),
        ("0.001", "0.001"),
        ("-0", "-0"),
        ("0", "0"),
    ],
)
def test_v2_json_and_yaml_reject_non_integer_or_out_of_range_timeout(
    json_literal, yaml_literal
):
    prefix = (
        '{"schema":"mission-specialist-registry/2","specialists_v2":['
        '{"provider_id":"deep-planning-provider","timeout":'
    )
    json_text = f"{prefix}{json_literal}}}]}}"
    yaml_text = "\n".join(
        [
            "schema: mission-specialist-registry/2",
            "specialists_v2:",
            "  - provider_id: deep-planning-provider",
            f"    timeout: {yaml_literal}",
        ]
    )

    for content in (json_text, yaml_text):
        with pytest.raises(RegistryContractError):
            parse_v2_registry(content)


@pytest.mark.parametrize(
    ("suffix", "content"),
    [
        (
            "json",
            lambda token: (
                '{"schema":"mission-specialist-registry/2",'
                '"specialists_v2":[{"provider_id":"numeric-provider",'
                f'"timeout":{token}}}]}}'
            ),
        ),
        (
            "yml",
            lambda token: "\n".join(
                [
                    "schema: mission-specialist-registry/2",
                    "specialists_v2:",
                    "  - provider_id: numeric-provider",
                    f"    timeout: {token}",
                ]
            ),
        ),
    ],
)
@pytest.mark.parametrize("token", ["9" * 4300, "9" * 4301, "1e9999"])
def test_oversized_or_unrepresentable_registry_number_fails_with_structured_diagnostic(
    suffix, content, token, run_cli, tmp_path
):
    registry = tmp_path / f"oversized-number.{suffix}"
    registry.write_text(content(token), encoding="utf-8")

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0
    assert "Traceback" not in result.stderr
    assert "Infinity" not in result.stdout
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_unavailable"] == []
    assert data["specialists_phase_plan"] == []
    assert data["specialists_ineligible"][0]["reason_code"] == "number-limit"


def test_low_priority_numeric_barrier_preserves_valid_explicit_candidate(
    run_cli, tmp_path
):
    explicit = tmp_path / "explicit-v2.json"
    explicit.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "explicit-planning-provider",
                        "role": "deep-planning",
                        "skill": "explicit-planning-provider",
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
    installed = (
        tmp_path
        / "skills"
        / "low-priority-provider"
        / "mission-specialist-v2.yml"
    )
    installed.parent.mkdir(parents=True)
    installed.write_text(
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - provider_id: low-priority-provider",
                f"    timeout: {'9' * 4300}",
            ]
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists",
        "recommend",
        "--no-default-skill-roots",
        "--skills-dir",
        str(tmp_path / "skills"),
        "--registry",
        str(explicit),
        "--task",
        "Review a multi-step architecture",
        "--complexity",
        "Complex",
        "--installed-skills",
        "explicit-planning-provider",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [item["provider_id"] for item in data["specialists_selected"]] == [
        "explicit-planning-provider"
    ]
    assert any(
        item["reason_code"] == "number-limit"
        for item in data["specialists_ineligible"]
    )
    assert data["specialist_registry_projection"]["precedence_barriers"]


def test_v1_candidate_cannot_override_discovery_owned_source(run_cli, tmp_path):
    registry = tmp_path / "source-injection-v1.json"
    private_marker = str(tmp_path / "private-source-marker")
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "provider_id": "portable-planning-provider",
                        "role": "deep-planning",
                        "skill": "portable-planning-provider",
                        "source": private_marker,
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "auto_use": {"min_complexity": "Complex"},
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
        "--registry",
        str(registry),
        "--task",
        "Review a multi-step architecture",
        "--complexity",
        "Complex",
        "--installed-skills",
        "portable-planning-provider",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert private_marker not in result.stdout
    assert data["specialists_selected"][0]["source"] == (
        f"registry:{_project_identity(registry, tmp_path)}"
    )


def test_v1_invalid_activation_enum_is_not_copied_to_public_diagnostic(
    run_cli, tmp_path
):
    registry = tmp_path / "invalid-activation-v1.json"
    private_marker = str(tmp_path / "private-complexity-marker")
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "provider_id": "portable-planning-provider",
                        "role": "deep-planning",
                        "skill": "portable-planning-provider",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "auto_use": {"min_complexity": private_marker},
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
        "--registry",
        str(registry),
        "--task",
        "Review a multi-step architecture",
        "--complexity",
        "Complex",
        "--installed-skills",
        "portable-planning-provider",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert private_marker not in result.stdout
    diagnostic = data["specialists_ineligible"][0]
    assert diagnostic["reason_code"] == "unknown-complexity"
    assert diagnostic["field_code"] == "activation.min_complexity"
    assert "minimum_complexity" not in diagnostic


def test_nested_json_and_yaml_numbers_use_the_same_strict_numeric_parser():
    documents = [
        (
            '{"schema":"mission-specialist-registry/2",'
            '"specialists_v2":[{"provider_id":"numeric-provider",'
            '"risk":{"weight":1e309}}]}'
        ),
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - provider_id: numeric-provider",
                "    risk:",
                "      weight: 1e309",
            ]
        ),
    ]

    for document in documents:
        with pytest.raises(RegistryContractError) as caught:
            parse_v2_registry(document)
        assert caught.value.code == "number-limit"


@pytest.mark.parametrize(
    "document",
    [
        (
            '{"schema":"mission-specialist-registry/2",'
            '"specialists_v2":[{"provider_id":"nested-provider",'
            '"task_profiles":[{"name":"architecture"}]}]}'
        ),
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - provider_id: nested-provider",
                '    task_profiles: [{"name":"architecture"}]',
            ]
        ),
        "\n".join(
            [
                "schema: mission-specialist-registry/2",
                "specialists_v2:",
                "  - provider_id: nested-provider",
                "    task_profiles: [[architecture]]",
            ]
        ),
    ],
)
def test_nested_flow_collections_fail_with_json_yaml_depth_parity(document):
    with pytest.raises(RegistryContractError) as caught:
        parse_v2_registry(document)

    assert caught.value.code == "unsupported-registry-depth"


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


@pytest.mark.parametrize(
    ("mode", "reason_code"),
    [
        ("disabled", "provider-disabled"),
        ("conflict", "same-tier-identity-conflict"),
    ],
)
def test_disabled_and_conflict_diagnostics_redact_nonportable_provider_identity(
    mode, reason_code, run_cli, tmp_path
):
    private_provider_id = f"private/providers/{mode}-provider"
    entry = {
        "provider_id": private_provider_id,
        "role": "deep-planning",
        "skill": "deep-planning-provider",
        "kind": "skill",
        "task_profiles": ["architecture"],
        "phases": ["planning"],
        "activation": {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
        },
    }
    entries = [{**entry, "disabled": True}] if mode == "disabled" else [entry, entry]
    registry = tmp_path / f"{mode}-private-identity.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": entries,
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    diagnostic = next(
        item for item in data["specialists_ineligible"]
        if item["reason_code"] == reason_code
    )
    assert diagnostic["provider_id"].startswith("provider:sha256:")
    assert private_provider_id not in json.dumps(data, sort_keys=True)


def test_provider_id_omission_uses_distinct_canonical_identity_references(
    run_cli, tmp_path
):
    private_skills = [
        "private/providers/first-planner",
        "private/providers/second-planner",
    ]
    registry = tmp_path / "omitted-provider-ids.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "skill": skill,
                        "kind": "skill",
                        "disabled": True,
                    }
                    for skill in private_skills
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    disabled_ids = {
        item["provider_id"]
        for item in data["specialists_ineligible"]
        if item["reason_code"] == "provider-disabled"
    }
    projection_ids = {
        item["provider_id"]
        for item in data["specialist_registry_projection"]["effective_entries"]
        if item["projection_state"] == "tombstone"
    }
    assert len(disabled_ids) == 2
    assert projection_ids == disabled_ids
    assert all(identity.startswith("provider:sha256:") for identity in disabled_ids)
    assert not any(skill in json.dumps(data, sort_keys=True) for skill in private_skills)


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
        "invalid-registry-number",
        "number-limit",
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
        {"enabled": False},
        {"activation": []},
        {"activation": {"min_complexity": 3, "auto_select_if": ["complexity"]}},
    ],
    ids=[
        "provider-id",
        "skill",
        "profiles",
        "phases",
        "disabled-bool",
        "legacy-enabled",
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
    assert data["ok"] is True
    assert data["specialists_candidates"] == []
    assert any(
        item["reason_code"] == "invalid-registry-number"
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


def test_recommendation_and_state_do_not_expose_process_local_provider_config(
    run_cli, state_dir, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["complexity"] = "Complex"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    command = root / "private-home" / "bin" / "provider-command"
    command.parent.mkdir(parents=True)
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o700)
    private_arg = root / "private-temp" / "provider-input.json"
    private_env_value = "private-provider-env-value"
    registry = root / "command-provider.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "portable-command-provider",
                        "role": "deep-planning",
                        "skill": "portable-command-provider",
                        "kind": "command",
                        "command": str(command),
                        "args": ["--input", str(private_arg)],
                        "env": {"PROVIDER_SECRET_PATH": private_env_value},
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
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--record-state", "--json", cwd=root,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["specialists_candidates"] == []
    assert output["specialists_selected"] == []
    assert output["specialists_phase_plan"] == []
    assert any(
        item["provider_id"] == "portable-command-provider"
        and item["reason_code"] == "non-portable-execution-config"
        for item in output["specialists_ineligible"]
    )
    persisted = read_state(state_dir)
    for payload in (output, persisted):
        surface = {
            key: payload.get(key)
            for key in (
                "specialists_candidates",
                "specialists_selected",
                "specialists_unavailable",
                "specialists_ineligible",
                "specialist_registry_projection",
                "specialists_phase_plan",
            )
        }
        serialized = json.dumps(surface, sort_keys=True)
        assert str(command) not in serialized
        assert str(private_arg) not in serialized
        assert private_env_value not in serialized
        for provider in [
            *(surface["specialists_candidates"] or []),
            *(surface["specialists_selected"] or []),
            *(surface["specialists_unavailable"] or []),
        ]:
            assert "command" not in provider
            assert "args" not in provider
            assert "env" not in provider


def test_relative_path_command_config_fails_closed_without_raw_diagnostic(
    run_cli, tmp_path
):
    registry = tmp_path / "relative-command.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "portable-command-provider",
                        "role": "deep-planning",
                        "skill": "portable-command-provider",
                        "kind": "command",
                        "command": "true",
                        "args": ["--input", "private/prompts/plan.json"],
                        "env": {},
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
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_unavailable"] == []
    assert data["specialists_phase_plan"] == []
    blocked = next(
        item
        for item in data["specialists_ineligible"]
        if item["reason_code"] == "non-portable-execution-config"
    )
    assert blocked["blocked_config_class"] == "argument-locator"
    assert "private/prompts/plan.json" not in json.dumps(blocked, sort_keys=True)


def test_every_nonempty_command_argument_fails_closed_without_raw_diagnostic(
    run_cli, tmp_path
):
    unsafe_arguments = [
        ".",
        "..",
        "secrets.env",
        "review",
        "https://example.invalid/input",
        "urn:example:input",
        "@input-file",
        "private/input.json",
        "two words",
    ]
    for index, unsafe_argument in enumerate(unsafe_arguments):
        registry = tmp_path / f"nonempty-argument-{index}.json"
        registry.write_text(
            json.dumps(
                {
                    "schema": "mission-specialist-registry/2",
                    "specialists_v2": [
                        {
                            "provider_id": "portable-command-provider",
                            "role": "deep-planning",
                            "skill": "deep-planning-provider",
                            "kind": "command",
                            "command": "true",
                            "args": [unsafe_argument],
                            "env": {},
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
            "--task", "Review a multi-step architecture", "--registry", str(registry),
            "--complexity", "Complex", "--json", cwd=tmp_path,
        )

        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["specialists_candidates"] == []
        assert data["specialists_selected"] == []
        assert data["specialists_unavailable"] == []
        assert data["specialists_phase_plan"] == []
        blocked = next(
            item
            for item in data["specialists_ineligible"]
            if item["reason_code"] == "non-portable-execution-config"
        )
        assert blocked["blocked_config_class"] == "argument-locator"
        assert not {
            "command", "args", "env", "risk", "result_contract"
        } & set(blocked)


def test_nonportable_provider_identity_is_redacted_from_public_diagnostic(
    run_cli, tmp_path
):
    private_provider_id = "private/providers/deep-planning"
    registry = tmp_path / "private-provider-id.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": private_provider_id,
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "kind": "command",
                        "command": "true",
                        "args": [],
                        "env": {},
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
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    blocked = next(
        item
        for item in data["specialists_ineligible"]
        if item["reason_code"] == "non-portable-execution-config"
    )
    assert blocked["blocked_config_class"] == "provider-identity"
    assert blocked["provider_id"].startswith("provider:sha256:")
    assert private_provider_id not in json.dumps(data, sort_keys=True)


def test_nonportable_skill_provider_identity_is_fail_closed_on_every_public_surface(
    run_cli, tmp_path
):
    private_skill = "private/providers/deep-planning"
    registry = tmp_path / "private-skill-id.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "role": "deep-planning",
                        "skill": private_skill,
                        "kind": "skill",
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
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", private_skill,
        "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_unavailable"] == []
    assert data["specialists_phase_plan"] == []
    blocked = next(
        item
        for item in data["specialists_ineligible"]
        if item["reason_code"] == "non-portable-execution-config"
    )
    assert blocked["blocked_config_class"] == "provider-identity"
    assert blocked["provider_id"].startswith("provider:sha256:")
    assert private_skill not in json.dumps(data, sort_keys=True)


def test_public_specialist_records_drop_raw_nested_provider_config_from_stdout_state_and_backup(
    run_cli, state_dir, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["complexity"] = "Complex"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    private_marker = str(root / "private-temp" / "provider-secret")
    registry = root / "nested-config-provider.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "portable-skill-provider",
                        "role": "deep-planning",
                        "skill": "portable-skill-provider",
                        "kind": "skill",
                        "task_profiles": ["architecture"],
                        "phases": ["planning"],
                        "risk": {"private_marker": private_marker},
                        "result_contract": {
                            "forbidden_markers": [private_marker],
                        },
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
    command = (
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "portable-skill-provider",
        "--record-state", "--json",
    )

    first = run_cli(*command, cwd=root)
    second = run_cli(*command, cwd=root)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    backup_path = state_path.with_suffix(".json.bak")
    assert backup_path.exists()
    get_result = run_cli("get", cwd=root)
    assert get_result.returncode == 0, get_result.stderr
    for serialized in (
        first.stdout,
        second.stdout,
        state_path.read_text(encoding="utf-8"),
        backup_path.read_text(encoding="utf-8"),
        get_result.stdout,
    ):
        assert private_marker not in serialized
        payload = json.loads(serialized)
        for surface in (
            "specialists_candidates",
            "specialists_selected",
            "specialists_unavailable",
        ):
            for record in payload.get(surface) or []:
                assert "env" not in record
                assert "risk" not in record
                assert "result_contract" not in record
                assert "activation" not in record
                assert "auto_use" not in record


def test_parser_diagnostic_drops_duplicate_key_detail_and_private_value(
    run_cli, tmp_path
):
    private_marker = str(tmp_path / "private-temp" / "duplicate-key")
    registry = tmp_path / "duplicate-private-key.json"
    registry.write_text(
        (
            '{"schema":"mission-specialist-registry/2",'
            '"specialists_v2":[],'
            f'{json.dumps(private_marker)}:1,'
            f'{json.dumps(private_marker)}:2}}'
        ),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    diagnostic = next(
        item for item in data["specialists_ineligible"]
        if item["reason_code"] == "duplicate-registry-key"
    )
    assert "detail" not in diagnostic
    assert private_marker not in json.dumps(data, sort_keys=True)


def test_unsafe_legacy_specialist_state_fails_closed_before_read_write_or_invoke(
    run_cli, state_dir, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    private_marker = str(root / "private-temp" / "legacy-provider")
    state = read_state(state_dir)
    state["specialists_candidates"] = [
        {
            "provider_id": "legacy-command-provider",
            "role": "deep-planning",
            "skill": "legacy-command-provider",
            "kind": "command",
            "command": private_marker,
            "args": [private_marker],
            "env": {"PRIVATE_PROVIDER_VALUE": private_marker},
            "risk": {"private_marker": private_marker},
            "result_contract": {"forbidden_markers": [private_marker]},
        }
    ]
    state["specialists_selected"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")
    backup_path = state_path.with_suffix(".json.bak")
    backup_path.unlink(missing_ok=True)
    commands = [
        ("get",),
        ("specialists", "accounting", "--json"),
        ("specialists", "summary", "--json"),
        (
            "specialists", "invoke-command",
            "--provider", "legacy-command-provider",
            "--iteration", "1", "--phase", "planning", "--json",
        ),
        ("set", "phase=planning"),
    ]

    for command in commands:
        result = run_cli(*command, cwd=root)
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
        assert private_marker not in result.stdout
        assert private_marker not in result.stderr
        data = json.loads(result.stdout)
        assert data["ok"] is False
        assert data["reason_code"] == "unsafe-legacy-specialist-record"
        assert data["field_path"].startswith("/specialists_candidates/0/")
        assert not backup_path.exists()

    assert json.loads(state_path.read_text(encoding="utf-8"))["phase"] == "scoring"


def test_get_rejects_huge_legacy_candidate_score_without_traceback(
    run_cli, state_dir, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["specialists_candidates"] = [
        {
            "provider_id": "portable-provider",
            "kind": "skill",
            "score": 10**4000,
        }
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli("get", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    data = json.loads(result.stdout)
    assert data == {
        "ok": False,
        "reason_code": "unsafe-legacy-specialist-record",
        "field_path": "/specialists_candidates/0/score",
    }


def test_init_refuses_to_archive_unsafe_legacy_specialist_state(
    run_cli, state_dir, read_state
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    private_marker = str(root / "private-temp" / "legacy-init-provider")
    state = read_state(state_dir)
    state["specialists_candidates"] = [
        {
            "provider_id": "legacy-command-provider",
            "role": "deep-planning",
            "skill": "legacy-command-provider",
            "kind": "command",
            "command": private_marker,
            "args": [],
            "env": {},
        }
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli(
        "init", "replacement mission", "--complexity", "Complex",
        cwd=root, env_extra={"MISSION_SESSION_ID": "test"},
    )

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert private_marker not in result.stdout
    assert private_marker not in result.stderr
    data = json.loads(result.stdout)
    assert data["reason_code"] == "unsafe-legacy-specialist-record"
    assert data["field_path"] == "/specialists_candidates/0/command"
    assert not list((state_dir / "archive").glob("state-test-*.json"))
    assert json.loads(state_path.read_text(encoding="utf-8"))["mission_id"] == "abc12345"


@pytest.mark.parametrize("version", [1, 2], ids=["v1", "v2"])
@pytest.mark.parametrize(
    "source_kind",
    ["explicit", "project", "user", "installed"],
)
def test_nonempty_command_env_fails_closed_for_every_registry_source(
    version, source_kind, run_cli, tmp_path
):
    project = tmp_path / "project"
    fake_home = tmp_path / "home"
    project.mkdir()
    fake_home.mkdir()
    secret_value = "raw-private-provider-value"
    candidate = {
        "provider_id": "portable-command-provider",
        "role": "deep-planning",
        "skill": "portable-command-provider",
        "kind": "command",
        "command": "true",
        "args": [],
        "env": {"PROVIDER_SECRET": secret_value},
        "task_profiles": ["architecture"],
        "phases": ["planning"],
    }
    if version == 2:
        candidate["activation"] = {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
        }
        document = {
            "schema": "mission-specialist-registry/2",
            "specialists_v2": [candidate],
        }
    else:
        document = {"version": 1, "specialists": [candidate]}

    args = [
        "specialists", "recommend", "--task", "Review the architecture",
        "--complexity", "Complex", "--json",
    ]
    suffix = "v2.yml" if version == 2 else "v1.yml"
    if source_kind == "explicit":
        registry = project / f"explicit-{suffix}"
        args.extend(["--no-default-skill-roots", "--registry", str(registry)])
    elif source_kind == "project":
        registry = project / ".mission" / (
            "specialists-v2.yml" if version == 2 else "specialists.yml"
        )
        args.append("--no-default-skill-roots")
    elif source_kind == "user":
        registry = fake_home / ".config" / "mission" / (
            "specialists-v2.yml" if version == 2 else "specialists.yml"
        )
    else:
        skill_root = project / "skills"
        registry = skill_root / "portable-command-provider" / (
            "mission-specialist-v2.yml" if version == 2 else "mission-specialist.yml"
        )
        args.extend(["--no-default-skill-roots", "--skills-dir", str(skill_root)])
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps(document), encoding="utf-8")

    result = run_cli(*args, cwd=project, env_extra={"HOME": str(fake_home)})

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_unavailable"] == []
    assert data["specialists_phase_plan"] == []
    blocked = [
        item
        for item in data["specialists_ineligible"]
        if item["reason_code"] == "non-portable-execution-config"
    ]
    assert len(blocked) == 1
    assert blocked[0]["blocked_config_class"] == "environment-values"
    assert secret_value not in json.dumps(data, sort_keys=True)


@pytest.mark.parametrize("version", [1, 2], ids=["v1", "v2"])
def test_portable_path_command_remains_invokable_and_accounted(
    version, run_cli, tmp_path, prepare_approved_invocation
):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    command = command_dir / "portable-provider"
    command.write_text(
        "#!/bin/sh\ncat >/dev/null\nprintf 'portable substantive planning evidence\\n'\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    run_cli(
        "init", "portable command provider mission", "--complexity", "Complex",
        cwd=tmp_path, check=True, env_extra=env,
    )
    candidate = {
        "provider_id": "portable-command-provider",
        "role": "deep-planning",
        "skill": "portable-command-provider",
        "kind": "command",
        "command": "portable-provider",
        "args": [],
        "env": {},
        "task_profiles": ["architecture"],
        "phases": ["planning"],
    }
    if version == 2:
        candidate["activation"] = {
            "min_complexity": "Complex",
            "auto_select_if": ["complexity"],
        }
        document = {
            "schema": "mission-specialist-registry/2",
            "specialists_v2": [candidate],
        }
    else:
        document = {"version": 1, "specialists": [candidate]}
    registry = tmp_path / f"portable-command-v{version}.json"
    registry.write_text(json.dumps(document), encoding="utf-8")

    recommendation = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--record-state", "--json", cwd=tmp_path,
        env_extra=env,
    )
    assert recommendation.returncode == 0, recommendation.stderr
    selected = json.loads(recommendation.stdout)["specialists_selected"][0]
    assert selected["command"] == "portable-provider"
    assert selected["args"] == []
    assert "env" not in selected

    invoke_args, invoke_env, _ = prepare_approved_invocation(
        cwd=tmp_path, provider="portable-command-provider", iteration=1,
        phase="planning", registry=registry, env_extra=env, json_output=True,
    )
    invoked = run_cli(*invoke_args, cwd=tmp_path, env_extra=invoke_env)

    assert invoked.returncode == 0, invoked.stderr
    output = json.loads(invoked.stdout)
    assert output["ok"] is True
    state = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["specialist_invocations"][-1]["status"] == "completed"


@pytest.mark.parametrize(
    "path_kind",
    [
        "local", "linux-home", "linux-root", "windows", "windows-unc",
        "windows-extended", "windows-device", "windows-rooted",
        "windows-drive-relative-backslash", "windows-drive-relative-slash",
        "windows-drive-relative-leaf", "windows-drive-relative-dotfile",
        "windows-drive-relative-lowercase",
        "malformed-url-percent", "malformed-url-authority", "malformed-url-port",
        "malformed-url-path", "malformed-url-query", "malformed-url-fragment",
        "file-relative", "file-authority", "bare-home", "bare-named-home",
        "named-home-slash", "named-home-backslash",
    ],
)
def test_command_provider_evidence_redacts_process_local_paths(
    path_kind, run_cli, tmp_path, prepare_approved_invocation
):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    private_marker = {
        "local": str(tmp_path / "private-temp" / "provider-output"),
        "linux-home": "/home/neutral-user/private-provider-output.txt",
        "linux-root": "/root/private-provider-output.txt",
        "windows": r"C:\Users\neutral-user\private-provider-output.txt",
        "windows-unc": r"\\server\share\private-provider-output.txt",
        "windows-extended": r"\\?\C:\private\provider-output.txt",
        "windows-device": r"\\.\PhysicalDrive0",
        "windows-rooted": r"\Device\HarddiskVolume1\private-provider-output.txt",
        "windows-drive-relative-backslash": r"C:relative\private-provider-output.txt",
        "windows-drive-relative-slash": "D:relative/private-provider-output.txt",
        "windows-drive-relative-leaf": "C:private-provider-output.txt",
        "windows-drive-relative-dotfile": "D:.private-provider-output",
        "windows-drive-relative-lowercase": "c:private-provider-output.txt",
        "malformed-url-percent": "https://exa%mple/C:private-provider-output.txt",
        "malformed-url-authority": "https://example.test,local=/home/portable-user/private.txt",
        "malformed-url-port": "https://example.test:bad/home/portable-user/private.txt",
        "malformed-url-path": r"https://example.test/path\home\portable-user\private.txt",
        "malformed-url-query": "https://example.test/path?locator=|C:private.txt",
        "malformed-url-fragment": "https://example.test/path#locator={C:private.txt}",
        "file-relative": "file:relative/private-provider-output.txt",
        "file-authority": "file://server/share/private-provider-output.txt",
        "bare-home": "~",
        "bare-named-home": "~portable-user",
        "named-home-slash": "~portable-user/private-provider-output.txt",
        "named-home-backslash": r"~portable-user\private-provider-output.txt",
    }[path_kind]
    command = command_dir / "portable-path-reporter"
    command.write_text(
        "#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' "
        + json.dumps(f"finding: private path {private_marker}")
        + "\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    run_cli(
        "init", "provider evidence path hygiene", "--complexity", "Complex",
        cwd=tmp_path, check=True, env_extra=env,
    )
    registry = tmp_path / "path-reporter.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "portable-path-reporter",
                        "role": "deep-planning",
                        "skill": "portable-path-reporter",
                        "kind": "command",
                        "command": "portable-path-reporter",
                        "args": [],
                        "env": {},
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
    run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--record-state", "--json",
        cwd=tmp_path, check=True, env_extra=env,
    )

    invoke_args, invoke_env, _ = prepare_approved_invocation(
        cwd=tmp_path, provider="portable-path-reporter", iteration=1,
        phase="planning", registry=registry, env_extra=env, json_output=True,
    )
    result = run_cli(*invoke_args, cwd=tmp_path, env_extra=invoke_env)

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    evidence = tmp_path / data["entry"]["evidence_path"]
    serialized = evidence.read_text(encoding="utf-8")
    assert private_marker not in serialized
    assert "[REDACTED_PATH]" in serialized


def test_external_registry_projection_requires_explicit_resupply(run_cli, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "external-provider.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "deep-planning-provider",
                        "role": "deep-planning",
                        "skill": "deep-planning-provider",
                        "kind": "command",
                        "command": "true",
                        "args": ["review", "--stdin"],
                        "env": {},
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
        "--task", "Review a multi-step architecture", "--registry", str(registry),
        "--complexity", "Complex", "--installed-skills", "deep-planning-provider",
        "--json", cwd=project,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    projection = data["specialist_registry_projection"]
    external = next(
        item
        for item in projection["ordered_inputs"]
        if str(item["canonical_identity"]).startswith("$EXTERNAL/sha256:")
    )
    assert external["resolution_mode"] == "explicit-resupply-required"
    assert str(registry) not in json.dumps(projection, sort_keys=True)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_phase_plan"] == []
    blocked = next(
        item
        for item in data["specialists_ineligible"]
        if item["reason_code"] == "non-portable-execution-config"
    )
    assert blocked["blocked_config_class"] == "external-registry-resupply"


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
    original_open = module.os.open
    opens = 0

    def counted_open(path, flags, *args, **kwargs):
        nonlocal opens
        if Path(path) == registry:
            opens += 1
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(module.os, "open", counted_open)
    monkeypatch.chdir(tmp_path)
    args = SimpleNamespace(
        registry=[str(registry)],
        no_default_skill_roots=True,
        skills_dir=None,
    )

    module._discover_specialist_registry_candidates(args)

    assert opens == 1


def test_registry_fd_snapshot_remains_bound_when_symlink_target_swaps(
    monkeypatch, tmp_path
):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    alias = tmp_path / "registry.json"
    first.write_text(
        '{"schema":"mission-specialist-registry/2","specialists_v2":[]}',
        encoding="utf-8",
    )
    second.write_text(
        '{"schema":"mission-specialist-registry/2","specialists_v2":['
        '{"provider_id":"replacement"}]}',
        encoding="utf-8",
    )
    alias.symlink_to(first)
    module = _load_mission_state_module("mission_state_issue394_symlink_swap")
    original_open = module.os.open
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = original_open(path, flags, *args, **kwargs)
        if Path(path) == alias and not swapped:
            alias.unlink()
            alias.symlink_to(second)
            swapped = True
        return fd

    monkeypatch.setattr(module.os, "open", swapping_open)
    monkeypatch.chdir(tmp_path)

    record, raw, physical_identity, error = module._read_registry_input(
        alias,
        "registry:$PROJECT/registry.json",
        "explicit",
        2,
        0,
        0,
    )

    first_stat = first.stat()
    assert record["status"] == "present"
    assert record["canonical_identity"] == "$PROJECT/registry.json"
    assert raw == first.read_bytes()
    assert physical_identity == (first_stat.st_dev, first_stat.st_ino)
    assert error is None


def test_registry_fd_snapshot_rejects_non_regular_inputs(monkeypatch, tmp_path):
    directory = tmp_path / "registry-directory"
    fifo = tmp_path / "registry-fifo"
    directory.mkdir()
    os.mkfifo(fifo)
    module = _load_mission_state_module("mission_state_issue394_non_regular")
    monkeypatch.chdir(tmp_path)

    inputs = [directory, fifo]
    device = Path("/dev/null")
    if device.exists():
        inputs.append(device)
    for order, path in enumerate(inputs):
        record, raw, _physical_identity, error = module._read_registry_input(
            path,
            f"registry:$PROJECT/non-regular-{order}",
            "explicit",
            2,
            0,
            order,
        )
        assert record["status"] == "invalid"
        assert raw is None
        assert error is not None
        assert error.code == "registry-input-not-regular"


def test_registry_fd_snapshot_rejects_oversize_input(monkeypatch, tmp_path):
    registry = tmp_path / "oversize.json"
    registry.write_bytes(b"x" * (1024 * 1024 + 1))
    module = _load_mission_state_module("mission_state_issue394_oversize")
    monkeypatch.chdir(tmp_path)

    record, raw, _physical_identity, error = module._read_registry_input(
        registry,
        "registry:$PROJECT/oversize.json",
        "explicit",
        2,
        0,
        0,
    )

    assert record["status"] == "invalid"
    assert raw is None
    assert error is not None
    assert error.code == "registry-input-too-large"


def test_registry_fd_snapshot_rejects_in_place_mutation(monkeypatch, tmp_path):
    registry = tmp_path / "mutating.json"
    registry.write_bytes(b"a" * 1024)
    module = _load_mission_state_module("mission_state_issue394_mutation")
    original_read = module.os.read
    mutated = False

    def mutating_read(fd, size):
        nonlocal mutated
        chunk = original_read(fd, size)
        if chunk and not mutated:
            with registry.open("r+b") as stream:
                stream.seek(0)
                stream.write(b"b" * 1024)
                stream.flush()
                os.fsync(stream.fileno())
            mutated = True
        return chunk

    monkeypatch.setattr(module.os, "read", mutating_read)
    monkeypatch.chdir(tmp_path)
    record, raw, _physical_identity, error = module._read_registry_input(
        registry,
        "registry:$PROJECT/mutating.json",
        "explicit",
        2,
        0,
        0,
    )

    assert record["status"] == "invalid"
    assert raw is None
    assert error is not None
    assert error.code == "registry-input-changed"


@pytest.mark.parametrize("failure_stage", ["read", "post-fstat"])
def test_registry_fd_io_error_is_structured_and_closes_once(
    failure_stage, monkeypatch, tmp_path
):
    registry = tmp_path / "io-error.json"
    registry.write_text(
        '{"schema":"mission-specialist-registry/2","specialists_v2":[]}',
        encoding="utf-8",
    )
    module = _load_mission_state_module(f"mission_state_issue394_{failure_stage}")
    original_read = module.os.read
    original_fstat = module.os.fstat
    original_close = module.os.close
    fstat_calls = 0
    close_calls = 0

    def failing_read(fd, size):
        if failure_stage == "read":
            raise OSError("simulated registry read failure")
        return original_read(fd, size)

    def failing_fstat(fd):
        nonlocal fstat_calls
        fstat_calls += 1
        if failure_stage == "post-fstat" and fstat_calls == 2:
            raise OSError("simulated registry metadata failure")
        return original_fstat(fd)

    def counted_close(fd):
        nonlocal close_calls
        close_calls += 1
        return original_close(fd)

    monkeypatch.setattr(module.os, "read", failing_read)
    monkeypatch.setattr(module.os, "fstat", failing_fstat)
    monkeypatch.setattr(module.os, "close", counted_close)
    monkeypatch.chdir(tmp_path)

    record, raw, _physical_identity, error = module._read_registry_input(
        registry,
        "registry:$PROJECT/io-error.json",
        "explicit",
        2,
        0,
        0,
    )

    assert record["status"] == "invalid"
    assert raw is None
    assert error is not None
    assert error.code == "registry-input-unreadable"
    assert close_calls == 1


def test_byte_identical_distinct_registry_inodes_are_not_duplicate(run_cli, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    content = json.dumps(
        {
            "schema": "mission-specialist-registry/2",
            "specialists_v2": [
                {
                    "provider_id": "deep-planning-provider",
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
    )
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")
    assert first.stat().st_ino != second.stat().st_ino

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(first),
        "--registry", str(second), "--complexity", "Complex",
        "--installed-skills", "deep-planning-provider", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"][0]["provider_id"] == "deep-planning-provider"
    assert data["specialists_selected"][0]["provider_id"] == "deep-planning-provider"
    assert not any(
        item["reason_code"] == "duplicate-registry-input"
        for item in data["specialists_ineligible"]
    )


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


def test_duplicate_explicit_registry_hardlink_alias_fails_closed(run_cli, tmp_path):
    registry = tmp_path / "planning-v2.json"
    alias = tmp_path / "planning-hardlink.json"
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
    os.link(registry, alias)

    result = run_cli(
        "specialists", "recommend", "--no-default-skill-roots",
        "--task", "Review the architecture", "--registry", str(registry),
        "--registry", str(alias), "--complexity", "Complex",
        "--installed-skills", "deep-planning-provider", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["specialists_candidates"] == []
    assert data["specialists_selected"] == []
    assert data["specialists_phase_plan"] == []
    assert sum(
        item["reason_code"] == "duplicate-registry-input"
        for item in data["specialists_ineligible"]
    ) == 2


def test_v2_provider_id_only_tombstone_suppresses_builtin_provider(run_cli, tmp_path):
    registry = tmp_path / "disable-builtin.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "documentation-provider",
                        "disabled": True,
                    }
                ],
            }
        ),
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
    assert data["specialist_registry_projection"]["effective_entries"][0] == {
        **data["specialist_registry_projection"]["effective_entries"][0],
        "provider_id": "documentation-provider",
        "projection_state": "tombstone",
    }


def test_v2_tombstone_does_not_disable_different_provider_with_shared_skill_alias(
    run_cli, tmp_path
):
    tombstone = tmp_path / "tombstone-v2.json"
    fallback = tmp_path / "fallback-v1.json"
    tombstone.write_text(
        json.dumps(
            {
                "schema": "mission-specialist-registry/2",
                "specialists_v2": [
                    {
                        "provider_id": "retired-provider",
                        "skill": "shared-planning-skill",
                        "disabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    fallback.write_text(
        json.dumps(
            {
                "version": 1,
                "specialists": [
                    {
                        "provider_id": "active-provider",
                        "role": "active-planning",
                        "skill": "shared-planning-skill",
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
        "--task", "Review the architecture", "--registry", str(tombstone),
        "--registry", str(fallback), "--complexity", "Complex",
        "--installed-skills", "shared-planning-skill", "--json", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [item["provider_id"] for item in data["specialists_candidates"]] == [
        "active-provider"
    ]
    assert data["specialists_selected"][0]["provider_id"] == "active-provider"


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
