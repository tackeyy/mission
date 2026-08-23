# #622 `_ISSUED_TRANSITIONS` registry 包含性の実測精査

## 結論

**削除不可。** `LocalFencedRepository.stage()` の decide-replay は、入力
`state` / `command` から正規 decision を再計算する一方、受け取った
`Transition` について比較するのは `new_state` と `events` だけである
（`skills/mission/lib/mission_persistence/fenced_commit.py:2089-2100`）。そのため、次の
registry 固有の検出クラスを包含しない。

1. 正規出力と値が同じ偽造 `Transition`。registry は object identity と weak reference を
   照合するが、decide-replay は生成元を観測しない
   （`skills/mission/lib/mission_kernel/transitions.py:560-565`）。
2. 異なる入力から同じ出力になる transition のすり替え。registry は元の state / command
   を保存して照合するが、decide-replay は出力の値しか比較しない
   （`skills/mission/lib/mission_kernel/transitions.py:568-577`）。
3. legacy claims の `before`。`transition_control_claim_bounds()` は registry が保持する入力
   state を直接読むため、registry を消すだけでは `before` の供給元が消える
   （`skills/mission/lib/mission_kernel/transitions.py:591-614`）。

ADR-006 は registry 削除を「decide-replay が包含すると確認できた場合」に限定している
（`docs/adr/006-kernel-reducer-adjudication.md:92-100`）。この前提が反例により偽なので、
本 Issue では registry と `decide()` の副作用を残し、下記の責務分担を ADR-006 追補として
記録する。

## 調査条件と方法

- 対象は `main` の `ba5a87c1e97ef72d45375dcefb52fccb31958ab7` を基点とする
  worktree である（調査時の branch 名は `investigate/622`、成果物確定時に
  `docs/622-registry-subsumption` へ改名）。
- `rg` により指定 6 symbol の canonical source、plugin mirror、テスト、設計文書の参照を
  全件抽出した。
- 反例は既存 fixture から実 `MissionState` / `AdmittedSnapshot` を作り、registry 判定だけを
  無効化した `stage()` で decide-replay 以降の gate を実行した。値同一の偽造 transition と、
  別 state 由来だが値同一の transition は、いずれも `_stage_persistence()` まで到達した。
  この到達条件そのものは `stage()` の gate 順序と比較対象から機械的に再現できる
  （`skills/mission/lib/mission_persistence/fenced_commit.py:2051-2115`）。
- 現行の fail-closed 挙動は既存テストでも固定されている。偽造拒否は
  `skills/mission/tests/test_issue511_p1_repository_binding.py:211-242`、別 state 拒否は
  `skills/mission/tests/test_issue511_p1_repository_binding.py:269-289`、claims の偽造拒否は
  `skills/mission/tests/test_issue630_transition_claims.py:94-105` と
  `skills/mission/tests/test_issue630_transition_claims.py:168-180` にある。

## 全参照箇所

### 直接参照

以下は指定された symbol の canonical source における全参照である（本調査書自身の引用は除く）。plugin mirror には同じ
相対行が byte-identical に存在する。再帰 inventory は canonical / plugin の全 Python module を
列挙し、byte 差を失敗にする
（`skills/mission/lib/mission_python_inventory.py:33-53`,
`skills/mission/lib/mission_python_inventory.py:56-76`）。実 repository を対象にする CI test は
`skills/mission/tests/test_plugins_in_sync.py:89-92` と
`skills/mission/tests/test_plugins_in_sync.py:629-631` である。

| symbol | 定義・内部参照 | canonical source の外部参照 | テスト上の参照・観測箇所 |
|---|---|---|---|
| `_ISSUED_TRANSITIONS` | 宣言 `transitions.py:539-541`、weakref cleanup `:554-557`、seal lookup `:564-565`、binding lookup `:576-577`、claims before lookup `:604-605`、effects binding 元情報 lookup `:634-636` | なし | なし |
| `_register_transition` | `decide()` から登録 `transitions.py:527-535`、定義 `:544-557`、effects-bound transition 再登録 `:627-636` | なし | なし |
| `is_sealed_transition` | 定義 `transitions.py:560-565`、binding 前提 `:574-576`、claims 前提 `:601-604`、effects binding 前提 `:632-634` | import `fenced_commit.py:35-43`、public stage `:2051-2055` | 契約説明 `test_issue503_fenced_commit.py:1120-1125`、偽造実体の拒否 `test_issue511_p1_repository_binding.py:233-242` |
| `is_transition_bound_to` | 定義 `transitions.py:568-577` | import `fenced_commit.py:35-43`、public stage `:2075-2088` | 別 state 由来 transition の拒否 `test_issue511_p1_repository_binding.py:269-289` |
| `transition_control_claim_bounds` | 定義 `transitions.py:591-614`、`transition_control_claims()` の委譲先 `:617-624` | import `legacy_v4.py:20-26`、legacy claims 適用 `:46-63` | `transition_control_claims()` 経由の delta / forged tests `test_issue630_transition_claims.py:65-105` |
| `bind_transition_effects` | 定義 `transitions.py:627-636` | imports `fenced_commit.py:35-43`, `legacy_v4.py:20-26`、v5 typed execute `fenced_commit.py:2171-2177`、v4 typed execute `legacy_v4.py:266-271` | import と明示 binding `test_issue511_p1_repository_binding.py:41`, `:363-392` |

