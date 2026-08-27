"""#680: verification record が v5 format の state で失敗する回帰テスト。

再現手順:
  1. init (v5 / sealed format を生成)
  2. verification record --iteration 1 --stdin

_legacy_evidence_repository が select_legacy_repository を経由するため
v5 format を「repository-format-v5-requires-uow」で拒否する。
本 PR では V5CompatibilityRepository に execute_evidence_transition_effects を追加し、
cmd_verification_record が _legacy_lifecycle_repository 経由で v5 を受け入れるよう修正する。

NOTE: run_cli（conftest 既定）は legacy override をしないため init 後の state が
      v5 sealed 形式のまま残る。legacy_run_cli を使うと v4 に変換されてしまう。
"""

import json

import pytest


def _v5_env(tmp_path):
    """v5 state 生成用: MISSION_* を絞り、version-skew 警告も抑制する。"""
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME":          str(tmp_path / "fake-codex-home"),
    }


def _record(run_cli, tmp_path, *, checks, iteration=1, env_extra=None):
    payload = json.dumps({"schema": "mission-verification/1", "checks": checks})
    return run_cli(
        "verification", "record",
        "--iteration", str(iteration),
        "--stdin",
        cwd=tmp_path,
        input_text=payload,
        env_extra=env_extra or {},
    )


# --------------------------------------------------------------------------- #
# Red: v5 format で verification record が成功すること                        #
# --------------------------------------------------------------------------- #

def test_verification_record_succeeds_on_v5_state(tmp_path, run_cli):
    """v5 sealed state で verification record が exit 0 を返すこと。"""
    env = _v5_env(tmp_path)

    # 1. v5 sealed state を生成する (run_cli は legacy override なし)
    init_result = run_cli(
        "init", "v5 mission", "--complexity", "Standard",
        cwd=tmp_path, env_extra=env, check=True,
    )
    assert init_result.returncode == 0, init_result.stderr

    # 2. verification record を実行する
    result = _record(
        run_cli, tmp_path,
        checks=[{"name": "tests", "ok": True, "detail": "5 passed"}],
        env_extra=env,
    )
    assert result.returncode == 0, (
        f"verification record が v5 state で失敗した\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    out = json.loads(result.stdout)
    assert out.get("ok") is True


def test_verification_record_v5_result_is_readable(tmp_path, run_cli):
    """v5 state で記録した検証結果が出力 JSON に含まれること。"""
    env = _v5_env(tmp_path)
    init_result = run_cli(
        "init", "v5 readability mission", "--complexity", "Standard",
        cwd=tmp_path, env_extra=env, check=True,
    )
    assert init_result.returncode == 0, init_result.stderr

    result = _record(
        run_cli, tmp_path,
        checks=[{"name": "unit", "ok": True, "detail": "3 passed"},
                {"name": "lint", "ok": False, "detail": "1 warning"}],
        env_extra=env,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    verification = out.get("verification", {})
    assert verification.get("status") == "failed"   # lint が失敗
    assert verification.get("failed_count") == 1


def test_verification_record_v5_empty_checks_recorded_as_not_run(tmp_path, run_cli):
    """checks=[] を v5 state に記録すると status == 'not-run' になること。"""
    env = _v5_env(tmp_path)
    run_cli(
        "init", "v5 empty checks mission", "--complexity", "Standard",
        cwd=tmp_path, env_extra=env, check=True,
    )
    result = _record(run_cli, tmp_path, checks=[], env_extra=env)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["verification"]["status"] == "not-run"


def test_verification_record_v5_failure_does_not_block_command(tmp_path, run_cli):
    """v5 state で failed checks があっても verification record は exit 0 を返すこと。"""
    env = _v5_env(tmp_path)
    run_cli(
        "init", "v5 fail mission", "--complexity", "Standard",
        cwd=tmp_path, env_extra=env, check=True,
    )
    result = _record(
        run_cli, tmp_path,
        checks=[{"name": "tests", "ok": False, "detail": "1 failed"}],
        env_extra=env,
    )
    assert result.returncode == 0, result.stderr
