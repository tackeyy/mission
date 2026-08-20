# mission へのコントリビューション

**日本語** | [English](CONTRIBUTING.md)

`mission` へのコントリビューションに関心を持っていただきありがとうございます。

このリポジトリには、Claude Code / Codex プラグイン、複数の skill ドキュメント、Python 製の state 管理 CLI、shell 製の Stop hook、関連ドキュメントが含まれます。変更時は ReAct ループと scoring gate の挙動を壊さないことを重視してください。

## 貢献できること

コード、ドキュメント、テスト、Issue 起票、アイデア、レビュー、フィードバックを
`mission` への貢献として扱います。

- 再現手順付きの bug report
- インストール手順や使い方のドキュメント改善
- `mission-state.py` や Stop hook のテスト追加・改善
- macOS / Linux での可搬性改善
- orchestration protocol の改善提案

## 開発環境

必要なもの:

- Python 3.9 以上
- `pytest`
- Stop hook 挙動確認用の `jq`
- shell lint 用の `shellcheck`
- Git

リポジトリを clone します。

```bash
git clone https://github.com/tackeyy/mission.git
cd mission
```

必要に応じてテストツールを入れます。

```bash
python3 -m pip install pytest
```

## テスト実行

Python テスト:

```bash
cd skills/mission
python3 -m pytest -q
```

shell lint:

```bash
shellcheck scripts/mission-stop-guard.sh
```

詳細は [docs/TESTING.ja.md](docs/TESTING.ja.md) を参照してください。

## コーディング指針

Python:

- migration 方針が明確でない限り、state file schema の後方互換性を保つ
- 文字列処理より structured JSON 操作を優先する
- `mark-passes` の threshold gate を維持する
- scoring、session routing、lifecycle 変更にはテストを追加する

Shell:

- 変数は quote する
- Stop hook の依存は小さく保つ
- optional command がない環境でも graceful degrade する
- Stop hook に長時間処理を入れない

Skills / docs:

- `skills/mission/SKILL.md` を orchestration behavior の source of truth とする
- 詳細運用は必要に応じて `skills/mission/refs/` に分離する
- 例には具体的な path と command を書く
- 公開ドキュメントに個人環境の絶対パスを入れない

## Commit message

可能な範囲で conventional commit prefix を使ってください。

- `feat:` 新機能
- `fix:` bug fix
- `docs:` ドキュメント変更
- `test:` テスト変更
- `refactor:` 内部整理
- `chore:` メンテナンス

## Pull Request チェックリスト

Pull Request 前に確認してください。

- `skills/mission` で `python3 -m pytest -q` を実行した
- hook を変更した場合は `shellcheck scripts/mission-stop-guard.sh` を実行した
- ユーザー向け挙動の変更に README または refs の更新がある
- 挙動変更にテストを追加または更新した
- orchestration rule の変更理由を PR description に明記した
- PR description へ貼るエージェント出力を redaction した (下記参照)

## エージェント出力の redaction

本リポジトリの Issue・Pull Request・コミットされる成果物には、**エージェントの実行ログ**が
頻繁に含まれます。実行ログはエージェントが読んだものをそのまま含みます。絶対パス、環境情報、
無関係な非公開作業、場合によっては credential まで入ります。貼る前に消してください。あとから
ではありません。

transcript・ログ・コマンド出力を Issue / Pull Request / コミット対象のファイルへ貼る前に、
次を除去してください。

- あらゆる credential。トークンに見えるだけの値も含む
- 実在アカウント名を含む絶対 home パス (`/Users/<user>/…`)
- エージェントの session 識別子・プライベート memory store のパス
- 他リポジトリに属する名称・Issue 番号・機能説明

**あとから編集しても露出は取り消せません。** GitHub は Issue / Pull Request 本文の編集前
リビジョンを保持し、public repository では GitHub アカウントを持つ者なら誰でも API から
読めます。一度投稿した secret を
消す手段は、Issue / Pull Request 自体を削除するか、GitHub Support に編集履歴の purge を
依頼するかの 2 つだけです。いずれにせよ credential の rotate は必要です。

だから redaction は投稿の**前**でなければなりません。push protection が守るのは git 側だけで、
Issue に何を入力するかは見えません。

消しすぎないでください。匿名化済みの placeholder (`<user>` / `[REDACTED]`) や CI runner の
パス (`/home/runner/work/…`) は構造上公開情報であり、誰かのマシンについて何も明かしません。
これらを消すと再現可能な証跡を失うだけで、得るものがありません。

コミットされるファイルは CI ガードが検査します。

- `skills/mission/tests/test_artifact_hygiene.py`
- `skills/mission/tests/test_vendor_fingerprint.py`
- `skills/mission/tests/test_private_project_names.py`

Issue / Pull Request 本文は対象外であり、原理的に対象にできません。この経路は規律でしか
守れません。各ガードの射程を含む監査手順の全体は
[docs/SECURITY_REVIEW_CHECKLIST.ja.md](docs/SECURITY_REVIEW_CHECKLIST.ja.md) を参照してください。

## Security

脆弱性は public issue で報告しないでください。[SECURITY.ja.md](SECURITY.ja.md) に従ってください。
