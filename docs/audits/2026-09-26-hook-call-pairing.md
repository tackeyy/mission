# Hook recorderの呼び出し対応評価

[Issue 812](https://github.com/tackeyy/mission/issues/812) の評価記録。
基準は [`f0baddfb753c`](https://github.com/tackeyy/mission/commit/f0baddfb753c53aa9a51a3781076b6d1e61d8aad)、評価日は2026-09-26 JST。

## 結論

現行の一回起動契約を保つ範囲では、recorderへのPID/call ID追加は不要と判断する。
phaseだけの対応付けには交錯時の限界があるが、交錯に必要な二回目のCLI呼び出しを既存の起動数検査が拒否する。
この結論は、あらゆる並行writeを観測できるという意味ではない。

## 観測と既存の防御

| 対象 | 実際の契約・評価結果 |
|---|---|
| recorder | CLI前後の`phase`とtreeを記録する。PID/call IDは記録しない。 |
| gap comparator | 隣接する`entry → exit`を除外し、それ以外の隣接点の残存差分を比較する。 |
| 通常の一回起動 | `before → entry`、`exit → after`の差分を検出し、CLI内部のwriteを除外する。既存のgap自己検証を実行して確認した。 |
| 合成交錯 | `A entry → B entry → write → A exit → B exit`を与えると差分は空。`B entry → A exit`も除外される。実launcherでこの順序が発生した測定ではない。 |
| runtime count | quiet/staleの制御経路が`subcommands == ["stop-verdict"]`を要求する。二回目のrecorded callは別の違反になる。 |
| static count | 同じ`stop-verdict`でも二つ以上のcall siteを拒否する。 |

ソース参照:

- [recorderの生成とentry/exit記録](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/skills/mission/tests/test_issue779_guard_one_process.py#L677-L743)
- [digestとgap比較](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/skills/mission/tests/test_issue779_guard_one_process.py#L876-L933)
- [前景で一度だけ呼び出すlauncher](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/scripts/mission-stop-guard.sh#L49-L61)
- [quiet pathの起動数検査](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/skills/mission/tests/test_issue779_guard_one_process.py#L1063-L1069)と[stale pathの起動数検査](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/skills/mission/tests/test_issue779_guard_one_process.py#L1314-L1326)
- [static countの拒否](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/skills/mission/tests/test_issue615_guard_decision.py#L769-L780)と[二回呼び出しを拒否する回帰](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/skills/mission/tests/test_issue615_guard_decision.py#L876-L884)

## 再現と検証

合成交錯は既存`_writes_outside_the_cli`へ次のtree列を渡した。
`a`と`b`は同一fileの異なる内容のdigestであり、kind/modeは同じにした。

```text
before=a
snapshots=[entry:a, entry:a, exit:b, exit:b]
after=b
result=[]
```

一回起動では既存`test_which_gaps_are_read_and_which_belong_to_the_cli`が、
CLI内writeの除外と外側のwrite検出を比較する。このテストは通過した。

| 実行した既存回帰 | 結果 |
|---|---|
| `test_the_stale_path_also_costs_one_call`、`test_rewriting_only_the_lease_is_caught` | 2 passed、単回3.89秒 |
| `test_canonical_hook_is_judgment_free_and_dispatches_the_closed_command_set`、`test_the_hook_may_not_call_the_verdict_twice` | 2 passed、単回0.16秒 |

前者は`ps`の実行権限を要するため、`ps`を拒否するsandboxでの失敗を成功と扱わず、
同じ基準SHAで権限のある実行環境へ移して確認した。製品コードやテストは変更していない。
各fileは[CIの6 shardとQuality](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/.github/workflows/ci.yml#L65-L119)へ配線されている。

## 限界と見直し条件

- file内容はSHA256、他のentryはkind/mode/payloadを比較する。原文byteをそのまま比較する仕組みではない。
- 一つのCLIのentry→exit窓内でHookが並行writeしたか、観測点の間でwriteして復元したかは識別できない。call ID追加だけでは解決しない。
- 起動数の観測はrecorderを通る呼び出しが対象。[CLI名を変数以外へ直書きさせない検査](https://github.com/tackeyy/mission/blob/f0baddfb753c53aa9a51a3781076b6d1e61d8aad/skills/mission/tests/test_issue779_guard_one_process.py#L1042-L1060)もあるが、任意のshell構文を完全解析する保証とは区別する。
- 将来、複数のrecorded callを許容する場合は、一回起動契約の変更と合わせてcall identityによる対応付けを再評価する。

新しい恒久テストを追加していない。既存の安価なgap自己検証、実起動数、静的call site検査を保持し、
診断用の合成交錯は通常回帰スイートへ追加しない。
説明の訂正は[Issue 811](https://github.com/tackeyy/mission/issues/811)で別途扱う。
