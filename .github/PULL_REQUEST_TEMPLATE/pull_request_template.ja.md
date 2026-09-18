## 概要

変更内容と、その変更が必要な理由を書いてください。

## 変更種別

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Test improvement
- [ ] Refactoring
- [ ] Configuration or tooling change

## テストの検出価値

判断基準: [Test Value Policy](https://github.com/tackeyy/mission/blob/main/AGENTS.md#test-value-policy).

<!-- 同じ根拠のケース群ごとに短く記載。
無関係な変更は下の箇条書きを N/A と理由に置換してよい。新規テストが不要なら十分な既存テストを示す。 -->

- 検出する不具合:
- 既存テストとの差 / 統合・削除後に残す保証:
- 実行・保守コスト（実測があれば記載、なければ未計測と明記）:

## テスト

実行したコマンドと結果を書いてください。

```bash
cd skills/mission
python3 -m pytest -q
```

## チェックリスト

- [ ] self-review を実施した
- [ ] 関連ドキュメントを更新した
- [ ] 検出価値ルールに沿って回帰の保証を確認し、「テストの検出価値」欄を記載した
- [ ] 既存テストが local で pass した
- [ ] hook 変更時は `shellcheck scripts/mission-stop-guard.sh` を実行した
- [ ] user-visible behavior の変更を明確に説明した

## 補足

migration note、compatibility concern、reviewer に伝えたい context があれば書いてください。
