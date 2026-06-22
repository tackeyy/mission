# バージョニング方針

**日本語** | [English](VERSIONING.md)

この repository では、通常の PR merge と配布 release を分離します。

## 定義

- **merge release**: PR を `main` に merge し、CI を待ち、関連 issue を閉じ、branch を cleanup すること。plugin version は変更しません。
- **distribution release**: 新しい plugin version を意図的に公開すること。`vX.Y.Z` tag、GitHub Release、manifest version bump、install path 更新、release changelog entry を含みます。

ユーザーが単に「リリース」と言い、version bump、GitHub Release、marketplace release、tag を明示していない場合は、merge release として扱います。

## 基本ルール

PR を merge するたびに version を上げません。

user-facing な変更は `CHANGELOG.md` と `CHANGELOG.ja.md` の `[Unreleased]` に積みます。distribution release は、plugin user に変更のまとまりを配布すると意図的に判断した時だけ作成します。

通常 cadence は最大でも週 1 回です。ただし hotfix が必要な場合は例外です。

## Hotfix 例外

公開済み version に次の問題がある場合だけ、即時 patch distribution release を作成します。

- install、startup、基本的な `mission-state.py` command が壊れている
- state corruption または pass/fail gate bypass
- security / privacy risk
- marketplace metadata または wrapper sync drift により、install 済み user が壊れた package を受け取る

小さな docs 修正、tests、internal refactor、audit output の polish、non-blocking UX improvement は `[Unreleased]` に蓄積します。

## SemVer の対応

- **MAJOR**: state schema、CLI contract、既存 mission workflow 互換性の破壊的変更。
- **MINOR**: provider protocol、新 CLI command、新 audit/reporting surface などの互換性のある新機能。
- **PATCH**: 互換性のある bug fix、documentation correction、release metadata correction、wrapper/package sync fix。

## Release PR のスコープ

distribution release PR は、基本的に機械的な変更だけにします。

- 両方の changelog で該当する `[Unreleased]` entry を `vX.Y.Z` に移す
- `.claude-plugin/plugin.json`、`.codex-plugin/plugin.json`、`plugins/mission/.codex-plugin/plugin.json` を更新する
- README と Codex setup docs の visible install path を更新する
- `git log <previous-tag>..HEAD --oneline` と両方の changelog を突合する
- marketplace release checklist を実行する

新機能の実装を release PR に混ぜません。
