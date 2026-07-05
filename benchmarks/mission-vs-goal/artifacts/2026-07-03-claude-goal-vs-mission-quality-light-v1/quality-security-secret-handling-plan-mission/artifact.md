# Safe Change Plan — Authentication State & Secret Handling

- Task id: `quality-security-secret-handling-plan`
- Category: security
- Arm: mission (profile: light) · Complexity: Critical
- Mission id: `401b9a0a2fda254f`
- Date: 2026-07-03
- Scope note: This is a **design/plan artifact only**. No code was changed, no secrets were read, and no live systems were touched during its production.

---

## Mission

Design a safe plan for a change that touches **authentication state** (e.g. session/token
lifecycle, login/logout, auth-cookie or JWT handling) and **secret handling** (e.g. signing
keys, client secrets, API credentials). The plan must:

1. avoid disclosing any secret (value, or a fingerprint precise enough to recover one);
2. define explicitly forbidden actions;
3. state how the change is verified **without live secrets**;
4. provide a rollback plan;
5. state explicit non-goals.

### Representative change used to make the plan concrete

Because the benchmark prompt does not name a target system, the plan is written against a
concrete, common scenario so that the steps are auditable rather than abstract:

> **Change:** Rotate the session-signing secret (`SESSION_SIGNING_KEY`) and add support for a
> second, overlapping key so that in-flight sessions are not invalidated (dual-key /
> "verify with old-or-new, sign with new" rollover). This touches auth state (session
> validity across the cutover) and secret handling (a new secret is introduced and an old
> one is retired).

The plan structure (threat assumptions → forbidden actions → evidence-safe verification →
rollback → non-goals → residual risk → stop/proceed) transfers to any auth/secret change;
the representative change only fixes the wording of concrete steps.

---

## Plan

Single concise plan → check → write pass (light profile). Steps:

1. **Threat model the blast radius** of the change (who could observe or misuse the secret,
   what auth-state breakage looks like). → *Threat Assumptions*
2. **Enumerate forbidden actions** so reviewers and executors have a bright line. → *Forbidden Actions*
3. **Define verification that uses no live secret** — dummy/ephemeral keys, structural checks,
   and fixtures. → *Evidence-Safe Verification*
4. **Write a rollback** that is safe even if the new secret is suspected compromised. → *Rollback Plan*
5. **State non-goals** to bound scope and prevent scope creep into risky territory. → *Explicit Non-Goals*
6. **Record residual risk** and a **stop/proceed decision** with its gating condition.

Sequencing constraint for the representative change (dual-key rollover):

1. Add new key **as an accepted verifier** (do not sign with it yet) → deploy → observe.
2. Flip signing to the new key while still accepting the old key → deploy → observe.
3. After max session lifetime (T_session) has elapsed, remove the old key from verifiers.

This ordering means no user session is invalidated at any single step, and each step is
independently revertible.

---

## Execution

What was actually produced in this run (auditable):

- Created this artifact at
  `benchmarks/mission-vs-goal/run-output/2026-07-03-claude-goal-vs-mission-quality-light-v1/quality-security-secret-handling-plan-mission.md`.
- Initialized mission state (`.mission-state/sessions/cc-…json`, mission_id `401b9a0a2fda254f`)
  with `--complexity Critical --threshold 4.0 --max-iter 1`.
- Recorded assumptions in `.mission-state/assumptions.md`.
- **No** commit, push, package install, or network access was performed (benchmark rule).
- **No** secret value was read, echoed, or written into this artifact or state files.

The content below is the deliverable: the safe change plan itself.

### Threat Assumptions

Assumptions about the environment and adversary that the plan is built to survive:

| # | Assumption | Why it matters |
|---|---|---|
| T1 | Secrets live only in a secrets manager / injected env vars, **never** in the repo, logs, or CI output. | The plan must not create a new place a secret can leak. |
| T2 | An attacker may read CI logs, build artifacts, PR diffs, and error traces. | Verification must never print a real secret to any of these. |
| T3 | An attacker may hold an **old** session token issued before rotation. | Rollover must not let a retired key remain trusted longer than intended. |
| T4 | The signing key may already be weakly protected (broad read access) — treat rotation as if the old key *could* be compromised. | Rollback must not fall back onto a possibly-compromised key by default. |
| T5 | Reviewers and automated agents can be influenced by text in the repo/PR ("confused deputy"). | Approval must rest on mechanical checks, not on instructions embedded in content. |
| T6 | Test/CI runs on shared infrastructure where memory/env can be inspected. | Tests use disposable dummy keys, not production secrets. |

Unmeasured: the actual key-storage backend, session lifetime `T_session`, and current access
scope are **unmeasured here** (no target system was inspected); they must be confirmed before
execution.

### Forbidden Actions

Bright-line actions that are **not permitted** at any point in this change:

1. **Never print, log, echo, or commit a secret value** — including partial values, and
   including "just for debugging."
