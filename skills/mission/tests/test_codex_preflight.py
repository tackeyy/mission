"""Codex startup guard diagnostics for /mission.

Issue #108: Codex can return a final answer before mission state is initialized
or before the user has opted into the Stop hook. The preflight command must make
both conditions visible to the orchestrator.
"""

import json


def _no_skew_env(tmp_path):
    """#186: MISSION_CLAUDE_HOME / CODEX_HOME を隔離し、実行マシンの実際の plugin cache
    (version skew warning の対象) がテスト結果に混入しないようにする。"""
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME": str(tmp_path / "fake-codex-home"),
    }


def _init(run_cli, tmp_path, *args):
    run_cli("init", "codex mission", "--complexity", "Standard", cwd=tmp_path,
            env_extra=_no_skew_env(tmp_path), check=True)


def _json(run_cli, *args, cwd):
    r = run_cli("codex-preflight", "--json", *args, cwd=cwd, env_extra=_no_skew_env(cwd))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_codex_preflight_without_state_requires_init(tmp_path, run_cli):
    out = _json(run_cli, "--hook-config", str(tmp_path / "hooks.json"), cwd=tmp_path)

    assert out["ok"] is False
    assert out["state_guard"]["present"] is False
    assert out["mechanical_guard"] == "none"
    assert out["next_action"] == "init"
    assert any("init" in action for action in out["required_actions"])


def test_codex_preflight_active_state_warns_when_stop_hook_missing(tmp_path, run_cli):
    _init(run_cli, tmp_path)

    out = _json(run_cli, "--hook-config", str(tmp_path / "missing-hooks.json"), cwd=tmp_path)

    assert out["ok"] is True
    assert out["state_guard"]["active"] is True
    assert out["codex_stop_hook"]["configured"] is False
    assert out["mechanical_guard"] == "state-next-fallback"
    assert out["next_action"] == "run-planner"
    assert any("Stop hook" in warning for warning in out["warnings"])


def test_codex_preflight_detects_configured_stop_hook(tmp_path, run_cli):
    _init(run_cli, tmp_path)
    hook_config = tmp_path / "hooks.json"
    hook_config.write_text(
        json.dumps({
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": 'bash "/repo/scripts/mission-stop-guard.sh"',
                            }
                        ]
                    }
                ]
            }
        }),
        encoding="utf-8",
    )

    out = _json(run_cli, "--hook-config", str(hook_config), cwd=tmp_path)

    assert out["ok"] is True
    assert out["codex_stop_hook"]["configured"] is True
    assert out["mechanical_guard"] == "stop-hook"
    assert out["warnings"] == []
    assert out["version_skew"] is None


def test_codex_preflight_require_stop_hook_exits_nonzero_when_missing(tmp_path, run_cli):
    _init(run_cli, tmp_path)

    r = run_cli(
        "codex-preflight",
        "--json",
        "--require-stop-hook",
        "--hook-config",
        str(tmp_path / "missing-hooks.json"),
        cwd=tmp_path,
        env_extra=_no_skew_env(tmp_path),
    )

    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert any("mission-stop-guard.sh" in action for action in out["required_actions"])


def test_codex_preflight_includes_scoring_pipeline_summary(tmp_path, run_cli):
    """#187: preflight は aggregate-reviews 前提の scoring パイプライン要約を返し、
    force を推奨する文言を含まない (force に触れる場合も禁止文脈のみ)."""
    out = _json(run_cli, "--hook-config", str(tmp_path / "hooks.json"), cwd=tmp_path)
    pipeline = out["scoring_pipeline"]
    assert "aggregate-reviews" in pipeline
    assert "push-score" in pipeline
    assert "mark-passes" in pipeline
    assert "mission-scorer" in pipeline  # Codex 向け fallback 変換器への言及
    assert "just because" in pipeline or "Never" in pipeline  # force を安易に使わない旨の警告


def test_codex_preflight_strict_rejects_scoring_evidence_escape_hatch(tmp_path, run_cli):
    """#226 (A-4): MISSION_REQUIRE_SCORING_EVIDENCE=0 は scoring evidence gate を
    バイパスする deprecated escape hatch。preflight --strict は検出して exit 2 にする."""
    _init(run_cli, tmp_path)
    env = _no_skew_env(tmp_path)
    env["MISSION_REQUIRE_SCORING_EVIDENCE"] = "0"
    r = run_cli("codex-preflight", "--json", "--strict",
                "--hook-config", str(tmp_path / "hooks.json"),
                cwd=tmp_path, env_extra=env)
    assert r.returncode == 2, f"expected exit 2, got {r.returncode}: {r.stderr}"
    out = json.loads(r.stdout)
    assert out["ok"] is False
    assert any("MISSION_REQUIRE_SCORING_EVIDENCE" in action for action in out["required_actions"])


def test_codex_preflight_without_escape_hatch_stays_clean(tmp_path, run_cli):
    """escape hatch env が無ければ preflight は従来どおり通る (active state)."""
    _init(run_cli, tmp_path)
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "mission-stop-guard.sh"}]}]}}))
    env = _no_skew_env(tmp_path)
    r = run_cli("codex-preflight", "--json", "--strict",
                "--hook-config", str(hooks), cwd=tmp_path, env_extra=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True, out
    assert not any("MISSION_REQUIRE_SCORING_EVIDENCE" in a for a in out["required_actions"]), out["required_actions"]


def test_codex_preflight_non_strict_does_not_block_on_escape_hatch(tmp_path, run_cli):
    """#226 (A-4, case A): --strict なしでは escape hatch があっても exit 0 で通し、
    ok を False にしない (移行期の後方互換: 非対話 gate は --strict でのみ止める)。
    required_actions には可視化のため残す."""
    _init(run_cli, tmp_path)
    hooks = tmp_path / "hooks.json"
    hooks.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "mission-stop-guard.sh"}]}]}}))
    env = _no_skew_env(tmp_path)
    env["MISSION_REQUIRE_SCORING_EVIDENCE"] = "0"
    r = run_cli("codex-preflight", "--json",
                "--hook-config", str(hooks), cwd=tmp_path, env_extra=env)
    assert r.returncode == 0, f"expected exit 0, got {r.returncode}: {r.stderr}"
    out = json.loads(r.stdout)
    assert out["ok"] is True, out
    assert any("MISSION_REQUIRE_SCORING_EVIDENCE" in a for a in out["required_actions"])
