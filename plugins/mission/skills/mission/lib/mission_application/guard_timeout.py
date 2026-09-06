"""Self-applied time limit for the Stop guard (Issue #742, decisions D2 and D3).

The shell adapter used to bound `mission-state.py` with `timeout`, falling back to
`perl -e 'alarm ...'`, and running the command **unbounded** when neither existed.
A host without both commands could therefore hang the Stop hook forever.

D2 moves the limit into the command itself, so it no longer depends on `timeout` or
`perl` being installed. It is **not** unconditional: without ``SIGALRM`` there is no
in-process mechanism to interrupt the call, and what remains is the shell adapter's
external limit or, failing that, the host's own hook timeout.

D3 fixes the value handling: only the values both sides accept are honoured, and every
other value falls back to the default rather than becoming a failure of its own.
"""

from __future__ import annotations

import functools
import math
import os
import signal
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, TypeVar

# The host bounds the whole hook at 10 seconds (`claude-hooks/hooks.json`). The
# guard's own limit has to stay inside that budget, or the host cuts first and the
# guard never gets to emit its block -- the host discards the output instead.
#
# So this value is both the default *and* the ceiling. A larger override is not
# clamped but rejected outright, because the shell adapter has to reach the same
# verdict for the same input and cannot clamp without numeric comparison (#615).
DEFAULT_GUARD_TIMEOUT_SECONDS = 8

# The shell adapter validates the same value with a `case` pattern, and #615 forbids
# numeric comparison there. So the accepted set is spelled out literally on both sides
# rather than expressed as a range -- a range would need `-le`, and two different
# formulations would drift apart. Anything outside this set falls back to the default.
# #754: the total is split into an execution budget and a reserve. `1` and `2`
# would leave an execution budget of zero or less -- a permanent block -- so they
# are not accepted any more; they fall back to the default like any other bad
# value, and `budget_clamped_from` makes that fall-back observable.
_ACCEPTED = frozenset({"3", "4", "5", "6", "7", "8"})
_CLAMPED = frozenset({"1", "2"})

# Seconds kept back from every command's limit so that, once the execution budget
# is spent, the tail -- one child's start-up and import (about 0.3 s), stopping it,
# and writing the block -- still fits inside the host's own timeout. Measured in
# #749: the tail is 0.4-0.5 s; two seconds is about four times that.
RESERVE_SECONDS = 2

# The shell copies the caller's original value here *before* normalising it, so the
# guard can tell "the caller asked for 1 and got 8" from "the caller asked for 8".
RAW_ENV_VAR = "MISSION_STATE_TIMEOUT_RAW"

# The one reason every budget exhaustion reports, whether the decorator refused to
# start the body or the alarm interrupted it.
EXHAUSTED_REASON = "guard-budget-exhausted"


class GuardTimeout(BaseException):
    """Raised in-process when the budget is spent.

    A ``BaseException`` on purpose (#754): the commands wrap their bodies in
    ``except Exception`` to turn defects into a diagnosable block, and the alarm
    used to be swallowed there as ``guard-decision-unavailable``. Exhaustion is
    not a defect; it travels past those handlers to the decorator, which owns
    the terminal verdict.
    """


class GuardBudgetLost(GuardTimeout, Exception):
    """A continuation arrived without a usable deadline (#742 D3').

    Still a ``GuardTimeout`` for callers that check the family, and still an
    ``Exception`` so its handling does not change with #754: it is raised by
    the decorator before the command body runs, reaches ``main()``'s generic
    handler, and is reported as a typed ``internal-error`` with exit 1 -- not
    as the terminal exhaustion verdict, which would misname a lost budget.
    """


