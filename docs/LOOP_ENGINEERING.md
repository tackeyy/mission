# Loop Engineering Launch Positioning

This document turns the current loop-engineering trend into concrete launch
positioning for `mission`.

## One-Line Position

`mission` is the missing quality gate for loop engineering.

## Core Message

Prompt engineering tells an agent what to do. Loop engineering defines how the
agent keeps working until the job is actually done.

`mission` adds the completion layer: plan, execute, review, score, persist state,
and continue until the quality gate passes.

## Audience

| Audience | Pain | Message |
|---|---|---|
| Claude Code / Codex power users | Agents stop too early or report success without enough evidence | Use a stateful quality gate instead of trusting a single completion message. |
| Engineers adopting agent loops | Prompt-only workflows do not survive multi-step work | Turn your repeated workflow into a scored loop. |
| PMs and operators delegating substantial work to AI | Hard to know whether the agent really finished | Require review, scoring, and auditable state before stopping. |
| OSS tool explorers | Many agent tools overlap and are hard to compare | `mission` is narrow: it owns the mission loop and completion gate. |

## Comparison

| Tool / approach | Best for | Limitation | `mission` angle |
|---|---|---|---|
| Manual prompting | One-off requests | Human has to keep steering | Replace repeated nudges with a loop. |
| Claude Code `/goal` | Session-scoped run-until condition | Lightweight evaluator, no full mission state machine | Use when you need persistent state, review, score history, and threshold gates. |
| `ralph-loop` | Replaying a prompt until completion | Less structure around planning, peer review, and scoring | Add plan/review/score phases around the loop. |
| Superpowers | Broad development methodology | Bigger surface than a completion loop | Use `mission` when the narrow problem is "do not stop early." |
| Review / CI plugins | Specialist checks | They do not own the top-level stop/continue decision | Use them as evidence providers inside a mission. |

## GitHub Topics

Recommended topics:

```text
ai-agents
agentic-workflow
claude-code
codex
loop-engineering
quality-gate
skills
plugins
react-loop
oss
```

If updating repository metadata with GitHub CLI:

```bash
gh repo edit tackeyy/mission \
  --add-topic ai-agents \
  --add-topic agentic-workflow \
  --add-topic claude-code \
  --add-topic codex \
  --add-topic loop-engineering \
  --add-topic quality-gate \
  --add-topic skills \
  --add-topic plugins \
  --add-topic react-loop \
  --add-topic oss
```

## Launch CTA

Use one primary call to action for the first week:

> Star `mission` if you are building or using loop-engineered agent workflows and
> want a quality gate before the agent says it is done.

Secondary CTA:

> Follow for practical notes on loop engineering with Claude Code, Codex, skills,
> plugins, sub-agents, and quality gates.

## Content Pillars

| Pillar | Example |
|---|---|
| Concept | Prompt engineering is instructions; loop engineering is the operating system around the instructions. |
| Failure | The agent said "done" before tests, docs, or review passed. |
| Demo | Show `/mission` moving from review feedback to score to another iteration. |
| Comparison | `/goal` vs `ralph-loop` vs Superpowers vs `mission`. |
| Dogfood | Use `mission` to improve `mission`, then publish the trace and score. |

