# Issue #395 実装記録

## 対象

specialist provider の application path で、現在の Mission state と registry-backed
selection を再検証する。#396 以降の approval receipt、plan import、executor handoff は
実装しない。

## テストリスト

- [x] minimum complexity 未満の command provider はプロセスを起動しない
- [x] CLI phase と現在の Mission phase が異なる application を拒否する
- [x] current selection に属さない provider の invocation/evidence を拒否する
- [x] stale iteration、registry/selection identity の不一致を拒否する
- [x] `log-invocation --selection-source` で選定を後付けできない
- [x] 拒否時に specialist state を変更しない
- [x] 許可された current selection は invocation/evidence を記録できる
- [x] provider signal がない core invocation record は互換維持し、signal がある未選定 record は拒否する
- [x] `max_calls_per_iteration` を registry から selected checkpoint と spawn guard まで保持する
- [x] application 直前に current registry を再解決し entry / activation / projection drift を拒否する
- [x] command invocation を `reserved` → `running` → terminal で管理し、spawn 直前に context digest を再確認する
- [x] active invocation 中の state mutation を拒否し、fenced reconcile だけで terminal 化する
- [x] unknown child result は `abandoned-unknown` とし、provider result を application しない

## 実装方針

`provider_eligibility` に application context を作る純粋な validation seam を追加し、
`invoke-command` と `log-invocation` が StateLock 下で current state を再読してから呼ぶ。
検証に失敗した場合は process spawn と state write の前に同一の機械可読 reason code を返す。

追加修正は F1/F2 を既存差分から先に Green 化し、registry の current re-resolve
（F4）を完成させてから、その検証 seam を invocation lifecycle transaction（F3）へ接続する。
各 slice は focused test の Red を確認してから最小実装へ進み、#396 の approval
payload / receipt は対象外のまま維持する。

## 検証記録

Red: 新規ガード5件は実装前に失敗し、低complexity・phase不一致・未選定・
stale selection・call limitの各経路が process spawn まで到達していたことを確認した。

Green: `test_provider_application_guard.py` は5件成功。既存
`test_specialist_invocations.py` は105件成功。最終候補では全スイートをCIで1回実行する。

Final Green:

- provider application / specialist invocation / planning eligibility focused: 425 passed
- registry drift は process spawn 0、state bytes 不変
- running 中の state mutation は exit 2、terminal 後は正常終了
- dead running invocation は fenced evidence で `abandoned-unknown` へ遷移し、再application不可
- canonical / Codex plugin wrapper は同期済み
