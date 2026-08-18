"""Closed CLI command ownership and C2 persistence migration inventory."""

from __future__ import annotations


def _owned(owner: str, *commands: str) -> dict[str, str]:
    return {command: owner for command in commands}


COMMAND_OWNER_REGISTRY = {
    **_owned(
        "A1.lifecycle",
        "activity end",
        "activity start",
        "advance",
        "cleanup-stale",
        "halt",
        "init",
        "mark-halt",
        "reactivate",
        "refresh-pid",
        "resume",
        "set",
        "update-project-root",
    ),
    **_owned(
        "A2.review",
        "aggregate-reviews",
        "closeout",
        "manual-score-capture",
        "mark-passes",
        "push-score",
        "review-finalize",
        "review-import",
        "supersede-reviews",
    ),
    **_owned(
        "A3.evidence",
        "artifact append",
        "artifact export",
        "artifact init",
        "artifact publish",
        "artifact render",
        "context-manifest",
        "progress clear",
        "progress update",
    ),
    **_owned(
        "A4.specialist-planning",
        "executor-handoff begin",
        "executor-handoff complete",
        "executor-handoff record-step",
        "executor-handoff verify-step",
        "planning adopt-core",
        "planning promote-provider-plan",
        "planning reselect",
        "specialists consent",
        "specialists invoke-command",
        "specialists invoke-prepared",
        "specialists log-invocation",
        "specialists plan-import",
        "specialists prepare-invocation",
        "specialists recommend",
        "specialists reconcile-invocation",
        "specialists verify-approval",
    ),
    **_owned(
        "A5.runtime-guard",
        "permission-preflight",
        "stop-guard-observe",
    ),
    **_owned(
        "R1.query",
        "codex-preflight",
        "freshness",
        "get",
        "lane-report",
        "learning brief",
        "list",
        "next",
        "progress get",
        "specialists accounting",
        "specialists summary",
        "stats",
        "stop-verdict",
    ),
    **_owned(
        "C1.separate-aggregate",
        "archive-worktree",
        "cleanup-empty",
        "handoff await",
        "handoff publish",
        "handoff verify",
        "parallel-closeout",
        "parallel-init",
        "parallel-status",
        "pregate check",
        "pregate digest",
        "pregate record",
        "queue enqueue",
        "queue mark",
        "queue next",
        "queue status",
        "queue verify",
        "resolve-archive",
    ),
}


C2_REPOSITORY_COMMANDS = frozenset(
    {
        "executor-handoff begin",
        "executor-handoff complete",
        "executor-handoff record-step",
        "executor-handoff verify-step",
        "planning reselect",
        "supersede-reviews",
        # Batch 2: specialists 8 commands
        "specialists recommend",
        "specialists log-invocation",
        "specialists verify-approval",
        "specialists prepare-invocation",
        "specialists invoke-command",
        "specialists invoke-prepared",
        "specialists reconcile-invocation",
        "specialists plan-import",
        # Batch 3: planning authority + scoring authority
        "planning adopt-core",
        "planning promote-provider-plan",
        "manual-score-capture",
    }
)


# All mutating commands are now repository-owned.  The allowlist is empty.
C2_DIRECT_WRITE_ALLOWLIST = frozenset()


# Parser adapters that can reach a direct legacy session writer or lock.
# Migration is complete; no cmd_* functions write directly.
C2_DIRECT_WRITE_FUNCTIONS = frozenset()


NON_SESSION_DIRECT_CALL_FUNCTIONS = frozenset(
    {
        "cmd_archive_worktree",
        "cmd_codex_preflight",
        "cmd_resolve_archive",
        "cmd_specialists_consent",
    }
)
