"""Apply the Stop guard's own commands, in the guard's own process (#779).

The hook used to apply them: it read `command.kind` from the verdict, ran
`mission-state.py` again with the matching subcommand, fed the receipt back,
and looped.  One decision cost up to seven processes, and all of them shared
one 8-second budget, so a loaded host spent the budget on process startup and
the guard returned `guard-budget-exhausted` -- observed five times in a single
session.

Applying in-process removes the startup cost.  What it must not remove is the
shape the rest of the guard depends on:

* **the receipt stays `(exit_code, stdout)`.**  `resolve_guard_command_receipt`
  is unchanged, so the verdict side sees exactly what a child process produced;
* **`GuardTimeout` is not converted.**  It is a `BaseException` raised by the
  alarm so that the outermost decorator can emit `guard-budget-exhausted`.
  Turning it into a failed receipt would resolve it as an ordinary command
  failure -- the terminal reason would change, and a fired alarm would be
  swallowed;
* **nothing is resolved from the process.**  The session id and the project
  root arrive as arguments.  Writing them into `os.environ` or `os.chdir()`
  would change which session the *next* decision treats as its own.

The command set lives here rather than in the shell, and is closed: the hook
no longer carries a `case` block whose labels could drift from the kinds the
guard can emit.  What the shell still owns is the complementary check, that it
never runs a subcommand other than `stop-verdict` at all -- a table here cannot
see a `python3 ... mark-halt` line added to the hook.

This module is deliberately free of I/O: every effect is a callable the adapter
passes in.  That keeps the effects inside functions the thin-adapter ratchet
already measures, instead of creating new ones it would reject.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Mapping

from mission_application.runtime_guard import (
    GuardCommandKind,
    GuardCommandReceipt,
    decide_stop_guard,
    resolve_guard_command_receipt,
)

# The closed set.  `none` is a kind the guard emits (it means "stop, print the
# reply"), so it belongs in the table even though applying it does nothing:
# leaving it out would make "unknown kind" and "nothing to do" the same case.
GUARD_COMMAND_KINDS = frozenset({
    "none",
    "mark-halt",
    "cleanup-stale",
    "stop-guard-observe",
})

_DISPATCH_MISMATCH = "guard-command-dispatch-mismatch"


def validate_guard_command_dispatch(appliers: Mapping[str, object]) -> None:
    """Fail unless the table is exactly the kinds the guard can emit.

    Missing and extra keys are both errors.  A missing kind would be applied by
    nothing and reported as an unknown command; an extra one would let a kind
    the guard never emits be applied if a future decision produced it.
    """
    if set(appliers) != set(GUARD_COMMAND_KINDS):
        raise ValueError(_DISPATCH_MISMATCH)


def no_application(_command: object) -> tuple[int, str]:
    """Apply the `none` kind: there is nothing to run, and that is not a failure."""
    return (0, "")


def capture_application(
    apply,
    args,
    *,
    redirect_stdout,
    buffer_factory,
):
    """Run one application and describe it the way a child process did.

    The command prints its result; the verdict side parses that text out of the
    receipt (`resolve_guard_command_receipt`).  In a child process the pipe did
    the separating.  Here the guard's own stdout carries the verdict, so the
    command's output is captured instead -- otherwise it would be printed ahead
    of the verdict and the hook would read the two as one document.

    `redirect_stdout` and `buffer_factory` are passed in so this module keeps
    importing nothing from the standard library's I/O surface.

    `GuardTimeout` is not caught: it derives from `BaseException`, so the
    `except Exception` below lets it reach the decorator that owns the budget
    and produces the terminal `guard-budget-exhausted` verdict (#779 D2-c).
    """
    buffer = buffer_factory()
    try:
        with redirect_stdout(buffer):
            apply(args)
    except SystemExit as exit_request:
        code = exit_request.code
        return (code if isinstance(code, int) else 1, buffer.getvalue())
    except Exception:  # noqa: BLE001 - the receipt is the diagnosis channel
        return (2, buffer.getvalue())
    return (0, buffer.getvalue())


def receipt_decision(prior, kind, exit_code, stdout, *, request_for_orphan, hook_input):
    """Turn one application's outcome into the next decision.

    The same resolution the hook drove with two file descriptors, done with the
    values in hand.  `resolve_guard_command_receipt` is untouched, so the
    verdict side cannot tell the difference between this and a child process.

    The orphan path re-decides from a fresh request, because processing one
    orphan changes which state files remain to be considered.
    """
    receipt = GuardCommandReceipt(
        decision_id=prior.decision_id,
        kind=GuardCommandKind(kind),
        exit_code=exit_code,
        stdout=stdout,
    )
    resolved = resolve_guard_command_receipt(prior, receipt)
    if resolved.reason_code != "orphan-processed":
        return resolved
    return decide_stop_guard(replace(
        request_for_orphan(hook_input),
        processed_orphan_state_files=(
            resolved.continuation.processed_orphan_state_files
        ),
    ))


def resolve_with_applications(
    decision,
    *,
    appliers,
    request_for_orphan,
    hook_input,
    limit,
):
    """Apply commands until the guard asks for none, and return that decision.

    This is the loop the hook used to run with one process per step.  `limit`
    bounds the walk so a decision cycle cannot spin: the budget already bounds
    wall time, but a cycle would spend all of it here and report exhaustion
    rather than the cycle.
    """
    validate_guard_command_dispatch(appliers)
    for _ in range(limit):
        kind = decision.command.kind.value
        if kind == "none":
            return decision
        applier = appliers.get(kind)
        if applier is None:
            raise ValueError(_DISPATCH_MISMATCH)
        exit_code, stdout = applier(decision)
        decision = receipt_decision(
            decision,
            kind,
            exit_code,
            stdout,
            request_for_orphan=request_for_orphan,
            hook_input=hook_input,
        )
    raise ValueError("guard-command-application-did-not-settle")


def project_root_of(args, current_root, path_factory):
    """Return the root the command names, or the process's own.

    The adapter passes `Path.cwd` and `Path` rather than calling them, so the
    choice lives here: `cmd_*` must not grow a branch for the guard's sake, and
    the guard must not reach the process's cwd when a command named a root.
    """
    named = getattr(args, "guard_project_root", None)
    if named is None:
        return current_root()
    return path_factory(named)


def state_file_of(args, root, session_file, resolve_for_process):
    """Return the state file for the session the command names, or this one's.

    Resolving from `MISSION_SESSION_ID` is right for the CLI and wrong for the
    guard: the guard's own session is not the session it is halting.
    """
    named = getattr(args, "guard_session_id", None)
    if named is None:
        return resolve_for_process(root)
    return session_file(root, named)


class GuardCommandArgs:
    """The fields one `cmd_*` reads, carried without a parser.

    Deliberately not `argparse.Namespace`: this module imports nothing from the
    standard library's I/O or CLI surface, and the commands only read
    attributes.
    """

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


def apply_command(kind, decision, run, *, redirect_stdout, buffer_factory):
    """Build the command's arguments and apply it, in one call.

    The adapter calls this and nothing else: the thin-adapter ratchet counts a
    call nested inside an allowlisted call as the adapter composing behaviour
    of its own, so the shaping of arguments belongs here rather than at the
    call site.
    """
    return capture_application(
        run,
        _ARGS_BUILDERS[kind](decision),
        redirect_stdout=redirect_stdout,
        buffer_factory=buffer_factory,
    )


def mark_halt_args(command):
    return GuardCommandArgs(
        reason=command.reason,
        category=command.category.value,
        guard_project_root=command.cwd,
        guard_session_id=command.session_id,
    )


def cleanup_stale_args(command):
    return GuardCommandArgs(root=command.root, execute=command.execute)


def stop_guard_observe_args(command, project_root):
    return GuardCommandArgs(
        session_id=command.session_id,
        digest=command.digest,
        now_epoch=command.now_epoch,
        ttl_seconds=command.ttl_seconds,
        guard_project_root=project_root,
    )


def _stop_guard_observe_args_from(decision):
    return stop_guard_observe_args(
        decision.command, decision.continuation.project_root
    )


_ARGS_BUILDERS = {
    "mark-halt": lambda decision: mark_halt_args(decision.command),
    "cleanup-stale": lambda decision: cleanup_stale_args(decision.command),
    "stop-guard-observe": _stop_guard_observe_args_from,
}
