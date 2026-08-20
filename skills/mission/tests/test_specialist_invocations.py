"""Issue #31: specialist skill invocation logging."""
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import re
import shlex
import sys

import pytest

from provider_eligibility import evaluate_provider_eligibility, value_digest


@pytest.fixture
def run_cli(legacy_run_cli):
    """Specialist invocation ownership remains on retained v4 until #543."""
    return legacy_run_cli


def _json_result(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _load_mission_state_module(name):
    state_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, state_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


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
    """Seed a current selected command-provider contract for runtime tests."""
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
    if not candidate.get("phases"):
        candidate["phases"] = ["planning", "review"]
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
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    for item in state["specialists_candidates"]:
        item["selection_id"] = selection_id
    state["specialists_selected"] = [] if ask_user else [dict(candidate)]
    provider_phase = "review" if "review" in candidate["phases"] else candidate["phases"][0]
    state["phase"] = {
        "planning": "planning",
        "execution": "executing",
        "review": "reviewing",
        "scoring": "scoring",
        "critic": "critic",
    }[provider_phase]
    state["specialists_decision"] = (
        {
            "policy": "first-use",
            "action": "ask-user",
            "prompted_user": True,
            "decision": "none",
            "reason_code": "pending-confirmation",
            "lifecycle_state": "terminal",
            "selection_id": selection_id,
        }
        if ask_user
        else {
            "policy": "auto", "action": "select", "prompted_user": False,
            "decision": "selected", "reason_code": "candidate-selected",
            "lifecycle_state": "selected", "selection_id": selection_id,
        }
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _set_current_bounded_provider(state, *, provider_phase):
    """Install the typed selection checkpoint required by application paths."""
    activation = {
        "min_complexity": "Standard",
        "auto_select_if": ["complexity"],
        "explicit_below_min": "deny",
    }
    candidate = {
        "provider_id": "broad-methodology",
        "role": "methodology",
        "skill": "broad-methodology",
        "kind": "skill",
        "phases": [provider_phase],
        "activation": activation,
        "available": True,
    }
    context = {
        "complexity": state["complexity"],
        "task_profile": {},
        "iteration": state["iteration"],
        "previous_iteration_passed": None,
    }
    eligibility = evaluate_provider_eligibility(
        candidate,
        context,
        requested_phase=provider_phase,
        selection_source="automatic",
    )
    selection_id = "sel_0123456789abcdef0123456789abcdef"
    state.update({
        "phase": {"execution": "executing", "review": "reviewing"}[provider_phase],
        "specialists_decision": {
            "decision": "selected",
            "action": "select",
            "selection_id": selection_id,
        },
        "specialists_selected": [{
            **candidate,
            "status": "selected",
            "source": "project:.mission/specialists.yml",
            "bounded_use": True,
            "bounded_purpose_required": True,
            "selection_id": selection_id,
            "registry_entry_digest": value_digest({"provider_id": "broad-methodology"}),
            "registry_projection_digest": value_digest({"providers": ["broad-methodology"]}),
            "context_digest": eligibility["context_digest"],
            "activation_digest": eligibility["activation_digest"],
            "normalized_activation": eligibility["normalized_activation"],
            "eligibility_selection_source": "automatic",
        }],
    })
    state["specialists_selected"][0].pop("activation", None)


def _expected_specialist_archive_path(state_dir, entry):
    return state_dir / "archive" / (
        f"iter-{entry['iteration']}-abc12345-{entry['invocation_id']}-specialist-"
        f"{entry['skill']}.md"
    )


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

    entry = read_state(state_dir)["specialist_invocations"][0]
    archived = _expected_specialist_archive_path(state_dir, entry)
    content = archived.read_text(encoding="utf-8")
    assert archived.exists()
    assert "session_id=test" in content
    assert "mission_id=abc12345" in content
    assert "skill=dev-code-reviewer" in content
    assert "status=completed" in content
    assert "No blocking issues." in content
    assert entry["content_digest"] == "sha256:" + hashlib.sha256(
        archived.read_bytes()
    ).hexdigest()
    assert entry["evidence_path"] == (
        f".mission-state/archive/iter-1-abc12345-{entry['invocation_id']}-specialist-"
        "dev-code-reviewer.md"
    )


def test_log_invocation_preserves_both_evidence_archives_for_same_skill_and_iteration(
    state_dir, run_cli, tmp_path, read_state
):
    first = tmp_path / "review-1.md"
    first.write_text("# Specialist Review\n\nFirst pass.\n", encoding="utf-8")
    second = tmp_path / "review-2.md"
    second.write_text("# Specialist Review\n\nSecond pass.\n", encoding="utf-8")

    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "completed",
        "--evidence-output", str(first),
        cwd=state_dir.parent,
        check=True,
    )
    run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "completed",
        "--evidence-output", str(second),
        cwd=state_dir.parent,
        check=True,
    )

    state = read_state(state_dir)
    assert len(state["specialist_invocations"]) == 2
    first_entry, second_entry = state["specialist_invocations"]
    first_archive = _expected_specialist_archive_path(state_dir, first_entry)
    second_archive = _expected_specialist_archive_path(state_dir, second_entry)
    assert first_archive.exists()
    assert second_archive.exists()
    assert first_archive != second_archive
    assert first_archive.read_text(encoding="utf-8") != second_archive.read_text(encoding="utf-8")
    assert first_entry["content_digest"] == "sha256:" + hashlib.sha256(
        first_archive.read_bytes()
    ).hexdigest()
    assert second_entry["content_digest"] == "sha256:" + hashlib.sha256(
        second_archive.read_bytes()
    ).hexdigest()


