"""#698 第1段: gh 呼び出しの repository identity を origin から固定する。

背景（実測 2026-08-29）:
- `GH_REPO=other/repo gh pr view <N>` は別 repository の PR を返す。統合ゲートは
  `gh pr view` / `gh pr merge` に `--repo` を渡していないため、環境変数だけで
  別 repository の PR を読み・merge しうる。
- `--repo owner/name` は `GH_REPO` に優先するが、**`GH_HOST` の差し替えは防げない**。
  `--repo github.com/owner/name` のようにホストまで修飾すると `GH_HOST` を無効化できる。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import integration_gate as gate  # noqa: E402


def _ops(origin_url: str, *, recorder: list | None = None, fail_origin: bool = False):
    calls = recorder if recorder is not None else []

    def runner(arguments, cwd):
        argv = tuple(arguments)
        calls.append(argv)
        if argv[:3] == ("git", "remote", "get-url"):
            if fail_origin:
                return gate.CommandResult(128, "", "no such remote")
            return gate.CommandResult(0, origin_url + "\n", "")
        return gate.CommandResult(0, "", "")

    return gate.SubprocessGateOperations(Path.cwd(), runner=runner), calls


class TestOriginIdentity:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("git@github.com:tackeyy/mission.git", "github.com/tackeyy/mission"),
            ("git@github.com:tackeyy/mission", "github.com/tackeyy/mission"),
            ("https://github.com/tackeyy/mission.git", "github.com/tackeyy/mission"),
            ("https://github.com/tackeyy/mission", "github.com/tackeyy/mission"),
            ("ssh://git@github.com/tackeyy/mission.git", "github.com/tackeyy/mission"),
            ("git@ghe.example.com:team/repo.git", "ghe.example.com/team/repo"),
        ],
    )
    def test_derives_host_qualified_identity(self, url, expected):
        ops, _ = _ops(url)
        assert ops.repository_identity() == expected

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "not-a-url",
            "https://github.com/onlyowner",
            "git@github.com:",
            "https:///tackeyy/mission",
            "file:///tmp/local-repo",
        ],
    )
    def test_rejects_unparseable_origin(self, url):
        ops, _ = _ops(url)
        with pytest.raises(gate.IntegrationGateError) as excinfo:
            ops.repository_identity()
        assert excinfo.value.reason == "origin-identity-unresolved"

    def test_rejects_when_origin_lookup_fails(self):
        ops, _ = _ops("git@github.com:tackeyy/mission.git", fail_origin=True)
        with pytest.raises(gate.IntegrationGateError) as excinfo:
            ops.repository_identity()
        assert excinfo.value.reason == "origin-identity-unresolved"

    def test_identity_is_resolved_once_and_cached(self):
        ops, calls = _ops("git@github.com:tackeyy/mission.git")
        first = ops.repository_identity()
        second = ops.repository_identity()
        assert first == second
        lookups = [c for c in calls if c[:3] == ("git", "remote", "get-url")]
        assert len(lookups) == 1


class TestGhCallsArePinned:
    def _pr_payload(self) -> str:
        return (
            '{"number": 1, "headRefOid": "' + "a" * 40 + '", "baseRefName": "main",'
            ' "state": "OPEN", "mergedAt": null, "mergeCommit": null}'
        )

    def _ops_with_pr(self, recorder: list):
        payload = self._pr_payload()

        def runner(arguments, cwd):
            argv = tuple(arguments)
            recorder.append(argv)
            if argv[:3] == ("git", "remote", "get-url"):
                return gate.CommandResult(0, "git@github.com:tackeyy/mission.git\n", "")
            if argv[:3] == ("gh", "pr", "view"):
                return gate.CommandResult(0, payload, "")
            return gate.CommandResult(0, "", "")

        return gate.SubprocessGateOperations(Path.cwd(), runner=runner)

    def test_pr_view_pins_host_qualified_repo(self):
        calls: list = []
        ops = self._ops_with_pr(calls)
        ops.read_pull_request("1", step=3)
        view = [c for c in calls if c[:3] == ("gh", "pr", "view")][0]
        assert "--repo" in view
        assert view[view.index("--repo") + 1] == "github.com/tackeyy/mission"

    def test_pr_merge_pins_host_qualified_repo(self):
        calls: list = []
        ops = self._ops_with_pr(calls)
        ops.merge_pull_request("1", "b" * 40)
        merge = [c for c in calls if c[:3] == ("gh", "pr", "merge")][0]
        assert "--repo" in merge
        assert merge[merge.index("--repo") + 1] == "github.com/tackeyy/mission"

    def test_every_gh_call_uses_the_same_identity(self):
        calls: list = []
        ops = self._ops_with_pr(calls)
        ops.read_pull_request("1", step=3)
        ops.merge_pull_request("1", "b" * 40)
        gh_calls = [c for c in calls if c[0] == "gh"]
        assert gh_calls, "gh を 1 度も呼んでいない"
        identities = {c[c.index("--repo") + 1] for c in gh_calls if "--repo" in c}
        assert identities == {"github.com/tackeyy/mission"}
        assert all("--repo" in c for c in gh_calls), "--repo 未指定の gh 呼び出しがある"
