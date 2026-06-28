"""Issue #29: specialist auto-selection policy and interactive fallback."""
import json
import sys


def _json_result(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _recommend_args(*args):
    return ("specialists", "recommend", "--no-default-skill-roots", *args)


def test_recommend_auto_selects_high_confidence_installed_candidate(run_cli, tmp_path):
    r = run_cli(
        *_recommend_args(
        "--task",
        "Implement a React UI component with accessibility tests",
        "--installed-skills",
        "frontend-provider",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["task_profile"]["primary"] == "frontend"
    assert data["specialists_decision"]["policy"] == "auto"
    assert data["specialists_selected"][0]["skill"] == "frontend-provider"
    assert data["specialists_decision"]["prompted_user"] is False


def test_recommend_tied_installed_candidates_uses_interactive_fallback(run_cli, tmp_path):
    r = run_cli(
        *_recommend_args(
        "--task",
        "Improve React UI component visual quality",
        "--installed-skills",
        "frontend-provider,visual-quality-provider",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "interactive"
    assert data["specialists_decision"]["prompted_user"] is True
    assert data["specialists_selected"] == []


def test_recommend_missing_builtin_preset_falls_back_without_install_prompt(run_cli, tmp_path):
    r = run_cli(
        *_recommend_args(
        "--task",
        "Write README documentation and ADR guidance",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["task_profile"]["primary"] == "documentation"
    assert data["specialists_decision"]["policy"] == "fallback"
    assert data["specialists_decision"]["action"] == "continue-core"
    assert data["specialists_decision"]["prompted_user"] is False
    assert data["specialists_unavailable"][0]["skill"] == "documentation-provider"


def test_recommend_missing_registry_candidate_recommends_install(run_cli, tmp_path):
    registry = tmp_path / "specialists.yml"
    registry.write_text(
        "\n".join([
            "version: 1",
            "specialists:",
            "  - role: doc-reviewer",
            "    skill: missing-doc-provider",
            "    task_profiles: [documentation]",
            "    phases: [planning, review]",
        ])
    )
    r = run_cli(
        *_recommend_args(
        "--task",
        "Write README documentation and ADR guidance",
        "--registry",
        str(registry),
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "install-recommended"
    assert data["specialists_unavailable"][0]["skill"] == "missing-doc-provider"


def test_recommend_prefers_registry_candidate_over_builtin_preset_duplicate(run_cli, tmp_path):
    registry = tmp_path / "specialists.yml"
    registry.write_text(
        "\n".join([
            "version: 1",
            "specialists:",
            "  - role: doc-writer",
            "    skill: documentation-provider",
            "    task_profiles: [documentation]",
            "    phases: [review]",
        ])
    )

    r = run_cli(
        *_recommend_args(
        "--task",
        "Update README documentation",
        "--registry",
        str(registry),
        "--installed-skills",
        "documentation-provider",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)

    matching = [c for c in data["specialists_candidates"] if c["skill"] == "documentation-provider"]
    assert len(matching) == 1
    assert matching[0]["source"].startswith("registry:")
    assert matching[0]["phases"] == ["review"]


def test_recommend_missing_required_registry_candidate_prompts_user(run_cli, tmp_path):
    registry = tmp_path / "specialists.yml"
    registry.write_text(
        "\n".join([
            "version: 1",
            "specialists:",
            "  - role: security-reviewer",
            "    skill: required-security-skill",
            "    task_profiles: [security]",
            "    phases: [planning, review]",
            "    required: true",
        ])
    )
    r = run_cli(
        *_recommend_args(
        "--task",
        "Review auth token handling for security risks",
        "--registry",
        str(registry),
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "confirm"
    assert "high-risk" in data["specialists_decision"]["reason"]


def test_recommend_missing_required_low_risk_candidate_prompts_required_missing(run_cli, tmp_path):
    registry = tmp_path / "specialists.yml"
    registry.write_text(
        "\n".join([
            "version: 1",
            "specialists:",
            "  - role: doc-reviewer",
            "    skill: required-doc-skill",
            "    task_profiles: [documentation]",
            "    required: true",
        ])
    )
    r = run_cli(
        *_recommend_args(
        "--task",
        "Update README documentation",
        "--registry",
        str(registry),
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "required-missing"
    assert data["specialists_decision"]["prompted_user"] is True


def test_recommend_high_risk_installed_candidate_requires_confirmation(run_cli, tmp_path):
    r = run_cli(
        *_recommend_args(
        "--task",
        "Deploy production auth token security change",
        "--installed-skills",
        "security-review-provider",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["task_profile"]["risk"] == "high"
    assert data["specialists_decision"]["policy"] == "confirm"
    assert data["specialists_selected"] == []


def test_recommend_first_use_candidate_requires_confirmation(run_cli, tmp_path):
    r = run_cli(
        *_recommend_args(
        "--task",
        "Write README documentation",
        "--installed-skills",
        "documentation-provider",
        "--first-use",
        "documentation-provider",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "first-use"
    assert data["specialists_decision"]["prompted_user"] is True


def test_recommend_record_state_persists_selection_metadata(run_cli, tmp_path):
    run_cli("init", "specialist selection mission", "--complexity", "Standard", cwd=tmp_path, check=True)
    r = run_cli(
        *_recommend_args(
        "--task",
        "Implement backend API endpoint tests",
        "--installed-skills",
        "backend-provider",
        "--record-state",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["task_profile"] == data["task_profile"]
    assert state["specialists_candidates"] == data["specialists_candidates"]
    assert state["specialists_selected"] == data["specialists_selected"]
    assert state["specialists_unavailable"] == data["specialists_unavailable"]
    assert state["specialists_decision"] == data["specialists_decision"]
    assert state["specialists_phase_plan"] == data["specialists_phase_plan"]
    assert state["specialists_mode"] == "auto"


def test_recommend_discovers_project_registry_without_explicit_registry(run_cli, tmp_path):
    project_registry = tmp_path / ".mission" / "specialists.yml"
    project_registry.parent.mkdir()
    project_registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "project-doc-reviewer",
            "skill": "project-doc-skill",
            "task_profiles": ["documentation"],
            "phases": ["planning", "review"],
            "required": False,
        }],
    }))

    r = run_cli(
        *_recommend_args(
        "--task",
        "Update README documentation",
        "--installed-skills",
        "project-doc-skill",
        "--json",
        ),
        cwd=tmp_path,
    )

    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "auto"
    assert data["specialists_selected"][0]["skill"] == "project-doc-skill"
    assert data["specialists_selected"][0]["source"] == "project:.mission/specialists.yml"


def test_project_registry_can_disable_user_default_provider(run_cli, tmp_path):
    user_registry = tmp_path / ".config" / "mission" / "specialists.yml"
    user_registry.parent.mkdir(parents=True)
    user_registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "user-doc-reviewer",
            "skill": "user-doc-skill",
            "task_profiles": ["documentation"],
        }],
    }))
    project_registry = tmp_path / ".mission" / "specialists.yml"
    project_registry.parent.mkdir()
    project_registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "user-doc-reviewer",
            "skill": "user-doc-skill",
            "enabled": False,
        }],
    }))

    r = run_cli(
        "specialists", "recommend",
        "--task", "Update README documentation",
        "--installed-skills", "user-doc-skill",
        "--json",
        cwd=tmp_path,
        env_extra={"HOME": str(tmp_path), "MISSION_SESSION_ID": "test"},
    )

    data = _json_result(r)
    assert all(c["skill"] != "user-doc-skill" for c in data["specialists_candidates"])


def test_recommend_supports_command_provider_yaml_schema(run_cli, tmp_path):
    helper = tmp_path / "reviewer.py"
    helper.write_text("import sys; print('reviewed', len(sys.stdin.read()))\n", encoding="utf-8")
    project_registry = tmp_path / ".mission" / "specialists.yml"
    project_registry.parent.mkdir()
    project_registry.write_text("\n".join([
        "version: 1",
        "specialists:",
        "  - role: oracle-reviewer",
        "    kind: command",
        f"    command: {sys.executable}",
        f"    args: [{helper}]",
        "    task_profiles: [documentation, architecture]",
        "    phases: [planning, review, critic]",
        "    required: false",
        "    max_calls_per_iteration: 1",
        "    auto_use:",
        "      min_complexity: Complex",
        "    risk:",
        "      first_use_confirmation: false",
    ]))

    r = run_cli(
        *_recommend_args(
        "--task",
        "Review architecture documentation",
        "--complexity",
        "Complex",
        "--json",
        ),
        cwd=tmp_path,
    )

    data = _json_result(r)
    selected = data["specialists_selected"][0]
    assert selected["kind"] == "command"
    assert selected["skill"] == "oracle-reviewer"
    assert selected["command"] == sys.executable
    assert selected["args"] == [str(helper)]
    assert selected["max_calls_per_iteration"] == "1"


def test_risk_first_use_consent_allowlist_enables_auto_selection(run_cli, tmp_path):
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "paid-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": ["-c", "print('ok')"],
            "task_profiles": ["documentation"],
            "risk": {"first_use_confirmation": True, "may_consume_paid_quota": True},
        }],
    }))
    consent_file = tmp_path / "provider-consent.json"

    first = run_cli(
        *_recommend_args(
        "--task", "Update README documentation",
        "--complexity", "Complex",
        "--consent-file", str(consent_file),
        "--json",
        ),
        cwd=tmp_path,
    )
    first_data = _json_result(first)
    assert first_data["specialists_decision"]["policy"] == "first-use"

    run_cli(
        "specialists", "consent",
        "--provider", "paid-reviewer",
        "--consent-file", str(consent_file),
        cwd=tmp_path,
        check=True,
    )
    second = run_cli(
        *_recommend_args(
        "--task", "Update README documentation",
        "--complexity", "Complex",
        "--consent-file", str(consent_file),
        "--json",
        ),
        cwd=tmp_path,
    )
    second_data = _json_result(second)
    assert second_data["specialists_decision"]["policy"] == "auto"
    assert second_data["specialists_selected"][0]["skill"] == "paid-reviewer"


