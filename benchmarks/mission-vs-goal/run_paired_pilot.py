#!/usr/bin/env python3
"""Run the local mission-vs-goal paired pilot with Codex CLI.

This runner executes a controlled local pilot. It does not claim to be a general
model benchmark. Each run starts from the same committed repository state in a
temporary clone, asks Codex CLI to produce a task artifact, then records raw
results and automated evaluator scores.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
DEFAULT_TASKS_PATH = BENCH_DIR / "tasks.json"
RESULTS_DIR = BENCH_DIR / "results"
ARTIFACTS_DIR = BENCH_DIR / "artifacts"
DEFAULT_RUN_ID = "2026-06-27-codex-cli-local"
ARMS = ("goal_only", "mission")


def strip_form(text: str) -> str:
    """Remove structural markup before marker scoring (F-2, arm-blind).

    Structure must not earn marker credit: an arm that emits more template
    sections would otherwise match section-title markers without content.
    Drops markdown headings, label-only lines (bold labels and bare
    trailing-colon labels), horizontal rules, and table separator rows; keeps
    body prose and table data rows. Kept identical in both runners.
    """
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if stripped.startswith("#"):
            continue
        compact = stripped.replace(" ", "")
        # Horizontal rules (***, ___) and table separator rows (|---|:---|, ---).
        if len(compact) >= 3 and len(set(compact)) == 1 and compact[0] in "*_":
            continue
        if set(compact) <= {"|", "-", ":"} and "-" in compact:
            continue
        # Bold label-only lines: **Evidence** / **Stop Decision:**.
        if compact.startswith("**") and compact.endswith("**") and len(compact) > 4:
            continue
        # Bare trailing-colon labels with nothing after the colon.
        if stripped.endswith(":") and "|" not in stripped and len(stripped.rstrip(":").split()) <= 6:
            continue
        kept.append(line)
    return "\n".join(kept)


def evaluate_quality_markers(text: str, task: dict) -> dict:
    """Count task-defined quality markers present in the artifact text.

    Baseline tasks (tasks.json) define no markers, so this returns a None score
    and the arm-blind scorer falls back to validator_pass only.
    """
    markers = task.get("quality_markers", [])
    if not markers:
        return {
            "quality_markers_total": 0,
            "quality_markers_matched": 0,
            "quality_markers_missing": [],
            "quality_marker_score": None,
        }
    matched = 0
    missing: list[str] = []
    for marker in markers:
        needle = marker if isinstance(marker, str) else marker.get("text", "")
        if needle and needle in text:
            matched += 1
        else:
            missing.append(needle)
    total = len(markers)
    ratio = matched / total if total else None
    return {
        "quality_markers_total": total,
        "quality_markers_matched": matched,
        "quality_markers_missing": missing,
        "quality_marker_score": round(ratio, 2) if ratio is not None else None,
    }


def score_from_signals(validator_pass: bool, marker_score: float | None) -> tuple[float, float]:
    """Deterministic, arm-blind scoring (F-1).

    The arm identity is never consulted here; the same artifact signals produce
    the same score for any arm. Returns (quality_score, evidence_completeness).
        quality = 1.0 + 3.0 * validator_pass + 1.0 * (marker_score or 0.0)
    Marker-less tasks (marker_score is None -> treated as 0.0) collapse to 1.0
    (fail) or 4.0 (pass).
    """
    base = marker_score if marker_score is not None else 0.0
    value = round(1.0 + 3.0 * (1.0 if validator_pass else 0.0) + 1.0 * base, 2)
    return value, value


def counterbalanced_plan(tasks: list[dict], arms) -> list[tuple[dict, str, int]]:
    """Alternate which arm runs first by task index to cancel run-order bias (F-1).

    Even task index -> arms in declared order; odd index -> reversed. Deterministic
    (no randomness, for reproducibility). Returns (task, arm, arm_order) triples,
    where arm_order is 1 for the arm that runs first for that task.
    """
    ordered_arms = tuple(arms)
    plan: list[tuple[dict, str, int]] = []
    for idx, task in enumerate(tasks):
        sequence = ordered_arms if idx % 2 == 0 else tuple(reversed(ordered_arms))
        for order, arm in enumerate(sequence, start=1):
            plan.append((task, arm, order))
    return plan


def run_command(args: list[str], cwd: Path | None = None, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_task_data(tasks_path: Path) -> dict:
    return json.loads(tasks_path.read_text(encoding="utf-8"))


def load_tasks(tasks_path: Path) -> list[dict]:
    data = load_task_data(tasks_path)
    return data["tasks"]


def prepare_clone(source: Path, target: Path, starting_commit: str) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    clone = run_command(["git", "clone", "--no-hardlinks", str(source), str(target)], timeout=120)
    if clone.returncode != 0:
        raise RuntimeError(f"git clone failed: {clone.stderr}")
    checkout = run_command(["git", "checkout", "--detach", starting_commit], cwd=target, timeout=120)
    if checkout.returncode != 0:
        raise RuntimeError(f"git checkout failed: {checkout.stderr}")


def build_prompt(task: dict, arm: str, output_rel: str, session_id: str) -> str:
    common = f"""You are executing one controlled local benchmark run.

