# セキュリティレビュー観点

**日本語** | [English](SECURITY_REVIEW_CHECKLIST.md)

distribution release 前と定期棚卸しで使う、再現可能なレビュー観点。

このチェックリストが必要な理由は、`mission` が **エージェントの実行トランスクリプト**
（`benchmarks/**/artifacts`、`reports/`、`docs/audit-*.md`）を public repository に
コミットするためである。トランスクリプトはエージェントが読んだものをそのまま含む。
絶対パス、無関係なプライベート案件、最悪の場合は credential まで入る。ここが本 repo で
最もリスクの高い面である。

全項目を実施し、発見と処置をレビュー完了 PR に記録する。

## 実施タイミング

| トリガー | 範囲 |
|---|---|
| distribution release (`vX.Y.Z`) 前 | A〜E |
| 四半期ごと | 全項目 |
| 新しい benchmark cohort・audit 文書の追加後 | C・D・E |

---

## A. リポジトリ設定と公開範囲

- [ ] visibility が意図どおり。collaborator が想定アカウントのみ。
- [ ] secret scanning **と** push protection が有効。
- [ ] **private vulnerability reporting が有効。** `SECURITY.md` が報告者をここへ誘導
      しているため、無効だと非公開の通報経路が実質存在しないことになる。
- [ ] default branch の branch protection: force push / 削除の禁止、admin へも適用、
      required status check あり。
- [ ] 使っていない面（Wiki / Discussions / Projects）は無効化する。未レビューの
      コンテンツが溜まる場所を残さない。

```bash
gh api repos/:owner/:repo --jq '{visibility,hasWikiEnabled,security_and_analysis}'
gh api repos/:owner/:repo/private-vulnerability-reporting
gh api repos/:owner/:repo/branches/main/protection --jq \
  '{force:.allow_force_pushes.enabled,del:.allow_deletions.enabled,admins:.enforce_admins.enabled}'
```

## B. 作業ツリーと git 履歴の秘匿情報

- [ ] `gitleaks` が作業ツリー・全履歴の両方でゼロ件。
- [ ] gitleaks が拾わない provider prefix を別途確認する（Anthropic / OpenAI /
      OpenRouter / xAI / Slack / GitHub / Google / Notion / Resend / Supabase /
      AWS / PEM 秘密鍵 / JWT）。
- [ ] テスト fixture は予約済み・到達不能な値のみを使う。ホスト・メールは
      `example.invalid` / `example.test`、IP は `192.0.2.0/24`（TEST-NET-1）、
      秘密値は用途が名前から分かる placeholder にする。
- [ ] `.gitignore` がローカル state を除外し続けている（`.env*`、`.mission-state/`、
      `.venv-ci/`、`.worktrees/`、`.bench-archive/`）。

```bash
gitleaks git . --no-banner --redact
gitleaks dir . --no-banner --redact
```

**`HEAD` だけでなく履歴を見る。** 後続コミットで sanitize しても、元の blob は残り、
公開リモートから取得できる状態が続く。

```bash
# 現在のツリーではなく、全 ref から到達可能な全 blob を列挙する
git rev-list --objects --all | awk '{print $1}' | git cat-file --batch | grep -aE '<pattern>'
```

## C. GitHub 側（git 外）の秘匿情報

リポジトリは公開面の半分でしかない。Issue / PR の本文・コメント、そして
**その編集履歴** は別に保存され、別に公開されている。

- [ ] Issue / PR 本文を location とする secret scanning alert が存在しない
      （open / resolved を問わず確認する）。
- [ ] resolved の alert について、失効を **Provider 側で実地確認** している。
      GitHub の resolution ラベルは所有者の自己申告であり、validity check を
      有効にしていない限り GitHub は裏取りしていない。
- [ ] 現在の本文だけでなく編集履歴も走査する。本文を編集しても旧リビジョンは
      消えず、API から読める。

```bash
gh api repos/:owner/:repo/secret-scanning/alerts --paginate \
  --jq '.[] | {n:.number, type:.secret_type_display_name, state, resolution, validity}'

gh api graphql -f query='{repository(owner:"OWNER",name:"REPO"){
  issue(number:N){ userContentEdits(first:20){ nodes{ editedAt diff } } } }}'
```

Issue / PR に credential を投稿してしまった場合、**本文の修正では不十分**。
Issue を delete するか GitHub Support に編集履歴の purge を依頼し、
いずれにせよ credential は rotate する。

## D. ローカル環境・第三者コンテキストの漏えい

エージェントのトランスクリプトは、実行者のマシンと無関係な作業内容を漏らす。
現在のツリーと履歴の両方を確認する。

