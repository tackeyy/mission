"""Issue #31: specialist skill invocation logging."""
import json
import os
import re
import shlex
import sys


def _json_result(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _assert_unsafe_legacy_specialist_record(result, field):
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    data = json.loads(result.stdout)
    assert data["ok"] is False
    assert data["reason_code"] == "unsafe-legacy-specialist-record"
    assert data["field_path"].startswith("/specialists_candidates/0/")
    assert data["field_path"].rsplit("/", 1)[-1] in {
        "command", "args", "env", "result_contract"
    }


def _seed_legacy_command_provider_state(tmp_path, provider, *, ask_user=False):
    """Preserve the pre-#395 runtime consumer contract for an already-active state."""
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    candidate = {
        **provider,
        "skill": provider.get("skill") or provider.get("role"),
        "provider_id": provider.get("provider_id") or provider.get("skill") or provider.get("role"),
        "status": "available",
        "installed": True,
        "available": True,
    }
    if not provider.get("env") and not provider.get("result_contract"):
        command_dir = tmp_path / ".mission-test-bin"
        command_dir.mkdir(exist_ok=True)
        command_name = re.sub(
            r"[^A-Za-z0-9._+-]", "-", str(candidate["provider_id"])
        )
        wrapper = command_dir / command_name
        argv = [provider.get("command"), *(provider.get("args") or [])]
        wrapper.write_text(
            "#!/bin/sh\nexec " + " ".join(shlex.quote(str(value)) for value in argv) + "\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
        os.environ["PATH"] = f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        candidate["command"] = command_name
        candidate["args"] = []
        candidate["env"] = {}
    state["specialists_candidates"] = [candidate]
    state["specialists_selected"] = []
    state["specialists_decision"] = (
        {
            "policy": "first-use",
            "action": "ask-user",
            "prompted_user": True,
        }
        if ask_user
        else {"policy": "auto", "action": "select", "prompted_user": False}
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")


def test_init_includes_specialist_invocations(run_cli, tmp_path):
    run_cli("init", "specialist invocation mission", "--complexity", "Standard", cwd=tmp_path, check=True)

    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())

    assert state["specialist_invocations"] == []


def test_get_accepts_safe_legacy_invocation_with_bare_command(
    state_dir, run_cli, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["specialist_invocations"] = [
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
    state_path.write_text(json.dumps(state), encoding="utf-8")

    result = run_cli("get", cwd=state_dir.parent)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["specialist_invocations"][0]["command"] == (
        "portable-reviewer"
    )


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


def test_log_invocation_rejects_blank_role(state_dir, run_cli):
    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", " ",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "completed",
        cwd=state_dir.parent,
    )

    assert r.returncode != 0
    assert "--role" in r.stderr


def test_log_invocation_rejects_blank_skill(state_dir, run_cli):
    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", " ",
        "--mode", "skill-tool",
        "--status", "completed",
        cwd=state_dir.parent,
    )

    assert r.returncode != 0
    assert "--skill" in r.stderr


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
        "--skill", "documentation-provider",
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
        "--skill", "documentation-provider",
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
    assert selected["skill"] == "documentation-provider"
    assert selected["status"] == "selected"
    assert selected["selection_source"] == "user-instruction"
    assert selected["source"] == "user-instruction:log-invocation"
    assert data["selected_entry"] == selected


def test_log_invocation_task_required_selection_source_adds_selection_metadata(state_dir, run_cli, read_state):
    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "planning",
        "--role", "source-retrieval",
        "--skill", "source-retrieval-provider",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        "--selection-source", "task-required",
        "--notes", "The task required source retrieval before answering",
        "--json",
        cwd=state_dir.parent,
    )

    data = _json_result(r)
    state = read_state(state_dir)
    entry = state["specialist_invocations"][0]
    selected = state["specialists_selected"][0]
    assert entry["selection_source"] == "task-required"
    assert selected["skill"] == "source-retrieval-provider"
    assert selected["selection_source"] == "task-required"
    assert selected["source"] == "task-required:log-invocation"
    assert data["selected_entry"] == selected


def test_log_invocation_requires_selection_source_after_ask_user_confirmation(state_dir, run_cli):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "specialists_decision": {"policy": "confirm", "action": "ask-user", "prompted_user": True},
        "specialists_selected": [],
        "specialists_candidates": [
            {"role": "reviewer", "skill": "example-reviewer", "kind": "skill", "status": "available"},
        ],
    })
    state_path.write_text(json.dumps(state))

    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "reviewer",
        "--skill", "example-reviewer",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        cwd=state_dir.parent,
    )

    assert r.returncode != 0
    assert "--selection-source confirmed-user" in r.stderr


