"""Immutable compatibility observations for legacy command projection."""

from __future__ import annotations

import copy
from typing import Iterable

from mission_kernel.commands import CompatibilityPayload


_MISSING = object()


def compatibility_delta(
    before: dict,
    after: dict,
    *,
    exclude: Iterable[str] = (),
) -> CompatibilityPayload:
    """Freeze the exact non-authoritative delta between two legacy documents."""
    excluded = frozenset(exclude)
    upserts = {}
    removals = []
    for key in sorted(set(before) | set(after)):
        if key in excluded:
            continue
        old = before.get(key, _MISSING)
        new = after.get(key, _MISSING)
        if old == new and type(old) is type(new):
            continue
        if new is _MISSING:
            removals.append(key)
        else:
            upserts[key] = copy.deepcopy(new)
    return CompatibilityPayload(upserts, tuple(removals))
