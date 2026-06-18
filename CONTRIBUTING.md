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

Install test tools if needed:

```bash
python3 -m pip install pytest
```

## Running Tests

Run the Python test suite:

```bash
cd skills/mission
python3 -m pytest -q
```

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

- Run `python3 -m pytest -q` from `skills/mission`
- Run `shellcheck scripts/mission-stop-guard.sh` if the hook changed
- Update README or reference docs for user-visible behavior
- Add or update tests for behavior changes
- Explain any orchestration-rule changes clearly in the PR description

## Security

Do not report security vulnerabilities through public issues. Follow
[SECURITY.md](SECURITY.md).