def resolve_guard_timeout(raw: Optional[object]) -> int:
    """Return the limit in seconds, falling back to the default for any bad value.

    A bad value is not an error: the guard still has to produce a verdict, and
    refusing to run because the environment held ``"8s"`` would block every Stop.
    """
    # No `.strip()`: the shell's `case` does not strip either, and a value only one
    # side accepts is exactly the drift this set is meant to prevent.
    if str(raw) not in _ACCEPTED:
        return DEFAULT_GUARD_TIMEOUT_SECONDS
    return int(str(raw))


@contextmanager
def guard_time_limit(seconds: float) -> Iterator[None]:
    """Bound the enclosed block, restoring the previous handler on the way out.

    On platforms without ``SIGALRM`` the block runs unbounded here. What remains is
    the shell adapter's ``timeout`` / ``perl`` branch, and -- where neither exists --
    only the host's own hook timeout. That gap is inherent to the platform, not
    something this function papers over.
    """
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    # An outer limit may already be running -- the shell adapter's `perl -e 'alarm N'`
    # sets one, and it is inherited across `exec`. Reading it costs the alarm, so it is
    # cancelled here and accounted for below.
    # `alarm(0)` reports the remainder rounded **up** to whole seconds, so reading an
    # outer limit through it grants extra time: a real call entering 0.2s after `perl`
    # armed 2s reads 2s, not 1.8s. `getitimer` returns the true float remainder.
    inherited, _interval = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 0)
    # Hold the outer limit as an absolute deadline rather than as "seconds left", so the
    # time spent inside this block is charged against it.
    deadline = time.monotonic() + inherited if inherited else None
    # Never extend a deadline that already exists. Setting `seconds` outright would let
    # a 5s inner limit override a 1s outer one, and the outer deadline would be lost for
    # the whole call -- the two limits would not be independent.
    effective = min(float(seconds), inherited) if inherited else float(seconds)

    def _expire(_signum: int, _frame: object) -> None:
        raise GuardTimeout("stop verdict exceeded {}s".format(effective))

    previous = signal.signal(signal.SIGALRM, _expire)
    # `setitimer` throughout, never `alarm`: mixing them means the sub-second precision
    # obtained above is thrown away again on the way in or out.
    signal.setitimer(signal.ITIMER_REAL, effective)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if deadline is not None:
            left = deadline - time.monotonic()
            # Already past it: leave the alarm cancelled rather than granting more time.
            if left > 0:
                # `setitimer` takes a float, so sub-second remainders survive; `alarm()`
                # would have to round, and rounding up is an extension.
                signal.setitimer(signal.ITIMER_REAL, left)


_T = TypeVar("_T")

TIMEOUT_ENV_VAR = "MISSION_STATE_TIMEOUT"

# The hook runs a loop: stop-verdict -> side-effect command -> stop-verdict (three calls
# in the normal case, per #730). A per-call limit does not bound that loop -- it is
# re-armed on every call, so N calls cost N times the limit and the host's own timeout
# cuts first, discarding the output and with it the reason for the block.
#
# So the budget is an absolute deadline established once and carried across calls. The
# hook cannot compute it: #615 forbids arithmetic and `date +%s` there. Instead the
# first call reports the deadline it established, and the hook copies that string into
# the environment of the calls that follow -- copying, not computing.
DEADLINE_ENV_VAR = "MISSION_GUARD_DEADLINE"

# Set unconditionally by the hook once the loop starts -- never derived from the verdict
# JSON. Deriving it would reproduce the very failure this flag guards against: if the
# extraction fails, the deadline and the flag would go missing together.
CONTINUATION_ENV_VAR = "MISSION_GUARD_CONTINUATION"


def budget_clamped_from(env: Optional[dict] = None) -> Optional[int]:
    """Return the caller's value when it was `1` or `2` and therefore clamped.

    Exact ASCII match only. Any other bad value falls back silently, as before:
    the clamp is reported because it is the one case where a *previously valid*
    setting changed meaning (#749 M1), not as general input validation.
    """
    source = os.environ if env is None else env
    raw = source.get(RAW_ENV_VAR)
    if isinstance(raw, str) and raw in _CLAMPED:
        return int(raw)
    return None