上表の `transitions.py` は
`skills/mission/lib/mission_kernel/transitions.py`、`fenced_commit.py` は
`skills/mission/lib/mission_persistence/fenced_commit.py`、`legacy_v4.py` は
`skills/mission/lib/mission_persistence/legacy_v4.py` を表す。plugin 側の対応ファイルは次の 3 件で、
同一 symbol が同一行にある。

- `plugins/mission/skills/mission/lib/mission_kernel/transitions.py:534-636`
- `plugins/mission/skills/mission/lib/mission_persistence/fenced_commit.py:35-43`, `:2045-2177`
- `plugins/mission/skills/mission/lib/mission_persistence/legacy_v4.py:20-26`, `:46-63`, `:266-271`

既存設計文書の文字列参照は、`transition_control_claim_bounds` が
`docs/design/632-transition-is-the-writer.md:8-12`、`_ISSUED_TRANSITIONS` が同 `:82-88`
である。

### registry への間接依存

`transition_control_claims()` は `transition_control_claim_bounds()` を必ず呼ぶため
（`skills/mission/lib/mission_kernel/transitions.py:617-624`）、次の参照も registry の間接 consumer
である。

- `skills/mission/tests/test_issue618_kernel_a2_review.py:19-27`
- `skills/mission/tests/test_issue620_kernel_a5_c1.py:119-132`
- `skills/mission/tests/test_issue630_transition_claims.py:65-105`, `:314-355`
- `skills/mission/tests/test_issue631_real_state_halt.py:112-117`
- `skills/mission/tests/test_issue632_transition_is_the_writer.py:666-684`, `:742-761`
- `docs/design/632-transition-is-the-writer.md:8-18`, `:174-177`

`Transition._input_state` と `Transition._command` にも登録時の値が書かれるが
（`skills/mission/lib/mission_kernel/transitions.py:58-67`, `:544-551`）、production code はそれらを
読み返さず、照合と claims は registry tuple を読む
（`skills/mission/lib/mission_kernel/transitions.py:576-577`, `:604-605`）。したがって、registry を
削除して private field に置換する案は、`decide()` の副作用を残したまま、identity 検証だけを失う。

## decide-replay の実測データフロー

`LocalFencedRepository.stage()` の順序は次のとおりである。

1. `AdmittedSnapshot` と sealed transition を要求する
   （`skills/mission/lib/mission_persistence/fenced_commit.py:2045-2055`）。
2. request と blobs の一致、blob set の妥当性、target state の型・session lineage・pending lease を
   検証する（同 `:2056-2070`）。
3. admitted base と typed command から `admitted_state` を再構成し、registry の
   `is_transition_bound_to()` で受領 transition の生成元を照合する（同 `:2071-2088`）。
4. 同じ `admitted_state` / typed command で `decide()` を再実行し、accepted、transition 存在、
   `new_state`、`events` を比較する（同 `:2089-2100`）。**`effects`、object identity、入力 state、
   command はこの比較式に含まれない。**
5. audit event categories と `transition.events` を比較し、effects の型と captured blob bindings との
   完全一致を別 gate で検証する（同 `:2101-2115`）。
6. canonical encoding 後に private `_stage_persistence()` へ渡す（同 `:2116-2137`）。

`execute()` は一度目の `decide()` を行い、必要なら effects を binding して public `stage()` へ渡すため、
通常の typed v5 新規実行では上記の二度目の decide が走る
（`skills/mission/lib/mission_persistence/fenced_commit.py:2139-2179`）。

## decide-replay が走らない経路

| 経路 | 実測した挙動 | 一次証拠 |
|---|---|---|
| operation-id の冪等 replay | `begin()` が既存 `CommitResult` を返し、`execute()` は decision / stage 前に返る | `fenced_commit.py:1984-1997`, `:2143-2149` |
| v5 genesis | typed command を持たない validated bytes を private stage へ直接渡す | `fenced_commit.py:2018-2043` |
| legacy v4 typed request | admitted state で `decide()` は一度だけ。二度目の canonical decision 比較はなく、target を直接 project する | `legacy_v4.py:213-277` |
| legacy v4 callback execute | mutation 後に `_apply_transition_claims()` を呼ぶだけ。そこでは registry 由来の before / after を使う | `legacy_v4.py:193-211`, `:46-91` |
| v5 compatibility callback execute/save | claims 適用後、public `stage()` ではなく private `_stage_persistence()` を直接呼ぶ | `legacy_v4.py:507-545` |
| administrative commit | `Transition` / `decide()` を引数に持たず、identity-checked read、validate、mutation、record identity 再確認、atomic write の独立 protocol | `skills/mission/lib/mission_persistence/administrative.py:78-110` |