- [ ] 実在アカウント名を含む絶対 home パスがない。匿名化済みの placeholder
      (`<user>` / `runner` 等) は許可される。
- [ ] エージェントの session 識別子・プライベート memory パスがない
      （`.codex/memories`、`.codex/sessions`、`rollout-*`、`.claude/projects/`）。
- [ ] **他の** repository / 事業に属する名称・Issue 番号・機能説明がない。
      公開例では中立な placeholder を使う。
- [ ] git が構造上記録する commit metadata を超えて、個人・業務のメールアドレスが
      本文に含まれていない。
- [ ] redaction を **コミット前** に適用している。後続コミットでの sanitize は、
      元の blob を永久に公開したままにする。

パス redaction は `skills/mission/lib/provider_public_contract.py` の
`redact_local_locators()` が担う。`test_artifact_hygiene.py` は tracked ファイル
全体に対して、実在アカウント名を含む home path がないこと、個人 memory store の
出力が artifact に固定されていないことを強制する。

これらのガードが**カバーしない**範囲に注意する。他リポジトリの名称は対象外である。
`test_vendor_fingerprint.py` はベンダー用語を対象としており、その
`_ALLOWED_COMPOUND_HASHES` は「禁止ベンダー語を部分に含むだけの自プロジェクト名」を
意図的に通す。したがってプライベート repository 名は検出されず、レビューで拾う必要が
ある。

## E. リポジトリにコミットする生成物

- [ ] 新しい benchmark cohort は、コミット前に C・D を通過している。
- [ ] audit / report 文書は挙動を記述しており、無関係なプライベート案件の
      固有情報を書いていない。
- [ ] 機械生成の state ディレクトリが untracked のままである。

## F. コード実行面

- [ ] 配布コードに `shell=True` / `os.system` / `os.popen` / `eval()` / `exec()` /
      `pickle.load` / 安全でない loader の `yaml.load()` / `__import__()` がない。
- [ ] すべての `subprocess` 呼び出しが list 形式 argv で、呼び出し元由来の文字列を
      連結していない。
- [ ] shell script は `set -euo pipefail` を設定し、JSON は文字列処理ではなく `jq` で
      パースし、CI の `shellcheck` を通る。
- [ ] パス処理が symlink を解決し、書き込みを project root 内に閉じ込めている。
- [ ] specialist registry が shell command / URL / 任意 module・file 参照 / secret /
      絶対パスを拒否し、verifier を検証できない場合は fail-closed で停止する。
- [ ] provider 出力の redaction が `key=value` と `Bearer <token>` 形式だけでなく、
      裸のトークン形式もカバーしている。

```bash
git grep -nE "shell=True|os\.system|os\.popen|pickle\.load|yaml\.load\(|__import__\(" -- '*.py' ':!*/tests/*'
git grep -nE "(^|[^a-zA-Z_.])(eval|exec)\(" -- '*.py' ':!*/tests/*'
```

## G. CI・サプライチェーン

- [ ] workflow の `permissions` が最小権限で、top level に宣言されている。
- [ ] PR のコードを checkout する workflow で `pull_request_target` を使っていない。
- [ ] fork のコードを実行する workflow に repository の Actions secrets が
      渡っていない。
- [ ] 第三者 action を可変な tag ではなく commit SHA で pin している。
- [ ] Python 等の依存を厳密なバージョンで pin している。
- [ ] Dependabot の version updates **と** security updates が両方有効。
- [ ] 集約 gate job が fail-closed。上流 job の skip / cancel を success として
      通さない。

## H. エージェント固有リスクの開示

`mission` はホストツールの既定よりも高い自律性をエージェントに与える。採用者が
ソースを読まずに README からこれを把握できる必要がある。

- [ ] 不可逆操作の方針が文書化されている。特に、ユーザー依頼が「リリースして」
      「本番へデプロイして」等で対象操作を明示している場合、それを事前承認として
      扱い実行直前の確認を省略する点を含める。
- [ ] Stop hook の挙動が文書化されている。ミッションが active な間はターンの終了を
      block する = gate 到達 / halt / orphan 検出まで課金が続く、という意味を書く。
- [ ] 外部 specialist が evidence provider に留まり最終判定者にならない。委譲した
      出力だけでスコアゲートを満たせない設計である。
- [ ] 自律実行してはならない承認フラグが、そう明記されている。

## I. ライセンス・出自

- [ ] `LICENSE`・各言語版・`plugin.json` の license 表記が一致している。
- [ ] 帰属表示のない第三者コードの vendoring がない。
- [ ] ベンダー固有の専有用語がない。CI の fingerprint ガードが現行の禁止語を
      カバーしている（プライベート repository 名を含む）。
