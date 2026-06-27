# Goal

Audit the marketplace release checklist against repository release rules and propose minimal documentation fixes. Stop when each proposed fix maps to either a specific repository rule or an existing checklist gap.

# Result

The checklist already covers the core distribution-release guard: version bumps are not complete until the remote `vX.Y.Z` tag and GitHub Release both exist and are re-verified.

Proposed minimal doc fixes:

1. Clarify release ordering in `docs/MARKETPLACE_RELEASE_CHECKLIST.md` and `docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md`.
   - Current gap: the checklist asks users to create and push the `vX.Y.Z` tag in the shared section, but the final gate later says to commit and push only after checks pass. This can be read as tagging before the exact release commit has been created or merged.
   - Minimal fix: change the tag item to say the tag is created and pushed after the release PR/checklist changes are committed, pushed, and merged, and only for the exact release commit.
   - Rule mapping: `AGENTS.md` requires the matching remote tag and GitHub Release before a distribution release is complete; `docs/VERSIONING.md` defines a distribution release PR as the mechanical step that updates changelogs, manifests, install paths, and then runs the checklist.

2. Make the version-path checklist item name the exact files guarded by the repository tests.
   - Current gap: the checklist says to update README install paths and Codex setup docs, but the enforced file set is more specific.
   - Minimal fix: expand the item to name `README.md`, `README.ja.md`, `skills/mission/refs/codex-setup.md`, and `plugins/mission/skills/mission/refs/codex-setup.md`.
   - Rule mapping: `docs/VERSIONING.md` requires visible install paths in README and Codex setup docs to be updated; `skills/mission/tests/test_doc_consistency.py` enforces those four paths against the manifest version.

3. Add an explicit incomplete-release escape hatch when a user asks to stop before publication.
   - Current gap: the checklist correctly blocks completion until remote tag and GitHub Release verification pass, but it does not say what to record if the user explicitly stops before publication.
   - Minimal fix: add a final-gate note: if the user explicitly stops before tag push or GitHub Release creation/update, document the release as unpublished/incomplete and do not report it as a completed distribution release.
   - Rule mapping: `AGENTS.md` allows stopping before publication only when the user explicitly asks, while still treating the distribution release as incomplete.

# Evidence

- `AGENTS.md:22-26` defines the Distribution Release Rule: a version bump is incomplete until the matching remote tag and GitHub Release exist, both are verified with `git ls-remote --tags origin vX.Y.Z` and `gh release view vX.Y.Z --repo tackeyy/mission`, and manifest/README/changelog version updates must carry through publication unless the user explicitly stops before publication.
- `CLAUDE.md:3-9` delegates to `AGENTS.md` and repeats that version bumps are incomplete until the matching remote tag and GitHub Release have both been created and verified.
- `docs/VERSIONING.md:51-62` says a distribution release PR should mechanically move changelog entries, update three manifests, update visible README/Codex setup install paths, reconcile `git log <previous-tag>..HEAD --oneline`, run the marketplace checklist, and avoid new feature work.
- `docs/MARKETPLACE_RELEASE_CHECKLIST.md:17-22` already requires synchronized version bumps, changelog entries, git-log-to-changelog reconciliation, tag push, GitHub Release creation/update, and publication re-verification.
- `docs/MARKETPLACE_RELEASE_CHECKLIST.md:48-51` places "commit and push only after all applicable checks pass" after the tag/GitHub Release items, creating the ordering ambiguity described above.
- `docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md:15-20` mirrors the English publication requirements, and `docs/MARKETPLACE_RELEASE_CHECKLIST.ja.md:46-49` mirrors the same final-gate ordering ambiguity.
- `skills/mission/tests/test_doc_consistency.py:183-203` enforces synchronized manifest versions and visible install-path references in `README.md`, `README.ja.md`, `skills/mission/refs/codex-setup.md`, and `plugins/mission/skills/mission/refs/codex-setup.md`.
- `skills/mission/tests/test_doc_consistency.py:215-229` enforces the release-publication verification tokens in both marketplace checklists and `AGENTS.md`.

# Assumptions

- This benchmark asks for proposed fixes only, so no repository docs were modified.
- Network access was intentionally not used; remote tag and GitHub Release existence were not checked.
- The AGENTS.md startup pull rule was not run because this benchmark explicitly forbids network access.