def test_legacy_evidence_path_is_readable_without_rewrite(state_dir, run_cli, read_state):
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["specialist_invocations"] = [
        {
            "invocation_id": "inv_0123456789abcdef0123456789abcdef",
            "iteration": 1,
            "phase": "review",
            "role": "reviewer",
            "skill": "dev-code-reviewer",
            "mode": "skill-tool",
            "status": "completed",
            "lifecycle_state": "terminal",
            "timestamp": "2026-08-10T00:00:00Z",
            "evidence_path": ".mission-state/archive/iter-1-abc12345-specialist-dev-code-reviewer.md",
        }
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    summary = run_cli("specialists", "summary", "--json", cwd=state_dir.parent)
    accounting = run_cli("specialists", "accounting", "--json", cwd=state_dir.parent)

    assert summary.returncode == 0, summary.stderr
    assert accounting.returncode == 0, accounting.stderr
    assert state_path.read_bytes() == before


def test_log_invocation_rejects_rewrite_when_archive_path_already_exists(
    state_dir, run_cli, tmp_path, read_state
):
    state_path = state_dir / "sessions" / "test.json"
    evidence = tmp_path / "rewrite.md"
    evidence.write_text("# Specialist Review\n\nRewrite attempt.\n", encoding="utf-8")
    state = read_state(state_dir)
    state["specialist_invocations"] = [
        {
            "invocation_id": "inv_0123456789abcdef0123456789abcdef",
            "iteration": 1,
            "phase": "review",
            "role": "code-reviewer",
            "skill": "dev-code-reviewer",
            "mode": "skill-tool",
            "status": "started",
            "lifecycle_state": "invoked",
            "timestamp": "2026-08-10T00:00:00Z",
        }
    ]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    archive_path = _expected_specialist_archive_path(
        state_dir,
        {
            "iteration": 1,
            "invocation_id": "inv_0123456789abcdef0123456789abcdef",
            "skill": "dev-code-reviewer",
        },
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text("original archive", encoding="utf-8")
    state_before = state_path.read_bytes()
    archive_before = archive_path.read_text(encoding="utf-8")

    result = run_cli(
        "specialists", "log-invocation",
        "--invocation-id", "inv_0123456789abcdef0123456789abcdef",
        "--iteration", "1",
        "--phase", "review",
        "--role", "code-reviewer",
        "--skill", "dev-code-reviewer",
        "--mode", "skill-tool",
        "--status", "completed",
        "--evidence-output", str(evidence),
        cwd=state_dir.parent,
    )

    assert result.returncode != 0
    assert state_path.read_bytes() == state_before
    assert archive_path.read_text(encoding="utf-8") == archive_before


def test_log_invocation_preflights_pending_entry_before_archive_or_state_side_effects(
    state_dir, run_cli, tmp_path
):
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    backup_path = state_path.with_suffix(".json.bak")
    backup_path.unlink(missing_ok=True)
    evidence = tmp_path / "pending-review.md"
    evidence.write_text("private review body", encoding="utf-8")
    archive_dir = state_dir / "archive"
    artifacts_before = set(archive_dir.glob("*")) if archive_dir.exists() else set()
    private_skill = "/home/portable-user/private-provider"

    result = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "reviewer",
        "--skill", private_skill,
        "--mode", "skill-tool",
        "--status", "completed",
        "--selection-source", "manual",
        "--evidence-output", str(evidence),
        "--json",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert private_skill not in result.stdout
    assert private_skill not in result.stderr
    assert state_path.read_bytes() == state_before
    assert not backup_path.exists()
    artifacts_after = set(archive_dir.glob("*")) if archive_dir.exists() else set()
    assert artifacts_after == artifacts_before


@pytest.mark.parametrize(
    "private_note",
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
        "finding path=C:private.txt",
        "finding metadata='D:.env'",
        "finding path=c:private.txt",
        "finding link=https://exa%mple/C:private.txt",
        "finding link=https://example.test,local=/home/portable-user/secret.txt",
        "finding link=https://example.test:bad/home/portable-user/secret.txt",
        r"finding link=https://example.test/path\home\portable-user\private.txt",
        "finding link=https://example.test/path?locator=|C:private.txt",
        "finding link=https://example.test/path#locator={C:private.txt}",
        "finding file=file:relative/private.txt",
        "finding file=file://server/share/private.txt",
        "finding home=~",
        "finding home='~portable-user'",
        "finding home=~+",
        "finding home=~-",
        "finding path=~portable-user/private.txt",
        r"finding path=~portable-user\private.txt",
    ],
)
def test_log_invocation_cli_rejects_embedded_private_locator_without_disclosure(
    state_dir, run_cli, private_note
):
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()

    result = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "reviewer",
        "--skill", "portable-provider",
        "--mode", "skill-tool",
        "--status", "completed",
        "--notes", private_note,
        "--json",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert private_note not in result.stdout
    assert private_note not in result.stderr
    assert state_path.read_bytes() == state_before


@pytest.mark.parametrize(
    "private_text",
    [
        "path=/home/portable-user/private.txt",
        "path:/root/private.txt",
        "path=/tmp/private.txt",
        r"path=C:\\Users\\portable-user\\private.txt",
        "path=~/private.txt",
        r"path=\\server\share\private.txt",
        r"path=\\?\C:\private\file.txt",
        r"path=\\.\PhysicalDrive0",
        r"path=\Device\HarddiskVolume1\private.txt",
        "path=//server/share/private.txt",
        r"path=C:relative\private.txt",
        "path=D:relative/private.txt",
        "path=C:private.txt",
        "metadata='D:.env'",
        "path=c:private.txt",
        "link=https://exa%mple/C:private.txt",
        "link=https://example.test,local=/home/portable-user/secret.txt",
        "link=https://example.test:bad/home/portable-user/secret.txt",
        r"link=https://example.test/path\home\portable-user\private.txt",
        "link=https://example.test/path?locator=|C:private.txt",
        "link=https://example.test/path#locator={C:private.txt}",
        "file=file:relative/private.txt",
        "file=file://server/share/private.txt",
        "home=~",
        "home='~portable-user'",
        "home=~+",
        "home=~-",
        "path=~portable-user/private.txt",
        r"path=~portable-user\private.txt",
    ],
)
def test_provider_output_redactor_covers_every_private_locator_separator(private_text):
    module = _load_mission_state_module("mission_state_issue394_path_redactor")

    redacted = module._redact_provider_output(f"finding {private_text}")

    assert private_text not in redacted
    assert "[REDACTED_PATH]" in redacted


# provider の stdout/stderr は state と成果物へ転記される。`key=value` の形を
# とらない裸のトークン (CLI が "Using sk-... " のように出す等) が素通りすると、
# 一度の実行ミスで credential が公開リポジトリの artifact に固定される。
#
# fixture は実形式を模した合成値。値はすべて反復文字で、どの provider でも
# 実在しない。ただし形が本物なので secret scanner が反応する。行単位の
# `gitleaks:allow` で個別に除外している (ファイル全体の allowlist にすると、
# 後から同じファイルへ本物が混入しても検出されなくなる)。
@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-" + "A" * 80,
        "sk-or-v1-" + "b" * 64,
        "xai-" + "C" * 80,
        "xoxb-1234567890-1234567890123-" + "d" * 24,  # gitleaks:allow
        "ghp_" + "E" * 36,
        "github_pat_" + "F" * 22 + "_" + "g" * 59,
        "AIza" + "H" * 35,
        "GOCSPX-" + "i" * 28,
        "ntn_" + "J" * 46,
        "sbp_" + "0123456789abcdef" * 2 + "01234567",
        "AKIA" + "K" * 16,
        "sk_live_" + "L" * 24,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "M" * 43,  # gitleaks:allow
    ],
)
def test_provider_output_redactor_removes_bare_credential_tokens(secret):
    module = _load_mission_state_module("mission_state_bare_token_redactor")

    redacted = module._redact_provider_output(f"provider said: {secret} done")

    assert secret not in redacted, "裸のトークンが素通りしている"
    assert "[REDACTED]" in redacted