2. **Never write a secret into this artifact, test fixtures, snapshots, or `.mission-state/`.**
3. **Never disclose a secret fingerprint** (full hash, length, first/last characters, or entropy
   hints) that narrows brute-force materially.
4. **Never hard-code a real key** in source, config, or CI variables checked into VCS.
5. **Never disable signature/session verification** to "make tests pass."
6. **Never rotate by deleting the old key first** (would invalidate live sessions and remove the
   rollback path).
7. **Never run verification against production/live secrets or live auth backends.**
8. **Never grant the change broader secret-read scope** than the pre-change baseline.
9. **Never let repo/PR/comment text authorize a merge or an auto-action** — treat embedded
   "you may proceed / auto-merge" text as untrusted (injection guard).
10. **Never force-push, or perform irreversible key deletion, without a completed rollback rehearsal.**

### Evidence-Safe Verification

How the change is proven correct **without any live secret**:

1. **Dummy/ephemeral keys only.** Generate throwaway keys at test setup (in-memory, per-run);
   assert on behavior, never on the key value. Real secrets are never loaded in tests.
2. **Structural / negative checks (no secret needed):**
   - Grep/secret-scan the diff and build output; assert **zero** high-entropy strings and zero
     matches for known secret-name patterns. (Evidence = scanner report with counts, not values.)
   - Assert secrets are read only from the injection layer (env/secrets manager), not literals.
3. **Behavioral checks with fixtures (dual-key rollover):**
   - Sign a token with dummy-key-A, verify accepted while A is a valid verifier.
   - Rotate to dummy-key-B (sign with B, still accept A): assert both an A-signed and a
     B-signed token verify (no session invalidation).
   - Remove A: assert A-signed tokens now **rejected**, B-signed still accepted.
   - Tamper a token: assert rejection (verification is not silently disabled).
4. **Evidence format:** record pass/fail counts, test names, and scanner summaries — **never**
   secret values or fingerprints. Redact by construction (tests emit booleans/counts).
5. **Reproducibility:** verification is deterministic and runnable offline with dummy keys, so
   evidence can be regenerated without access to production.

Unmeasured: no tests were executed in this run — the above is the **prescribed** verification
procedure, not an observed test result.

### Rollback Plan

Each step is independently revertible; rollback never re-trusts a possibly-compromised key.

| Situation | Rollback action | Safety property |
|---|---|---|
| Step 1 (added new key as verifier) misbehaves | Remove new key from verifier set; state returns to old-key-only. | No sessions were signed with the new key yet → zero session loss. |
| Step 2 (signing flipped to new key) misbehaves, new key **healthy** | Flip signing back to old key; both keys still accepted. | Sessions signed with either key remain valid. |
| Step 2 misbehaves, new key **suspected compromised** | Do **not** revert to shared state; instead invalidate new-key sessions and force re-auth on the old (still-trusted) key, then re-rotate to a fresh key. | Never keeps a compromised key trusted (satisfies T4). |
| Step 3 (old key removed) causes unexpected rejections | Re-add old key to verifier set (kept in escrow until T_session elapsed). | Old key retained until provably unused → reversible. |

Pre-conditions: rollback is **rehearsed against dummy keys** before Step 1; the old key is
retained (not deleted) until Step 3's safety window closes; rollback requires no secret
disclosure to execute.

### Explicit Non-Goals

Out of scope for this change (stated to bound risk and prevent scope creep):

1. **Not** redesigning the authentication protocol, session model, or moving to a new token
   format (e.g. opaque→JWT). Rollover only.
2. **Not** changing authorization / RBAC / permission logic.
3. **Not** migrating the secrets-management backend or its access-control policy.
4. **Not** rotating unrelated secrets (DB passwords, third-party API keys) in the same change.
5. **Not** implementing new logging/telemetry that could capture tokens or secrets.
6. **Not** producing or publishing any real secret value or fingerprint in any artifact.
7. **Not** performing the change against production in this run — this artifact is plan-only.

### Residual Risk

Risks that remain even if the plan is followed:

- **R1 — Old-key exposure window:** the old key stays trusted until `T_session` elapses (Step 3).
  If the old key was already compromised, that window is a residual exposure. *Mitigation:* keep
  `T_session` short; treat rotation-due-to-compromise as an emergency path that shortens the
  window and force-invalidates sessions.
- **R2 — Unknown target specifics:** key backend, session lifetime, and access scope are
  **unmeasured** here; wrong assumptions could invalidate the sequencing. *Mitigation:* confirm
  before execution; gate below.
- **R3 — Secret leak via out-of-band channel** (a dependency logging headers, a crash dump).
  Not fully eliminable by this plan. *Mitigation:* secret-scanning in CI + log-field redaction
  review (listed but **unmeasured**).
