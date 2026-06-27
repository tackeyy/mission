# Goal

Create a short internal research brief for task `ambiguous-research-brief` using only available repository evidence, without external browsing.

# Result

The current repo evidence supports a narrow internal conclusion: the `mission` vs `goal_only` benchmark package is ready to run, but it does not yet support comparative outcome claims. The pilot is designed to test whether `mission`'s stateful plan, review, scoring, and iteration loop improves multi-step work versus a lightweight goal-only baseline. The selected task is intentionally ambiguous and research-oriented, so its useful output is a concise, evidence-grounded brief rather than a code change.

Inference: for this benchmark task, a goal-only workflow is sufficient to produce the requested artifact because the validator is document-level and directly checkable: separate evidence from inference and list assumptions. This is not evidence that goal-only is better or worse than `mission`; it is only an observation about this single local run.

# Evidence

- `benchmarks/mission-vs-goal/README.md:3-8` says the directory contains a 10-task pilot comparing `mission` with a goal-only baseline, and that the benchmark is intentionally small.
- `benchmarks/mission-vs-goal/README.md:12-24` defines the research question and the two arms: `goal_only` as a lightweight completion-condition workflow and `mission` as a stateful loop-engineering workflow.
- `benchmarks/mission-vs-goal/tasks.json:56-62` defines `ambiguous-research-brief` as a research task whose validator requires separating evidence from inference and listing assumptions.
- `benchmarks/mission-vs-goal/report.md:3-15` states that the 20 paired benchmark runs are not complete and that current evidence supports package readiness, not workflow-performance claims.
- `benchmarks/mission-vs-goal/report.md:23-29` records 10 fixed pilot tasks, 2 benchmark arms, 20 expected paired runs, and 0 completed paired benchmark runs.
- `README.md:10-16` describes `mission` as an OSS loop-engineering plugin that plans, executes, reviews, scores, and iterates until a threshold is reached.
- `README.md:93-95` frames `mission` as useful when the main risk is stopping too early, especially for unclear multi-step work, quality drift, compaction/resume, or auditability needs.

# Assumptions

- "Available repo evidence" means files present in this local checkout, not external documentation, package registries, GitHub, or prior memory.
- A short internal brief can cite repository paths and line ranges as sufficient evidence for this benchmark artifact.
- Because this is the `goal_only` arm, no `.mission-state/` updates are needed.
- The brief should avoid benchmark-superiority language because the repository report says paired outcome runs have not yet been completed.
