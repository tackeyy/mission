"""Issue #31: specialist skill invocation logging."""
import json
import sys


def _json_result(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_init_includes_specialist_invocations(run_cli, tmp_path):
    run_cli("init", "specialist invocation mission", "--complexity", "Standard", cwd=tmp_path, check=True)

    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    assert state["specialist_invocations"] == []


def test_log_invocation_appends_machine_readable_record(state_dir, run_cli, read_state):
    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "completed",
        "--notes", "Reviewed diff; no blocking issues",
        "--json",
        cwd=state_dir.parent,
    )

    data = _json_result(r)
    state = read_state(state_dir)
    entry = state["specialist_invocations"][0]
    assert data["ok"] is True
    assert data["entry"] == entry
    assert entry["iteration"] == 1
    assert entry["phase"] == "review"
    assert entry["skill"] == "dev-code-reviewer"
    assert entry["mode"] == "skill-tool"
    assert entry["status"] == "completed"
    assert entry["notes"] == "Reviewed diff; no blocking issues"
    assert entry["timestamp"].endswith("Z")


def test_log_invocation_archives_evidence_with_metadata(state_dir, run_cli, tmp_path, read_state):
    evidence = tmp_path / "review.md"
    evidence.write_text("# Specialist Review\n\nNo blocking issues.\n", encoding="utf-8")

    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "completed",
        "--evidence-output", str(evidence),
        cwd=state_dir.parent,
        check=True,
    )

    archived = state_dir / "archive" / "iter-1-abc12345-specialist-dev-code-reviewer.md"
    content = archived.read_text(encoding="utf-8")
    entry = read_state(state_dir)["specialist_invocations"][0]
    assert archived.exists()
    assert "session_id=test" in content
    assert "mission_id=abc12345" in content
    assert "skill=dev-code-reviewer" in content
    assert "status=completed" in content
    assert "No blocking issues." in content
    assert entry["evidence_path"] == ".mission-state/archive/iter-1-abc12345-specialist-dev-code-reviewer.md"


def test_log_invocation_records_codex_inline_usage(state_dir, run_cli, read_state):
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "planning",
        "--role", "doc-writer",
        "--skill", "dev-doc-writer",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        cwd=state_dir.parent,
        check=True,
    )

    entry = read_state(state_dir)["specialist_invocations"][0]
    assert entry["mode"] == "codex-inline"
    assert entry["status"] == "inline-applied"


def test_log_invocation_selection_source_adds_selection_metadata(state_dir, run_cli, read_state):
    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "planning",
        "--role", "doc-writer",
        "--skill", "dev-doc-writer",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        "--selection-source", "user-instruction",
        "--notes", "User explicitly requested this specialist",
        "--json",
        cwd=state_dir.parent,
    )

    data = _json_result(r)
    state = read_state(state_dir)
    entry = state["specialist_invocations"][0]
    selected = state["specialists_selected"][0]
    assert entry["selection_source"] == "user-instruction"
    assert selected["skill"] == "dev-doc-writer"
    assert selected["status"] == "selected"
    assert selected["selection_source"] == "user-instruction"
    assert selected["source"] == "user-instruction:log-invocation"
    assert data["selected_entry"] == selected


def test_log_invocation_records_unavailable_without_evidence(state_dir, run_cli, read_state):
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "security-reviewer",
        "--skill", "dev-security-reviewer",
        "--mode", "fallback-core",
        "--status", "unavailable",
        "--notes", "Skill is not callable in this environment",
        cwd=state_dir.parent,
        check=True,
    )

    entry = read_state(state_dir)["specialist_invocations"][0]
    assert entry["status"] == "unavailable"
    assert entry["reason"] == "Skill is not callable in this environment"
    assert "evidence_path" not in entry