def test_missing_command_provider_degrades_to_core_reviewers(run_cli, tmp_path):
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "missing-reviewer",
            "kind": "command",
            "command": str(tmp_path / "missing-command"),
            "task_profiles": ["documentation", "testing", "infra"],
            "unavailable": "continue",
        }],
    }))

    r = run_cli(
        *_recommend_args(
        "--task", "Update README documentation with pytest and CI guidance",
        "--complexity", "Complex",
        "--json",
        ),
        cwd=tmp_path,
    )

    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "provider-unavailable"
    assert data["specialists_decision"]["action"] == "continue-core"
    assert data["specialists_selected"] == []
    assert data["specialists_unavailable"][0]["kind"] == "command"
    assert data["specialists_unavailable"][0]["skill"] == "missing-reviewer"


def test_recommend_builds_phase_plan_for_development_registry(run_cli, tmp_path):
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [
            {
                "role": "backend",
                "skill": "backend-provider",
                "task_profiles": ["backend"],
                "phases": ["execution"],
            },
            {
                "role": "unit-tester",
                "skill": "unit-test-provider",
                "task_profiles": ["testing", "backend"],
                "phases": ["review"],
            },
        ],
    }))

    r = run_cli(
        *_recommend_args(
        "--task", "Implement backend API behavior and unit tests",
        "--complexity", "Complex",
        "--installed-skills", "backend-provider,unit-test-provider",
        "--json",
        ),
        cwd=tmp_path,
    )

    data = _json_result(r)
    phases = {item["phase"]: item for item in data["specialists_phase_plan"]}
    assert phases["execution"]["providers"] == ["backend-provider"]
    assert phases["review"]["providers"] == ["unit-test-provider"]