def test_log_invocation_confirmed_user_selection_resolves_ask_user_metadata(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "specialists_decision": {"policy": "confirm", "action": "ask-user", "prompted_user": True},
        "specialists_selected": [],
        "specialists_candidates": [
            {"role": "reviewer", "skill": "example-reviewer", "kind": "skill", "status": "available"},
        ],
    })
    state_path.write_text(json.dumps(state))

    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "reviewer",
        "--skill", "example-reviewer",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        "--selection-source", "confirmed-user",
        cwd=state_dir.parent,
        check=True,
    )

    state = read_state(state_dir)
    assert state["specialist_invocations"][0]["selection_source"] == "confirmed-user"
    assert state["specialists_selected"][0]["selection_source"] == "confirmed-user"


def test_log_invocation_rejects_bounded_orchestrator_execution(state_dir, run_cli):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "specialists_selected": [{
            "role": "methodology",
            "skill": "broad-methodology",
            "kind": "skill",
            "status": "selected",
            "source": "project:.mission/specialists.yml",
            "bounded_use": True,
            "bounded_purpose_required": True,
        }],
    })
    state_path.write_text(json.dumps(state))

    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "execution",
        "--role", "methodology",
        "--skill", "broad-methodology",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        "--bounded-purpose", "Produce a constrained implementation checklist only",
        cwd=state_dir.parent,
    )

    assert r.returncode != 0
    assert "cannot be applied in execution phase" in r.stderr


def test_log_invocation_requires_bounded_purpose_for_broad_orchestrator(state_dir, run_cli):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "specialists_selected": [{
            "role": "methodology",
            "skill": "broad-methodology",
            "kind": "skill",
            "status": "selected",
            "source": "project:.mission/specialists.yml",
            "bounded_use": True,
            "bounded_purpose_required": True,
        }],
    })
    state_path.write_text(json.dumps(state))

    r = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "methodology",
        "--skill", "broad-methodology",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        cwd=state_dir.parent,
    )

    assert r.returncode != 0
    assert "--bounded-purpose" in r.stderr


def test_log_invocation_records_bounded_orchestrator_purpose(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "specialists_selected": [{
            "role": "methodology",
            "skill": "broad-methodology",
            "kind": "skill",
            "status": "selected",
            "source": "project:.mission/specialists.yml",
            "bounded_use": True,
            "bounded_purpose_required": True,
        }],
    })
    state_path.write_text(json.dumps(state))

    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "methodology",
        "--skill", "broad-methodology",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        "--bounded-purpose", "Review the implementation plan only; mission owns execution",
        cwd=state_dir.parent,
        check=True,
    )

    entry = read_state(state_dir)["specialist_invocations"][0]
    assert entry["bounded_purpose"] == "Review the implementation plan only; mission owns execution"


