"""#698: gate-and-merge が default branch を main と決め打ちしない。

不変条件は「merge 先はその repository の default branch のみ」。literal `main` を外しても
この不変条件は維持する。base は PR の申告値ではなく origin から解決する
（PR 由来値を信じると、queue の accepted SHA と同じ SHA を指す非 default branch へ
retarget することで別 branch へ merge できてしまうため）。
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import integration_gate as gate  # noqa: E402
from mission_application import integration_gate as application  # noqa: E402

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "c" * 40


class RecordingOperations:
    """default branch を master とする repository を模した fake。"""

    def __init__(self, *, default_branch="master", identity="github.com/acme/widgets",
                 base_shas=None, resolver_results=None, snapshots=None):
        self.identity = identity
        self.resolver_results = list(resolver_results or [default_branch, default_branch])
        self.base_shas = iter(base_shas or [BASE_SHA, BASE_SHA])
        self.calls = []
        self.snapshots = list(snapshots or [
            gate.PullRequestSnapshot(1, HEAD_SHA, default_branch, "OPEN", None, None),
            gate.PullRequestSnapshot(1, HEAD_SHA, default_branch, "OPEN", None, None),
            gate.PullRequestSnapshot(1, HEAD_SHA, default_branch, "MERGED", "2026-08-29T00:00:00Z", MERGE_SHA),
        ])

    @contextmanager
    def lease(self):
        yield

    def resolve_origin_identity(self):
        self.calls.append(("identity",))
        return self.identity

    def resolve_default_branch(self):
        self.calls.append(("resolve",))
        if not self.resolver_results:
            raise gate.IntegrationGateError(2, "default-branch-resolution-failed", "exhausted")
        value = self.resolver_results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_base(self, branch):
        self.calls.append(("fetch_base", branch))

    def current_base_sha(self):
        return next(self.base_shas)

    def read_pull_request(self, pr_ref, step=6):
        return self.snapshots.pop(0)

    def fetch_pull_request_head(self, snapshot):
        pass

    def integrate_and_test(self, head_sha, base_sha, logger):
        return application.IntegrationObservation("full", "all", "d" * 40)

    def merge_pull_request(self, pr_ref, expected_head_sha):
        self.calls.append(("merge", pr_ref, expected_head_sha))


def run(operations, logs=None):
    return application.run_gate_and_merge(
        "1",
        application.GateRuntimeServices(
            lease=operations.lease,
            resolve_origin_identity=operations.resolve_origin_identity,
            resolve_default_branch=operations.resolve_default_branch,
            fetch_base=operations.fetch_base,
            current_base_sha=operations.current_base_sha,
            read_pull_request=operations.read_pull_request,
            fetch_pull_request_head=operations.fetch_pull_request_head,
            integrate_and_test=operations.integrate_and_test,
            merge_pull_request=operations.merge_pull_request,
        ),
        (logs.append if logs is not None else (lambda _m: None)),
    )


def test_master_repository_completes_the_gate():
    operations = RecordingOperations(default_branch="master")
    result = run(operations)
    assert result["status"] == "merged"
    assert ("fetch_base", "master") in operations.calls


def test_main_repository_still_completes_the_gate():
    operations = RecordingOperations(default_branch="main")
    result = run(operations)
    assert result["status"] == "merged"
    assert ("fetch_base", "main") in operations.calls


def test_non_default_base_is_rejected_even_when_base_sha_matches():
    """v1 設計の High: accepted SHA が一致していても非 default branch は拒否する。"""
    operations = RecordingOperations(default_branch="master", snapshots=[
        gate.PullRequestSnapshot(1, HEAD_SHA, "release/v2", "OPEN", None, None),
        gate.PullRequestSnapshot(1, HEAD_SHA, "release/v2", "OPEN", None, None),
        gate.PullRequestSnapshot(1, HEAD_SHA, "release/v2", "MERGED", "2026-08-29T00:00:00Z", MERGE_SHA),
    ])
    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)
    assert excinfo.value.reason == "pull-request-not-mergeable"
    assert not any(call[0] == "merge" for call in operations.calls)


def test_default_branch_moving_between_observations_is_rejected():
    operations = RecordingOperations(resolver_results=["master", "main"])
    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)
    assert excinfo.value.reason == "default-branch-moved"
    assert not any(call[0] == "merge" for call in operations.calls)


def test_second_resolver_failure_stops_the_gate():
    operations = RecordingOperations(resolver_results=[
        "master",
        gate.IntegrationGateError(5, "default-branch-resolution-failed", "boom"),
    ])
    with pytest.raises(gate.IntegrationGateError):
        run(operations)
    assert not any(call[0] == "merge" for call in operations.calls)


def test_identity_is_resolved_before_any_pull_request_read():
    operations = RecordingOperations()
    run(operations)
    assert operations.calls[0] == ("identity",)


def test_logs_report_identity_and_default_branch():
    logs = []
    run(RecordingOperations(default_branch="master"), logs=logs)
    joined = "\n".join(logs)
    assert "github.com/acme/widgets" in joined
    assert "master" in joined


@pytest.mark.parametrize("value", ["@{-1}", "refs/heads/main", "HEAD", "", "-x", "a..b"])
def test_adapter_rejects_unsafe_branch_names(value):
    with pytest.raises(gate.IntegrationGateError):
        gate.validate_default_branch_name(value)


@pytest.mark.parametrize("value", ["main", "master", "release/v2", "feature/a-b"])
def test_adapter_accepts_ordinary_branch_names(value):
    assert gate.validate_default_branch_name(value) == value


@pytest.mark.parametrize("url,expected", [
    ("git@github.com:acme/widgets.git", "github.com/acme/widgets"),
    ("https://github.com/acme/widgets.git", "github.com/acme/widgets"),
    ("https://github.com/acme/widgets", "github.com/acme/widgets"),
    ("ssh://git@github.com/acme/widgets.git", "github.com/acme/widgets"),
])
def test_origin_identity_is_host_qualified(url, expected):
    assert gate.parse_origin_identity(url) == expected


@pytest.mark.parametrize("url", [
    "git@gitlab.com:acme/widgets.git",
    "https://example.com/acme/widgets.git",
    "not-a-url",
    "",
    "git@github.com:acme.git",
])
def test_origin_identity_rejects_unsupported_remotes(url):
    with pytest.raises(gate.IntegrationGateError):
        gate.parse_origin_identity(url)


@pytest.mark.parametrize("output,expected", [
    ("ref: refs/heads/master\tHEAD\n" + BASE_SHA + "\tHEAD\n", "master"),
    ("ref: refs/heads/main\tHEAD\n" + BASE_SHA + "\tHEAD\n", "main"),
])
def test_symref_parsing_extracts_branch_name(output, expected):
    assert gate.parse_symref_output(output) == expected


@pytest.mark.parametrize("output", [
    "",
    BASE_SHA + "\tHEAD\n",
    "ref: refs/tags/v1\tHEAD\n",
    "ref: refs/heads/a\tHEAD\n" + BASE_SHA + "\tHEAD\nref: refs/heads/b\tHEAD\n",
    "garbage",
    # SHA 行が無い（malformed）
    "ref: refs/heads/main\tHEAD\n",
    # HEAD 以外を指す symref を default と誤認しない
    "ref: refs/heads/release\tNOT_HEAD\n" + BASE_SHA + "\tHEAD\n",
    # 余分な行が混ざる
    "ref: refs/heads/main\tHEAD\n" + BASE_SHA + "\tHEAD\nunexpected\n",
])
def test_symref_parsing_rejects_unexpected_output(output):
    with pytest.raises(gate.IntegrationGateError):
        gate.parse_symref_output(output)


def test_gh_calls_are_pinned_to_origin_identity(tmp_path):
    """GH_REPO / GH_HOST でローカル解決を上書きされても、gh の操作先は origin 由来へ固定する。"""
    commands = []
    payload = (
        '{"number":1,"headRefOid":"' + HEAD_SHA + '","baseRefName":"master",'
        '"state":"OPEN","mergedAt":null,"mergeCommit":null}'
    )

    def runner(arguments, _cwd):
        command = tuple(arguments)
        commands.append(command)
        if command == ("git", "remote", "get-url", "origin"):
            return gate.CommandResult(0, "git@github.com:acme/widgets.git\n", "")
        if command[:3] == ("gh", "pr", "view"):
            return gate.CommandResult(0, payload, "")
        return gate.CommandResult(0, "", "")

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    operations.read_pull_request("1")
    operations.merge_pull_request("1", HEAD_SHA)

    gh_commands = [command for command in commands if command[:2] == ("gh", "pr")]
    assert gh_commands
    for command in gh_commands:
        assert "--repo" in command
        assert command[command.index("--repo") + 1] == "github.com/acme/widgets"


def test_origin_identity_failure_stops_before_any_gh_call(tmp_path):
    commands = []

    def runner(arguments, _cwd):
        command = tuple(arguments)
        commands.append(command)
        if command == ("git", "remote", "get-url", "origin"):
            return gate.CommandResult(1, "", "no such remote")
        return gate.CommandResult(0, "", "")

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    with pytest.raises(gate.IntegrationGateError) as excinfo:
        operations.read_pull_request("1")
    assert excinfo.value.reason == "origin-identity-failed"
    assert not any(command[:1] == ("gh",) for command in commands)


def test_default_branch_resolution_failure_is_reported(tmp_path):
    def runner(arguments, _cwd):
        if tuple(arguments)[:3] == ("git", "ls-remote", "--symref"):
            return gate.CommandResult(1, "", "network down")
        return gate.CommandResult(0, "", "")

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    with pytest.raises(gate.IntegrationGateError) as excinfo:
        operations.resolve_default_branch()
    assert excinfo.value.reason == "default-branch-resolution-failed"


def test_base_fetch_uses_fully_qualified_private_ref(tmp_path):
    commands = []

    def runner(arguments, _cwd):
        commands.append(tuple(arguments))
        return gate.CommandResult(0, BASE_SHA + "\n", "")

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    operations.fetch_base("master")
    assert operations.current_base_sha() == BASE_SHA
    assert (
        "git",
        "fetch",
        "--prune",
        "origin",
        "+refs/heads/master:refs/mission-gate/base",
    ) in commands
    assert (
        "git",
        "rev-parse",
        "--verify",
        "refs/mission-gate/base^{commit}",
    ) in commands


def test_post_step_six_retarget_is_detected_only_at_readback():
    """既知の限界: step 6 以降の retarget は予防できず read-back で検出されるだけ。

    --match-head-commit は head SHA のみを固定し base の compare-and-swap を提供しない
    (SKILL.md の「保証しないもの」に既出)。本変更はこの窓を広げも狭めもしない。
    """
    operations = RecordingOperations(default_branch="master", snapshots=[
        gate.PullRequestSnapshot(1, HEAD_SHA, "master", "OPEN", None, None),
        gate.PullRequestSnapshot(1, HEAD_SHA, "master", "OPEN", None, None),
        # merge 後の read-back で初めて base の差し替えが露見する
        gate.PullRequestSnapshot(1, HEAD_SHA, "release/v2", "MERGED", "2026-08-29T00:00:00Z", MERGE_SHA),
    ])
    with pytest.raises(application.IntegrationGateFailure) as excinfo:
        run(operations)
    assert excinfo.value.reason == "merge-readback-failed"
    # merge 自体は実行されてしまう（予防できていない）ことを固定する
    assert any(call[0] == "merge" for call in operations.calls)


def test_origin_identity_drift_during_the_gate_is_rejected(tmp_path):
    """初回解決の後に origin が差し替わったら fail-closed にする。

    固定しないと「ログ上の repository は A、実際の gh 操作先は B」が成立してしまう。
    """
    urls = iter([
        "git@github.com:acme/widgets.git\n",
        "git@github.com:evil/widgets.git\n",
    ])
    commands = []

    def runner(arguments, _cwd):
        command = tuple(arguments)
        commands.append(command)
        if command == ("git", "remote", "get-url", "origin"):
            return gate.CommandResult(0, next(urls), "")
        return gate.CommandResult(0, "", "")

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    assert operations.resolve_origin_identity() == "github.com/acme/widgets"
    with pytest.raises(gate.IntegrationGateError) as excinfo:
        operations.resolve_origin_identity()
    assert excinfo.value.reason == "origin-identity-moved"


def test_gh_calls_reuse_the_first_resolved_identity(tmp_path):
    """gh 呼び出しは初回 identity を再利用し、origin を再解決しない。"""
    resolutions = []
    payload = (
        '{"number":1,"headRefOid":"' + HEAD_SHA + '","baseRefName":"master",'
        '"state":"OPEN","mergedAt":null,"mergeCommit":null}'
    )

    def runner(arguments, _cwd):
        command = tuple(arguments)
        if command == ("git", "remote", "get-url", "origin"):
            resolutions.append(command)
            return gate.CommandResult(0, "git@github.com:acme/widgets.git\n", "")
        if command[:3] == ("gh", "pr", "view"):
            assert command[command.index("--repo") + 1] == "github.com/acme/widgets"
            return gate.CommandResult(0, payload, "")
        return gate.CommandResult(0, "", "")

    operations = gate.SubprocessGateOperations(tmp_path, runner=runner)
    operations.resolve_origin_identity()
    operations.read_pull_request("1")
    operations.merge_pull_request("1", HEAD_SHA)
    # 初回の 1 回だけ。gh 呼び出しごとに再解決しない
    assert len(resolutions) == 1
