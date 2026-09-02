"""Issue #665: merge only after testing the integrated base/head tree."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib
import importlib.util
from io import StringIO
import json
from pathlib import Path
from types import SimpleNamespace
import os
import shlex
import shutil
import subprocess
import sys
import threading

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_STATE = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
CI_SCOPE_HELPER = REPO_ROOT / "scripts" / "ci_changed_scopes.js"
HISTORICAL_HEAD = "ac328fa"
HISTORICAL_BASE = "73681cc"
OLD_BASE = "a" * 40
NEW_BASE = "b" * 40
HEAD_SHA = "c" * 40
MERGE_SHA = "d" * 40
# 統合ツリーで観測される変更集合 digest。fake は常にこの値を返す
FAKE_DIGEST = "f" * 64


def _gate_module():
    return importlib.import_module("integration_gate")


def _mission_state_module():
    spec = importlib.util.spec_from_file_location("mission_state_issue665", MISSION_STATE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sanitized_env() -> dict:
    """Drop inherited pytest configuration so fixture suites are self-contained."""
    env = dict(os.environ)
    for name in ("PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST", "PYTEST_PLUGINS", "PYTHONPATH"):
        env.pop(name, None)
    return env


def _run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        cwd=repo,
        capture_output=True,
        text=True,
        check=check,
        env=_sanitized_env(),
    )


def _commit(repo: Path, message: str) -> str:
    _run(repo, "git", "add", ".")
    _run(repo, "git", "commit", "-q", "-m", message)
    return _run(repo, "git", "rev-parse", "HEAD").stdout.strip()


def _write_fixture_scaffolding(repo: Path) -> None:
    (repo / "scripts").mkdir()
    shutil.copyfile(CI_SCOPE_HELPER, repo / "scripts" / "ci_changed_scopes.js")
    # CI の別 interpreter 環境でも同じ結果になるよう、fixture の suite は現在の
    # interpreter を絶対パスで固定し、外側の pytest 設定を継承しない。
    (repo / "Makefile").write_text(
        "test:\n\t{} -m pytest -q -p no:cacheprovider --rootdir . .\n".format(
            shlex.quote(sys.executable)
        ),
        encoding="utf-8",
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _run(repo, "git", "init", "-q", "-b", "main")
    _run(repo, "git", "config", "user.email", "fixture@example.invalid")
    _run(repo, "git", "config", "user.name", "Fixture")
    _write_fixture_scaffolding(repo)


def _integration_fixture(tmp_path: Path, *, regression: bool) -> tuple[Path, str, str]:
    repo = tmp_path / ("regression" if regression else "compatible")
    _init_repo(repo)
    if not regression:
        separation = "\n".join("# stable separation {}".format(index) for index in range(20))
        (repo / "module.py").write_text(
            "def first():\n    return 1\n\n{}\n\ndef second():\n    return 2\n".format(separation),
            encoding="utf-8",
        )
        (repo / "test_initial.py").write_text(
            "from module import first, second\n\n"
            "def test_functions_return_positive_values():\n"
            "    assert first() > 0\n    assert second() > 0\n",
            encoding="utf-8",
        )
        _commit(repo, "initial")
        _run(repo, "git", "switch", "-q", "-c", "feature")
        (repo / "module.py").write_text(
            "def first():\n    return 10\n\n{}\n\ndef second():\n    return 2\n".format(separation),
            encoding="utf-8",
        )
        head_sha = _commit(repo, "feature")
        assert _run(repo, "make", "test").returncode == 0
        _run(repo, "git", "switch", "-q", "main")
        (repo / "module.py").write_text(
            "def first():\n    return 1\n\n{}\n\ndef second():\n    return 20\n".format(separation),
            encoding="utf-8",
        )
        base_sha = _commit(repo, "main change")
        assert _run(repo, "make", "test").returncode == 0
        return repo, head_sha, base_sha

    (repo / "left.py").write_text("def left(value):\n    return value\n", encoding="utf-8")
    (repo / "right.py").write_text("def right(value):\n    return value\n", encoding="utf-8")
    (repo / "test_initial.py").write_text(
        "from left import left\nfrom right import right\n\n"
        "def test_functions_return_positive_values():\n"
        "    assert left(1) > 0\n    assert right(1) > 0\n",
        encoding="utf-8",
    )
    _commit(repo, "initial")

    _run(repo, "git", "switch", "-q", "-c", "feature")
    (repo / "left.py").write_text("def left(value):\n    return value + 1\n", encoding="utf-8")
    if regression:
        (repo / "test_feature.py").write_text(
            "from left import left\nfrom right import right\n\n"
            "def test_composition():\n    assert left(right(1)) == 2\n",
            encoding="utf-8",
        )
    head_sha = _commit(repo, "feature")
    assert _run(repo, "make", "test").returncode == 0

    _run(repo, "git", "switch", "-q", "main")
    (repo / "right.py").write_text("def right(value):\n    return value * 2\n", encoding="utf-8")
    if regression:
        (repo / "test_main.py").write_text(
            "from left import left\nfrom right import right\n\n"
            "def test_composition():\n    assert left(right(1)) == 2\n",
            encoding="utf-8",
        )
    base_sha = _commit(repo, "main change")
    assert _run(repo, "make", "test").returncode == 0
    return repo, head_sha, base_sha


class ScriptedOperations:
    def __init__(
        self,
        gate,
        *,
        base_shas=(OLD_BASE, OLD_BASE),
        fetch_failure=False,
        integration_error=None,
    ):
        self.gate = gate
        self.base_shas = iter(base_shas)
        self.fetch_failure = fetch_failure
        self.integration_error = integration_error
        self.fetches = 0
        self.merges = []
        self.lease_active = False
        self.snapshots = [
            gate.PullRequestSnapshot(665, HEAD_SHA, "main", "OPEN", None, None, OLD_BASE),
            gate.PullRequestSnapshot(665, HEAD_SHA, "main", "OPEN", None, None, OLD_BASE),
            gate.PullRequestSnapshot(665, HEAD_SHA, "main", "MERGED", "2026-08-25T00:00:00Z", MERGE_SHA, OLD_BASE),
        ]

    @contextmanager
    def lease(self):
        assert not self.lease_active
        self.lease_active = True
        try:
            yield
        finally:
            self.lease_active = False

    def _assert_leased(self):
        assert self.lease_active

    def resolve_origin_identity(self):
        self._assert_leased()
        return "github.com/acme/mission"

    def resolve_default_branch(self):
        self._assert_leased()
        return "main"

    def fetch_base(self, default_branch):
        self._assert_leased()
        assert default_branch == "main"
        self.fetches += 1
        if self.fetch_failure and self.fetches == 1:
            raise self.gate.IntegrationGateError(2, "fetch-failed", "base fetch failed")

    def current_base_sha(self):
        self._assert_leased()
        return next(self.base_shas)

    def read_pull_request(self, _pr_ref, step=6):
        self._assert_leased()
        return self.snapshots.pop(0)

    def fetch_pull_request_head(self, _snapshot):
        self._assert_leased()

    def integrate_and_test(self, _head_sha, _base_sha, _logger):
        self._assert_leased()
        if self.integration_error is not None:
            raise self.integration_error
        return self.gate.IntegrationObservation("full", "skills/mission", "e" * 40, FAKE_DIGEST)

    def merge_pull_request(self, pr_ref, expected_head_sha):
        self._assert_leased()
        self.merges.append((pr_ref, expected_head_sha))


def _historical_incident_is_reachable() -> bool:
    """CI は shallow checkout なので、実 SHA が届く環境かを先に確かめる。"""
    for sha in (HISTORICAL_HEAD, HISTORICAL_BASE):
        probe = _run(REPO_ROOT, "git", "cat-file", "-e", sha + "^{commit}", check=False)
        if probe.returncode != 0:
            return False
    return True


def test_pull_request_read_failure_reports_the_calling_step(tmp_path):
    """診断の手順番号は呼び出し位置に従う（統合前の失敗を step 6 と誤報しない）。"""
    gate = _gate_module()
    repo = tmp_path / "diagnostics"
    _init_repo(repo)
    (repo / "module.py").write_text("def value():\n    return 0\n", encoding="utf-8")
    _commit(repo, "initial")
    # #698: gh の操作先は origin 由来の identity へ固定されるため、
    # PR 読み取りの手順番号を検査するには origin が解決できる必要がある。
    _run(repo, "git", "remote", "add", "origin", "git@github.com:acme/mission.git")
    operations = gate.SubprocessGateOperations(repo)

    # 存在しない PR 番号は `gh pr view` が非 0 で返る。
    for step in (3, 6, 7):
        with pytest.raises(gate.IntegrationGateError) as captured:
            operations.read_pull_request("99999999", step=step)
        assert captured.value.step == step
        assert captured.value.reason == "pull-request-read-failed"


def test_real_git_conflict_fixture_fails_at_step_three(tmp_path):
    """AC1 の常時版: 実 git のコンフリクトを step 3 が落とすことを環境非依存で固定する。

    実履歴の再現は shallow checkout では動かせないため、同じ conflict 経路を
    合成 fixture で必ず 1 回通す。scripted な fake ではなく実 git を使う。
    """
    gate = _gate_module()
    repo = tmp_path / "conflict"
    _init_repo(repo)
    (repo / "module.py").write_text("def value():\n    return 0\n", encoding="utf-8")
    (repo / "test_initial.py").write_text(
        "from module import value\n\ndef test_value():\n    assert value() == 0\n",
        encoding="utf-8",
    )
    _commit(repo, "initial")

    _run(repo, "git", "switch", "-q", "-c", "feature")
    (repo / "module.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    head_sha = _commit(repo, "feature")

    _run(repo, "git", "switch", "-q", "main")
    (repo / "module.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    base_sha = _commit(repo, "main change")

    operations = gate.SubprocessGateOperations(repo)
    with pytest.raises(gate.IntegrationGateError) as captured:
        operations.integrate_and_test(head_sha, base_sha, lambda _message: None)

    assert captured.value.step == 3
    assert captured.value.reason == "merge-conflict"
    assert "手動統合してから再実行" in str(captured.value)


def test_real_issue660_and_issue662_history_fails_on_merge_conflict(tmp_path):
    """AC1: the only observed incident must exercise the conflict failure path."""
    if not _historical_incident_is_reachable():
        pytest.skip(
            "shallow checkout: {} / {} が届かないため実履歴の再現は行わない。"
            "同じ conflict 経路は "
            "test_real_git_conflict_fixture_fails_at_step_three が常時固定する。".format(
                HISTORICAL_HEAD, HISTORICAL_BASE
            )
        )
    gate = _gate_module()
    clone = tmp_path / "history"
    _run(tmp_path, "git", "clone", "-q", "--no-hardlinks", str(REPO_ROOT), str(clone))
    operations = gate.SubprocessGateOperations(clone)

    with pytest.raises(gate.IntegrationGateError) as captured:
        operations.integrate_and_test(HISTORICAL_HEAD, HISTORICAL_BASE, lambda _message: None)

    assert captured.value.step == 3
    assert captured.value.reason == "merge-conflict"
    assert "手動統合してから再実行" in str(captured.value)


def test_clean_merge_with_combined_regression_is_rejected_by_suite(tmp_path):
    """AC2: two independently green branches can clean-merge into a red tree."""
    gate = _gate_module()
    repo, head_sha, base_sha = _integration_fixture(tmp_path, regression=True)
    operations = gate.SubprocessGateOperations(repo)

    with pytest.raises(gate.IntegrationGateError) as captured:
        operations.integrate_and_test(head_sha, base_sha, lambda _message: None)

    assert captured.value.step == 4
    assert captured.value.reason == "suite-failed"


def test_different_functions_that_clean_merge_and_stay_green_are_not_rejected(tmp_path):
    """AC3: compatible edits remain mergeable and the integrated suite runs."""
    gate = _gate_module()
    repo, head_sha, base_sha = _integration_fixture(tmp_path, regression=False)
    operations = gate.SubprocessGateOperations(repo)

    observation = operations.integrate_and_test(head_sha, base_sha, lambda _message: None)

    assert observation.scope == "full"
    assert observation.targets == "skills/mission"
    assert len(observation.tree_sha) == 40


def test_scratch_deregistration_failure_stops_before_merge(tmp_path):
    gate = _gate_module()
    repo, head_sha, base_sha = _integration_fixture(tmp_path, regression=False)

    def runner(arguments, cwd):
        command = tuple(arguments)
        if command[:3] == ("git", "worktree", "remove"):
            return gate.CommandResult(1, "", "deregister failed")
        return gate._local_runner(command, cwd)

    operations = gate.SubprocessGateOperations(repo, runner=runner)

    with pytest.raises(gate.IntegrationGateError) as captured:
        operations.integrate_and_test(head_sha, base_sha, lambda _message: None)

    assert captured.value.reason == "scratch-cleanup-failed"


def test_cleanup_failure_is_logged_when_preserving_an_earlier_failure(tmp_path):
    gate = _gate_module()
    repo, head_sha, base_sha = _integration_fixture(tmp_path, regression=True)
    messages = []

    def runner(arguments, cwd):
        command = tuple(arguments)
        if command[:3] == ("git", "worktree", "remove"):
            return gate.CommandResult(1, "", "deregister failed")
        return gate._local_runner(command, cwd)

    operations = gate.SubprocessGateOperations(repo, runner=runner)

    with pytest.raises(gate.IntegrationGateError) as captured:
        operations.integrate_and_test(head_sha, base_sha, messages.append)

    assert captured.value.reason == "suite-failed"
    assert "scratch_cleanup=failed" in messages


def test_same_base_race_allows_first_merge_and_stops_second_at_final_fetch():
    """AC4: final base observation closes the tested-tree stale-result race."""
    gate = _gate_module()
    barrier = threading.Barrier(2)
    first_merged = threading.Event()
    remote = SimpleNamespace(base=OLD_BASE, merges=[])

    class ConcurrentOperations(ScriptedOperations):
        def __init__(self, role):
            super().__init__(gate)
            self.role = role
            self.base_read_count = 0

        def current_base_sha(self):
            self._assert_leased()
            self.base_read_count += 1
            if self.base_read_count == 1:
                return OLD_BASE
            return remote.base

        def integrate_and_test(self, _head_sha, _base_sha, _logger):
            self._assert_leased()
            barrier.wait(timeout=5)
            if self.role == "second":
                assert first_merged.wait(timeout=5)
            return gate.IntegrationObservation("full", "skills/mission", "e" * 40, FAKE_DIGEST)

        def merge_pull_request(self, pr_ref, expected_head_sha):
            self._assert_leased()
            remote.merges.append((self.role, pr_ref, expected_head_sha))
            remote.base = NEW_BASE
            first_merged.set()

    def execute(role):
        try:
            gate.gate_and_merge(
                "665",
                ConcurrentOperations(role),
                lambda _message: None,
                expected_changeset_digest=FAKE_DIGEST,
            )
            return "merged"
        except gate.IntegrationGateError as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(execute, ("first", "second")))

    assert outcomes == ["merged", "base-moved"]
    assert remote.merges == [("first", "665", HEAD_SHA)]


def test_repository_lease_serializes_the_complete_gate_interval(tmp_path):
    gate = _gate_module()
    repo = tmp_path / "leased"
    _init_repo(repo)
    first = gate.SubprocessGateOperations(repo)
    second = gate.SubprocessGateOperations(repo)
    waiting = threading.Event()
    acquired = threading.Event()

    def acquire_second():
        waiting.set()
        with second.lease():
            acquired.set()

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with first.lease():
            future = executor.submit(acquire_second)
            assert waiting.wait(timeout=5)
            assert acquired.wait(timeout=0.1) is False
            assert future.done() is False
        future.result(timeout=5)
        assert acquired.is_set()
    finally:
        executor.shutdown(wait=True)


@pytest.mark.parametrize("failure", ["fetch", "conflict", "suite", "base-moved"])
def test_fetch_conflict_suite_and_base_movement_all_exit_nonzero(failure):
    """AC5: every safety observation fails closed at the command boundary."""
    gate = _gate_module()
    kwargs = {}
    if failure == "fetch":
        kwargs["fetch_failure"] = True
    elif failure == "conflict":
        kwargs["integration_error"] = gate.IntegrationGateError(
            3, "merge-conflict", "手動統合してから再実行せよ"
        )
    elif failure == "suite":
        kwargs["integration_error"] = gate.IntegrationGateError(4, "suite-failed", "suite failed")
    elif failure == "base-moved":
        kwargs["base_shas"] = (OLD_BASE, NEW_BASE)
    operations = ScriptedOperations(gate, **kwargs)
    stdout = StringIO()
    stderr = StringIO()

    with pytest.raises(SystemExit) as captured:
        gate.execute_gate_and_merge_cli(
            str(REPO_ROOT),
            "665",
            operations=operations,
            stdout=stdout,
            stderr=stderr,
        )

    assert captured.value.code != 0
    assert "ERROR" in stderr.getvalue()
    assert operations.merges == []


def test_public_gate_command_maps_typed_failure_to_exit_two(monkeypatch):
    module = _mission_state_module()

    def reject(_request, _services):
        raise module.IntegrationGateFailure(3, "rejected", "gate rejected")

    monkeypatch.setattr(module, "run_integration_gate", reject)

    with pytest.raises(SystemExit) as captured:
        module.cmd_gate_and_merge(
            SimpleNamespace(
                pr="665",
                expected_head_sha=None,
                expected_base_sha=None,
                reviewed_changeset_digest=None,
            )
        )

    assert captured.value.code == 2


def test_queue_verified_head_is_forwarded_by_public_gate_adapter(monkeypatch, capsys):
    module = _mission_state_module()
    captured = {}

    def accept(request, _services):
        captured["request"] = request
        return {"status": "merged", "merge_commit_sha": MERGE_SHA}

    monkeypatch.setattr(module, "run_integration_gate", accept)
    args = module._build_parser().parse_args(
        [
            "gate-and-merge",
            "665",
            "--expected-head-sha",
            HEAD_SHA,
            "--expected-base-sha",
            OLD_BASE,
        ]
    )
    args.func(args)

    assert captured["request"].expected_head_sha == HEAD_SHA
    assert captured["request"].expected_base_sha == OLD_BASE
    assert json.loads(capsys.readouterr().out)["status"] == "merged"


def test_logs_include_test_scope_and_both_base_observations():
    """AC6: no safety stop is silent and tested scope is auditable."""
    gate = _gate_module()
    operations = ScriptedOperations(gate)
    messages = []

    result = gate.gate_and_merge(
        "665", operations, messages.append, expected_changeset_digest=FAKE_DIGEST
    )

    output = "\n".join(messages)
    assert f"base_sha(step=2)={OLD_BASE}" in output
    assert f"base_sha(step=5)={OLD_BASE}" in output
    assert "test_scope=full" in output
    assert "test_targets=skills/mission" in output
    assert result["merge_commit_sha"] == MERGE_SHA
    assert operations.fetches == 2
    assert operations.merges == [("665", HEAD_SHA)]
    assert operations.lease_active is False


def test_non_numeric_pr_reference_is_rejected_before_repository_effects():
    gate = _gate_module()
    operations = ScriptedOperations(gate)

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.gate_and_merge(
            "https://example.invalid/pr/665",
            operations,
            lambda _message: None,
            expected_changeset_digest=FAKE_DIGEST,
        )

    assert captured.value.reason == "invalid-pr-ref"
    assert operations.fetches == 0
    assert operations.merges == []


def test_queue_accepted_base_is_rejected_if_main_moved_before_gate():
    gate = _gate_module()
    operations = ScriptedOperations(gate, base_shas=(NEW_BASE, NEW_BASE))

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.gate_and_merge(
            "665",
            operations,
            lambda _message: None,
            expected_head_sha=HEAD_SHA,
            expected_base_sha=OLD_BASE,
            expected_changeset_digest=FAKE_DIGEST,
        )

    assert captured.value.reason == "accepted-base-moved"
    assert operations.merges == []


def test_pr_base_and_state_are_rechecked_before_merge():
    gate = _gate_module()
    operations = ScriptedOperations(gate)
    operations.snapshots[1] = gate.PullRequestSnapshot(
        665,
        HEAD_SHA,
        "release",
        "OPEN",
        None,
        None,
        OLD_BASE,
    )

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.gate_and_merge(
            "665", operations, lambda _message: None, expected_changeset_digest=FAKE_DIGEST
        )

    assert captured.value.reason == "pull-request-changed"
    assert operations.merges == []


def test_merge_readback_must_confirm_main_and_expected_head():
    gate = _gate_module()
    operations = ScriptedOperations(gate)
    operations.snapshots[2] = gate.PullRequestSnapshot(
        665,
        HEAD_SHA,
        "release",
        "MERGED",
        "2026-08-25T00:00:00Z",
        MERGE_SHA,
        OLD_BASE,
    )

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.gate_and_merge(
            "665", operations, lambda _message: None, expected_changeset_digest=FAKE_DIGEST
        )

    assert captured.value.reason == "merge-readback-failed"


def test_fetch_view_head_and_merge_commands_are_runner_injected(tmp_path):
    gate = _gate_module()
    commands = []
    open_payload = json.dumps(
        {
            "number": 665,
            "headRefOid": HEAD_SHA,
            "baseRefName": "main",
            "state": "OPEN",
            "mergedAt": None,
            "mergeCommit": None,
        }
    )

    def runner(arguments, _cwd):
        command = tuple(arguments)
        commands.append(command)
        if command[:3] == ("gh", "pr", "view"):
            return gate.CommandResult(0, open_payload, "")
        if command == ("git", "rev-parse", "FETCH_HEAD"):
            return gate.CommandResult(0, HEAD_SHA + "\n", "")
        if command == ("git", "remote", "get-url", "origin"):
            return gate.CommandResult(0, "git@github.com:acme/mission.git\n", "")
        return gate.CommandResult(0, "", "")

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    operations.fetch_base("main")
    snapshot = operations.read_pull_request("665")
    operations.fetch_pull_request_head(snapshot)
    operations.merge_pull_request("665", HEAD_SHA)

    # #698: 完全修飾で固定の私有 ref へ fetch する。
    # remote 名ではなく検証済み URL を直接渡す（名前は検査後に差し替えられうる）
    assert (
        "git",
        "fetch",
        "git@github.com:acme/mission.git",
        "+refs/heads/main:refs/mission-gate/base",
    ) in commands
    # #698: gh の操作先を origin 由来の host 修飾 identity へ固定する
    assert all(
        "--repo" in command and "github.com/acme/mission" in command
        for command in commands
        if command[:2] == ("gh", "pr")
    )
    assert ("git", "fetch", "git@github.com:acme/mission.git", "refs/pull/665/head") in commands
    assert (
        "gh",
        "pr",
        "merge",
        "665",
        "--repo",
        "github.com/acme/mission",
        "--squash",
        "--match-head-commit",
        HEAD_SHA,
    ) in commands


def test_docs_only_scope_delegates_to_existing_ci_selector(tmp_path):
    repo = tmp_path / "docs-only"
    _init_repo(repo)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    (repo / "test_initial.py").write_text(
        "def test_fixture():\n    assert True\n",
        encoding="utf-8",
    )
    _commit(repo, "initial")
    _run(repo, "git", "switch", "-q", "-c", "feature")
    (repo / "README.md").write_text("feature docs\n", encoding="utf-8")
    head_sha = _commit(repo, "feature docs")
    _run(repo, "git", "switch", "-q", "main")
    (repo / "base.txt").write_text("base docs\n", encoding="utf-8")
    base_sha = _commit(repo, "base docs")
    gate = _gate_module()

    commands = []

    def runner(arguments, cwd):
        commands.append(tuple(arguments))
        return gate._local_runner(arguments, cwd)

    observation = gate.SubprocessGateOperations(repo, runner=runner).integrate_and_test(
        head_sha, base_sha, lambda _message: None
    )

    helper = json.loads(
        _run(
            repo,
            "node",
            "-e",
            "const h=require(process.argv[1]);process.stdout.write(JSON.stringify(h.classifyChangedFiles({eventName:'pull_request',files:['README.md']})));",
            str(repo / "scripts" / "ci_changed_scopes.js"),
        ).stdout
    )
    assert observation.scope == "docs-only"
    assert observation.targets == helper["pythonTargets"]
    assert ("make", "test", "PYTEST_TARGETS={}".format(helper["pythonTargets"])) in commands


def test_queue_workflow_delegates_to_the_same_gate_command():
    skill = (REPO_ROOT / "skills" / "mission" / "SKILL.md").read_text(encoding="utf-8")
    state_management = (
        REPO_ROOT / "skills" / "mission" / "refs" / "state-management.md"
    ).read_text(encoding="utf-8")

    invocation = (
        "`queue verify` → `gate-and-merge <PR> --expected-head-sha <head_sha> "
        "--expected-base-sha <accepted_base_sha>`"
    )
    assert invocation in skill
    assert invocation in state_management
