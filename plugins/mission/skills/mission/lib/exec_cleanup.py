"""Housekeeping policy independent of process lifetime and settlement evidence."""
from __future__ import annotations

from contextlib import contextmanager


def finish_cleanup(action, *args, original=None, suppress=(), **kwargs):
    """Run cleanup; preserve an explicit original or the site's success policy.

    Return a suppressed failure for callers that record cleanup telemetry.
    Never infer the original from ambient exception state: an enclosing except
    block does not mean this operation failed. Lifetime attributes stay intact.
    """
    try:
        action(*args, **kwargs)
    except BaseException as failure:
        if original is None and not isinstance(failure, suppress):
            raise
        return failure
    return None


@contextmanager
def cleanup_scope(action, *args, suppress=(), **kwargs):
    """Close a resource with the actual exception from this scope, if any."""
    try:
        yield
    except BaseException as original:
        finish_cleanup(action, *args, original=original, suppress=suppress, **kwargs)
        raise
    else:
        finish_cleanup(action, *args, suppress=suppress, **kwargs)
