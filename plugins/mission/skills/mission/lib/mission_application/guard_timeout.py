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

# The host bounds the whole hook at 10 seconds (`settings.json`). The guard's own
# limit sits inside that budget so the guard, not the host, decides what happens when
# the verdict is slow: the host discards the output, while the guard emits a block.
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
    return value


@contextmanager
def guard_time_limit(seconds: int) -> Iterator[None]:
    """Bound the enclosed block, restoring the previous handler on the way out.

    On platforms without ``SIGALRM`` the block runs unbounded, because there is no
    in-process mechanism to interrupt it; the shell adapter's external limit is the
    remaining defence there.
    """
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _expire(_signum: int, _frame: object) -> None:
        raise GuardTimeout("stop verdict exceeded {}s".format(seconds))

    previous = signal.signal(signal.SIGALRM, _expire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


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