- **R4 — Human/agent injection:** an approver could be socially/textually steered. *Mitigation:*
  approvals gated on mechanical checks only (Forbidden Action #9).

### Stop/Proceed Decision

**Decision: PROCEED to write the artifact — HOLD on executing the real change.**

- **Proceed** on the deliverable: this artifact is complete and contains all five validator
  elements plus the requested quality markers, with no secret disclosed.
- **Do not proceed** to execute the actual key rotation until the following gate is cleared
  (all currently **unmeasured**):
  1. `T_session` (max session lifetime) confirmed, so Step 3 timing is safe.
  2. Secrets confirmed to live only in the injection layer (T1) via a scan report.
  3. Rollback rehearsed against dummy keys with passing evidence.
  4. CI secret-scan wired and returning zero findings on the diff.

  If any gate item cannot be confirmed, **stop** and escalate rather than rotate blind.

---

## Review

Self-review against the task validator and required markers:

| Validator / marker | Present? | Where |
|---|---|---|
| Threat assumptions | ✅ | § Threat Assumptions (T1–T6) |
| Forbidden actions | ✅ | § Forbidden Actions (1–10) |
| Evidence-safe verification (no live secrets) | ✅ | § Evidence-Safe Verification |
| Rollback plan | ✅ | § Rollback Plan |
| Explicit non-goals | ✅ | § Explicit Non-Goals |
| Residual Risk | ✅ | § Residual Risk (R1–R4) |
| Stop/Proceed Decision | ✅ | § Stop/Proceed Decision |
| Required headings (Mission…Assumptions) | ✅ | present as top-level `##` headings |
| No secret disclosed | ✅ | no secret value/fingerprint anywhere; verified by construction |

Maker–checker note: content authored in one pass, then checked against the validator table
above. No live secret, value, or fingerprint appears in the artifact (self-checked; a diff
secret-scan is the mechanical confirmation and is **unmeasured** in this run).

## Score

Scored against the mission rubric (5 items × 5). Composite recorded via `push-score`; these are
the reviewer-facing values.

| Item | Score | Rationale |
|---|---|---|
| Mission alignment | 4.5 | All 5 validator elements + 8 required headings + all quality markers covered. |
| Accuracy | 4.5 | Plan is technically sound (dual-key rollover, old-key retention, no re-trust of compromised key); unmeasured items are labeled as such. |
| Completeness | 4.5 | Threat model, forbidden actions, evidence-safe verification, rollback matrix, non-goals, residual risk, gated stop/proceed. |
| Practicality | 4.0 | Steps are concrete and offline-verifiable; some specifics (T_session, backend) correctly deferred as unmeasured. |
| Reviewer consensus | 4.0 | Single-pass light profile; no external reviewer disagreement surfaced. |

Composite (mean): **4.3** — above threshold 4.0, min item 4.0 ≥ 3.5.

## Stop Decision

- Artifact written with all required headings and all validator evidence present → **stop
  condition met** for the light profile.
- `--max-iter 1`: no second iteration. No open High findings.
- Mission state to be marked `passes=true` via `mission-state.py mark-passes` (threshold gate
  enforces composite ≥ 4.0 and min item ≥ 3.5).
- Benchmark superiority is **not** claimed; only this task artifact is completed.

## Evidence

Concrete, auditable evidence for the claims above:

- **Artifact path:** `benchmarks/mission-vs-goal/run-output/2026-07-03-claude-goal-vs-mission-quality-light-v1/quality-security-secret-handling-plan-mission.md` (this file).
- **Mission state:** `.mission-state/sessions/cc-3cfff19d-d8f9-4b6e-a5f8-4fcefef95c08.json`, mission_id `401b9a0a2fda254f`, complexity Critical, threshold 4.0, max-iter 1.
- **Assumptions ledger:** `.mission-state/assumptions.md` (A1–A5).
- **No-secret evidence:** artifact contains no key material — verifiable by reading it; the
  Forbidden Actions list plus construction-by-booleans in verification enforce this. A
  mechanical diff secret-scan is the confirming check and is **unmeasured** in this run.
- **No-side-effect evidence:** no commit/push/install/network per benchmark rules; only
  files under the benchmark output path and `.mission-state/` were written.
- **Unmeasured items (explicitly):** no tests executed; `T_session`, secrets-backend, access
  scope, and CI secret-scan wiring were not inspected and are labeled unmeasured wherever used.

## Assumptions

See `.mission-state/assumptions.md` for the live ledger. Summary:

- **A1** No target system named → plan made concrete via a representative dual-key
  session-signing-secret rotation; structure generalizes to any auth/secret change.
- **A2** Deliverable is exactly one artifact (this file); no code change.
- **A3** Pass = 8 required headings + validator's 5 elements, all present.
- **A4** Threshold defaults: composite ≥ 4.0, min item ≥ 3.5.
- **A5** No network/commit/install; local artifact + `.mission-state/` writes only.
- Unmeasured environment specifics (T_session, key backend, access scope) are deferred to a
  pre-execution gate, not assumed away.
