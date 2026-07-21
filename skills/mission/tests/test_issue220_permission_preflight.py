"""Issue #220: non-interactive startup must fail closed on write denial."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_state_module():
    spec = importlib.util.spec_from_file_location(
        "mission_state_issue220", MISSION_STATE_PY
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_permission_preflight_passes_when_state_and_assumptions_are_writable(
    tmp_path, run_cli
):
    run_cli(
        "init",
        "permission preflight",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )

    result = run_cli("permission-preflight", "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["ok"] is True
    assert output["halt_recorded"] is False
    assert {probe["target"] for probe in output["probes"]} == {
        "state",
        "assumptions",
    }


def test_permission_preflight_halts_without_question_when_assumptions_write_fails(
    tmp_path, run_cli
):
    run_cli(
        "init",
        "permission preflight",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )
    assumptions = (
        tmp_path / ".mission-state" / "sessions" / "test-assumptions.md"
    )
    assumptions.chmod(0o400)
    try:
        result = run_cli("permission-preflight", "--json", cwd=tmp_path)
    finally:
        assumptions.chmod(0o600)

    assert result.returncode == 2
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["halt_recorded"] is True
    assert output["halt_category"] == "blocked-external"
    assert output["probes"][-1] == {
        "target": "assumptions",
        "ok": False,
        "error": "write-unavailable",
    }
    assert "approval" not in (result.stdout + result.stderr).lower()
    assert "承認" not in result.stdout + result.stderr

    state = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text()
    )
    assert state["loop_active"] is False
    assert state["phase"] == "halted"
    assert state["halt_category"] == "blocked-external"


def test_permission_preflight_emits_fallback_evidence_when_state_write_fails(
    tmp_path, run_cli
):
    run_cli(
        "init",
        "permission preflight",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.chmod(0o500)
    try:
        result = run_cli("permission-preflight", "--json", cwd=tmp_path)
    finally:
        sessions.chmod(0o700)

    assert result.returncode == 2
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["halt_recorded"] is False
    assert output["halt_category"] == "blocked-external"
    assert output["probes"] == [
        {"target": "state", "ok": False, "error": "write-unavailable"}
    ]
    assert "approval" not in (result.stdout + result.stderr).lower()
    assert "承認" not in result.stdout + result.stderr


def test_init_runs_permission_preflight_before_returning_success(
    tmp_path, monkeypatch
):
    module = _load_state_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")

    def deny_assumptions_write(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(module, "_probe_file_write", deny_assumptions_write)
    args = SimpleNamespace(
        mission="permission preflight",
        complexity="Standard",
        threshold=4.0,
        max_iter=None,
        issue_ref=None,
        files=None,
        review_tier=None,
    )

    with pytest.raises(SystemExit) as exc:
        module.cmd_init(args)

    assert exc.value.code == 2
    state = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text()
    )
    assert state["halt_category"] == "blocked-external"
    assert state["loop_active"] is False


def test_skill_allows_only_state_cli_and_forbids_questions_on_preflight_failure():
    paths = (
        REPO_ROOT / "skills" / "mission" / "SKILL.md",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "SKILL.md",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        assert "allowed-tools:" in frontmatter
        bash_rules = {
            line.strip().removeprefix("- ")
            for line in frontmatter.splitlines()
            if line.strip().startswith("- Bash(")
        }
        assert bash_rules == {
            "Bash(scripts/mission-state.py init:*)",
            "Bash(scripts/mission-state.py permission-preflight:*)",
            "Bash(${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py init:*)",
            "Bash(${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py permission-preflight:*)",
        }
        assert "Bash(python3:*)" not in frontmatter
        assert "Bash(*:*)" not in frontmatter
        assert "Bash(scripts/mission-state.py:*)" not in frontmatter
        assert "specialists invoke-command" not in frontmatter

        compact = text.split("## Compact Instructions", 1)[1].split(
            "## state.json 操作", 1
        )[0]
        assert "permission-preflight --json" in compact
        assert "blocked-external" in compact
        assert "質問" in compact


def test_permission_preflight_rejects_assumptions_path_outside_state_root(
    tmp_path, run_cli
):
    run_cli(
        "init",
        "permission preflight",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    outside = tmp_path / "outside.md"
    outside.write_text("must stay unchanged\n")
    state["assumptions_path"] = "outside.md"
    state_path.write_text(json.dumps(state))

    result = run_cli("permission-preflight", "--json", cwd=tmp_path)

    assert result.returncode == 2
    output = json.loads(result.stdout)
    assert output["ok"] is False
    assert output["halt_recorded"] is True
    assert output["halt_category"] == "blocked-external"
    assert output["probes"][-1] == {
        "target": "assumptions",
        "ok": False,
        "error": "invalid-evidence-path",
    }
    assert outside.read_text() == "must stay unchanged\n"


def test_init_emits_structured_fallback_when_first_state_write_fails(
    tmp_path, monkeypatch, capsys
):
    module = _load_state_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")

    def deny_state_write(_path, _data):
        raise PermissionError("denied")

    monkeypatch.setattr(module, "atomic_write_json", deny_state_write)
    args = SimpleNamespace(
        mission="permission preflight",
        complexity="Standard",
        threshold=4.0,
        max_iter=None,
        issue_ref=None,
        files=None,
        review_tier=None,
    )

    with pytest.raises(SystemExit) as exc:
        module.cmd_init(args)

    assert exc.value.code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["halt_recorded"] is False
    assert output["halt_category"] == "blocked-external"
    assert output["probes"] == [
        {"target": "state", "ok": False, "error": "write-unavailable"}
    ]


def test_permission_preflight_emits_fallback_when_state_lock_write_fails(
    tmp_path, monkeypatch, capsys
):
    module = _load_state_module()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    args = SimpleNamespace(
        mission="permission preflight",
        complexity="Standard",
        threshold=4.0,
        max_iter=None,
        issue_ref=None,
        files=None,
        review_tier=None,
    )
    module.cmd_init(args)
    capsys.readouterr()

    class DeniedStateLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            raise PermissionError("denied")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(module, "StateLock", DeniedStateLock)

    with pytest.raises(SystemExit) as exc:
        module.cmd_permission_preflight(SimpleNamespace(json=True))

    assert exc.value.code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is False
    assert output["halt_recorded"] is False
    assert output["halt_category"] == "blocked-external"
    assert output["probes"] == [
        {"target": "state", "ok": False, "error": "write-unavailable"}
    ]
