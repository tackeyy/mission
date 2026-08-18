# Issue #543 C2 Stage A iteration 4 検証記録

## 結論

repository command guard を、forbidden callable が解析対象 AST 内で字句的に到達する container subscript、Call wrapper、静的 binding に対して fail-closed にした。C2 の fencing 契約と配布実装は変更していない。

## TDD 証跡

- dict subscript: 修正前は expected violation に対して `[]` となり Red。
- list subscript: 修正前は expected violation に対して `[]` となり Red。
- module/local/direct Call wrapper、comprehension、unknown expression、default parameter、destructuring、mutation、variadic forwarding は各 synthetic test を Red にしてから Green 化。
- 正常系は parameter shadow、comprehension scope、tuple positional binding で誤検出しないことを固定。

## M-2

allowlist を空にした実ソースの call-site 集合と allowlist を照合し、stale entry があれば Red になるテストを追加した。独立 Checker は fake stale entry の注入で Red を確認した。

## 保証境界

静的 AST で追跡できる binding を保証対象とする。`getattr` 等の runtime dispatch、`eval` / `exec`、解析 AST 外にある imported callable の実装は非保証として guard docstring に明記した。

## 検証

- inventory: `63 passed`
- mirror sync / artifact hygiene / vocabulary guard: `42 passed`
- independent adversarial Checker: Accepted、未解決 finding なし
- full suite: `4147 passed in 358.34s (0:05:58)`、failed 0
