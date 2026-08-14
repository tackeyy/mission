親 Issue: #473（Wave 3 / 依存順 10–13 の前段・設計）

# 概要

親 Issue の Wave 3 は `MissionState` kernel の抽出、use case 分離、UnitOfWork 集約、adapter の依存逆転という 4 項目からなる。これらは**設計が確定していない状態で実装 Issue に分解しても、曖昧な指示で大規模改造を走らせることになる**。親 Issue 自身が「authority / state transition / persistence / compatibility を変更する場合は ADR を先に更新する」と定めているため、本 Issue で設計と ADR を確定させ、その結果を踏まえて実装子 Issue を分割する。

**本 Issue ではプロダクションコードを変更しない。**

# 前提（先に完了しているべきもの）

Wave 0〜2 の子 Issue。特に:

- Wave 0-1（artifact commit protocol）: UnitOfWork の commit 契約の下敷きになる
- Wave 2-8（schema 互換マトリクス）: v4 reader / v5 writer の双方向契約の土台になる

これらの結論が出る前に設計を固めると、後から覆る。

# 検討して確定させること

## 1. Typed Mission Kernel の境界

- `MissionState` を versioned typed aggregate として定義する。Phase / TerminalOutcome / Plan / Handoff / Review / Finding / Score / Lease を closed union で表現する（親 Issue 設計原則 1）
- `decide(state, command) -> Transition(new_state, events, effects)` の pure kernel に何を入れ、何を外に出すか
- `derive_next` を同じ transition table から生成する方法と、「提示する全 command sequence が実行可能である」ことをどう property test するか（親 Issue 設計原則 3）

## 2. UnitOfWork の protocol

親 Issue のセルフレビューで「例外時 rollback だけでは不十分」と明記されている。次をすべて 1 つの protocol として設計すること。

- staging
- lease / fencing precondition
- state generation CAS
- immutable generation
- commit record
- crash recovery
- unreferenced generation の GC

## 3. 移行戦略（strangler）

- 現行 CLI（16,000 行超）から、どの順序で何を切り出すか
- 各段階で既存 CLI と kernel が併存する期間の整合性をどう担保するか
- 途中で中断しても壊れない切り方になっているか

## 4. 実装子 Issue への分割案

- 1 capability / 1 trust boundary を原則として、実装可能な粒度に割る
- 各子 Issue の依存順と、それぞれの TDD 契約（何を Red にするか）
- **各子 Issue は、現行コードの該当箇所・期待挙動・テストリスト・受け入れ条件を含む形で書けるだけの具体性を持つこと**（本 Issue のアウトプットがそのまま起票内容になる）

# 成果物

1. ADR（新規、または ADR-002 の改訂）: kernel 境界と UnitOfWork protocol の決定と、採用しなかった選択肢の記録
2. 移行計画のドキュメント（`docs/` 配下）
3. 実装子 Issue の分割案（この Issue のコメントに、起票可能な粒度で記載）

# 受け入れ条件

- [ ] ADR が起票され、kernel 境界・UnitOfWork protocol・移行順序が決定として記録されている
- [ ] 採用しなかった案（全面リライト / DB・service 化 / 完全 event sourcing）を却下理由つきで記録している（親 Issue の非採用方針と整合すること）
- [ ] 実装子 Issue の分割案が、依存順・TDD 契約つきで提示されている
- [ ] プロダクションコードの変更が無い

# 非スコープ

- 実装そのもの
- DB / service 分割、完全 event sourcing、cloud 依存の導入（親 Issue Non-scope）
- provider への pass/review/score authority 付与（同上）

# 注意

この Issue は設計判断を伴うため、**安価モデルへの実装委譲には向かない**。設計は上位モデルが行い、確定後に切り出される実装子 Issue を委譲する想定。
