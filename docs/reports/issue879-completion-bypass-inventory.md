# 必須条件の completion gate: 経路と検証証拠

対象: [Issue 879](https://github.com/tackeyy/mission/issues/879) /
[draft PR 893](https://github.com/tackeyy/mission/pull/893)。
基点は `baabdd957e3929304c8d353c968a894ed02537b9`、開始 head は
`a3dafc43f29df323374b20803e2ae38688068dc4`。以下はその head に対する未コミットの作業結果。
正式 review・独立 Checker・required CI の accepted/green を示す記録ではない。

## 実装の結論

契約付き session の成功終了は拒否したままにする。coverage/fresh-review の公開 producer は
この段階にはない。`acceptance-fresh-review-pending` は解消しない。
matching receipt の回帰は、coverage が valid の保存済み fixture に対して公開 runner を実行し、
実際の passed receipt を生成する。公開 API が coverage を valid にできる、または完了できるという証明ではない。

- kernel は legacy passthrough または closed-v5 extensions から契約を読む。
- application は同じ pure guard を承認 provider の起動前にも確認する。
  provider による公開 archive の追加を拒否後に残さず、kernel の最終 MarkPass guard も維持する。
- `command` を verifier 定義で上書きする変数名を `verifier_command` へ変更した。
- 配布 mirror を同期した。kernel に IO・application import を追加していない。
- closeout の既 pass 判定と candidate observer の構築を application へ移した。
  thin-adapter baseline は増加なし。closeout の non-allowlisted call を 32→31、compare を 2→1 に縮小した。
- legacy v4 の通常 init が同じ session の契約を失わせる経路を拒否した。
  同一 mission の再開・別 mission への置換とも、archive/backup/state の公開前に止める。

## 迂回経路の一覧

CLI の証拠は [test_issue879_completion_cli.py](../../skills/mission/tests/test_issue879_completion_cli.py) の
各関数（下表）。
すべての拒否回帰で exit 2、state/backup bytes、公開 session/evidence/aggregate を比較する。
v4 flat と v5 fenced container の両方を実行する。

| 経路 | 判定・証拠となるテスト |
|---|---|
| `mark-passes` / 未合格 `closeout` | `test_pending_contract_rejects_public_completion_atomically`: pending coverage を拒否 |
| 既に `passes=true` の `closeout` | `test_already_passed_contract_closeout_is_not_a_success_shortcut`: exit 2、公開 bytes 不変 |
| valid coverage、matching passed receipt | `test_public_completion_revalidates_latest_receipt[matching]`: fresh-review pending まで到達して拒否 |
| 必須 receipt 欠落、2 つ目の必須条件欠落 | 同関数の `missing` / `missing-second`: 全 required 条件を要求 |
| malformed / 最新 failed / 最新 blocked | 同関数の `invalid` / `latest-failed` / `latest-blocked`: 古い pass に fallback しない |
| contract / frozen policy / verifier definition 不一致 | 同関数の `stale-contract` / `stale-policy-binding` / `stale-definition` |
| 同じ HEAD の候補内容変更、候補取得不能、live policy drift | 同関数の `stale-candidate` / `unavailable-candidate` / `stale-policy` |
| typed approval を持つ `mark-passes --force` | `test_valid_force_approval_cannot_override_acceptance`: 契約を拒否、承認 receipt も公開されない。契約なしの対照では同じ registered provider が成功 |
| `set passes=true` / `terminal_outcome=completed_pass` | `test_alternate_completion_and_evidence_writes_reject_atomically`: dedicated/frozen authority による拒否 |
| `set phase=done/halted` / `advance --phase done/halted` | 同関数: terminal writer 迂回を拒否 |
| `set schema_version=2` | 同関数: downgrade による gate 迂回を拒否 |
| 契約の再登録・削除・coverage 上書き | 同関数の再 import / `acceptance_contract=null` / 契約上書き、および `test_contract_import_cannot_produce_valid_coverage` |
| 通常 `init` による契約の除去 | `test_reinitialization_cannot_remove_a_frozen_contract`: v4 の resume/replace を拒否、v5 の既存拒否も維持。すべて公開 bytes 不変 |
| `set verification_receipts=...` | 同関数: receipt writer の権限を汎用 set に渡さない |
| `verification record` に receipt/coverage を混入、caller boolean | `test_observations_and_caller_boolean_cannot_supply_acceptance_evidence`: legacy observation だけを記録し、契約/runner receipt を書かず、mark-passes を拒否 |
| `verification run` | 上記 receipt 回帰と既存 [runner テスト](../../skills/mission/tests/test_issue878_verification_runner.py): runner が receipt だけを発行。passed command は fresh review を代替しない |
| `halt` / `mark-halt` | `test_halt_stops_a_contract_session_without_claiming_success`: 停止は許可するが `passes=false`, phase=halted, outcome=failed。契約も維持 |
| v4 decode/encode/projection | `test_codecs_keep_contract_and_receipt_evidence`: 契約と receipt を legacy projection に保持し、kernel/CLI が拒否 |
| closed schema 5 decode/encode | 同関数: extensions の契約/receipt を保持し、pure kernel が pending coverage を拒否。契約なしの pure MarkPass は従来どおり成功 |
| 契約なし normal pass、既 pass closeout | `test_contractless_completion_and_already_passed_closeout_remain_usable`: 公開成功動作と再 closeout の bytes 不変を維持 |

source inventory: `skills/mission/lib/mission_application/review.py` の `mark_pass` と
`skills/mission/lib/mission_kernel/transitions.py` の `_mark_pass` が pass writer。
`skills/mission/lib/mission_kernel/commands.py` の `GENERIC_SET_DEDICATED_FIELDS` は
`acceptance_contract` / `verification_receipts` を保護する。
`skills/mission/lib/mission_application/lifecycle.py` の `advance` は terminal targets を拒否する。
`skills/mission/lib/mission_application/legacy_initialization.py` の `initialize_legacy_v4` は
通常 init で契約付きの既存 document を再初期化しない。
明示 `--new-mission` は `mission_application/lifecycle.py::initialize` と
`mission_persistence/reinitialization.py::V5MissionReinitializer` が別 identity の開始を扱う経路であり、
旧 mission の completion は行わない。
`skills/mission/bin/mission-state.py` の parser は acceptance-contract を import/status、
runner receipt を verification run に限定する。契約削除・任意 runner receipt import の公開 command はない。

## テストの検出価値と統合

公開 CLI 回帰は argparse、candidate recapture、application、pure guard、repository publication を
通すため、簡易 repository の saved=None だけでは検出しなかった公開 approval receipt の追加を検出した。
pending/missing/stale/latest-failure/force-pending の簡易 repository 5 関数を公開回帰へ統合した。
公開 CLI からは作れない candidate carrier 未配線の回帰は
`test_issue632_transition_is_the_writer.py::test_mark_pass_rejects_receipt_without_fresh_candidate_observation`
に保持した。既存 score/force/writer の保証は変更しない。

入力の全面的な表をすべての command に複製しない。receipt 詳細は mark-passes で、closeout は
その配線と既 pass shortcut、force は有効な承認 provider と公開 archive の副作用を個別に検査する。
subprocess の費用は実測結果に記録する。純粋な単体検査だけでは CLI と publication の境界を代替できない。
CI は `.github/workflows/ci.yml` の Python shards → `make test-shard` が `skills/mission` を収集する。
`scripts/ci_shard_targets.py::expand_target` は tracked ファイルだけを列挙するため、
新しい回帰は commit 後にその経路に入る。現状は未追跡で、明示ファイル指定によるローカル実行だけを検証した。
draft skip は検証成功と扱わない。

## Red と途中結果

- `python3 -m pytest -q -n 4 skills/mission/tests/test_issue879_completion_cli.py`:
  pytest-xdist が未導入で収集前停止、exit 4（pass/fail は未測定）。後の実行は一時領域に置いた pure Python test dependencies を使う。
- `python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py -x`:
  fixture 作成中の 5 回は各 2 passed / 1 failed、exit 1。その後の 1 回は 8 passed / 1 failed、exit 1。
  必須引数、v5 reference/payload shape、および valid coverage の下で runner receipt を生成する fixture に修正した。
- `python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py -k codecs`:
  1 passed / 1 failed / 50 deselected、exit 1。C gate の v4-only helper による schema 5 回帰を再現した。
- `python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py`:
  54 passed / 10 failed、exit 1、161.91 秒。再 import の理由名、halt outcome、approval receipt fixture の保存先を実装契約に合わせた。
- `python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py -k valid_force`:
  2 passed / 2 failed / 60 deselected、exit 1、8.98 秒。typed approval は legacy 対照で成功するが、契約拒否時に archive receipt が追加される原子性の欠陥を再現した。
- `python3 -m pytest -q -n 4 skills/mission/tests/test_issue879_completion_cli.py -k 'valid_force or codecs'`:
  6 passed、exit 0、4.91 秒。
- `python3 -m pytest -q -n 4 skills/mission/tests/test_issue879_completion_cli.py skills/mission/tests/test_issue632_transition_is_the_writer.py`:
  102 passed / 1 failed、exit 1、139.41 秒。追加 fixture の二度の genesis admission が秒境界をまたいだ lease 不一致。fixture clock を固定した。
- `python3 -m pytest -q -n 4 skills/mission/tests/test_issue879_completion_cli.py -k 'latest_failed or latest-failed'`:
  2 passed、exit 0、3.09 秒。
- `python3 -m pytest -q -n 4 --dist loadfile skills/mission/tests/test_issue879_completion_cli.py -k reinitialization`:
  修正前 2 passed / 2 failed、exit 1、18.07 秒。v4 の通常 init が exit 0 となり、
  保存済み contract と score history を除去した。修正後 4 passed / 0 failed、exit 0、13.12 秒。

## 最終対象検証

対象全体の初回は 555 passed / 1 failed、exit 1、237.82 秒。
失敗は `test_issue626_thin_adapter_guard.py::test_repository_scan_matches_the_headroom_free_baseline_exactly`。
開始 head の closeout 判定と candidate-capture lambda が baseline を超えていたため、
判断・observer の構築を lib へ移した。baseline を増やす対応は行っていない。

修正後の集中検証:

```sh
python3 -m pytest -q -n 4 --dist loadfile \
  skills/mission/tests/test_issue879_completion_cli.py \
  skills/mission/tests/test_issue626_thin_adapter_guard.py \
  skills/mission/tests/test_codex_wrapper_sync.py \
  skills/mission/tests/test_command_inventory.py \
  skills/mission/tests/test_python_module_inventory.py
```

215 passed / 0 failed、exit 0、225.64 秒。
同じ対象全体は 556 passed / 0 failed、exit 0、286.42 秒。
init 修正後の最終対象全体は次の command で実行した:

```sh
python3 -m pytest -q -n 4 --dist loadfile \
  skills/mission/tests/test_issue879_completion_cli.py \
  skills/mission/tests/test_issue632_transition_is_the_writer.py \
  skills/mission/tests/test_codex_wrapper_sync.py \
  skills/mission/tests/test_issue626_thin_adapter_guard.py \
  skills/mission/tests/test_command_inventory.py \
  skills/mission/tests/test_python_module_inventory.py \
  skills/mission/tests/test_mark_passes_threshold.py \
  skills/mission/tests/test_issue681_pass_gate_invariance.py \
  skills/mission/tests/test_issue618_kernel_a2_review.py \
  skills/mission/tests/test_terminal_outcome.py \
  skills/mission/tests/test_lifecycle_usecases.py \
  skills/mission/tests/test_issue617_kernel_a1_lifecycle.py \
  skills/mission/tests/test_issue620_kernel_a5_c1.py \
  skills/mission/tests/test_issue877_acceptance_contract.py \
  skills/mission/tests/test_issue878_candidate_snapshot.py \
  skills/mission/tests/test_issue878_verification_runner.py \
  skills/mission/tests/test_issue500_codec_v5.py \
  skills/mission/tests/test_artifact_hygiene.py \
  skills/mission/tests/test_vendor_fingerprint.py \
  skills/mission/tests/test_issue2_init_archive.py \
  skills/mission/tests/test_issue655_new_mission.py
```

592 passed / 0 failed、exit 0、436.98 秒。full suite は実行していない。
未追跡の新規ファイルは tracked-only の衛生・語彙テストの対象外なので、同じ scanner でも別途確認した。
新規 2 ファイルの衛生・語彙検査、`git diff --check`、
`python3 scripts/check-thin-adapter-ratchet.py` はすべて exit 0。

reviewed area は repo の 600 行 accountability 帯、1,400 行未満。
PR をさらに分けない理由は、C の completion authority、公開 CLI、拒否時の publication、
配布 mirror が同じ受入条件を構成するため。実装・回帰・mirror を 1 commit、証拠文書を別 commit とする案。

## 未解決・範囲外

1. fresh-review producer は後続 [Issue 689](https://github.com/tackeyy/mission/issues/689) の責務。
   coverage を valid にする公開 producer も本 PR にはない。現段階では契約付き session の成功を主張しない。
   caller boolean と legacy observation で補わない。
2. fenced v5 container と閉じた schema 5 payload は別物。通常 genesis は v4 payload を保持する。
   closed schema 5 payload の公開 compatibility read は
   `mission_kernel/codec_v4.py::project_legacy_document` の legacy passthrough 必須条件と
   `mission_persistence/legacy_v4.py::V5CompatibilityRepository.load/read_snapshot` により拒否される。
   codec/kernel の証拠保持と公開 CLI の非変更拒否は検証するが、schema 5 の公開成功経路は
   C で新設しない。この既存境界の修正・新規 Issue 起票は行わない。
3. 正式 review・独立 Checker・50 入力以上の独立した探索・required CI は未実施。
   commit/push/merge はこの作業では行わない。次は未コミット差分を規定の review に渡す。
