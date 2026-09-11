# 設計 v2: replay が元の operation の結果を再現しない（tackeyy/mission #773）

round 1 で 3 件の High が出て、**D1 の根拠・D3 の前提・D5 と D2 の関係**がいずれも誤りだと
実装で示された。v2 はその 3 つを直したもので、**D3 は撤回した。**

## 現象

同じ operation id で `executor-handoff` を再実行したとき、その間に handoff の status が
変わっていると、**元の結果ではなく失敗が返る。**

```
begin(op-begin)            -> rc=0, handoff は consuming
abort(op-abort)            -> rc=0, handoff は rejected
begin(op-begin) をもう一度  -> rc=2, ERROR: executor handoff rejected: handoff-binding-invalid
```

3 度目は 1 度目の replay なので、**1 度目と同じ stdout を rc=0 で返す**のが正しい。
head は動いていない（replay は commit しない）ので、そこは壊れていない。

**abort を使わなくても再現する。** canonical drift で `rejected` にしても同じで、
main `f3648c8` と `d451452` の両方で確認した。

## 原因（実測）

### 1. `prepare` は replay 検出より**前**に走る

v5 executor は "A' order: read without admitting, prepare, then admit" という順序を持つ
（`legacy_v4.py` の当該コメント）。admission に渡す blob set が prepare の `effects` から
作られるため、prepare を先に走らせる必要がある。

replay かどうかは `self._repository.begin(request)` が `OperationReplay` を返すかで決まり、
これは admission の中である。したがって prepare の実行時点で `operation_replayed` は
まだ `False` である。

**実測**: 上の repro の 3 度目で、adapter に一時的な出力を入れて確かめた。

```
PROBE replayed=False source='fresh'
```

`fresh` を選んでいるので、**replay なのに現在の state に対する検証経路が走っている。**

### 2. `prepare` が現在の state に対して検証している

adapter の `fresh_facts` は `canonical_plan_identity(...)`（plan ファイルの読み取りと
digest 照合）と `verify_handoff_binding(...)`（**現在の** handoff と plan の一致）を行う。
上の repro で落ちているのは後者で、3 度目の時点で handoff は `rejected` になっている。

### 3. 応答が `execution.projection`（現在の head）から組まれている

`executor_handoff_response` は `execution.projection` を読む。`LegacyCommandExecutionResult`
は replay のために `replayed_state`（その operation が commit した状態）を渡しており、
宣言にも「the caller reconstructs the original result from it」と書かれている
（`ports.py` の同フィールドのコメント）。**その値を使っていない。**

### 4. `prepared.result` も現在の prepare から来ている

`executor_handoff_response` は `prepared.result["rejection"]` を読む。replay のときも
**その回の prepare が作った値**であり、元の operation のものではない
（round 1 の High 3 で指摘され、`planning.py` の当該箇所で確認した）。

**3 と 4 は別の入口である。** projection だけ差し替えても 4 が残る。

## 決定

### D1. replay では prepare の判断を結果にしない

**replay は何も commit しないので、prepare が現在の state に対して下した判断は
すべて無関係である。** 失敗も同じで、replay と分かった時点で捨てる。

順序（prepare → admit）は変えない。#747 が「admission は prepare が作ったものを運ぶ」と
決めており、戻すと publish されたファイルが unit of work の外に出る。

代わりに **prepare の失敗を遅延させる。**

1. `run_executor_handoff` が prepare を包み、`rejected=(...)` に列挙された型の例外を
   **捕まえて保持する**（`passthrough=(...)` は従来どおり即座に抜ける）
2. seam へは、**kernel が必ず拒否する placeholder** を prepared として渡す
3. **replay だった** → 保持した例外を捨て、`replayed_state` から応答を組む
4. **replay でなかった** → kernel の拒否ではなく、**保持した元の例外**を送出する

**placeholder に求める性質**（round 2 の Medium）。

- **kernel が必ず拒否する。** `decide` が accepted を返す余地を持たない。
  現行の executor は decision が accepted なら `save` へ進むので、ここが破れると
  **元の例外を握り潰したまま何かを commit する**
- **effects は空。** executor-handoff と同じ空集合にすることで、
  `_assert_replay_materializes` の比較が双方 `None` になり干渉しない
- **非 replay 経路で外へ出るのは kernel の拒否ではなく、保持した元の例外**。
  拒否理由が入れ替わると、呼び出し側が読むエラーが変わる

#### なぜ replay 判定が prepare の失敗に影響されないか（round 1 High 1 の訂正）

