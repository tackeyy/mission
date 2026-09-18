#!/usr/bin/env python3
"""Write one cell's counts, after every run rather than at the end.

The probe used to emit this once, after the whole loop.  A cell that was cut
off mid-loop -- by the job's timeout, which 10 repeats of a 15-minute cap can
reach -- wrote nothing at all.  `if-no-files-found: ignore` then dropped it
silently, and the surviving cells printed a bound: the failures that cell had
already observed left both the numerator and the denominator.

So the counts are rewritten after each run, and the file is moved into place
rather than written in place.  A cell killed midway leaves the counts it had,
not a half-written line that `probe_summarise.load_cells` would drop.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def build(ref: str, runner: int, failed: int, no_result: int, passed: int) -> dict:
    for name, value in (("failed", failed), ("no_result", no_result), ("passed", passed)):
        if value < 0:
            raise ValueError(f"{name} cannot be negative: {value}")
    return {
        "ref": ref,
        "runner": runner,
        "failed": failed,
        "no_result": no_result,
        "passed": passed,
    }


def write(path: Path, payload: dict) -> None:
    """Replace ``path`` atomically, so a kill never leaves a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--runner", type=int, required=True)
    parser.add_argument("--failed", type=int, required=True)
    parser.add_argument("--no-result", type=int, required=True)
    parser.add_argument("--passed", type=int, required=True)
    args = parser.parse_args(argv)

    write(args.out, build(args.ref, args.runner, args.failed, args.no_result, args.passed))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI test
    raise SystemExit(main())
