# Security Review Checklist

[Japanese](SECURITY_REVIEW_CHECKLIST.ja.md) | **English**

A repeatable checklist for auditing this repository before a distribution
release, and periodically thereafter. It exists because `mission` commits
**agent execution transcripts** (`benchmarks/**/artifacts`, `reports/`,
`docs/audit-*.md`) into a public repository. Those transcripts are the highest
risk surface here: they capture whatever the agent happened to read, including
absolute paths, unrelated private projects, and — in the worst case — secrets.

Run the whole list. Record findings and their disposition in the PR that closes
the review.

## Cadence

| Trigger | Scope |
|---|---|
| Before a distribution release (`vX.Y.Z`) | Sections A–E |
| Quarterly | All sections |
| After adding any new benchmark cohort or audit doc | Sections C, D, E |

---

## A. Repository settings and exposure

- [ ] Visibility is intentional; collaborator list contains only expected accounts.
- [ ] Secret scanning **and** push protection are enabled.
- [ ] **Private vulnerability reporting is enabled.** `SECURITY.md` points reporters
      at it, so if it is off there is no working private disclosure channel.
- [ ] Branch protection on the default branch: force pushes and deletions blocked,
      admin enforcement on, required status check present.
- [ ] Unused surfaces are disabled (Wiki, Discussions, Projects) so they cannot
      accumulate unreviewed content. Use the REST field names (`has_wiki`,
      `has_projects`, `has_discussions`) — the GraphQL spellings return `null`
      through `gh api repos/...` and silently answer "not enabled" for
      everything.

```bash
gh api repos/:owner/:repo --jq \
  '{visibility,has_wiki,has_discussions,has_projects,security_and_analysis}'
gh api repos/:owner/:repo/private-vulnerability-reporting
gh api repos/:owner/:repo/branches/main/protection --jq \
  '{force:.allow_force_pushes.enabled,del:.allow_deletions.enabled,admins:.enforce_admins.enabled}'
```

## B. Secrets in the working tree and in git history

- [ ] `gitleaks` reports zero findings for both the working tree and full history.
      Zero has to mean zero: this repository keeps credential-shaped fixtures in
      the redaction tests, and those are excluded **per line** with
      `gitleaks:allow` rather than by allowlisting the file, so a real secret
      landing in the same file is still reported. `.gitleaks.toml` excludes only
      untracked build directories. If you find yourself explaining away a
      finding, silence it at the line or fix it — do not carry it forward.
- [ ] Provider-prefix patterns not covered by gitleaks are checked separately
      (Anthropic, OpenAI, OpenRouter, xAI, Slack, GitHub, Google, Notion, Resend,
      Supabase, AWS, PEM private keys, JWT).
- [ ] Test fixtures use reserved, non-routable values only:
      `example.invalid` / `example.test` for hosts and email, `192.0.2.0/24`
      (TEST-NET-1) for addresses, and self-describing placeholder names for secrets.
- [ ] `.gitignore` still excludes local state (`.env*`, `.mission-state/`,
      `.venv-ci/`, `.worktrees/`, `.bench-archive/`).

```bash
gitleaks git . --no-banner --redact
gitleaks dir . --no-banner --redact
```

Scan history rather than only `HEAD`: a file sanitized in a later commit still
exposes its original blob, which stays fetchable from the public remote.

```bash
# Enumerate every blob reachable from every ref, not just the current tree.
git rev-list --objects --all | awk '{print $1}' | git cat-file --batch | grep -aE '<pattern>'
```

## C. Secrets and private content on GitHub itself (not in git)

The repository is only half the surface. Issue and PR bodies, comments, and
**their edit histories** are separately stored and separately public.

- [ ] No open or resolved secret scanning alert whose location is an issue or PR body.
- [ ] For any alert marked resolved, revocation is **verified at the provider**,
      not inferred from the GitHub resolution label. GitHub does not validate the
      claim unless validity checks are enabled.
- [ ] Edit histories are scanned, not just current bodies. Editing a body does not
      remove the prior revision; it remains readable through the API.

```bash
gh api repos/:owner/:repo/secret-scanning/alerts --paginate \
  --jq '.[] | {n:.number, type:.secret_type_display_name, state, resolution, validity}'

gh api graphql -f query='{repository(owner:"OWNER",name:"REPO"){
  issue(number:N){ userContentEdits(first:20){ nodes{ editedAt diff } } } }}'
```

If a secret was ever posted in an issue or PR: **redacting the body is not
sufficient.** Delete the issue, or ask GitHub Support to purge the edit history,
and rotate the credential regardless.

## D. Local environment and third-party context leakage

Agent transcripts leak the operator's machine and unrelated work. Check both the
current tree and history.

