"""Issue #29: specialist auto-selection policy and interactive fallback."""
import json


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
        "dev-frontend",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["task_profile"]["primary"] == "frontend"
    assert data["specialists_decision"]["policy"] == "auto"
    assert data["specialists_selected"][0]["skill"] == "dev-frontend"
    assert data["specialists_decision"]["prompted_user"] is False


def test_recommend_tied_installed_candidates_uses_interactive_fallback(run_cli, tmp_path):
    r = run_cli(
        *_recommend_args(
        "--task",
        "Improve React UI component visual quality",
        "--installed-skills",
        "dev-frontend,frontend-skill",
        "--json",
        ),
        cwd=tmp_path,
    )
    data = _json_result(r)
    assert data["specialists_decision"]["policy"] == "interactive"
    assert data["specialists_decision"]["prompted_user"] is True
    assert data["specialists_selected"] == []


def test_recommend_missing_optional_candidate_recommends_install(run_cli, tmp_path):
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
    assert data["specialists_decision"]["policy"] == "install-recommended"
    assert data["specialists_unavailable"][0]["skill"] == "dev-doc-writer"


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
        "dev-security-reviewer",
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
        "dev-doc-writer",
        "--first-use",
        "dev-doc-writer",
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
        "dev-backend",
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
    assert state["specialists_mode"] == "auto"
