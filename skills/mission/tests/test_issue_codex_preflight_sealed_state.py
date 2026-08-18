"""Regression test: codex-preflight --json --strict must not return internal-error
for an active sealed (v5) mission state.

Issue: cmd_codex_preflight called _derive_next_action(data) without resolving the
authoritative snapshot first.  For sealed/v5 documents, authoritative_snapshot_from_document
raises ValueError("sealed state document uses an unsupported format"), which propagates as
outcome_kind="internal-error".  cmd_next already uses _load_authoritative_state correctly;
this test locks in parity.

NOTE: This file intentionally does NOT override the run_cli fixture with legacy_run_cli.
test_codex_preflight.py replaces run_cli with legacy_run_cli (retained-v4 path), which
never exercises the sealed v5 code path.  We use the base run_cli from conftest so the
freshly-created sealed state is left intact.
"""

import json


def _no_skew_env(tmp_path):
    """Isolate MISSION_CLAUDE_HOME / CODEX_HOME to avoid version-skew warnings (#186)."""
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME": str(tmp_path / "fake-codex-home"),
    }


def test_codex_preflight_sealed_state_no_internal_error(tmp_path, run_cli):
    """codex-preflight --json --strict on a fresh (sealed/v5) active state must not
    produce outcome_kind == 'internal-error', and state_guard.active must be True."""

    env = _no_skew_env(tmp_path)

    # 1. Create an active sealed (v5) mission state via init.
    #    run_cli (base fixture) does NOT materialise a legacy v4 override, so the
    #    on-disk file remains the sealed format written by cmd_init.
    init_result = run_cli(
        "init", "codex mission", "--complexity", "Standard",
        cwd=tmp_path, env_extra=env, check=True,
    )
    assert init_result.returncode == 0, init_result.stderr

    # 2. Run codex-preflight --json --strict against the sealed state.
    preflight_result = run_cli(
        "codex-preflight", "--json", "--strict",
        cwd=tmp_path, env_extra=env,
    )

    assert preflight_result.returncode == 0, (
        f"codex-preflight exited non-zero.\nstdout: {preflight_result.stdout}\nstderr: {preflight_result.stderr}"
    )

    out = json.loads(preflight_result.stdout)

    # Must NOT be an internal-error.
    assert out.get("outcome_kind") != "internal-error", (
        f"codex-preflight returned internal-error for a sealed state.\nFull output: {json.dumps(out, ensure_ascii=False, indent=2)}"
    )

    # state_guard.active must be True (state was just init-ed and is active).
    state_guard = out.get("state_guard", {})
    assert state_guard.get("active") is True, (
        f"state_guard.active is not True.\nstate_guard: {state_guard}\nFull output: {json.dumps(out, ensure_ascii=False, indent=2)}"
    )
