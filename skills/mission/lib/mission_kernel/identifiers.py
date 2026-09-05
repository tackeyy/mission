"""Identifier rules shared by the record and by whatever names an operation.

The fenced record refuses an operation id that is not a Token128.  A caller
that chooses the id has to be held to the same rule *before* the work runs,
or the refusal arrives as ``record-invalid`` after every attempt has run.
One compiled pattern, imported by both sides, keeps the two from drifting.
"""

from __future__ import annotations

import re

TOKEN128_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def is_token128(value: object) -> bool:
    return isinstance(value, str) and TOKEN128_RE.fullmatch(value) is not None
