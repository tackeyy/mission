from __future__ import annotations

"""Recursive inventory for Mission Python production modules."""

from dataclasses import dataclass
import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path


class PythonModuleInventoryError(RuntimeError):
    """Raised when the recursive Python module inventory is invalid."""


@dataclass(frozen=True)
class PythonModuleInventoryEntry:
    relative_path: Path
    canonical_path: Path
    plugin_path: Path
    module_name: str


@dataclass(frozen=True)
class PythonModuleInventory:
    canonical_root: Path
    plugin_root: Path
    entries: tuple[PythonModuleInventoryEntry, ...]


def discover_python_module_inventory(
    canonical_root: Path,
    plugin_root: Path,
) -> PythonModuleInventory:
    """Discover a deterministic recursive inventory of canonical .py modules."""
    canonical_root = Path(canonical_root)
    plugin_root = Path(plugin_root)
    entries = tuple(
        PythonModuleInventoryEntry(
            relative_path=relative_path,
            canonical_path=canonical_root / relative_path,
            plugin_path=plugin_root / relative_path,
            module_name=_module_name_from_relative_path(relative_path),
        )
        for relative_path in _discover_relative_python_paths(canonical_root)
    )
    return PythonModuleInventory(
        canonical_root=canonical_root,
        plugin_root=plugin_root,
        entries=entries,
    )


def assert_python_module_inventory_matches(inventory: PythonModuleInventory) -> None:
    """Fail when the recursive inventory is missing mirrors or differs byte-for-byte."""
    for entry in inventory.entries:
        canonical = entry.canonical_path
        plugin = entry.plugin_path
        if not canonical.exists():
            raise PythonModuleInventoryError(f"missing canonical module: {canonical}")
        if not plugin.exists():
            raise PythonModuleInventoryError(
                "missing plugin mirror for recursive Python inventory entry "
                f"{entry.relative_path}: canonical={canonical} plugin={plugin}"
            )
        if canonical.is_symlink():
            raise PythonModuleInventoryError(f"canonical inventory entry is a symlink: {canonical}")
        if plugin.is_symlink():
            raise PythonModuleInventoryError(f"plugin inventory entry is a symlink: {plugin}")
        if canonical.read_bytes() != plugin.read_bytes():
            raise PythonModuleInventoryError(
                "recursive Python inventory entry differs byte-for-byte: "
                f"{entry.relative_path} canonical={canonical} plugin={plugin}"
            )


def assert_python_module_inventory_compatible(
    inventory: PythonModuleInventory,
    *,
    parse_feature_version: tuple[int, int] = (3, 9),
) -> None:
    """Fail when discovered modules do not parse with the supported grammar or import cleanly."""
    for entry in inventory.entries:
        try:
            ast.parse(
                entry.canonical_path.read_text(encoding="utf-8"),
                filename=str(entry.canonical_path),
                feature_version=parse_feature_version,
            )
        except SyntaxError as exc:
            raise PythonModuleInventoryError(
                "recursive Python inventory entry does not parse with the supported grammar: "
                f"{entry.relative_path} canonical={entry.canonical_path}"
            ) from exc

        _assert_importable_from_root(inventory.canonical_root, entry.module_name, entry.canonical_path)
        _assert_importable_from_root(inventory.plugin_root, entry.module_name, entry.plugin_path)


def _discover_relative_python_paths(root: Path) -> list[Path]:
    if root.exists() and root.is_symlink():
        raise PythonModuleInventoryError(f"inventory root is a symlink: {root}")
    if not root.exists():
        return []

    discovered: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = sorted(dirnames)
        filenames = sorted(filenames)
        current = Path(dirpath)

        pruned_dirnames: list[str] = []
        for dirname in dirnames:
            candidate = current / dirname
            if candidate.name == "__pycache__":
                continue
            if candidate.is_symlink():
                raise PythonModuleInventoryError(f"symlinked directory is not allowed in inventory: {candidate}")
            pruned_dirnames.append(dirname)
        dirnames[:] = pruned_dirnames

        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            candidate = current / filename
            if candidate.is_symlink():
                raise PythonModuleInventoryError(f"symlinked module is not allowed in inventory: {candidate}")
            discovered.append(candidate.relative_to(root))

    discovered.sort(key=lambda path: path.as_posix())
    return discovered


def _module_name_from_relative_path(relative_path: Path) -> str:
    parts = list(relative_path.with_suffix("").parts)
    if not parts:
        raise PythonModuleInventoryError("root-level __init__.py is not a supported inventory entry")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        raise PythonModuleInventoryError("root-level __init__.py is not a supported inventory entry")
    return ".".join(parts)


def _assert_importable_from_root(root: Path, module_name: str, path: Path) -> None:
    with tempfile.TemporaryDirectory() as tempdir:
        env = {
            "PYTHONPATH": str(root),
            "PYTHONNOUSERSITE": "1",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import importlib, sys; importlib.import_module(sys.argv[1])",
                module_name,
            ],
            cwd=tempdir,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise PythonModuleInventoryError(
                "recursive Python inventory entry is not importable from the configured root: "
                f"module={module_name} root={root} path={path}\n"
                f"stderr={result.stderr.strip()}"
            )
