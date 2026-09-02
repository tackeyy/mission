# Agent Guardrails

This repository is OSS. Keep public behavior portable across users, machines, and agent runtimes.

## Personal Skill Boundary

- Do not add maintainer-local or organization-specific skills to OSS defaults.
- Do not hard-code personal skill names, private workflow taxonomies, home-directory paths, or local command providers into core behavior.
- Use project/user registries and skill manifests for private integrations:
  - `.mission/specialists.yml` for project-local policy
  - `~/.config/mission/specialists.yml` for user-local defaults
  - installed `mission-specialist.yml` files for skill-provided metadata
- Tests for extension behavior must use neutral fixture names, not a maintainer's private skill set.
- Documentation may mention private skills only as clearly labeled examples, never as required or bundled OSS capabilities.
- Generated artifacts are covered by the same rule. Benchmark outputs, audit reports, and captured execution logs must not carry a real home path (`/Users/<user>/` is the anonymized form) or the contents of a personal memory store. Committing a raw agent transcript publishes whatever that agent happened to read.
- This is enforced by `skills/mission/tests/test_artifact_hygiene.py`, which scans every tracked file on each PR.

## Neutral Vocabulary

- Describe the design with general concept vocabulary: ontology, object, property, link, action, function, lineage, provenance, grounding, branch, scenario, finding, score, decision, audit.
- Do not introduce a specific vendor's product names or coined terms into code, comments, documentation, file names, commit messages, or issue/PR text. Name the general design pattern instead (DDD, CQRS, capability-based security, hexagonal architecture, event sourcing, data lineage, FSM, data quality expectations).
- This is enforced automatically by `skills/mission/tests/test_vendor_fingerprint.py`, which scans every tracked file on each PR. The blocked terms are stored as hashes rather than plain text, because the list itself would disclose what it withholds. That test's docstring explains how to add a term.
- One-time cleanup is not enough: derived artifacts such as audit logs, benchmark outputs, and captured execution logs have re-introduced these terms after a manual purge. The automated scan is the control.

## Specialist Policy

- OSS code may define generic provider protocols, registry schemas, ranking logic, audit output, and safety gates.
- External specialists are evidence providers only. `mission` owns state, scoring, pass/fail gates, and final reporting.
- Broad orchestrator skills must be bounded to a single evidence artifact such as a plan, review, or synthesis note. Do not nest a second autonomous completion loop inside `/mission`.

## PR Size Calibration

The shared PR-size rule ships pre-calibration defaults of 400 and 1,000 lines and
asks each repository to replace them with its own p65 and p85. Uncalibrated,
**54 of the last 100 merged PRs exceed 400 and 22 exceed 1,000** — a rule that
fires on a fifth of all work stops being read.

**Measure reviewed area, not raw diff.** `plugins/mission/skills/` and
`plugins/mission/scripts/` are byte-identical copies of `skills/` and
`scripts/`, enforced by `test_plugins_in_sync.py` and
`test_codex_wrapper_sync.py` — **except under `__pycache__` and
`.pytest_cache`, which those tests skip.** Paths under those directories are
counted as reviewed area, since nothing holds them identical to a source.

**The rest of `plugins/mission/` is not a copy.** Its `CHANGELOG.md`,
`CHANGELOG.ja.md`, and `.codex-plugin/plugin.json` carry their own content and
are reviewed like anything else. Excluding the whole directory would have
quietly exempted them.

### How the distribution was measured

`main` is squash-merged, so each first-parent commit is one merged PR. The
calibration walks the last 100 of them and diffs each against its parent:

```bash
git log --first-parent --no-merges -n 100 --format=%H origin/main
git diff --numstat <sha>^ <sha>
```

| | p50 | p65 | p75 | p85 | p90 |
|---|---|---|---|---|---|
| Reviewed lines | 417 | **608** | 789 | **1,349** | 2,690 |

**The GitHub API was not used for this.** `gh pr view --json files` requests
`files(first: 100)` with no way to page, so any PR with more than 100 changed
files comes back truncated — and the set contains one (#605, 194 files). An
earlier measurement through that path reported p65 580.3, which was wrong for
that reason. `scripts/pr_size.py --pr` now refuses to report a number when the
list reaches that limit.

The earlier 60-PR sample in #719 gave much higher values (p65 1,119 / p85 3,844);
widening the sample and excluding the mirror both moved the distribution down,
so **the 60-PR figures are not used.**

### Thresholds

| Threshold | Lines | Requirement |
|---|---|---|
| Accountability (p65) | **600** | State in the PR body why it is not split. Does not block |
| Split required (p85) | **1,400** | Split, or record an exception and the reason it applies |

**The thresholds are judgment, not a formula.** They are round numbers near the
measured p65 and p85, chosen so the values stay readable. At them, 37 of the
last 100 merged PRs need an explanation and 14 need splitting or an exception.

### Generated-artifact allowlist

Excluded from reviewed area. The first two entries name paths **a test holds
identical to their source**; the rest are files produced by a tool from inputs
already under review:

- `plugins/mission/skills/**` — held byte-identical by `test_plugins_in_sync.py`
- `plugins/mission/scripts/**` — held byte-identical by `test_codex_wrapper_sync.py`

Both sync tests skip `__pycache__` and `.pytest_cache`, so paths under those are
not excluded here.
- `benchmarks/*/artifacts/**` — recorded benchmark output
- `*.lock`
- `package-lock.json`
- `*.snap`

**Changing this list is a security concern**, not housekeeping: adding a path is
how a threshold gets evaded. `scripts/pr_size.py` holds the same list and the
tests fail if the two disagree. Matching treats `/` as a real separator, so
`benchmarks/*/artifacts/**` does not also cover `benchmarks/a/b/artifacts/`.

### Large PRs here are already split

The four largest merged PRs are the residue of splitting, not unsplit work:
#555 landed as three domain batches, #654 as "PR2/2", #645 as a second stage,
#660 as one numbered sub-item. Kernel migrations have an irreducible unit — a
command family moves together or the intermediate state breaks. **The thresholds
exist to catch work that was never divided, not to re-divide what already was.**

**This is evidence of splitting, not proof that further splitting is
impossible.** Titles and bodies show the series; whether each residue could be
cut again was not independently verified.

### How to check

```bash
python3 scripts/pr_size.py --pr <number>      # against GitHub
python3 scripts/pr_size.py --base origin/main # against a local range
```

**This check is not enforced by CI.** It is self-reported (自己申告): run the
script and put the number in the PR body when you land in the accountability
band or above. Wiring it into CI is not implemented.

## Distribution Release Rule

- A version bump is not a completed distribution release until the matching `vX.Y.Z` git tag exists on the remote and the GitHub Release for that tag exists.
- Before reporting a distribution release as complete, verify both with `git ls-remote --tags origin vX.Y.Z` and `gh release view vX.Y.Z --repo tackeyy/mission`.
- If manifests, README install paths, or changelogs are updated to a new version, the same task must carry through tag push and GitHub Release creation/update, unless the user explicitly asks to stop before publication.
