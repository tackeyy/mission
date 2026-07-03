# 変更履歴

**日本語** | [English](CHANGELOG.md)

本プロジェクトの主要な変更を記録します。

フォーマットは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

## [1.0.7] - 2026-07-03

### 修正
- `mission-state.py` と `mission-migrate.py` に `from __future__ import annotations` を追加し、PEP 604 union 注釈が Python 3.9 (macOS Xcode CLT の `python3`) でモジュール読み込み時にクラッシュして全コマンドが使えなくなる問題を修正しました (#99)。

### 追加
- `mission-state.py codex-preflight` を追加しました。現在の Codex `/mission` session に active state があるか、user Stop hook に `mission-stop-guard.sh` が登録されているか、`mission-state.py next` fallback で継続できるかを診断します。skills-only の Codex run では警告に留め、`--require-stop-hook` では hook 未設定を failure にできるため、Issue #108 の「state なし・guard なし・未完了 final」パターンを検出できます。
- `specialists recommend --user-specified <skill,skill>` を追加しました。ミッション本文でユーザーが名指ししたスキルを confirmed 扱いにし、high-risk task profile でも `selection_source: user-specified` の selected として記録するため、以後の `log-invocation` が `--selection-source confirmed-user` 要求で reject されなくなります (#100)。名指しの中に first-use consent が必要な provider が混在する場合、または required specialist が未インストールの場合は、全体を従来の確認フローに倒します。
- `mission-state.py push-score --scoring-json <path>` (ADR-002 Stage 1) を追加しました。scorer の構造化 JSON ファイルから items を読み、`composite`/`min_item` を CLI 側で再計算し、未知キー・範囲外値を reject し、payload を `_meta` 付きで `iter-N-<mid8>-scoring.json` として archive し、score entry に `score_source`/`scoring_evidence_path` を記録します (orchestrator のスコア転記レイヤを排除)。
- `push-score` が「全 items スコアが 1.0 以下」の入力を 0-1 正規化スケール混入の疑いとして reject するようにしました (実ログで composite 0.96 = 4.8/5 が push された事例の回帰ガード)。
- `mission-state.py next` (ADR-002 Stage 3) を追加しました。session state から次の 1 手 (`run-planner`/`run-reviewers`/`run-scorer`/`mark-passes`/`report-blocker` 等) を決定論的に導出し、Stop hook が使えない Codex セッションや compaction 復帰時に、散文指示に依存しないハーネス非依存の進行ガイドを提供します。

### 変更
- scoring evidence なしの `push-score` は default で hard reject するようにしました。`--scoring-json` (推奨) または `--scoring-output` を指定してください。移行専用の一時 escape hatch として `MISSION_REQUIRE_SCORING_EVIDENCE=0` は残しています (#105)。
- evidence なし `push-score` の generated scoring evidence fallback を削除し、reviewer 本文のない `generated=true` archive file で score entry を裏付ける挙動を廃止しました (#105)。

## [1.0.6] - 2026-07-02

### 修正
- `mission-state.py init` が破損した session JSON を隔離するようになり、同一セッションでの mission 変更時にクラッシュしないようにしました。
- `mission-state.py set` が pass・score history・threshold 系フィールドを凍結するようになり、raw な state 更新で完了ゲートをバイパスできないようにしました。
- `mission-state.py push-score` が、渡されたスカラースコアと items 明細のスコアが乖離している場合に警告を出すようにしました。
- Stop hook の CWD 探索が遅い `lsof` によるハングを避け、Linux では `/proc/<pid>/cwd` を優先し、自セッションの直接参照を先に行い、`awaiting_user` セッションの stale auto-halt をスキップするようにしました。
- specialist の同点処理が、インストール済みで optional な low/medium リスク provider を決定論的に自動選択し、tie-break 理由を記録するようにしました。
- mission executor が `Agent` や `rm` を含まない bounded な allowed tools を宣言するようにしました。
- specialist の task_profile 分類が architecture / system design 系 mission を認識するようになり、architecture 専用の project / user provider が documentation fallback に隠れて選ばれない問題を修正しました。
- mission audit が archived worktree の `iteration-archive/` ディレクトリに保存された scorer evidence を認識するようになり、scoring artifact が存在する場合の `missing-scoring-evidence` 誤検出を防ぐようにしました。
- mission audit が JSON として完全一致する archive-only の worktree state copy を resolved duplicate として分類するようにし、cross-root audit で想定内の archive/archive copy が P1 `duplicate-state` と誤報告されないようにしました。

### 追加
- ADR-002 として、local JSON + flock ストレージを維持したまま Finding / Score / Decision / Action を段階的に型付き state オブジェクト化するロードマップを定義しました。
- local-first な mission artifact を archived evidence 付きで管理する `mission-state.py artifact` CLI を追加しました（`docs/MISSION_ARTIFACTS.ja.md` 参照）。
- specialist registry の `kind: command` provider に `env` と `timeout` の runtime 設定を宣言できるようにしました。`env` はその provider プロセスにのみ渡され、CLI の `--timeout` は registry の値より優先されます。

## [1.0.5] - 2026-06-26

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
- mission audit に `--current-since` を追加し、historical audit debt を可視化したまま current regression と分離して判定できるようにしました。
- distribution release では、対応する git tag の作成・push、GitHub Release の作成または更新、両方の再照合まで完了条件とする release guardrail を追加しました。

### 変更
- mission orchestrator の運用指針に、`phase=executing` / `phase=reviewing` の明示更新と長時間作業の progress checkpoint を必須化しました。
- Complex mission の specialist accounting を、リスクを持つ candidate だけに explicit terminal decision を求める形へ調整し、ユーザー plugin をデフォルトでは optional evidence source として扱うハッカブルな拡張性を維持しました。
- database/backend candidate は schema / migration / query / SQL / persistence などの強い database signal がある場合だけ high-risk accounting candidate として扱うようにしました。
- command provider の `result_contract` により、準備完了バナーだけ、または短すぎる出力を `prepared` と分類し、完了済みレビュー証跡として扱わないようにしました。
- `oracle-reviewer` に browser-review の準備完了バナー向け default result contract を適用し、`ask-user` 後の specialist confirmation は `--selection-source confirmed-user` で永続化してから selected evidence として扱うようにしました。
- broad orchestrator specialist は non-execution の evidence use に限定し、plan/review などの適用済み証跡には `--bounded-purpose` を必須にしました。
- Standard / Complex の監査・自己改善 mission では、利用可能な testing / security / risk specialist candidate に explicit accounting を求めるようにしました。

### 修正
- command provider invocation が `completed` と記録されていても、archive evidence が Oracle / browser review の準備パケットだけの場合に mission audit が検出するようにしました。
- mission audit が、ユーザー判断待ちの active な `ask-user` specialist wait を、decision 記録前の candidate-only specialist debt として誤検出しないようにしました。
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

[1.0.7]: https://github.com/tackeyy/mission/releases/tag/v1.0.7
[1.0.6]: https://github.com/tackeyy/mission/releases/tag/v1.0.6
[1.0.5]: https://github.com/tackeyy/mission/releases/tag/v1.0.5
[1.0.4]: https://github.com/tackeyy/mission/releases/tag/v1.0.4
[1.0.3]: https://github.com/tackeyy/mission/releases/tag/v1.0.3
[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
