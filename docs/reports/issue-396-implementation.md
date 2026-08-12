# Issue #396 実装記録

## 対象

外部 command provider を起動する前に、exact canonical outbound packet を
preflight と approval receipt に束縛する。plan import と executor handoff は
#397 以降の対象であり、本変更には含めない。

Base: `d52d3634f74d23fd73a235f7c3eb8467cc3b5c56`

## テストリストと実装スライス

- [x] 1. safe regular input の一回限り snapshot と canonical packet/digest
- [x] 2. secret/browser material を除く redaction、destination、quota、risk projection
- [x] 3. `prepare-invocation` dry-run は provider/browser/network を起動しない
- [x] 4. private preflight artifact と state pointer の原子的公開
- [x] 5. trusted isolator の strict attestation と declared-ambient の実行時隔離
- [x] 6. trusted verifier の approval evidence と scope/subject/expiry/nonce binding
- [x] 7. `awaiting-approval → approved → consuming → consumed` の single-use transition
- [x] 8. live 直前の再snapshotで payload/provider/selection/input/policy drift を拒否
- [x] 9. immutable canonical bytes だけを stdin に渡し、plugin mirror を同期

## 境界

`outbound_context_digest` は payload 関連 projection のみを対象とする。preflight
pointer、approval state、timestamp、通常auditなどの bookkeeping は同じ payload の
approval を自己失効させない。一方、mission本文、registry selection、input bytes、
destination、argv、risk scope、execution policy の変化は必ず失効させる。

strict execution は host-trusted `execution-isolator/1` の required capability
attestation がある場合だけ許可する。isolator/verifier がない場合、または証跡が
不正・期限切れ・scope不足の場合は process spawn なしで失敗する。declared-ambient
への切替は新しい preflight と scope別 approval なしには行わない。

## 検証記録

Red: `test_provider_preflight.py` を追加し、preflight module 未実装、続いて
direct command invocation が receipt なしで起動することを確認した。

Green:

- safe file snapshot、canonical packet、redaction、digest drift、receipt scope/subject/
  expiry/nonce、replayの純粋契約を追加した。
- `prepare-invocation` は private canonical packet を publish し、`verify-approval`
  は host user registry にpinされた verifierだけを子processで実行する。
- `invoke-command` は receipt と private packetを再検証し、approved → consuming を
  reservationと同じstate transactionで記録する。provider stdinにはprivate artifactと
  byte単位で一致するpacketだけを渡し、terminal時に consumedへ遷移する。

Focused Green:

- `test_provider_preflight.py`: 52 passed
- `test_plugins_in_sync.py`: 25 passed
- canonical / plugin mirror `py_compile`: passed

strict execution は host adapter の責務である。portable core は host-only registry の
source/version/policy/capability pin を検証し、strict packet を host backend 以外へ
dispatchしない。backendがない、不完全、またはpinがdriftした場合は spawn なしで拒否する。

## 受入条件カバレッジ

| 領域 | 直接テスト | 状態 |
| --- | --- | --- |
| dry-run spawn 0 / regular snapshot / redaction | preflight contract, unsafe input parameterization, prepare CLI | 実装済み |
| receipt scope・subject・expiry・nonce / unknown verifier | receipt contract, untrusted verifier CLI | 実装済み |
| exact stdin / replay / post-approval input mutation | trusted verifier E2E, replay, input drift | 実装済み |
| strict isolator attestation / ambient scope | execution-context contract | 実装済み（contract） |
| strict namespace・mount・networkのhost enforcement | host backend seam / strict dispatch test | host adapter contractとして実装済み |