def test_specialists_summary_reports_kind_source_and_unselected_manual(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "specialists_selected": [{
            "role": "reviewer",
            "skill": "command-reviewer",
            "kind": "command",
            "status": "selected",
            "source": "project:.mission/specialists.yml",
            "selection_source": "confirmed-user",
        }],
        "specialists_candidates": [{
            "role": "inline-reviewer",
            "skill": "inline-reviewer",
            "kind": "skill",
            "source": "user:~/.config/mission/specialists.yml",
        }],
        "specialist_invocations": [
            {
                "iteration": 1,
                "phase": "review",
                "role": "reviewer",
                "skill": "command-reviewer",
                "mode": "command-provider",
                "provider_kind": "command",
                "status": "completed",
                "selection_source": "confirmed-user",
                "timestamp": "2026-05-25T00:00:00Z",
                "evidence_path": ".mission-state/archive/review.md",
            },
            {
                "iteration": 1,
                "phase": "review",
                "role": "inline-reviewer",
                "skill": "inline-reviewer",
                "mode": "codex-inline",
                "status": "inline-applied",
                "timestamp": "2026-05-25T00:00:01Z",
            },
            {
                "iteration": 1,
                "phase": "review",
                "role": "missing-reviewer",
                "skill": "missing-reviewer",
                "mode": "fallback-core",
                "status": "unavailable",
                "reason": "Skill not callable",
                "timestamp": "2026-05-25T00:00:02Z",
            },
        ],
    })
    state_path.write_text(json.dumps(state))

    r = run_cli("specialists", "summary", "--json", cwd=state_dir.parent)
    data = _json_result(r)

    assert data["selected"][0]["kind"] == "command"
    assert data["selected"][0]["source"] == "project:.mission/specialists.yml"
    assert data["used"][0]["mode"] == "command-provider"
    assert data["used"][0]["kind"] == "command"
    assert data["used"][0]["source"] == "project:.mission/specialists.yml"
    assert data["used"][1]["skill"] == "inline-reviewer"
    assert data["used"][1]["kind"] == "skill"
    assert data["used"][1]["source"] == "user:~/.config/mission/specialists.yml"
    assert data["degraded"][0]["skill"] == "missing-reviewer"
    assert data["unselected_manual"][0]["skill"] == "inline-reviewer"

    text = run_cli("specialists", "summary", cwd=state_dir.parent, check=True)
    assert "command-reviewer[command project:.mission/specialists.yml command-provider:completed]" in text.stdout


def test_log_invocation_records_unavailable_without_evidence(state_dir, run_cli, read_state):
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "security-reviewer",
        "--skill", "security-review-provider",
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
        "--skill", "security-review-provider",
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
        "--skill", "security-review-provider",
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
        "--skill", "unit-test-provider",
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
            {"role": "doc-writer", "skill": "documentation-provider", "task_profiles": ["documentation"], "status": "available"},
            {"role": "backend", "skill": "backend-provider", "task_profiles": ["backend", "database"], "status": "available"},
            {"role": "unit-tester", "skill": "unit-test-provider", "task_profiles": ["testing", "backend"], "status": "available"},
            {"role": "infra", "skill": "infra-provider", "task_profiles": ["infra"], "status": "available"},
        ],
        "specialists_selected": [
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
        ],
        "specialist_invocations": [
            {"skill": "documentation-provider", "status": "inline-applied", "mode": "codex-inline"},
            {"skill": "infra-provider", "status": "skipped", "mode": "fallback-core", "reason": "no infra changes"},
        ],
    })
    state_path.write_text(json.dumps(state))

    r = run_cli("specialists", "accounting", "--json", cwd=state_dir.parent)

    data = _json_result(r)
    assert data["ok"] is True
    assert data["priority"] == "P1"
    assert [item["skill"] for item in data["unaccounted_candidates"]] == ["unit-test-provider"]
    assert [item["skill"] for item in data["required_unaccounted_candidates"]] == ["unit-test-provider"]


