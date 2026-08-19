"""LLM-as-judge sidecar for quality-marker evaluation.

Reads existing benchmark artifacts and emits a JSONL sidecar.
Does NOT modify results/ or artifacts/ directories.

Usage:
    python judge_quality_markers.py \\
        --run-id 2026-08-19-tail-v280-r2 \\
        --model-id claude-3-5-sonnet-20241022 \\
        [--artifacts-dir benchmarks/mission-vs-goal/artifacts] \\
        [--out judge_results/<run-id>.jsonl] \\
        [--tasks-file benchmarks/mission-vs-goal/tasks.tail.json] \\
        [--dry-run]

Denominator policy for judge_marker_score:
    identified: null entries (judge errors / exceptions) are excluded from both
    numerator and denominator. Score = count(identified==True) / count(identified!=null).
    If all markers are null (all judge calls failed), score is null rather than 0/0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

JudgeFn = Callable[[str, str], Dict[str, Any]]
"""Callable(prompt: str, model_id: str) -> {"identified": bool, "reason": str}

May raise on error; the caller records identified=null and continues.
"""


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def load_tasks(tasks_file: Path) -> Dict[str, Any]:
    """Return a dict keyed by task_id from a tasks JSON file."""
    with open(tasks_file, encoding="utf-8") as f:
        data = json.load(f)
    tasks_raw = data.get("tasks", [])
    if isinstance(tasks_raw, list):
        return {t["id"]: t for t in tasks_raw}
    if isinstance(tasks_raw, dict):
        return tasks_raw
    raise ValueError(f"Unexpected tasks format in {tasks_file}")


def find_tasks_file_for_run(run_id: str, bench_dir: Path) -> Path:
    """Heuristically pick a tasks file based on the run-id prefix.

    Looks for the first tasks JSON file whose cohort or name matches
    a substring of the run_id. Falls back to tasks.tail.json if present,
    otherwise tasks.json.
    """
    candidates = sorted(bench_dir.glob("tasks.*.json"))
    for c in candidates:
        stem = c.stem.replace("tasks.", "")  # e.g. "tail"
        if stem in run_id:
            return c
    # Fall back
    fallback = bench_dir / "tasks.tail.json"
    if fallback.exists():
        return fallback
    return bench_dir / "tasks.json"


# ---------------------------------------------------------------------------
# Artifact loading
# ---------------------------------------------------------------------------

def load_artifact_body(artifact_paths: List[str], repo_root: Optional[Path] = None) -> str:
    """Load the primary artifact.md content.

    Returns empty string if not found.
    """
    root = repo_root or Path(".")
    for ap in artifact_paths:
        p = Path(ap)
        if not p.is_absolute():
            p = root / p
        if p.name == "artifact.md" and p.exists():
            return p.read_text(encoding="utf-8")
    # Fallback: first .md file
    for ap in artifact_paths:
        p = Path(ap)
        if not p.is_absolute():
            p = root / p
        if p.suffix == ".md" and p.exists():
            return p.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# Results loading
# ---------------------------------------------------------------------------

def load_results(run_id: str, results_dir: Path) -> Dict[Tuple[str, str], float]:
    """Load automated marker scores keyed by (task_id, arm).

    Returns empty dict if the results file is missing.
    """
    path = results_dir / f"{run_id}.jsonl"
    if not path.exists():
        return {}
    scores: Dict[Tuple[str, str], float] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            task_id = rec.get("task_id")
            arm = rec.get("arm")
            score = rec.get("quality_marker_score")
            if task_id and arm:
                scores[(task_id, arm)] = score
    return scores


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_judge_prompt(marker_name: str, artifact_body: str) -> str:
    """Build the prompt sent to the LLM judge for one marker.

    The marker's `patterns` are intentionally omitted to avoid biasing
    the judge toward string-matching behaviour.
    """
    return (
        f"You are evaluating whether a written artifact has *identified* a specific "
        f"defect or mismatch as a concrete finding.\n\n"
        f"## Marker\n\n"
        f"{marker_name}\n\n"
        f"## Artifact\n\n"
        f"{artifact_body}\n\n"
        f"## Judging criterion\n\n"
        f"Mere mention of a value is NOT identification. "
        f"Identification requires that the artifact explicitly names the item as a "
        f"spec/implementation mismatch or defect — not just that the value appears "
        f"somewhere in the text.\n\n"
        f"## Output format\n\n"
        f'Return a JSON object with exactly two keys: "identified" (boolean) and '
        f'"reason" (string, at most 80 characters).\n\n'
        f'Example: {{"identified": true, "reason": "Artifact names the value as '
        f'contradicting the spec requirement."}}\n'
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_markers(
    task: Dict[str, Any],
    artifact_body: str,
    judge_fn: JudgeFn,
    model_id: str,
    dry_run: bool,
) -> List[Dict[str, Any]]:
    """Run the judge for each quality marker, returning the result list.

    In dry-run mode, prints the prompt and returns a stub record with
    identified=null (no LLM call is made).
    """
    markers = task.get("quality_markers", [])
    results: List[Dict[str, Any]] = []

    for marker in markers:
        name = marker["name"]
        # patterns must NOT be passed to the judge
        prompt = build_judge_prompt(name, artifact_body)

        if dry_run:
            print(f"\n{'='*60}")
            print(f"[DRY-RUN] Marker: {name}")
            print(f"{'='*60}")
            print(prompt)
            results.append({"marker": name, "identified": None, "reason": "dry-run"})
            continue

        try:
            verdict = judge_fn(prompt, model_id)
            identified = verdict.get("identified")
            reason = str(verdict.get("reason", ""))[:80]
        except Exception as exc:  # noqa: BLE001
            identified = None
            reason = f"judge-error: {type(exc).__name__}: {str(exc)[:60]}"

        results.append({"marker": name, "identified": identified, "reason": reason})

    return results


def compute_marker_score(marker_results: List[Dict[str, Any]]) -> Optional[float]:
    """Fraction of non-null markers that were identified.

    identified=null entries are excluded from both numerator and denominator.
    Returns null (None) if all entries are null.
    """
    valid = [r for r in marker_results if r.get("identified") is not None]
    if not valid:
        return None
    identified_count = sum(1 for r in valid if r["identified"] is True)
    return identified_count / len(valid)


# ---------------------------------------------------------------------------
# Main run logic
# ---------------------------------------------------------------------------

def run(
    run_id: str,
    artifacts_dir: Path,
    results_dir: Path,
    tasks: Dict[str, Any],
    model_id: str,
    out_path: Path,
    judge_fn: JudgeFn,
    dry_run: bool,
    repo_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Execute the judge sidecar for all artifact directories in the run.

    Returns the list of output records.
    Raises nothing — per-marker errors are captured inside score_markers.
    """
    run_artifacts_dir = artifacts_dir / run_id
    if not run_artifacts_dir.exists():
        raise FileNotFoundError(f"Artifacts directory not found: {run_artifacts_dir}")

    automated_scores = load_results(run_id, results_dir)

    out_records: List[Dict[str, Any]] = []

    # Each subdirectory is <task-id>-<arm>
    for arm_dir in sorted(run_artifacts_dir.iterdir()):
        if not arm_dir.is_dir():
            continue
        dir_name = arm_dir.name
        # Find the matching (task_id, arm) by scanning known tasks
        task_id, arm = _split_task_arm(dir_name, tasks)

        task = tasks.get(task_id, {})
        artifact_paths = [str(arm_dir / "artifact.md")]

        artifact_body = load_artifact_body(artifact_paths, repo_root)
        artifact_digest = hashlib.sha256(artifact_body.encode()).hexdigest()[:16]

        marker_results = score_markers(
            task, artifact_body, judge_fn, model_id, dry_run
        )
        judge_score = compute_marker_score(marker_results)
        automated_score = automated_scores.get((task_id, arm))

        record: Dict[str, Any] = {
            "run_id": run_id,
            "task_id": task_id,
            "arm": arm,
            "judge_model_id": model_id,
            "artifact_digest": artifact_digest,
            "judge_marker_results": marker_results,
            "judge_marker_score": judge_score,
            "automated_marker_score": automated_score,
        }
        out_records.append(record)

        if not dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return out_records


