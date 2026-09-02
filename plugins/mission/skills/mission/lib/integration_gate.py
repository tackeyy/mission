"""One fail-closed merge entrypoint over a freshly integrated tree."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Callable, Dict, Iterable, Optional, TextIO

from mission_application.integration_gate import (
    compute_changeset_digest,
    GateRuntimeServices,
    IntegrationGateFailure,
    IntegrationObservation,
    PullRequestSnapshot,
    run_gate_and_merge,
)


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_COMMAND_NOT_FOUND_EXIT = 127

_SCOPE_SCRIPT = """
const helper = require(process.argv[1]);
const input = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(helper.classifyChangedFiles(input)));
""".strip()


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class BinaryCommandResult:
    """digest 算出用。パッチ本文を decode せず bytes のまま扱う。"""

    returncode: int
    stdout: bytes = b""


IntegrationGateError = IntegrationGateFailure


def _local_runner(arguments: Iterable[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        list(arguments),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _local_binary_runner(arguments: Iterable[str], cwd: Path) -> "BinaryCommandResult":
    completed = subprocess.run(
        list(arguments),
        cwd=str(cwd),
        capture_output=True,
        check=False,
    )
    return BinaryCommandResult(completed.returncode, completed.stdout)


# 変更集合 digest を算出する diff は、ローカル設定に左右されてはならない。設定次第で
# 同じ変更集合が別の digest になると、レビュー側と merge 側で不一致が常態化し、
# 運用が「不一致を無視する」方向へ倒れる。差分の見た目を決める設定を明示的に固定する。
_DIGEST_GIT_CONFIG = (
    "-c", "core.quotepath=false",
    "-c", "diff.noprefix=false",
    "-c", "diff.mnemonicPrefix=false",
    "-c", "diff.algorithm=myers",
    "-c", "diff.renames=true",
    # 既定 400 のまま放置すると、ローカル設定が違うホストで rename 検出の打ち切りが
    # 変わり、同一の変更集合でも digest が動く
    "-c", "diff.renameLimit=400",
    "-c", "diff.external=",
    "-c", "diff.wsErrorHighlight=none",
)
_DIGEST_DIFF_FLAGS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--binary",
    "--full-index",
    "--find-renames",
)


def changeset_diff_command(base_sha: str, head_sha: str) -> tuple:
    """digest 算出に使う git コマンドを組み立てる。

    範囲は三点（`base...head`）。merge-base からの差分なので、base が進んでも
    head の分岐点が動かない限り同じ変更集合を指す。これが「base が動いても
    accepted を維持できる」根拠そのものであり、二点差分に変えてはならない。
    """
    return (
        ("git",)
        + _DIGEST_GIT_CONFIG
        + ("diff",)
        + _DIGEST_DIFF_FLAGS
        + ("{}...{}".format(base_sha, head_sha),)
    )


_BRANCH_UNSAFE = ("@{", "..", "\\", "~", "^", ":", "?", "*", "[", " ")
_ORIGIN_RE = re.compile(
    r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


def parse_origin_identity(url: str) -> str:
    """origin の URL から host 修飾つき identity を作る。

    gh は GH_REPO / GH_HOST でローカル解決を上書きできるため、fetch 元 (origin) と
    gh の操作先を同一 identity へ固定する。host まで含めるのは GH_HOST 対策。
    """
    if not isinstance(url, str):
        raise IntegrationGateError(2, "origin-identity-failed", "origin url is not a string")
    match = _ORIGIN_RE.match(url.strip())
    if match is None:
        raise IntegrationGateError(2, "origin-identity-failed", "origin url is not a supported github remote")
    owner, repo = match.group("owner"), match.group("repo")
    # `..` や `.` は path traversal 形になるため owner/repo として受理しない
    if owner in {".", ".."} or repo in {".", ".."}:
        raise IntegrationGateError(2, "origin-identity-failed", "origin url has an invalid path segment")
    return "github.com/{}/{}".format(owner, repo)


def validate_default_branch_name(name: object) -> str:
    """default branch 名として安全に使える短縮名だけを通す。

    `git check-ref-format --branch` は `@{-1}` を exit 0 で通すため使わない
    (実測: --branch は @{-n} を「以前 checkout した branch」として展開する)。
    完全修飾した refs/heads/<name> を通常モードで検証する。
    branch `HEAD` は check-ref-format を通るが refs/remotes/origin/HEAD と衝突するため拒否する。
    """
    if not isinstance(name, str) or not name or name == "HEAD":
        raise IntegrationGateError(2, "default-branch-resolution-failed", "default branch name is invalid")
    # refs/heads/main のような完全修飾形は短縮名として渡されると解決がずれるため拒否する
    if name.startswith("refs/"):
        raise IntegrationGateError(2, "default-branch-resolution-failed", "default branch name is invalid")
    if name.startswith("-") or name.startswith("/") or name.endswith("/"):
        raise IntegrationGateError(2, "default-branch-resolution-failed", "default branch name is invalid")
    if any(token in name for token in _BRANCH_UNSAFE):
        raise IntegrationGateError(2, "default-branch-resolution-failed", "default branch name is invalid")
    probe = subprocess.run(
        ("git", "check-ref-format", "refs/heads/{}".format(name)),
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise IntegrationGateError(2, "default-branch-resolution-failed", "default branch name is invalid")
    return name


def parse_symref_output(output: object) -> str:
    """`git ls-remote --symref origin HEAD` の出力から default branch 名を取り出す。"""
    if not isinstance(output, str):
        raise IntegrationGateError(2, "default-branch-resolution-failed", "symref output is not a string")
    # `ref: <target>\tHEAD` の形と、対応する `<40hex>\tHEAD` 行の両方を要求する。
    # 緩く読むと `ref: refs/heads/release\tNOT_HEAD` のような出力で
    # 非 default branch を default と誤認しうる。
    refs = []
    shas = []
    for line in output.split("\n"):
        if line == "":
            continue
        if line.startswith("ref: "):
            parts = line[len("ref: "):].split("\t")
            # 空白の混入を許すと malformed 行を通すため strip せず厳密一致で見る
            if len(parts) != 2 or parts[1] != "HEAD":
                raise IntegrationGateError(
                    2, "default-branch-resolution-failed", "symref line is malformed"
                )
            refs.append(parts[0])
            continue
        parts = line.split("\t")
        if len(parts) == 2 and _SHA_RE.fullmatch(parts[0]) and parts[1] == "HEAD":
            shas.append(parts[0])
            continue
        raise IntegrationGateError(
            2, "default-branch-resolution-failed", "symref output has unexpected content"
        )
    # SHA 行はちょうど 1 行を要求する（重複行も HEAD を一意に定めない）
    if len(refs) != 1 or len(shas) != 1:
        raise IntegrationGateError(2, "default-branch-resolution-failed", "symref output is not a single ref")
    ref = refs[0]
    if not ref.startswith("refs/heads/"):
        raise IntegrationGateError(2, "default-branch-resolution-failed", "symref target is not a branch")
    return validate_default_branch_name(ref[len("refs/heads/"):])


class SubprocessGateOperations:
    """Git and process adapter; all external commands are runner-injectable."""

    def __init__(
        self,
        repository_root: Path,
        *,
        runner: Optional[Callable[[Iterable[str], Path], CommandResult]] = None,
        binary_runner: Optional[Callable[[Iterable[str], Path], BinaryCommandResult]] = None,
        node_binary: str = "node",
    ):
        self.repository_root = Path(repository_root).resolve()
        self.runner = runner or _local_runner
        self.binary_runner = binary_runner or _local_binary_runner
        self.node_binary = node_binary

    def _run(self, arguments: Iterable[str], *, cwd: Optional[Path] = None) -> CommandResult:
        return self.runner(tuple(arguments), cwd or self.repository_root)

    def _checked(
        self,
        step: int,
        reason: str,
        arguments: Iterable[str],
        *,
        cwd: Optional[Path] = None,
    ) -> CommandResult:
        result = self._run(arguments, cwd=cwd)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if len(detail) > 1000:
                detail = detail[-1000:]
            suffix = ": " + detail if detail else ""
            raise IntegrationGateError(step, reason, reason + suffix)
        return result

    @contextmanager
    def lease(self):
        common = self._checked(
            1,
            "repository-unavailable",
            ("git", "rev-parse", "--git-common-dir"),
        ).stdout.strip()
        common_dir = Path(common)
        if not common_dir.is_absolute():
            common_dir = self.repository_root / common_dir
        lock_path = common_dir.resolve() / "mission-gate-and-merge.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(os.fspath(lock_path), flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise OSError("repository lease path is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise IntegrationGateError(1, "lease-failed", "repository lease acquisition failed") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _identity(self) -> str:
        """初回に解決した identity を返す。未解決なら解決して固定する。"""
        cached = getattr(self, "_origin_identity", None)
        if cached is None:
            return self.resolve_origin_identity()
        return cached

    def resolve_origin_identity(self) -> str:
        """origin の identity を解決し、初回値へ固定する。

        初回解決の後に origin が差し替わると、ログ上の repository と実際の gh 操作先が
        乖離しうる。2 回目以降は初回値と一致することを要求し、違えば fail-closed にする。
        """
        url = self._checked(
            2,
            "origin-identity-failed",
            ("git", "remote", "get-url", "origin"),
        ).stdout.strip()
        identity = parse_origin_identity(url)
        cached = getattr(self, "_origin_identity", None)
        if cached is None:
            self._origin_identity = identity
            # 以後の git 操作は remote 名ではなくこの URL を直接使う。
            # 名前 `origin` を使い続けると、検査と fetch の間に remote を
            # 差し替えられる TOCTOU が残るため。
            #
            # 限界: URL 直指定でも `url.<base>.insteadOf` による書き換えは残る。
            # ただしその設定を書ける主体は、本 gate の実装や `git` 実行ファイル
            # 自体も書き換えられる（= runtime 侵害）ため、本 gate の脅威モデル外とする。
            # 詳細は #701。
            self._origin_url = url
            return identity
        if identity != cached:
            raise IntegrationGateError(
                2,
                "origin-identity-moved",
                "origin repository identity changed during the gate",
            )
        return cached

    def _origin_url_pinned(self) -> str:
        """検証済みの origin URL を返す。未解決なら解決してから返す。"""
        url = getattr(self, "_origin_url", None)
        if url is None:
            self.resolve_origin_identity()
            url = getattr(self, "_origin_url")
        return url

    def resolve_default_branch(self) -> str:
        output = self._checked(
            2,
            "default-branch-resolution-failed",
            ("git", "ls-remote", "--symref", self._origin_url_pinned(), "HEAD"),
        ).stdout
        return parse_symref_output(output)

    def fetch_base(self, default_branch: str) -> None:
        # 完全修飾で固定の私有 ref へ書く。短縮名を渡すと refs/heads/main という名の
        # branch 等で解決がずれ、refs/remotes/origin/HEAD とも衝突しうる。
        self._checked(
            2,
            "fetch-failed",
            (
                "git",
                "fetch",
                self._origin_url_pinned(),
                "+refs/heads/{}:refs/mission-gate/base".format(default_branch),
            ),
        )

    def current_base_sha(self) -> str:
        value = self._checked(
            2,
            "base-observation-failed",
            ("git", "rev-parse", "--verify", "refs/mission-gate/base^{commit}"),
        ).stdout.strip()
        if _SHA_RE.fullmatch(value) is None:
            raise IntegrationGateError(2, "base-observation-failed", "base sha is invalid")
        return value

    def read_pull_request(self, pr_ref: str, step: int = 6) -> PullRequestSnapshot:
        result = self._checked(
            step,
            "pull-request-read-failed",
            (
                "gh",
                "pr",
                "view",
                str(pr_ref),
                "--repo",
                self._identity(),
                "--json",
                # #701: baseRefOid is the API's own view of the base branch tip.
                # git resolves the same branch through url.<base>.insteadOf, so
                # requiring the two to agree is what pins the destination.
                "number,headRefOid,baseRefName,baseRefOid,state,mergedAt,mergeCommit",
            ),
        )
        try:
            payload = json.loads(result.stdout)
            merge_commit = payload.get("mergeCommit")
            merge_sha = merge_commit.get("oid") if isinstance(merge_commit, dict) else None
            snapshot = PullRequestSnapshot(
                number=int(payload["number"]),
                head_sha=str(payload["headRefOid"]),
                base_ref_name=str(payload["baseRefName"]),
                state=str(payload["state"]),
                merged_at=payload.get("mergedAt"),
                merge_commit_sha=merge_sha,
                base_ref_oid=payload.get("baseRefOid"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntegrationGateError(step, "pull-request-read-failed", "pull request response is invalid") from exc
        if _SHA_RE.fullmatch(snapshot.head_sha) is None:
            raise IntegrationGateError(step, "pull-request-read-failed", "pull request head sha is invalid")
        return snapshot

    def fetch_pull_request_head(self, snapshot: PullRequestSnapshot) -> None:
        self._checked(
            3,
            "head-fetch-failed",
            ("git", "fetch", self._origin_url_pinned(), "refs/pull/{}/head".format(snapshot.number)),
        )
        fetched = self._checked(
            3,
            "head-fetch-failed",
            ("git", "rev-parse", "FETCH_HEAD"),
        ).stdout.strip()
        if fetched != snapshot.head_sha:
            raise IntegrationGateError(3, "head-changed", "pull request head changed during fetch")

    def _classify_scope(
        self,
        changed_files: Iterable[str],
        integrated_tree: Path,
        logger: Optional[Callable[[str], None]] = None,
    ) -> tuple[str, str]:
        """Select the suite scope, falling back to the full suite when unclassifiable.

        The helper is this repository's convention, not a contract every target
        repository accepts.  Treating its absence as a hard failure locked such
        repositories out of the merge procedure entirely (#697), which pushed
        callers toward the direct ``gh pr merge`` detour that Phase 7 forbids.

        Absence falls back to the full suite.  **That is a fallback, not a
        guarantee of wider coverage**: what "full" runs is decided by the
        target repository's own ``make test``, which may run fewer tests than
        the helper would have selected -- or none at all (#722).  A helper that
        *is* present but answers incorrectly still fails closed, so a broken
        classifier is not silently downgraded into a full run.
        """
        helper = integrated_tree / "scripts" / "ci_changed_scopes.js"
        if not helper.is_file():
            return self._full_scope_fallback("helper-missing", logger)
        payload = json.dumps(
            {"eventName": "pull_request", "files": list(changed_files)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            result = self._run(
                (self.node_binary, "-e", _SCOPE_SCRIPT, str(helper), payload),
            )
        except (FileNotFoundError, NotADirectoryError):
            # The interpreter is absent.  That is the same class of absence as a
            # missing helper: the target repository does not carry this
            # convention, so it must not be locked out of the merge procedure.
            #
            # PermissionError is deliberately NOT caught.  A present but
            # non-executable interpreter is a misconfiguration, and the design
            # rule for this gate is that "could not determine" never becomes
            # "safe" (docs/design/665-integration-gate.md).
            return self._full_scope_fallback("node-unavailable", logger)
        if result.returncode != 0:
            if self._node_is_unavailable(result):
                return self._full_scope_fallback("node-unavailable", logger)
            raise IntegrationGateError(
                4, "scope-selection-failed", "scope selector exited non-zero"
            )
        try:
            decision = json.loads(result.stdout)
            targets = decision["pythonTargets"]
            docs_only = decision["docsOnly"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise IntegrationGateError(4, "scope-selection-failed", "scope selector response is invalid") from exc
        if not isinstance(targets, str) or not targets.strip() or not isinstance(docs_only, bool):
            raise IntegrationGateError(4, "scope-selection-failed", "scope selector response is invalid")
        return ("docs-only" if docs_only else "full"), targets

    @staticmethod
    def _node_is_unavailable(result: CommandResult) -> bool:
        """Tell a missing interpreter apart from a helper that ran and failed.

        Two signals must agree.  Exit code 127 is the POSIX convention for a
        command that could not be executed at all, and an empty stdout means
        no classifier ever wrote its answer.  A helper that ran writes JSON to
        stdout, so stdout is the observable that distinguishes "never started"
        from "started and failed".

        The shell's message is deliberately NOT matched.  It is localized --
        a Japanese shell says the equivalent of "command not found" in
        Japanese -- so matching English text would make the decision depend on
        the operator's locale.  Matching any filesystem error text would be
        worse still: it would also swallow a helper that ran and then failed on
        its own missing file, turning a broken classifier into a silent full
        run.
        """
        return (
            result.returncode == _COMMAND_NOT_FOUND_EXIT
            and not (result.stdout or "").strip()
        )

    def _full_scope_fallback(
        self, reason: str, logger: Optional[Callable[[str], None]]
    ) -> tuple[str, str]:
        """Run everything the target repository's own ``make test`` defines.

        An empty target string means the caller omits ``PYTEST_TARGETS``, so the
        repository's Makefile decides what "everything" is.  A silent fallback
        would hide which suite actually ran, so the reason is always logged.

        **This does not guarantee that more tests run -- or that any run at
        all.**  The gate only reads the exit code of ``make test``; a target
        that executes nothing still exits 0 and the gate proceeds.  Closing
        that path needs a contract the target repository declares, which is
        being designed in #722.  Until then the gate's stated guarantee holds
        only for repositories that carry the scope helper.
        """
        if logger is not None:
            logger("scope_fallback={}".format(reason))
        return "full", ""

    def observe_changeset_digest(self, base_sha: str, head_sha: str, cwd: Path) -> str:
        """統合ツリー上で `git diff <merge-base>...<head>` の digest を観測する。

        算出できなければ digest を返さず fail-closed で止める。空文字や既定値を
        返すと、上位が「検査した」と誤認する。
        """
        result = self.binary_runner(changeset_diff_command(base_sha, head_sha), cwd)
        if result.returncode != 0:
            raise IntegrationGateError(
                4,
                "changeset-digest-failed",
                "changeset diff for the digest could not be produced",
            )
        # 空の変更集合で digest を成立させない。sha256("") は書式として妥当な値になり、
        # 「digest が一致した」が「変更集合を確認した」を意味しないケースを作る。
        #
        # 判定は decode も trim もせず、hash にかけるのと同じ生の bytes に対して行う。
        # 実在の `git diff` 出力は必ず非空白の header を含むため trim しても結果は
        # 変わらないが、**判定対象と hash 対象を同じものに保つ**ことで、片方だけが
        # 加工される余地を残さない。
        if len(result.stdout) == 0:
            raise IntegrationGateError(
                4,
                "changeset-empty",
                "changeset is empty; there is nothing for a review to have covered",
            )
        return compute_changeset_digest(result.stdout)

    def _cleanup_worktree(self, scratch_parent: Path, tree: Path, registered: bool) -> bool:
        deregistered = True
        if registered:
            removed = self._run(("git", "worktree", "remove", "--force", str(tree)))
            deregistered = removed.returncode == 0
        shutil.rmtree(scratch_parent, ignore_errors=True)
        return deregistered and not scratch_parent.exists()

    def integrate_and_test(
        self,
        head_sha: str,
        base_sha: str,
        logger: Callable[[str], None],
    ) -> IntegrationObservation:
        scratch_parent = Path(tempfile.mkdtemp(prefix="mission-integration-gate-"))
        tree = scratch_parent / "tree"
        registered = False
        try:
            self._checked(
                3,
                "scratch-worktree-failed",
                ("git", "worktree", "add", "--detach", str(tree), head_sha),
            )
            registered = True
            merged = self._run(("git", "merge", "--no-commit", "--no-ff", base_sha), cwd=tree)
            if merged.returncode != 0:
                self._run(("git", "merge", "--abort"), cwd=tree)
                detail = (merged.stdout + "\n" + merged.stderr).lower()
                if "conflict" in detail or "automatic merge failed" in detail:
                    raise IntegrationGateError(
                        3,
                        "merge-conflict",
                        "統合コンフリクト: 手動統合してから再実行せよ",
                    )
                raise IntegrationGateError(3, "merge-failed", "integration merge failed")
            tree_sha = self._checked(3, "tree-observation-failed", ("git", "write-tree"), cwd=tree).stdout.strip()
            if _SHA_RE.fullmatch(tree_sha) is None:
                raise IntegrationGateError(3, "tree-observation-failed", "integrated tree sha is invalid")
            changed = self._checked(
                4,
                "changed-files-failed",
                ("git", "diff", "--name-only", "-z", "{}...{}".format(base_sha, head_sha)),
                cwd=tree,
            ).stdout.split("\0")
            scope, targets = self._classify_scope(
                (item for item in changed if item),
                tree,
                logger,
            )
            logger("test_scope={} test_targets={}".format(scope, targets or "<repository default>"))
            changeset_digest = self.observe_changeset_digest(base_sha, head_sha, tree)
            suite_command = ("make", "test")
            if targets:
                suite_command += ("PYTEST_TARGETS={}".format(targets),)
            suite = self._run(suite_command, cwd=tree)
            if suite.returncode != 0:
                logger("suite_exit={}".format(suite.returncode))
                raise IntegrationGateError(4, "suite-failed", "integrated tree suite failed")
            logger("suite_exit=0")
            observation = IntegrationObservation(scope, targets, tree_sha, changeset_digest)
        except BaseException:
            if not self._cleanup_worktree(scratch_parent, tree, registered):
                logger("scratch_cleanup=failed")
            raise
        if not self._cleanup_worktree(scratch_parent, tree, registered):
            raise IntegrationGateError(4, "scratch-cleanup-failed", "scratch worktree cleanup failed")
        return observation

    def merge_pull_request(self, pr_ref: str, expected_head_sha: str) -> None:
        self._checked(
            6,
            "merge-command-failed",
            (
                "gh",
                "pr",
                "merge",
                str(pr_ref),
                "--repo",
                self._identity(),
                "--squash",
                "--match-head-commit",
                expected_head_sha,
            ),
        )


def gate_and_merge(
    pr_ref: str,
    operations,
    logger: Callable[[str], None],
    expected_head_sha: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
    expected_changeset_digest: Optional[str] = None,
) -> Dict[str, object]:
    return run_gate_and_merge(
        pr_ref,
        GateRuntimeServices(
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
        logger,
        expected_head_sha,
        expected_base_sha,
        expected_changeset_digest,
    )


def execute_changeset_digest(
    repository_root: str,
    base_sha: str,
    head_sha: str,
) -> Dict[str, object]:
    root = Path(repository_root)
    digest = SubprocessGateOperations(root).observe_changeset_digest(base_sha, head_sha, root)
    return {"changeset_digest": digest}


def execute_gate_and_merge(
    repository_root: str,
    pr_ref: str,
    expected_head_sha: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
    expected_changeset_digest: Optional[str] = None,
) -> Dict[str, object]:
    return gate_and_merge(
        pr_ref,
        SubprocessGateOperations(Path(repository_root)),
        print,
        expected_head_sha,
        expected_base_sha,
        expected_changeset_digest,
    )


def execute_gate_and_merge_cli(
    repository_root: str,
    pr_ref: str,
    *,
    operations=None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    expected_head_sha: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
    expected_changeset_digest: Optional[str] = None,
) -> Dict[str, object]:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    selected = operations or SubprocessGateOperations(Path(repository_root))
    try:
        result = gate_and_merge(
            pr_ref,
            selected,
            lambda message: print(message, file=output),
            expected_head_sha,
            expected_base_sha,
            expected_changeset_digest,
        )
    except IntegrationGateError as exc:
        print(
            "ERROR: step={} reason={}: {}".format(exc.step, exc.reason, exc),
            file=errors,
        )
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=output)
    return result
