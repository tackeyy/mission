# 変更履歴

**日本語** | [English](CHANGELOG.md)

本プロジェクトの主要な変更を記録します。

フォーマットは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

### 追加
- `ask-user` 後に confirmed selection metadata が残っていない specialist 適用を、unselected invocation とは別の audit finding として報告するようにしました。
- phase duration がある一方で経過時間の大半が planning に粗く帰属している slow session を mission audit が報告するようにしました。
- mission audit の self-improvement prompt に、agent が GitHub Issue を作成する前の重複 issue 確認と development/tech-lead review 証跡の記録を必須化する指示を追加しました。
- `mission-state.py push-score` が `--scoring-output` 未指定時にも generated scoring evidence を保存するようになり、すべての score history entry に監査可能な archive artifact が残るようになりました。
- `mission-state.py specialists log-invocation --selection-source` を追加し、inline / tool invocation evidence の記録時に、明示・手動選択された specialist の selection metadata も同時に残せるようにしました。
- final report 用に selected / used / degraded / unselected-manual を provider の `kind` と registry/source metadata 付きで出力する `mission-state.py specialists summary` を追加しました。
- 通常の merge release と意図的な distribution release を分離する versioning policy を文書化し、PR を merge するたびに plugin version を上げない運用を明確化しました。
- `AGENTS.md`、`CLAUDE.md`、ADR-001 に OSS portability guardrail を追加し、個人/private specialist skill を public default ではなく user / project registry に置く方針を明確化しました。
- 完了前の warning として、terminal decision trail がない available specialist/provider candidate を表示する `mission-state.py specialists accounting --json` を追加しました。
- `mission-state.py` と `scripts/mission-audit.py` で candidate accounting ロジックを共有し、実行中チェックと事後監査で同じルールを使うようにしました。
- 正典の state CLI に委譲する repository root の安定 wrapper `scripts/mission-state.py` を追加しました。
- 長時間 batch 向けに `mission-state.py progress update/get/clear` checkpoint を追加し、進捗証跡を archive に保存して slow-session の audit 行にも表示できるようにしました。
- maintainer-local な skill 名を組み込まず、development / strategy 系 registry の段階的な利用順を示す `specialists_phase_plan` を recommendation に追加しました。
- mission audit が不正な score iteration と空の specialist invocation record を明示的な finding として報告するようにしました。

### 変更
- mission orchestrator の運用指針に、`phase=executing` / `phase=reviewing` の明示更新と長時間作業の progress checkpoint を必須化しました。
- Complex mission の specialist accounting を、リスクを持つ candidate だけに explicit terminal decision を求める形へ調整し、ユーザー plugin をデフォルトでは optional evidence source として扱うハッカブルな拡張性を維持しました。
- database/backend candidate は schema / migration / query / SQL / persistence などの強い database signal がある場合だけ high-risk accounting candidate として扱うようにしました。
- command provider の `result_contract` により、準備完了バナーだけ、または短すぎる出力を `prepared` と分類し、完了済みレビュー証跡として扱わないようにしました。
- `oracle-reviewer` に browser-review の準備完了バナー向け default result contract を適用し、`ask-user` 後の specialist confirmation は `--selection-source confirmed-user` で永続化してから selected evidence として扱うようにしました。
- broad orchestrator specialist は non-execution の evidence use に限定し、plan/review などの適用済み証跡には `--bounded-purpose` を必須にしました。
- Standard / Complex の監査・自己改善 mission では、利用可能な testing / security / risk specialist candidate に explicit accounting を求めるようにしました。

### 修正
- core mission subskill の呼び出しを external specialist の unselected invocation として誤検出しないようにしました。
- marketplace 配布版の `mission-state.py` wrapper から specialist accounting / result-contract marker が欠落しないよう、同期テストで保護しました。
- mission audit の pass rate 計算から active no-score checkpoint を分母除外しつつ、incomplete active session としては引き続き報告するようにしました。
- mission audit が nested `archive/worktree-*/sessions/*.json` copy を resolved archive duplicate として分類するようにし、cross-root audit で live/archive の完全一致 copy が P1 `duplicate-state` と誤報告されないようにしました。
- `mission-state.py mark-passes` が required specialist provider の適用済み結果証跡を確認するようにし、`prepared` / `skipped` / `failed` だけでは strict required-provider gate を満たせないようにしました。
- `mission-state.py push-score` が 1 未満の iteration を拒否するようにし、監査不能な `score_history` entry を防ぐようにしました。
- `mission-state.py specialists log-invocation` が空の `role` / `skill` を保存前に拒否するようにしました。
- `mission-state.py stats` が nested `archive/worktree-*/sessions/*.json` を含めて集計し、audit discovery と session count が揃うようにしました。

## [1.0.4] - 2026-06-22

