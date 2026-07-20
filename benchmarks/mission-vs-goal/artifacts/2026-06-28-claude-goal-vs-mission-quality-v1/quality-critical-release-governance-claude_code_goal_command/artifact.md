# quality-critical-release-governance — claude_code_goal_command

- Task id: `quality-critical-release-governance`
- Task category: operations
- Arm: `claude_code_goal_command` (Claude Code official built-in `/goal` command as completion controller)
- Run set: `2026-06-28-claude-goal-vs-mission-quality-v1`
- Date: 2026-06-28

## Goal

The benchmark artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-06-28-claude-goal-vs-mission-quality-v1/quality-critical-release-governance-claude_code_goal_command.md`
and includes the headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task prompt: *Audit whether a hypothetical benchmark release should proceed when
it includes raw Claude artifacts, marketing claims, generated files, and a remote
publication step. The artifact must decide stop/proceed, separate reversible from
irreversible actions, and reject at least two plausible but wrong interpretations.*

Task validator: *Artifact must include a stop/proceed decision, evidence map, at
least two rejected hypotheses, unsafe claims to avoid, and a rollback or defer
plan for irreversible actions.*

### Scope rules honored in this run

- No commit, push, package install, or network access were performed.
- Exactly one task artifact is written (this file).
- Edits are confined to benchmark output files. No repository source code, no
  tags, no release, and no remote were touched. This audit **decides and plans**;
  it does not execute the release.
- No benchmark-superiority claim is made. This artifact only completes the audit.

## Result

### Stop/Proceed Decision

**Decision: STOP the remote publication step — DO NOT PROCEED to publish.**
Reversible local preparation MAY proceed; every irreversible step is **deferred**
until named preconditions are verified by a human with network access.

Split verdict:

| Release component | Verdict |
|---|---|
| Draft/regenerate benchmark report and artifacts locally | **Proceed (reversible)** |
| Ship raw `claude-result.json` Claude artifacts as-is | **Stop** — sanitize first |
| Publish marketing / superiority claims as conclusions | **Stop** — unsupported, and forbidden by repo rules |
| Push tag + create GitHub Release (remote publication) | **Defer** — preconditions unverifiable in this run (network off) |

The release as described (raw Claude artifacts + marketing claims + generated
files + a remote publication step, bundled together) **must not proceed as one
unit.** Three of its four components fail a gate that can be checked here, and the
fourth (remote publication) cannot be verified here at all. Bundling an
unverifiable irreversible step with three failing reversible ones is itself the
reason to stop.

### Evidence Map

Each governing claim is mapped to a concrete, checkable source in this checkout.
"Verified" = confirmed by a command/file read in this run. "Unmeasured" = could
not be checked under the no-network scope.

| # | Claim driving the decision | Evidence source (this checkout) | Status |
|---|---|---|---|
| E1 | Raw Claude artifacts are present and would be shipped raw | `find benchmarks -name claude-result.json` → **30 files** (e.g. `…/2026-06-28-claude-goal-vs-mission-incremental-v1/complex-review-thread-resolution-claude_code_goal_command/claude-result.json`) | Verified |
| E2 | Those raw artifacts can carry sensitive run internals (token usage, cost, session ids, API error strings) | Prior run state noted in `…/complex-review-thread-resolution-claude_code_goal_command/artifact.md`: a `claude-result.json` showed `is_error: true`, `api_error_status: 400` ("workspace API usage limits") | Verified (the error-bearing field exists in this artifact class) |
| E3 | Marketing / superiority claims exist in source text | `benchmarks/mission-vs-goal/report.md:158` "`mission` is better than official `/goal`." and `:300` "`mission` is cheaper and faster than official `/goal`." | Verified |
| E4 | Those claims are already flagged as **unsupported** in the same report | `report.md:160` "This remains unsupported because the completed comparable sample is one task." and `:303-305` "unsupported because the completed light-profile sample is one task." | Verified |
| E5 | Repo policy forbids benchmark-superiority claims | `CLAUDE.md` (project): "Do not claim benchmark superiority." / task rules restate it | Verified |
| E6 | Remote publication is gated by a hard rule needing GitHub-side verification | `AGENTS.md:22-26` Distribution Release Rule: a version bump "is not a completed distribution release until the matching `vX.Y.Z` git tag exists on the remote and the GitHub Release for that tag exists"; verify with `git ls-remote --tags origin` and `gh release view` | Verified (rule text) |
| E7 | Local tags exist but do **not** prove publication | `git tag` → `v1.0.0 … v1.0.5` present locally | Verified |
| E8 | The configured remote is **not** GitHub in this checkout | `git remote -v` → `origin /Users/<user>/dev/mission` (a local filesystem path, not `github.com/tackeyy/mission`) | Verified |
| E9 | GitHub remote tag + Release state (the actual release precondition) | Would require `git ls-remote --tags origin vX.Y.Z` and `gh release view` against GitHub | **Unmeasured** (network disallowed) |
| E10 | Whether raw artifacts actually contain secrets/PII beyond usage/error fields | Full content scan of all 30 JSON files not performed in this run | **Unmeasured** |

### Rejected Hypotheses (plausible but wrong interpretations)

- **H1 — "Local tags `v1.0.0`–`v1.0.5` exist, so the release is effectively done;
  proceed/announce."** *Rejected.* `AGENTS.md:24` is explicit that a local
  version/tag is **not** a completed distribution release until the matching tag
  exists *on the remote* and the GitHub Release exists. Local tags (E7) prove
  nothing about publication. Treating a local tag as "released" is exactly the
  failure mode the rule exists to prevent.
- **H2 — "`origin` is configured, so `git push origin` will publish the release
  safely."** *Rejected.* `git remote -v` shows `origin` points to a **local
  path** `/Users/<user>/dev/mission` (E8), not GitHub. Pushing there creates **no
  GitHub Release** and would produce a false "published" signal while the actual
  public release state (E9) stays unverified. The publication target itself is
  mis-wired relative to the rule's verification commands.
- **H3 — "This audit task authorizes performing the publication, so I should push
  the tag / create the Release to finish."** *Rejected.* Task scope forbids
  commit, push, and network. The deliverable is a *decision + plan*, not an
  executed release. Executing would also violate the very gate (E6) the audit
  exists to enforce.
- **H4 — "`claude-result.json` files are just generated build output, ship them
  like any other artifact."** *Rejected.* They are raw model-run records that can
  contain usage, cost, session identifiers, and raw API error strings (E2);
  publishing them is an irreversible disclosure, not a benign generated-file copy.
  "Generated" ≠ "safe to publish."

### Irreversible Action Split

**Reversible (safe to do locally now; fully undoable):**

- Draft or regenerate the benchmark report and per-task artifacts under
  `benchmarks/…/run-output/` (editable, deletable).
- Produce **sanitized copies** of the 30 `claude-result.json` files (strip
  usage/cost/session/error internals) for potential publication.
- Create a **local** annotated tag (deletable with `git tag -d`).
- Stage a **draft** GitHub Release locally (not published).
- Rewrite marketing sentences into evidence-bounded statements.

**Irreversible (must be gated; assume "cannot fully un-publish"):**

- `git push` of commits or tags to a **public** remote.
- Creating/publishing a **GitHub Release** for `vX.Y.Z`.
- Publishing the **raw** `claude-result.json` artifacts (E1/E2) — once public they
  may be cached/indexed even if later deleted.
- Publishing **superiority/marketing claims** (E3) as conclusions — reputational
  and policy (E5) damage persists after edits; external caches retain them.
- Deleting or overwriting source run data without a retained backup.

### Unsupported Claims to avoid (do not publish)

- "`mission` is better than official `/goal`." — `report.md:158`, self-flagged
  unsupported at `:160` (one comparable task). Forbidden by `CLAUDE.md` (E5).
- "`mission` is cheaper and faster than official `/goal`." — `report.md:300`,
  self-flagged unsupported at `:303-305` (one light-profile task).
- Any phrasing implying a *winner*, *general speed/cost advantage*, or
  *statistical* result. The defensible sample is **n=1 comparable task**; that
  supports only a narrow, single-task, single-run statement — never a general
  claim. The report's own "Safe interpretation" blocks (`report.md:150-154`,
  `:298-301`) are the correct ceiling for any public wording.

### Residual Risk Register

| ID | Residual risk (after STOP/DEFER) | Likelihood | Impact | Mitigation / owner |
|---|---|---|---|---|
| R1 | Raw artifact leaks usage/cost/session/error internals if published | Med (if shipped raw) | High (irreversible disclosure) | Sanitize + diff before any publish; human reviewer signs off |
| R2 | A reader infers superiority from selective quoting even after claims are softened | Med | Med (policy E5 breach, reputational) | Keep explicit "Unsafe interpretation / unsupported (n=1)" labels adjacent to every number |
| R3 | GitHub tag/Release state never actually verified (E9 unmeasured) → false "released" report | Med | High | Block release report until `git ls-remote --tags origin vX.Y.Z` AND `gh release view` both pass (E6) |
| R4 | Mis-wired `origin` (local path, E8) causes a push that *looks* published but isn't | Med | Med | Re-point/verify remote is `github.com/tackeyy/mission` before any push |
| R5 | Undetected secrets/PII inside the 30 JSON files (E10 unmeasured) | Low-Med | High | Full content scan + secret-scanner before publish; until done, treat as sensitive |

### Rollback or Defer Plan (for irreversible actions)

Because the irreversible steps cannot be cleanly undone, the plan is
**defer-then-gate**, with rollback paths for the partially-reversible cases:

1. **Defer remote publication** until a human with network access verifies the
   E6 preconditions: `git ls-remote --tags origin vX.Y.Z` returns the tag **and**
   `gh release view vX.Y.Z --repo tackeyy/mission` shows the Release. Until both
   pass, do not report the release as complete (closes R3).
2. **Fix the publication target first.** Confirm `origin` resolves to the GitHub
   repo, not the local path in E8, before any push (closes R4).
3. **Sanitize before ship.** Publish only scrubbed copies of the 30 artifacts;
   keep raw originals local. If raw files were already pushed: rotate any exposed
   session/token values, delete the remote copy, and request cache/index removal —
   while recording that full un-publication is **not guaranteed** (R1/R5).
4. **Gate the claims.** Replace E3 sentences with the E4/safe-interpretation
   wording before any public text ships (closes R2).
5. **Rollback handles for the reversible prep:** local tag → `git tag -d vX.Y.Z`;
   draft Release → discard draft; local file edits → `git restore` / delete. These
   are the only release-adjacent actions that can be cleanly reverted.

## Evidence

Concrete, checkable evidence from this checkout (all gathered without network):

- **Raw Claude artifacts (E1):** `find benchmarks -name 'claude-result.json' | wc -l`
  → `30`. These are raw per-run records, the "raw Claude artifacts" named in the
  task.
- **Sensitive-content precedent (E2):** the existing artifact
  `benchmarks/mission-vs-goal/artifacts/2026-06-28-claude-goal-vs-mission-incremental-v1/complex-review-thread-resolution-claude_code_goal_command/artifact.md`
  records a `claude-result.json` with `is_error: true`, `api_error_status: 400`
  ("workspace API usage limits") — evidence that this artifact class can embed raw
  API error/usage detail.
- **Marketing claims (E3) and their self-flagging (E4):** `report.md:158` and
  `:300` contain the superiority sentences; `:160` and `:303-305` already label
  them unsupported (n=1). The report's "Safe interpretation" blocks at
  `:150-154` and `:298-301` show the correct bounded wording.
- **Policy (E5):** project `CLAUDE.md` states "Do not claim benchmark superiority."
- **Remote-publication gate (E6):** `AGENTS.md:22-26` (Distribution Release Rule),
  including the two verification commands.
- **Local tags (E7):** `git tag` → `v1.0.0`…`v1.0.5`.
- **Remote wiring (E8):** `git remote -v` → `origin /Users/<user>/dev/mission`
  (local path, not GitHub).
- **Heading check:** the five required headings (Goal, Result, Evidence,
  Assumptions, Stop Condition) are all present.
- **Validator coverage:** this artifact contains a Stop/Proceed Decision, an
  Evidence Map (E1–E10), four Rejected Hypotheses (H1–H4, ≥2 required), an
  Unsupported Claims section, a Residual Risk Register, and a Rollback/Defer Plan
  for irreversible actions.

### Explicitly unmeasured / not done in this run

- **GitHub-side release state (E9):** the actual precondition for publication —
  remote tag + GitHub Release existence — was **not** verified, because network
  access is out of scope. This is the single most important unknown and is the
  reason the publication step is *deferred* rather than approved.
- **Full secret/PII scan of the 30 JSON files (E10):** not performed; only the
  presence of usage/error fields is established. The files are therefore treated
  as *potentially* sensitive, not *confirmed* clean or *confirmed* leaking.
- No source files were edited, no tests/CI were run, no tags or releases were
  created or pushed.
- No claim about which arm (mission vs official `/goal`) is superior is made or
  implied; this audit is arm-neutral.

## Assumptions

- The "hypothetical benchmark release" is modeled on the *actual* contents of this
  checkout (the `benchmarks/mission-vs-goal/` tree, its raw artifacts, its report,
  and the `AGENTS.md` release rule), so the audit's facts are real even though the
  "release" event is hypothetical.
- "Remote publication step" is interpreted as pushing a `vX.Y.Z` tag and creating
  the GitHub Release per `AGENTS.md:22-26` — the repo's own definition of a
  completed distribution release — not merely pushing to the (locally-wired)
  `origin`.
- "Raw Claude artifacts" = the `claude-result.json` files (and any embedded
  transcript/usage/error content), distinct from curated `artifact.md` outputs.
- The no-network, output-files-only scope is binding, so this artifact records a
  decision and plan rather than executing any release action.
- Line numbers cited for `report.md` and `AGENTS.md` reflect the files as read in
  this run and could shift if the files change.

## Stop Condition

Stop when **all** of the following hold:

1. The artifact exists at the exact required path. — **Met.**
2. It contains the headings Goal, Result, Evidence, Assumptions, Stop Condition.
   — **Met.**
3. A clear stop/proceed decision is recorded. — **Met** (STOP publication / proceed
   reversible prep / defer irreversible steps).
4. Reversible vs irreversible actions are separated. — **Met** (Irreversible Action
   Split).
5. At least two plausible-but-wrong interpretations are rejected with reasons.
   — **Met** (H1–H4).
6. An evidence map, unsupported-claims list, residual-risk register, and a
   rollback/defer plan for irreversible actions are all present. — **Met.**
7. Every claim carries concrete evidence, and anything unverifiable here is labeled
   unmeasured. — **Met** (E9, E10 flagged).
8. Scope honored: no commit/push/install/network, one artifact only, no source
   edits, no superiority claim. — **Met.**

All conditions are satisfied; this run stops here.