ここで `v5 compatibility` は保存先が v5 でも decide-replay を通るとは限らないことを示す。
CLI の repository selector は legacy と `V5CompatibilityRepository` を実際に切り替える
（`skills/mission/bin/mission-state.py:9544-9589`）。また ADR-006 自身も administrative command の
kernel 化を Batch 3 としており、現時点で Batch 2 の replay 包含範囲には入れていない
（`docs/adr/006-kernel-reducer-adjudication.md:98-103`）。

## 違反クラスと包含判定

判定語は次の意味で使う。

- **検出**: 当該機構だけで違反を拒否できる。
- **非検出**: 値同一の反例があり、当該機構だけでは拒否できない。
- **別 gate**: decide-replay ではない検証が最終状態を拒否する。

| 違反クラス | registry | decide-replay | 判定と反例 | 一次証拠 |
|---|---|---|---|---|
| decide 由来でない偽造 `Transition` | 検出 | **非検出** | 正規 transition の `new_state` / `events` / `effects` を値コピーした新規 object は registry identity がない。一方 replay 比較対象は同値なので通る | `transitions.py:58-67`, `:560-565`; `fenced_commit.py:2089-2100`; 現行拒否 test `test_issue511_p1_repository_binding.py:233-242` |
| 別 state 由来 transition のすり替え | 検出 | **非検出** | `MarkHalt` は入力 phase が planning / executing のどちらでも、他値が同じなら phase を halted に上書きして同じ出力を作る。registry は入力 state の不一致を拒否するが replay の出力比較は同値 | `transitions.py:230-249`, `:568-577`; `fenced_commit.py:2075-2100` |
| 別 command 由来 transition のすり替え | 検出 | **非検出** | `Reactivate.reason` は妥当性確認されるが出力 state / event に保持されないため、異なる有効 reason の command が同じ出力を作る。registry は command equality を照合する | `transitions.py:266-290`, `:568-577`; `fenced_commit.py:2080-2100` |
| 発行後の `new_state` / `events` 改変 | 非検出 | 検出 | seal と registry entry は同じ object の field 改変を観測しない。再実行した正規出力との値差が拒否する | `transitions.py:560-577`; `fenced_commit.py:2089-2100`; test `test_issue511_p1_repository_binding.py:292-317` |
| claims `before` の出所改変・欠落 | 検出 | **非検出** | `before` は registry の入力 state だけに存在する。planning / executing から同一 halted 出力へ収束する反例では、出力 replay から元 phase を逆算できない | `transitions.py:591-614`, `:230-249`; `legacy_v4.py:75-91` |
| compatibility writer が before / after 以外の第三値を書く | registry 由来 bounds で検出 | 対象経路では走らない | legacy `_apply_transition_claims()` が第三値を `transition-divergence` として拒否する | `legacy_v4.py:75-87`; test `test_issue630_transition_claims.py:134-149` |
| effects binding の再登録 | **非検出** | **非検出** | `bind_transition_effects()` は sealed transition なら effects が既に空でないかを確認せず、新 transition を毎回 registry へ登録する。同じ effects の二重 binding は履歴上区別不能 | `transitions.py:627-636`; `fenced_commit.py:2089-2115` |
| 最終 effects と captured blobs の不一致 | helper は tuple 型のみ検出 | 別 gate | replay 比較は effects を見ないが、直後の blob equality gate が不一致を拒否する。legacy typed request にも同じ最終 equality がある | `transitions.py:627-636`; `fenced_commit.py:2106-2115`; `legacy_v4.py:278-287` |
| 同一 operation の再実行 | 対象外 | decide-replay は走らない | operation record の intent 照合と `CommitResult` fast path が冪等性を担う。transition registry の責務ではない | `fenced_commit.py:1984-1997`, `:2147-2149` |
| 並行 commit の stale winner | 対象外 | stage 後の commit gate | commit lock、precondition、stage authority の一回消費、head CAS / lease 再検証が担う | `fenced_commit.py:4200-4231`, `:4280-4290` |