def test_provider_output_redactor_leaves_pem_body_out_of_the_output():
    module = _load_mission_state_module("mission_state_pem_redactor")
    pem = "-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBg\n-----END PRIVATE KEY-----"  # gitleaks:allow

    redacted = module._redact_provider_output(f"leaked:\n{pem}")

    assert "MIIBVgIBADANBg" not in redacted
    assert "[REDACTED]" in redacted


def test_provider_output_redactor_handles_unterminated_pem_block():
    """END marker を欠く PEM も落とす。

    provider の出力は切り詰められることがあり、その場合 BEGIN だけが残って
    本体が続く。対を要求すると、まさに異常系で鍵が素通りする。
    """
    module = _load_mission_state_module("mission_state_unterminated_pem")
    truncated = "-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBg\n"  # gitleaks:allow

    redacted = module._redact_provider_output(f"leaked:\n{truncated}")

    assert "MIIBVgIBADANBg" not in redacted
    assert "[REDACTED]" in redacted


@pytest.mark.parametrize(
    "secret",
    [
        "xapp-1-A0123456789-1234567890123-" + "a" * 64,  # Slack app-level, gitleaks:allow
        "npm_" + "b" * 36,
        "dckr_pat_" + "c" * 36,
    ],
)
def test_provider_output_redactor_covers_additional_provider_prefixes(secret):
    module = _load_mission_state_module("mission_state_more_prefixes")

    redacted = module._redact_provider_output(f"provider said: {secret}")

    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_provider_output_redactor_requires_a_token_boundary():
    """語の途中に prefix が現れただけの文字列を潰さない（偽陽性の抑制）。"""
    module = _load_mission_state_module("mission_state_token_boundary")
    text = "package mysk-antenna-utils is unrelated"

    assert module._redact_provider_output(text) == text


