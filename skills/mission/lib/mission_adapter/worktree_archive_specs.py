"""Filesystem-facing primitives for worktree archive specification collection."""

from __future__ import annotations

from pathlib import Path


def path_name(value: object) -> str:
    return Path(value).name


def path_stem(value: object) -> str:
    return Path(value).stem


def path_suffix(value: object) -> str:
    return Path(value).suffix