Rules:
- Do not commit, push, install packages, or use network access.
- Write exactly one task artifact at `{output_rel}`.
- Keep edits narrowly scoped to benchmark output files. For the mission arm, `.mission-state/` is also allowed.
- Do not claim benchmark superiority. Only complete this task artifact.

Task id: {task["id"]}
Task category: {task["category"]}
Task prompt: {task["prompt"]}
Task validator: {task["validator"]}
"""
    if arm == "goal_only":
        return common + """
Arm: goal_only

Use a lightweight goal-only workflow:
1. Restate the concrete goal.
2. Do the smallest useful work needed for this benchmark artifact.
3. Stop when the artifact satisfies the validator.

The artifact must include these headings:
- Goal
- Result
- Evidence
- Assumptions
"""
    mission_complexity = task.get("mission_complexity", "Simple")
    mission_max_iter = task.get("mission_max_iter", 1)
    return common + f"""
Arm: mission

Use a mission-style completion workflow with auditable state:
1. Initialize mission state with:
   `MISSION_SESSION_ID={session_id} python3 skills/mission/bin/mission-state.py init "{task["id"]}: controlled benchmark artifact" --complexity {mission_complexity} --threshold 4.0 --max-iter {mission_max_iter} --files {output_rel}`
2. Produce the task artifact.
3. Review the artifact yourself against the validator.
4. Record a passing score with `push-score`.
5. Run `mark-passes`.

