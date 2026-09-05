"""#747 項目 7: テストダブルは 1 箇所に置き、production の形に機械的に追従させる.

第 1 段で 5 回、第 2 段でも起きた「production は正しいがダブルが契約に追いついて
いない」を、次の経路追加で 3 倍にしないための固定。
"""
import importlib
import inspect

import pytest

from . import evidence_doubles
from .evidence_doubles import PRODUCTION_SHAPES, V5_EXECUTOR_SURFACE


def _resolve(spec):
    module_name, _, attribute = spec.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


@pytest.mark.parametrize("double_path,production,fields", PRODUCTION_SHAPES,
                         ids=[shape[0] for shape in PRODUCTION_SHAPES])
def test_every_field_a_double_answers_exists_in_production(double_path, production, fields):
    """A double may answer less than production, never something it does not have.

    When a production field is renamed, this fails before any test that
    trusted the double starts passing for the wrong reason.
    """
    cls = _resolve(production)
    names = set(inspect.signature(cls).parameters) | set(vars(cls))
    for field in fields:
        assert field in names, f"{double_path} answers {field!r}, but {production} has no such field"


def test_the_fenced_fake_answers_the_declared_shapes():
    """The declaration above has to match what the fake really returns."""
    from mission_persistence.local_uow import VerifiedBlobSet

    fake = evidence_doubles.FakeFencedRepository(
        evidence_doubles.decoded_state(
            {"phase": "executing", "loop_active": True, "session_id": "portable"}
        )
    )
    read = fake.read("portable")
    assert {"state", "head_digest", "head"} <= set(vars(read))
    assert read.head.generation == 0
    begun = fake.begin(None)
    assert {"base", "pending_lease", "request", "precondition"} <= set(vars(begun))
    assert isinstance(begun.request.blobs, VerifiedBlobSet)
    assert {"base_head_digest", "base_generation"} <= set(vars(begun.precondition))


def test_the_in_memory_double_and_the_real_class_share_the_executor_surface():
    """Both directions: the double defines it, and production still has it.

    The executor reads private state off the repository.  A double missing
    one attribute fails deep inside the executor with an AttributeError that
    looks like a production bug; production dropping one leaves the double
    asserting a contract that no longer exists.
    """
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    double = evidence_doubles.in_memory_v5_repository({"phase": "executing"})
    for name in V5_EXECUTOR_SURFACE:
        assert hasattr(double, name), f"double lacks {name}"
    class_level = set(vars(V5CompatibilityRepository))
    init_source = inspect.getsource(V5CompatibilityRepository.__init__)
    for name in V5_EXECUTOR_SURFACE:
        assert name in class_level or f"self.{name}" in init_source, (
            f"production no longer defines {name}; drop it from the double too"
        )


def test_the_surface_list_covers_what_the_executor_reads():
    """The list is only useful if it is complete for the executor it guards."""
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    source = inspect.getsource(V5CompatibilityRepository.execute_evidence_transition_effects)
    for name in ("_admitted", "read_snapshot", "load", "observed_base", "operation_replayed"):
        assert f"self.{name}" in source
        assert name in V5_EXECUTOR_SURFACE


def test_no_test_module_keeps_a_private_copy_of_the_doubles():
    """The copies are what drifted; the shared module is the only definition."""
    from pathlib import Path

    tests = Path(__file__).resolve().parent
    offenders = []
    for path in sorted(tests.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue  # this file names the patterns it looks for
        text = path.read_text(encoding="utf-8")
        if "class _FakeFencedRepository" in text or "def _in_memory_v5_repository" in text:
            offenders.append(path.name)
    assert offenders == [], offenders
