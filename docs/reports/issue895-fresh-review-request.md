# D1: typed fresh-review request の実装と検証

対象: [Issue 895](https://github.com/tackeyy/mission/issues/895)。
基点: `93c0833efc55a90f535d7c525ac533c7897b3cbf`。
設計: [fresh-review request/receipt の設計](https://github.com/tackeyy/mission/blob/93a631c68d23b8b9e9b5fac1a981347702580a08/docs/design/689-fresh-review-receipt.md)
の §1、§2、§7、§8 D1、§9 の確定判断。

## 実装範囲

- 閉じた request/projection decoder を v4 の `fresh_review` と closed-v5
  extensions の予約キーで共有。キー欠落だけを空として扱う。
- prepare は nonce/request ID を生成し、command ごとの候補 digest map と
  immutable input packet を束縛する。packet と request は既存の evidence
  transaction で公開し、v5 container では fenced generation に束縛する。
- 同じ operation/intent/payload は保存した request を再応答する。
  nonce の予約・消費は pure reducer に置き、reserved/consumed と保存結果を
  decoder で復元する。runtime dispatch/result の公開コマンドは含めない。
- 公開 CLI は prepare/status だけ。schema/help と ownership を登録。
  set・init・new-mission・review-import から request の上書きを防ぐ。
- adapter registration digest は将来の照合用 binding。host の登録・能力強制・
  起動許可を検証した証拠ではない。
- completion gate の関数は変更しない。契約ありの pending 拒否を維持。

## テストの検出価値

公開 CLI テストは v4 flat/v5 container の request+packet 公開、historical retry、
operation 衝突、汎用 writer、packet 上限、未公開 runtime command、completion 拒否を
観測する。既存の completion/verification fixtures を再利用する。
validator と nonce reducer は pure test で nested shape、bool-as-int、digest、
operation の意図変更、nonce 再利用、stale binding、消費状態の保存復元を検出する。
同じ validation 表を CLI ごとに繰り返さない。新規 subprocess 費用は最終実測を下に記録。
CI は `scripts/ci_shard_targets.py::expand_target` の git-tracked tests discovery を使う。
新規テストは未追跡なので現在の checkout の CI 選択にはまだ入らず、commit 後に対象となる。
既存衛生・語彙 gate も tracked-only のため、未追跡の新規ファイルは同じ scanner で別途検査した。

## Red と検証記録

- 初回: `python3 -m pytest -q skills/mission/tests/test_issue895_fresh_review.py --tb=short`
  → 16 failed、exit 1、39.94 秒。公開 prepare が未登録で拒否された。
- nested shape の 56 入力から candidate role の list/dict が hash lookup に到達する
  経路を確認し、理由コード付き検査を先行させた。空 capability list は有効。
- `python3 -m pytest -q skills/mission/tests/test_issue895_fresh_review.py -k 'kernel' --tb=short`
  → 1 failed / 2 passed / 23 deselected、exit 1。typed command の request が dict の時、
  dataclass serialization が TypeError になった。専用理由コードの拒否へ修正。
- 最終指定テスト: **487 passed、exit 0、558.57 秒**（新規ファイルは 26 cases）。
  pytest-xdist は未導入で `-n` なし。実行中は編集しない。
  module inventory/compat、mirror、thin-adapter baseline、既存 completion/verification、
  operation/lifecycle 回帰を含む。CI 自体は未実行。
- `git diff --check`: exit 0。未追跡ソースを既存の home-path/personal-store/vendor scanner
  で検査して検出ゼロ（exit 0）。
- 正式な異系統レビュー・独立 Checker・required CI は実装段階では未実施（結果は PR 本文に記録する）。

最終テストの正確な argv:

```bash
python3 -m pytest -q skills/mission/tests/test_issue895_fresh_review.py skills/mission/tests/test_issue879_completion_cli.py skills/mission/tests/test_issue632_transition_is_the_writer.py skills/mission/tests/test_issue877_acceptance_contract.py skills/mission/tests/test_issue878_verification_runner.py skills/mission/tests/test_issue878_candidate_snapshot.py skills/mission/tests/test_issue500_codec_v5.py skills/mission/tests/test_command_inventory.py skills/mission/tests/test_issue626_thin_adapter_guard.py skills/mission/tests/test_python_module_inventory.py skills/mission/tests/test_plugins_in_sync.py skills/mission/tests/test_codex_wrapper_sync.py skills/mission/tests/test_artifact_hygiene.py skills/mission/tests/test_vendor_fingerprint.py skills/mission/tests/test_issue747_p2b_cli_operation_id.py skills/mission/tests/test_issue617_kernel_a1_lifecycle.py --tb=short
```

## 範囲外・引き継ぎ

- D2: adapter registry、host launch、dispatch/result の fenced publication、
  reconcile/replay verifier。D1 の reserve/consume reducer を公開 commit に接続する。
- D3: completion receipt/coverage/finding gate。現行 pending 拒否を置き換える。
- C carry-over の null contract / persisted verifier shape の横断修正は含めない。
  [candidate capture](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_application/review.py#L29-L53)
  と [completion rejection](https://github.com/tackeyy/mission/blob/93c0833efc55a90f535d7c525ac533c7897b3cbf/skills/mission/lib/mission_kernel/transitions.py#L985-L1100)
  に None presence 判定と persisted command lookup がある。対象側で再確認する。
- GitHub in-flight 照合は接続エラー、exit 1。専用 worktree と単独 writer の委任を
  根拠に実装。外部 PR/CI/merge 状態は未確認。Git の変更操作は実行しない。

## コミット分割案

1. `feat: fresh-review の型と一度限りの消費規則を追加`
   kernel/codec と pure 回帰をまとめる。
2. `feat: fresh-review の prepare と status を公開`
   application、evidence 公開、CLI、writer 保護、公開 CLI 回帰をまとめる。
3. `docs: D1 の検証結果と引き継ぎを記録`
   本記録をまとめる。

D1 は 1 PR の単一契約変更。型・producer・consumer・対応回帰を揃える必要があり、
コミットは分けても PR をさらに分けない。mirror を除く reviewed lines は下の計測値を参照。

- 最終時点で origin/main は設計文書の merge commit
  `93a631c68d23b8b9e9b5fac1a981347702580a08` に進んだ。作業 HEAD は指定基点を維持。
  統合や Git の変更操作は行わない。設計 remote branch は prune 済みだが、読んだ設計
  commit の object は存在し、上の固定 SHA で参照できる。

## 変更ファイル

- `docs/reports/issue895-fresh-review-request.md`
- `skills/mission/bin/mission-state.py`
- `skills/mission/lib/mission_application/command_owners.py`
- `skills/mission/lib/mission_application/contract_schemas.py`
- `skills/mission/lib/mission_application/evidence.py`
- `skills/mission/lib/mission_application/evidence_publication.py`
- `skills/mission/lib/mission_application/fresh_review.py`
- `skills/mission/lib/mission_application/legacy_initialization.py`
- `skills/mission/lib/mission_application/lifecycle.py`
- `skills/mission/lib/mission_kernel/codec_v4.py`
- `skills/mission/lib/mission_kernel/codec_v5.py`
- `skills/mission/lib/mission_kernel/commands.py`
- `skills/mission/lib/mission_kernel/fresh_review.py`
- `skills/mission/lib/mission_kernel/model.py`
- `skills/mission/lib/mission_kernel/transitions.py`
- `skills/mission/lib/mission_persistence/evidence_order.py`
- `skills/mission/tests/test_issue895_fresh_review.py`

実装の 15 canonical ファイルは `plugins/mission/skills/mission/` に byte-identical mirror を同期。
テストと本記録は配布 mirror の対象外。

Reviewed lines（追加+削除、未追跡も含み mirror を除外）: **1079 行**。
1,400 行の分割閾値以内。600 行の説明責任帯のため、上記の単一契約の理由を PR に残す。
