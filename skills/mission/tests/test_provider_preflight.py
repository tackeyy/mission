"""Issue #396: exact outbound packet and receipt gates for command providers."""

import json
import os
from pathlib import Path
import sys

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from provider_preflight import (  # noqa: E402
    ProviderPreflightError,
    build_preflight,
    canonical_json_bytes,
    consume_receipt,
    safe_input_snapshot,
    validate_execution_context,
    validate_receipt,
    verify_live_packet,
)


def _digest(char="a"):
    return "sha256:" + char * 64


def _subject():
    return {
        "session_id": "session-396", "mission_id": "mission-396",
        "mission": "Design a portable provider contract.",
        "provider_id": "portable-provider", "registry_entry_digest": _digest("b"),
        "selection_id": "sel_0123456789abcdef0123456789abcdef",
        "selection_source": "automatic",
        "invocation_id": "inv_0123456789abcdef0123456789abcdef",
        "iteration": 1, "phase": "planning",
        "destination": {"kind": "external-service", "display_name": "planning-provider"},
        "risk_scopes": ["external-context", "paid-quota"],
        "quota_mode": "api-metered",
        "effective_argv": ["portable-provider", "--token", "${TOKEN_REF}"],
        "env_keys": ["TOKEN_REF"],
        "execution_context": {
            "isolation": "strict", "assurance": "stdin-exact",
            "cwd": "session-local-empty", "resource_mounts": [], "env_allowlist": ["TOKEN_REF"],
            "ambient_scopes": [], "network_destination_policy": "verified",
            "isolator": {
                "schema": "execution-isolator/1", "backend_id": "host-isolator",
                "version": "1", "policy_digest": _digest("c"), "host_support": True,
                "enforced_capabilities": ["env-reset", "filesystem-namespace", "network-policy", "readonly-mount"],
            },
        },
    }


def _preflight(tmp_path, content=b"safe input"):
    source = tmp_path / "input.txt"
    source.write_bytes(content)
    snapshots = [safe_input_snapshot(source, root=tmp_path)]
    preflight = build_preflight(_subject(), snapshots)
    preflight["_test_snapshots"] = snapshots
    return preflight


def _receipt(preflight, *, scopes=None, expires_at="2099-01-01T00:00:00Z", nonce="n" * 32):
    return {
        "schema": "mission-provider-approval-receipt/1",
        "preflight_id": preflight["preflight_id"],
        "session_id": preflight["session_id"], "mission_id": preflight["mission_id"],
        "outbound_context_digest": preflight["outbound_context_digest"],
        "invocation_id": preflight["invocation_id"],
        "outbound_packet_digest": preflight["outbound_packet_digest"],
        "registry_entry_digest": preflight["registry_entry_digest"],
        "selection_id": preflight["selection_id"], "selection_source": preflight["selection_source"],
        "iteration": preflight["iteration"], "phase": preflight["phase"],
        "approved_scopes": scopes if scopes is not None else preflight["risk_scopes"],
        "expires_at": expires_at, "single_use_nonce": nonce,
        "approval_provenance": {
            "issuer_id": "host-approval", "verifier_id": "trusted-verifier", "verifier_version": "1",
            "actor_kind": "human", "actor_id": "actor:opaque", "proof_kind": "opaque-host-event",
            "proof_digest": _digest("d"),
        },
    }


def test_canonical_packet_uses_exact_sorted_bytes_and_redacts_secret_values(tmp_path):
    preflight = _preflight(tmp_path, b"token=secret-value\nbody")

    assert preflight["outbound_packet_bytes"] == canonical_json_bytes(preflight["outbound_packet"])
    assert preflight["outbound_packet_digest"] == "sha256:" + __import__("hashlib").sha256(preflight["outbound_packet_bytes"]).hexdigest()
    rendered = preflight["outbound_packet_bytes"].decode("utf-8")
    assert "secret-value" not in rendered
    assert "${TOKEN_REF}" in rendered
    assert list(preflight["outbound_packet"]) == sorted(preflight["outbound_packet"])


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversize", "invalid-utf8", "traversal", "nul"])
def test_input_snapshot_fails_closed_for_unsafe_input(kind, tmp_path):
    source = tmp_path / "input"
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_text("safe", encoding="utf-8")
        source.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(source)
    elif kind == "oversize":
        source.write_bytes(b"x" * (1024 * 1024 + 1))
    elif kind == "invalid-utf8":
        source.write_bytes(b"\xff")
    elif kind == "traversal":
        source = tmp_path.parent / "outside"
        source.write_text("safe", encoding="utf-8")
    else:
        source = Path(str(tmp_path / "bad\x00name"))

    with pytest.raises(ProviderPreflightError):
        safe_input_snapshot(source, root=tmp_path)