# The deadline the running decorated command decided at entry. `deadline_token()`
# used to call `resolve_deadline()` again when the verdict was written, and on the
# first call -- no deadline in the environment yet -- that re-issued `now + budget`,
# later than the one the decorator was enforcing. The hook then carried the later
# one, and the "one budget for the whole hook" of #742 D3 was a budget plus the
# first call's run time. Held for the duration of one decorated call only.
_ACTIVE_DEADLINE: list = []


def active_deadline() -> Optional[float]:
    """Return the deadline retained by the decorated command now running, if any."""
    return _ACTIVE_DEADLINE[-1] if _ACTIVE_DEADLINE else None


def resolve_deadline(env: Optional[dict] = None, *, now: Optional[float] = None) -> float:
    """Return the absolute deadline for the whole hook, establishing it if needed.

    Uses wall-clock time, not `monotonic`, because the value crosses process
    boundaries. Over a budget of a few seconds the difference does not matter.

    Inside a decorated command the deadline decided at entry is returned as is,
    so the value written into the verdict is the value that was enforced.
    """
    if env is None and _ACTIVE_DEADLINE:
        return _ACTIVE_DEADLINE[-1]
    source = os.environ if env is None else env
    current = time.time() if now is None else now
    budget = resolve_guard_timeout(source.get(TIMEOUT_ENV_VAR))
    raw = source.get(DEADLINE_ENV_VAR)
    if raw:
        try:
            carried = float(raw)
        except (TypeError, ValueError):
            carried = None
        # Compare against the *configured* budget, not the default. Clamping to the
        # default would let `MISSION_STATE_TIMEOUT=1` be overridden by a 7-second
        # deadline handed in through the environment.
        if carried is not None and math.isfinite(carried) and carried <= current + budget:
            return carried
    # The deadline could not be used. On a continuation that means the budget was lost
    # mid-flight, and issuing a fresh one would re-arm it on every iteration -- three
    # calls would cost three budgets and overrun the host's own timeout. Fail closed.
    if source.get(CONTINUATION_ENV_VAR):
        raise GuardBudgetLost("stop guard budget was not carried into the continuation")
    return current + budget


def deadline_token(env: Optional[dict] = None, *, now: Optional[float] = None) -> str:
    """The deadline as the string the hook copies into the environment.

    Formatting lives here rather than in the adapter: the adapter is held to a thin
    dispatch shape (#626), and a `.format()` call there counts against it.
    """
    return "{:.3f}".format(resolve_deadline(env, now=now))


def remaining_budget(deadline: float, *, now: Optional[float] = None) -> float:
    """Seconds left before the hook's deadline. Zero or less means exhausted."""
    return deadline - (time.time() if now is None else now)


def bounded_by_guard_timeout(func: Callable[..., _T]) -> Callable[..., _T]:
    """Apply the guard's own limit around a command, where the platform allows it.

    Written as a decorator so the command body keeps its shape: the adapter states
    that the command is bounded, and the mechanism stays in this module.

    **This is not an unconditional bound.** Without ``SIGALRM`` the wrapped call runs
    unbounded here -- see :func:`guard_time_limit` for what covers that case.
    """

    @functools.wraps(func)
    def _wrapper(*args: object, **kwargs: object) -> _T:
        # A lost budget (continuation without a usable deadline) is not exhaustion;
        # it is raised here, before anything is retained, and keeps its own path.
        deadline = resolve_deadline()
        # Retained for the whole call *including* the exhaustion report, so the
        # `guard_deadline` written into a terminal verdict is the enforced one.
        _ACTIVE_DEADLINE.append(deadline)
        try:
            # The reserve is subtracted here, not only checked: with the full
            # remainder as the limit a body that started with 2.1 s left could run
            # into the reserve, and the tail the reserve exists for would not fit.
            left = remaining_budget(deadline) - RESERVE_SECONDS
            if left <= 0:
                raise GuardTimeout(EXHAUSTED_REASON + ": the execution budget is spent")
            with guard_time_limit(left):
                return func(*args, **kwargs)
        except GuardBudgetLost:
            raise
        except GuardTimeout as error:
            return _report_exhaustion(func.__name__, error)  # type: ignore[return-value]
        finally:
            _ACTIVE_DEADLINE.pop()

    return _wrapper


