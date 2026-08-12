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

## 実装方針

`provider_eligibility` に application context を作る純粋な validation seam を追加し、
`invoke-command` と `log-invocation` が StateLock 下で current state を再読してから呼ぶ。
検証に失敗した場合は process spawn と state write の前に同一の機械可読 reason code を返す。

## 検証記録

Red: 新規ガード5件は実装前に失敗し、低complexity・phase不一致・未選定・
stale selection・call limitの各経路が process spawn まで到達していたことを確認した。

Green: `test_provider_application_guard.py` は5件成功。既存
`test_specialist_invocations.py` は105件成功。最終候補では全スイートをCIで1回実行する。
