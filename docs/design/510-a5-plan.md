Issue #510 は、runtime guard が得た事実を閉じた observation として記録する A5 application boundary を追加する。freshness、lease、pass/fail、terminal outcome の決定権限は既存の Python control logic に残す。

## Outcome

- `stop-guard-observe` と `permission-preflight` を typed use case と専用 repository port の薄い adapter にする。
- observation input、既存 sidecar、mutation result を application boundary で再検証する。
- command ownership を A5 に一意に割り当て、source/plugin 配布物を同期する。

## Red to Green

1. application module 不在、未知 field、bool-as-int、権限 field 混入、denied/unknown の allow 化、repository failure 時の非変更を Red で固定する。
2. immutable request/result、closed reducer、専用 stop-observation repository、permission halt repository use case を実装する。
3. CLI の filesystem probe と CAS writer は adapter service として注入し、handler 自体から legacy mutation を除く。
4. focused regression、D1 mirror/hygiene、Python compatibility、full suite を実行する。

## Authority boundary

- Stop observation が更新できるのは digest、detail epoch、4 counter のみ。session state の pass、halt、phase、score、lease は入力にも出力にも存在しない。
- Permission observation は `state` と `assumptions` の ordered capability result のみを受け取る。denied/unknown は fixed blocked-external halt へ単調に写像し、allowed へ弱めない。
- repository は lock、identity CAS、lease/fence validation、atomic publish を所有する。application use case は filesystem、process、stdout、ambient environment を参照しない。

## Verification

- 新規 A5 unit/adapter/inventory tests。
- permission preflight、Stop-hook dedupe/freshness/parallel regression。
- module inventory、plugin mirror、artifact hygiene、vendor fingerprint、Python 3.9 compile。
- full test suite、CI、exact-head、独立 Checker 3 名の反例探索。

## Non-scope

- v5 UnitOfWork 選択と format migration は P1。
- Stop-hook の block 判定、mission pass/fail、review/score authority の変更は行わない。
- production release や plugin activation は行わない。