`effects binding の再登録` は「registry だけが守る保証」ではなく、現行にも single-bind invariant が
存在しないことが実測結果である。したがってこの行は包含失敗の根拠には数えないが、registry 削除を
理由に誤って「再 binding も防げた」と主張してはならない。必要な契約が「最終 effects が verified blobs
と一致すること」だけなら現行の別 gate が満たす。single-bind 自体を契約化するなら別 Issue で明示的な
状態または API 境界が必要である。

## `before` の代替設計評価

registry を消して `transition._input_state` を読む案は採らない。`_input_state` は現在
`object.__setattr__()` で後付けされるため `decide()` の純粋化にならず、production の authenticity
判定にも使われていない
（`skills/mission/lib/mission_kernel/transitions.py:544-557`, `:560-565`）。

将来 registry を外す最小の安全な設計は次の二段階である。

1. **transition を repository 外から受け取らない。** typed 経路は repository が transaction 内で
   確定した state と request command から一度だけ `decide()` し、その戻り値を直接 stage する。
   呼び出し元が渡した transition を再照合する構造自体をなくせば、偽造と state / command すり替えは
   API 上表現できない。現状の `execute()` は既に admitted state から decision を作るが、その後 public
   `stage()` に transition を渡して再実行している（`fenced_commit.py:2152-2178`）。
2. **legacy claims を終わらせるか、入力 state を明示引数にする。** 推奨は Batch 2 の目的どおり
   compatibility mutation callback を廃止して `transition.new_state` を書き、before / third-value 判定を
   不要にすること。移行中だけ残すなら、同一 transaction で読んだ typed input state を
   `_apply_transition_claims(input_state, transition, proposed)` へ明示的に渡し、`before` をそこから導出する。
   `Transition` の private field や process-global side table を新しい管理元にしない。現行 claims が
   compatibility callback の後に適用されることは `legacy_v4.py:193-211` と `:507-514` に表れている。

この代替へ移るまでは、registry と decide-replay は重複ではなく、入力 provenance と出力再計算という
異なる責務を持つ。

## ADR-006 追補との対応

本調査の判定は、ADR-006 末尾の「批2-b の issued-transition registry 包含性判定」追補に記録した。
追補は、現 ADR の「確認できた場合に削除」という条件
（`docs/adr/006-kernel-reducer-adjudication.md:92-100`）が成立しなかったことを確定し、無条件削除を
意味する Consequences 表現（同 `:135-136`）を実測結果に合わせて限定する。違反クラスごとの反例と
一次証拠は本設計書を詳細記録とし、ADR には責務分担と将来の削除条件だけを残す。

## #622 のクローズ方針

#622 は「registry 削除の実装 Issue」としてではなく、**包含性を精査して fail-closed の分岐を確定した
設計 Issue**として閉じる。

受け入れ証拠は次の 4 点とする。

1. 本文書の全参照 inventory と、偽造・別 state・別 command の値同一反例。
2. 「削除不可」と、registry / decide-replay / effects / administrative protocol の責務分担。
3. ADR-006 末尾の追補。既存の Accepted な決定は書き換えず、条件分岐の結果として記録する。
4. 将来の削除前提を別 Issue に切り出す場合は、repository 内 decision、legacy `before` 廃止または
   explicit 化、single-bind を契約化するか否か、source/plugin mirror を受け入れ条件にする。

この結論では production code、canonical source、plugin mirror のいずれも変更しない。

## 検証結果

- 対象 6 symbol の `rg` 全件確認: canonical と plugin mirror 以外の production 参照は
  `fenced_commit.py` と `legacy_v4.py` に限定され、直接テスト参照は上記 inventory のとおり。
- 反例 probe:
  - 値同一 forged transition: `is_sealed_transition == False`、replay 比較 field は同一、registry 判定を
    外した `stage()` は accepted（`transitions.py:560-565`; `fenced_commit.py:2089-2100`）。
  - planning と executing の別入力からの `MarkHalt`: `is_transition_bound_to == False`、
    `new_state/events` は同一、registry 判定を外した `stage()` は accepted。claims の phase before は
    planning と executing に分岐（`transitions.py:230-249`, `:568-577`, `:591-614`）。
  - reason の異なる二つの `Reactivate`: command は不一致、`new_state/events` は同一、registry binding は
    不一致（`transitions.py:266-290`, `:568-577`）。
  - 同一 effects の二重 `bind_transition_effects()`: 二回目も sealed transition として accepted
    （`transitions.py:627-636`）。
- focused regression: 9 tests passed。対象は public stage の seal / state binding / output replay /
  effects、legacy claims の delta / forged / divergence、inventory 差分検出。
- 実 repository の recursive source/plugin inventory: 1 test passed。この gate は全 canonical Python
  module の plugin mirror 存在と byte equality を検証する
  （`skills/mission/tests/test_plugins_in_sync.py:629-631`）。
- production code、canonical source、plugin mirror は変更していない。

## 最終判定

**結論: 削除不可**
