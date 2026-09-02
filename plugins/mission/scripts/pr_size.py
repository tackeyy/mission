#!/usr/bin/env python3
"""Measure the reviewed area of a pull request (#719).

The shared PR-size rule asks each repository to calibrate its thresholds to its
own distribution, because the pre-calibration defaults (400 / 1,000) fire on
half of the work here and stop being read.

What makes this repository's raw diff misleading is `plugins/mission/`: it is a
byte-identical copy of `skills/mission/`, and `test_plugins_in_sync.py` fails if
the two drift.  A reviewer reads that content once.  Counting it twice inflates
every PR that touches the skill, by 19% at the median and 40% at p85.

The thresholds below are p65 and p85 of the last 100 merged PRs measured this
way.  They are documented in AGENTS.md, and the tests fail if the two disagree.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys

# Mechanically derived paths.  A human does not review these separately, so
# they do not count toward reviewed area.
#
# Widening this list is how a threshold gets evaded, which is why the shared
# rule treats changes to it as a security concern and why the tests require it
# to match the list documented in AGENTS.md exactly.
GENERATED_PATTERNS = (
    "plugins/mission/**",
    "benchmarks/*/artifacts/**",
    "*.lock",
    "package-lock.json",
    "*.snap",
)

# p65 and p85 of the last 100 merged PRs, measured with the exclusions above.
ACCOUNTABILITY_THRESHOLD = 600
SPLIT_REQUIRED_THRESHOLD = 1400


def is_generated(path: str) -> bool:
    """Whether one changed path is mechanically derived."""
    for pattern in GENERATED_PATTERNS:
        if fnmatch.fnmatch(path, pattern):
            return True
        # fnmatch treats "**" as a plain glob, so a trailing "/**" needs the
        # prefix form to match nested paths.
        if pattern.endswith("/**") and (
            path == pattern[:-3] or fnmatch.fnmatch(path, pattern[:-1])
        ):
            return True
    return False


def measure(files) -> dict:
    """Return the reviewed and excluded line counts for a set of changed files."""
    raw = 0
    generated = 0
    for entry in files:
        lines = int(entry.get("additions", 0)) + int(entry.get("deletions", 0))
        raw += lines
        if is_generated(str(entry.get("path", ""))):
            generated += lines
    reviewed = raw - generated
    if reviewed > SPLIT_REQUIRED_THRESHOLD:
        verdict = "split-required"
    elif reviewed > ACCOUNTABILITY_THRESHOLD:
        verdict = "explain"
    else:
        verdict = "ok"
    return {
        "raw": raw,
        "generated": generated,
        "reviewed": reviewed,
        "accountability_threshold": ACCOUNTABILITY_THRESHOLD,
        "split_required_threshold": SPLIT_REQUIRED_THRESHOLD,
        "verdict": verdict,
    }


def _files_for_pr(pr: str, repo: str):
    result = subprocess.run(
        ["gh", "pr", "view", pr, "--repo", repo, "--json", "files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout).get("files") or []


def _files_for_range(base: str, head: str):
    result = subprocess.run(
        ["git", "diff", "--numstat", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    files = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        additions, deletions, path = parts
        # Binary files report "-"; they carry no reviewable lines.
        files.append({
            "path": path,
            "additions": int(additions) if additions.isdigit() else 0,
            "deletions": int(deletions) if deletions.isdigit() else 0,
        })
    return files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", help="pull request number to measure")
    parser.add_argument("--repo", default="tackeyy/mission", help="owner/name for --pr")
    parser.add_argument("--base", default="origin/main", help="base ref for a local range")
    parser.add_argument("--head", default="HEAD", help="head ref for a local range")
    args = parser.parse_args(argv)

    files = _files_for_pr(args.pr, args.repo) if args.pr else _files_for_range(args.base, args.head)
    print(json.dumps(measure(files), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
