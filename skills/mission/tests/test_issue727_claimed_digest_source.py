"""Issue #727: digest の「出所の申告」を gate が受け取り、出力に残す。

`--reviewed-changeset-digest` は Checker の報告から転記する引数であり、gate の実行時に
計算して渡してはならない（`docs/design/665-integration-gate.md`）。**この規律を機械で
強制することはできない。** gate が観測できるのは値だけで、その値がどこから来たかは
観測できないからである。

そこで本 suite は、**強制ではなく申告の記録**を固定する。

- 申告は digest と双方向で対になる（片方だけの指定は成立しない）
- 申告値は閉じた列挙で、未知の値は受理しない
- 申告は結果へ残る（後から監査できる）
- **digest を渡さない既存の呼び出しは、従来どおり動く**

**この記録は出所の証跡ではなく、出所の申告である。** producer が自算した値に
`checker-comment` と申告することは可能で、gate はそれを検出しない。
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


class RecordingOperations:
    """merge まで到達する正常系の fake。"""

    def __init__(self, *, digest=DIGEST, default_branch="main"):
        self.digest = digest
        self.default_branch = default_branch
        self.merges = []
        self.lease_entries = 0
        self.snapshots = [
            gate.PullRequestSnapshot(1, HEAD_SHA, default_branch, "OPEN", None, None, BASE_SHA),
            gate.PullRequestSnapshot(1, HEAD_SHA, default_branch, "OPEN", None, None, BASE_SHA),
            gate.PullRequestSnapshot(
                1, HEAD_SHA, default_branch, "MERGED", "2026-09-04T00:00:00Z", MERGE_SHA,
                BASE_SHA,
            ),
        ]

    @contextmanager
    def lease(self):
        # 検査が lease の外で終わることを固定するための spy。記録しないと、検査を
        # lease の内側へ移しても全テストが通ってしまう。
        self.lease_entries += 1
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


def run(operations, **kwargs):
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
        lambda _message: None,
        **kwargs,
    )


class TestPairing:
    """digest と申告は双方向で対になる。片方だけを許すと記録が欠ける。"""

    @pytest.mark.parametrize("source", ["checker-comment", "argv-manual"])
    def test_digest_with_source_merges_and_records_the_claim(self, source):
        operations = RecordingOperations()
        result = run(
            operations,
            expected_changeset_digest=DIGEST,
            claimed_digest_source=source,
        )
        assert result["status"] == "merged"
        assert result["claimed_digest_source"] == source
        assert operations.merges == [("1", HEAD_SHA)]

    def test_digest_without_source_fails_closed(self):
        """digest だけ渡せると、申告のない digest が記録なしで通る。"""
        operations = RecordingOperations()
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            run(operations, expected_changeset_digest=DIGEST)
        assert excinfo.value.reason == "claimed-digest-source-missing"
        assert excinfo.value.step == 1
        assert operations.merges == []

    def test_source_without_digest_fails_closed(self):
        """申告だけ渡せると、検証していない merge に出所が付く。"""
        operations = RecordingOperations()
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            run(operations, claimed_digest_source="checker-comment")
        assert excinfo.value.reason == "reviewed-changeset-digest-missing"
        assert excinfo.value.step == 1
        assert operations.merges == []


class TestValidationHappensBeforeTheLease:
    """不正な入力は merge 経路へ入る前に落とす。

    lease は他セッターとの排他であり、取得したまま失敗すると、その間ほかの gate が
    待たされる。**入力の形式だけで判定できるものを、資源を取ってから判定しない。**
    """

    @pytest.mark.parametrize(
        ("kwargs", "reason"),
        [
            (
                {"expected_changeset_digest": "not-a-digest", "claimed_digest_source": "nope"},
                "invalid-expected-changeset-digest",
            ),
            (
                {"expected_changeset_digest": DIGEST, "claimed_digest_source": "nope"},
                "invalid-claimed-digest-source",
            ),
            (
                {"expected_changeset_digest": DIGEST},
                "claimed-digest-source-missing",
            ),
            (
                {"claimed_digest_source": "checker-comment"},
                "reviewed-changeset-digest-missing",
            ),
        ],
    )
    def test_invalid_input_fails_at_step_one_without_taking_the_lease(self, kwargs, reason):
        operations = RecordingOperations()
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            run(operations, **kwargs)
        assert (excinfo.value.step, excinfo.value.reason) == (1, reason)
        assert operations.lease_entries == 0
        assert operations.merges == []

    def test_valid_input_does_take_the_lease(self):
        """通るべきものが通ることも固定する。これが無いと、何でも落とす実装でも緑になる。"""
        operations = RecordingOperations()
        run(
            operations,
            expected_changeset_digest=DIGEST,
            claimed_digest_source="checker-comment",
        )
        assert operations.lease_entries == 1


class TestPortContract:
    """port の arity は入力によって変わらない。

    申告が無いときだけ 5 引数で呼ぶ互換経路を作ると、port の実装者はどちらの契約へ
    合わせるか決められず、**壊れ方が入力依存になる**。6 引数固定を契約として固定する。
    """

    def _run_with_port(self, request):
        seen = {}

        def execute(*args):
            seen["args"] = args
            return {"status": "merged"}

        application.run_integration_gate(
            request, application.IntegrationGateServices(execute=execute)
        )
        return seen["args"]

    def test_port_receives_six_arguments_without_a_claim(self):
        args = self._run_with_port(
            application.IntegrationGateRequest(repository_root=".", pr_ref="1")
        )
        assert len(args) == 6
        assert args[5] is None

    def test_port_receives_the_claim_when_present(self):
        args = self._run_with_port(
            application.IntegrationGateRequest(
                repository_root=".",
                pr_ref="1",
                expected_changeset_digest=DIGEST,
                claimed_digest_source="argv-manual",
            )
        )
        assert len(args) == 6
        assert args[4:] == (DIGEST, "argv-manual")


class TestClaimVocabulary:
    """申告値は閉じた列挙。未知の値を通すと、記録の意味が呼び出し側依存になる。"""

    @pytest.mark.parametrize(
        "source",
        ["", "checker", "Checker-Comment", "argv manual", "unknown", " checker-comment"],
    )
    def test_unknown_source_fails_closed(self, source):
        operations = RecordingOperations()
        with pytest.raises(application.IntegrationGateFailure) as excinfo:
            run(
                operations,
                expected_changeset_digest=DIGEST,
                claimed_digest_source=source,
            )
        assert excinfo.value.reason == "invalid-claimed-digest-source"
        assert operations.merges == []


class TestBackwardCompatibility:
    """digest を渡さない既存の呼び出しは、そのまま動き続ける。"""

    def test_neither_argument_still_merges(self):
        operations = RecordingOperations()
        result = run(operations)
        assert result["status"] == "merged"
        assert operations.merges == [("1", HEAD_SHA)]

    def test_result_records_the_absence_of_a_claim(self):
        """キー自体は常に出す。欠落と `未申告` を後から区別できなくなるため。"""
        operations = RecordingOperations()
        result = run(operations)
        assert "claimed_digest_source" in result
        assert result["claimed_digest_source"] is None


class TestCliAdapter:
    """CLI が申告を受け取り、application まで運ぶ。"""

    def _module(self):
        import importlib.util

        path = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
        spec = importlib.util.spec_from_file_location("mission_state_727", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mission_state_727"] = module
        spec.loader.exec_module(module)
        return module

    def test_parser_forwards_the_claim(self, monkeypatch, capsys):
        module = self._module()
        captured = {}

        def accept(request, _services):
            captured["request"] = request
            return {"status": "merged", "merge_commit_sha": MERGE_SHA}

        monkeypatch.setattr(module, "run_integration_gate", accept)
        args = module._build_parser().parse_args(
            [
                "gate-and-merge",
                "727",
                "--reviewed-changeset-digest",
                DIGEST,
                "--claimed-digest-source",
                "checker-comment",
            ]
        )
        args.func(args)

        assert captured["request"].expected_changeset_digest == DIGEST
        assert captured["request"].claimed_digest_source == "checker-comment"
        assert json.loads(capsys.readouterr().out)["status"] == "merged"

    def test_parser_rejects_unknown_source_by_name_and_lists_the_vocabulary(self, capsys):
        """`SystemExit` だけを見ない。**未知のオプション名でも SystemExit になる**ため、
        それだけでは「語彙を検査した」ことの証拠にならない。

        引数名と受理可能な値が利用者へ提示されることまで固定する。
        """
        module = self._module()
        with pytest.raises(SystemExit) as captured:
            module._build_parser().parse_args(
                [
                    "gate-and-merge",
                    "727",
                    "--reviewed-changeset-digest",
                    DIGEST,
                    "--claimed-digest-source",
                    "made-up",
                ]
            )
        assert captured.value.code == 2
        message = capsys.readouterr().err
        assert "--claimed-digest-source" in message
        assert "made-up" in message
        for value in ("checker-comment", "argv-manual"):
            assert value in message

    def test_the_cli_vocabulary_is_the_application_vocabulary(self):
        """CLI と application が別の語彙を持たないことを固定する。

        `choices` は `CLAIMED_DIGEST_SOURCES` から作る。列挙を 1 箇所に保たないと、
        CLI が受理する値を application が拒否する（あるいはその逆）状態が生まれる。
        """
        module = self._module()
        assert tuple(module.CLAIMED_DIGEST_SOURCES) == application.CLAIMED_DIGEST_SOURCES

    def test_cli_rejects_the_vocabulary_before_reaching_the_application(self, monkeypatch):
        """**CLI 経路では argparse が語彙を先に拒否する。** application の順序
        （digest 書式 → 語彙 → 対）とは異なる。

        これは意図した差である。argparse は `--help` に受理値を載せ、実行前に落とす。
        **順序を保証しているのは application 経路の契約であり、CLI 経路ではない**ことを
        テストとして明示し、後から「順序が壊れた」と誤読されないようにする。
        """
        module = self._module()
        reached = []
        monkeypatch.setattr(
            module, "run_integration_gate", lambda *_a, **_k: reached.append(True)
        )
        with pytest.raises(SystemExit):
            module._build_parser().parse_args(
                [
                    "gate-and-merge",
                    "727",
                    "--reviewed-changeset-digest",
                    "not-a-digest",
                    "--claimed-digest-source",
                    "made-up",
                ]
            )
        assert reached == []

    def test_cli_forwards_pairing_failures_to_the_application(self, monkeypatch, capsys):
        """語彙として正しい入力は application まで届き、対の検査で落ちる。

        argparse が語彙を先に見ることと、**対の検査が CLI で握り潰されていない**ことは
        別の話である。後者をここで固定する。
        """
        module = self._module()

        def reject(_request, _services):
            raise module.IntegrationGateFailure(
                1,
                "reviewed-changeset-digest-missing",
                "a claimed digest source requires the reviewed changeset digest it describes",
            )

        monkeypatch.setattr(module, "run_integration_gate", reject)
        args = module._build_parser().parse_args(
            [
                "gate-and-merge",
                "727",
                "--claimed-digest-source",
                "checker-comment",
            ]
        )
        with pytest.raises(SystemExit) as captured:
            args.func(args)
        assert captured.value.code == 2
        assert "reviewed-changeset-digest-missing" in capsys.readouterr().err

    def test_existing_invocation_without_the_claim_still_builds_a_request(
        self, monkeypatch, capsys
    ):
        module = self._module()
        captured = {}

        def accept(request, _services):
            captured["request"] = request
            return {"status": "merged", "merge_commit_sha": MERGE_SHA}

        monkeypatch.setattr(module, "run_integration_gate", accept)
        module.cmd_gate_and_merge(
            SimpleNamespace(
                pr="727",
                expected_head_sha=None,
                expected_base_sha=None,
                reviewed_changeset_digest=None,
                claimed_digest_source=None,
            )
        )

        assert captured["request"].claimed_digest_source is None
        assert json.loads(capsys.readouterr().out)["status"] == "merged"


class TestInfrastructureSeam:
    """infra 層も申告を運ぶ。ここで落ちると CLI から application まで届かない。"""

    def test_gate_and_merge_forwards_the_claim(self):
        operations = RecordingOperations()
        result = gate.gate_and_merge(
            "1",
            operations,
            lambda _message: None,
            None,
            None,
            DIGEST,
            "argv-manual",
        )
        assert result["claimed_digest_source"] == "argv-manual"
