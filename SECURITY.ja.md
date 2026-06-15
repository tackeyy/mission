# Security Policy

**日本語** | [English](SECURITY.md)

## サポート対象

security fix は default branch の latest version を対象にします。

| Version | Supported |
|---|---|
| latest | Yes |

## 脆弱性の報告

security vulnerability は public GitHub issue で報告しないでください。

このリポジトリで GitHub private vulnerability reporting が有効な場合は、それを使ってください。無効な場合は、repository owner account の GitHub profile から maintainer に連絡し、exploit details を公開しないでください。

報告には以下を含めてください。

- 影響を受ける file または command
- 再現手順
- 期待される挙動と実際の挙動
- impact assessment
- 分かる場合は mitigation proposal

## 対応プロセス

maintainer が報告を triage し、必要に応じて修正を準備します。解決後に security advisory または release note を公開します。

## 対象領域

security-sensitive な領域:

- Stop hook の command execution
- `.mission-state` file の read/write
- session ID sanitization
- plugin-local file の path handling
- scoring threshold gate を bypass できる可能性のある挙動
