"""Issue #721: merge は「レビュー時の変更集合」と一致するときだけ通す。

`~/.claude/rules/git-workflow.md` の「変更集合の不変性による代替」は、機械検証を
備える repo に限り base 移動後の accepted 維持を認める。その条件は
**不一致・算出不能・digest 未指定のいずれでも fail-closed で止まる**ことである。
本 suite はその 3 条件を固定する。
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "skills" / "mission" / "lib"))

import integration_gate as gate  # noqa: E402
from mission_application import integration_gate as application  # noqa: E402

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "c" * 40
TREE_SHA = "d" * 40
PATCH = b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
DIGEST = hashlib.sha256(PATCH).hexdigest()
OTHER_DIGEST = hashlib.sha256(PATCH + b"\n").hexdigest()


class RecordingOperations:
    """merge まで到達する正常系の fake。digest は差し替えられる。"""

    def __init__(self, *, digest=DIGEST, default_branch="main"):
        self.digest = digest
        self.default_branch = default_branch
        self.merges = []
        self.snapshots = [
            gate.PullRequestSnapshot(1, HEAD_SHA, default_branch, "OPEN", None, None),
            gate.PullRequestSnapshot(1, HEAD_SHA, default_branch, "OPEN", None, None),
            gate.PullRequestSnapshot(
                1, HEAD_SHA, default_branch, "MERGED", "2026-08-30T00:00:00Z", MERGE_SHA
            ),
        ]

    @contextmanager
    def lease(self):
        yield

    def resolve_origin_identity(self):
        return "github.com/acme/widgets"

    def resolve_default_branch(self):
        return self.default_branch

    def fetch_base(self, branch):
        pass

    def current_base_sha(self):
        return BASE_SHA

    def read_pull_request(self, pr_ref, step=6):
        return self.snapshots.pop(0)

    def fetch_pull_request_head(self, snapshot):
        pass

    def integrate_and_test(self, head_sha, base_sha, logger):
        return application.IntegrationObservation("full", "all", TREE_SHA, self.digest)

    def merge_pull_request(self, pr_ref, expected_head_sha):
        self.merges.append((pr_ref, expected_head_sha))


def run(operations, *, expected_changeset_digest=DIGEST, logs=None):
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
        (logs.append if logs is not None else (lambda _message: None)),
        expected_changeset_digest=expected_changeset_digest,
    )


class TestFailClosedConditions:
    """規約が要求する 3 条件。ひとつでも通ると代替の前提が崩れる。"""

    def test_matching_digest_merges(self):
        operations = RecordingOperations()
        result = run(operations)
        assert result["status"] == "merged"
        assert result["changeset_digest"] == DIGEST
        assert operations.merges == [("1", HEAD_SHA)]

    def test_mismatching_digest_stops_before_merge(self):
        operations = RecordingOperations(digest=OTHER_DIGEST)
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            run(operations)
        assert excinfo.value.reason == "changeset-digest-mismatch"
        assert operations.merges == []

    def test_unspecified_digest_does_not_relax_anything(self):
        """digest は任意。渡さなければ緩和が適用されないだけで、gate の既存要求は残る。

        参照実装 (company-os verify-exact-head.mjs) と同じ意味論。全 merge に digest を
        課すと、実装者は「引数を埋める最も自然な方法」＝実行時に計算する方法を選び、
        gate の観測値と同じ計算から出た値を渡すことになる。**常に一致するので検証は
        空回りし、儀式だけが残る。** 渡す行為に意味を持たせるため任意にする。
        """
        operations = RecordingOperations()
        result = run(operations, expected_changeset_digest=None)
        assert result["status"] == "merged"
        # 観測値は検査の有無にかかわらず記録する（後から何を merge したか追える）
        assert result["changeset_digest"] == DIGEST

    def test_unobtainable_digest_is_tolerated_when_none_was_reviewed(self):
        """digest を渡していない以上、観測できなくても止める根拠がない。"""
        operations = RecordingOperations(digest="")
        result = run(operations, expected_changeset_digest=None)
        assert result["status"] == "merged"

    @pytest.mark.parametrize(
        "digest",
        [
            "",
            "not-a-digest",
            "A" * 64,
            "a" * 63,
            "a" * 65,
            " " + "a" * 64,
            DIGEST.upper(),
            123,
        ],
    )
    def test_malformed_expected_digest_is_rejected(self, digest):
        operations = RecordingOperations()
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            run(operations, expected_changeset_digest=digest)
        assert excinfo.value.reason == "invalid-expected-changeset-digest"
        assert operations.merges == []

    @pytest.mark.parametrize("observed", ["", None, "not-a-digest", "a" * 63])
    def test_unobtainable_observed_digest_stops_before_merge(self, observed):
        """算出不能を「検査不要」に読み替えない。"""
        operations = RecordingOperations(digest=observed)
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            run(operations)
        assert excinfo.value.reason == "changeset-digest-unavailable"
        assert operations.merges == []


class TestOrdering:
    def test_malformed_digest_is_rejected_before_the_lease(self):
        """引数の不備で lease を取らない。取ると他セッションを無用に待たせる。"""
        acquired = []

        class Watching(RecordingOperations):
            @contextmanager
            def lease(self):
                acquired.append(True)
                yield

        with pytest.raises(application.IntegrationGateFailure):
            run(Watching(), expected_changeset_digest="not-a-digest")
        assert acquired == []

    def test_digest_is_logged_for_later_audit(self):
        logs = []
        run(RecordingOperations(), logs=logs)
        assert any("changeset_digest={}".format(DIGEST) in line for line in logs)


# --- 実 git による検出力の実証 -------------------------------------------------
#
# 「digest を出す」ことと「変更集合の同一性を判定できる」ことは別である。値が出る
# 実装は書式だけで通ってしまうため、以下では実際に変異を注入して分離を確認する。


def _git(repo: Path, *args: str) -> str:
    import subprocess

    completed = subprocess.run(
        ("git",) + args,
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "fixture@example.invalid")
    _git(path, "config", "user.name", "fixture")
    (path / "base.txt").write_text("base\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "base")
    return path


def _digest(repo: Path, base: str, head: str) -> str:
    return gate.SubprocessGateOperations(repo).observe_changeset_digest(base, head, repo)


@pytest.fixture()
def diverged(tmp_path):
    """main と feature が分岐した実 repository を作る。"""
    repo = _init_repo(tmp_path / "repo")
    root = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "feature.txt").write_text("feature\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feature change")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return repo, root, head


class TestDetectionPower:
    def test_unrelated_base_movement_keeps_the_digest(self, diverged):
        """base が進んでも変更集合が動いていなければ digest は同じ。

        これが「base 移動後も accepted を維持できる」根拠そのもの。ここが不安定なら
        代替は成立しない。
        """
        repo, base, head = diverged
        before = _digest(repo, base, head)

        (repo / "unrelated.txt").write_text("unrelated\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "unrelated main commit")
        moved_base = _git(repo, "rev-parse", "HEAD")

        assert moved_base != base
        assert _digest(repo, moved_base, head) == before

    def test_changed_changeset_changes_the_digest(self, diverged):
        """head 側の変更内容が動けば digest は変わる（＝検出できる）。"""
        repo, base, head = diverged
        before = _digest(repo, base, head)

        _git(repo, "checkout", "-q", "feature")
        (repo / "feature.txt").write_text("feature tampered\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "--amend", "-m", "feature change")
        tampered = _git(repo, "rev-parse", "HEAD")

        assert _digest(repo, base, tampered) != before

    def test_added_file_changes_the_digest(self, diverged):
        """変更集合へのファイル追加も検出する。"""
        repo, base, head = diverged
        before = _digest(repo, base, head)

        _git(repo, "checkout", "-q", "feature")
        (repo / "extra.txt").write_text("extra\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", "extra")
        extended = _git(repo, "rev-parse", "HEAD")

        assert _digest(repo, base, extended) != before

    def test_commit_message_alone_does_not_change_the_digest(self, diverged):
        """判定対象は変更集合であって commit の並びではない。"""
        repo, base, head = diverged
        before = _digest(repo, base, head)

        _git(repo, "checkout", "-q", "feature")
        _git(repo, "commit", "-q", "--amend", "-m", "reworded")
        reworded = _git(repo, "rev-parse", "HEAD")

        assert reworded != head
        assert _digest(repo, base, reworded) == before

    @pytest.mark.parametrize(
        "setting",
        [
            ("diff.noprefix", "true"),
            ("diff.mnemonicPrefix", "true"),
            ("core.quotepath", "true"),
            ("diff.algorithm", "histogram"),
            ("diff.renameLimit", "1"),
        ],
    )
    def test_local_diff_configuration_does_not_move_the_digest(self, diverged, setting):
        """ローカル設定で digest が動くと、レビュー側と merge 側で恒常的に不一致になる。"""
        repo, base, head = diverged
        before = _digest(repo, base, head)
        _git(repo, "config", setting[0], setting[1])
        assert _digest(repo, base, head) == before

    def test_empty_changeset_does_not_produce_a_digest(self, diverged):
        """#731: 空の変更集合で digest を成立させない。

        `sha256("")` は書式として妥当な 64 桁小文字 hex になる。そのまま返すと
        **「digest が一致した」が「レビューされた変更集合を確認した」を意味しない**
        ケースが生まれ、機構の主張が「変更集合が空である場合を除いて」という
        但し書きを暗黙に持つ。但し書きを暗黙に持つ検査は次に読む人が気づけない。
        """
        repo, base, _head = diverged
        _git(repo, "checkout", "-q", "-b", "empty-only", base)
        _git(repo, "commit", "-q", "--allow-empty", "-m", "no content change")
        empty_head = _git(repo, "rev-parse", "HEAD")

        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            _digest(repo, base, empty_head)
        assert excinfo.value.reason == "changeset-empty"

    def test_empty_changeset_failure_is_distinct_from_unobtainable(self, diverged):
        """算出不能（revision が無い）と、算出できたが空、を混同しない。"""
        repo, base, _head = diverged
        _git(repo, "checkout", "-q", "-b", "empty-only-2", base)
        _git(repo, "commit", "-q", "--allow-empty", "-m", "no content change")
        empty_head = _git(repo, "rev-parse", "HEAD")

        with pytest.raises(application.IntegrationGateFailure) as empty:
            _digest(repo, base, empty_head)
        with pytest.raises(application.IntegrationGateFailure) as unknown:
            _digest(repo, "0" * 40, empty_head)
        assert empty.value.reason != unknown.value.reason

    def test_digest_is_unobtainable_for_an_unknown_revision(self, diverged):
        """算出不能は静かに通さず fail-closed で止める。"""
        repo, _base, head = diverged
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            _digest(repo, "0" * 40, head)
        assert excinfo.value.reason == "changeset-digest-failed"


class TestCommandLine:
    """producer と merge 側が同じ digest を得られること。

    producer が手元で `git diff | shasum` を組み立てると diff 設定の差で別の値が出る。
    同じ実装を通す経路を用意し、それが gate の観測値と一致することを固定する。
    """

    def _run_cli(self, repo: Path, *args: str):
        import subprocess

        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"), *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )

    def test_cli_digest_matches_the_gate_observation(self, diverged):
        import json

        repo, base, head = diverged
        completed = self._run_cli(repo, "changeset-digest", "--base-sha", base, "--head-sha", head)
        assert completed.returncode == 0, completed.stderr
        reported = json.loads(completed.stdout)["changeset_digest"]
        assert reported == _digest(repo, base, head)

    def test_cli_digest_fails_closed_on_an_unknown_revision(self, diverged):
        repo, _base, head = diverged
        completed = self._run_cli(repo, "changeset-digest", "--base-sha", "0" * 40, "--head-sha", head)
        assert completed.returncode == 2
        assert "ERROR" in completed.stderr