- [ ] No absolute home paths carrying a real account name. Anonymised
      placeholders (`<user>`, `runner`) are allowed.
- [ ] No agent session identifiers or private memory paths
      (`.codex/memories`, `.codex/sessions`, `rollout-*`, `.claude/projects/`).
- [ ] No names, issue numbers, or feature descriptions belonging to **other**
      repositories or businesses. Public examples use neutral placeholders.
- [ ] No personal or work email addresses beyond the commit metadata that git
      necessarily records.
- [ ] Redaction is applied **before** the artifact is committed. Sanitizing in a
      follow-up commit leaves the original blob public forever.

`redact_local_locators()` in `skills/mission/lib/provider_public_contract.py`
performs the path redaction.

Know exactly how far each automated check reaches, because the two checks in
`test_artifact_hygiene.py` do **not** have the same scope:

- the home-path check reads every tracked file;
- the personal-memory-dump check reads `.jsonl` files only, and matches a fixed
  set of markers. A memory dump pasted into a `.md` file passes.

The other items in this section — session identifiers, `rollout-*` paths, third
party issue numbers, email addresses — have no automated check at all. Read the
diff.

Three guards cover three distinct axes, and each is easy to mistake for the
others. Confirm all three still run, and that a new generated-content directory
is inside their scope:

| Guard | Axis |
|---|---|
| `test_artifact_hygiene.py` | home paths carrying a real account name; personal memory store output |
| `test_vendor_fingerprint.py` | vendor terminology |
| `test_private_project_names.py` | names of other (private) repositories |

All three read `git ls-files`, so they assert only that **`HEAD` is clean**. They
say nothing about blobs still reachable in history — see section B.

## E. Generated artifacts committed to the repository

- [ ] Every new benchmark cohort passes sections C and D before it is committed.
- [ ] Audit and report documents describe behavior, not the identity of unrelated
      private projects.
- [ ] Machine-generated state directories remain untracked.

## F. Code execution surface

- [ ] No `shell=True`, `os.system`, `os.popen`, `eval()`, `exec()`, `pickle.load`,
      `yaml.load()` (unsafe loader), or `__import__()` in shipped code.
- [ ] Every `subprocess` call uses list-form argv with no string interpolation of
      caller-controlled data.
- [ ] Shell scripts set `set -euo pipefail`, parse JSON with `jq` rather than
      string manipulation, and pass `shellcheck` in CI.
- [ ] Path handling resolves symlinks and confines writes to the project root.
- [ ] The specialist registry rejects shell commands, URLs, arbitrary module or
      file references, secrets, and absolute paths, and fails closed when a
      verifier cannot be validated.
- [ ] Provider output redaction covers bare token formats, not only
      `key=value` and `Bearer <token>` shapes.

```bash
git grep -nE "shell=True|os\.system|os\.popen|pickle\.load|yaml\.load\(|__import__\(" -- '*.py' ':!*/tests/*'
git grep -nE "(^|[^a-zA-Z_.])(eval|exec)\(" -- '*.py' ':!*/tests/*'
```

## G. CI and supply chain

- [ ] Workflow `permissions` are least-privilege and declared at the top level.
- [ ] `pull_request_target` is not used for workflows that check out PR code.
- [ ] No repository Actions secrets are exposed to workflows that run fork code.
- [ ] Third-party actions are pinned to a commit SHA, not a mutable tag.
- [ ] Python and other dependencies are pinned to exact versions. Pinning a
      version is not integrity: a version can be re-published. For dependencies
      that reach production, verify hashes (`pip install --require-hashes`) and
      run a vulnerability check (`pip-audit`).
- [ ] Dependabot version updates **and** security updates are enabled.
- [ ] The aggregate gate job is fail-closed: a skipped or cancelled upstream job
      fails the gate rather than passing it.

## H. Agent-specific risk disclosure

`mission` grants an agent more autonomy than the host tool's defaults. Adopters
must be able to learn this from the README without reading the source.

- [ ] The irreversible-operation policy is documented, including that an explicit
      user request ("release it", "deploy to production") is treated as advance
      approval for that operation and suppresses the pre-execution confirmation.
- [ ] The Stop hook's behavior is documented: it blocks turn completion while a
      mission is active, which means continued token spend until a gate, halt, or
      orphan detection fires.
- [ ] External specialists remain evidence providers, never final judges, so that
      scoring gates cannot be satisfied by delegated output alone.
- [ ] Approval flags that must never be set autonomously are documented as such.

## I. Licensing and provenance

- [ ] `LICENSE`, localized license text, and `plugin.json` agree.
- [ ] No vendored third-party code without attribution.
- [ ] No vendor-specific proprietary terminology; the CI fingerprint guard covers
      the current forbidden list, including private repository names.
