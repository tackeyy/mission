"""Self-applied time limit for the Stop guard (Issue #742, decisions D2 and D3).

The shell adapter used to bound `mission-state.py` with `timeout`, falling back to
`perl -e 'alarm ...'`, and running the command **unbounded** when neither existed.
A host without both commands could therefore hang the Stop hook forever.

D2 moves the limit into the command itself, so it holds regardless of what the host
provides. D3 fixes the value handling: only positive integers are honoured and every
other value falls back to the default rather than becoming a failure of its own.
"""

from __future__ import annotations

import functools
import os
import signal
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, TypeVar

# The host bounds the whole hook at 10 seconds (`claude-hooks/hooks.json`). The
# guard's own limit has to stay inside that budget, or the host cuts first and the
# guard never gets to emit its block -- the host discards the output instead.
#
# So this value is both the default *and* the ceiling: a larger override is clamped
# rather than honoured. Smaller overrides are honoured (tests use 1).
DEFAULT_GUARD_TIMEOUT_SECONDS = 8

_ASCII_DIGITS = frozenset("0123456789")


class GuardTimeout(Exception):
    """Raised in-process when the verdict exceeds its own limit."""


def resolve_guard_timeout(raw: Optional[object]) -> int:
    """Return the limit in seconds, falling back to the default for any bad value.

    A bad value is not an error: the guard still has to produce a verdict, and
    refusing to run because the environment held ``"8s"`` would block every Stop.
    """
    if raw is None:
        return DEFAULT_GUARD_TIMEOUT_SECONDS
    text = str(raw).strip()
    # `str.isdigit()` accepts non-ASCII digits (e.g. Arabic-Indic "٨"), which `int()`
    # then parses into a value the operator never wrote. Restrict to ASCII.
    if not text or not set(text) <= _ASCII_DIGITS:
        return DEFAULT_GUARD_TIMEOUT_SECONDS
    value = int(text)
    # Zero would disable the alarm outright (`alarm 0` cancels it), which is the very
    # unbounded state D2 removes. Treat it as unusable, not as "no limit".
    if value <= 0:
        return DEFAULT_GUARD_TIMEOUT_SECONDS
    # Clamp rather than honour: a value above the host's own hook timeout would let the
    # host cut first, and D3 puts the guard's limit inside the host's budget.
    return min(value, DEFAULT_GUARD_TIMEOUT_SECONDS)


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

    def _expire(_signum: int, _frame: object) -> None:
        raise GuardTimeout("stop verdict exceeded {}s".format(seconds))

    previous = signal.signal(signal.SIGALRM, _expire)
    # `alarm()` returns what was left of any alarm it replaces. Restoring the handler
    # without restoring that remainder would silently cancel an outer limit.
    remaining = signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        if remaining:
            signal.alarm(remaining)


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
