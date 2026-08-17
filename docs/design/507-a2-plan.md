# A2（#507）実装計画 — review / score / pass authority 境界の抽出

## 結論

現行 CLI に混在する review evidence の検証、score reduction、pass 判定を、薄い CLI adapter、application use case、typed kernel の三層へ分離する。既存の ADR-003 gate、v4 JSON、fenced lease、content-addressed evidence、rollback 契約は変更しない。

## Authority 境界

| 入力または判断 | 所有者 | 許可しないこと |
|---|---|---|
| review / manual score bytes の安全な取得 | adapter / port | provider が state、score、pass を直接決めること |
| immutable evidence ref の検証と score reduction | application use case | 未検証 path や事前決定済み pass の受理 |
| pass 条件と Transition | typed kernel | CLI や provider 内の独立した合否分岐 |
| v4 state と evidence の一体的 publish / rollback | `LegacyV4Repository` | lease admission 前の public write |

Legacy finding は status の欠落・任意文字列にかかわらず `open` として扱い、`open_high` は canonical な open High のみ数える。`resolved` を生成する command は本 Issue では追加しない。

## TDD 順序

1. review/manual evidence の malformed、duplicate key、読取中 mutation を staging 前に拒否する Red を追加する。
2. foreign lease、stale fence、expiry race で state と public evidence の bytes が不変である Red を追加する。
3. provenance、revision scope、findings、agreement、threshold、minimum item、artifact、required specialist gate の parity Red を追加する。
4. force pass の pinned approval と replay 拒否、open High の pass 不可を kernel Transition で Red にする。
5. application use case と v4 repository adapter を最小実装し、CLI を委譲だけにする。
6. 既存 review / score / pass suite、v1-v4 corpus、D1 recursive mirror gate を実行する。

## 非スコープ

- `resolved` finding を生成する新 command
- ADR-003 の tier または pass threshold の変更
- v5 `RecoverableUnitOfWork` への production cutover
- provider の権限拡大
- production release / activation

## 完了条件

- A2 の routed command は registry 上で一意の owner を持つ。
- CLI handler から review reduction と pass gate の決定ロジックが除かれる。
- pass Transition は kernel rule だけが生成する。
- 既存 assertion を弱めず対象 suite と full gate が Green になる。
- `skills/mission/` と `plugins/mission/skills/mission/` の対応ファイルが byte-identical になる。
- exact PR head に対する独立 Checker と CI が Green で、最新 base を統合後に merge する。
