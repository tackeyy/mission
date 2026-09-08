"""#747 3a: who may write inside the repository, enforced where both halves meet.

The blob set built from a command's effects is one way in, and it knows the
command type, so it can refuse an in-root destination for a command that has
no business writing one.  It is not the only way in: ``ExecutionRequest``
takes a command and a blob set directly, and nothing there compared the two.
A hand-built binding with the right digest and the wrong path would therefore
publish in-root under a command that never asked to.

So the rule is enforced at both entries, and these fix that.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_application.evidence_publication import (  # noqa: E402
    INTERNAL_DESTINATION_COMMAND_TYPES,
    authorize_generated_destinations,
    EvidencePublicationError,
)

ROOT = ".mission-state"
INTERNAL = ".mission-state/archive/iter-4-abcdef01-progress.md"
EXTERNAL = "docs/evidence/manifest.json"


def test_only_update_progress_may_write_in_root_today():
    assert INTERNAL_DESTINATION_COMMAND_TYPES == frozenset({"update-progress"})


def test_the_permitted_command_may_use_the_internal_shape():
    authorize_generated_destinations(
        (INTERNAL,), command_type="update-progress", repository_root_name=ROOT
    )


@pytest.mark.parametrize(
    "command_type",
    ["generate-context-manifest", "generate-claims-ledger", "export-artifact",
     "render-artifact", "initialize-artifact", "record-artifact-publication"],
)
def test_no_other_command_may(command_type):
    with pytest.raises(EvidencePublicationError) as caught:
        authorize_generated_destinations(
            (INTERNAL,), command_type=command_type, repository_root_name=ROOT
        )
    assert caught.value.code == "publication-destination-unauthorized"
    # The message names the command, because the reader is looking at a
    # refusal for a path that is well formed.
    assert command_type in str(caught.value)


def test_an_unknown_command_type_may_not():
    """Fail closed: a type nobody listed is not permitted by omission."""
    with pytest.raises(EvidencePublicationError):
        authorize_generated_destinations(
            (INTERNAL,), command_type="some-future-command",
            repository_root_name=ROOT,
        )
    with pytest.raises(EvidencePublicationError):
        authorize_generated_destinations(
            (INTERNAL,), command_type=None, repository_root_name=ROOT
        )


@pytest.mark.parametrize(
    "command_type",
    ["update-progress", "generate-context-manifest", "some-future-command", None],
)
def test_the_external_shape_needs_no_permission(command_type):
    authorize_generated_destinations(
        (EXTERNAL,), command_type=command_type, repository_root_name=ROOT
    )


def test_one_unauthorised_path_among_many_refuses_the_whole_set():
    with pytest.raises(EvidencePublicationError):
        authorize_generated_destinations(
            (EXTERNAL, INTERNAL, EXTERNAL),
            command_type="generate-context-manifest",
            repository_root_name=ROOT,
        )


def test_a_malformed_path_is_not_silently_treated_as_external():
    """Otherwise an in-root path that fails the name rule would need no permission."""
    with pytest.raises(EvidencePublicationError) as caught:
        authorize_generated_destinations(
            (".mission-state/commits/x",),
            command_type="update-progress",
            repository_root_name=ROOT,
        )
    assert caught.value.code == "publication-path-invalid"