def test_preflight_requires_no_process_or_network_and_reports_destination_risk_quota(tmp_path):
    preflight = _preflight(tmp_path)

    assert preflight["schema"] == "mission-provider-preflight/1"
    assert preflight["requires_approval"] is True
    assert preflight["destination"] == _subject()["destination"]
    assert preflight["risk_scopes"] == ["external-context", "paid-quota"]
    assert preflight["quota_mode"] == "api-metered"
    assert preflight["live_effects"] == ["provider-process", "external-send"]


def test_bookkeeping_does_not_change_context_digest_but_payload_change_does(tmp_path):
    first = _preflight(tmp_path)
    same = build_preflight({**_subject(), "preflight_status": "awaiting-approval"}, first["_test_snapshots"])
    changed = _preflight(tmp_path, b"one byte changed")

    assert same["outbound_context_digest"] == first["outbound_context_digest"]
    assert changed["outbound_context_digest"] != first["outbound_context_digest"]


def test_strict_execution_needs_trusted_complete_isolator_and_ambient_cannot_downgrade():
    strict = _subject()["execution_context"]
    validate_execution_context(strict)
    missing = {**strict, "isolator": {**strict["isolator"], "enforced_capabilities": ["env-reset"]}}
    with pytest.raises(ProviderPreflightError, match="isolator-capability-missing"):
        validate_execution_context(missing)
    ambient = {**strict, "isolation": "declared-ambient", "ambient_scopes": ["inherited-env"]}
    with pytest.raises(ProviderPreflightError, match="ambient-scope-unapproved"):
        validate_execution_context(ambient)


def test_receipt_requires_trusted_verifier_scope_subject_expiry_and_nonce(tmp_path):
    preflight = _preflight(tmp_path)
    receipt = _receipt(preflight)
    validate_receipt(preflight, receipt, trusted_verifiers={"trusted-verifier": "1"}, now="2026-08-12T00:00:00Z")
    for mutated in (
        _receipt(preflight, scopes=["external-context"]),
        _receipt(preflight, expires_at="2026-08-11T00:00:00Z"),
        {**_receipt(preflight), "approval_provenance": {**_receipt(preflight)["approval_provenance"], "verifier_id": "unknown"}},
        {**_receipt(preflight), "session_id": "another-session"},
    ):
        with pytest.raises(ProviderPreflightError):
            validate_receipt(preflight, mutated, trusted_verifiers={"trusted-verifier": "1"}, now="2026-08-12T00:00:00Z")


def test_receipt_consumes_once_and_rejects_replay(tmp_path):
    preflight = _preflight(tmp_path)
    receipt = _receipt(preflight)
    consumed = consume_receipt(preflight, receipt, used_nonces=set(), trusted_verifiers={"trusted-verifier": "1"}, now="2026-08-12T00:00:00Z")
    assert consumed["status"] == "consumed"
    with pytest.raises(ProviderPreflightError, match="receipt-replayed"):
        consume_receipt(preflight, receipt, used_nonces={receipt["single_use_nonce"]}, trusted_verifiers={"trusted-verifier": "1"}, now="2026-08-12T00:00:00Z")


@pytest.mark.parametrize("field,value", [
    ("mission", "mutated mission"), ("selection_source", "manual"), ("iteration", 2),
    ("phase", "review"), ("destination", {"kind": "external-service", "display_name": "other"}),
    ("effective_argv", ["other-provider"]), ("risk_scopes", ["external-context"]),
])
def test_live_verification_rejects_every_payload_or_binding_drift(field, value, tmp_path):
    preflight = _preflight(tmp_path)
    receipt = _receipt(preflight)
    current = _subject()
    current[field] = value
    with pytest.raises(ProviderPreflightError, match="payload-drift"):
        verify_live_packet(preflight, receipt, current, preflight["_test_snapshots"], trusted_verifiers={"trusted-verifier": "1"}, now="2026-08-12T00:00:00Z")


