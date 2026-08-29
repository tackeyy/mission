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


class TestIdentityIsFailClosed:
    """異系統レビュー(High)で受理と判明した入力を fail-closed へ倒す。

    port を黙って捨てると別エンドポイントの identity を同一とみなす。
    owner/name に `..`・制御文字・`@`・`?`・`#` を通すと、gh 側がほぼそのまま
    扱うため identity 固定の意味が失われる。
    """

    @pytest.mark.parametrize(
        "url",
        [
            # port を黙って破棄しない（別エンドポイントを同一視しない）
            "https://github.com:8443/tackeyy/mission.git",
            "ssh://git@github.com:2222/tackeyy/mission.git",
            # owner / name に使えない文字
            "https://github.com/tack..eyy/mission",
            "https://github.com/tackeyy/mis..sion",
            "https://github.com/tackeyy/mission?x=1",
            "https://github.com/tackeyy/mission#frag",
            "https://github.com/tack@eyy/mission",
            "https://github.com/tackeyy/mis sion",
            # host が不正
            "https://exa mple.com/tackeyy/mission",
            "https://../tackeyy/mission",
        ],
    )
    def test_rejects_unsafe_identity_components(self, url):
        ops, _ = _ops(url)
        with pytest.raises(gate.IntegrationGateError) as excinfo:
            ops.repository_identity()
        assert excinfo.value.reason == "origin-identity-unresolved"

    @pytest.mark.parametrize(
        "url",
        [
            # owner は英数で始まり英数で終わる（先頭・末尾のハイフンを許さない）
            "https://github.com/-lead/mission",
            "https://github.com/trail-/mission",
            # owner / name の文字集合外。上の明示拒否リストには載っていない文字を選ぶ
            "https://github.com/tack~eyy/mission",
            "https://github.com/tackeyy/mis~sion",
            "https://github.com/tackeyy/mis+sion",
            "https://github.com/tackeyy/mis%20sion",
            "https://github.com/tack:eyy/mission",
            # name が単独のドット
            "https://github.com/tackeyy/.",
        ],
    )
    def test_rejects_characters_outside_the_allowed_set(self, url):
        """文字集合の検証それ自体に検出力があることを固定する。

        `..` や空白・`@` は別の検査でも落ちるため、**この検査だけが落とす入力**を選ぶ。
        （変異テストで、文字集合検証を外しても他の検査が拾ってしまい
        気付けなかったため追加した）
        """
        ops, _ = _ops(url)
        with pytest.raises(gate.IntegrationGateError) as excinfo:
            ops.repository_identity()
        assert excinfo.value.reason == "origin-identity-unresolved"

    @pytest.mark.parametrize(
        "url",
        [
            # 連続・先頭・末尾のスラッシュを畳み込まない。
            # 畳み込むと別 URL が同一 identity になり fail-closed が崩れる。
            "https://github.com//tackeyy/mission",
            "https://github.com/tackeyy//mission",
            "https://github.com/tackeyy/mission/",
            "https://github.com//tackeyy//mission//",
            "git@github.com:/tackeyy/mission.git",
            "git@github.com://tackeyy/mission.git",
            "git@github.com:tackeyy//mission.git",
            "git@github.com:tackeyy/mission/.git",
        ],
    )
    def test_rejects_slash_folding(self, url):
        """空 segment を捨てて 2 要素に畳み込まない。

        `//o/n` `o//n` `/o/n` がすべて `host/o/n` になると、
        別のエンドポイントを同一視することになる。
        """
        ops, _ = _ops(url)
        with pytest.raises(gate.IntegrationGateError) as excinfo:
            ops.repository_identity()
        assert excinfo.value.reason == "origin-identity-unresolved"

    def test_rejects_control_characters(self):
        ops, _ = _ops("https://github.com/tackeyy/mis\x01sion")
        with pytest.raises(gate.IntegrationGateError):
            ops.repository_identity()

    def test_host_is_normalised_to_lower_case(self):
        ops, _ = _ops("https://GitHub.COM/tackeyy/mission.git")
        assert ops.repository_identity() == "github.com/tackeyy/mission"

    def test_accepts_legitimate_dots_and_dashes(self):
        ops, _ = _ops("https://github.com/my-org/my.repo_name-1.git")
        assert ops.repository_identity() == "github.com/my-org/my.repo_name-1"


class TestIdentityResolvedBeforeAnyRemoteUse:
    """lease 取得直後に identity を確定する。

    遅延解決だと、lease 取得後・最初の PR 読取前に走る fetch の間に
    origin を差し替えられる窓が残る（異系統レビュー Medium）。
    """

    def test_lease_resolves_identity_before_yielding(self, tmp_path):
        calls: list = []

        def runner(arguments, cwd):
            argv = tuple(arguments)
            calls.append(argv)
            if argv[:3] == ("git", "remote", "get-url"):
                return gate.CommandResult(0, "git@github.com:tackeyy/mission.git\n", "")
            if argv[:2] == ("git", "rev-parse"):
                return gate.CommandResult(0, str(common) + "\n", "")
            return gate.CommandResult(0, "", "")

        common = tmp_path / ".git"
        common.mkdir()
        ops = gate.SubprocessGateOperations(tmp_path, runner=runner)
        with ops.lease():
            inside = list(calls)
        assert any(c[:3] == ("git", "remote", "get-url") for c in inside), (
            "lease の内側へ入る前に identity が確定していない"
        )


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
