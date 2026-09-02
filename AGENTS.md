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
**51 of the last 100 merged PRs exceed 400 and 22 exceed 1,000** — a rule that
fires on a fifth of all work stops being read.

**Measure reviewed area, not raw diff.** `plugins/mission/skills/` and
`plugins/mission/scripts/` are byte-identical copies of `skills/` and
`scripts/`, enforced by `test_plugins_in_sync.py` and
`test_codex_wrapper_sync.py`. A reviewer reads that content once. Counting it
twice inflates every PR that touches the skill: **19% of the diff at the median
and 40% at p85.**

**The rest of `plugins/mission/` is not a copy.** Its `CHANGELOG.md`,
`CHANGELOG.ja.md`, and `.codex-plugin/plugin.json` carry their own content and
are reviewed like anything else. Excluding the whole directory would have
quietly exempted them.

### Thresholds

| Threshold | Lines | Requirement |
|---|---|---|
| Accountability (p65) | **600** | State in the PR body why it is not split. Does not block |
| Split required (p85) | **1,400** | Split, or record an exception and the reason it applies |

At these values, 35 of the last 100 merged PRs need an explanation and 14 need
splitting or an exception.

**Two measurements support these numbers, and they do not agree exactly.**

| Method | p65 | p85 |
|---|---|---|
| GitHub API, last 100 merged PRs (`gh pr view --json files`) | 580.3 | 1349.0 |
| Local first-parent walk, 100 merges (#546–#737, `git diff`) | 607.4 | 1349.0 |

They measure different things: the API reports each PR's `base...head` diff,
while the first-parent walk reports what each merge commit brought in. **p85
agrees; p65 differs by about 5%.**

**The thresholds are the two p65/p85 estimates rounded to the nearest hundred**
(580.3 and 607.4 → 600; 1349.0 → 1400 for both). 600 therefore sits just below
the local p65 and just above the API one. **Rounding to a shared, readable
number was chosen over tracking a figure that moves with the measurement
method** — the 27-line gap between the two estimates is smaller than the noise
from adding or removing a single PR from the sample.

The earlier 60-PR sample in #719 gave much higher values (p65 1,119 / p85 3,844);
widening the sample and excluding the mirror both moved the distribution down,
so **the 60-PR figures were not stable and are not used.**

### Generated-artifact allowlist

Excluded from reviewed area. The first two entries name paths **a test holds
identical to their source**; the rest are files produced by a tool from inputs
already under review:

- `plugins/mission/skills/**` — held byte-identical by `test_plugins_in_sync.py`
- `plugins/mission/scripts/**` — held byte-identical by `test_codex_wrapper_sync.py`
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
