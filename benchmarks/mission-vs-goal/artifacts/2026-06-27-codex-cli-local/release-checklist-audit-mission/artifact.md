# Mission

Audit the repository's release checklist against repository rules and existing release documentation, then propose minimal documentation fixes. No release action, commit, push, package install, or network access was performed.

# Plan

1. Use the mission state workflow required by the benchmark.
2. Inspect repository rules that define release completion.
3. Inspect the release checklist and adjacent release policy docs.
4. Identify only minimal doc fixes, with each fix mapped to a rule or checklist gap.
5. Self-review the artifact against the validator and record a passing mission score.

# Execution

I audited these local sources:

- `AGENTS.md`
- `docs/VERSIONING.md`
- `docs/DISTRIBUTION.md`
- `docs/DISTRIBUTION.ja.md`
- `docs/MARKETPLACE_RELEASE_CHECKLIST.md`
- `docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md`
- `README.md`
- `skills/mission/refs/codex-setup.md`

The main marketplace checklist is mostly aligned with the repository's distribution release rule. It already requires creating and pushing the `vX.Y.Z` tag, creating or updating the GitHub Release, and re-verifying both before reporting release completion.

The smaller `docs/DISTRIBUTION.md` / `docs/DISTRIBUTION.ja.md` release checklist is weaker. It points to the versioning policy and says intentional distribution releases update manifests, tags, and GitHub Releases, but it does not explicitly carry through the stricter completion gate from `AGENTS.md` or the detailed marketplace checklist.

Proposed minimal doc fixes:

1. In `docs/DISTRIBUTION.md` under `## Release Checklist`, add one bullet after the first bullet:

   `For distribution releases, do not report completion until the matching remote tag and GitHub Release are both verified with the commands in MARKETPLACE_RELEASE_CHECKLIST.md.`

   Mapping: `AGENTS.md` requires remote tag plus GitHub Release verification before reporting completion; `docs/MARKETPLACE_RELEASE_CHECKLIST.md` already contains the exact verification commands. The shorter distribution checklist currently mentions tags and GitHub Releases but not the report-completion verification gate.

2. In `docs/DISTRIBUTION.ja.md` under `## Release checklist`, add the Japanese equivalent after the first bullet:

   `distribution release は、MARKETPLACE_RELEASE_CHECKLIST.ja.md のコマンドで remote tag と GitHub Release の両方を再照合するまで完了報告しない。`

   Mapping: English and Japanese docs are expected to stay updated together for user-facing install or release steps. The Japanese marketplace checklist already has the stricter gate, while the Japanese distribution checklist has the same shorter wording gap as English.

3. In `docs/DISTRIBUTION.md` and `docs/DISTRIBUTION.ja.md`, update the Codex validation bullet from "symlink install and plugin add" to refer directly to the detailed marketplace checklist for exact Codex install/cache checks.

   Minimal wording:
   `Validate Codex using the install and cache checks in MARKETPLACE_RELEASE_CHECKLIST.md, including wrapper sync when skills or scripts change.`

   Mapping: `docs/MARKETPLACE_RELEASE_CHECKLIST.md` lists wrapper sync, isolated `CODEX_HOME`, plugin marketplace add, plugin add, installed cache contents, cache exclusions, and root environment documentation. `docs/DISTRIBUTION.md` only names symlink install and plugin commands, so it is a checklist gap rather than a conflicting rule.

4. In `docs/MARKETPLACE_RELEASE_CHECKLIST.md` and `.ja.md`, clarify the version-surface bullet by naming both `README.md` and `README.ja.md` instead of the broader "README install paths".

   Minimal wording:
   `README.md and README.ja.md install paths`

   Mapping: `docs/VERSIONING.md` requires visible install paths in README and Codex setup docs to be updated; the marketplace checklist also requires English and Japanese docs to move together. Naming both README files makes the checklist mechanically harder to under-apply without changing release policy.

# Review

Validator: Every proposed fix maps to a specific repository rule or existing checklist gap.

Result: Pass.

- Fix 1 maps to `AGENTS.md` distribution release completion rules and the existing detailed command gate in `docs/MARKETPLACE_RELEASE_CHECKLIST.md`.
- Fix 2 maps to the same release completion rule plus the existing bilingual update expectation.
- Fix 3 maps to the checklist gap between the detailed Codex section in `docs/MARKETPLACE_RELEASE_CHECKLIST.md` and the shorter Codex validation bullet in `docs/DISTRIBUTION.md`.
- Fix 4 maps to the versioning policy's visible install-path update requirement and the checklist's bilingual-doc consistency requirement.

No proposed fix adds new release policy. Each is a doc alignment or specificity change.

# Score

Composite: 4.6 / 5.0

Items:

- mission_achievement: 4.7
- accuracy: 4.6
- completeness: 4.5
- usability: 4.6

Rationale: The artifact identifies the relevant rules, distinguishes aligned checklist items from gaps, and proposes minimal doc-only fixes with explicit mappings. `reviewer_consensus` is omitted because this Simple mission uses a single self-review path, so cross-reviewer consensus is not meaningful. The score is below 5.0 because no actual doc patch was requested or applied in this benchmark artifact.

# Stop Decision

Stop after one iteration. The artifact satisfies the requested headings and the validator, and the benchmark explicitly asks only to complete this task artifact.

# Evidence

- `AGENTS.md:22-26` defines the distribution release completion rule: remote `vX.Y.Z` tag, GitHub Release, and verification commands before reporting completion.
- `docs/MARKETPLACE_RELEASE_CHECKLIST.md:17-22` already requires synchronized version bumps, changelog entries, tag creation/push, GitHub Release creation/update, and remote verification.
- `docs/MARKETPLACE_RELEASE_CHECKLIST.md:39-46` lists detailed Codex wrapper, install, cache, and root-env checks.
- `docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md:15-20` contains the same version bump, tag, GitHub Release, and verification gate in Japanese.
- `docs/DISTRIBUTION.md:54-67` contains a shorter release checklist that lacks an explicit "do not report complete until verified" bullet and points to less detailed Codex validation.
- `docs/DISTRIBUTION.ja.md:39-46` has the same shorter Japanese release checklist gap.
- `docs/VERSIONING.md:51-60` defines distribution release PR scope, including changelog moves, manifest updates, README/Codex setup install path updates, git-log reconciliation, and running the marketplace checklist.
- `README.md:122-125` identifies `.codex-plugin/`, `.agents/plugins/`, and `plugins/mission/` as distinct Codex metadata/wrapper surfaces.
- `skills/mission/refs/codex-setup.md:44-65` explains Codex plugin metadata, wrapper sync, marketplace install, and installed cache root setup.

# Assumptions

- "Release checklist" includes both the detailed marketplace checklist and the shorter release checklist embedded in distribution strategy docs.
- Because the benchmark asks to propose minimal doc fixes, I did not edit the release documentation itself.
- Network-dependent verification commands were audited as checklist requirements only; they were not executed.
- The repository is in a detached HEAD benchmark checkout, so I did not attempt branch updates, commits, or pushes.
