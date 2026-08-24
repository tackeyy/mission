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
    GateRuntimeServices,
    IntegrationGateFailure,
    IntegrationObservation,
    PullRequestSnapshot,
    run_gate_and_merge,
)


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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


class SubprocessGateOperations:
    """Git and process adapter; all external commands are runner-injectable."""

    def __init__(
        self,
        repository_root: Path,
        *,
        runner: Optional[Callable[[Iterable[str], Path], CommandResult]] = None,
        node_binary: str = "node",
    ):
        self.repository_root = Path(repository_root).resolve()
        self.runner = runner or _local_runner
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

    def fetch_main(self) -> None:
        self._checked(2, "fetch-failed", ("git", "fetch", "--prune", "origin", "main"))

    def current_base_sha(self) -> str:
        value = self._checked(
            2,
            "base-observation-failed",
            ("git", "rev-parse", "origin/main"),
        ).stdout.strip()
        if _SHA_RE.fullmatch(value) is None:
            raise IntegrationGateError(2, "base-observation-failed", "origin/main sha is invalid")
        return value

    def read_pull_request(self, pr_ref: str) -> PullRequestSnapshot:
        result = self._checked(
            6,
            "pull-request-read-failed",
            (
                "gh",
                "pr",
                "view",
                str(pr_ref),
                "--json",
                "number,headRefOid,baseRefName,state,mergedAt,mergeCommit",
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
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise IntegrationGateError(6, "pull-request-read-failed", "pull request response is invalid") from exc
        if _SHA_RE.fullmatch(snapshot.head_sha) is None:
            raise IntegrationGateError(6, "pull-request-read-failed", "pull request head sha is invalid")
        return snapshot

    def fetch_pull_request_head(self, snapshot: PullRequestSnapshot) -> None:
        self._checked(
            3,
            "head-fetch-failed",
            ("git", "fetch", "origin", "refs/pull/{}/head".format(snapshot.number)),
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
    ) -> tuple[str, str]:
        helper = integrated_tree / "scripts" / "ci_changed_scopes.js"
        payload = json.dumps(
            {"eventName": "pull_request", "files": list(changed_files)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        result = self._checked(
            4,
            "scope-selection-failed",
            (self.node_binary, "-e", _SCOPE_SCRIPT, str(helper), payload),
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
            )
            logger("test_scope={} test_targets={}".format(scope, targets))
            suite = self._run(("make", "test", "PYTEST_TARGETS={}".format(targets)), cwd=tree)
            if suite.returncode != 0:
                logger("suite_exit={}".format(suite.returncode))
                raise IntegrationGateError(4, "suite-failed", "integrated tree suite failed")
            logger("suite_exit=0")
            observation = IntegrationObservation(scope, targets, tree_sha)
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
) -> Dict[str, object]:
    return run_gate_and_merge(
        pr_ref,
        GateRuntimeServices(
            lease=operations.lease,
            fetch_main=operations.fetch_main,
            current_base_sha=operations.current_base_sha,
            read_pull_request=operations.read_pull_request,
            fetch_pull_request_head=operations.fetch_pull_request_head,
            integrate_and_test=operations.integrate_and_test,
            merge_pull_request=operations.merge_pull_request,
        ),
        logger,
        expected_head_sha,
        expected_base_sha,
    )


def execute_gate_and_merge(
    repository_root: str,
    pr_ref: str,
    expected_head_sha: Optional[str] = None,
    expected_base_sha: Optional[str] = None,
) -> Dict[str, object]:
    return gate_and_merge(
        pr_ref,
        SubprocessGateOperations(Path(repository_root)),
        print,
        expected_head_sha,
        expected_base_sha,
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
        )
    except IntegrationGateError as exc:
        print(
            "ERROR: step={} reason={}: {}".format(exc.step, exc.reason, exc),
            file=errors,
        )
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=output)
    return result