v1 は「prepare が失敗すれば blob が無いので intent digest が一致せず、replay と誤認
されない」と書いた。**これは誤りだった。** `compute_intent_digest` は **generated blob を
意図的に除外する**（`fenced_commit.py` の同関数の docstring: 「Generated blobs are what
prepare produces, so folding them made the same request a different operation before and
after prepare」）。

正しい説明は逆である。

**intent digest の入力は prepare の出力に一切依存しない。**
`session_id` / `lease_owner_session_id` / `operation_id` / `command` / **captured** blob の
5 つで、`command` は adapter が repository 構築時に渡す `_operation_command` である
（`legacy_v4.py` の `_request`）。

したがって **同じ invocation なら、prepare が成功しても失敗しても request identity は同じ**
になる。これが遅延が成立する理由であり、**同時に、それが正しい振る舞いでもある** —
その operation id は既に commit されているので、答えは記録された結果である。
いま prepare が失敗するかどうかは無関係である。

**別の intent が同じ operation id を使い回す**ことへの防壁は `command` が digest に
入っていることと `_assert_replay_materializes` であり、**遅延はどちらも弱めない**
（digest は元から prepare に依存していない）。

したがって **v1 の受け入れ条件 6「prepare が失敗した実行が、成功した実行の replay として
扱われない」は誤りなので撤回する。** 正しい条件は「**同じ intent の retry は、prepare が
いま失敗するかどうかに関わらず、記録された結果を返す**」である。

#### 適用範囲を executor-handoff に限る

**遅延は共有 seam（`legacy_v4.py` の v5 executor）ではなく、`run_executor_handoff` に置く。**

generated blob を持つ command 族では、prepare が失敗すると blob set が空になり、
base が動いていない場合に `_assert_replay_materializes` が記録された materialization と
比較する（`legacy_v4.py` の同メソッド。base が動いていれば比較しない）。
**その族で遅延が何を意味するかは本 Issue で決めない。**

executor-handoff の `effects` は常に空なので、この問いは生じない。

### D2. 応答は replay のとき、**現在の prepare から何も読まない**

replay のときは次の 2 つを**両方**切り替える（round 1 High 3 の訂正）。

| 入口 | 通常 | replay |
|---|---|---|
| projection | `execution.projection` | **`execution.replayed_state`** |
| rejection | `prepared.result["rejection"]` | **読まない** |

`replayed_state` は既に `project_legacy_document` を通した形なので変換は要らない。

#### replay で成功と失敗をどう区別するか

記録されているのは**状態だけ**で、元の応答は保存されていない。**historical な handoff の
status と理由コードから復元する。**

| historical な handoff | 判定 |
|---|---|
| `consuming` / `consumed` | 成功 |
| `rejected` かつ `rejected_reason` が **`CanonicalPlanRejectionCode` の値** | **その理由で失敗** |
| `rejected` かつ `rejected_reason` が **`HandoffAbortReason` の値** | 成功（abort が求めた結果） |
| `prepared` | 成功（`verify-step` は handoff を変えない） |

**判定は閉じた集合への所属で行う。前置き（`canonical-` で始まるか）で判定しない。**
2 つの enum は値が交わらないことを #767 でテストに固定しており、所属検査ならその保証を
そのまま使える。前置きだと、将来どちらかに別形式の値が入ったときに黙って誤判定する。

**`rejected_reason` がどちらの集合にも無い場合は失敗として扱う**（fail-closed）。

#### `operation` は現在の invocation から取る

応答の `operation` は historical state からは復元できない。`consuming` な handoff は
**成功した `begin` / `verify` / `record` を区別しない**（round 2 の Medium）。

**replay でも `operation` は現在の invocation のもの**（CLI が受け取ったサブコマンド、
すなわち adapter が組んだ `ExecutorHandoffRequest.operation`）を使う。
`prepared.result["operation"]` は読まない。

**これが元の operation と一致することは、replay の成立条件から従う。** intent digest は
`command` を折り込み、`command` は adapter が `EXECUTOR_HANDOFF_COMMAND_NAMES[operation]` と
その引数から組む `_operation_command` である。**operation が違えば command が違い、
digest が違うので replay にならない。**

**したがって「現在の prepare から何も読まない」は「現在の invocation から何も読まない」
ではない。** 読まないのは **prepare が state を見て作った値**（`rejection` と projection）で
あり、**呼び出し側が入力として与えた値**（operation）は読んでよい。

### D3.（撤回）adapter の `verify_handoff_binding` は外さない

v1 は「kernel が同じ検査を完全に行っているので adapter から外す」と書いた。
**round 1 の High 2 で、そうではないと実装で示された。**

