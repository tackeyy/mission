# Security Policy

[Japanese](SECURITY.ja.md) | **English**

## Supported Versions

Security fixes target the latest version on the default branch.

| Version | Supported |
|---|---|
| latest | Yes |

## Reporting a Vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Use GitHub private vulnerability reporting if it is enabled for this repository.
If it is not enabled, contact the maintainer through the GitHub profile linked
from the repository owner account and avoid publishing exploit details publicly.

Include:

- Affected files or commands
- Steps to reproduce
- Expected and actual behavior
- Impact assessment
- Suggested mitigation, if known

## Response Process

The maintainer will triage the report, prepare a fix when appropriate, and
publish a security advisory or release note after the issue is resolved.

## Scope

Security-sensitive areas include:

- Stop hook command execution
- `.mission-state` file reads and writes
- Session ID sanitization
- Path handling for plugin-local files
- Any behavior that could bypass the scoring threshold gate
