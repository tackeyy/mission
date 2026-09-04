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
def guard_time_limit(seconds: int) -> Iterator[None]:
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
    inherited = signal.alarm(0)
    # Never extend a deadline that already exists. Setting `seconds` outright would let
    # a 5s inner limit override a 1s outer one, and the outer deadline would be lost for
    # the whole call -- the two limits would not be independent.
    effective = min(seconds, inherited) if inherited else seconds

    def _expire(_signum: int, _frame: object) -> None:
        raise GuardTimeout("stop verdict exceeded {}s".format(effective))

    previous = signal.signal(signal.SIGALRM, _expire)
    started = time.monotonic()
    signal.alarm(effective)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        if inherited:
            # Hand the outer deadline back, minus what this block consumed. `alarm(0)`
            # would cancel it, so a floor of 1 keeps it armed rather than dropping it.
            elapsed = int(time.monotonic() - started)
            signal.alarm(max(1, inherited - elapsed))


_T = TypeVar("_T")

TIMEOUT_ENV_VAR = "MISSION_STATE_TIMEOUT"


def bounded_by_guard_timeout(func: Callable[..., _T]) -> Callable[..., _T]:
    """Apply the guard's own limit around a command.

    Written as a decorator so the command body keeps its shape: the adapter states
    that the command is bounded, and the mechanism stays in this module.
    """

    @functools.wraps(func)
    def _wrapper(*args: object, **kwargs: object) -> _T:
        with guard_time_limit(resolve_guard_timeout(os.environ.get(TIMEOUT_ENV_VAR))):
            return func(*args, **kwargs)

    return _wrapper
