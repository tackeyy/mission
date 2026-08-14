"""Recursive Mission Python module inventory gates."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mission_python_inventory import (
    PythonModuleInventoryError,
    assert_python_module_inventory_compatible,
    assert_python_module_inventory_matches,
    discover_python_module_inventory,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _inventory(tmp_path: Path) -> tuple[Path, Path, list[object]]:
    canonical_root = tmp_path / "skills" / "mission" / "lib"
    plugin_root = tmp_path / "plugins" / "mission" / "skills" / "mission" / "lib"
    return canonical_root, plugin_root, discover_python_module_inventory(canonical_root, plugin_root)


def test_recursive_inventory_fails_when_canonical_module_is_not_mirrored(tmp_path: Path) -> None:
    canonical_root, plugin_root, _ = _inventory(tmp_path)
    _write(canonical_root / "kernel" / "new_feature.py", "VALUE = 1\n")

    inventory = discover_python_module_inventory(canonical_root, plugin_root)

    with pytest.raises(PythonModuleInventoryError) as excinfo:
        assert_python_module_inventory_matches(inventory)

    message = str(excinfo.value)
    assert "kernel/new_feature.py" in message
    assert str(canonical_root / "kernel" / "new_feature.py") in message
    assert str(plugin_root / "kernel" / "new_feature.py") in message


def test_recursive_inventory_reports_the_exact_pair_when_a_mirror_byte_changes(tmp_path: Path) -> None:
    canonical_root, plugin_root, _ = _inventory(tmp_path)
    canonical_path = canonical_root / "application" / "tool.py"
    plugin_path = plugin_root / "application" / "tool.py"
    _write(canonical_path, "VALUE = 1\n")
    _write(plugin_path, "VALUE = 2\n")

    inventory = discover_python_module_inventory(canonical_root, plugin_root)

    with pytest.raises(PythonModuleInventoryError) as excinfo:
        assert_python_module_inventory_matches(inventory)

    message = str(excinfo.value)
    assert str(canonical_path) in message
    assert str(plugin_path) in message


@pytest.mark.parametrize(
    ("relative_path", "source", "expected_fragment"),
    [
        (
            Path("kernel") / "syntax_fixture.py",
            "match value:\n    case 1:\n        pass\n",
            "syntax_fixture.py",
        ),
        (
            Path("application") / "import_fixture.py",
            "import maintainer_local_helper\n",
            "import_fixture.py",
        ),
    ],
    ids=["unsupported-syntax", "unsupported-import"],
)
def test_recursive_inventory_compatibility_gate_fails_for_bad_fixture(
    tmp_path: Path,
    relative_path: Path,
    source: str,
    expected_fragment: str,
) -> None:
    canonical_root, plugin_root, _ = _inventory(tmp_path)
    _write(canonical_root / relative_path, source)
    _write(plugin_root / relative_path, source)

    inventory = discover_python_module_inventory(canonical_root, plugin_root)

    with pytest.raises(PythonModuleInventoryError) as excinfo:
        assert_python_module_inventory_compatible(inventory)

    assert expected_fragment in str(excinfo.value)


def test_recursive_inventory_compatibility_gate_imports_from_both_roots(tmp_path: Path) -> None:
    canonical_root, plugin_root, _ = _inventory(tmp_path)
    _write(canonical_root / "persistence" / "reader.py", "VALUE = 1\n")
    _write(plugin_root / "persistence" / "reader.py", "VALUE = 1\n")

    inventory = discover_python_module_inventory(canonical_root, plugin_root)

    assert_python_module_inventory_compatible(inventory)


def test_recursive_inventory_rejects_symlinked_entries(tmp_path: Path) -> None:
    canonical_root, plugin_root, _ = _inventory(tmp_path)
    outside = tmp_path / "outside.py"
    _write(outside, "VALUE = 1\n")
    symlink_path = canonical_root / "kernel" / "linked.py"
    symlink_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(outside, symlink_path)

    with pytest.raises(PythonModuleInventoryError) as excinfo:
        discover_python_module_inventory(canonical_root, plugin_root)

    assert "linked.py" in str(excinfo.value)
