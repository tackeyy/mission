"""#670: evidence CLI commands use the v5 lifecycle repository."""

import json


def _v5_env(tmp_path):
    """v5 state 生成用: MISSION_* を絞り、version-skew 警告も抑制する。"""
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "fake-claude-home"),
        "CODEX_HOME": str(tmp_path / "fake-codex-home"),
    }


def _init_v5(run_cli, tmp_path, *, mission="v5 evidence mission"):
    env = _v5_env(tmp_path)
    run_cli(
        "init",
        mission,
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
        check=True,
    )
    head = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "test.json").read_text()
    )
    assert head["schema"] == "mission-head/1"
    return env


def test_v5_context_manifest_publishes_and_records_manifest(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path)
    result = run_cli(
        "context-manifest",
        "--iteration",
        "1",
        "--out",
        "reports/context.json",
        cwd=tmp_path,
        env_extra=env,
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    manifest = json.loads((tmp_path / "reports" / "context.json").read_text())
    assert manifest["schema"] == "mission-context-manifest/1"


def test_v5_artifact_and_progress_commands_publish_and_update_state(tmp_path, run_cli):
    env = _init_v5(run_cli, tmp_path)
    commands = (
        ("artifact", "init", "--title", "Evidence", "--json"),
        (
            "artifact",
            "append",
            "--section",
            "evidence",
            "--text",
            "v5 regression evidence",
            "--json",
        ),
        ("artifact", "render", "--redaction-status", "reviewed", "--json"),
        (
            "artifact",
            "export",
            "--to",
            "reports/artifact.md",
            "--redaction-status",
            "reviewed",
            "--json",
        ),
        (
            "artifact",
            "publish",
            "--provider",
            "local",
            "--require-confirm",
            "--approval-text",
            "approved for regression test",
            "--json",
        ),
        ("progress", "update", "--total", "2", "--completed", "1", "--json"),
        ("progress", "clear", "--json"),
    )

    for command in commands:
        result = run_cli(*command, cwd=tmp_path, env_extra=env)
        assert result.returncode == 0, (
            f"{' '.join(command)} failed\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    assert (tmp_path / "reports" / "artifact.md").exists()


def test_v5_publication_rolls_back_when_the_commit_fails():
    """An exception after publication must remove the published file (#670 review).

    The remaining exposure is a hard process kill between publication and
    commit, which the v4 route shares; it is tracked separately.
    """
    import pytest

    from mission_kernel.json_codec import freeze_json_value
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    published_paths = []

    class _Boom(RuntimeError):
        pass

    def publisher(effects, prepared=None):
        import contextlib

        @contextlib.contextmanager
        def _managed():
            published_paths.append("published")
            try:
                yield effects
            except BaseException:
                published_paths.remove("published")
                raise

        return _managed()

    repository = V5CompatibilityRepository.__new__(V5CompatibilityRepository)
    repository._callback_depth = 0
    repository._effect_transaction = publisher

    from mission_application.artifact import make_evidence_effect

    effects = (make_evidence_effect("evidence", "evidence.json", b"{}"),)
    with pytest.raises(_Boom):
        with repository._guarded_context(publisher, effects, None):
            raise _Boom("commit failed")
    assert published_paths == []


def test_v5_publication_closes_the_transaction_on_success():
    """The publication context closes normally when nothing raises."""
    import contextlib

    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    closed = []

    def publisher(effects, prepared=None):
        @contextlib.contextmanager
        def _managed():
            yield effects
            closed.append(True)

        return _managed()

    repository = V5CompatibilityRepository.__new__(V5CompatibilityRepository)
    repository._callback_depth = 0
    with repository._guarded_context(publisher, (), None):
        pass
    assert closed == [True]


def test_publication_binding_truth_value_cannot_reenter_persistence(tmp_path, run_cli):
    """`__eq__` and `__bool__` both run inside the production guard (#670 review).

    The probe goes through `execute_evidence_transition_effects` so moving the
    truth-value evaluation back outside the guard fails this test.
    """
    import contextlib
    import importlib.util
    import os
    from pathlib import Path

    from mission_application.evidence import ContextManifestRequest, prepare_context_manifest

    base_env = _v5_env(tmp_path)
    init = run_cli(
        "init", "v5 guard probe", "--complexity", "Standard",
        cwd=tmp_path, env_extra=base_env, check=True,
    )
    lease = json.loads(
        [line for line in init.stderr.splitlines() if "MISSION_LEASE_CARRIER=" in line][-1]
        .split("MISSION_LEASE_CARRIER=", 1)[1]
    )
    env = {
        **base_env,
        "MISSION_SESSION_ID": "test",
        "MISSION_LEASE_ID": lease["lease_id"],
    }
    spec = importlib.util.spec_from_file_location(
        "mission_state_guard_probe",
        Path(__file__).resolve().parent.parent / "bin" / "mission-state.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    observed = {}

    class _Truth:
        def __init__(self, repository):
            self._repository = repository

        def __bool__(self):
            observed["bool_depth"] = self._repository._callback_depth
            return True

    class _Published:
        def __init__(self, repository):
            self._repository = repository

        def __eq__(self, other):
            observed["eq_depth"] = self._repository._callback_depth
            return _Truth(self._repository)

    previous = os.getcwd()
    previous_env = {key: os.environ.get(key) for key in env}
    os.chdir(tmp_path)
    os.environ.update(env)
    try:
        state_file = module.resolve_state_file(Path.cwd())
        repository = module._legacy_lifecycle_repository(
            Path.cwd(), state_file, stamp=True, pre_admit_lease=True
        )

        def publisher(_effects, _prepared=None):
            @contextlib.contextmanager
            def _managed():
                yield _Published(repository)

            return _managed()

        def prepare(state):
            return prepare_context_manifest(
                state,
                now="2030-01-01T00:00:00Z",
                iteration=1,
                publication_path="reports/guard.json",
            )

        # The v5 executor only accepts its own injected publisher, so the probe
        # replaces that binding rather than passing one in.
        repository._effect_transaction = publisher
        repository.execute_evidence_transition_effects(prepare)
    finally:
        os.chdir(previous)
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert observed["eq_depth"] >= 1
    assert observed["bool_depth"] >= 1
