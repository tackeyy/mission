"""Behavior-compatible repository for missing/v1-v4 session documents."""

from __future__ import annotations

import copy
from typing import Callable, ContextManager


class AggregateIndexError(RuntimeError):
    """The session write succeeded but the rebuildable aggregate index did not."""


class LegacyV4InitializerRepository:
    """Expose the existing atomic v4 bootstrap as a repository operation.

    A1 does not switch new sessions to v5.  The injected writer is therefore
    the compatibility persistence implementation, while the application use
    case and CLI remain independent of its filesystem details.
    """

    def __init__(self, initialize_state: Callable[[object], None]) -> None:
        self._initialize_state = initialize_state

    def initialize(self, arguments: object) -> None:
        self._initialize_state(arguments)


class LegacyV4Repository:
    """Coordinate legacy load/save while keeping ``execute`` pure.

    The injected persistence functions preserve the existing CLI's StateLock,
    atomic writer, backup, and fenced-lease behavior without making the
    application layer depend on the CLI module.
    """

    def __init__(
        self,
        *,
        lock: Callable[[], ContextManager[object]],
        read_state: Callable[[], dict],
        write_state: Callable[..., None],
        backup_state: Callable[[], None],
        add_to_aggregate: Callable[[], None] | None = None,
        remove_from_aggregate: Callable[[], None] | None = None,
    ) -> None:
        self._lock = lock
        self._read_state = read_state
        self._write_state = write_state
        self._backup_state = backup_state
        self._add_to_aggregate = add_to_aggregate
        self._remove_from_aggregate = remove_from_aggregate

    def transaction(self) -> ContextManager[object]:
        return self._lock()

    def load(self) -> dict:
        return self._read_state()

    def execute(self, state: dict, mutation: Callable[[dict], None], transition=None) -> dict:
        """Return the proposed v4 document without performing any I/O."""
        # The typed transition is the authority decision, while the A1
        # compatibility reducer remains the writer for legacy timing, lease,
        # and passthrough fields.  Projecting the canonical state here would
        # pre-apply the phase and lose the legacy reducer's duration boundary.
        proposed = copy.deepcopy(state)
        mutation(proposed)
        return proposed

    def save(
        self,
        state: dict,
        *,
        backup: bool = True,
        administrative: bool = False,
        aggregate_action: str | None = None,
    ) -> None:
        if backup:
            self._backup_state()
        # Preserve the legacy writer call shape.  Several callers replace the
        # writer with a one-argument fault injector; the administrative flag
        # was only ever supplied for the routed-goal path.
        if administrative:
            self._write_state(state, administrative=True)
        else:
            self._write_state(state)
        callback = {
            None: None,
            "add": self._add_to_aggregate,
            "remove": self._remove_from_aggregate,
        }.get(aggregate_action)
        if aggregate_action not in {None, "add", "remove"}:
            raise ValueError("unknown aggregate action")
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:
            raise AggregateIndexError(str(exc)) from exc