def test_live_verification_returns_the_preflight_immutable_bytes_only(tmp_path):
    preflight = _preflight(tmp_path)
    receipt = _receipt(preflight)
    packet = verify_live_packet(preflight, receipt, _subject(), preflight["_test_snapshots"], trusted_verifiers={"trusted-verifier": "1"}, now="2026-08-12T00:00:00Z")

    assert packet is preflight["outbound_packet_bytes"]
    assert json.loads(packet) == preflight["outbound_packet"]


def test_direct_risk_scoped_command_invoke_requires_preflight_before_spawn(run_cli, tmp_path):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    marker = tmp_path / "provider-ran"
    command = command_dir / "provider-command"
    command.write_text("#!/bin/sh\nprintf invoked > \"$PROVIDER_MARKER\"\ncat >/dev/null\n", encoding="utf-8")
    command.chmod(0o700)
    registry = tmp_path / "provider-registry.json"
    registry.write_text(json.dumps({"schema": "mission-specialist-registry/2", "specialists_v2": [{
        "provider_id": "guarded-command-provider", "role": "planning-provider",
        "skill": "guarded-command-provider", "kind": "command", "command": "provider-command",
        "args": [], "env": {}, "task_profiles": ["architecture"], "phases": ["planning"],
        "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
    }]}), encoding="utf-8")
    env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}", "PROVIDER_MARKER": str(marker)}
    run_cli("init", "provider preflight guard", "--complexity", "Complex", cwd=tmp_path, check=True, env_extra=env)
    run_cli("specialists", "recommend", "--no-default-skill-roots", "--task", "Review architecture",
            "--registry", str(registry), "--complexity", "Complex", "--record-state", cwd=tmp_path, check=True, env_extra=env)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    before = state_path.read_bytes()

    result = run_cli(
        "specialists", "invoke-command", "--provider", "guarded-command-provider",
        "--iteration", "1", "--phase", "planning", cwd=tmp_path, env_extra=env,
    )

    assert result.returncode == 2
    assert "preflight-required" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_prepare_invocation_publishes_private_packet_and_state_pointer_without_spawn(run_cli, tmp_path):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    marker = tmp_path / "provider-ran"
    command = command_dir / "provider-command"
    command.write_text("#!/bin/sh\nprintf invoked > \"$PROVIDER_MARKER\"\ncat >/dev/null\n", encoding="utf-8")
    command.chmod(0o700)
    registry = tmp_path / "provider-registry.json"
    registry.write_text(json.dumps({"schema": "mission-specialist-registry/2", "specialists_v2": [{
        "provider_id": "prepared-command-provider", "role": "planning-provider",
        "skill": "prepared-command-provider", "kind": "command", "command": "provider-command",
        "args": [], "env": {}, "task_profiles": ["architecture"], "phases": ["planning"],
        "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
    }]}), encoding="utf-8")
    env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}", "PROVIDER_MARKER": str(marker)}
    source = tmp_path / "input.txt"
    source.write_text("TOKEN=not-for-state\nbrief", encoding="utf-8")
    run_cli("init", "provider preflight prepare", "--complexity", "Complex", cwd=tmp_path, check=True, env_extra=env)
    run_cli("specialists", "recommend", "--no-default-skill-roots", "--task", "Review architecture",
            "--registry", str(registry), "--complexity", "Complex", "--record-state", cwd=tmp_path, check=True, env_extra=env)

    result = run_cli("specialists", "prepare-invocation", "--provider", "prepared-command-provider",
                     "--iteration", "1", "--phase", "planning", "--input-file", str(source),
                     cwd=tmp_path, env_extra=env)

    assert result.returncode == 0, result.stderr
    prepared = json.loads(result.stdout)
    assert prepared["schema"] == "mission-provider-preflight/1"
    assert not marker.exists()
    state = json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text(encoding="utf-8"))
    pointer = state["provider_preflights"][prepared["preflight_id"]]
    assert pointer["outbound_packet_digest"] == prepared["outbound_packet_digest"]
    artifact = tmp_path / ".mission-state" / pointer["artifact_path"]
    assert artifact.is_file()
    assert "not-for-state" not in artifact.read_text(encoding="utf-8")


