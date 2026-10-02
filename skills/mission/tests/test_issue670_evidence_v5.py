"""#670: evidence CLI commands use the v5 lifecycle repository."""

import json


def _acceptance_contract(mission_id):
    import hashlib

    text = "Preserve every stated obligation."
    return {
        "schema": "mission-acceptance-contract/1",
        "mission_id": mission_id,
        "requirement_text": text,
        "requirement_digest": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        "revision": 1,
        "review_policy": "fresh-required",
        "requirements": [
            {"id": "R1", "start": 0, "end": len(text), "text": text, "classification": "obligation"}
        ],
        "criteria": [
            {
                "id": "AC1", "requirement_ids": ["R1"], "expected": "contract retained",
                "required": True, "prohibited_side_effects": [], "verification_kind": "command",
                "target_path": "reports/receipt.json", "command_id": "project-test",
            }
        ],
        "coverage": {"status": "pending"},
    }


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


def test_v5_acceptance_import_rejects_foreign_or_replacement_contracts(tmp_path, run_cli):
    """The v5 route persists the typed command only for its own mission once."""
    from mission_persistence.fenced_commit import LocalFencedRepository

    env = {**_init_v5(run_cli, tmp_path), "MISSION_OPERATION_ID": "acceptance-import"}
    repository = LocalFencedRepository(tmp_path / ".mission-state")
    before = json.loads(repository.read("test").state_bytes)
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps(_acceptance_contract("foreign")), encoding="utf-8")
    rejected = run_cli("acceptance-contract", "import", "--input", str(foreign), cwd=tmp_path, env_extra=env)
    assert rejected.returncode != 0
    assert "acceptance_contract" not in json.loads(repository.read("test").state_bytes)

    source = tmp_path / "contract.json"
    source.write_text(json.dumps(_acceptance_contract(before["mission_id"])), encoding="utf-8")
    accepted = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path, env_extra=env)
    assert accepted.returncode == 0, accepted.stderr
    stored = json.loads(repository.read("test").state_bytes)["acceptance_contract"]

    replay = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path, env_extra=env)
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == json.loads(accepted.stdout)
    assert json.loads(replay.stdout)["acceptance_contract"]["digest"] == json.loads(run_cli("acceptance-contract", "status", cwd=tmp_path, env_extra=env).stdout)["digest"]

    replacement = run_cli("acceptance-contract", "import", "--input", str(source), cwd=tmp_path, env_extra={**env, "MISSION_OPERATION_ID": "replacement"})
    assert replacement.returncode != 0
    assert json.loads(repository.read("test").state_bytes)["acceptance_contract"] == stored


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


def _artifact_prepare(state):
    from mission_application.artifact import prepare_artifact_init

    return prepare_artifact_init(
        state,
        now="2030-01-01T00:00:00Z",
        artifact_path="a.md",
        format="markdown",
        title="t",
        redaction_status="unchecked",
        required_for_pass=False,
        render=lambda _document, _artifact: b"# t\n",
    )


def test_publication_binding_truth_value_cannot_reenter_persistence(tmp_path, run_cli, monkeypatch):
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
            observed["publisher"] = True
            @contextlib.contextmanager
            def _managed():
                yield _Published(repository)

            return _managed()

        # The v5 executor only accepts its own injected publisher, so the probe
        # replaces that binding rather than passing one in.
        #
        # #764 moves every artifact command to the UoW path.  Remove this
        # command's path declaration only inside this guard probe so the old
        # branch remains tested without changing production routing.
        from mission_application import evidence_publication

        monkeypatch.delitem(
            evidence_publication.PUBLICATION_PATH_FIELD_BY_COMMAND_TYPE,
            "initialize-artifact",
        )
        repository._effect_transaction = publisher
        repository.execute_transition_effects(_artifact_prepare)
    finally:
        os.chdir(previous)
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert observed["eq_depth"] >= 1
    assert observed["bool_depth"] >= 1
    assert observed["publisher"] is True
