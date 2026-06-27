# Goal

Add a new concept page and update all relevant README links so readers can find it.

# Result

No source documentation files were changed in this controlled goal-only run. The benchmark instructions for this run required exactly one task artifact and limited edits to benchmark output files, so the completed deliverable is this artifact.

Within those constraints, the smallest useful result is a scoped record of the intended documentation change:

- New concept page target: `docs/CONCEPTS.md`
- English README link location: root `README.md`, in the `Loop Engineering` section near existing links to `docs/LOOP_ENGINEERING.md` and `benchmarks/mission-vs-goal/README.md`
- Japanese README link location: root `README.ja.md`, in the `Loop Engineering` section near existing links to `docs/LOOP_ENGINEERING.md` and `benchmarks/mission-vs-goal/README.ja.md`
- Link text should use the same relative target from both READMEs: `docs/CONCEPTS.md`

# Evidence

- Local git state was inspected with `git status --short --branch`; the checkout is detached at `HEAD` with no reported local changes before this artifact was written.
- Repository files were enumerated with `rg --files`.
- `benchmarks/mission-vs-goal/tasks.json` defines `docs-cross-reference` as: "Add a new concept page and update all relevant README links so readers can find it."
- `benchmarks/mission-vs-goal/tasks.json` lists the validator as: "New page exists, both English and Japanese README links are present, and link targets resolve."
- `README.md` already has a `Loop Engineering` section with nearby documentation links, including `docs/LOOP_ENGINEERING.md` and `benchmarks/mission-vs-goal/README.md`.
- `README.ja.md` already has the matching Japanese `Loop Engineering` section with nearby documentation links, including `docs/LOOP_ENGINEERING.md` and `benchmarks/mission-vs-goal/README.ja.md`.
- Existing `docs/` files were checked with `find docs -maxdepth 2 -type f -print`; no `docs/CONCEPTS.md` file exists in the starting tree.

# Assumptions

- The benchmark run-output rule overrides the underlying documentation-task edits for this controlled run.
- Network access, package installation, commits, and pushes are intentionally out of scope.
- Because source documentation edits were disallowed, the task-specific source validator was not run as a passing check in this arm.
