# 必須条件の completion gate: 経路と検証証拠

対象: [Issue 879](https://github.com/tackeyy/mission/issues/879) /
[merged PR 893](https://github.com/tackeyy/mission/pull/893)。
基点は `baabdd957e3929304c8d353c968a894ed02537b9`、開始 head は
`a3dafc43f29df323374b20803e2ae38688068dc4`。C の実装は 2026-10-03 12:40 JST に
merge commit `93c0833efc55a90f535d7c525ac533c7897b3cbf` として取り込まれた。
本書は C の履歴と、後続 [D0: Issue 894](https://github.com/tackeyy/mission/issues/894) の形状検証を記録する。

## C の確定記録

以下は GitHub の一次記録（PR 893 のコメント、required CI run、merge commit）で確認した値である。

- [PR 893 の review / Checker / merge](https://github.com/tackeyy/mission/pull/893):
  reviewed head `b1b30145cf596c4577ed5ea86de3d016754fd31c` で異系統 review round 1 と
  独立 Checker がともに accepted、High / Medium はともに 0 件。
- 同 PR の独立反例探索は 71 入力（must-reject 63、must-accept 8）を実行し、
  product defect は 0 件、harness の誤りは 2 件。
- [required CI run 37093400756](https://github.com/tackeyy/mission/actions/runs/37093400756) は全 success。
- [merge commit 93c0833](https://github.com/tackeyy/mission/commit/93c0833efc55a90f535d7c525ac533c7897b3cbf)
  は 2026-10-03 12:40 JST の merge。以下の Red / 途中結果は過去の記録として保持する。

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
| D0: 不正な criterion command_id / 凍結 command の欠落field・出力要素・external inputs | `test_malformed_frozen_verifier_rejects_public_completion_atomically`: lookup / hash / capture 前に `acceptance-contract-invalid` または `verifier-policy-command-invalid` で拒否 |
| D0: live / frozen command の閉じた形 | `test_shared_validator_closes_live_and_frozen_command_fields_before_sets`: 必須fieldと各要素の表を共有し、live の set 化前検査も検証 |
| D0 追補: `acceptance-contract status` の不正binding・criterion・command・Unicode | `test_status_rejects_malformed_persisted_contract_atomically` と上記共有表: 同じ閉じたvalidatorを表示・digest前に通し、exit 2で拒否。正常契約とキー欠落は `test_status_keeps_valid_and_contractless_sessions_readable` で維持 |
| D0 追補: runner の通常 / blocked receipt のcanonical失敗 | `test_runner_rejects_uncanonical_contract_before_receipt_generation`: 契約全体のcanonical検査を共有境界へ置き、実行・receipt生成前に拒否 |
| D0 追補: 既pass closeout の不正frozen command | `test_already_passed_closeout_validates_frozen_commands`: 共有validatorの理由コードで拒否し、公開bytesと制御値を保持 |
| D0 parser追補: malformed URL argv のlive import / frozen run / replay run | `test_malformed_url_arguments_reject_before_import_or_execution`: URL parserのValueErrorを明示path拒否へ変換。`test_explicit_path_parser_rejects_malformed_command_inputs` はpercent escape・option内・環境変数の同形も確認 |
| D0 parser追補: v5 containerのUnicodeとcompletion / 既pass / force | 既存の `test_malformed_frozen_verifier_rejects_public_completion_atomically` / `test_already_passed_closeout_validates_frozen_commands` を拡張。argv / expectedの孤立surrogateを共有projection境界で `canonical-json-invalid` に拒否 |
| D0 parser追補: get / next / init / new-mission / freshness / lane-report | `test_authoritative_contract_reads_reject_unencodable_state`: encoding不能な契約を読み取り・再初期化前に拒否。通常initの既存理由とv4のnew-mission拒否も維持 |
| D0 parser追補: closed schema 5のraw契約のinspection | `test_closed_v5_inspection_rejects_unencodable_raw_contract`: consumer projectionに表示されないextensionsも共有snapshot境界でUTF-8検査し、audit snapshotへ不正契約を渡さない |
| D0 parser追補: replay command / replay入力本文のUnicode | `test_runner_and_replay_reject_malformed_frozen_commands_atomically`: v4/v5ともreceipt追加なし。入力本文は `replay-input-invalid`、v5の契約projectionは `canonical-json-invalid` |
| D0: runner / replay の不正command・replay command_id | `test_runner_and_replay_reject_malformed_frozen_commands_atomically`: 同じ共有validatorで exit 2、receipt追加なし |
| D0: present null の mark-passes / closeout / 既pass / status / runner / 通常init | `test_null_contract_is_present_and_rejects_atomically`: null を契約なしに変換しない。v5 init は既存の `session-already-initialized` 拒否を維持 |
| D0: v4 / closed-v5 kernel の null・不正command | `test_codecs_keep_contract_and_receipt_evidence[null / malformed-command]`: pure gate が理由コード付き拒否、transition なし |
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
C の回帰ファイルは PR 893 で tracked となり、required CI run 37093400756 で実行された。
D0 は同じ tracked ファイルを拡張するため、その収集経路を共有する。
draft skip は検証成功と扱わない。

## C の Red と途中結果（履歴）

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

## C の最終対象検証（履歴）

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
C の commit 前には新規ファイルが tracked-only の衛生・語彙テストの対象外だったため、
当時は同じ scanner でも別途確認した。PR 893 ではそれらも tracked ファイルとして検査対象に入った。
新規 2 ファイルの衛生・語彙検査、`git diff --check`、
`python3 scripts/check-thin-adapter-ratchet.py` はすべて exit 0。

reviewed area は repo の 600 行 accountability 帯、1,400 行未満。
PR をさらに分けない理由は、C の completion authority、公開 CLI、拒否時の publication、
配布 mirror が同じ受入条件を構成したため。C は PR 893 として merge 済み。

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
3. C の review / Checker / 独立探索 / CI / merge は上の確定記録を参照。
   D0 の正式 review・独立 Checker・required CI は別工程であり、C の accepted を流用しない。
4. candidate capture の低水準 primitive を共有validatorなしで直接呼ぶ場合の型不正は対象外。
   `skills/mission/lib/mission_application/verification_runner.py::capture_candidate` の
   `tuple(declared_untracked)` / `for item in external_inputs` は null 入力で TypeError を返す。
   D0 の公開経路では、その前に `acceptance_contract.py::frozen_verifier_commands` を通す。


## D0 の実装と検証（初回の履歴）

テストリスト: 不正な criterion command_id、凍結commandの必須field / 各要素、
replay の参照とtarget、present null、key-absent legacy、v4 / v5 の公開bytes不変、pure kernel。

- `skills/mission/lib/verifier_command.py::validate_command` / `validate_command_links` が
  IO のない閉じた形状検査を担う。live policy は同じ検査を使用し、出力要素を set 化する前に検査する。
- `skills/mission/lib/acceptance_contract.py::frozen_verifier_commands` は criterion / binding の
  不正を `acceptance-contract-invalid`、command の不正を `verifier-policy-command-invalid` に分ける。
  candidate capture、mark-pass preflight、kernel、runner / replay の lookup / hash / capture より前に使う。
- 契約はキーの存在で判定する。present null は拒否し、キー欠落legacyを維持する。
  通常initにも同じpresence規則を適用し、archiveの例外処理が拒否をwarningへ変えないようにする。
- CLI adapter の変更は runner のエラーを exit 2 へ変換する1行だけ。判断はlibにあり、
  thin-adapter baseline を増やさない。kernel にIO・application importを追加していない。

Red / Green の実測（すべて `python3 -m pytest -q`、xdist 未導入につき `-n` なし）:

| 対象 / selector | passed | failed | deselected | exit | 秒 |
|---|---:|---:|---:|---:|---:|
| `test_issue879_completion_cli.py`（baseline） | 72 | 0 | 0 | 0 | 203.14 |
| 同ファイル `-k malformed_frozen`（Red） | 0 | 8 | 72 | 1 | 26.24 |
| 同ファイル `-k malformed_frozen`（Green） | 8 | 0 | 72 | 0 | 18.38 |
| 同ファイル `-k null_contract`（Red） | 1 | 11 | 80 | 1 | 20.26 |
| 同ファイル `-k null_contract`（途中） | 11 | 1 | 80 | 1 | 28.02 |
| 同ファイル `-k 'null_contract or malformed_frozen or contractless'`（Green） | 22 | 0 | 70 | 0 | 45.02 |
| 同ファイル + `test_issue632_transition_is_the_writer.py` + `test_issue626_thin_adapter_guard.py`、`-k 'shared_validator or runner_and_replay or codecs'` | 13 | 0 | 201 | 0 | 41.14 |

Red の不正shapeでは6件が internal-error / exit 1、必須argv欠落2件はshape検査を通過して
coverage-pendingで拒否された。nullは完了やstatusがexit 0となる経路、およびrunnerの
理由文字列を `sys.exit` に渡してexit 1となる経路を再現した。
途中のv4 initは理由コードをValueErrorで投げたためarchive処理がwarningへ変換した。
公開前のSystemExitへ揃え、公開bytes不変のGreenを確認した。

独立探索は純粋validatorに対して不正97入力 / 正常8入力を実行し、
不正は全拒否、正常は全受理、TypeError / KeyError / AttributeErrorは0件。
入力分類の訂正4件（base未変更、既存の `.` 許容、正常test report、optional replay null）は
不正97入力の数に含めない。これは公開CLIの原子性や正式reviewの代替ではない。
null / key-absentのcapture・status・既pass・preflight・runnerも独立したread-only検査で確認した。

テストの検出価値: commandの形状表は純粋な共有検査で1度だけ検証し、公開CLIでは
lookup / capture / replay / presenceの別境界を代表入力で検査する。
既存の `_policy` / `_replay_policy` / completion fixture とbytes比較を再利用した。
writerのcandidate欠落テストは完全なcommand fixtureへ更新し、同じ欠落保証を保持する。
codec回帰はclosed-v5のextensionsでも下位gateが不正shapeを見ることを追加検証する。
CLI subprocessの費用は上表の実測。全面入力表を全CLIへ複製するより安く、
pure testだけでは検出できない公開state / backup / approval receiptの副作用も検査できる。

最終の指定14ファイル検証:

```sh
python3 -m pytest -q \
  skills/mission/tests/test_issue879_completion_cli.py \
  skills/mission/tests/test_issue632_transition_is_the_writer.py \
  skills/mission/tests/test_issue877_acceptance_contract.py \
  skills/mission/tests/test_issue878_verification_runner.py \
  skills/mission/tests/test_issue878_candidate_snapshot.py \
  skills/mission/tests/test_codex_wrapper_sync.py \
  skills/mission/tests/test_issue626_thin_adapter_guard.py \
  skills/mission/tests/test_command_inventory.py \
  skills/mission/tests/test_python_module_inventory.py \
  skills/mission/tests/test_plugins_in_sync.py \
  skills/mission/tests/test_artifact_hygiene.py \
  skills/mission/tests/test_vendor_fingerprint.py \
  skills/mission/tests/test_mark_passes_threshold.py \
  skills/mission/tests/test_terminal_outcome.py
```

399 passed / 0 failed、exit 0、427.78秒。full suiteは実行していない。
mirror一致、thin-adapter baseline一致、inventory / import、衛生・語彙を含む。
初回作業では新規validatorのsource / mirrorがまだtrackedではなかったため、同じ衛生・語彙scannerでも個別に検査した。
文書の結果追記後は全変更ファイルを同じscannerで確認し、`git diff --check`もexit 0。
テスト実行中はファイルを編集していない。

上表の実際のコマンド（Red / Greenは同じコマンドを再実行）:

```sh
python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py
python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py -k malformed_frozen
python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py -k null_contract
python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py -k 'null_contract or malformed_frozen or contractless'
python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py -k 'shared_validator or runner_and_replay or codecs' skills/mission/tests/test_issue632_transition_is_the_writer.py skills/mission/tests/test_issue626_thin_adapter_guard.py
```

D0の正式review / 独立Checker / required CI / GitHubへの公開は実施していない。
独立したread-only探索の結果を正式なacceptedへ読み替えない。
初回作業時のHEADは基点`93c0833efc55a90f535d7c525ac533c7897b3cbf`だった。
その時点のローカル`origin/main`は`93a631c68d23b8b9e9b5fac1a981347702580a08`
（[設計文書PR 898](https://github.com/tackeyy/mission/pull/898)）へ1commit進んでいた。
差分は`docs/design/689-fresh-review-receipt.md`のみ。Git操作禁止に従い統合はしていない。
次工程では最新baseを確認してからcandidateをfreezeし、review / Checker / CIを同じheadで取得する。
commit分割案: `fix: 永続completion gateの不正形とnull契約を拒否する`
（実装・回帰・mirror）、`docs: completion gateの確定記録とD0の検証経路を更新する`（本書）。
本作業はcommit / push / PR / mergeを行わない。

## D0 status 追補: 同種検索とRed / Green（履歴）

追補の開始HEADは `4c3e535ca24eb7e4e88ffb1c015811b2b1408c3e`。cleanな専用worktreeで確認した。
依頼元から共有された独立探索は、このheadの122入力中34件がstatus経路で期待と異なり、
原因は共有validatorを通さない表示・digest処理だった。この件数は今回のローカル測定ではない。

以下の検索をrepository全体のPython sourceで実施し、mirrorは同じsourceの複製として照合した。

```sh
rg -n 'acceptance_contract|frozen_verifier_commands|canonical_contract_digest|canonical_bytes|verifier_definition_digest' skills scripts --glob '*.py' --glob '!**/tests/**'
rg -n 'from acceptance_contract|import acceptance_contract' --glob '*.py' --glob '!**/tests/**' --glob '!plugins/**' .
rg -n 'acceptance_contract|verifier_policy|canonical_contract_digest|canonical_bytes\(' --glob '*.py' --glob '!**/tests/**' --glob '!plugins/**' .
```

下表のlocatorは `skills/mission/lib/` に対するpath:line。契約を解釈する経路と、
既に検査済みの値だけをhashする経路をsourceで確認した。

| hit | 判定 / 対応 |
|---|---|
| `acceptance_contract.py:203`, `mission_application/acceptance.py:78`, `:132` | statusを共有frozen validatorへ接続。契約例外をEvidenceFailureへ変換し、CLIでexit 2。schema 1のimport済み契約も検査して表示を維持 |
| `mission_application/verification_execution.py:138`, `:169` | 通常 / blocked receiptのdigest前に、`:162`の共有validatorで契約全体のcanonical encodingを検査。Unicodeを含む契約を実行前に拒否 |
| `mission_application/review.py:88` | 既passのshortcutでも共有validatorを通し、不正commandの理由コードを保持 |
| `mission_application/review.py:35`, `:516`; `mission_kernel/transitions.py:999`, `:1026`, `:1046` | candidate capture / preflight / kernelは既存の共有validatorと理由変換を使用。共有canonical検査の追加も同じ境界で効く。kernelのdigestはValueError（AcceptanceContractErrorの親）も拒否へ変換 |
| `mission_application/acceptance.py:43`, `:58`, `:68`, `:72` | importのdigestはloadの閉じた検査・canonical encodingとlive policy freezeの後。永続契約を直接hashする経路ではなく、入力例外は既存の境界で変換 |
| `mission_application/evidence.py:239`, `:249` | import operationの再送結果はimportで検査済みのexpectedとの完全一致を要求し、不正な保存値はprojection-mismatchで拒否。digestは一致した値のみ。追加の受理経路はない |
| `mission_application/legacy_initialization.py:340` | キー存在時は通常initを無条件に拒否。契約を解釈・hash・captureしない安全な拒否境界を維持 |
| `mission_kernel/evidence.py:415`, `:429`; `mission_application/contract_schemas.py:97`; `mission_kernel/commands.py:432` | import commandの検査・キー保護・静的schema。検査なしで永続契約を受理するstatus経路ではない |
| `acceptance_contract.py:31`, `:135`, `:140`, `:155`, `:199`, `:215`, `:220`; `mission_application/verification_runner.py:4` | canonical helperとre-export。純粋helperはAcceptanceContractErrorを返し、公開契約経路の境界で変換。新しいstatus・共有validatorもその境界を使用 |
| `pregate_cache.py:105`; `mission_persistence/reinitialization.py:40`; `mission_persistence/fenced_commit.py:535` | `_canonical_bytes`は別の関数であり、acceptance contract helperの呼び出しではない |

v5のretained-v4 payloadにsurrogateがある場合、互換projectionが契約validatorの前に
UnicodeErrorを返す。status / runnerの読み取り境界をexit 2・`canonical-json-invalid`へ変換した。
v4のcommand / criterion shapeは共有validatorの`verifier-policy-command-invalid` /
`acceptance-contract-invalid`を保持する。全回帰で公開bytesとpasses / loop_active / phase /
terminal_outcomeを比較した。surrogate入りv5の制御値は、表示失敗と独立したfenced readerの保存bytesから確認する。

当時の範囲外の発見（後続parser追補で修正）: 汎用`get`は同じv5 surrogate fixtureでexit 1・internal-errorとなった。
`mission_persistence/legacy_v4.py:1057` / `:1095`の互換projectionと
`mission_kernel/json_codec.py:74`のUTF-8 encodingを通るためで、独立した公開CLI probeで確認した。
このstatus追補では汎用state出力のUnicode処理は変更しなかった。後続parser追補では共有persistence境界を修正した。

テストの検出価値: 既存command形状表をlive / frozen / statusで共有した。
公開CLIではcommand欠落、criteria空、Unicodeの別境界を代表入力に絞った。
runnerの通常 / blocked receiptと既pass shortcutは異なるhash / 拒否経路を通すため保持する。
正常契約・キー欠落のstatusは誤拒否を検出する。subprocess費用は下表の実測で示す。

すべて `python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py` に次のselectorを付けた。
xdistは未導入なので `-n` は使っていない。

| selector / 段階 | passed | failed | deselected | exit | 秒 |
|---|---:|---:|---:|---:|---:|
| `-k 'null_contract or shared_validator'` baseline | 13 | 0 | 90 | 0 | 38.03 |
| `-k status_rejects_malformed` 初回fixture調整 | 0 | 10 | 103 | 1 | 16.81 |
| 同selector Red（fixture調整後） | 0 | 10 | 103 | 1 | 54.16 |
| `-k 'shared_validator or status_uses_shared or runner_rejects_uncanonical or already_passed_closeout_validates or status_keeps'` Red | 4 | 8 | 112 | 1 | 86.59 |
| `-k 'status_rejects_malformed or shared_validator or status_uses_shared or runner_rejects_uncanonical or already_passed_closeout_validates or status_keeps'` Green | 22 | 0 | 102 | 0 | 115.88 |

初回10 failuresのうち3件はfixtureのUTF-8 encodingで止まったもので、product Redとは扱わない。
escaped JSONを実際のfenced genesisへ投入するfixtureに直した後の10 failuresでは、
statusのexit 0 / present trueと、canonical / projectionのinternal-error / exit 1を再現した。
追加Redではrunnerのinternal-error、既pass closeoutの不適切な理由コード、statusの表検査不実行を再現した。

追補の最終指定10ファイル検証:

```sh
python3 -m pytest -q \
  skills/mission/tests/test_issue879_completion_cli.py \
  skills/mission/tests/test_issue877_acceptance_contract.py \
  skills/mission/tests/test_issue878_verification_runner.py \
  skills/mission/tests/test_issue632_transition_is_the_writer.py \
  skills/mission/tests/test_codex_wrapper_sync.py \
  skills/mission/tests/test_issue626_thin_adapter_guard.py \
  skills/mission/tests/test_python_module_inventory.py \
  skills/mission/tests/test_plugins_in_sync.py \
  skills/mission/tests/test_artifact_hygiene.py \
  skills/mission/tests/test_vendor_fingerprint.py
```

295 passed / 0 failed、exit 0、735.31秒。full suiteは実行していない。
実行中の編集はなし。source / mirrorはbyte一致、thin adapterは変更なし。
結果追記後の文書も既存の衛生・語彙scannerで確認し、`git diff --check`はexit 0。
追補ではcommit / pushおよびレビューCLIを実行していない。正式review / Checker / CIは次工程で取得する。
commit案は `fix: 契約参照経路の不正形とcanonicalエラーを拒否する`（実装・回帰・mirror）と
`docs: status検証と同種検索結果を記録する`（本書）。

## D0 parser追補: 共有URL / state encoding境界

開始HEADは `79ca75a289a5a3df73797525da69f80e43f66b18`（clean）。依頼元から共有された
正式reviewと独立Checkerはともにchanges-requestedで、Mediumは不正URLの未捕捉ValueErrorと
v5 retained-v4 containerの孤立surrogateによる未捕捉encoding例外の2件だった。
これは依頼元のレビュー記録であり、今回レビューCLIを起動したという記録ではない。

`explicit_paths_are_supported` は不正URLをunsupportedとして返し、live importは
`verifier-policy-explicit-path-unsupported`、frozen / replay実行は
`verifier-explicit-path-unsupported` で拒否する。valid commandの判定は維持した。
state projection / encodingはpersistenceの共有境界で `FencedCommitError` に変換し、
生のUnicodeErrorは `canonical-json-invalid` となる。status / runnerの個別Unicode捕捉は除去した。
legacy compatibility fallbackもUTF-8 renderabilityを要求するが、歴史的なnon-finite scoreの
読取互換性は維持する。元の公開bytesを書き換えたり、null契約を除去したりはしない。
new-missionの既存error変換は共有reasonを保持する。freshness / lane-reportは共有errorの
表示を保持するだけで、adapterへvalidatorを追加しない。kernelは変更していない。

### 同形検索

以下を実行した。sourceを調査し、配布mirrorは同期後の同一bytesを確認する。

```sh
rg -n 'urlsplit\(|urlparse\(|shlex\.split\(|unquote\(' skills scripts --glob '*.py' --glob '!**/tests/**'
rg -n 'urlsplit\(|urlparse\(|shlex\.split\(|unquote\(|PurePosixPath\(|\.resolve\(|\.encode\(' skills/mission/lib/verifier_command.py skills/mission/lib/mission_application/verifier_policy.py skills/mission/lib/mission_application/verification_execution.py skills/mission/lib/mission_application/verification_runner.py
rg -n 'Path\(|\.resolve\(|\.relative_to\(|\.is_relative_to\(|\.is_absolute\(|urlsplit\(|urlparse\(' skills/mission/lib/verifier_command.py skills/mission/lib/mission_application/verifier_policy.py skills/mission/lib/mission_application/verification_runner.py skills/mission/lib/mission_application/verification_execution.py
rg -n 'project_legacy_document|encode_json_value\(' skills scripts --glob '*.py' --glob '!**/tests/**'
rg -n '_load_authoritative_state\(' skills/mission/bin/mission-state.py
rg -n 'read_authoritative_snapshot\(|read_live_authoritative_snapshot\(|read_authoritative_legacy_compatibility_snapshot\(|project_legacy_document\(|canonical_contract_digest\(|canonical_bytes\(' skills scripts --glob '*.py' --glob '!**/tests/**'
rg -n 'read_authoritative|read_legacy_compatibility|state_snapshot' skills scripts --glob '*.py' --glob '!**/tests/**'
```

locatorは特記しない限り `skills/mission/lib/` のpath:line。この追補の差分に対する行番号。

| parser / encoder hit | 判定 / 対応 |
|---|---|
| `mission_application/verifier_policy.py:51` | ValueErrorをunsupportedへ変換。live validator `:33` とrunner `verification_runner.py:293` が共有する境界を修正 |
| `mission_application/verifier_policy.py:61`, `:67`, `:71`, `:90`, `:104` | unquoteはreplacement decode、shlexは既存ValueError捕捉、executable Pathは閉じたargvの文字列。option / envも同じURL境界を通る |
| `verifier_command.py:9`, `:15`, `:23`; `mission_application/verification_runner.py:41`, `:74`, `:196` | command path / toolchainは閉じた型・NUL・surrogate・traversal検査後にPathへ渡る。candidate `_relative` とsymlink検査も既存理由コードを返す。追加変更なし |
| `mission_application/verification_runner.py:121`, `:148`, `:183`, `:189`, `:324` | candidate pathは_relativeで検査済み、resolveはproject/materialized rootのfilesystem経路、repro digestのkind/pathは閉じたreplay定義の検査後。command入力からの未捕捉URL parserは他にない |
| `mission_application/verification_runner.py:202`, `:332` | toolchainのPath / parentは閉じたcommandのpath検査後。既存OSError処理を維持 |
| `mission_application/verifier_policy.py:126`, `:127`; `mission_application/verification_execution.py:55` | policy file選択はproject / user root、repro入力file読取は既存OSError / ValueError捕捉で `replay-input-invalid`。command定義のpath parserではない |
| `mission_application/verification_execution.py:109`, `:120` | 探索でreplay入力本文のUTF-8 encodeにも同形を確認。実際のJSON escape入力でRedを取得し、`replay-input-invalid`へ変換。検査済みbytesを再利用 |
| `provider_public_contract.py:214` | 既存ValueError捕捉でFalseを返す。追加変更なし |
| `plan_contract.py:163` | plan resource identifier用のURL parserで、verifier入力の消費経路ではない。範囲外。source上のValueError候補であり、この追補では公開CLI再現・修正を行っていない |
| `mission_persistence/fenced_commit.py:118`, `:134`, `:2703`, `:2809` | shared context / projection wrapperを追加。kernelのencoder例外を理由付きpersistence rejectionへ変換。genesis / stageも同じwrapperを使用。既存coded codec errorの診断文言は保持 |
| `mission_persistence/legacy_v4.py:447`, `:526`, `:741`, `:1034`, `:1058`, `:1085`, `:1096`, `:1176` | proposal、historical replay、read_snapshot、pre-admission load、admitted loadの全projectionをshared wrapperへ接続 |
| `mission_persistence/authoritative_reader.py:275`, `:298`, `:474`, `:495`, `:664`, `:680`, `:694`, `:779` | flat / v5 snapshot、closed-v5 raw extensions、archive再hydration、legacy compatibility fallbackの全encoding / projectionを共有境界へ接続。fallbackで不正UTF-8を受理しない |
| `mission_kernel/json_codec.py:74`; `mission_kernel/codec_v4.py:862` | pure encoderは例外を返す責務のまま。公開persistenceで理由に変換。kernelにIO / application importを追加しない |
| `mission_kernel/transitions.py:1143`, `:1147` | force approvalのterminal digestは既存TypeError / ValueError / UnicodeError捕捉で `force-approval-binding-invalid`。追加変更なし |
| `mission_application/lifecycle.py:418`; `mission_application/acceptance.py:132`; `mission_application/verification_execution.py:44` | init reinitは共有canonical reasonを保持、status / verificationは共有load境界を使用。契約のsemantic shapeは既存frozen validatorで検査 |
| `mission_persistence/fenced_commit.py:557`; `mission_persistence/reinitialization.py:40`; `acceptance_contract.py:31` | record encoderは既存coded error、new-mission archive encoderは上記snapshot検査後、contract helperは既存closed validator / semantic error変換後。追加変更なし |

全authoritative CLI読取call siteも確認した。下表のCLI locatorは `skills/mission/bin/mission-state.py`。
診断collectorの既存skip / quarantineはsession成功と扱わず、その挙動を変更しない。

| authoritative reader hit | 判定 / 対応 |
|---|---|
| `mission-state.py:594`, `:610` | 全CLI読取をshared snapshotへ接続。legacy fallbackも新しいrenderability検査を通す |
| `mission-state.py:8073`, `:8696`, `:14043` | get / next / closeout: shared reasonを共通CLI rejectionへ渡す。v4/v5の公開CLI回帰で確認 |
| `mission-state.py:9250`, `:15098` | freshness / lane-report: 既存exit 2の表示でreasonが失われるRedを確認し、shared exceptionを表示。v4/v5で公開bytes / 制御値不変 |
| `mission-state.py:6373`, `:13975`, `:14816` | archive-worktree / finish sink / janitor: shared readが公開処理に先行し、coded errorで停止。追加分岐なし |
| `mission-state.py:8762`, `:8824` | stop-verdict: pending readは共通rejection、fact読取不能はread_errorへ渡し、既存 `authoritative-state-unreadable` block。成功へのfallbackはない |
| `mission-state.py:1767`, `:6953` | lease rejection診断 / peer identity: 前者は診断不能時も元のrejection、後者はlegacy JSON fallbackでsession metadataを扱う。completion / verifierの契約受理経路ではない |
| `mission-state.py:9403`, `:9527` | runtime readiness / permission preflight: 前者はunreadable診断、後者は既存write-unavailable blocker。shared load例外を捕捉し、contractを完了成功の根拠にしない。追加変更なし |
| `mission-state.py:14971`, `:15239` | list / stats: malformed recordを既存skip / `authoritative-state-unreadable` quarantineへ渡す。snapshotから不正契約を出力しない。追加変更なし |
| `state_snapshot.py:48`; `scripts/mission-audit.py:1158`, `:1163`, `:1330` | auditもshared strict / compatibility readerを通る。raw_document_copyも共有encoding検査後のsnapshotのみ。読取不能は既存quarantine。audit側の追加変更なし |
| `mission_persistence/aggregate_index.py:312`; `mission_application/worktree_archive_specs.py:54` | aggregate / archive: shared snapshot errorをauthority-unreadableまたは公開rejectionへ変換。aggregateのlegacy直読はidentity captureで、契約解釈 / 出力経路ではない |

別入力領域の未確認候補: `mission_application/verification_runner.py:65` はgit filename bytesの
UTF-8 decodeで、command fieldのparserではない。不正UTF-8のtracked filenameは本追補の
contract / policy入力とは別領域で、公開再現は未実施。範囲外として返し、修正しない。

### Red / Greenと検出価値

すべて `python3 -m pytest -q skills/mission/tests/test_issue879_completion_cli.py` に下表の
selectorを付けた。xdist未導入を確認し `-n` は使っていない。

| selector / 段階 | passed | failed | deselected | exit | 秒 |
|---|---:|---:|---:|---:|---:|
| `-k 'shared_validator or already_passed_closeout_validates'` baseline | 3 | 0 | 121 | 0 | 7.45 |
| `-k 'malformed_url or explicit_path_parser'` Red | 0 | 7 | 124 | 1 | 8.22 |
| 同selector Green | 7 | 0 | 124 | 0 | 10.19 |
| `-k 'surrogate or unencodable'` 初回Red | 19 | 13 | 125 | 1 | 41.80 |
| `-k 'authoritative_contract_reads and v4-flat and get'` 診断 | 0 | 1 | 156 | 1 | 1.11 |
| `-k 'surrogate or unencodable'` fixture / 期待理由調整後Red | 17 | 15 | 125 | 1 | 33.87 |
| 同selector 共有projection追加後の途中結果 | 30 | 2 | 125 | 1 | 32.95 |
| 同selector compatibility fallback修正後Green | 32 | 0 | 125 | 0 | 31.74 |
| `-k repro-surrogate` Red | 0 | 2 | 157 | 1 | 3.75 |
| `-k 'repro-surrogate or malformed_url or explicit_path_parser'` Green | 9 | 0 | 150 | 0 | 12.96 |
| `-k 'unencodable and (freshness or lane-report)'` Red | 0 | 4 | 159 | 1 | 4.28 |
| `-k closed_v5_inspection` Red | 0 | 1 | 163 | 1 | 1.11 |
| `-k 'closed_v5_inspection or codecs_keep'` Green | 7 | 0 | 157 | 0 | 11.14 |

最後のselectorに `skills/mission/tests/test_issue626_thin_adapter_guard.py` を併記したGreenは
4 passed / 0 failed / 235 deselected、exit 0、4.10秒。selectorのためguard自体は収集後除外され、
guardの実行証拠は下記の最終指定テストに含める。

URL Redの公開6ケースはexit 1 / internal-error、pure predicateはInvalid IPv6 URLのValueError。
Unicode Redはv5 closeout / already-passedのinternal-error、mark / forceのraw codec表示、
get / next / reinitの共有読取境界を再現した。初回のv4 get期待は診断成功を仮定していたため、
実際のexit 1を確認して拒否期待へ直した。調整後Redのうち2件は既存の理由コードから
共有canonical reasonへの期待変更であり、新しいinternal-errorの件数とは数えない。
途中の2 failuresはlegacy fallbackのget internal-errorとnext exit 0を示した。
replay本文Redは実JSON escape入力のencodeでexit 1、表示経路の4 failuresはexit 2だがreasonなしだった。
closed-v5 inspection Redはraw契約がencoding不能でもconsumer projectionだけを表示してexit 0となった。
auditのraw_document_copyへ同じ契約が届くことをsourceで確認し、shared snapshotにraw encoding検査を追加した。

既存v4/v5 fixture・command表・公開bytes / 制御値検査を拡張し、別の入力表は作らない。
argv / expectedは閉じたcommand validatorと契約canonical処理の異なる境界、既pass / force /
runner replayはそれぞれshortcut / provider / receiptの異なる副作用を検査する。
freshness / lane-reportは共有拒否を表示が消す欠陥を検出する。正常policy、contract-key-absent
legacy、既存receipt動作は指定回帰を維持する。subprocess費用は上表の実測。
thin adapterの2箇所はerror表示だけを変更し、baselineは増やさない。

この追補でもcommit / push / review CLIを実行しない。正式review / Checkerの再確認とrequired CIは
依頼元の次工程。commit案: `fix: verifier入力と永続状態のparser例外を理由付きで拒否する`
（実装・回帰・mirror）、`docs: parser境界の回帰と同形検索結果を記録する`（本書）。

指定11ファイルの最初の通し実行は343 passed / 3 failed、exit 1、304.73秒。
3件は既存closed-v5 codec拒否の診断文言の変化で、旧文言と理由コードを保持する修正を行った。
上記closed-v5 inspectionの追加とともに、次の同じ指定範囲を再実行した。full suiteは実行していない。

```sh
python3 -m pytest -q \
  skills/mission/tests/test_issue879_completion_cli.py \
  skills/mission/tests/test_issue877_acceptance_contract.py \
  skills/mission/tests/test_issue878_verification_runner.py \
  skills/mission/tests/test_issue878_candidate_snapshot.py \
  skills/mission/tests/test_issue632_transition_is_the_writer.py \
  skills/mission/tests/test_codex_wrapper_sync.py \
  skills/mission/tests/test_issue626_thin_adapter_guard.py \
  skills/mission/tests/test_python_module_inventory.py \
  skills/mission/tests/test_plugins_in_sync.py \
  skills/mission/tests/test_artifact_hygiene.py \
  skills/mission/tests/test_vendor_fingerprint.py
```

最終結果: **347 passed / 0 failed、exit 0、309.17秒**。実行中の編集なし。
正常policy / 契約キー欠落legacyの回帰、mirror一致、thin-adapter guard、module inventory、
衛生 / 語彙検査を含む。thin-adapter baselineは変更なし。HEADは開始時の `79ca75a` のまま。
正式review / Checker / required CIの再確認は未実施で、今回のローカルGreenをacceptedへ読み替えない。