def test_prepared_risk_scoped_command_still_requires_verified_receipt_before_spawn(run_cli, tmp_path):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    marker = tmp_path / "provider-ran"
    command = command_dir / "provider-command"
    command.write_text("#!/bin/sh\nprintf invoked > \"$PROVIDER_MARKER\"\ncat >/dev/null\n", encoding="utf-8")
    command.chmod(0o700)
    registry = tmp_path / "provider-registry.json"
    registry.write_text(json.dumps({"schema": "mission-specialist-registry/2", "specialists_v2": [{
        "provider_id": "receipt-command-provider", "role": "planning-provider",
        "skill": "receipt-command-provider", "kind": "command", "command": "provider-command",
        "args": [], "env": {}, "task_profiles": ["architecture"], "phases": ["planning"],
        "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
    }]}), encoding="utf-8")
    env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}", "PROVIDER_MARKER": str(marker)}
    source = tmp_path / "input.txt"
    source.write_text("brief", encoding="utf-8")
    run_cli("init", "provider receipt gate", "--complexity", "Complex", cwd=tmp_path, check=True, env_extra=env)
    run_cli("specialists", "recommend", "--no-default-skill-roots", "--task", "Review architecture",
            "--registry", str(registry), "--complexity", "Complex", "--record-state", cwd=tmp_path, check=True, env_extra=env)
    prepared = run_cli("specialists", "prepare-invocation", "--provider", "receipt-command-provider",
                       "--iteration", "1", "--phase", "planning", "--input-file", str(source), cwd=tmp_path, env_extra=env, check=True)
    preflight = json.loads(prepared.stdout)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    before = state_path.read_bytes()

    result = run_cli("specialists", "invoke-command", "--provider", "receipt-command-provider",
                     "--iteration", "1", "--phase", "planning", "--preflight-id", preflight["preflight_id"],
                     cwd=tmp_path, env_extra=env)

    assert result.returncode == 2
    assert "approval-required" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_hand_marked_approved_preflight_without_receipt_never_spawns(run_cli, tmp_path):
    command_dir = tmp_path / "commands"; command_dir.mkdir()
    marker = tmp_path / "provider-ran"
    command = command_dir / "provider-command"
    command.write_text("#!/bin/sh\nprintf invoked > \"$PROVIDER_MARKER\"\ncat >/dev/null\n", encoding="utf-8"); command.chmod(0o700)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": "mission-specialist-registry/2", "specialists_v2": [{
        "provider_id": "manual-approval-provider", "role": "planning-provider", "skill": "manual-approval-provider",
        "kind": "command", "command": "provider-command", "args": [], "env": {}, "task_profiles": ["architecture"],
        "phases": ["planning"], "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
    }]}), encoding="utf-8")
    env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}", "PROVIDER_MARKER": str(marker)}
    source = tmp_path / "input.txt"; source.write_text("brief", encoding="utf-8")
    run_cli("init", "manual receipt rejection", "--complexity", "Complex", cwd=tmp_path, check=True, env_extra=env)
    run_cli("specialists", "recommend", "--no-default-skill-roots", "--task", "Review architecture", "--registry", str(registry), "--complexity", "Complex", "--record-state", cwd=tmp_path, check=True, env_extra=env)
    preflight = json.loads(run_cli("specialists", "prepare-invocation", "--provider", "manual-approval-provider", "--iteration", "1", "--phase", "planning", "--input-file", str(source), cwd=tmp_path, env_extra=env, check=True).stdout)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8")); state["provider_preflights"][preflight["preflight_id"]]["status"] = "approved"; state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    result = run_cli("specialists", "invoke-command", "--provider", "manual-approval-provider", "--iteration", "1", "--phase", "planning", "--preflight-id", preflight["preflight_id"], "--input-file", str(source), cwd=tmp_path, env_extra=env)

    assert result.returncode == 2
    assert "receipt-invalid" in result.stderr
    assert not marker.exists()
    assert state_path.read_bytes() == before


