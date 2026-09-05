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
_ACCEPTED = frozenset({"1", "2", "3", "4", "5", "6", "7", "8"})


class GuardTimeout(Exception):
    """Raised in-process when the verdict exceeds its own limit."""


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


def resolve_deadline(env: Optional[dict] = None, *, now: Optional[float] = None) -> float:
    """Return the absolute deadline for the whole hook, establishing it if needed.

    Uses wall-clock time, not `monotonic`, because the value crosses process
    boundaries. Over a budget of a few seconds the difference does not matter.
    """
    source = os.environ if env is None else env
    current = time.time() if now is None else now
    raw = source.get(DEADLINE_ENV_VAR)
    if raw:
        try:
            carried = float(raw)
        except (TypeError, ValueError):
            carried = None
        # A deadline further out than a fresh budget would be an escalation, not a
        # carry-over: reject it rather than honouring whatever the environment held.
        if carried is not None and carried <= current + DEFAULT_GUARD_TIMEOUT_SECONDS:
            return carried
    return current + resolve_guard_timeout(source.get(TIMEOUT_ENV_VAR))


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
        left = remaining_budget(resolve_deadline())
        if left <= 0:
            raise GuardTimeout("stop guard budget is exhausted")
        with guard_time_limit(left):
            return func(*args, **kwargs)

    return _wrapper
