# Issue #396 実装記録

## 対象

外部 command provider を起動する前に、exact canonical outbound packet を
preflight と approval receipt に束縛する。plan import と executor handoff は
#397 以降の対象であり、本変更には含めない。

Base: `d52d3634f74d23fd73a235f7c3eb8467cc3b5c56`

## テストリストと実装スライス

- [ ] 1. safe regular input の一回限り snapshot と canonical packet/digest
- [ ] 2. secret/browser material を除く redaction、destination、quota、risk projection
- [ ] 3. `prepare-invocation` dry-run は provider/browser/network を起動しない
- [ ] 4. private preflight artifact と state pointer の原子的公開
- [ ] 5. trusted isolator の strict attestation と declared-ambient の明示的 fail-closed
- [ ] 6. trusted verifier の approval evidence と scope/subject/expiry/nonce binding
- [ ] 7. `awaiting-approval → approved → consuming → consumed` の single-use transition
- [ ] 8. live 直前の再snapshotで payload/provider/selection/input/policy drift を拒否
- [ ] 9. immutable canonical bytes だけを stdin に渡し、plugin mirror と safe-default docs を同期

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

Red/Green および最終focused testの結果は実装完了時に追記する。
