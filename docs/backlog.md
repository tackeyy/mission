# Backlog

**ここは「いま着手しないと決めたもの」の置き場である。** Issue にすると WIP に積まれるだけで、
着手の宣言にならないものを置く（`~/.claude/rules/github-collaboration.md` §9）。

各項目は **何を / なぜ今やらないか / 何をもって再評価するか** の 3 つを書く。
着手が見えた時点で Issue を立て、ここから消す。

## evidence publish の UoW 取り込み（#711 / #747 の持ち越し）

#747 は子 Issue #763（PR #765）と #764（PR #787）の完了をもって閉じた。
下は #747 が保持していた持ち越しのうち、**その時点で 7 日以内に ready の PR にできないもの**である。

### 1. `ExecutionRequest` 入口へ typed command を通す

**何を**: 永続化の入口（`skills/mission/lib/mission_persistence/fenced_commit.py` の
`validate_execution_request`）は、audit の `command_type` と blob 集合しか受け取らない。
kernel の typed command が届かないため、blob がどの effect フィールド由来かを入口では判別できない。
#764 はこれを「公開される blob 集合の形」で近似して塞いだが、**別のセッションの `<segment>` を
指す手組みの binding は、いまも形だけでは弾けない**（#787 の設計「入口で塞がないもの」）。

**なぜ今やらないか**: v5 互換経路は command を `compatibility-mutation` という合成文書で包み、
`typed_command` も `None` で渡す（`legacy_v4.py`）。typed command を通すには `_request` の構成を
変える必要があり、`command-binding-mismatch` の検査と intent digest の計算が動く。
**記録済みの operation identity が動く変更**なので、影響範囲の調査から始まる。

**再評価の契機**: 手組みの binding を作れる主体が増えたとき（外部プロセスからの実行経路を足すとき）、
または intent digest を別の理由で変えるとき。

### 2. application 層の `reason` 振り分けが fall-through で既定を持つ

**何を**: projection の拒否理由を文言へ直す resolver が、既知の 3 理由以外を既定へ落とす。
4 つ目の理由が kernel に増えると、誤った文言で報告する（#762 の独立 Checker 指摘・Low）。

**なぜ今やらないか**: 現に 4 つ目は無く、増やす予定もない。増えた瞬間に誤るだけで、いまは誤らない。

**再評価の契機**: `ProjectionRejection` の `reason` を増やすとき。**その変更の中で直す。**

### 3. projection の判定順序がテストで固定されていない

**何を**: `canonical_generated_path` は progress 規則 → artifact 規則 → projection 規則の順に当てる。
順序を入れ替えても現在のテストは通る（規則同士が重ならないため）。

**なぜ今やらないか**: 規則が重ならない限り順序は観測できない。**重なりを禁じるほうが本質**で、
順序を固定すると「重なってもよい」と読める。

**再評価の契機**: repository 内の宛先規則を 3 つ目として足すとき。

### 4. guard の予算が、guard のロジックではなく環境の速度を測っている

**何を**: Stop guard の予算（8 秒）は壁時間で測るため、負荷が上がると `guard-budget-exhausted` になる。
guard を呼ぶテストが並列度の影響で落ちる。

**なぜ今やらないか**: #779（PR #780）が CLI の起動回数を 1 回にすることで、同じ症状の主因を
先に減らす。**そちらの効果を測ってから、予算の測り方を変えるかを決める。**

**再評価の契機**: #779 の merge 後に、同じ症状が残るかを実測したとき。

### 5. 残りの設計上の宿題

いずれも「いま困っていないが、触るときに一緒に決めること」。

- `resolve_projection_path` を kernel の内部名前空間へ置くか、種別や `validate_execution_request` の
  認可と結び付けるか
- evidence の type → prefix の所有をどこに置くか
- recovery の base 再構成
- `published/` の GC と保持期間
- 層をまたぐ root 名の一貫性（いまは `.mission-state` を複数の層がそれぞれ知っている）

**再評価の契機**: evidence の保存形式を変える作業に着手するとき。