# The one command whose exhaustion is a verdict rather than an error. The other
# hook commands (`mark-halt` / `cleanup-stale` / `stop-guard-observe`) fail non-zero
# and the shell adapter turns that into the block; only their reason has to be
# nameable on stderr. Kept here, not in the adapter: the adapter is held to a thin
# dispatch shape (#626) and the ratchet refuses new branches there.
_TERMINAL_VERDICT_COMMANDS = frozenset({"cmd_stop_verdict"})


def _report_exhaustion(command_name: str, error: GuardTimeout) -> None:
    import json
    import sys

    if command_name in _TERMINAL_VERDICT_COMMANDS:
        sys.stdout.write(json.dumps(exhausted_verdict_payload(), ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return None
    sys.stderr.write("ERROR: {}: {}\n".format(EXHAUSTED_REASON, error))
    raise SystemExit(2)


def finish_guard_verdict(payload: dict) -> dict:
    """Complete a verdict with everything the budget contributes to it.

    ``guard_deadline``: the string the hook copies into the environment of the
    calls that follow, so the whole loop shares one deadline (#742 D3'). Inside a
    decorated command this is the retained deadline, not a re-issued one (#754).

    ``budget_clamped_from``: present only when the caller's value was clamped,
    at the top level *and* inside ``shell_text`` -- the hook only forwards
    ``shell_text``, so a top-level field alone would never be seen where the
    setting was made. ``shell_text`` stays a single JSON line, or empty.

    One function rather than two so the adapter keeps a single application
    call per command (the thin-adapter ratchet, #626).
    """
    import json

    payload["guard_deadline"] = deadline_token()
    clamped = budget_clamped_from()
    if clamped is None:
        return payload
    payload["budget_clamped_from"] = clamped
    shell_text = payload.get("shell_text") or ""
    if shell_text:
        reply = json.loads(shell_text)
        reply["budget_clamped_from"] = clamped
        payload["shell_text"] = json.dumps(reply, ensure_ascii=False) + "\n"
    return payload


def exhausted_verdict_payload(env: Optional[dict] = None, *, now: Optional[float] = None) -> dict:
    """Return the terminal `stop-verdict` for an exhausted budget.

    Emitted on stdout with exit 0 whether the decorator refused to start the body
    or the alarm interrupted it. Both used to escape as errors (`internal-error`
    / exit 1 and `guard-decision-unavailable` / exit 2), which the hook could not
    tell from a defect. The envelope is the ordinary `mission-stop-verdict/1`
    with `command.kind = "none"`, so the shell prints `shell_text` and stops.
    """
    import json

    reply = {
        "decision": "block",
        "reason": EXHAUSTED_REASON,
        "outcome_kind": "expected-gate",
    }
    clamped = budget_clamped_from(env)
    if clamped is not None:
        reply["budget_clamped_from"] = clamped
    try:
        token = deadline_token(env, now=now)
    except GuardTimeout:
        # A continuation whose deadline was lost: nothing to carry. The next call
        # fails closed on the missing deadline, which is the existing contract.
        token = ""
    payload = {
        "schema": "mission-stop-verdict/1",
        "decision": "block",
        "reason": EXHAUSTED_REASON,
        "outcome_kind": "expected-gate",
        "command": {"kind": "none"},
        "shell_text": json.dumps(reply, ensure_ascii=False) + "\n",
        "guard_deadline": token,
    }
    if clamped is not None:
        payload["budget_clamped_from"] = clamped
    return payload