def test_provider_output_redactor_removes_an_overlong_google_key_entirely():
    """固定長で切ると、規格より長い値の末尾が残る。"""
    module = _load_mission_state_module("mission_state_overlong_google_key")
    overlong = "AIza" + "H" * 40

    redacted = module._redact_provider_output(f"key={overlong}")

    assert "H" not in redacted


@pytest.mark.parametrize(
    "benign",
    [
        "score 4.31 で pass",
        "commit 3d3c42e5aac5ba805825da76410c181273ba90b1 を検証",
        "docs/VERSIONING.md を更新",
        "mission-state.py advance --to reviewing",
        "sk not a key",
    ],
)
def test_provider_output_redactor_keeps_benign_text_intact(benign):
    """過剰 redaction は証跡を壊す。通常の実行ログは素通しでなければならない。"""
    module = _load_mission_state_module("mission_state_benign_redactor")

    assert module._redact_provider_output(benign) == benign


def test_log_invocation_rolls_back_staged_archive_when_state_publish_fails(
    state_dir, tmp_path, monkeypatch
):
    module = _load_mission_state_module("mission_state_issue394_archive_rollback")
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    evidence = tmp_path / "rollback-review.md"
    evidence.write_text("portable review body", encoding="utf-8")
    archive_dir = state_dir / "archive"
    artifacts_before = set(archive_dir.glob("*")) if archive_dir.exists() else set()
    args = module._build_parser().parse_args(
        [
            "specialists", "log-invocation",
            "--iteration", "1",
            "--phase", "review",
            "--role", "reviewer",
            "--skill", "portable-provider",
            "--mode", "skill-tool",
            "--status", "completed",
            "--evidence-output", str(evidence),
            "--json",
        ]
    )
    monkeypatch.chdir(state_dir.parent)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setenv("MISSION_LEASE_ID", "test-lease")
    monkeypatch.setattr(
        module,
        "atomic_write_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(OSError, match="publish failed"):
        args.func(args)

    assert state_path.read_bytes() == state_before
    artifacts_after = set(archive_dir.glob("*")) if archive_dir.exists() else set()
    assert artifacts_after == artifacts_before


def test_log_invocation_redacts_local_locators_from_evidence_body(
    state_dir, run_cli, tmp_path
):
    private_locators = [
        "/home/portable-user/private.txt",
        "/root/private.txt",
        "/tmp/private.txt",
        r"C:\\Users\\portable-user\\private.txt",
        r"\\server\share\private.txt",
        r"\\?\C:\private\file.txt",
        r"\\.\PhysicalDrive0",
        r"\Device\HarddiskVolume1\private.txt",
        "~/private.txt",
        r"C:relative\private.txt",
        "D:relative/private.txt",
        "C:private.txt",
        "D:.env",
        "c:private.txt",
        "https://exa%mple/C:private.txt",
        "https://example.test,local=/home/portable-user/secret.txt",
        "https://example.test:bad/home/portable-user/secret.txt",
        r"https://example.test/path\home\portable-user\private.txt",
        "https://example.test/path?locator=|C:private.txt",
        "https://example.test/path#locator={C:private.txt}",
        "file:relative/private.txt",
        "file://server/share/private.txt",
        "~",
        "~portable-user",
        "~+",
        "~-",
        "~portable-user/private.txt",
        r"~portable-user\private.txt",
    ]
    evidence = tmp_path / "private-locator-review.md"
    evidence.write_text(
        "\n".join(f"finding path={value}" for value in private_locators),
        encoding="utf-8",
    )

    result = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "reviewer",
        "--skill", "portable-provider",
        "--mode", "skill-tool",
        "--status", "completed",
        "--evidence-output", str(evidence),
        "--json",
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)["entry"]
    archived = state_dir.parent / entry["evidence_path"]
    archived_text = archived.read_text(encoding="utf-8")
    assert "[REDACTED_PATH]" in archived_text
    for locator in private_locators:
        assert locator not in archived_text
        assert locator not in result.stdout
        assert locator not in result.stderr


