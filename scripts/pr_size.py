#!/usr/bin/env python3
"""Measure the reviewed area of a pull request (#719).

The shared PR-size rule asks each repository to calibrate its thresholds to its
own distribution, because the pre-calibration defaults (400 / 1,000) fire on
half of the work here and stop being read.

What makes this repository's raw diff misleading is the distribution copy under
`plugins/mission/`: its `skills/` and `scripts/` subtrees are held identical to
their sources by `test_plugins_in_sync.py` and `test_codex_wrapper_sync.py` --
outside `__pycache__` and `.pytest_cache`, which those tests skip.  A reviewer
reads that content once.  Counting it twice inflates every PR that
touches the skill, by 19% at the median and 40% at p85.

The rest of `plugins/mission/` is not a copy -- it carries its own CHANGELOGs
and plugin manifest -- so the allowlist names the two subtrees, not the
directory.

The thresholds below are round numbers chosen near p65 and p85 of the last 100
merged PRs measured this way; they are not those percentiles.  AGENTS.md records
both measurements and the choice, and the tests fail if the two disagree.
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
# Mechanically derived paths.  A human does not review these separately, so
# they do not count toward reviewed area.
#
# The list names only what a test enforces as a copy:
# `plugins/mission/skills/**` and `plugins/mission/scripts/**` are held
# byte-identical by test_plugins_in_sync.py and test_codex_wrapper_sync.py,
# outside the cache directories those tests skip.
# The rest of `plugins/mission/` is NOT a copy -- it carries its own CHANGELOGs
# and plugin manifest, which are content a human reads.  Excluding the whole
# directory would have quietly exempted those.
#
# Widening this list is how a threshold gets evaded, which is why the shared
# rule treats changes to it as a security concern and why the tests require it
# to match the list documented in AGENTS.md exactly.
GENERATED_PATTERNS = (
    "plugins/mission/skills/**",
    "plugins/mission/scripts/**",
    "benchmarks/*/artifacts/**",
    "*.lock",
    "package-lock.json",
    "*.snap",
)

# Round numbers chosen near p65 and p85 of the last 100 merged PRs, measured
# with the exclusions above.  The measured values are 608.0 (p65) and 1,349.0
# (p85); see AGENTS.md.
ACCOUNTABILITY_THRESHOLD = 600
SPLIT_REQUIRED_THRESHOLD = 1400


# Directories both sync tests skip when building their inventories.
_UNCOMPARED_DIRECTORIES = frozenset({"__pycache__", ".pytest_cache"})


def _matches(path: str, pattern: str) -> bool:
    """Match a pattern against a path, treating "/" as a real separator.

    ``fnmatch`` does not: its ``*`` spans separators, so ``benchmarks/*/artifacts/**``
    would also match ``benchmarks/a/b/artifacts/x``.  For an allowlist whose
    widening is a security concern, over-matching is the dangerous direction.
    """
    path_parts = [part for part in path.split("/") if part]
    pattern_parts = pattern.split("/")
    return _match_parts(path_parts, pattern_parts)


def _match_parts(path_parts, pattern_parts) -> bool:
    if not pattern_parts:
        return not path_parts
    head, rest = pattern_parts[0], pattern_parts[1:]
    if head == "**":
        # "**" is only meaningful as a trailing segment here; it stands for one
        # or more remaining segments.
        return bool(path_parts) and not rest
    if not path_parts:
        return False
    if not fnmatch.fnmatchcase(path_parts[0], head):
        return False
    return _match_parts(path_parts[1:], rest)


def is_generated(path: str) -> bool:
    """Whether one changed path is mechanically derived.

    The path is used exactly as given.  Leading and trailing spaces are legal
    in a Git path, so trimming them would let `" plugins/mission/skills/x.py"`
    -- a different file -- be excluded as if it were the mirrored one.
    """
    normalized = str(path)
    # Cache directories are excluded from the sync tests' inventory, so nothing
    # holds a file under one identical to a source.  Treating such a path as
    # generated would exempt it from review while no test compares it -- a file
    # force-added there would be invisible to both.  Count it as reviewed area.
    if any(part in _UNCOMPARED_DIRECTORIES for part in normalized.split("/")):
        return False
    for pattern in GENERATED_PATTERNS:
        if "/" not in pattern:
            # A bare pattern applies to the file name at any depth.
            if fnmatch.fnmatchcase(normalized.rsplit("/", 1)[-1], pattern):
                return True
            continue
        if _matches(normalized, pattern):
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


# `gh pr view --json files` asks for `files(first: 100)` and gives no way to
# page, so a PR with more than 100 changed files comes back silently truncated.
# The calibration set contains one (#605, 194 files), which is how this was
# found.  A short list is trustworthy; a list of exactly this length is not.
_GH_FILES_PAGE_SIZE = 100


def _files_for_pr(pr: str, repo: str):
    result = subprocess.run(
        ["gh", "pr", "view", pr, "--repo", repo, "--json", "files"],
        capture_output=True,
        text=True,
        check=True,
    )
    files = json.loads(result.stdout).get("files") or []
    if len(files) >= _GH_FILES_PAGE_SIZE:
        raise SystemExit(
            "gh returned {} files, which is its page limit -- the list may be "
            "truncated and the measurement would be wrong.  Measure this PR "
            "locally instead:\n"
            "  git fetch origin pull/{}/head && "
            "python3 scripts/pr_size.py --base origin/main --head FETCH_HEAD".format(
                len(files), pr
            )
        )
    return files


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
