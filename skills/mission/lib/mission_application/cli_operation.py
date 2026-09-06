"""#747 P2-b: bind one CLI invocation to a caller-stable operation identity.

Without a stable identity the repository mints a fresh operation id per
transaction, so a crash retry runs the command a second time instead of
replaying the first.  The identity has to be the same across processes, which
is why it is opt-in through the caller's environment rather than derived from
state: two concurrent runs deriving the same id from the same pre-state would
replay each other.

The *arguments* that go into the identity decide what counts as "the same
operation".  They are the command's meaning after normalisation -- never the
wall clock, and never a body of text.  A body enters as its digest so the
record stays bounded and does not carry content, and normalisation goes
through the kernel's own rules so that two spellings the kernel treats
alike are one operation here too.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional


class CliOperationRejected(ValueError):
    """The caller named an operation id the repository cannot use.

    It reaches the CLI as an input refusal.  Letting the underlying
    ``ValueError`` escape instead turned a typo in the environment into an
    internal error.
    """


@dataclass(frozen=True)
class CliOperationIdentity:
    """What the repository needs to recognise a retry as the same operation.

    All three fields are ``None`` when the caller did not opt in; the
    repository then mints its own per-transaction identity and no replay is
    possible, which is the behaviour every command had before.
    """

    operation_id: Optional[str]
    operation_command: object
    command_type: Optional[str]

    @property
    def opted_in(self) -> bool:
        return self.operation_id is not None


ABSENT_IDENTITY = CliOperationIdentity(None, None, None)


def text_digest(value: object) -> str:
    """Return the digest a body of text enters the identity as."""
    if not isinstance(value, str):
        raise TypeError("cli-operation-text-invalid")
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def prepare_cli_operation(
    command_type: str,
    arguments: dict,
    *,
    session_id: str,
    compatibility_arguments,
    canonical_operation,
) -> CliOperationIdentity:
    """Return the caller-stable identity for one CLI invocation.

    ``arguments`` must already be normalised and must not contain the time
    the caller ran the command: a retry runs with a new clock, and including
    it would make every retry a different intent.
    """
    try:
        caller_operation_id, resolved = compatibility_arguments(
            arguments, target_digest="", require_caller=False
        )
    except ValueError as exc:
        raise CliOperationRejected(str(exc)) from exc
    if caller_operation_id is None:
        return ABSENT_IDENTITY
    operation_id, operation_command = canonical_operation(
        session_id,
        command_type,
        resolved,
        caller_operation_id=caller_operation_id,
    )
    return CliOperationIdentity(operation_id, operation_command, command_type)