def test_provider_output_redactor_rejects_control_in_http_candidate():
    module = _load_mission_state_module("mission_state_issue394_url_control")
    unsafe = "https://example.test/path\x7fC:private.txt"

    redacted = module._redact_provider_output(f"finding link={unsafe}")

    assert unsafe not in redacted
    assert "[REDACTED_PATH]" in redacted


@pytest.mark.parametrize(
    "input_kind", ["symlink", "oversize", "non-regular", "invalid-encoding"]
)
def test_log_invocation_rejects_unsafe_evidence_snapshot_without_side_effects(
    input_kind, state_dir, run_cli, tmp_path
):
    state_path = state_dir / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    archive_dir = state_dir / "archive"
    artifacts_before = set(archive_dir.glob("*")) if archive_dir.exists() else set()
    if input_kind == "symlink":
        target = tmp_path / "evidence-target.md"
        target.write_text("portable evidence", encoding="utf-8")
        evidence = tmp_path / "evidence-link.md"
        evidence.symlink_to(target)
    else:
        evidence = tmp_path / f"{input_kind}-evidence.md"
        if input_kind == "oversize":
            evidence.write_bytes(b"x" * (1024 * 1024 + 1))
        elif input_kind == "non-regular":
            evidence.mkdir()
        else:
            evidence.write_bytes(b"\xff\xfe")

    result = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "reviewer",
        "--skill", "portable-provider",
        "--mode", "skill-tool",
        "--status", "completed",
        "--evidence-output", str(evidence),
        "--json",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert str(evidence) not in result.stdout
    assert str(evidence) not in result.stderr
    assert state_path.read_bytes() == state_before
    artifacts_after = set(archive_dir.glob("*")) if archive_dir.exists() else set()
    assert artifacts_after == artifacts_before