The artifact must include these headings:
- Mission
- Plan
- Execution
- Review
- Score
- Stop Decision
- Evidence
- Assumptions
"""


def evaluate_run(worktree: Path, task: dict, arm: str, output_rel: str, session_id: str, returncode: int) -> dict:
    output_path = worktree / output_rel
    text = output_path.read_text(encoding="utf-8") if output_path.exists() else ""
    required_headings = ["Evidence", "Assumptions"]
    if arm == "goal_only":
        required_headings += ["Goal", "Result"]
    else:
        required_headings += ["Mission", "Plan", "Execution", "Review", "Score", "Stop Decision"]

    missing_headings = [heading for heading in required_headings if heading not in text]
    completion = returncode == 0 and output_path.exists()
    validator_pass = completion and not missing_headings
    mission_state_passes = None
    if arm == "mission":
        state_path = worktree / ".mission-state" / "sessions" / f"{session_id}.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            mission_state_passes = bool(state.get("passes") is True and state.get("loop_active") is False)
            validator_pass = validator_pass and mission_state_passes
        else:
            mission_state_passes = False
            validator_pass = False

    # F-2: markers are scored against the form-stripped body; the unstripped
    # score is kept as quality_marker_score_raw for comparability.
    marker_eval = evaluate_quality_markers(strip_form(text), task)
    marker_eval_raw = evaluate_quality_markers(text, task)
    if not validator_pass:
        marker_eval["quality_marker_score"] = None
        marker_eval_raw["quality_marker_score"] = None
    quality_score, evidence_score = score_from_signals(
        validator_pass, marker_eval["quality_marker_score"]
    )

    return {
        "completion": completion,
        "validator_pass": validator_pass,
        "human_quality_score": quality_score,
        "quality_score_method": "automated_heuristic_form_stripped_not_blind_human",
        "intervention_count": 0,
        "resume_success": None if task["id"] != "interrupted-doc-task" else validator_pass,
        "evidence_completeness": evidence_score,
        "quality_marker_score": marker_eval["quality_marker_score"],
        "quality_marker_score_raw": marker_eval_raw["quality_marker_score"],
        "missing_headings": missing_headings,
        "mission_state_passes": mission_state_passes,
    }


def copy_artifacts(worktree: Path, artifact_dir: Path, output_rel: str, last_message: Path, event_log: Path) -> list[str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for source, name in (
        (worktree / output_rel, "artifact.md"),
        (last_message, "last-message.txt"),
        (event_log, "codex-events.jsonl"),
    ):
        if source.exists():
            dest = artifact_dir / name
            if source.resolve() != dest.resolve():
                shutil.copy2(source, dest)
            copied.append(str(dest.relative_to(REPO_ROOT)))
    diff = run_command(["git", "diff", "--", output_rel, ".mission-state"], cwd=worktree, timeout=60)
    diff_path = artifact_dir / "diff.patch"
    diff_path.write_text(diff.stdout, encoding="utf-8")
    copied.append(str(diff_path.relative_to(REPO_ROOT)))
    return copied


def run_one(task: dict, arm: str, run_id: str, starting_commit: str, run_root: Path, timeout: int, arm_order: int, model_id: str) -> dict:
    task_id = task["id"]
    run_name = f"{task_id}-{arm}"
    worktree = run_root / run_name / "repo"
    prepare_clone(REPO_ROOT, worktree, starting_commit)
    output_rel = f"benchmarks/mission-vs-goal/run-output/{run_id}/{run_name}.md"
    session_id = f"bench-{run_id}-{task_id}-{arm}".replace("_", "-")
    prompt = build_prompt(task, arm, output_rel, session_id)
    artifact_dir = ARTIFACTS_DIR / run_id / run_name
    last_message = artifact_dir / "last-message.txt"
    event_log = artifact_dir / "codex-events.jsonl"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    started = iso_now()
    start_time = time.monotonic()
    with event_log.open("w", encoding="utf-8") as events:
        proc = subprocess.run(
            [
                "codex",
                "--ask-for-approval",
                "never",
                "exec",
                "--cd",
                str(worktree),
                "--sandbox",
                "workspace-write",
                "--output-last-message",
                str(last_message),
                "--json",
                prompt,
            ],
            text=True,
            stdout=events,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    elapsed = round((time.monotonic() - start_time) / 60, 2)
    completed = iso_now()
    evaluation = evaluate_run(worktree, task, arm, output_rel, session_id, proc.returncode)
    artifacts = copy_artifacts(worktree, artifact_dir, output_rel, last_message, event_log)
    stderr_path = artifact_dir / "stderr.txt"
    stderr_path.write_text(proc.stderr, encoding="utf-8")
    artifacts.append(str(stderr_path.relative_to(REPO_ROOT)))

    notes = [
        f"codex_exec_returncode={proc.returncode}",
        f"quality_score_method={evaluation['quality_score_method']}",
    ]
    if evaluation["missing_headings"]:
        notes.append(f"missing_headings={','.join(evaluation['missing_headings'])}")
    if proc.stderr.strip():
        notes.append("stderr captured in artifact")

    return {
        "benchmark": "mission-vs-goal-pilot",
        "run_id": run_id,
        "task_id": task_id,
        "arm": arm,
        "arm_order": arm_order,
        "model": "codex_cli_default",
        "model_id": model_id,
        "started_at": started,
        "completed_at": completed,
        "starting_commit": starting_commit,
        "completion": evaluation["completion"],
        "validator_pass": evaluation["validator_pass"],
        "human_quality_score": evaluation["human_quality_score"],
        "quality_score_method": evaluation["quality_score_method"],
        "quality_marker_score": evaluation["quality_marker_score"],
        "intervention_count": evaluation["intervention_count"],
        "resume_success": evaluation["resume_success"],
        "evidence_completeness": evaluation["evidence_completeness"],
        "elapsed_minutes": elapsed,
        "token_estimate": None,
        "artifacts": artifacts,
        "notes": "; ".join(notes),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--starting-commit", required=True)
    parser.add_argument("--tasks-file", default=str(DEFAULT_TASKS_PATH))
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N records for smoke testing.")
    parser.add_argument("--run-root", default="/tmp/mission-vs-goal-pilot")
    parser.add_argument(
        "--model-id",
        required=True,
        help="Truthful model identifier for this run (e.g. codex-cli-<version>). "
        "Recorded verbatim; there is no silent 'unknown' fallback.",
    )
    args = parser.parse_args()

    tasks_path = Path(args.tasks_file)
    if not tasks_path.is_absolute():
        tasks_path = REPO_ROOT / tasks_path

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"{args.run_id}.jsonl"
    summary_path = RESULTS_DIR / f"{args.run_id}-summary.json"
    artifact_run_dir = ARTIFACTS_DIR / args.run_id
    if result_path.exists():
        result_path.unlink()
    if summary_path.exists():
        summary_path.unlink()
    if artifact_run_dir.exists():
        shutil.rmtree(artifact_run_dir)
    task_data = load_task_data(tasks_path)
    tasks = task_data["tasks"]
    planned = counterbalanced_plan(tasks, ARMS)
    if args.limit:
        planned = planned[: args.limit]

    records = []
    for task, arm, arm_order in planned:
        record = run_one(task, arm, args.run_id, args.starting_commit, Path(args.run_root), args.timeout, arm_order, args.model_id)
        records.append(record)
        with result_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    by_arm: dict[str, list[dict]] = {arm: [r for r in records if r["arm"] == arm] for arm in ARMS}
    summary = {
        "run_id": args.run_id,
        "task_file": str(tasks_path.relative_to(REPO_ROOT)),
        "task_cohort": task_data.get("cohort", "baseline"),
        "starting_commit": args.starting_commit,
        "records": len(records),
        "expected_records": len(tasks) * len(ARMS),
        "arms": {
            arm: {
                "records": len(items),
                "completion_rate": sum(1 for r in items if r["completion"]) / len(items) if items else None,
                "validator_pass_rate": sum(1 for r in items if r["validator_pass"]) / len(items) if items else None,
                "average_quality_score": round(sum(r["human_quality_score"] for r in items) / len(items), 2) if items else None,
                "average_intervention_count": round(sum(r["intervention_count"] for r in items) / len(items), 2) if items else None,
                "average_evidence_completeness": round(sum(r["evidence_completeness"] for r in items) / len(items), 2) if items else None,
                "average_elapsed_minutes": round(sum(r["elapsed_minutes"] for r in items) / len(items), 2) if items else None,
            }
            for arm, items in by_arm.items()
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
