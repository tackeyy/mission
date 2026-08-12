# Issue #397 実装記録

## 境界

`mission-provider-result/1` の入力を検証し、raw result と検証済み canonical
candidate だけを原子的に記録する。plan の昇格、phase transition、executor handoff は
本 Issue の対象外とする。

## TDD テストリスト

- [ ] 1. 最小 `mission-plan/1` envelope、multi-step DAG、新規 project-relative path、exactly-one artifact
- [ ] 2. artifacts 0件・2件・plan と非plan の混在・duplicate plan を cardinality error として拒否
- [ ] 3. truncated / extra text / duplicate key / invalid UTF-8 / 4MiB 超過を拒否
- [ ] 4. duplicate step ID / unknown dependency / cycle / 空 acceptance check を拒否
- [ ] 5. traversal / symlink escape、typed URI / record / dataset、unknown resource/action type を検証
- [ ] 6. authority / provenance / mission metadata / control field injection と class/variant mismatch を拒否
- [ ] 7. canonical key order / whitespace の digest 同一性と array order / 1 byte 変更の差異
- [ ] 8. raw archive・canonical candidate・manifest と `provider_plan_imports` を同一 transaction で公開
- [ ] 9. contract のない exit 0 prose / preparation marker を plan として昇格させない
- [ ] 10. schema docs、plugin mirror、CLI smoke と focused tests

## 実装スライス

Red: `plan_contract` 未実装時に `ModuleNotFoundError` を確認した。

Green: strict UTF-8 / 4MiB / duplicate key / NaN 拒否、envelope binding、artifact
cardinality、capability attestation、typed scope、DAG、reserved authority field、canonical
serialization を実装した。`specialists plan-import` は current state の invocation,
preflight, selection, registry contract を再検証し、raw archive と canonical candidate を
publish transaction 内で作成してから state の `provider_plan_imports` pointer を公開する。
