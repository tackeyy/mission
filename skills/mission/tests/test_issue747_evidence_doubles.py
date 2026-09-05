"""#747 項目 7: テストダブルは 1 箇所に置き、production の形に機械的に追従させる.

第 1 段で 5 回、第 2 段でも起きた「production は正しいがダブルが契約に追いついて
いない」を、次の経路追加で 3 倍にしないための固定。
"""
import importlib
import inspect
import re

import pytest

from . import evidence_doubles
from .evidence_doubles import (
    PRODUCTION_SHAPES,
    V5_EXECUTOR_INSTANCE_STATE,
    V5_EXECUTOR_SURFACE,
    executor_surface_from_source,
)


def _resolve(spec):
    module_name, _, attribute = spec.partition(":")
    return getattr(importlib.import_module(module_name), attribute)


def _walk(root, path):
    """Follow ``FakeFencedRepository.begin().precondition`` on a live object."""
    value = root
    for token in path.split(".")[1:]:
        if not token.endswith("()"):
            value = getattr(value, token)
            continue
        method = getattr(value, token[:-2])
        if token == "_stage_persistence()":
            value = method(None, state_bytes=b"{}", effects=())
        else:
            value = method(*(None,) * len(inspect.signature(method).parameters))
    return value


def _fake():
    return evidence_doubles.FakeFencedRepository(
        evidence_doubles.decoded_state(
            {"phase": "executing", "loop_active": True, "session_id": "portable"}
        )
    )


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


@pytest.mark.parametrize("double_path,production,fields", PRODUCTION_SHAPES,
                         ids=[shape[0] for shape in PRODUCTION_SHAPES])
def test_the_fenced_fake_really_answers_each_declared_shape(double_path, production, fields):
    """The declaration is only worth checking if the fake honours it.

    Derived from ``PRODUCTION_SHAPES`` rather than restated, so a shape added
    to the declaration is exercised here without a second edit.
    """
    value = _walk(_fake(), double_path)
    for field in fields:
        assert hasattr(value, field), f"{double_path} does not answer {field!r}"


def test_the_fenced_fake_answers_the_types_the_executor_relies_on():
    """`hasattr` alone would accept a string where a blob set is expected."""
    from mission_persistence.local_uow import VerifiedBlobSet

    fake = _fake()
    assert fake.read("portable").head.generation == 0
    assert isinstance(fake.begin(None).request.blobs, VerifiedBlobSet)
    assert fake.begin(None).precondition.base_generation == 0


def test_the_surface_list_equals_what_the_executor_reads():
    """The list is derived from the executor's source, not remembered.

    Hard-coding a handful of names let ``_effect_transaction`` go missing
    from both the list and the double: the executor reads it on the
    legacy-publisher branch, ``__init__`` sets it, and a double built without
    ``__init__`` raised AttributeError there.  Equality in both directions
    means the executor cannot start reading a new attribute, nor stop reading
    an old one, without this file changing in the same commit.
    """
    assert executor_surface_from_source() == frozenset(V5_EXECUTOR_SURFACE)


def test_the_in_memory_double_defines_every_instance_attribute_the_executor_reads():
    """Methods come with the class; instance state does not, so the double sets it.

    Every non-callable name in the surface has to be instance state the
    double assigns, and every assigned name has to exist on production.
    """
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    double = evidence_doubles.in_memory_v5_repository({"phase": "executing"})
    for name in V5_EXECUTOR_SURFACE:
        assert hasattr(double, name), f"double lacks {name}"
    # Resolve through the class so descriptors (staticmethod, property) are
    # seen as what they produce; the raw entry in ``vars`` is not callable for
    # a staticmethod before Python 3.10, and the project still declares 3.9.
    raw = vars(V5CompatibilityRepository)
    non_callable = [
        name for name in V5_EXECUTOR_SURFACE
        if not isinstance(raw.get(name), property)
        and not callable(getattr(V5CompatibilityRepository, name, None))
    ]
    for name in non_callable:
        assert name in V5_EXECUTOR_INSTANCE_STATE, (
            f"{name} is instance state the executor reads; the double must set it"
        )
    init_source = inspect.getsource(V5CompatibilityRepository.__init__)
    for name in V5_EXECUTOR_INSTANCE_STATE:
        assert re.search(rf"\bself\.{re.escape(name)}\b", init_source), (
            f"production __init__ no longer sets {name}; drop it from the double too"
        )
        assert hasattr(double, name), f"double lacks instance state {name}"


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