def test_evidence_snapshot_detects_in_place_mutation_from_one_open_fd(
    tmp_path, monkeypatch
):
    module = _load_mission_state_module("mission_state_issue394_evidence_snapshot")
    evidence = tmp_path / "mutable-evidence.md"
    evidence.write_bytes(b"portable evidence")
    original_read = module.os.read
    mutated = False

    def mutating_read(fd, size):
        nonlocal mutated
        chunk = original_read(fd, size)
        if not mutated:
            mutated = True
            evidence.write_bytes(b"changed evidence!")
        return chunk

    monkeypatch.setattr(module.os, "read", mutating_read)

    with pytest.raises(module.SpecialistEvidenceInputError) as caught:
        module._read_specialist_evidence_input(evidence)

    assert caught.value.reason_code == "specialist-evidence-changed"


@pytest.mark.parametrize("failing_operation", ["read", "second-fstat"])
def test_evidence_snapshot_io_error_is_structured_and_closes_once(
    failing_operation, tmp_path, monkeypatch
):
    module = _load_mission_state_module(
        f"mission_state_issue394_evidence_io_{failing_operation}"
    )
    evidence = tmp_path / "evidence.md"
    evidence.write_text("portable evidence", encoding="utf-8")
    original_read = module.os.read
    original_fstat = module.os.fstat
    original_close = module.os.close
    close_count = 0
    fstat_count = 0

    def close_spy(fd):
        nonlocal close_count
        close_count += 1
        return original_close(fd)

    def fstat_spy(fd):
        nonlocal fstat_count
        fstat_count += 1
        if failing_operation == "second-fstat" and fstat_count == 2:
            raise OSError("metadata changed")
        return original_fstat(fd)

    monkeypatch.setattr(module.os, "close", close_spy)
    monkeypatch.setattr(module.os, "fstat", fstat_spy)
    if failing_operation == "read":
        monkeypatch.setattr(
            module.os, "read", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read failed"))
        )
    else:
        monkeypatch.setattr(module.os, "read", original_read)

    with pytest.raises(module.SpecialistEvidenceInputError) as caught:
        module._read_specialist_evidence_input(evidence)

    assert caught.value.reason_code == "specialist-evidence-unreadable"
    assert close_count == 1


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


def test_log_invocation_selection_source_does_not_create_selection_metadata(state_dir, run_cli, read_state):
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

    assert r.returncode == 2
    assert "provider-ineligible" in r.stderr


def test_log_invocation_task_required_source_does_not_create_selection_metadata(state_dir, run_cli, read_state):
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

    assert r.returncode == 2
    assert "provider-ineligible" in r.stderr


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


def test_log_invocation_confirmed_user_source_cannot_promote_candidate_to_selection(state_dir, run_cli, read_state):
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
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "log-invocation",
        "--iteration", "1",
        "--phase", "review",
        "--role", "reviewer",
        "--skill", "example-reviewer",
        "--mode", "codex-inline",
        "--status", "inline-applied",
        "--selection-source", "confirmed-user",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "provider-ineligible" in result.stderr
    assert state_path.read_bytes() == before