def test_recommend_bounds_broad_orchestrator_to_non_execution_phases(run_cli, tmp_path):
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [
            {
                "role": "backend",
                "skill": "backend-provider",
                "task_profiles": ["backend"],
                "phases": ["execution"],
            },
            {
                "role": "methodology",
                "skill": "broad-methodology",
                "task_profiles": ["backend"],
                "phases": ["planning", "execution", "review"],
                "notes": "Broad orchestrator for methodology; bounded evidence only.",
            },
        ],
    }))

    r = run_cli(
        *_recommend_args(
        "--task", "Implement backend API behavior and tests",
        "--complexity", "Complex",
        "--installed-skills", "backend-provider,broad-methodology",
        "--json",
        ),
        cwd=tmp_path,
    )

    data = _json_result(r)
    broad = next(c for c in data["specialists_candidates"] if c["skill"] == "broad-methodology")
    phases = {item["phase"]: item["providers"] for item in data["specialists_phase_plan"]}
    assert broad["bounded_use"] is True
    assert broad["bounded_purpose_required"] is True
    assert "execution" not in broad["phases"]
    assert set(broad["phases"]) == {"planning", "review"}
    assert "broad-methodology" not in phases["execution"]
    assert any("broad-methodology" in providers for providers in phases.values())


def test_recommend_classifies_strategy_registry_without_private_taxonomy(run_cli, tmp_path):
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [
            {
                "role": "market-research",
                "skill": "market-research-provider",
                "task_profiles": ["research", "strategy"],
                "phases": ["planning", "execution"],
            },
            {
                "role": "financial-modeling",
                "skill": "financial-model-provider",
                "task_profiles": ["financial", "strategy"],
                "phases": ["execution"],
            },
            {
                "role": "strategy-review",
                "skill": "strategy-review-provider",
                "task_profiles": ["strategy", "risk"],
                "phases": ["review", "synthesis"],
            },
        ],
    }))

    r = run_cli(
        *_recommend_args(
        "--task", "市場規模と競合差別化を踏まえてROIとリスクを整理する戦略提案",
        "--complexity", "Critical",
        "--installed-skills", "market-research-provider,financial-model-provider,strategy-review-provider",
        "--json",
        ),
        cwd=tmp_path,
    )

    data = _json_result(r)
    assert data["task_profile"]["primary"] in {"research", "strategy", "financial", "risk"}
    providers_by_phase = {item["phase"]: item["providers"] for item in data["specialists_phase_plan"]}
    assert "market-research-provider" in providers_by_phase["planning"]
    assert "financial-model-provider" in providers_by_phase["execution"]
    assert "strategy-review-provider" in providers_by_phase["review"]
