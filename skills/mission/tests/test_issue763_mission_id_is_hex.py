"""#763: the progress rule accepts only what the generator can produce.

``_PROGRESS_BASENAME_RE`` admits ``[0-9a-f]{1,8}`` or ``unknown`` for the
mission id segment, and the generator fills that segment with the first eight
characters of the state's ``mission_id``.  The rule is therefore safe only
while every mission id the CLI can put into a state is a hex digest.

Two facts hold that, and neither is stated where the rule is: the id derives
from ``sha256`` (so it is hex by construction), and the generic ``set``
command refuses to write the field (so a running mission cannot replace it
with anything else).  Cross-model review round 1 read the rule and the
generator side by side and asked what happens to a state carrying, say,
``Mission-1`` -- the old publisher wrote that file, the new path would refuse
it.  The answer is that no CLI path produces such a state, and this module is
where that answer is fixed: if either fact stops holding, the rule and its
generator have drifted and these tests fail rather than the drift reaching a
user as ``publication-path-invalid``.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from mission_application.evidence_publication import (  # noqa: E402
    canonical_generated_path,
)
from mission_kernel.commands import GENERIC_SET_FROZEN_FIELDS  # noqa: E402

MISSION_STATE_PY = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
ROOT_NAME = ".mission-state"
HEX_DIGEST = re.compile(r"\A[0-9a-f]{16}\Z")

# The generator's own inputs: a mission is free text, so the ids below are the
# shapes a user can actually reach -- not a sample of hex strings chosen to
# pass.
MISSIONS = (
    "Mission-1",
    "ABCDEF0123456789",
    "progress を UoW の唯一の writer 経路へ移す",
    "a" * 4096,
    "",
    "../../etc/passwd",
    "mission with spaces and \t tabs",
    "🙂",
)


def _load():
    spec = importlib.util.spec_from_file_location("gs_mission_id", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_mission_id_the_generator_produces_is_a_hex_digest():
    module = _load()
    produced = {mission: module.mission_id(mission) for mission in MISSIONS}
    assert all(HEX_DIGEST.match(value) for value in produced.values()), produced


def test_the_generic_set_command_cannot_replace_the_mission_id():
    # Without this the previous test only describes how ids start out: a
    # running mission could still be given a non-hex id afterwards.
    assert "mission_id" in GENERIC_SET_FROZEN_FIELDS


def test_the_generated_path_is_accepted_for_every_mission_id_produced():
    module = _load()
    for mission in MISSIONS:
        gid = module.mission_id(mission)[:8]
        for iteration in (0, 1, 12, 4096):
            relative = f"archive/iter-{iteration}-{gid}-progress.md"
            # Raises EvidencePublicationError if the rule and the generator
            # disagree, which is the drift this module exists to catch.
            canonical_generated_path(
                f"{ROOT_NAME}/{relative}", repository_root_name=ROOT_NAME
            )


def test_a_state_carrying_a_non_hex_mission_id_still_gets_a_path():
    """The decoder is wider than the generator, so the segment is derived.

    ``codec_v5`` admits any non-empty ``mission_id``.  A state from an older
    version or a migration can therefore carry ``Mission-1``, and the legacy
    publisher used to write its progress file.  Refusing it here would be a
    regression for that state's owner, so the segment is derived instead.
    """
    module = _load()
    for stored in ("Mission-1", "ABCDEF0123456789", "../../etc/passwd", "🙂", "ZZ"):
        segment = module._progress_mission_segment(stored)
        canonical_generated_path(
            f"{ROOT_NAME}/archive/iter-7-{segment}-progress.md",
            repository_root_name=ROOT_NAME,
        )


def test_the_production_path_builder_is_wired_to_the_derivation(tmp_path):
    """Drive the helper the CLI actually calls, not the derivation alone.

    Cross-model review round 3: testing ``_progress_mission_segment`` on its
    own and feeding the result to the rule by hand leaves the wiring free --
    ``_progress_archive_path`` could go back to slicing the raw id and these
    tests would still pass.  This one asks the production builder for the
    path and hands *its* answer to the rule.
    """
    module = _load()
    for stored in ("Mission-1", "ABCDEF0123456789", "../../etc/passwd", "🙂"):
        built = module._progress_archive_path(tmp_path, {"mission_id": stored}, 3)
        assert built.startswith(f"{ROOT_NAME}/archive/"), built
        canonical_generated_path(built, repository_root_name=ROOT_NAME)


def test_the_production_path_builder_keeps_a_hex_id_unchanged(tmp_path):
    # The wiring must not rewrite ids that already fit: existing progress
    # files are found by name.
    module = _load()
    built = module._progress_archive_path(tmp_path, {"mission_id": "abcdef0123456789"}, 3)
    assert built == f"{ROOT_NAME}/archive/iter-3-abcdef01-progress.md", built


def test_the_derived_segment_is_stable_for_one_mission_id():
    # One mission keeps one file name across runs; the checkpoints written
    # under the previous name would not be found otherwise.
    module = _load()
    first = module._progress_mission_segment("Mission-1")
    assert first == module._progress_mission_segment("Mission-1")
    assert first != module._progress_mission_segment("Mission-2")


def test_a_hex_mission_id_is_passed_through_unchanged():
    # Deriving unconditionally would move every existing progress file.
    module = _load()
    assert module._progress_mission_segment("abcdef0123456789") == "abcdef01"


def test_the_absent_mission_id_still_reaches_the_literal_the_rule_admits():
    # `_progress_archive_path` substitutes "unknown" when the state carries no
    # id.  The rule admits that literal; this fixes the pair together so that
    # removing it from either side fails here.
    canonical_generated_path(
        f"{ROOT_NAME}/archive/iter-3-unknown-progress.md",
        repository_root_name=ROOT_NAME,
    )
