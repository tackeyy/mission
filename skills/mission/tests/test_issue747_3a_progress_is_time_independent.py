"""#747 3a: the same operation must publish the same bytes.

An operation's identity excludes the semantic time on purpose -- that is what
lets a crashed run be retried and recognised as the same operation.  Once a
route publishes through the unit of work, the commit's materialization is
compared against what the retry prepares, so content that moves with the
clock makes every retry look like a different operation and be refused.

The other blob-carrying route has no clock-derived field in its record, which
reads as a coincidence until this is written down.  It is not: it is what the
comparison requires.
"""

from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_kernel.commands import ProgressEffectClaim, UpdateProgress  # noqa: E402
from mission_kernel.evidence import project_progress_update  # noqa: E402

STATE = {"session_id": "cx-1", "mission_id": "abcdef0123456789"}
PATH = ".mission-state/archive/iter-1-abcdef01-progress.md"


def _command(at):
    return UpdateProgress(
        at, 4, 2, 2, "unit", "artifact.md", 1,
        ProgressEffectClaim("progress", PATH, "sha256:" + "0" * 64, 0),
    )


def test_two_times_produce_the_same_bytes():
    _first, early = project_progress_update(STATE, _command("2026-01-01T00:00:00Z"))
    _second, late = project_progress_update(STATE, _command("2031-06-30T23:59:59Z"))
    assert early == late


def test_the_time_is_still_recorded_in_the_state():
    """Dropped from the file, not from the record: a reader still has it."""
    progress, _content = project_progress_update(
        STATE, _command("2026-01-01T00:00:00Z")
    )
    assert progress["updated_at"] == "2026-01-01T00:00:00Z"


def test_everything_that_identifies_the_checkpoint_still_moves_the_bytes():
    """Time-independent must not become input-independent."""
    baseline = project_progress_update(STATE, _command("2026-01-01T00:00:00Z"))[1]

    def _bytes(**overrides):
        command = _command("2026-01-01T00:00:00Z")
        fields = {
            "at": command.at,
            "total": command.total,
            "completed": command.completed,
            "batch_size": command.batch_size,
            "last_unit": command.last_unit,
            "artifact_path": command.artifact_path,
            "iteration": command.iteration,
            "effect": command.effect,
        }
        fields.update(overrides)
        return project_progress_update(STATE, UpdateProgress(**fields))[1]

    assert _bytes(total=5) != baseline
    assert _bytes(completed=3) != baseline
    assert _bytes(batch_size=7) != baseline
    assert _bytes(last_unit="other") != baseline
    assert _bytes(artifact_path="other.md") != baseline
    assert _bytes(iteration=2) != baseline
    # ``remaining`` is derived, so it moves with the two counts above rather
    # than on its own; the pair above already covers it.
    other_state = dict(STATE, session_id="cx-2")
    assert project_progress_update(
        other_state, _command("2026-01-01T00:00:00Z")
    )[1] != baseline
    other_mission = dict(STATE, mission_id="0000000000000000")
    assert project_progress_update(
        other_mission, _command("2026-01-01T00:00:00Z")
    )[1] != baseline
