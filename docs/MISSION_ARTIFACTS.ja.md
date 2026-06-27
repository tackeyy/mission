# Mission Artifacts 設計

状態: 設計契約と実装計画です。この文書は artifact support がすでに実装済みだとは主張しません。

`mission` は、ミッションごとに検査可能な artifact を残すべきです。artifact は、依頼内容、実施内容、
確認した証拠、レビュー結果、score gate、なぜ止まってよいかを説明する durable なローカルファイルです。

これは local-first の設計です。Claude Code Artifacts は優れた参照実装ですが、`mission` は Claude Code、
Codex、shell 利用、offline repository のどれでも動く OSS plugin である必要があります。

## 公式参照

ベストプラクティスの参照元は Claude Code の公式ドキュメントです。

- [Claude Code Artifacts](https://code.claude.com/docs/ja/artifacts)
- [Claude Code `/goal`](https://code.claude.com/docs/ja/goal)

Claude Code Artifacts から取り込む設計原則は以下です。

- artifact 作成は明示的であり、共有可能なページを黙って publish しない。
- hosted page になる前に、HTML または Markdown の具体的なファイルがある。
- publish すると private URL が作られ、共有境界は platform が制御する。
- artifact は hidden agent state ではなく、人間が検査できる成果物である。

`mission` は、この user-facing discipline を取り込みます。ただし Claude Code hosting を必須依存にはしません。

## 目的

- デフォルトで 1 mission につき 1 つの canonical artifact を作る。
- artifact data を `.mission-state` と結び、stop decision と evidence trail が乖離しないようにする。
- まず Markdown をサポートし、その後に必要なら HTML render を足す。
- remote publish は明示 opt-in の adapter に限定する。
- marketing claim が chat-only summary ではなく、ファイルと raw evidence に基づくようにする。

## やらないこと

- Claude Code Artifacts を runtime の必須依存にしない。
- 明示許可なしに remote publish しない。
- artifact を `.mission-state` の代替にしない。artifact は state と evidence の human-readable surface です。
- artifact があるだけで benchmark 優位性を主張しない。

## Artifact Contract

最初の実装では、local artifact を以下に作ります。

```text
.mission-state/artifacts/<session_id>/mission-artifact.md
```

state file には以下を記録します。

```json
{
  "artifact": {
    "status": "draft",
    "format": "markdown",
    "path": ".mission-state/artifacts/<session_id>/mission-artifact.md",
    "exports": [],
    "publish_events": [],
    "redaction_status": "unchecked"
  }
}
```

artifact には以下の section を含めます。

| Section | 目的 |
|---|---|
| Mission | ユーザー依頼、scope、constraints、session id |
| Plan | 現在の plan と重要な plan 変更 |
| Execution | 変更ファイル、実行コマンド、触った外部システム |
| Evidence | テスト結果、benchmark record、source link、raw artifact path |
| Review | reviewer 指摘と、修正済み/受容/未対応の状態 |
| Score Gate | score items、threshold、pass/fail、stop rationale |
| Assumptions | 推論、未検証、blocked、time-sensitive な claim |
| Follow-ups | 完了ミッションの外に意図的に残した作業 |

## CLI 計画

`skills/mission/bin/mission-state.py` に artifact subcommands を追加します。

```text
mission-state.py artifact init --format markdown --title "..."
mission-state.py artifact append --section evidence --file path/or/stdin
mission-state.py artifact render
mission-state.py artifact export --to docs/marketing/<slug>.md
mission-state.py artifact publish --provider claude-code --require-confirm
```

ルール:

- `artifact init` は local file を作り、active session state に artifact block を記録する。
- `artifact append` は既知の section 名だけを受け付ける。
- `artifact render` は state と追記された evidence block から canonical Markdown を再生成する。
- `artifact export` はレビュー済み artifact をユーザー指定の durable path にコピーする。
- `artifact publish` は任意であり、必ず明示的なユーザー確認を要求する。

## Stop Gate 連携

artifact support はまず opt-in とし、その後 durable result が期待される mission type で必須にします。

推奨フェーズ:

| Phase | 変更 | 検証 |
|---|---|---|
| 0 | contract と plan を文書化する | doc consistency tests |
| 1 | local artifact state schema と CLI commands を追加する | init/append/render/export の unit tests |
| 2 | orchestrator skill が通常実行中に artifact を作成・更新する | temporary mission state を使う integration tests |
| 3 | artifact-required mission の stop-gate check を追加する | artifact 不足時に `mark-passes` が拒否することを確認 |
| 4 | optional publisher adapters を追加する | publish が opt-in で、consent を記録することを確認 |
| 5 | artifact-required mission run で `/goal` vs `mission` benchmark を再実行する | raw paired records と artifact paths |

## Security And Privacy

artifact は state file より共有されやすいため、より厳しい扱いが必要です。

- local artifact は default private。
- publish には必ず明示的なユーザー許可を要求する。
- export / publish 前に redaction status を記録する。
- secret、token、private customer name、local home-directory path は、ユーザーが明示的に含めるよう指示しない限り redaction 対象にする。
- remote adapter は provider、timestamp、destination、approval evidence を `publish_events` に記録する。

## Benchmark Implication

marketing-safe な比較では、artifact support により問いが
「どちらが done と言ったか」から「どちらの workflow が再利用可能で監査可能な結果を残したか」に変わります。

Phase 5 の paired run が完了するまでは、外向きに言える claim は次に限定します。

> `mission` は completion evidence を監査可能にするための local-first artifact contract を計画済みです。
> 現時点の `/goal` 比較結果は、この artifact behavior をまだ測定していません。