def test_log_invocation_rejects_bounded_orchestrator_execution(state_dir, run_cli):
    state_path = state_dir / "sessions" / "test.json"
    state = json.loads(state_path.read_text())
    _set_current_bounded_provider(state, provider_phase="execution")
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
    _set_current_bounded_provider(state, provider_phase="review")
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
    _set_current_bounded_provider(state, provider_phase="review")
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
    command_dir = tmp_path / "provider-bin"
    command_dir.mkdir()
    command = command_dir / "fake-reviewer-command"
    command.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(helper)) + "\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    provider_env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "fake-reviewer",
            "kind": "command",
                "command": command.name,
                "args": [],
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
            env_extra=provider_env,
        )
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = "reviewing"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "fake-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--input-file", str(context),
        "--json",
        cwd=tmp_path,
        env_extra=provider_env,
    )

    assert r.returncode == 2
    assert "preflight-required" in r.stderr
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["specialist_invocations"] == []


def test_invoke_command_preflights_timeout_before_activity_spawn_or_archive(
    run_cli, tmp_path
):
    run_cli(
        "init", "command provider preflight", "--complexity", "Complex",
        cwd=tmp_path, check=True,
    )
    spawned = tmp_path / "provider-spawned"
    helper = tmp_path / "provider.py"
    helper.write_text(
        "from pathlib import Path\n"
        f"Path({str(spawned)!r}).write_text('spawned', encoding='utf-8')\n"
        "print('substantive provider output')\n",
        encoding="utf-8",
    )
    provider = {
        "provider_id": "portable-timeout-provider",
        "role": "portable-timeout-provider",
        "skill": "portable-timeout-provider",
        "kind": "command",
        "command": sys.executable,
        "args": [str(helper)],
        "task_profiles": ["documentation"],
        "phases": ["planning"],
    }
    _seed_legacy_command_provider_state(tmp_path, provider)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state_before = state_path.read_bytes()
    backup_path = state_path.with_suffix(".json.bak")
    backup_path.unlink(missing_ok=True)
    archive_dir = tmp_path / ".mission-state" / "archive"
    artifacts_before = set(archive_dir.glob("*")) if archive_dir.exists() else set()

    result = run_cli(
        "specialists", "invoke-command",
        "--provider", "portable-timeout-provider",
        "--iteration", "1",
        "--phase", "planning",
        "--timeout", "86401",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert state_path.read_bytes() == state_before
    assert not backup_path.exists()
    assert not spawned.exists()
    artifacts_after = set(archive_dir.glob("*")) if archive_dir.exists() else set()
    assert artifacts_after == artifacts_before


def test_invoke_command_provider_records_failure_without_blocking_optional_provider(run_cli, tmp_path):
    run_cli("init", "command provider failure mission", "--complexity", "Complex", cwd=tmp_path, check=True)
    helper = tmp_path / "provider.py"
    helper.write_text("import sys; print('bad token=abc123'); sys.exit(7)\n", encoding="utf-8")
    command_dir = tmp_path / "provider-bin"
    command_dir.mkdir()
    command = command_dir / "failing-reviewer-command"
    command.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(helper)) + "\n",
        encoding="utf-8",
    )
    command.chmod(0o700)
    provider_env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    registry = tmp_path / ".mission" / "specialists.yml"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "version": 1,
        "specialists": [{
            "role": "failing-reviewer",
            "kind": "command",
                "command": command.name,
                "args": [],
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
            env_extra=provider_env,
        )
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = "reviewing"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "failing-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--json",
        cwd=tmp_path,
        env_extra=provider_env,
    )

    assert r.returncode == 2
    assert "preflight-required" in r.stderr
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())
    assert state["specialist_invocations"] == []


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
    assert "preflight-required" in r.stderr


def test_invoke_command_provider_confirmed_source_cannot_promote_candidate_after_ask_user(run_cli, tmp_path):
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
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    before = state_path.read_bytes()

    r = run_cli(
        "specialists", "invoke-command",
        "--provider", "paid-reviewer",
        "--iteration", "1",
        "--phase", "review",
        "--selection-source", "confirmed-user",
        "--json",
        cwd=tmp_path,
    )

    assert r.returncode == 2
    assert "provider-ineligible" in r.stderr
    assert state_path.read_bytes() == before


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