def test_log_invocation_records_skipped_with_reason(state_dir, run_cli, read_state):
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "planning",
        "--role", "security-reviewer",
        "--skill", "dev-security-reviewer",
        "--mode", "fallback-core",
        "--status", "skipped",
        "--reason", "Core reviewer covered the security checklist for this low-risk docs-only change",
        cwd=state_dir.parent,
        check=True,
    )

    entry = read_state(state_dir)["specialist_invocations"][0]
    assert entry["status"] == "skipped"
    assert entry["reason"] == "Core reviewer covered the security checklist for this low-risk docs-only change"


def test_log_invocation_rejects_skipped_without_reason(state_dir, run_cli):
    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "planning",
        "--role", "security-reviewer",
        "--skill", "dev-security-reviewer",
        "--mode", "fallback-core",
        "--status", "skipped",
        cwd=state_dir.parent,
    )

    assert r.returncode != 0
    assert "判断理由" in r.stderr


def test_log_invocation_records_failed_attempt(state_dir, run_cli, read_state):
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "unit-tester",
        "--skill", "dev-unit-tester",
        "--mode", "skill-tool",
        "--status", "failed",
        "--reason", "Skill subprocess exited before producing review evidence",
        cwd=state_dir.parent,
        check=True,
    )

    entry = read_state(state_dir)["specialist_invocations"][0]
    assert entry["status"] == "failed"
    assert entry["reason"] == "Skill subprocess exited before producing review evidence"


def test_log_invocation_accepts_skill_tool_applied_status(state_dir, run_cli, read_state):
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "skill-tool-applied",
        cwd=state_dir.parent,
        check=True,
    )

    entry = read_state(state_dir)["specialist_invocations"][0]
    assert entry["mode"] == "skill-tool"
    assert entry["status"] == "skill-tool-applied"


def test_log_invocation_accepts_prepared_with_reason(state_dir, run_cli, read_state):
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "external-reviewer",
        "--skill", "external-reviewer",
        "--mode", "fallback-core",
        "--status", "prepared",
        "--reason", "Provider prepared the browser session but did not return findings",
        cwd=state_dir.parent,
        check=True,
    )

    entry = read_state(state_dir)["specialist_invocations"][0]
    assert entry["status"] == "prepared"
    assert entry["reason"] == "Provider prepared the browser session but did not return findings"


def test_specialist_accounting_reports_only_required_complex_candidates(state_dir, run_cli):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "complexity": "Complex",
        "task_profile": {"primary": "documentation", "secondary": ["testing", "infra"], "risk": "medium"},
        "specialists_candidates": [
            {"role": "doc-writer", "skill": "dev-doc-writer", "task_profiles": ["documentation"], "status": "available"},
            {"role": "backend", "skill": "dev-backend", "task_profiles": ["backend", "database"], "status": "available"},
            {"role": "unit-tester", "skill": "dev-unit-tester", "task_profiles": ["testing", "backend"], "status": "available"},
            {"role": "infra", "skill": "dev-infra", "task_profiles": ["infra"], "status": "available"},
        ],
        "specialists_selected": [
            {"role": "doc-writer", "skill": "dev-doc-writer", "status": "selected"},
        ],
        "specialist_invocations": [
            {"skill": "dev-doc-writer", "status": "inline-applied", "mode": "codex-inline"},
            {"skill": "dev-infra", "status": "skipped", "mode": "fallback-core", "reason": "no infra changes"},
        ],
    })
    state_path.write_text(json.dumps(state))

    r = run_cli("specialists", "accounting", "--json", cwd=state_dir.parent)

    data = _json_result(r)
    assert data["ok"] is True
    assert data["priority"] == "P1"
    assert [item["skill"] for item in data["unaccounted_candidates"]] == ["dev-unit-tester"]
    assert [item["skill"] for item in data["required_unaccounted_candidates"]] == ["dev-unit-tester"]


