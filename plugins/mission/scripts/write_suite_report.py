#!/usr/bin/env python3
"""Write the suite report the integration gate requires (#735).

The gate cannot tell a passing suite from one that ran nothing, because both
exit zero.  This records what actually ran, bound to the tree it ran against.

The count comes from the JUnit XML pytest already writes, not from parsing its
console output: the output format is presentational and changes between
versions, while the XML is a contract.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def executed_count(junit_path: Path) -> int:
    """Count the test cases the run actually executed.

    Skipped cases are excluded: a suite that skips everything has not exercised
    anything, and counting skips would let it look like it had.
    """
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    executed = 0
    for suite in suites:
        for case in suite.findall("testcase"):
            if case.find("skipped") is None:
                executed += 1
    return executed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ci-shard", help="emit a CI shard receipt as INDEX/TOTAL")
    parser.add_argument("--test-manifest", help="target recorded in a CI shard receipt")
    args = parser.parse_args(argv)
    if (args.ci_shard is None) != (args.test_manifest is None):
        parser.error("--ci-shard and --test-manifest must be provided together")
    if args.ci_shard is not None:
        try:
            index, total = (int(part) for part in args.ci_shard.split("/"))
        except ValueError:
            parser.error("--ci-shard must be INDEX/TOTAL")
        if index <= 0 or total <= 0 or index > total:
            parser.error("--ci-shard must name a shard within its positive total")

    # `git write-tree`, not `HEAD^{tree}`: the gate integrates the PR into the
    # base **without committing** and observes the index, so the two differ
    # exactly when it matters.  Recording the committed tree would make every
    # honest report fail the gate's binding check.
    tree_sha = subprocess.run(
        ["git", "write-tree"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    executed = executed_count(args.junit)
    report = {
        "schema": "mission-suite-report/1",
        "tree_sha": tree_sha,
        "executed": executed,
        "status": "complete",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.ci_shard is not None:
        if executed <= 0:
            return 1
        print(json.dumps({
            "schema": "mission-test-report/1",
            "tree_sha": tree_sha,
            "tier": "shard",
            "shard": args.ci_shard,
            "test_manifest": [args.test_manifest],
            "executed": executed,
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
