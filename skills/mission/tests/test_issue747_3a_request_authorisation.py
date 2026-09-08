"""#747 3a: the in-root destination is refused at the repository's entry too.

The blob set built from a command's effects knows the command type, so it can
say who may publish in-root.  It is not the only way a command and a blob set
meet: ``ExecutionRequest`` carries both, and the effect binding the kernel
compares looks at kind, target, digest and size -- never at the path.  A
binding assembled by hand, with a claim and content that are otherwise
correct, therefore reached the in-root destination under a command that never
asked for it.

The command is read from the audit, because that is the field which survives
every route -- an ordinary CLI run carries no caller operation identity, so
its command travels as a compatibility wrapper -- and because the intent
digest does not fold the audit, naming the command there moves no identity.

The audit is a claim, so the executor compares it against the command it
prepared, where both are in hand.  A caller that both forges the audit and
bypasses the executor is still not caught; that is why the request carrying
the typed command remains tracked on #747.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

INTERNAL = ".mission-state/archive/iter-9-abcdef01-progress.md"
EXTERNAL = "build/manifest.json"


def _binding(relative_path, *, origin="generated", kind="progress"):
    from mission_application.evidence_publication import derive_blob_id
    from mission_persistence.evidence_order import published_binding_type

    return published_binding_type()(
        blob_id=derive_blob_id(relative_path),
        kind=kind,
        relative_path=relative_path,
        digest="sha256:" + "0" * 64,
        size=0,
        origin=origin,
        target="t",
    )


def _blob_set(*bindings):
    from mission_persistence.local_uow import VerifiedBlob, VerifiedBlobSet

    return VerifiedBlobSet(tuple(VerifiedBlob(b, b"") for b in bindings))


def _refuses(command_type, blobs):
    from mission_persistence.fenced_commit import (
        FencedCommitError,
        refuse_unauthorized_generated_blobs,
    )

    with pytest.raises(FencedCommitError) as caught:
        refuse_unauthorized_generated_blobs(command_type, blobs)
    assert caught.value.code == "request-invalid"
    return caught.value


def test_a_hand_built_internal_binding_is_refused_for_another_command():
    error = _refuses("generate-context-manifest", _blob_set(_binding(INTERNAL)))
    assert "generate-context-manifest" in str(error)


def test_the_owning_command_is_allowed():
    from mission_persistence.fenced_commit import refuse_unauthorized_generated_blobs

    refuse_unauthorized_generated_blobs("update-progress", _blob_set(_binding(INTERNAL)))


def test_a_command_nobody_listed_is_refused():
    """Fail closed: permission is not granted by omission."""
    _refuses("some-future-command", _blob_set(_binding(INTERNAL)))
    _refuses("compatibility-mutation", _blob_set(_binding(INTERNAL)))
    _refuses(None, _blob_set(_binding(INTERNAL)))


@pytest.mark.parametrize(
    "command_type",
    ["update-progress", "generate-context-manifest", "some-future-command", None],
)
def test_external_bindings_pass_for_every_command(command_type):
    from mission_persistence.fenced_commit import refuse_unauthorized_generated_blobs

    refuse_unauthorized_generated_blobs(command_type, _blob_set(_binding(EXTERNAL)))


def test_one_internal_binding_among_externals_is_found():
    _refuses(
        "generate-context-manifest",
        _blob_set(_binding(EXTERNAL), _binding(INTERNAL), _binding(EXTERNAL)),
    )


def test_an_empty_blob_set_passes():
    from mission_persistence.fenced_commit import refuse_unauthorized_generated_blobs

    refuse_unauthorized_generated_blobs("generate-context-manifest", _blob_set())


def test_a_captured_binding_is_not_a_generated_destination():
    """Captured blobs are caller input read from anywhere; this rule is about output."""
    from mission_persistence.fenced_commit import refuse_unauthorized_generated_blobs
    from mission_persistence.local_uow import BlobBinding, VerifiedBlob, VerifiedBlobSet

    captured = VerifiedBlob(
        BlobBinding(
            blob_id="t" * 32,
            kind="input",
            relative_path=INTERNAL,
            digest="sha256:" + "0" * 64,
            size=0,
            origin="captured",
        ),
        b"",
    )
    refuse_unauthorized_generated_blobs(
        "generate-context-manifest", VerifiedBlobSet((captured,))
    )


def test_the_request_validation_applies_the_rule():
    """Not a helper nobody calls: the entry itself refuses."""
    import inspect

    from mission_persistence import fenced_commit

    source = inspect.getsource(fenced_commit.validate_execution_request)
    assert "refuse_unauthorized_generated_blobs(request.audit.command_type" in source


def test_the_executor_names_the_command_it_prepared():
    """The audit must carry the real command, or the entry authorises a label."""
    import inspect

    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    executor = inspect.getsource(
        V5CompatibilityRepository.execute_evidence_transition_effects
    )
    assert "self._prepared_command_type = kernel_command_type(prepared.command)" in executor
    # ...and it is set before the admission reads the blobs, not after.
    assert executor.index("self._prepared_command_type = kernel_command_type") < (
        executor.index("current = self.load(blobs=blobs)")
    )

    request = inspect.getsource(V5CompatibilityRepository._request)
    assert request.index("self._prepared_command_type") < request.index(
        "self._operation_command_type"
    ), "the caller-supplied label must not win over the prepared command"


def test_the_executor_compares_the_audit_against_the_command_it_prepared():
    """The audit travelled with the request, so where both are in hand they agree."""
    import inspect

    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    source = inspect.getsource(
        V5CompatibilityRepository.execute_evidence_transition_effects
    )
    # The comparison itself, not merely the error it would raise: a guard
    # whose condition is disabled still contains the message.
    assert "if actual is not None and declared != actual:" in source
    assert "audit-binding-mismatch" in source
