## 概要

変更内容と、その変更が必要な理由を書いてください。

## 変更種別

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Test improvement
- [ ] Refactoring
- [ ] Configuration or tooling change

## テスト

実行したコマンドと結果を書いてください。

```bash
cd skills/mission
python3 -m pytest -q
```

## チェックリスト

- [ ] self-review を実施した
- [ ] 関連ドキュメントを更新した
- [ ] 挙動変更に対するテストを追加または更新した
- [ ] 既存テストが local で pass した
- [ ] hook 変更時は `shellcheck scripts/mission-stop-guard.sh` を実行した
- [ ] user-visible behavior の変更を明確に説明した

## 補足

migration note、compatibility concern、reviewer に伝えたい context があれば書いてください。