### 追加
- README で `mission` を loop engineering の品質ゲートとして位置づけ、launch positioning guidance へのリンクを追加しました。
- `mission-state.py stats` が repeated `--root` を受け付け、複数 root を集約し、scan root の一覧を出力し、重複する state identity を二重計上しないようにしました。
- specialist invocation logging が `skill-tool-applied` を受け付け、skipped / unavailable / failed の判断理由を必須化し、高リスク candidate accounting を文書化しました。
- specialist candidate が存在する一方で selection / invocation / skip の decision trail が記録されていない場合、mission audit が `candidate-only-specialists` として可視化するようにしました。
- terminal evidence はあるが Phase 1 selection metadata と対応しない specialist invocation を mission audit が可視化するようにしました。
- mission の最終報告に selected / used / degraded / unselected-manual の短い specialist summary を追加し、`codex-inline` を実 Skill tool 呼び出しと誤表現しない文言を明確化しました。
- specialist registry を project / user / skill/plugin manifest から自動 discovery し、project 側の `enabled: false` で user default を無効化できるようにしました。
- specialist provider schema が `kind: skill` と `kind: command`、first-use risk consent、command provider evidence invocation に対応し、`oracle` など特定 provider を mission core に hard-code せず扱えるようにしました。

## [1.0.3] - 2026-06-20

### 追加
- Phase 1 specialist selection checkpoint rollout 後に開始された session で selection metadata が欠落している場合、mission audit が可視化するようになりました。
- release 完了前に `git log <previous-tag>..HEAD --oneline` と英日 changelog entry を突合する手順を release checklist に追加しました。
- v1.0.2 の release theme が future changelog edit で欠落しないように documentation consistency test を追加しました。

### 修正
- v1.0.2 changelog entry に Phase 1 specialist selection checkpoint、specialist registry、file-overlap warning、audit CLI、GitHub Flow guidance、contributors、Reviewer/Scorer safeguards、audit diagnostics、Codex hook-packaging validation を追記しました。

## [1.0.2] - 2026-06-20

### 追加
- 任意の specialist registry を追加し、mission が task_profile を分類して利用可能な専門 skill を自動選定し、evidence provider として利用し、呼び出し証跡を記録できるようにしました。
- Phase 1 で mission state 初期化後に `specialists recommend --record-state --json` の結果を記録する specialist selection checkpoint を必須化しました。
- `mission-state.py init` に `--files` を追加し、別の active session と対象ファイルが重複する場合に警告できるようにしました。
- read-only な `scripts/mission-audit.py` CLI を追加し、local mission state の監査、self-improvement prompt 生成、forced/ungated pass、duplicate state、halt、slow session、low-score pass の bucket 可視化ができるようにしました。
- mission audit が nested worktree archive session を検出し、missing scoring evidence と specialist invocation gap を可視化するようになりました。
- slow session report に phase duration の観測可否 breakdown を分離して追加しました。
- issue 連携 mission、PR 本文の `Closes #N`、merge による issue 自動クローズを GitHub Flow として明文化しました。
- README に contributors と contribution type の表示を追加しました。

### 修正
- Reviewer / Scorer の安全策を強化し、merge-base 基準の diff 確認とテスト真正性チェックで誤った退行判定や浅いテスト検証を減らしました。
- 同一 logical mission run について、stale halt copy より完了済み pass/done record を優先して dedupe するようにしました。
- audit diagnostics が halt/incomplete の root cause、slow session bucket、low-score pass risk bucket を分類できるようにしました。
- Codex plugin の hook packaging contract が崩れた場合に release validation で検出できるようにしました。

## [1.0.1] - 2026-06-17

### 追加
- **Q11 – stagnation 自動カウント**: `push-score` で composite の改善幅 (`cur − prev`) が `[0, 0.1)` の場合に `stagnation_count` を自動インクリメント。後退（スコア低下）と初回 push は停滞と見なさず 0 にリセット。
- **S3 – 重複 issue-ref 警告**: `init` に `--issue-ref <ref>` オプションを追加。同プロジェクト内の active session に同一 `issue_ref` が存在する場合は stderr に `WARNING [S3]` を表示（reject しない）。同一 `session_id` での resume は自己検出として除外。

### 修正
- **Q11 後退ロジック修正**: 負の delta（スコア後退）が誤って stagnation として計上されていたバグを修正。条件を `0 <= delta < 0.1` に限定し、`_is_valid_composite()` による型チェックも追加。
- コピー配布用の Codex marketplace wrapper（`plugins/mission/`）を正典の `skills/` / `scripts/` と同期し、最新の stale auto-halt、High gate、stats、scoring rubric 修正を含めました。
- Codex wrapper が正典実装から drift した場合に失敗する回帰テストを追加しました。

## [1.0.0] - 2026-06-15

初の公開リリース。

### 追加
- ミッション・オーケストレーター skill と 5 つの補助 skill（planner / executor / reviewer / critic / scorer）。
- `.mission-state` セッション状態 CLI（`mission-state.py`）。Claude Code / Codex のマルチセッション分離に対応。
- スコア履歴とレビュー/critic ループを伴う閾値ゲート付き完了判定。
- ミッション実行中の早期終了を防ぐ Stop hook。stale-state のタイムスタンプ解釈は macOS（BSD `date`）と Linux（GNU `date`）の両対応。
- Claude Code プラグインメタデータとローカルプラグインマーケットプレイス manifest。
- Codex プラグインパッケージ（`plugins/mission/`）と skill symlink ガイド（Stop hook は opt-in）。
- 状態ルーティング・スコアゲート・hook 挙動をカバーする Python テストスイート。
- GitHub Actions CI（`push` / `pull_request` / `workflow_dispatch`）。pytest と ShellCheck を実行。

[1.0.4]: https://github.com/tackeyy/mission/releases/tag/v1.0.4
[1.0.3]: https://github.com/tackeyy/mission/releases/tag/v1.0.3
[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
