#!/usr/bin/env python3
"""Stable wrapper for the canonical mission-state CLI.

The implementation lives under ``skills/mission/bin`` so the skill package can
be copied into plugin bundles. This wrapper gives users and docs a repository
root entrypoint that is stable across packaging layouts.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> int:
    target = Path(__file__).resolve().parents[1] / "skills" / "mission" / "bin" / "mission-state.py"
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