def _split_task_arm(dir_name: str, tasks: Dict[str, Any]) -> Tuple[str, str]:
    """Split a directory name like '<task-id>-<arm>' into (task_id, arm).

    Tries longest-prefix match against known task IDs.
    Falls back to splitting on the last '-' separated segment that looks
    like a known arm suffix.
    """
    known_arms = {
        "mission",
        "claude_code_goal_command",
        "goal",
        "codex",
    }
    # Try known task ids (longest match wins)
    for task_id in sorted(tasks.keys(), key=len, reverse=True):
        if dir_name.startswith(task_id + "-"):
            arm = dir_name[len(task_id) + 1:]
            return task_id, arm

    # Fallback: check for known arm suffixes
    for arm in sorted(known_arms, key=len, reverse=True):
        suffix = "-" + arm
        if dir_name.endswith(suffix):
            task_id = dir_name[: -len(suffix)]
            return task_id, arm

    # Last resort: treat everything before final '-' as task_id
    parts = dir_name.rsplit("-", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return dir_name, "unknown"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="LLM-as-judge sidecar for benchmark quality markers."
    )
    p.add_argument("--run-id", required=True, help="Run ID to evaluate (required).")
    p.add_argument(
        "--artifacts-dir",
        default="benchmarks/mission-vs-goal/artifacts",
        help="Root artifacts directory (default: benchmarks/mission-vs-goal/artifacts).",
    )
    p.add_argument(
        "--results-dir",
        default="benchmarks/mission-vs-goal/results",
        help="Root results directory (default: benchmarks/mission-vs-goal/results).",
    )
    p.add_argument(
        "--model-id",
        required=True,
        help="Model ID to use as judge (required; no default).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output JSONL path (default: judge_results/<run-id>.jsonl).",
    )
    p.add_argument(
        "--tasks-file",
        default=None,
        help=(
            "Path to tasks JSON file. "
            "Defaults to auto-detect from run-id "
            "(e.g. 'tail' in run-id → tasks.tail.json)."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print prompts without calling the judge.",
    )
    return p


def _real_judge(prompt: str, model_id: str) -> Dict[str, Any]:
    """Real judge implementation using the Anthropic SDK.

    Separated from CLI logic so tests can inject a dummy.
    """
    try:
        import anthropic  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "anthropic SDK not installed. Install it or use --dry-run."
        ) from exc

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model_id,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    # Extract JSON from the response
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON found in judge response: {text[:120]}")
    return json.loads(text[start:end])


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    bench_dir = Path("benchmarks/mission-vs-goal")
    artifacts_dir = Path(args.artifacts_dir)
    results_dir = Path(args.results_dir)

    tasks_file = (
        Path(args.tasks_file)
        if args.tasks_file
        else find_tasks_file_for_run(args.run_id, bench_dir)
    )
    if not tasks_file.exists():
        print(f"Error: tasks file not found: {tasks_file}", file=sys.stderr)
        return 1

    tasks = load_tasks(tasks_file)

    out_path = (
        Path(args.out)
        if args.out
        else Path("benchmarks/mission-vs-goal/judge_results") / f"{args.run_id}.jsonl"
    )

    records = run(
        run_id=args.run_id,
        artifacts_dir=artifacts_dir,
        results_dir=results_dir,
        tasks=tasks,
        model_id=args.model_id,
        out_path=out_path,
        judge_fn=_real_judge,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        print(f"Wrote {len(records)} records to {out_path}")
    else:
        print(f"\n[DRY-RUN] {len(records)} artifact dirs processed; no output written.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
