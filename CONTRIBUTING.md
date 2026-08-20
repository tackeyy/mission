# Contributing to mission

[Japanese](CONTRIBUTING.ja.md) | **English**

Thank you for your interest in contributing to `mission`.

This repository contains a Claude Code / Codex plugin, several skill documents,
a Python state-management CLI, a shell Stop hook, and documentation. Changes
should preserve the behavior of the ReAct loop and the scoring gate.

## Ways to Contribute

We recognize code, documentation, tests, issue reports, ideas, reviews, and
feedback as contributions to `mission`.

- Report bugs with reproduction steps
- Improve installation and usage documentation
- Add or improve tests for `mission-state.py` and the Stop hook
- Fix portability issues for macOS and Linux
- Propose changes to the orchestration protocol

## Development Setup

Requirements:

- Python 3.9 or later
- `pytest`
- `jq` for Stop hook behavior
- `shellcheck` for shell linting
- Git

Clone the repository:

```bash
git clone https://github.com/tackeyy/mission.git
cd mission
```

## Running Tests

Run the deterministic repository-managed test tiers:

```bash
make test-smoke
make test
make test-e2e
```

`make test` creates `.venv-ci`, installs the pinned CI requirements, and runs
the same full pytest command as GitHub Actions. Every tier prints the exact Git
tree SHA and its test manifest.

Run shell linting:

```bash
shellcheck scripts/mission-stop-guard.sh
```

See [docs/TESTING.md](docs/TESTING.md) for more detail.

## Coding Guidelines

Python:

- Keep the state file schema backwards-compatible unless the migration path is explicit
- Prefer structured JSON operations over string parsing
- Preserve the threshold gate in `mark-passes`
- Add tests for scoring, session routing, and lifecycle changes

Shell:

- Use quoted variables
- Keep the Stop hook dependency surface small
- Preserve graceful degradation when optional commands are unavailable
- Avoid introducing long-running work into the Stop hook

Skills and docs:

- Keep `skills/mission/SKILL.md` as the source of orchestration behavior
- Keep operational details in `skills/mission/refs/` when the main skill would become too large
- Use concrete paths and commands in examples
- Avoid personal machine paths in public documentation

## Commit Messages

Use conventional commit prefixes where practical:

- `feat:` for new behavior
- `fix:` for bug fixes
- `docs:` for documentation changes
- `test:` for tests
- `refactor:` for internal cleanup
- `chore:` for maintenance

## Pull Request Checklist

Before opening a pull request:

- Run `make test`
- Run `shellcheck scripts/mission-stop-guard.sh` if the hook changed
- Update README or reference docs for user-visible behavior
- Add or update tests for behavior changes
- Explain any orchestration-rule changes clearly in the PR description
- Redact agent output before pasting it into the PR description (see below)

## Redacting Agent Output

Issues, pull requests, and committed artifacts in this repository frequently
carry **agent execution logs**. Those logs contain whatever the agent happened to
read: absolute paths, environment details, unrelated private work, and sometimes
credentials. Redact before you paste, not after.

Before pasting any transcript, log, or command output into an issue, a pull
request, or a file you intend to commit, remove:

- credentials of any kind, including values that merely look like a token
- absolute home paths carrying a real account name (`/Users/<user>/…`)
- agent session identifiers and private memory store paths
- names, issue numbers, and feature descriptions belonging to other repositories

**Editing afterwards does not undo the exposure.** GitHub keeps the pre-edit
revision of every issue and pull request body, and on a public repository anyone
can read it through the API. Once a secret is posted, the only ways to remove it
are deleting the issue or pull request outright, or asking GitHub Support to
purge the edit history — and the credential must be rotated regardless.

This is why redaction has to happen *before* posting. Push protection guards the
git side only; it cannot see what you type into an issue.

Committed files are covered by CI guards
(`skills/mission/tests/test_artifact_hygiene.py`,
`test_vendor_fingerprint.py`, `test_private_project_names.py`). Issue and pull
request bodies are not, and cannot be — that path is discipline only.

## Security

Do not report security vulnerabilities through public issues. Follow
[SECURITY.md](SECURITY.md).