def test_specialist_accounting_accepts_explicit_skips(state_dir, run_cli):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    state.update({
        "complexity": "Complex",
        "task_profile": {"primary": "documentation", "secondary": ["testing", "infra"], "risk": "medium"},
        "specialists_candidates": [
            {"role": "doc-writer", "skill": "documentation-provider", "task_profiles": ["documentation"], "status": "available"},
            {"role": "unit-tester", "skill": "unit-test-provider", "task_profiles": ["testing", "backend"], "status": "available"},
            {"role": "infra", "skill": "infra-provider", "task_profiles": ["infra"], "status": "available"},
        ],
        "specialists_selected": [
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
        ],
        "specialist_invocations": [
            {"skill": "documentation-provider", "status": "inline-applied", "mode": "codex-inline"},
            {"skill": "unit-test-provider", "status": "skipped", "mode": "fallback-core", "reason": "focused tests cover this change"},
            {"skill": "infra-provider", "status": "skipped", "mode": "fallback-core", "reason": "no infra changes"},
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
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
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
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
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


def test_invoke_command_provider_marks_approval_marker_as_awaiting_input(run_cli, tmp_path):
    run_cli("init", "command provider approval mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text(
        "import sys\n"
        "print('approval required: browser session material consent')\n"
        "sys.exit(75)\n",
        encoding="utf-8",
    )
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "approval-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
            "result_contract": {
                "awaiting_input_markers": ["approval required:"],
            },
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "approval-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    _assert_unsafe_legacy_specialist_record(r, "result_contract")


def test_invoke_command_provider_marks_configured_exit_code_as_awaiting_input(run_cli, tmp_path):
    run_cli("init", "command provider approval exit mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text("import sys; sys.exit(75)\n", encoding="utf-8")
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "approval-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
            "result_contract": {
                "awaiting_input_exit_codes": [75],
            },
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "approval-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    _assert_unsafe_legacy_specialist_record(r, "result_contract")


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
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "browser-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    _assert_unsafe_legacy_specialist_record(r, "result_contract")


def test_invoke_command_provider_rejects_preparation_marker_even_with_long_output(run_cli, tmp_path):
    run_cli("init", "command provider prepared long mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text(
        "print('Oracle Browser Review Prepared')\n"
        "print('x' * 500)\n",
        encoding="utf-8",
    )
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "oracle-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
            "result_contract": {"min_non_template_chars": 20},
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "oracle-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    _assert_unsafe_legacy_specialist_record(r, "result_contract")


def test_invoke_command_provider_requires_confirmed_selection_after_ask_user(run_cli, tmp_path):
    run_cli("init", "command provider ask user mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text("print('finding: review evidence is complete and actionable')\n", encoding="utf-8")
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "paid-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--first-use", "paid-reviewer",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path,
        json.loads(registry.read_text())["specialists"][0],
        ask_user=True,
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "paid-reviewer",
        "--iteration", "1",
        "--phase", "review",
        cwd=tmp_path,
    )

    assert r.returncode != 0
    assert "--selection-source confirmed-user" in r.stderr


def test_invoke_command_provider_persists_confirmed_selection_after_ask_user(run_cli, tmp_path):
    run_cli("init", "command provider confirmed mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text("print('finding: review evidence is complete and actionable')\n", encoding="utf-8")
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "paid-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "task_profiles": ["documentation"],
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--first-use", "paid-reviewer",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path,
        json.loads(registry.read_text())["specialists"][0],
        ask_user=True,
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "paid-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--selection-source", "confirmed-user",
        "--json",
        cwd=tmp_path,
    )

    data = _json_result(r)
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert data["ok"] is True
    assert state["specialist_invocations"][0]["selection_source"] == "confirmed-user"
    assert state["specialists_selected"][0]["selection_source"] == "confirmed-user"


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
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "evidence-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    _assert_unsafe_legacy_specialist_record(r, "result_contract")


def test_invoke_command_provider_uses_registry_env_and_timeout(run_cli, tmp_path):
    run_cli("init", "command provider env mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text(
        "import os\n"
        "print(os.environ['REVIEW_TEXT'])\n",
        encoding="utf-8",
    )
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "env-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "env": {
                "REVIEW_TEXT": "finding: registry env reached the provider with substantive review evidence",
            },
            "timeout": 17,
            "task_profiles": ["documentation"],
            "result_contract": {"min_non_template_chars": 40},
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path, json.loads(registry.read_text())["specialists"][0]
    )

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "env-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
    )

    _assert_unsafe_legacy_specialist_record(r, "env")


def test_confirmed_command_provider_selection_preserves_invocation_config(run_cli, tmp_path):
    run_cli("init", "command provider selected config mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text(
        "import os\n"
        "print(os.environ['REVIEW_TEXT'])\n",
        encoding="utf-8",
    )
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "paid-reviewer",
            "kind": "command",
            "command": sys.executable,
            "args": [str(helper)],
            "env": {
                "REVIEW_TEXT": "finding: selected command provider can run again with preserved config",
            },
            "timeout": 19,
            "task_profiles": ["documentation"],
            "result_contract": {"min_non_template_chars": 40},
        }],
    }))
    run_cli(
        "specialists", "recommend",
        "--no-default-skill-roots",
        "--task", "Review README documentation",
        "--complexity", "Complex",
        "--first-use", "paid-reviewer",
        "--record-state",
        "--json",
        cwd=tmp_path,
        check=True,
    )
    _seed_legacy_command_provider_state(
        tmp_path,
        json.loads(registry.read_text())["specialists"][0],
        ask_user=True,
    )
    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "paid-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--selection-source", "confirmed-user",
        "--json",
        cwd=tmp_path,
    )
    _assert_unsafe_legacy_specialist_record(r, "env")