| 検査 | adapter (`planning.py`) | kernel / codec |
|---|---|---|
| status ごとの**厳密なキー集合** | あり（`_closed_handoff_wire`） | **なし**（必須キーの存在のみ。余剰キーを拒否しない） |
| `handoff_id` / step id の**識別子形式** | あり（`_IDENTIFIER.fullmatch`） | **なし**（非空文字列まで） |
| schema の固定値 | あり | 非空文字列まで |
| plan binding の一致 | あり | あり |

**外すと検査が緩む。** そして **外す必要も無い** — D1 の遅延により、replay ではこの検査の
失敗が結果に影響しなくなる。

**adapter が判断を持つこと自体は ADR-006 と緊張関係にあるが、それは本 Issue の対象外とする。**
解くなら「不足分を codec へ移してから adapter を薄くする」であり、**検査を緩める方向へ
倒れる変更を replay の修正と同じ PR に混ぜない。**

### D4. `canonical_plan_identity` の失敗も D1 で扱う

plan ファイルが読めない・digest が合わない場合、adapter は `canonical-*` の例外を投げる。
これは plan を読む必要がある操作の正当な失敗なので、個別の対処は足さない。
replay のときだけ無関係になるので D1 の遅延で扱う。

### D5. `begin` / `verify` の canonical drift 記録は、replay では成立させない

adapter は `operation in {"begin", "verify"}` かつ `canonical-` で始まる失敗のとき、
例外ではなく **handoff を `rejected` にする transition** を返す
（`prepare_executor_handoff_rejection`）。**これは D1 の遅延の対象ではない**（例外として
抜けないため）。

v1 はここで止めていたが、**replay ではこの transition も捨てなければならない**
（round 1 High 3）。replay では commit されないので state は変わらないが、
`prepared.result["rejection"]` が残るため、D2 の「rejection を読まない」が無いと
**元の operation が成功していても失敗が返る。**

D2 がこれを吸収するので、D5 で追加の機構は要らない。**ただし D2 と D5 は同時に入れる**
必要がある（片方だけでは replay が壊れたままになる）。

## 対象外

- admission の順序（prepare → admit）の変更。#747 が決めた契約を戻さない
- replay の識別（operation id の生成・照合・intent digest の構成）の意味論
- generated blob を持つ command 族での遅延の意味（D1 の適用範囲に含めない）
- adapter の検査を codec へ移す作業（D3 で撤回した）
- `decide_executor_handoff`（現在どこからも呼ばれていない legacy 関数）
- v4 retained 経路。operation id を持たないので replay が存在しない

## 受け入れ条件

1. `begin(op-b)` → canonical drift で `rejected` → `begin(op-b)` が、**1 度目と同じ stdout** を
   `rc=0` で返す
2. `begin(op-b)` → `abort(op-a)` → `begin(op-b)` が、同じく 1 度目と同じ stdout を返す
3. **元の operation が canonical drift で rejected になっていた場合**、その replay は
   **その理由で失敗する**（成功に化けない）
4. **元の operation が abort だった場合**、その replay は成功する（#767 で入れた免除が
   historical state 経由でも効く）
5. replay は何も commit しない（head が動かない）
6. **同じ intent の retry は、prepare がいま失敗するかどうかに関わらず記録された結果を返す**
   （v1 の条件 6 を訂正したもの）
7. **異なる intent が同じ operation id を使うと、replay として扱われない**
   （`command` が digest に入っていることの確認。遅延で弱まっていないこと）
8. `rejected_reason` がどちらの enum にも無い値のとき、**失敗として扱う**（fail-closed）
9. **replay の応答の `operation` が、現在の invocation のサブコマンドと一致する**
10. **placeholder は kernel に必ず拒否される。** 非 replay 経路で accepted になることがない
11. **非 replay 経路で head が動かない**（placeholder が何も commit しない）
12. **非 replay 経路で外へ出るのは、kernel の拒否ではなく保持した元の例外**である
13. 上のそれぞれについて、**壊す変異を注入して落ちることを確認する**。
    とくに 10 は「placeholder を accepted になりうる command へ差し替える」変異で確認する
14. v5 で固定する（v4 retained には replay が無い）

## 分割

一次関心事は「replay の意味論」1 つ。D1 / D2 / D5 は同じ原因から導かれ、**分割すると
中間状態が壊れる**（D2 だけ入れると replay は依然 prepare で失敗し、D1 だけ入れると
replay が現在の rejection を返す）。1 PR とする。

D3 は撤回したので変更に含まれない。

## 未確認

- D1 の placeholder を具体的にどの command 型にするかは実装時に決める。
  **満たすべき性質は D1 に列挙したので、判断が分岐する余地は無い**
- v5 executor を共有する他の command 族（evidence handoff・artifact・progress）が同じ形の
  欠陥を持つかは**未調査**。本 Issue は executor-handoff に限る