def test_specialist_accounting_accepts_explicit_skips(state_dir, run_cli):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "complexity": "Complex",
        "task_profile": {"primary": "documentation", "secondary": ["testing", "infra"], "risk": "medium"},
        "specialists_candidates": [
            {"role": "doc-writer", "skill": "dev-doc-writer", "task_profiles": ["documentation"], "status": "available"},
            {"role": "unit-tester", "skill": "dev-unit-tester", "task_profiles": ["testing", "backend"], "status": "available"},
            {"role": "infra", "skill": "dev-infra", "task_profiles": ["infra"], "status": "available"},
        ],
        "specialists_selected": [
            {"role": "doc-writer", "skill": "dev-doc-writer", "status": "selected"},
        ],
        "specialist_invocations": [
            {"skill": "dev-doc-writer", "status": "inline-applied", "mode": "codex-inline"},
            {"skill": "dev-unit-tester", "status": "skipped", "mode": "fallback-core", "reason": "focused tests cover this change"},
            {"skill": "dev-infra", "status": "skipped", "mode": "fallback-core", "reason": "no infra changes"},
        ],
    })
    state_path.write_text(json.dumps(state))

    r = run_cli("specialists", "accounting", "--json", cwd=state_dir.parent)

    data = _json_result(r)
    assert data["priority"] is None
    assert data["unaccounted_candidates"] == []
    assert data["required_unaccounted_candidates"] == []


def test_log_invocation_rejects_unknown_status(state_dir, run_cli):
    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "mystery",
        cwd=state_dir.parent,
    )

    assert r.returncode != 0


def test_invoke_command_provider_archives_evidence_and_logs_invocation(run_cli, tmp_path):
    run_cli("init", "command provider mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text(
        "import json, sys\n"
        "packet = json.loads(sys.stdin.read())\n"
        "print('phase=' + packet['phase'])\n"
        "print('body=' + packet['input'])\n",
        encoding="utf-8",
    )
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "fake-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
            "phases": ["review"],
        }],
    }))
    context = tmp_path / "context.txt"
    context.write_text("review this diff", encoding="utf-8")

    run_cli(
        "specialists", "recommend",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "fake-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--input-file", str(context),
        "--json",
        cwd=tmp_path,
    )

    data = _json_result(r)
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    entry = state["specialist_invocations"][0]
    evidence = tmp_path / entry["evidence_path"]
    assert data["ok"] is True
    assert entry["mode"] == "command-provider"
    assert entry["status"] == "completed"
    assert entry["provider_kind"] == "command"
    assert entry["exit_code"] == 0
    assert evidence.exists()
    content = evidence.read_text(encoding="utf-8")
    assert "phase=review" in content
    assert "body=review this diff" in content


def test_invoke_command_provider_records_failure_without_blocking_optional_provider(run_cli, tmp_path):
    run_cli("init", "command provider failure mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text("import sys; print('bad token=abc123'); sys.exit(7)\n", encoding="utf-8")
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "failing-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "failing-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    data = _json_result(r)
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    entry = state["specialist_invocations"][0]
    evidence = tmp_path / entry["evidence_path"]
    assert r.returncode == 0
    assert data["ok"] is False
    assert entry["status"] == "failed"
    assert entry["exit_code"] == 7
    assert "status 7" in entry["reason"]
    assert "token=[REDACTED]" in evidence.read_text(encoding="utf-8")


def test_invoke_command_provider_marks_preparation_only_output_as_not_applied(run_cli, tmp_path):
    run_cli("init", "command provider prepared mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text("print('Oracle Browser Review Prepared')\n", encoding="utf-8")
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "browser-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
            "result_contract": {"min_non_template_chars": 20},
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "browser-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    data = _json_result(r)
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    entry = state["specialist_invocations"][0]
    assert data["ok"] is False
    assert entry["status"] == "prepared"
    assert "preparation-only" in entry["reason"]


def test_invoke_command_provider_accepts_result_contract_evidence(run_cli, tmp_path):
    run_cli("init", "command provider evidence mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text("print('finding: implementation is sound and tests cover the changed gate behavior')\n", encoding="utf-8")
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "evidence-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
            "result_contract": {"min_non_template_chars": 40},
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "evidence-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    data = _json_result(r)
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    entry = state["specialist_invocations"][0]
    assert data["ok"] is True
    assert entry["status"] == "completed"
