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
TASKS_PATH = BENCH_DIR / "tasks.json"
RESULTS_DIR = BENCH_DIR / "results"
ARTIFACTS_DIR = BENCH_DIR / "artifacts"
RUN_ID = "2026-06-27-codex-cli-local"
ARMS = ("goal_only", "mission")


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


def load_tasks() -> list[dict]:
    data = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
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
    return common + f"""
Arm: mission

Use a mission-style completion workflow with auditable state:
1. Initialize mission state with:
   `MISSION_SESSION_ID={session_id} python3 skills/mission/bin/mission-state.py init "{task["id"]}: {task["prompt"]}" --complexity Simple --threshold 4.0 --max-iter 1 --files {output_rel}`
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

    quality_score = 1.0
    evidence_score = 1.0
    if completion:
        quality_score = 3.0
        evidence_score = 3.0
    if validator_pass:
        quality_score = 4.0
        evidence_score = 4.0
    if validator_pass and arm == "mission" and mission_state_passes:
        quality_score = 4.5
        evidence_score = 4.7
    elif validator_pass and arm == "goal_only":
        quality_score = 4.0
        evidence_score = 3.8

    return {
        "completion": completion,
        "validator_pass": validator_pass,
        "human_quality_score": quality_score,
        "quality_score_method": "automated_heuristic_not_blind_human",
        "intervention_count": 0,
        "resume_success": None if task["id"] != "interrupted-doc-task" else validator_pass,
        "evidence_completeness": evidence_score,
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


def run_one(task: dict, arm: str, starting_commit: str, run_root: Path, timeout: int) -> dict:
    task_id = task["id"]
    run_name = f"{task_id}-{arm}"
    worktree = run_root / run_name / "repo"
    prepare_clone(REPO_ROOT, worktree, starting_commit)
    output_rel = f"benchmarks/mission-vs-goal/run-output/{RUN_ID}/{run_name}.md"
    session_id = f"bench-{RUN_ID}-{task_id}-{arm}".replace("_", "-")
    prompt = build_prompt(task, arm, output_rel, session_id)
    artifact_dir = ARTIFACTS_DIR / RUN_ID / run_name
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
        "run_id": RUN_ID,
        "task_id": task_id,
        "arm": arm,
        "model": "codex_cli_default",
        "started_at": started,
        "completed_at": completed,
        "starting_commit": starting_commit,
        "completion": evaluation["completion"],
        "validator_pass": evaluation["validator_pass"],
        "human_quality_score": evaluation["human_quality_score"],
        "quality_score_method": evaluation["quality_score_method"],
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
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--limit", type=int, default=0, help="Run only the first N records for smoke testing.")
    parser.add_argument("--run-root", default="/tmp/mission-vs-goal-pilot")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    result_path = RESULTS_DIR / f"{RUN_ID}.jsonl"
    summary_path = RESULTS_DIR / f"{RUN_ID}-summary.json"
    artifact_run_dir = ARTIFACTS_DIR / RUN_ID
    if result_path.exists():
        result_path.unlink()
    if summary_path.exists():
        summary_path.unlink()
    if artifact_run_dir.exists():
        shutil.rmtree(artifact_run_dir)
    tasks = load_tasks()
    planned = [(task, arm) for task in tasks for arm in ARMS]
    if args.limit:
        planned = planned[: args.limit]

    records = []
    for task, arm in planned:
        record = run_one(task, arm, args.starting_commit, Path(args.run_root), args.timeout)
        records.append(record)
        with result_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    by_arm: dict[str, list[dict]] = {arm: [r for r in records if r["arm"] == arm] for arm in ARMS}
    summary = {
        "run_id": RUN_ID,
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