def test_verify_approval_without_host_registered_verifier_keeps_preflight_awaiting(run_cli, tmp_path):
    run_cli("init", "untrusted verifier", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["provider_preflights"] = {"pf_0123456789abcdef0123456789abcdef": {"status": "awaiting-approval", "artifact_path": "missing", "outbound_packet_digest": _digest()}}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()

    result = run_cli("specialists", "verify-approval", "--preflight-id", "pf_0123456789abcdef0123456789abcdef", "--evidence-ref", _digest("e"), "--approval-verifier", "unknown-verifier", cwd=tmp_path)

    assert result.returncode == 2
    assert state_path.read_bytes() == before


def test_host_verified_receipt_runs_exact_packet_once_and_rejects_replay(run_cli, tmp_path):
    command_dir = tmp_path / "commands"; command_dir.mkdir()
    marker, captured = tmp_path / "provider-ran", tmp_path / "captured-packet"
    command = command_dir / "provider-command"
    command.write_text("#!/bin/sh\ncat > \"$CAPTURED_PACKET\"\nprintf invoked > \"$PROVIDER_MARKER\"\n", encoding="utf-8"); command.chmod(0o700)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema": "mission-specialist-registry/2", "specialists_v2": [{
        "provider_id": "trusted-provider", "role": "planning-provider", "skill": "trusted-provider", "kind": "command",
        "command": "provider-command", "args": [], "env": {}, "task_profiles": ["architecture"], "phases": ["planning"],
        "activation": {"min_complexity": "Complex", "auto_select_if": ["complexity"]},
    }]}), encoding="utf-8")
    providers = tmp_path / "providers"; providers.mkdir()
    verifier_source = providers / "fixture_provider.py"
    verifier_source.write_text(
        "from datetime import datetime,timezone\n"
        "def verify(request):\n"
        " return {**request,'schema':'approval-evidence/1','issuer_id':'host-event','verifier_id':'fixture-verifier','verifier_version':'1.0','actor_kind':'human','actor_id':'actor:opaque','proof_kind':'opaque-host-event','proof_digest':'sha256:'+'f'*64,'expires_at':'2099-01-01T00:00:00Z','single_use_nonce':'n'*32}\n",
        encoding="utf-8")
    dist = providers / "fixture_provider-1.0.dist-info"; dist.mkdir()
    (dist / "METADATA").write_text("Name: fixture-provider\nVersion: 1.0\n", encoding="utf-8")
    (dist / "entry_points.txt").write_text("[mission.approval_verifiers]\nfixture-entry = fixture_provider:verify\n", encoding="utf-8")
    config = tmp_path / "host-config" / "mission"; config.mkdir(parents=True)
    import hashlib
    (config / "approval-verifiers.json").write_text(json.dumps({"schema": "mission-approval-verifier-registry/2", "verifiers": [{
        "id": "fixture-verifier", "entry_point": "fixture-entry", "distribution": "fixture-provider", "version": "1.0",
        "source_digest": "sha256:" + hashlib.sha256(verifier_source.read_bytes()).hexdigest(),
    }]}), encoding="utf-8")
    env = {"PATH": f"{command_dir}{os.pathsep}{os.environ.get('PATH', '')}", "PROVIDER_MARKER": str(marker),
           "CAPTURED_PACKET": str(captured), "PYTHONPATH": str(providers), "XDG_CONFIG_HOME": str(tmp_path / "host-config")}
    source = tmp_path / "input.txt"; source.write_text("brief", encoding="utf-8")
    run_cli("init", "trusted receipt", "--complexity", "Complex", cwd=tmp_path, check=True, env_extra=env)
    run_cli("specialists", "recommend", "--no-default-skill-roots", "--task", "Review architecture", "--registry", str(registry), "--complexity", "Complex", "--record-state", cwd=tmp_path, check=True, env_extra=env)
    preflight = json.loads(run_cli("specialists", "prepare-invocation", "--provider", "trusted-provider", "--iteration", "1", "--phase", "planning", "--input-file", str(source), cwd=tmp_path, env_extra=env, check=True).stdout)
    verified = run_cli("specialists", "verify-approval", "--preflight-id", preflight["preflight_id"], "--evidence-ref", _digest("e"), "--approval-verifier", "fixture-verifier", cwd=tmp_path, env_extra=env)
    assert verified.returncode == 0, verified.stderr

    invoked = run_cli("specialists", "invoke-command", "--provider", "trusted-provider", "--iteration", "1", "--phase", "planning", "--preflight-id", preflight["preflight_id"], "--input-file", str(source), cwd=tmp_path, env_extra=env)
    assert invoked.returncode == 0, invoked.stderr
    packet_path = tmp_path / ".mission-state" / "private-preflights" / f"{preflight['preflight_id']}.json"
    assert marker.exists() and captured.read_bytes() == packet_path.read_bytes()
    state_path = tmp_path / ".mission-state" / "sessions" / "test.json"; state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["provider_preflights"][preflight["preflight_id"]]["status"] == "consumed"
    before = state_path.read_bytes(); marker.unlink()
    replay = run_cli("specialists", "invoke-command", "--provider", "trusted-provider", "--iteration", "1", "--phase", "planning", "--preflight-id", preflight["preflight_id"], "--input-file", str(source), cwd=tmp_path, env_extra=env)
    assert replay.returncode == 2 and "receipt-replayed" in replay.stderr and not marker.exists() and state_path.read_bytes() == before
