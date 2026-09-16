#!/usr/bin/env python3
"""Turn the probe's cells into a statement someone can act on.

The number this prints is the one a reader will use to decide whether a change
introduced a flake, so the two ways of getting it wrong both have to be closed
here rather than in the workflow:

  * bounding the **requested** run count instead of the measured one.  Cells
    that never reported, and runs that executed no test case, would then be
    counted as evidence of absence;
  * printing a bound at all when nothing was measured.  "0 failures" with an
    empty denominator reads exactly like a clean result.

Living in a file is the point: the previous version computed this inside the
workflow from the inputs alone, never reading what the cells reported, and no
test could reach it.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def load_cells(root: Path) -> list[dict]:
    """Read every cell report under ``root``, skipping ones that are unreadable.

    A cell that cannot be parsed is dropped rather than guessed at: its counts
    are unknown, and inventing them would put them in the denominator.
    """
    cells = []
    for path in sorted(root.rglob("cell.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("ref"), str):
            cells.append(payload)
    return cells


def totals(cells: list[dict]) -> dict[str, dict[str, int]]:
    by_ref: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"failed": 0, "no_result": 0, "passed": 0}
    )
    for cell in cells:
        bucket = by_ref[cell["ref"]]
        for key in ("failed", "no_result", "passed"):
            value = cell.get(key, 0)
            bucket[key] += value if isinstance(value, int) else 0
    return dict(by_ref)


def upper_bound(measured: int) -> float:
    """95% one-sided bound on the rate, given no failures in ``measured`` runs.

    The rule of three in its exact form: 1 - 0.05 ** (1 / n).
    """
    if measured < 1:
        raise ValueError("a bound needs at least one measured run")
    return 1 - 0.05 ** (1 / measured)


def render(cells: list[dict], requested_per_ref: int, probe_result: str) -> str:
    lines = ["## Reading this run", ""]

    if not cells:
        # Nothing to bound. Saying "0 failures" here would be the misreading
        # this whole job exists to prevent.
        lines += [
            "**No cell reported.** The probe did not measure anything, so there is no",
            "failure rate to bound. Check the probe jobs before concluding anything",
            "about either ref.",
        ]
        return "\n".join(lines) + "\n"

    by_ref = totals(cells)
    lines += ["| ref | failed | no result | measured | requested |", "|---|---|---|---|---|"]
    for ref, counts in sorted(by_ref.items()):
        # `measured` excludes no-result runs: they executed no test case, so
        # they carry no information about the rate.
        measured = counts["failed"] + counts["passed"]
        lines.append(
            f"| `{ref}` | {counts['failed']} | {counts['no_result']} | "
            f"{measured} | {requested_per_ref} |"
        )
    lines.append("")

    if probe_result != "success":
        lines += [
            "**Some probe jobs did not succeed.** The counts cover only the cells that",
            "reported.",
            "",
        ]

    for ref, counts in sorted(by_ref.items()):
        measured = counts["failed"] + counts["passed"]
        if counts["failed"]:
            rate = counts["failed"] / measured
            lines.append(f"- `{ref}`: **{counts['failed']} of {measured} failed** ({rate:.1%}).")
        elif measured:
            lines.append(
                f"- `{ref}`: no failure in **{measured} measured runs**. That puts the true "
                f"rate below about **{upper_bound(measured):.1%}** (95% one-sided). A rarer "
                "failure is invisible at this count."
            )
        else:
            lines.append(
                f"- `{ref}`: **nothing was measured** (every run was a no-result). This says "
                "nothing about the rate."
            )

    lines += [
        "",
        "- **Both refs zero** -> undetected, not absent. Raise the counts or move the",
        "  investigation to another axis.",
        "- **Only one ref fails** -> compare the per-runner cells before attributing it",
        "  to the ref; one hot runner can carry a cell.",
        "- **measured < requested** -> the difference is no-result runs and cells that",
        "  never reported. The bound uses `measured`, never `requested`.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cells", type=Path, required=True, help="directory of cell reports")
    parser.add_argument("--requested-per-ref", type=int, required=True)
    parser.add_argument("--probe-result", default="success")
    args = parser.parse_args(argv)

    print(render(load_cells(args.cells), args.requested_per_ref, args.probe_result), end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI test
    raise SystemExit(main())
