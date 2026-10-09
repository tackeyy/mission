# 修復と最終検証の予算を予約して実行を制御する設計

決定案: 既存の `budget_minutes` を総枠とし、型付きの予算 policy と ledger を新しい projection に置く。policy は `init` の時点でだけ受け付け、**mission が起動する全ての spawn 入口に admission が入るまで受け付けない**（§3.5）。全体の消費は「稼働時計」1 本で数え、並行 child の時間を足し合わせない。修復と最終検証の枠だけを保護枠とし、全ての spawn 入口で起動の前に durable な予約（時間と state bytes の両方）を取る。予約できなければ起動せず理由付きで拒否する。予算切れは kernel が導出する一方向の exhaustion として扱い、pass に到達できない（force を含む）。

**保証の範囲（設計レビュー round 1 を受けて狭めた）**: F が強制するのは「policy を持つ session で、mission の spawn 入口から起動した child の時間と終端記録」だけである。host の inline 作業（mission を経由しない編集・実行と、host が自分で起動する planner・executor・reviewer の subagent。§3.2 #16）の時間は止められず、保護枠を侵食した量を記録して表示するだけにとどまる（§4.4）。policy の無い session は従来どおり助言のみで、status に `enforcement: advisory-only` と出す。

対象: [Issue 881: 修復と最終検証の予算を予約して実行を制御する](https://github.com/tackeyy/mission/issues/881)（親 [Issue 876: 品質改善の全体追跡](https://github.com/tackeyy/mission/issues/876)）。本書は設計のみ。実装、テスト追加、Issue 起票・本文変更、Git 操作による公開は行わない。
照合 head: `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`（依頼で固定）。本書の「現状」はこの head のソースを指す。remote の main は `9c948878d878bbadbfb98a1d62a43d67fcc700c7`（[D2b #909](https://github.com/tackeyy/mission/issues/909)、PR #911 の merge を含む）へ進んでいる。spawn 箇所の検索（§3.2）だけは両方の head で行い、同じ集合だった。それ以外の差分は照合していない（**UNKNOWN**）。実装の着手時に最新 main で再照合する。

関連設計: E は main に merge 済みの版（`8b3bf236`）[D05]、I は `origin/docs/884-eval-prereg`（`aa604792`）[D06] を読んだ。どちらも並行して改訂中で、本書はその時点の記述にだけ結ぶ。

引用 [S*] / [T*] / [D*] は末尾の固定 head の `file:line` とリンクへ結ぶ。テスト引用は保持するソース上の契約であり、本ステップのテスト実行結果ではない。

## 1. 採用する境界と既存状態

### 1.1 いまある予算・時間の扱い

| 照合した現状 | 種別 | F の扱い |
|---|---|---|
| `init --budget-minutes` は正の有限な **float** を受理し、`budget_minutes` として保存する。分の端数を許す。[S01][S04][S05] | 宣言のみ | 総枠の唯一の入力として使う。秒への変換は切り捨てる（§2.3） |
| `_budget_pressure` は `started_at` から現在までの壁時計を `budget_minutes` で割り、80% で warn、100% で exceeded とする。halt 中の時間も数える。[S02] | 観測のみ | legacy の表示は変えない。ledger がある session だけ ledger の時計で表示する（§2.5） |
| `next` は exceeded のときだけ `run-planner/run-executor/run-reviewers` を `consider-halt` へ差し替える。read-only で state を変えない。[S03] | 助言のみ | 助言のまま残す。強制は admission・完了 guard・exhaustion が担い、`next` には依存しない（§5） |
| guidance parity は `application.clock-budget-override` を `outside-parity` に置き、入力を `$.budget_minutes`・`$.started_at`・`iso_now()` と列挙する。[S06] | 境界の宣言 | 入力に `$.budget_ledger` を足し、outside-parity のまま保つ |
| `budget_minutes` は generic set の frozen 集合にも dedicated 集合にも含まれない。`passes` は frozen、`phase` は dedicated。[S07] generic `set` で `budget_minutes` を書き換えられるかは実行で確かめていない（**UNKNOWN**） | 保護なし | ledger がある session では generic set を拒否する（§2.4） |
| 稼働の記録は `activity_segments`（active・各種 wait・idle）と rollup。種類と理由は caller が申告する。[S10] reactivate が書き換えてよいのは timing/activity 系の field と `reactivation_history` だけで、`started_at` は動かない。[S08][S09][S11] | 観測のみ | caller 申告の activity を予算の差し引きに使わない（§2.2） |
| 独立レビューの request は `wall_time_sec`（上限 300）・`max_tool_calls`・`max_replays`・`max_output_bytes`・`max_packet_bytes` を閉じた int として持つ。[S12] 公開 CLI は `prepare/status` だけで、起動は未実装。[S13] | request ごとの上限 | request の値は変えない。dispatch の締切を F が別に与える（§4） |
| runtime adapter protocol の `observe_parent/launch/collect/cancel/recover` は同期の Python 呼出しで、どれも締切を引数に持たない。[S14] | 強制なし | 全ての adapter 呼出しを締切付きの子 process で行う（§4.2） |
| 凍結 verifier の `timeout_sec` は 1〜3600 の int で、definition digest に含まれる。[S15] runner は `start_new_session=True` で起動し、締切で process group へ SIGKILL を送り、0.2 秒待って再送する。[S16] 締切は Popen の後に monotonic で決まる。[S42] 起動前の候補の採取は timeout の無い `git ls-files` を呼ぶ。[S42] | 強制あり（child だけ） | child の前後の処理を含めて締切付きの監督 process で包む（§4.2） |
| `verification run` は spawn の前に durable な intent を保存しない。実行後に receipt を 1 回公開する。[S17] | 記録なし | 起動前の予約 commit を足す（§3） |
| command provider は `--timeout`（無ければ provider 値、さらに無ければ 120 秒）を使う。[S18][S31] `Popen` に新しい session を指定せず、timeout では直下の child だけを `kill()` し、その後の `communicate()` に timeout が無い。[S20] 起動直後の記録 commit が失敗した経路は `terminate()` の後に `wait(timeout=5)` を呼ぶ。[S39] timeout の計測は起動後の記録 commit の後から始まる。[S20] | 強制あり（不完全） | 予約時刻からの絶対締切にし、process group で回収し、待ちを全て有限にする（§4.2） |
| strict 隔離の backend は in-process の呼出しで、締切も取消しも渡らない。[S19][S21] | 強制なし | 予算付き session では起動前に拒否する（§4） |
| 承認 verifier は fork した child を 5 秒 join し、生きていれば SIGTERM→0.2 秒→SIGKILL→0.2 秒で回収し、`finally` で同じ回収をもう一度行いうる。最悪 5.8 秒。[S22] 呼出し元は `specialists verify-approval`（repository lock を持ったまま spawn する）と force 付き `mark-passes`（pass の transaction の中で spawn する）の 2 つ。[S40][S41] | 強制あり（固定 5.8 秒以内） | 2 つの入口とも予約の対象にする（§3.2）。旧版の「対象外」は撤回 |
| provider の phase は caller の `--phase` を受け取るが、eligibility が state の `phase` から写した値との一致を要求する。[S31][S37] | 照合のみ | 予算の分類は caller の引数ではなく kernel が state から導出する（§3.1） |
| 完了 guard `_acceptance_completion_ready` は state に `acceptance_contract` が無ければ何も検査せずに戻る。[S36] application の preflight も contract がある場合だけ呼ぶ。[S35] `passes` を書くのは kernel の `_mark_pass` だけ。[S46] | 契約付き session だけ | 予算の guard は contract と独立に、ledger を持つ全 session で評価する（§5） |
| 停滞は score に基づく `stagnation_count` で数え、3 以上で `consider-halt` を助言する。[S23] `max_iter` で dispatch を拒否する箇所は本書で照合した範囲では見つからない。[S24] | 助言のみ | 証拠の増えない dispatch を admission で止める（§3.4） |
| token・費用の producer は `skills/mission/lib` と `skills/mission/bin` に無い（`input_tokens|output_tokens|token_usage|cost_usd|total_cost` の検索で 0 件）。[S25] | 未測定 | 強制値にしない。`unmeasured` として表示し、0 と書かない（§2.1） |
| state 全体の上限は 4 MiB。[S28] E の改訂設計は、書込み種別ごとの最大増分 Δ・state から導出する予約・単一の `state_capacity_verdict` を新しい子 E0 に置く。[D05] | E0 で強制予定 | F の書込みと、F が予約する終端を E0 の種別表へ加える（§4.3） |

**結論**: 現在の予算は「宣言」と「助言」だけで、どの spawn 入口も残り時間を見ずに起動する。実行中の打ち切りは policy の timeout だけで、全体の期限とは結びついていない。完了 guard は契約付き session にしか効かない。

### 1.2 層の分担

決定: kernel は閉じた型・decoder・純粋な admission 判定・予算分類・reducer・完了 guard を持ち、時計を読まない（時刻は command の field として受け取る）。application は時計の観測、入口ごとの予約→起動→回収→精算の調整、締切付き子 process の管理を担う。persistence は既存の fenced commit と repository の lock、E0 の容量検査をそのまま使う。CLI は typed request の構築と 1 use case の呼出し、描画、終了コード変換だけを担う。予約の直列化は repository lock（[S30] の 5 秒 lock）と fence で行い、新しい lock 機構を作らない。

## 2. Typed policy と ledger

### 2.1 単位

| 単位 | 強制 | 定義 |
|---|---|---|
| 稼働時計（秒） | する | `loop_active` の区間だけを数える壁時計（§2.2）。全体の消費はこれ 1 本。外部締切があればそれでも打ち切る |
| dispatch 時間（秒） | する | 各 dispatch の予約から精算期限までの時間。保護枠の消費として使う |
| state bytes | する（E0 の機構で） | 予約した dispatch の終端と精算を書く bytes（§4.3） |
| 同時 dispatch 数・phase ごとの dispatch 回数 | する | 子の無制限な増加を止める |
| tool calls・replays・output bytes | D の request ごと | adapter が強制する。F は精算時に観測値を telemetry として残すだけ |
| token・費用 | しない | producer が無いので `{"status": "unmeasured", "reason": "no-producer"}`。測れた場合も強制値にしない |

### 2.2 稼働時計と外部締切

決定: 稼働時計は `_mission_started_at` と同じ順（`created_at_session` → `started_at` → `created_at`）で最初に読める時刻から始まり、`MarkHalt` と `MarkPass` で閉じ、`Reactivate` と `ResumeStale` で開く区間の和とする。[S08][S09][S11] 停止区間は数えない（判断事項 2）。

- caller が申告する activity（idle・wait）は差し引きに使わない。差し引きを申告できると、申告だけで予算を延ばせる。
- stale halt（process が消えて halt が記録されない場合）は、`ResumeStale` までの時間をすべて消費として数える。少なく数える側へ倒さない。
- 時計の後退（直前に記録した時刻より前の `at`）は `budget-clock-regressed` で拒否し、state を変えない。
- 並行 child の時間は稼働時計へ足さない。

**外部締切**（設計レビュー round 1 の Medium を受けて追加）: policy は任意の `external_deadline_at`（UTC の秒精度の時刻、または null）を持つ。`overall_deadline = min(稼働時計が total_sec に達する時刻, external_deadline_at)` とする。停止区間を数えない稼働時計だけでは、halt と reactivate を挟んだ run で mission の締切が外部の壁時計上限より後ろへずれる。外部締切は停止区間に関係なく固定の時刻で打ち切るので、外部上限の内側で final 枠を確保できる。延長の手段にはならない（`min` しか取らず、policy は以後変更できない）。

**I（評価）との関係**: I は全 arm に共通の経過時間上限 T を事前登録し、T は harness の `RpcProcess` が起動時刻からの単一 deadline として強制する。[D06][S47] **T は Mission の外（harness）で強制され、F はそれを置き換えない。** Mission arm では、harness が `init --budget-minutes T/60 --budget-policy <file>` で policy を渡し、`external_deadline_at` に「harness の起動時刻 + T − harness の後処理の余白」を入れる。これで Mission 内の final 枠は T より前に終わるよう配置され、halt を挟んでも T を越えて計画されない。余白の値と、Mission arm の harness が halt 後に reactivate するかは I/G の決定事項で、本書では **UNKNOWN** とする（I への要求として §8 判断事項 10 に記す）。

### 2.3 Policy

決定: `mission-budget-policy/1` を閉じた構造で、**`init --budget-policy <file>` の時点でだけ** 受け付け、以後変更しない（§3.5）。旧版の `budget policy import` command（後から import する経路）は作らない。後から import できると、import 前に予約なしで起動した dispatch が残るためである。保存先は acceptance contract の外とする。contract に入れると `canonical_contract_digest` が変わり、B の既存 receipt が全て stale になる。[D01]

| field | 型・初期値 | 意味 |
|---|---|---|
| `schema` | `mission-budget-policy/1` | — |
| `total_sec` | int ≥ 1。`⌊Decimal(repr(budget_minutes)) × 60⌋` と一致しなければ拒否 | 総枠。端数秒は切り捨てる（延ばす側へ丸めない）。0 になる場合は `budget-total-too-small` で拒否 |
| `external_deadline_at` | UTC 秒精度の時刻、または null | §2.2 |
| `reserve_basis_points` | `{planning: 1000, implementation: 4000, verification: 2000, repair: 2000, final: 1000}`。合計 10000 | 配分 |
| `protected_phases` | `["repair", "final"]` 固定 | 強制する保護枠 |
| `max_concurrent_dispatches` | int、初期値 2 | 同時に開いている予約の上限 |
| `max_dispatches_per_phase` | int、初期値 32 | 予算分類ごとの予約回数の上限 |
| `term_grace_sec` / `kill_wait_sec` / `collect_sec` | int、初期値 2 / 1 / 2 | 締切後の SIGTERM→SIGKILL の待ちと、出力回収の上限（§4.1） |
| `post_run_sec` | int、初期値 10 | verifier の終了後、監督 process が候補と報告を読む上限 |
| `adapter_call_sec` / `cancel_call_sec` | int、初期値 30 / 10 | adapter の 1 回の呼出しと `cancel` の上限 |
| `commit_margin_sec` | int、初期値 10 | 終端と精算の commit のために残す時間（lock の 5 秒待ち [S30] 2 回分） |
| `closeout_margin_sec` | int、初期値 30 | final の dispatch の後、`mark-passes` を commit するために残す時間 |
| `min_dispatch_sec` | int、初期値 1 | これ未満の実効締切では起動しない |
| `system_recovery_sec` | int、初期値 60。`adapter_call_sec + cleanup_sec(recover) + commit_margin_sec`（初期値 43）未満なら拒否 | 回復専用の固定枠（§3.6）。final 枠から init 時に切り出し、補充しない |
| `no_progress_limit` | int、初期値 2 | §3.4 |
| `provenance` | `experimental-initial` 固定 | 初期値が実証済みではないことを保存面に残す |
| `reactivate` | `allowed` / `forbidden`、初期値 `allowed` | `forbidden` の session では Reactivate を拒否する（§3.3。I の要求。round 6 で追加） |

`budget_minutes` は float で保存され、`repr` は往復できる最短の 10 進表記を返すので、`Decimal(repr(...))` は利用者が渡した 10 進値と一致する（例: 1.1 分 → 66 秒、0.01 分 → 0 秒で拒否）。2 進の誤差で 65 秒へ落ちることはない。

**配分（計画 10 / 実装 40 / 検証 20 / 修復 20 / 最終 10 %）と上表の他の初期値は実験用の初期値であり、効果が実証された既定値ではない。** status にも `provenance` をそのまま出す。値の妥当性は I（[Issue 884](https://github.com/tackeyy/mission/issues/884)）の計測で判断する。

**final 枠の切り出し**: `final_reserve = ⌊total_sec × final の basis points / 10000⌋` から `system_recovery_sec` を init 時に差し引き、残りを final 分類が使える枠 `final_reserve_effective` とする。`final_reserve_effective < min_final_run_sec`（§5）なら `budget-final-reserve-too-small` で policy を拒否する（初期値では `0.1 × total_sec − 60 < 27`、つまり total が 870 秒（14.5 分）未満だと拒否される。小さな総枠では final の配分を上げる）。

決定（強制する範囲）: 保護枠として強制するのは repair と final だけとする。planning・implementation・verification は「非保護の共有枠」として合算で強制し、個別の配分は status の目標値として表示する（判断事項 1）。

### 2.4 Ledger と保存場所

決定: `MissionState.budget: BudgetProjection` を追加し、保存 schema を `mission-budget-ledger/1` とする。v4 document の予約キー `budget_ledger` と closed v5 の `extensions.budget_ledger` を同じ閉じた decoder で復元する。D の `fresh_review`、E の `repair_lineage` と同じ方式で、v5 の top-level field は増やさない。[S12][D03]

| 型 | 内容 |
|---|---|
| `BudgetPolicy` | §2.3 と、その canonical digest |
| `ActiveClock` | 閉じた区間の合計秒、開いている区間の開始時刻（または null）、最後に観測した時刻 |
| `DispatchReservation` | `reservation_id`（kernel が operation identity から domain-separated に導出）、`entry`（§3.2 の閉じた列挙）、`budget_class`（kernel が導出、§3.1）、`target`、`operation_id`/`fencing_epoch`、`reserved_at`、`child_deadline_at`、`settle_by`、`reserved_sec`、`reserved_bytes`（§4.3） |
| `PhaseCharge` | 予算分類ごとの精算済み dispatch 秒の合計、予約回数、開いている予約数 |
| `Settlement` | `reservation_id`、`outcome`（`settled`/`charged-full-unknown`/`kill-unconfirmed`）、`charged_sec`、`late_settlement_sec`、`observed`、`telemetry`（上限長の閉じた形） |
| `ProgressSignature` | `(entry, target)` ごとの最後の `candidate_digest`・結果 digest・連続回数 |
| `StopSlots` | 事前確保した固定長の slot: 拒否の計数と直近の理由、`FinalLatch`（時刻と理由）、`Exhaustion`（時刻と原因）、`BudgetStop`（§5）、`SystemRecovery`（回復専用枠の使用秒・回数・開いている回復予約 0〜1 件。§3.6）、`FinalRun`（final latch 後に完了として精算された final 分類の dispatch の最新の `reservation_id` と時刻。§5）。policy の受付時に確保し、以後は上書きだけ（増分 0） |

- state に置くのは上限付きの要約だけとする。開いている予約は `max_concurrent_dispatches` 件以下、精算済みは分類ごとの合計と直近 32 件だけ。`ProgressSignature` の件数は予約回数の上限（分類 5 × `max_dispatches_per_phase`）で抑えられる。dispatch の詳細な事実は、各入口が既に公開している receipt に残る。[S16][D02]
- キー欠落だけが「予算 policy なし」。null・未知 schema・未知 field・重複 ID・開いている予約の上限超過・時刻の逆転は理由コード付きで拒否し、空の ledger と読み替えない。
- `budget_ledger` とその子孫は generic set・init/reinit・compatibility delta・review/score import・specialist evidence・downgrade から保護する（D・E と同じ集合を拡張する。[S07]）。reinit は ledger を変更・削除しない。ledger がある session では `budget_minutes` の generic set も拒否する。ledger の無い legacy session の挙動は変えない。

### 2.5 Status の表示

決定: `budget status`（R1.query、read-only）を追加する。`next` には ledger がある session だけ `budget` ブロックを足し、`budget_pressure` を ledger の稼働時計から計算して `basis: "active-clock"` を付ける。ledger の無い session の出力は変えず（[T01] を保持）、`budget status` は `enforcement: advisory-only` を返す。

表示する項目: `enforcement`（`enforced`/`advisory-only`）、policy の値と `provenance`、総枠・消費・残り・外部締切、分類ごとの目標・精算済み・開いている予約、保護枠の未使用分、非保護枠・修復・最終それぞれの締切、開いている予約の一覧、直近の拒否理由、`ProgressSignature` の連続回数、final latch、exhaustion、停止記録、`state_capacity_verdict` の残量（E0 の値を読むだけ）、token・費用の `unmeasured`、`reserve_erosion_sec`（§4.4）。

## 3. 全 spawn 入口での admission

### 3.1 締切の計算と予算分類

kernel の純粋関数 `admit(policy, ledger, state, at, request) -> Admission | Refusal` が次を計算する。

- `final_unspent = final_reserve_effective − (final の精算済み + 開いている final 予約)`、`repair_unspent` も同様（0 未満は 0。`final_reserve_effective` は §2.3）
- `final_close = overall_deadline − closeout_margin_sec − system_recovery_sec`（回復専用枠は final の締切の後ろに置き、どの分類も使えない。§3.6）
- 非保護枠の締切 = `final_close − repair_unspent − final_unspent`
- repair の締切 = `final_close − final_unspent`
- final の締切 = `final_close`
- 子の締切 `child_deadline_at = min(at + policy_timeout, 分類の締切 − cleanup_sec(entry) − commit_margin_sec)`（`cleanup_sec` は §4.1 の入口ごとの定数）
- 精算期限 `settle_by = child_deadline_at + cleanup_sec(entry) + commit_margin_sec`、予約秒 `reserved_sec = settle_by − at`。したがって `settle_by ≤ 分類の締切` が常に成り立つ
- `child_deadline_at − at < min_dispatch_sec`、同時予約数が上限、分類の予約回数が上限、§3.4 の無進捗、exhaustion 成立（§5）、容量不足（§4.3）のいずれかなら `Refusal`

**final の予約は repair が使えない。** repair の締切は常に `final_unspent` を差し引いた値で、repair の dispatch の `settle_by` はこれを越えない。

保護枠の消費は、精算済み dispatch 時間の**合計**で数える（並行に走った 2 本は両方とも数える）。並行時に多く数える側へ倒れる保守的な近似で、少なく数えることはない。全体の消費は §2.2 の時計で、こちらは足し合わせない。

**予算分類は kernel が導出し、caller は選べない**（設計レビュー round 1 の Medium を受けて訂正）。`budget_class(entry, state, ledger, at)` は次の kernel が保持する事実だけを入力にする。caller の引数（`--phase` を含む）は入力にしない。

| 入口の種類 | final latch 前 | final latch 後 |
|---|---|---|
| verification 系（`verification run`、`fresh-review run`、force の承認 verifier） | 非保護 | final |
| repair 系（`repair reverify`、`repair disposition run`、`repair begin` の回数） | repair（E の projection に開いた attempt があることを要求） | `budget-final-latched` で拒否 |
| provider（`invoke-command`/`invoke-prepared`、`verify-approval`） | 非保護 | state の `phase` が reviewing/scoring/critic なら final、planning/executing なら `budget-final-latched` で拒否 |

provider の state の `phase` は、eligibility が caller の `--phase` との一致を要求する値である（`MISSION_PHASE_TO_PROVIDER_PHASE` と `mission-phase-mismatch`）。[S37] F はこの一致検査を残しつつ、分類の入力には `--phase` ではなく state の `phase` を使う。`phase` は generic set の dedicated 集合にあり、汎用書込みでは動かない。[S07] 非保護の分類はどれも同じ共有枠なので、`phase` の選び方で保護枠を得ることはできない。phase を reviewing へ進めると、その後の provider の作業は内容に関係なく final に分類され、final の枠を使う。これは §4.4 の保証が作業の内容ではなく分類で定まる（final 以外の分類の dispatch が final の予約を使えない）ことと整合し、保証の破れではない。

final latch は `budget enter-final`（明示）か `at ≥ repair の締切`（kernel が導出）で成立し、初版では戻らない（判断事項 5）。

### 3.2 Spawn 箇所の一覧（同形検索の結果）

予約は spawn の前に fenced commit で保存する（`ReserveDispatchBudget`）。予約 commit が失敗したら起動しない。精算（`SettleDispatchBudget`）は、各入口の終端公開と同じ commit に入れる。

検索: `Popen|subprocess.run|os.fork|multiprocessing|run_contract_verifier|dispatch` に、`subprocess.(check|call|getoutput|getstatus)|os.system|posix_spawn|.fork(|pty.|asyncio.create_subprocess|get_context(` を加え、`skills/mission/bin/mission-state.py` と `skills/mission/lib` を `d25a66c6` と `9c948878` の両方で検索した（ファイルごとのヒット数は両 head で同じ）。下表は全ヒットを分類したもの。`dispatch` 語は 2 head とも多数ヒットするので、定義と呼出し（`def *dispatch*`・`dispatch*(`）に絞って分類した（#4・#16）。`skills/mission/bin/mission-migrate.py` は session を動かさない移行 tool なので検索対象外とした。

| # | spawn 箇所 | 入口 | 扱い | 予算分類 | 起動前の検査と予約 | 精算 |
|---|---|---|---|---|---|---|
| 1 | `verification_runner.py:329` の Popen [S16]、前段の `verification_runner.py:48` の `git ls-files` [S42] | `verification run`（`--repro-input` を含む）。`run_verification_receipt_cli` → `run_contract_verifier`（`verification_execution.py:58,85`）[S17]、所有 A3.evidence [S29] | 予約する | 表 §3.1 | spawn 前に予約 commit を追加し、exec で起動した監督 process ごと締切で包む（§4.2） | receipt の公開 commit |
| 2 | 同上（E の再検証が B を呼ぶ） | `repair reverify`（[E2 #906](https://github.com/tackeyy/mission/issues/906)、未 merge）[D04] | 予約する | repair | E の実行 intent と同じ commit で予約 | `CommitFindingReverification` の commit |
| 3 | `command_provider.py:520` の Popen [S20]（mission-state.py:5751 で注入 [S33]） | `specialists invoke-command` / `invoke-prepared` [S31]、所有 A4 [S29] | 予約する | 表 §3.1 | 既存の Section 1 reservation commit [S18] に予約を入れる | 既存の terminal 更新の commit |
| 4 | `mission-state.py:10233` の `dispatch_prepared_packet`（in-process）[S19][S21] | strict 隔離の provider | 予算付き session では `budget-deadline-unenforceable` で拒否 | — | — | — |
| 5 | `mission-state.py:10409` の fork [S22]（F2p で exec の trampoline へ置き換える。§4.2）、呼出し 5550 [S40] | `specialists verify-approval` | 予約する（旧版の対象外を撤回） | 表 §3.1（preflight packet の phase から state 経由で導出） | lock を取る既存の transaction（5525）の**前に**、別の短い transaction で予約 commit | 既存 transaction の commit |
| 6 | 同上、呼出し `mission-state.py:10445` ← 14123 ← `review.py:523` [S41] | `mark-passes --force` | 予約する | 表 §3.1 | 予算 guard（§5）を通った後、pass の transaction の前に予約 commit | pass の commit。pass が拒否された場合は拒否の後に精算だけを commit |
| 7 | adapter の `launch/collect/cancel` [S14] | `fresh-review run`（[D2c #912](https://github.com/tackeyy/mission/issues/912)、未 merge）[D02] | 予約する | 表 §3.1 | dispatch intent と同じ commit で予約。全 adapter 呼出しを締切付きの子で行う（§4.2） | terminal（D2b の variant）の commit |
| 8 | 同上 | `repair disposition run`（[E3 #907](https://github.com/tackeyy/mission/issues/907)、未 merge）[D04] | 予約する | repair | D と同じ規律 | terminal の commit |
| 9 | adapter の `recover` [S14] | `fresh-review reconcile`、`repair reconcile`、`repair disposition reconcile`（未 merge） | 予約する（entry `recover`。round 2 で「予約しない」を撤回） | 回復する dispatch の予約の分類。分類の締切で起動できなければ回復専用枠（§3.6） | 回復の予約 commit の後に `recover` を締切付きの呼出し子で行う（§3.6・§4.2） | 回復の予約と、回復した dispatch の予約を同じ commit で精算 |
| 10 | — | `repair begin`（E2）[D04] | process を起動しない。分類の予約回数として数え、`repair の締切 − at` が B の最小実行（`min_dispatch_sec + cleanup_sec(B) + commit_margin_sec`）に満たなければ拒否 | repair | E の容量予約（[D05] の「受付時に全段を確保」）と同じ commit | — |
| 11 | — | `specialists reconcile-invocation` [S31] | spawn しない。回復の mutation | — | — | 開いている予約を精算 |
| 12 | `integration_gate.py:67,79,171` [S32] | `gate-and-merge` | 対象外（mission repository 自身の merge 工程で、session の dispatch ではない。判断事項 6） | — | — | — |
| 13 | git・ps の短い呼出し: `mission-state.py:1150, 2033, 2038, 6029, 6049, 6058, 6102, 10515, 10538, 14495`、`worktree_archive.py:115, 655`、`evidence.py:191` [S43] | 状態の観測・archive・revision scope の検証 | dispatch として数えない。agent の作業を起動しないため。時間は稼働時計に含まれる。timeout の無い git 呼出しが含まれるが、F の範囲外とする | — | — | — |
| 14 | `mission_python_inventory.py:153` [S43] | repository の import 検査 tool と `benchmarks/mission-vs-goal/public_benchmark.py`（どちらも mission-state.py から import されない独立した tool） | 対象外 | — | — | — |
| 15 | spawn ではないヒット: `mission-state.py:43`（`import multiprocessing`）、`command_provider.py:110`（注入される `Popen` の field 宣言。実体は #3）、`command_owners.py:153` と `guard_timeout.py:309`（`pty\.` が英文の `empty.` に当たった偽陽性） | — | 該当なし | — | — | — |
| 16 | host が起動する subagent（`next` の `run-planner`/`run-executor`/`run-reviewers` [S02]、goal dispatch の host-native 経路 [S48]）。`dispatch` 語のヒットの大半（`_resolve_goal_dispatch`、`record_dispatch_intent`・`reconcile_dispatch_unknown` [S49]）はこの経路の記録・案内で、mission は process を起動しない | host の作業 | **強制の外**。mission からは起動も中断もできない。§4.4 の inline 作業と同じ扱い（稼働時計に含まれ、侵食量として出る） | — | — | — |

- 予算 policy の無い session では、どの入口も予約 commit を作らず、従来どおり動く（`advisory-only`）。
- 未 merge の D2c・E2・E3 の入口は、その merge 後に最新 main で locator を取り直す。本書の契約は「その入口が予約・精算を同じ commit に入れること」と「adapter 呼出しが §4.2 の締切付き子を通ること」を要求するだけで、型や関数名を決めない。

### 3.3 直列化・再試行・再開

- 予約の判定は repository lock の中で現在の ledger に対して行う。並行する 2 つの入口は lock で直列になり、同じ未使用枠を二重に配らない。fence の古い書き手は予約できない。
- 同じ operation の再応答: B の `verification run` は同じ operation でも再実行する契約で [T03]、F は再実行ごとに新しい予約を取る（予約 ID は operation identity と実行回の連番から導出）。D/E の同じ operation の再応答は再 spawn しないので、新しい予約を取らず、保存済みの予約・精算を返す。
- crash 後に開いたまま残った予約は、`settle_by` を過ぎた後の最初の mutation（予約・reconcile・`budget reconcile`）が `charged-full-unknown`（予約秒をすべて消費として計上）で精算する。`settle_by` 前の予約は開いたまま残し、同時予約数に数え続ける。
- `kill-unconfirmed`（§4.1）の予約は全額計上し、同時予約数の枠も解放しない。解放は reconcile が child の終了を観測したときだけ。
- 再開（Reactivate/ResumeStale）は ledger を codec から復元し、空にしない。exhaustion が成立した session の Reactivate は `budget-exhausted` で拒否する（F は延長を持たない。判断事項 8）。
- policy の `reactivate` が `forbidden` の session では、kernel は Reactivate を flag（`--approved-by-user` を含む）に関係なく `budget-reactivate-forbidden` で拒否し、state を一切変えない（exhaustion の slot も書かない。この検査を他の検査より先に行う）。policy は session の間変わらない（§2.3）ので、途中で `allowed` へ戻す経路は無い。ResumeStale はこの field の対象外とする。I の設計（PR #914）は評価中の reactivate の禁止をこの field に依存し、F2c が merge されるまでは I 側で fail-closed の代替（reactivate を行わない harness の規則）を使う（判断事項 20）。

### 3.4 証拠の増えない反復を止める

決定: 精算のたびに `(entry, target)` ごとの `ProgressSignature` を更新する。同じ `candidate_digest` で同じ結果 digest（B は status・exit・count・output digest、D は terminal の output digest、E は reverification receipt digest、provider は outbound packet digest と exit）が `no_progress_limit` 回続いたら、同じ `(entry, target, candidate_digest)` の次の予約を `budget-no-new-evidence` で拒否する。候補が変われば連続回数は 0 に戻る。score に基づく既存の `stagnation_count` [S23] は残す。

### 3.5 有効化の fail-closed（設計レビュー round 1 の High 1）

決定: 予算の有効化（policy を持つ session を作ること）は、**main 上の全 spawn 入口に admission が入っていることを code で確かめてから**でないと成立しない。

- kernel に閉じた表 `BUDGET_SPAWN_ENTRIES` を置き、§3.2 の各入口を `covered` / `pending` / `excluded`（理由付き）のいずれかで持つ。`init --budget-policy` の use case は、表に `pending` が 1 件でもあれば `budget-admission-incomplete` で拒否し、session を作らない。
- inventory test を置く。§3.2 の文字列検索は偽陽性（#15）を含むので、test は AST で `subprocess` の各関数・`os.fork`/`os.system`/`os.posix_spawn*`・`multiprocessing` の context と `Process`・注入された `Popen` の呼出し・adapter protocol の method 呼出し [S14] を call node として拾い、`skills/mission/lib` と `skills/mission/bin/mission-state.py` を走査して、ヒットした全箇所が表のどれかに結ばれていること、`covered` の入口が実際に予約 use case を呼ぶこと（fixture で予約前に spawn しようとすると失敗すること）を検査する。新しい spawn 箇所が表に無ければ test が落ちる。あわせて、予算付きの入口の spawn が共通の起動 helper（§4.2「子の起動と group の確保」）だけを通ること、helper の `Popen` に `preexec_fn` が渡らないこと、`multiprocessing` の `Process` と `os.fork` の call node が policy の無い session の callable の承認 verifier の 1 箇所（`excluded`、理由付き。判断事項 19）以外に無いことを検査する（round 4 の High）。後から merge される D/E の入口（D2c・E2・E3、その後の E4 など）は、その PR で表に `covered` として足さない限り CI を通らない。
- 段階との対応（§7）: F1・F2p・F2a・F2b の間は `init --budget-policy` の parser 自体を公開しない。F2a は既存入口を `covered`、D/E 入口を `pending` として表に入れる。F2c は `pending` を 0 にし、同じ PR で `init --budget-policy` を公開する。**policy を持つ session が存在しうる時点では、全入口が covered である。**
- 旧版の窓（F1 で import を公開、F2a 後も D/E 入口が F2b まで未対応）はこれで閉じる。

### 3.6 回復の予約と回復専用枠（設計レビュー round 2 の High 1）

決定: adapter の `recover` 呼出し（§3.2 #9）は、他の spawn と同じく起動前に予約を取る。旧版は「予約せず exhaustion 後も許す」としていたため、回復の呼出し子が final の時間や、exhaustion 後の時間を予約なしで使えた。

- **通常の回復**: entry `recover` として予約する。分類は、回復する dispatch の予約が持つ `budget_class`（D/E の入口は予約 commit で intent に `reservation_id` と分類を一緒に書く。§3.2 #7・#8・#2）で、caller は選べない。`child_deadline_at = min(at + adapter_call_sec, その分類の締切 − cleanup_sec(recover) − commit_margin_sec)` で、§3.1 と同じく `settle_by ≤ 分類の締切` が成り立つ。同時予約数と分類の予約回数にも数える。
- **回復専用枠**: 通常の回復が時間の理由（分類の締切を過ぎた、final latch 後の repair、exhaustion）で拒否される場合に限り、`system_recovery_sec`（§2.3。final 枠から init 時に切り出した固定枠）を使う entry `system-recover` として予約できる。条件は、回復の対象が終端の無い dispatch（開いている予約、`kill-unconfirmed`、または `charged-full-unknown` で精算されたが D/E の terminal が無いもの）であること。
  - `child_deadline_at = min(at + adapter_call_sec, overall_deadline − cleanup_sec(recover) − commit_margin_sec, at + 枠の残り − cleanup_sec(recover) − commit_margin_sec)`。`reserved_sec` は枠の残りを越えない。
  - 精算は `SystemRecovery` slot の使用秒に計上する（不明なら予約秒の全額）。枠は補充しない。開いている回復専用の予約は同時に 1 件までで、`max_concurrent_dispatches` とは別に数える（`kill-unconfirmed` で上限が埋まっていても回復できるようにするため）。
  - 枠の残りが `cleanup_sec(recover) + commit_margin_sec + min_dispatch_sec` に満たない、または `at ≥ overall_deadline` なら `budget-recovery-allowance-exhausted` で拒否する。その場合 reconcile は adapter を呼ばず、ledger だけの精算（`charged-full-unknown` の計上、`kill-unconfirmed` の維持）を行う。終端は記録されず、pass には到達しない（§5）。
- **上限**: 回復専用枠で使える時間は session 全体で `system_recovery_sec` 以下、かつ `overall_deadline` を越えない。時間の上限が無い回復は残らない。
- `specialists reconcile-invocation`（#11）は process を起動しないので予約しない。

## 4. Timeout、bounded kill、終端の容量

### 4.1 入口ごとの締切後の待ち（`cleanup_sec`）

子の締切に達した後、親が順に行う待ちを全て数え、`cleanup_sec(entry)` とする。kernel は入口ごとの式を閉じた表で持つ。

| 入口 | 締切後の順序付きの待ち | `cleanup_sec(entry)` |
|---|---|---|
| B の verifier（#1・#2） | 監督 process の中で: verifier へ SIGKILL→0.2 秒の wait→再送 [S16]（1 秒に切り上げ）→候補と報告の読取り（`post_run_sec` 以内）。監督 process が超過したら親が: 監督が率いる process group G（§4.2）へ SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec` | `1 + post_run_sec + term_grace_sec + kill_wait_sec` |
| command provider（#3） | process group へ SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec`→pipe の回収（`collect_sec` 以内） | `term_grace_sec + kill_wait_sec + collect_sec` |
| adapter（#7・#8） | 実行中の呼出し子へ SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec`→`cancel` 呼出し（`cancel_call_sec` 以内）→その超過時の SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec` | `2 × (term_grace_sec + kill_wait_sec) + cancel_call_sec` |
| 承認 verifier（#5・#6） | 締切を渡さない。起動からの 5 秒（exec による interpreter の起動と job の受け渡しを含む）と最大 4 回の 0.2 秒待ち [S22]（1 秒に切り上げ）→group の掃除（§4.2。`kill_wait_sec`） | 固定。`reserved_sec = 5 + 1 + kill_wait_sec + commit_margin_sec`（初期値 17）として扱う |
| 回復（#9 の `recover` / `system-recover`） | `recover` の呼出し子へ SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec` | `term_grace_sec + kill_wait_sec` |

初期値では B 14 秒、provider 5 秒、adapter 16 秒、承認 verifier 7 秒、回復 3 秒。これに `commit_margin_sec` を足した時間が、分類の締切の手前に確保される。

**正常終了の経路も group の掃除を通す**（§4.2「全ての終了経路での group の掃除」）。正常終了は子の締切より前に起き、掃除の待ちは `kill_wait_sec` 以下なので、各行の `cleanup_sec` に収まる。締切後の経路では、各行の最後の「SIGKILL→`kill_wait_sec`」がこの掃除そのもので、待ちは増えない。承認 verifier だけは締切を渡さない固定枠なので、掃除の `kill_wait_sec` を固定枠へ足した（round 3 で 6 秒から 7 秒）。

**exec による起動（round 4。§4.2）は `cleanup_sec` を変えない。** interpreter の起動・trampoline の import・job の受け渡しは子の締切より**前**の時間で、子の締切の中で消費される（締切後の待ちには入らない）。2026-10-04 の macOS・Python 3.14.6 での実測は、`python3 -I -c pass` が 0.02 秒、lib を `sys.path` に足して `mission_application.verification_execution` または `fresh_review_runtime` を import するまでが各 0.14〜0.15 秒（各 1 回、負荷の無い状態）。`min_dispatch_sec`（初期値 1）はこれを含む最小の実行時間として足りるが、負荷時の起動時間は **UNKNOWN**（F2p で負荷をかけて計測し、`min_dispatch_sec` を下回るなら初期値を上げる）。承認 verifier の 5 秒も起動時間を含むので、callback に使える時間は fork 版より起動時間ぶん短くなる。固定枠 17 秒は変えない。

**親の待ちに無期限のものを残さない。** 現状の無期限の待ち（provider の `communicate()` [S20]、失敗経路の `wait(timeout=5)` の後に残る child [S39]）は、上の順序へ置き換える。SIGKILL の後 `kill_wait_sec` で終了を確認できない場合は、それ以上待たずに `kill-unconfirmed` を記録する（§3.3）。

### 4.2 締切付きの呼出し境界（設計レビュー round 1 の High 3）

- **B の verifier**: `run_contract_verifier` 全体（候補の採取、Popen、候補と報告の読取り）を監督 process で実行する。監督は Python の process の fork ではなく、exec で起動する（round 4 の High。下の「子の起動と group の確保」）。旧版の「承認 verifier と同じ `multiprocessing.get_context("fork")` を使う」は撤回する。
  - **process group は親が起動の時点で作り、子の Python の code より前に存在させる**（設計レビュー round 2 の High 2、round 3 の High、round 4 の High）。G の id（pgid = 監督の pid）は `Popen` の戻り値として親が最初から知っており、pipe での受け渡しは無い。
  - 監督は verifier を **新しい session を作らずに** 起動する。現状の runner は `start_new_session=True` で verifier を別 session にする（verification_runner.py:329-331 [S16]）ので、予算付き session の監督下では runner がこれを指定しない引数を受け取る。verifier とその子孫は G を継承する。`git ls-files` [S42] も監督の中で走るので G に入る。
  - runner の凍結 `timeout_sec` による打ち切りは、監督下では `killpg(child.pid)`（verification_runner.py:363 と 386 の 2 箇所）ではなく verifier の pid への SIGKILL にする（verifier は group leader ではないため。G へ送ると監督自身も止まる）。打ち切り後に pipe を閉じて抜ける既存の動き（同 366-368）は変えないので、子孫が pipe を持ち続けても監督の読取りは止まらない。残った子孫は親の G への送信で掃く。
  - **決定（[#921:既存spawn入口の予約・回収](https://github.com/tackeyy/mission/issues/921)、[PR #962:verifierの予約・精算](https://github.com/tackeyy/mission/pull/962)のround 1反映、[PR #966:verifierの締切付き起動](https://github.com/tackeyy/mission/pull/966)のround 1でwatchdogのgroupを分離）**: 上の2項（verifierはGを継承する、凍結timeoutはverifierのpidへのSIGKILL）を次のとおり改める。verifierは監督の子として**監督とは別のprocess group** で起動し、締切のwatchdogはtargetのexecより前に、verifierとは別のprocess groupへ移す（bootstrapは`-I -S`）。watchdogは同じsessionに残してsession leaderのIDを保持し、verifierのgroupへの最後のsignalまでIDの再利用を防ぐ。watchdogのreadyを確認するまでtargetを起動しない。bootstrapはtargetの起動後も残り、watchdogが途中で終了したらgroup全体を直ちに回収する。締切以後に終了を観測した場合はpassedにしない。verifierの打ち切りは、凍結timeout・Fの締切のどちらでもchildの締切の時点でverifierのgroup全体へSIGKILLを送り、同じgroupの孫も残さない。`setsid`等で自らgroupを抜ける子孫の回収は保証しない（下記の限界）。その子孫がstdoutを保持している場合も、上限付きの読取り後にblocked/timeoutとしpassedにしない。監督はその後に候補と報告を読み、frameを返す。親がframeを読む期限はchildの締切＋`1 + post_run_sec`（`cleanup_sec` に予約済み）とする。理由: 監督と同じGにverifierを置くと、孫まで止める送信が監督自身も止めてしまい、締切後の出力のdigestと終了codeを監督が返せない（round 1のHigh。候補が大きいと0.5秒の猶予で読取りが終わらず失われた）。verifierが自身のgroupをSIGSTOPし、監督も停止・終了した場合も、watchdogが締切にverifierのgroupへSIGKILLを送る。watchdogはbootstrapの終了時にもgroupを回収し、自身も終了する。bootstrapが停止していてもwatchdogの寿命は締切までに限られる。watchdogの起動に失敗したらverifierを実行せず、failed/completedとして扱わない。監督のframeが届かない場合は内側のgroupの回収を証明できないので `kill-unconfirmed` として予約を保持する。親によるGの掃除（次項）は従来どおり全ての終了経路で行う。
  - 親は、監督から結果を受け取った後、監督が異常終了した後、または監督の締切を過ぎた時点のいずれでも、下の「全ての終了経路での group の掃除」を行ってから精算する。旧版の「`killpg` が ESRCH なら監督の pid へ送れば足りる」は撤回する（round 3 の High。2 つの送信の間に監督が `setsid` して verifier を起動すると、verifier が G に残るため）。`Popen` が戻った時点で G は存在する（下の「子の起動と group の確保」）ので、親が G へ送る時点で G は必ず存在する。
  - 旧版の「監督が verifier の pgid を起動直後に pipe で送る」方式は撤回する。受け渡しの前に監督が止まると、verifier の group へ親が届かない窓が残るためである。
  - verifier の締切は、予約時に観測した wall 時刻と monotonic 時刻の組から `child_deadline_at` を monotonic へ換算して渡す（現状の runner は Popen の後に締切を決める [S42] ので、前段の時間を締切に含めるため）。凍結 `timeout_sec` は変えず、`min(凍結 timeout, 換算した締切)` で打ち切る。予約された `run_sec` が凍結 `timeout_sec` より短く、F の締切で打ち切った場合は status を `blocked`、`block_reason` を `budget-deadline` とし、`timeout`（policy の判定）と区別する。予約の `run_sec` が凍結timeout以上の場合は、起動前の時間でmonotonicの締切が先になっても理由は `timeout` とする。B の receipt の閉じた `block_reason` 集合へ 1 値を足す。換算した monotonic の締切は job に入れて監督へ渡す。親と監督は別の interpreter だが、`time.monotonic()` は process ごとではなく system 全体で共通の時計を読む前提で、この前提は F2a のテスト（親が読んだ値と監督が読んだ値の順序）で確かめる。監督の exec に失敗した場合（`Popen` の例外）は、子が存在しないので予算付き session の `verification run` を `budget-deadline-unenforceable` で拒否し、予約を拒否として精算する。
- **command provider**: `start_new_session=True` で起動し、締切を予約時刻からの絶対時刻にする（起動後の記録 commit の lock 待ちも締切に含める）。`setsid` は exec の前に子の中で行われ、`Popen` は exec が成功するまで戻らないので、provider のコードが走る前に G（pgid = child の pid）が存在する。pipe の読取りは「締切 − 現在」で毎回計算する。**packet の stdin への書込みも締切付きにする**（Checker の Medium）。現状の `communicate(input=packet, timeout=)`（command_provider.py:568 [S20]）は reap するので使わず、stdin の fd を nonblocking にし、selector で書込み可能を待って（待ちの上限は「締切 − 現在」）chunk ごとに書き、書き終えたら stdin を閉じる。`os.write` の戻り値で書込み位置を進める（部分書込みのとき、残りを次の書込み可能で書く）。stdin への書込みが `EPIPE`（`BrokenPipeError`。`ECONNRESET` に当たるものを含む）で失敗したら、例外にせず「書込みの終了」として扱う（Checker の Medium。`communicate()` は child が stdin を閉じたときの `BrokenPipeError` を無視して出力の収集を続けるが、Python は SIGPIPE を無視するので手で書く `os.write` は例外を送出する）。stdin を selector から外して閉じ、stdout・stderr の読取りを EOF か締切まで続ける。その後は通常の経路と同じく reap せずに終了を観測し、group を掃除し、reap し、精算する（packet を読み切らずに終了した provider の終了コードと出力も通常の経路で記録する）。fd は必ず selector から外してから閉じる（閉じた fd を残すと select 系の selector が `EBADF` を返す）。stdout・stderr は EOF で selector から外す。締切までに書き終えなければ、書込みを打ち切って §4.1 の順序の回収と下の掃除へ進む。stdin を読まない provider に pipe の容量を越える packet を渡しても、親は締切を越えて止まらない。書込みと stdout・stderr の読取りは同じ selector の loop で行う（provider が stdin を読む前に出力を書いて pipe が詰まる形でも止まらない）。job file の方式は使わない。provider の protocol は stdin の pipe で packet を受け取る既存の契約で、これを変えないため。締切後は §4.1 の順序で回収する。正常終了の経路でも、child の終了を reap せずに観測してから group を掃除し、その後に reap する（`communicate()`・`wait()`・`poll()` は reap するので、終了の観測に使わない。観測の手段は下の「全ての終了経路での group の掃除」）。
- **adapter（D2c・E3）**: `observe_parent/launch/collect/cancel/recover` の各呼出しを、exec で起動した呼出し子の中で行う。呼出し子も B の監督と同じく下の「子の起動と group の確保」で起動し、親は `Popen` の戻り値の pid を group として扱う（受け渡しの窓を作らない）。各呼出しの後、正常終了でも下の「全ての終了経路での group の掃除」を行う。`recover` の呼出し子は §3.6 の予約の締切に従う。呼出し子の締切は `min(adapter_call_sec, child_deadline_at − 現在)`（`cancel` は `cancel_call_sec`）。戻り値は `max_output_bytes` と閉じた observation の上限で pipe 越しに受け取り、親は締切付きで読む。締切で `collect` の呼出し子を回収した後、`cancel` を呼び、その結果を D の `blocked`/`failed` terminal の `cancel_result` に記録する。[D02] `cancel` の呼出しが超過した場合や、adapter が起動した reviewer の終了を観測できない場合は `kill-unconfirmed` とし、reconcile の `recover` が終了を観測するまで同時予約数を解放しない。D2c は全ての adapter 呼出しを 1 つの application の seam から行うこと（判断事項 3）。
  - **D2a の pin 付き読込みとの関係**（round 4）。adapter の code の読込み（`load_adapter` → `_load_pinned_factory`。fresh_review_runtime.py:246-271・158-195 [S50]）は呼出し子の中、trampoline の後で行う。親は `resolve_adapter`（同 241-243。adapter の code を import しない）で得た pin を job に入れるだけで、再発見・pin の比較（同 254-256）・登録 digest の照合・compile・実行は子が行う。D2a の docstring（同 247-251）が求める「締切付きの callback 子で、repository lock の外で呼ぶ」をそのまま満たす。`_load_pinned_factory` の「`pin.module` が既に `sys.modules` にあれば拒否」（同 167-168）は、新しい interpreter では trampoline が adapter の module を import しない限り成り立つので、fork 版（親が import 済みなら子へ複写される）より強くなる。source pin の契約（登録 digest と同じ bytes から compile した code だけを実行する）は変えない。job の pin は閉じた decoder で復元し、偽の pin は子の `current != pin` の比較で `fresh-review-adapter-pin-changed` になる。呼出し子は 1 回の呼出しごとに adapter を読み直す（呼出し子をまたいで instance を持ち越さない）。`load_adapter` を呼ぶ箇所は main にまだ無い（9c948878 で検索）ので、D2c がこの seam として最初の呼出し箇所を作る（§6.1 の他の設計への要求）。
- **承認 verifier**: 既存の固定上限（5 秒の待ちと 0.2 秒の待ち）は保つが、起動と終了を下の 2 つの規則へ揃える（round 3・round 4 の High。F2p の範囲の要求）。現状の `_run_approval_verifier`（mission-state.py:10406-10433 [S22]）には 3 つの穴がある。(1) `multiprocessing` の fork で子を起動する（同 10409・10413）ので、import 済みの library が `os.register_at_fork` で登録した after-fork hook が、子の `setsid`（同 10335-10338）より前に親の group の中で走り、そこで起動された孫は G に入らない。(2) 正常終了の経路で `child.join` が直下の child だけを reap し、group の子孫を掃かない。callback が pipe を閉じて孫を残すと、予約の精算後も孫が動き続ける。(3) `_approval_verifier_child` は `os.setsid()` の失敗を `contextlib.suppress(OSError)` で握りつぶすので、group を作れないまま callback を走らせうる。変更は次に限る。
  - **registry 由来の verifier**（`_configured_approval_entry_point` が返す dict。§3.2 #5 はこの形だけを使い（mission-state.py:5547-5550 [S40]）、#6 は同名の in-process の登録が無ければこの形を使う（同 10440-10445 [S41]）。配布の CLI の process は in-process の登録を持たない）は、下の trampoline で exec 起動する。`_approval_verifier_child` の本体（entry point の再発見・distribution の検査・module と value の照合・source digest の照合・`load`・呼出し。同 10339-10382）を lib の module へ移し、trampoline から呼ぶ。distribution の検査は既に lib にある（mission_application/runtime_guard.py:103-192 の `validate_registered_approval_entry_point_distribution`）ので、trampoline は mission-state.py を import しない。request は上の job file、結果は上限付きの JSON の frame で渡し、結果が JSON に直列化できない場合は拒否する（fork 版は `multiprocessing` の pipe で pickle を渡していた）。`setsid` は exec の前に行われ、失敗すれば `Popen` が例外を投げて子の code は走らないので、(3) の握りつぶしはこの経路から消える。親はこの例外を `approval verifier rejected the evidence` と同じ拒否にする。
  - **in-process で登録した callable**（`register_approval_verifier`、mission-state.py:10096-10100。host が module を import して登録する API で、配布の CLI は登録を持たない）は exec の子へ渡せない。予算付き session の #6 で同名の callable の登録が見つかれば、spawn せず `budget-deadline-unenforceable` で拒否する。policy の無い session ではこの形だけ fork の経路を残し、round 3 の起動の規則（子の最初の処理で `setsid`、失敗すれば readiness を書かずに終了、親は readiness を受け取ってから go を送る）を保つ。(3) は握りつぶさずに callback を走らせず終了する形へ、(2) は下の掃除の規則へ直す。(1) の窓はこの経路にだけ残る（予算の保証には関わらない。判断事項 19）。
  - どちらの経路でも、`child.join`（exec 経路では `Popen.wait`）で待たず、leader の終了を reap せずに観測し、受信 pipe を締切付きで待つ。正常終了・拒否・timeout のどの経路でも、reap の前に group を掃除する。予算 policy の無い session にも同じ変更が入る（反例が policy の有無に依存しないため）。

**子の起動と group の確保（B の監督・adapter の呼出し子・承認 verifier の子・provider に共通。round 4 の High）**。予算付きの子は Python の process を fork して起動せず、全て exec で起動する。round 3 版の「fork の後に子の最初の処理で `setsid` し、readiness と go の handshake で待つ」方式は撤回する。fork で起動すると、どの import 済み library が登録した after-fork hook も、子の `setsid` より前に親の group の中で走る。hook が孫を起動すると、その孫は G の外にいて、親は G が空になったことを確かめて精算した後も、孫が final の時間帯まで動き続けうる（「G を自ら抜ける子孫」とは別の、G へ入る前に起動された子孫）。
- 起動は共通の helper 1 つだけが行う。B の監督・adapter の呼出し子・承認 verifier の子は `subprocess.Popen([sys.executable, "-I", <trampoline の絶対パス>, <job file の絶対パス>, <job の sha256>], start_new_session=True, close_fds=True, pass_fds=(結果の fd,), stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)`、provider は既存の argv を同じ helper の `start_new_session=True`・`close_fds=True` で起動する（trampoline を通さない）。helper は `preexec_fn`・`process_group`・`user`・`group` 等を受け取らない（`preexec_fn` を渡すと、fork の後に子で Python の code と after-fork hook が走る）。
- CPython の `_posixsubprocess` は、`preexec_fn` が無ければ fork の後に子で Python の code を走らせず、exec の前に子の中で `setsid` を呼び、`Popen` は exec の成功を確かめてから戻る（`setsid` か exec の失敗は `Popen` の例外になる）。したがって `Popen` が戻った時点で G（pgid = 子の pid）が存在し、子の Python の code（interpreter の起動、site の処理、trampoline、target）は全て G の中で走る。2026-10-04 に macOS・Python 3.14.6 で、`os.register_at_fork(after_in_child=…)` を登録した process から起動した `Popen(start_new_session=True)` の子では hook が走らず、子の pgid と sid が子の pid に等しいこと、同じ process から `multiprocessing` の fork で起動した子では hook が親の pgid のまま走ることを確かめた（各 1 回）。
- `-I` は `PYTHON*` 環境変数と user site を無視させ、script の directory も `sys.path` に足さない。このため `-I -m <module>` は repository の lib の module を見つけられない（同日、同じ環境で `No module named` を確認）。trampoline は module 名ではなく絶対パスで起動し、自分の位置から lib の directory を `sys.path` の先頭へ足す（mission-state.py:61-63 と同じ方式）。system の site-packages の `.pth` と `sitecustomize` は `-I` でも読まれうるが、それらは G の中で走るので、起動する孫も G に入る。
- trampoline（新 module。`skills/mission/lib/mission_application/spawn_trampoline.py` を想定）は固定の最小の入口で、argv で渡された job file から上限付きの JSON の job を 1 つ読み（下の「job の受け渡し」）、job の種類（閉じた集合: `verification-supervisor`・`adapter-call`・`approval-verifier`）ごとの閉じた schema で復元し、対応する target（B の runner、pin 付きの adapter の読込みと 1 回の呼出し、承認 verifier の本体）を import して呼び、結果を `pass_fds` の fd へ上限付きの JSON の frame で書く。種類に無い job・上限超過・復元の失敗・job file の検査や sha256 の照合の失敗は、target を import せずに非 0 で終了する。trampoline は target の module 以外の利用者の code を import しない。
- **readiness の handshake も go も無くす。** G の存在は `Popen` の戻りで確かめられるため。予約 commit は spawn の前に済んでいる（§3.2）ので、起動と作業の開始の間に親が commit するものは無く、go は要らない。
- **job の受け渡し（round 5 の High）**。round 4 版の「`stdin=PIPE` で job を書くことを go とする」方式は撤回する。親の締切が効くのは読取りだけで、書込みは締切を持たないため、job が pipe の容量を越え、system の `.pth` や `sitecustomize` が trampoline の stdin の読取りより前に止まると、親の blocking な書込みが戻らず、締切も group の掃除も起きない（設計レビュー round 5 で、子が読まない間は 1 MiB の書込みが止まったままであることが確かめられた）。代わりに親は **spawn の前に** job を private な file へ書き、その絶対パスと sha256 だけを argv で渡す。spawn の後に親が子へ書くものは無く、親が止まりうる操作は締切付きの待ち（終了の観測と結果の fd の読取り）だけになる。
  - 置き場所は mission の state の directory の下の、利用者が所有する mode 0700 の directory（作成時と使用時に `lstat` で directory であること・symlink でないこと・`st_uid` が実行 uid であること・mode が 0700 であることを確かめ、違えば dispatch を拒否として精算する）。file は作成した親の pid と process の開始時刻、予約 ID（予約の無い job では省く）、乱数から名前を作り（round 6 の Medium。下の削除の規則で所有者の生存を判定するため）、`O_CREAT | O_EXCL | O_NOFOLLOW` と mode 0600 で作って fsync し、作成後に regular・link 数 1・mode 0600・大きさと内容の一致を確かめる。repo には同じ形の書込み（`_write_private_file`。local_uow.py:339-361 [S51]）と 0700 の directory の確保（`_ensure_directory`。fenced_commit.py:1624-1640 [S52]。owner の検査は持たない）があるので、これを共通の helper へ寄せて使い、owner の検査を足す。
  - **書込みの失敗（round 6 の Medium）**。job file の作成・書込み・fsync・作成後の検査のどこで失敗しても（ENOSPC を含む）、親は spawn せず、途中まで書いた file を best effort で削除し（削除にも失敗したら、そのパスと errno を拒否の記録に残す。残った file は下の削除の規則で後から消える）、予約を「拒否・未起動」として理由 `budget-job-write-failed` で精算する。未知の消費（`charged-full-unknown`）にはしない（子は存在しないので消費は 0 である）。現状の `_write_private_file`（local_uow.py:339-361 [S51]）は失敗時に部分 file を残して例外を投げるだけなので、共通の helper へ寄せる時に削除を足す。予約の無い job（policy の無い session の承認 verifier）は同じく file を消し、既存の `approval verifier rejected the evidence` と同じ拒否にする。
  - 子（trampoline）は file を `O_NOFOLLOW` で開き、`fstat` で regular・link 数 1・`st_uid` が自分の uid・mode 0600 を確かめ、上限付きで読み、argv の sha256 と照合してから復元する。regular file の読取りなので締切を越えて止まらない。
  - job には credential を入れない（job の中身は pin・締切・閉じた request で、構成上 credential を含まない。decoder は閉じた schema なので credential を運ぶ field が無い）。argv に出るのはパスと sha256 だけである。
  - 親は下の「全ての終了経路での group の掃除」の後に job file を削除する（`kill-unconfirmed` の場合も削除する。子は既に読み終えているか、読めずに拒否で終わる）。exec の失敗（`Popen` の例外）でも削除してから拒否として精算する。
  - **残った file の削除（round 6 の Medium）**。親の crash で残った file は `budget reconcile` と CLI の起動時に削除するが、**予約の有無だけでは判定しない**。policy の無い session の承認 verifier の job は予約を持たないので、旧版の「開いていない予約の file を削除する」では、並行して起動した別の CLI が、子がまだ読んでいない生きた job を消しうる。削除してよいのは、予約付きの job ならその予約が開いておらず、**かつ**（予約の有無に関係なく）名前の pid の process が存在しないか、存在しても開始時刻が名前と違う file だけとする。開始時刻は Linux では `/proc/<pid>/stat` の starttime、macOS では `sysctl` の `KERN_PROC_PID` の `p_starttime` を読む（外部 command は起動しない）。開始時刻が読めない・名前が規則に合わない場合は削除しない（生きた job を消す側へ倒さず、file の残留の側へ倒す）。この判定の helper は F2p に置き、起動時の削除と F2a の `budget reconcile` が共有する。2 つの OS で開始時刻が読めることは未実測（**UNKNOWN**。F2p のテストで確かめる）。
  - 親から子への経路は job file だけで、他に要るものは無い。今後足す場合も、締切付きの nonblocking な書込みに限る。
- interpreter の起動と job file の読取りにかかる時間は子の締切に含める（承認 verifier は起動からの 5 秒に含める）。子の締切までに結果が来なければ、子が job を読んだかどうかを問わず §4.1 の順序と下の掃除をそのまま行う。exec に失敗した場合（`Popen` の例外）は子が存在しないので、その dispatch を拒否として精算する。
- これにより、親が G へ送る時点で G は必ず存在し、ESRCH の窓は無い。また子の Python の code が G の外で走る時間も無い。

**全ての終了経路での group の掃除**（正常終了・拒否・例外・締切のいずれも）。結果を受け取った後（または締切後の §4.1 の順序の途中）、**leader を reap する前に** `killpg(G, SIGKILL)` を送り、leader を reap し、`kill_wait_sec` 以内に G が空になる（`killpg(G, 0)` が ESRCH を返す）ことを確かめる。確かめられなければ `kill-unconfirmed`（全額計上。§3.3）とする。**精算はこの掃除の後に行う。**
- leader が未 reap（生存中か zombie）の間は pid が再利用されないので、G が別の group を指すことは無い。leader を reap した後も、G に member が残る間は POSIX 上その id は再利用されない。
- leader の終了は reap せずに観測する。全ての予算付きの子が `Popen` で起動されるので、`os.waitid(P_PID, pid, WEXITED | WNOWAIT | WNOHANG)` の短い間隔の poll か、kqueue の `EVFILT_PROC`/`NOTE_EXIT` を使う（`WNOHANG` の無い `os.waitid` は締切なしで待つので使わない）。2026-10-04 に macOS・Python 3.14.6 で `os.waitid` と `os.WNOWAIT` が使え、`WNOWAIT` で観測した後も `waitpid` で reap できることを確かめた（旧版の UNKNOWN を解消。Linux は F2p の CI で確かめる）。`Popen.poll()`・`wait()`・`communicate()` は reap するので使わない。helper は `Popen` の object を掃除の後まで保持する（参照を失った `Popen` は後続の `Popen` の生成時に `subprocess` の内部の後始末で reap されうるため）。policy の無い session の callable の承認 verifier（fork の経路）だけは `multiprocessing` の sentinel を使う。
- zombie の孫に対する `killpg(G, 0)` が ESRCH を返すまでの時間（reparent 先が reap するまで）は macOS で未実測（**UNKNOWN**）。返らなければ `kill-unconfirmed` になり、過大計上の側に倒れる。

**前提（保証の外）**: 上の上限は、親 process が締切後の待ちを定数どおりに進められることを前提とする。親の停止・OS の stall・lock の 5 秒待ちが `commit_margin_sec` を越えて続く場合、精算は `settle_by` より遅れうる。そのときも子は回収済み（または `kill-unconfirmed`）で、遅れは `late_settlement_sec` として記録し、計上は予約秒を下回らない。

**限界（成功として扱わないもの）**:

- 親 process が crash した後の orphan は、mission から回収できない。予約は `settle_by` 後に全額計上され、結果は記録されない。B の runner は一時ディレクトリの複製で実行するので候補を書き換えないが、一時ディレクトリが残るかは実装で確かめる（**UNKNOWN**）。
- 監督・呼出し子・承認 verifier の子の group G（provider では起動した child の group）を自ら抜ける子孫（`setsid` や `setpgid` で別の group へ移るもの）は、process group の送信から漏れる。G を起動の時点で親が確保し、全ての終了経路で掃除することで閉じるのは「G が存在しない窓」と「正常終了の後に G に残る子孫」だけで、G を抜けた子孫は掃除の確認（`killpg(G, 0)` の ESRCH）にも現れないので、`kill-unconfirmed` にもならない。この漏れは残る限界として扱い、OS の権限境界の代替を主張しない。
- fork で起動した子の after-fork hook が `setsid` の前に起動する孫（round 4 の High）は、予算付きの子を全て exec で起動することで閉じる（上の「子の起動と group の確保」）。fork の経路が残るのは policy の無い session の callable の承認 verifier だけで、その経路ではこの漏れも残る（判断事項 19）。
- adapter が host 側で起動した reviewer の停止は adapter の `cancel` に依存する。F は `cancel` の呼出しを有限にするだけで、その効果は観測結果として記録するにとどまる。

### 4.3 終端と精算の state 容量（設計レビュー round 1 の High 4）

決定: F の予約は、その dispatch の**終端と精算を書く bytes** も、spawn の前に E0 の機構で確保する。E の改訂設計は、書込み種別ごとの最大増分 Δ を最大形の encode で測って固定し、終端していない item ごとに「残りの段の Δ の和」を state から導出し、全 writer で `state_capacity_verdict(base, proposed, encoded_len)` 1 つで `len(encoded) + Σ残り予約 ≤ STATE_LIMIT − S_sys_remaining` を検査する（S_sys_remaining は未書込の halt slot 分と残り takeover 回数 `max(0, N_L − 記録済み回数)` の分の残量で、proposed の state から導出する。halt・takeover の消費や reactivate による残量の回復があっても予約済み item の残り予約は減らず、reactivate は検査に通らなければ拒否される）。[D05]

- **開いている予約を E0 の「終端していない item」として扱う。** 予約 1 件の残り予約 = `Δ_settle + Δ_terminal(entry)`。
  - `Δ_settle`: F の精算行（`Settlement`・`PhaseCharge` の更新・`ProgressSignature` の新規行を含む最大形）
  - `Δ_terminal(entry)`: B の verification receipt（#1）、provider の terminal 更新（#3）、承認 verifier の receipt / force の `force_approval`（#5・#6）は F が新しく予約する。D/E の入口（#2・#7・#8）と回復（#9 の `recover`・`system-recover`。書く terminal は回復する D/E の dispatch のもの）は E0 が既に D request・repair attempt・disposition の段として予約しているので 0 とし、二重に予約しない
- **予約 commit が容量の検査点になる。** 予約行自身の Δ と上の残り予約を足して `state_capacity_verdict` が通らなければ、`state-capacity-exhausted` で拒否し、起動しない。4 MiB [S28] の近くで予約が通った場合も、終端と精算の bytes は既に確保されているので、終端の commit は検査を通る（通らなければ Δ の定数の欠陥で、E0 の `state-capacity-invariant-broken`）。
- **予約を持たない B receipt の扱いを変える**: E の改訂設計は B receipt を予約された終端として扱わず、公開時に超えたら拒否する。[D05] F の policy を持つ session ではこれを置き換え、B receipt は予約に含まれる終端となる。policy の無い session では E の規則のまま。
- **停止系の書込み**: `StopSlots`（拒否の計数、final latch、exhaustion、`BudgetStop`）は policy の受付時に確保する固定長 slot で、以後は上書きだけ（増分 0）。`budget stop` の書込みは「halt の field と F の slot の上書きだけ」になるので、E0 が増分 0 の停止系と判定する対象に含めてもらう（E0 への要求。E の main の設計 §2 で受け手が定義済み）。拒否の記録が容量で失敗した場合も起動しない。
- **所有**: Δ の表・最大形の encode test・`state_capacity_verdict`・S_sys_remaining・全 writer の検査は E0（E0b #918）が持つ。F は自分の書込み種別（予約行・精算行・slot・B receipt と provider terminal と承認 receipt の予約）の Δ 定数と最大形 test を、E0 の拡張点へ足す（F1 で型と Δ、F2a で入口の配線）。F は E0 の予約量を変えず、時間の予約は F、bytes の機構は E0 という分担を保つ。

### 4.4 修復が最終検証の時間を使わないこと（設計レビュー round 1 の High 2）

決定: **保証を「mission の spawn 入口（§3.2 #1〜#11）から起動した final 以外の作業（repair 系と非保護の分類の dispatch）は final の予約を使えない」に狭め、host の inline 作業（host が起動する subagent を含む。§3.2 #16）は強制の外とし、量を記録する。** 選んだ理由: mission は host の inline 編集・実行と host の subagent を観測も中断もできない。repair の締切以降に state の mutation を拒否しても、ファイルの編集は止まらず、記録だけが失われる。止められないものを止めると称するより、止められるもの（spawn）を確実に止め、止められないものを数えて出すほうが正確である。

強制できる境界は次のとおり。

- repair 系の入口の `settle_by` は repair の締切を越えない（§3.1）。repair の締切以降、repair 系の dispatch は child の段階で既に回収されている。
- `at ≥ repair の締切` で final latch が導出され、repair 系の入口と `repair begin` は `budget-final-latched` で拒否される（§3.1）。`next` の助言に依存しない。
- `repair begin` は、B の最小実行が repair 枠に収まらない時点では拒否する（§3.2 #10）。時間の予約はしない（attempt 自体は process を起動しないため）。

強制の外にあるもの: repair の締切の後も host が inline で修正を続けた時間。これは final 枠を実質的に削る（overall_deadline までの残りが減る）。F はこの量を `reserve_erosion_sec`（final latch の後、または非保護枠の締切の後に、final 系の予約が開いていない稼働時間の合計）として status と停止記録に出す。final verification は final の締切までの残りが `min_final_run_sec` に満たなければ拒否され、その時点で `final_infeasible` が成立して exhaustion になり（§5）、session は partial-done で終わる。成功は作らない。

受入条件もこれに合わせて書く（§6.3）: 「repair の dispatch は final 予約の時間帯に実行されない」「inline の侵食は `reserve_erosion_sec` に現れ、その結果 final が実行できない場合は pass にならない」。旧版の「repair 枠が残ること」を inline 作業まで含めて保証する書き方はしない。

## 5. Exhaustion、停止、完了 gate（設計レビュー round 1 の High 5）

- **final が実行できなくなった時点を kernel が導出する**（設計レビュー round 2 の High 3）。旧版は admission が final の dispatch を時間不足で拒否した後も、`overall_deadline − closeout_margin_sec` までは exhaustion が成立せず、その間に pass（force を含む）が通りえた。
  - `min_final_run_sec = min_dispatch_sec + max(final 系の入口の cleanup_sec) + commit_margin_sec`（初期値 1 + 16 + 10 = 27 秒。final 系の入口は §3.1 の表で final に分類されうる入口で、最大は adapter）。必要な final がどの入口かを kernel は知らないので、最も長いものを使う（pass を拒否する側へ倒す）。
  - `final_infeasible(ledger, at) = final の締切（§3.1）− at < min_final_run_sec`。admission が final 分類の最小の dispatch を時間不足で拒否する条件と同じ関数から導出し、両者がずれないようにする。final の締切は稼働時計上の固定点なので、`at` が進むほど残りは減り、一度成立すれば戻らない。
  - `final_recorded(ledger) = FinalRun slot が記録済み`。final latch の後に、final 分類の予約が `settled` で精算され、かつその入口の結果が完了（B は `blocked` でない receipt、provider は terminal、D は `blocked`/`failed` でない terminal、承認 verifier は受理）だったとき、精算と同じ commit で slot を上書きする。`charged-full-unknown`・`kill-unconfirmed`・`budget-deadline` の打ち切りは記録しない。F は「final が 1 回完了した」ことだけを記録し、その結果で pass してよいかは既存の gate（acceptance contract・force の承認）が判定する。
- **exhaustion は kernel が導出する一方向の状態とする。** `exhausted(ledger, at)` は次のいずれかで成立する。
  1. `at ≥ overall_deadline − closeout_margin_sec` かつ final 分類の予約が開いていない
  2. `at ≥ overall_deadline`
  3. `final_infeasible(ledger, at)` かつ `final_recorded` でない かつ final 分類の予約が開いていない（開いている final の dispatch が完了すれば `final_recorded` になりうるので、その精算を待つ）

  3 は contract の有無を見ない。contract の無い予算付き session でも、final が 1 回も完了しないまま final の時間が尽きれば exhaustion になる。final の時間が残っている間は、final を実行せずに pass することを F は止めない（既存の gate に従う）。予算に触れる mutation（予約・精算・拒否の記録・`budget stop`・`mark-passes`・Reactivate）は、`at` で exhaustion が成立していれば `Exhaustion` slot に時刻と原因を書く。一度書いた slot は戻らない。slot が未記録でも、guard は `at` から同じ判定をするので、記録の有無で結論が変わらない。
- **exhaustion 後に許すもの**: adapter を呼ばない ledger だけの精算と reconcile（§3.2 #11、`budget reconcile`）、回復専用枠の範囲での `system-recover`（§3.6。枠と `overall_deadline` で上限がある）、`budget stop`、`mark-halt`（任意の category）、read-only の query。**拒否するもの**: 回復専用枠以外の新しい予約（全入口。通常の `recover` を含む）、`repair begin`、`budget enter-final`、Reactivate、`mark-passes`（force を含む）。
- **完了 guard を全ての予算付き session に適用する。** kernel の `_mark_pass` に、`_acceptance_completion_ready`（contract が無いと何もせず戻る [S36]）とは別の guard `_budget_completion_ready` を足し、ledger を持つ全 session で評価する。条件は (1) 開いている予約がある（回復専用の予約を含む）→ `budget-dispatch-unsettled`、(2) exhaustion が成立 → `budget-exhausted`（原因が上の 3 なら停止記録の原因に `final-infeasible` を残す）。(2) は slot の記録ではなく `at` からの導出で評価するので、final の時間が尽きた直後の pass も、slot が書かれる前に拒否される。評価の位置は `_mark_pass` の先頭、force の分岐（transitions.py:1075）より前とする。[S36] application の preflight でも、contract の有無の分岐（review.py:516）の外で、force の承認 verifier の spawn（review.py:523）より前に同じ純関数を呼ぶ。[S35] これで、予算切れの session は force でも pass に到達せず、承認 verifier も起動しない。`passes` を書くのは `_mark_pass` だけなので [S46]、他の経路は無い。
- **予算停止**: `budget stop` は、exhaustion が成立しているか、kernel が ledger から「必要な次の dispatch がどれも予約できない」ことを導出できる場合だけ受け付け、同じ transition で `MarkHalt(category=partial-done)` と `BudgetStop{scope, reason_code, at, ledger_digest, open_reservations, reserve_erosion_sec}` を保存する。[S26][S27] 予算が残っているのに使うことは `budget-not-exhausted` で拒否する。既存の `mark-halt --category partial-done` はそのまま使えるが `BudgetStop` は残さない（I が「予算による停止」と「他の理由の partial-done」を区別できるようにするため）。新しい HaltCategory は足さない。
- **`next` は助言のまま**: 非保護枠の締切を過ぎたら spawn 系の action を「final へ移る」助言へ、exhaustion なら `budget stop` の助言へ変える。terminal・await-user・安価な確定手を差し替えない既存の規則は保つ。[S03][T01] 強制は上の guard と admission が持ち、`next` を読まない host でも結論は同じになる。
- **予算は完了を許可しない。** 既存の gate は一切緩めない。理由コードの優先順は、D の既定の後ろに `budget-dispatch-unsettled`、`budget-exhausted` の順で置く。

## 6. 変更面、保持する保証、検証方針

### 6.1 変更面

| 層 | 追加・変更 |
|---|---|
| kernel | 新 module（policy/ledger の型・decoder・`admit`・`budget_class`・`cleanup_sec` の表・回復と回復専用枠の admission（§3.6）・`final_infeasible` と exhaustion の導出・reducer・`BUDGET_SPAWN_ENTRIES`）。commands union と transition registry に `ReserveDispatchBudget`・`SettleDispatchBudget`・`EnterFinalPhase`・`BudgetStop`。lifecycle の reducer（MarkHalt・MarkPass・Reactivate・ResumeStale）に ledger がある場合だけ時計の区間の開閉と exhaustion の拒否。`_mark_pass` の予算 guard。generic set の保護集合の拡張 [S07]。E0 の種別表への F の Δ |
| codec | v4 の `budget_ledger` と v5 の `extensions.budget_ledger` の閉じた復元と、保存面との一致検査 |
| application | `init --budget-policy` の受付（coverage の検査付き）、`budget status/enter-final/stop/reconcile` の use case。§3.2 の各入口での予約・精算（回復の予約を含む）。exec の trampoline（新 module）と共通の起動 helper（`start_new_session=True`・`close_fds=True`・`preexec_fn` 無し、reap しない終了の観測。§4.2）、監督 process（B。trampoline で起動し、verifier を新しい session なしで起動する runner の引数を含む）、adapter の呼出し子（trampoline の中で D2a の `load_adapter` を呼ぶ）、provider の process group 回収と有限の待ち、全ての終了経路での group の掃除（共通の helper）、承認 verifier の起動と掃除の変更（registry 由来は trampoline、callable は policy の無い session だけ fork。§4.2）。mark-pass の preflight の予算 guard |
| CLI | parser と 1 use case 呼出しだけ。thin-adapter の baseline を増やさない |
| inventory | `command_owners.py` に mutation を A1.lifecycle（`budget stop`・`budget enter-final`）と A3.evidence（`budget reconcile`）へ、`budget status` を R1.query へ登録する [S29]。spawn 箇所の inventory test（§3.5）。guidance parity の入力に `$.budget_ledger` [S06]。配布 mirror の同期 |
| 他の設計への要求 | D2c: envelope に `deadline_at`、全 adapter 呼出しを 1 つの seam から（判断事項 3）。その seam は adapter の呼出しを fork でなく §4.2 の exec の呼出し子で行い、`load_adapter` を子の中で呼ぶ（親は `resolve_adapter` の pin だけを渡す）。D2c・E2・E3: dispatch の intent に予約の `reservation_id` と分類を同じ commit で書く（回復の分類の入力。§3.6）。E0: F の書込み種別を Δ の表へ、F の slot の上書きを停止系の判定へ（§4.3）。I/G: Mission arm へ policy と `external_deadline_at` を渡す（判断事項 10） |

### 6.2 保持する保証（緩めない）

| `skills/mission/tests/` 配下の file::test | 保持する保証 |
|---|---|
| `test_issue238_budget_pressure.py` の全 9 件 [T01] | ledger の無い session の budget pressure・warn・差し替え・非差し替えを変えない |
| `test_issue878_candidate_snapshot.py::test_runner_bounds_output_times_out_and_rejects_zero_test_count` [T02] | policy の timeout は `timeout` のまま blocked。F の締切は別の理由で区別する |
| `test_issue878_verification_runner.py::test_same_operation_retry_runs_the_verifier_again_and_rejects_a_different_receipt` [T03] | B の再実行の契約。F は回ごとに予約を取る |
| `test_issue895_fresh_review.py::test_budget_and_criterion_rejection_has_no_public_effect` / `test_packet_budget_and_runtime_commands_remain_closed` [T04] | D の request の予算 field の閉じた検査 |
| `test_issue742_stop_guard_timeout.py` の budget 系 [T05] | stop guard の 8 秒予算は別の仕組みで、F は触れない |
| E 設計 §8 の保持表と D 設計 §7 の保持表 [D03][D04] | completion gate・force・codec・fence・operation 再応答の保証 |

本書の検索（`timed_out|killpg|command provider timed out` を `skills/mission/tests` で）では、command provider の timeout 経路を直接検査するテストは見つからない。F2a で Red から足す。

### 6.3 検証方針

- TDD で、現実的な故障を先に Red にする: 初回の dispatch が repair・final の予約を食い尽くす、retry の二重計上と欠落、crash 後の予約の 0 計上、halt 中の時間の計上、時計の後退、並行予約の二重配賦、締切後も child が残る（孫 process を含む）、provider の回収が終わらない、pipe の容量を越える packet を stdin を読まない provider へ渡し、親が締切で回収と掃除を終えられない（Checker の Medium）、packet の途中で stdin を閉じて終了する provider の出力と終了コードが記録されない（`EPIPE` が例外の経路へ落ちる。Checker の Medium）、adapter の `collect` と `cancel` が戻らない、B の監督 process の読取りが戻らない、4 MiB の近くで予約した dispatch の終端が書けない、exhaustion 後や final の時間帯に予約なしの `recover` が走る、回復専用枠を越えて回復が続く、監督が verifier の起動直後（pgid の受け渡し前）に止まり verifier と子孫が残る、import 済みの module が `os.register_at_fork(after_in_child=…)` で孫を起動する hook を登録した状態で B の監督・adapter の呼出し子・承認 verifier の子を起動し、その孫が G の外で精算の後も生きている（round 4 の High）、`PYTHONPATH` や user site に置いた `sitecustomize` が子で読まれる（`-I` が効いていない）、system の site-packages に相当する場所（fixture の venv）の `.pth` が起動時に孫を起動し、その孫が掃除の後も生きている（G の中で走ることの確認）、上限に近い大きな job と、起動時に stdin を読まずに止まる `.pth`（または `sitecustomize`）の組で子を起動し、親が子の締切で回収と掃除を終えられない（round 5 の High。`stdin=PIPE` の書込みでは親が止まる）、job file の書換え・差替え（sha256 の不一致・symlink・link 数 2・mode や owner の違い）で子が target を始めてしまう、掃除の後（`kill-unconfirmed`・exec の失敗・親の crash 後の `budget reconcile` を含む）に job file が残る、`Popen` の object を手放した後に leader が掃除の前に reap される、予算付き session の force pass で callable の承認 verifier が spawn される、`setsid` が失敗した子が callback を走らせる、正常終了した B の監督・provider・adapter の呼出し子・承認 verifier の子が pipe を閉じて孫を残し、精算の後も孫が生きている、final の dispatch が時間不足で拒否された直後に pass（force・**契約の無い session** を含む）が通る、final の dispatch が開いている間に `final_infeasible` で exhaustion になり完了した final を捨てる、`budget stop` が予算の残る session で通る、開いている予約がある状態での mark-passes（**契約の無い session を含む**）、exhaustion 後の force pass、`--phase` を偽った provider が final へ計上される、`pending` の入口が残る状態での `init --budget-policy`、表に無い spawn 箇所の追加、予約の無い承認 verifier の job を子が読む前に並行の CLI の起動時の削除が消す（round 6）、名前の pid が再利用されて別の開始時刻の process が生きている file を残す・開始時刻が読めない file を消す、job file の書込み・fsync・検査の失敗（ENOSPC を注入）で部分 file が残る・予約が開いたまま残る・`charged-full-unknown` で精算される、`reactivate: forbidden` の session で `--approved-by-user` 付きの Reactivate が通る・state（exhaustion の slot を含む）を変える。
- 受入（§4.4 の狭めた保証に合わせる）: 制御された時計の fixture で、(a) mission の spawn 入口から起動した repair 系の dispatch は final の予約の時間帯に実行されず、`settle_by ≤ repair の締切` であること、(b) inline の時間を模した稼働時間が `reserve_erosion_sec` に現れ、final が実行できない場合は exhaustion から partial-done になり pass にならないこと、(c) 外部締切が稼働時計より早い場合に外部締切で exhaustion になること、(d) final の dispatch が時間不足で拒否された時点から、final が完了として記録されていない限り pass（force を含む、契約の無い session を含む）が拒否されること、(e) 回復の呼出しが final の時間帯・exhaustion 後に予約なしで走らず、回復専用枠と `overall_deadline` を越えないこと。
- 変異（検出できることを確かめる対象）: 保護枠の差し引きを外す、`<` と `≤` の取り違え、repair の締切から `final_unspent` を外す、開いている予約を unspent から外す、`cleanup_sec` の項を 1 つ落とす、`charged-full-unknown` を 0 にする、精算を 2 回適用する、子の締切を `policy_timeout` だけにする、並行 dispatch を稼働時計へ足す、kill を直下の child だけにする、provider の締切を起動後の相対時間に戻す、provider の packet の書込みを blocking な書込み（`stdin.write` や締切の無い書込み）へ戻す（stdin を読まない provider で親が締切を越えて止まり、テストが落ちること）、stdin の書込みの `EPIPE` を例外として伝播させる（packet の途中で終了する provider の出力と終了コードが失われ、テストが落ちること）、`os.write` の戻り値を無視する（部分書込みで packet が欠け、テストが落ちること）、時計の後退を受け付ける、no-progress の候補比較を外す、予算 guard を contract 付き session だけにする、`budget_class` に `--phase` を使う、`Δ_terminal` を 0 にする、`total_sec` を切り上げる、`recover` の予約を外す、回復の分類を caller の引数にする、回復専用枠を補充する、`final_close` から `system_recovery_sec` を外す、監督を `start_new_session` で verifier を起動する旧方式に戻す、親の `killpg(G)` を監督の reap の後に移す、正常終了の経路で group の掃除を省く（孫が残り、テストが落ちること）、精算を掃除の前に行う、予算付きの子の起動を exec から `multiprocessing` の fork（子の最初の処理で `setsid` する round 3 の方式）へ戻す（after-fork hook の孫が G の外に残り、テストが落ちること）、helper に `preexec_fn` を渡す、`-I` を外す、job の受け渡しを spawn の後の `stdin=PIPE` の blocking な書込みへ戻す（大きな job と起動の stall で親が締切を越えて止まり、テストが落ちること）、子の sha256 の照合を外す、job file の `O_EXCL`・`O_NOFOLLOW` を外す、掃除の後の job file の削除を省く、残った file の削除から所有者の生存の判定を外す（予約の有無だけで消す round 5 の規則に戻す）、開始時刻が読めない場合に削除する、書込みの失敗時に部分 file の削除を省く、書込みの失敗を `charged-full-unknown` で精算する、`reactivate: forbidden` の検査を外す・`--approved-by-user` で迂回できるようにする・exhaustion の slot の記録より後に置く、終了の観測から `WNOWAIT` を外す、ESRCH のとき監督の pid だけへ送る旧 fallback に戻す、承認 verifier の `setsid` の失敗を握りつぶす、`kill_wait_sec` 内に G が空にならない場合に `kill-unconfirmed` にしない、exhaustion から 3 を外す、`final_infeasible` と admission の判定を別の式にする、`final_recorded` に `charged-full-unknown` を数える。各変異で少なくとも 1 件のテストが落ちること、正常系が通ることの両方を固定する。
- 抜け穴探索: `admit`・`budget_class`・exhaustion の導出・policy decoder・ledger decoder に対し、正常 / 拒否を合わせて 50 入力以上を独立に作って通す（境界の秒、bool を int として渡す、配分の合計が 9999/10001、未知 field、null、重複 ID、上限超過の開いた予約、逆順の時刻、締切ちょうど、`min_dispatch_sec` ちょうど、latch 後の repair、同時上限ちょうど、候補だけ違う無進捗、`budget_minutes` が 0.01/1.1/大きな値、外部締切が過去・null・稼働時計より後、`final の締切 − at` が `min_final_run_sec` の前後 1 秒、開いた final 予約の有無、回復専用枠の残りちょうど、`system_recovery_sec` が 42/43、total が 869/870 秒）。件数と発見数を PR 本文に書く。本設計では実施していない。
- 公開 CLI の end-to-end（fixture の verifier・provider・adapter）: 予約→実行→精算、締切での回収、crash 後の精算、予算停止、legacy の不変。
- 新規テストの費用は **UNKNOWN**。実装後に対象実行で計測する。CI は shard 経由で tracked tests を選ぶ既存経路を使う。

## 7. Reviewed lines と分割

全体の見積（追加+削除、配布 mirror 除外、未実測）。過去の設計見積が実測の約 1/1.6 だったため、素の見積と ×1.6 を併記する。repo の閾値は 600 行で説明、1,400 行で分割必須。[AGENTS.md:119-128][S34] 設計レビュー round 1 の決定（init 時だけの受付と coverage の検査、監督 process と adapter の呼出し子、承認 verifier 2 入口、容量の予約、全 session の完了 guard と exhaustion）で範囲が増え、3 分割では F2b が 1,400 を越える見込みになったため 4 分割にした（判断事項 7 の改訂）。round 4 の改訂（予算付きの子を全て exec で起動する）で F2a が 1,400 を越える見込みになったため、予算に依存しない exec の起動と承認 verifier の移行を F2p として切り出し、5 分割にする（判断事項 18）。

| PR | 範囲 | 依存 | 素の見積 | ×1.6 |
|---|---|---|---:|---:|
| F1: 予算の kernel | policy/ledger の型・decoder・codec、generic set 保護、時計の区間と外部締切、`admit`・`budget_class`・`cleanup_sec` の表・回復と回復専用枠の admission・`final_infeasible` と exhaustion の導出、全 reducer、`_mark_pass` の予算 guard（純関数）、`StopSlots`、F の Δ 定数と最大形 test（E0 の拡張点）、`BUDGET_SPAWN_ENTRIES`（全て `pending`）、`budget status`。policy を作る経路が無いので inert | E0 | 700〜850 | 1,120〜1,360 |
| F2p: exec の起動と承認 verifier の移行 | trampoline（閉じた job の種類と schema、上限付きの JSON の frame、lib の `sys.path` 追加）、共通の起動 helper（`start_new_session=True`・`close_fds=True`・`preexec_fn` 無し、`WNOWAIT` の観測、全ての終了経路での group の掃除と `kill-unconfirmed` の判定）、承認 verifier の本体の lib への移動と exec 起動、callable の経路の `setsid` の握りつぶしの廃止と掃除、spawn の前の private な job file の書込みと子の検査（round 5）、書込みの失敗時の部分 file の削除と、pid と開始時刻による所有者の生存判定・起動時の残った file の削除（round 6）、after-fork hook・`-I`・大きな job と起動の stall・job file の改ざん・`setsid` の失敗・並行の起動時の削除・書込みの失敗のテスト。予算に依存しないので policy の無い session の挙動の変更（承認 verifier）だけを持つ | なし（main） | 510〜660 | 816〜1,056 |
| F2a: 既存入口の admission と bounded kill | #1・#3・#4・#5・#6 の予約と精算、B の監督 process（trampoline の `verification-supervisor` の job と runner の引数）、provider の絶対締切・process group・有限の待ち（F2p の helper を使う）、`budget reconcile` での残った job file の削除（F2p の生存判定を使う）、job file の書込みの失敗の `budget-job-write-failed` での精算、予算付き session での callable の承認 verifier の拒否、strict の拒否、crash 後の精算と `budget reconcile`、mark-pass の preflight、no-progress、spawn 箇所の inventory test（既存入口を `covered`、D/E を `pending`、helper 経由と `preexec_fn` 無しの検査） | F1、F2p | 605〜820 | 968〜1,312 |
| F2b: D/E 入口の admission | #2・#7〜#10 の予約と精算（#9 の回復の予約と回復専用枠の配線を含む）、adapter の呼出し子（trampoline の `adapter-call` の job、子の中での `load_adapter`）と `cancel` の順序、`repair begin` の検査、表の D/E を `covered` | F2a、D2c・E2・E3 が main にあること | 560〜740 | 896〜1,184 |
| F2c: 有効化と停止（Closes #881） | `init --budget-policy`（coverage の検査付き、`pending` 0 を要求）、`budget enter-final`・`budget stop`、exhaustion 後の Reactivate 拒否、policy の `reactivate` field（decoder への追加。policy を作る経路は F2c までは無いので保存済みの policy との互換は問題にならない）と `forbidden` の session での Reactivate の拒否（round 6）、`next` の差し替え、end-to-end と受入 fixture | F2b | 490〜660 | 784〜1,056 |

合計 2,865〜3,730 行（×1.6 で 4,584〜5,968 行）。round 6 の改訂（job file の名前への pid と開始時刻、所有者の生存判定、書込みの失敗の処理で F2p +50〜60、F2a +15〜20。I の要求の `reactivate` field で F2c +40〜60）で round 5 版の 2,760〜3,590 行から増やした。round 5 の改訂（job を spawn の前に private な file で渡す。F2p +40〜60、F2a の reconcile での削除 +10〜20）で round 4 版の 2,710〜3,510 行から増やした。round 4 の改訂（exec の trampoline と共通の起動 helper、承認 verifier の本体の lib への移動、adapter の呼出し子の job、after-fork hook のテスト。readiness の handshake は削る）で round 3 版の 2,360〜2,980 行から増やした（F2p を新設して 420〜540、round 3 で F2a に入れた掃除の helper と承認 verifier の変更を F2p へ移して F2a は 690〜850 から 580〜780、F2b +40〜60）。round 3 の改訂（readiness の handshake、全ての終了経路での group の掃除、承認 verifier の変更）で round 2 版の 2,270〜2,870 行から増やした（F2a +70〜80、F2b +20〜30。F2b は adapter の呼出し子が F2a の helper を使う配線とテスト）。round 2 の改訂（回復の予約と回復専用枠、`final_infeasible`、監督の group の先行確保）で round 1 版の 2,150〜2,750 行から増やした。初版は 1,650〜2,000 行。各 PR は 600 行を越えうるので、PR 本文に分割しない理由（F1 は型・decoder・reducer・codec を同時に成立させないと保存面の検査が空回りする、F2a は予約と回収を同じ入口で同時に入れないと予約だけの中間状態ができる）を書く（F2p は helper と承認 verifier の移行を同時に入れないと、helper に利用者が無いか、承認 verifier が fork のまま残る）。行数は実 diff を `scripts/pr_size.py` で再計測する。**有効化は F2c だけに置くので、F1〜F2b の中間状態はどれも policy を持つ session を作れない**（§3.5）。F2p は F1 と並行に進められる。

## 8. 判断が必要な事項と対象外

### 判断事項（orchestrator / owner）

1. **非保護 3 phase を合算で強制するか。** 推奨は合算（§2.3）。
2. **halt 中の時間を数えるか。** 推奨は数えない（§2.2）。外部の壁時計上限との整合は `external_deadline_at` で取る。
3. **D2c への要求。** `deadline_at` を envelope の閉じた集合へ今の段階で含めること、全 adapter 呼出しを 1 つの application の seam から行うこと（F2b がそこを締切付きの子で包むため）。
4. **strict 隔離の provider。** 推奨は予算付き session で拒否（§4）。
5. **final latch を一方向にするか。** 推奨は一方向（§3.1）。
6. **`gate-and-merge` を対象外にしてよいか。** 推奨は対象外。
7. **分割（改訂）**。F1 → F2a → F2b → F2c の 4 分割。旧決定の 3 分割から F2b を「D/E 入口」と「有効化と停止」に分ける。（round 4 で F2p を切り出して 5 分割にする提案。18）
8. **予算の延長**。F では作らない。exhaustion 後の Reactivate は拒否する。
9. **予算 policy を必須にするか**。F は policy の無い session を `advisory-only` で通す。J の verified-complex profile で必須にするかは J で決める（旧決定 9 で必須と決定済み）。
10. **（新規）I/G への要求**。Mission arm の harness が `init --budget-policy` と `external_deadline_at = 起動時刻 + T − 後処理の余白` を渡すこと、余白の値、Mission arm が halt 後に reactivate するか。I の事前登録の凍結項目に含めるかは I で決める。（round 6 で、reactivate の禁止を policy の `reactivate: forbidden` として kernel で強制できるようにした。20）
11. **（新規）承認 verifier を予約の対象にすること**。推奨は対象にする（§3.2 #5・#6）。予約のために verify-approval の lock 区間の前へ短い transaction を 1 つ足す。
12. **（新規）保証を狭めること**。§4.4 の「mission の spawn 入口から起動した final 以外の作業（repair 系と非保護の分類）は final 予約を使えない。host の inline 作業と host が起動する subagent は強制の外で、侵食量を記録する」を、#881 の受入条件の読み方として採るか。Issue 本文は「reserveはguidanceだけでなくrunner/provider/retryの全dispatch入口で強制する」「初回phaseが修復/最終検証の予約枠を消費し切らない」と書いている。前者は mission の spawn 入口として満たせるが、後者の「初回phase」の主な消費者である host の planner・executor の subagent（§3.2 #16）は mission から止められない。採る場合は、Issue 本文の完了条件を「mission が起動する dispatch は予約枠を消費し切らない。host 側の消費は `reserve_erosion_sec` で可視化し、final が実行できなければ pass にならない」へ更新する必要がある（Issue の更新は orchestrator が行う）。

13. **（round 2）回復の予約**。推奨は、`recover` を他の spawn と同じく予約し、分類は回復する dispatch の分類、締切はその分類の締切まで。分類の時間が尽きた後と exhaustion 後は、final 枠から init 時に切り出した固定の回復専用枠 `system_recovery_sec`（初期値 60 秒、補充しない）だけを使う（§3.6）。
14. **（round 2）B の監督の process group**。推奨は、監督が最初に `setsid` して group を作り、verifier を新しい session なしで起動して group を継承させる（§4.2）。親は fork の戻り値で group を知るので、受け渡しの窓が無い。（round 4 で、監督の起動を fork から exec へ置き換えた。group の継承と受け渡しの窓が無いことは変わらない。17）
15. **（round 2）final が実行できなくなった時点の扱い**。推奨は、`final_infeasible` を admission と同じ式で導出し、final の完了記録が無く final の予約も開いていなければ exhaustion に含める（§5）。contract の無い予算付き session も対象。
16. **（round 3）process group の確保と掃除**。推奨は、親が起動の時点で group を作り（fork 系は子の `setsid` の後の readiness と go の handshake、provider は `start_new_session=True`）、正常終了を含む全ての終了経路で leader の reap の前に `killpg(G, SIGKILL)` を送って G が空になるのを確かめてから精算する（§4.2）。承認 verifier の既存の実行（`_run_approval_verifier`）も同じ規則へ変える。（round 4 で、fork 系の readiness と go の handshake を 17 の exec 起動へ置き換えた。掃除の規則は変えない）
17. **（round 4）予算付きの子の起動方式**。推奨は、B の監督・adapter の呼出し子・承認 verifier の子・provider を、Python の process の fork でなく `Popen([sys.executable, "-I", <trampoline>], start_new_session=True, close_fds=True)` の exec で起動する（§4.2）。fork では import 済みの library の after-fork hook が子の `setsid` の前に親の group で走り、G の外に孫を起動しうる。exec では `setsid` が exec の前に行われ、after-fork hook は子で走らない。readiness の handshake と go は無くし、job は spawn の前に private な file で渡す（round 5。§4.2「job の受け渡し」）。adapter の pin 付きの読込みは子の中で行う。
18. **（round 4）分割の改訂**。推奨は、予算に依存しない exec の起動と承認 verifier の移行を F2p として F2a から切り出し、F1 → F2p → F2a → F2b → F2c の 5 分割にする（§7。F2p は F1 と並行可）。切り出さない場合、F2a の ×1.6 の見積は 1,400 を越える。
19. **（round 4）exec に移せない経路と環境の差**。推奨は 2 点。(a) in-process で登録した callable の承認 verifier（`register_approval_verifier`）は exec の子へ渡せないので、予算付き session の force pass では `budget-deadline-unenforceable` で拒否し、policy の無い session だけ fork の経路を残す（after-fork hook の窓はこの経路に残る）。代案は callable の登録を廃止して registry 由来へ一本化すること（host の組込み API の互換を壊す。`test_score_provenance.py:621` が使っている）。(b) `-I` により、`PYTHONPATH` や user site にだけある adapter・承認 verifier の distribution は子から見えず、子の再発見が失敗して拒否になる（fail-closed。親では見えて子では見えない）。代案は親の `sys.path` を job で渡すこと（user site の `.pth` は処理されないので editable install の扱いが別に要る）。既存の利用者がこの配置に依存しているかは **UNKNOWN**。
20. **（round 6）reactivate の禁止を policy で強制すること（I の要求、PR #914）**。推奨は、`mission-budget-policy/1` に `reactivate`（`allowed` / `forbidden`、初期値 `allowed`）を足し、`forbidden` の session では kernel が Reactivate を `--approved-by-user` を含む flag に関係なく `budget-reactivate-forbidden` で拒否し、state を変えない（§3.3）。policy は session の間変わらない。F2c で `init --budget-policy` と同時に入れる。I はこれに依存し、F2c の merge 前は I 側の fail-closed の代替を使う。ResumeStale は対象外。

### 対象外

- 既存 review tier の降格（旧 #611 は再開しない）、既存 gate・mark-passes の緩和。
- token・費用の推定値を強制に使うこと。値の producer の新設。
- host の inline 作業の強制停止、親 crash 後の orphan の回収、strict backend の protocol 変更。
- policy の無い session の強制、policy の後付け。
- 予算延長の command、phase 配分の自動調整、初期値の最適化（I の計測の後に別途）。
- legacy session（policy なし）の command provider の無期限の待ちの修正。
- event/export の新設（E4 の `mission-repair-event/1` と I の集計に任せ、F は status と停止記録を供給する）。
- closed v5 の public bridge（D・E と同じく対象外）。

この設計は設計レビューの対象であり、実装・CI の受入ではない。

## 固定 head の出典

[S*][T*] のリンクは全て `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`。各ラベルの file:line はこの head で読んだ範囲。テストの引用はコードの保証を指し、本ステップで実行した結果を指さない。[D05] は main の commit `8b3bf236`、[D06] は I の branch の commit に固定する。

[S01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L8403-L8420 "mission-state.py:8403-8420 — _validated_budget_minutes（float を返す）"
[S02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L8423-L8463 "mission-state.py:8423-8463 — BUDGET_PRESSURE_WARN_PCT・BUDGET_SPAWN_ACTIONS・_budget_pressure（started_at からの壁時計）"
[S03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L8666-L8718 "mission-state.py:8666-8718 — cmd_next の read-only な差し替え"
[S04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L16112-L16114 "mission-state.py:16112-16114 — init --max-iter / --budget-minutes"
[S05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/lifecycle.py#L356-L366 "mission_application/lifecycle.py:356-366 — new mission の予算検証（float）; legacy_initialization.py:187 で保存"
[S06]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/guidance.py#L256-L260 "mission_kernel/guidance.py:256-260 — application.clock-budget-override は outside-parity"
[S07]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/commands.py#L386-L455 "mission_kernel/commands.py:386-455 — GENERIC_SET_FROZEN_FIELDS（393 passes）/ GENERIC_SET_DEDICATED_FIELDS（422 phase）。budget_minutes を含まない"
[S08]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L282-L334 "mission_kernel/transitions.py:282-296 timing/activity field、334 Reactivate が書き換えてよい field"
[S09]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L869-L947 "mission_kernel/transitions.py:871 _reactivate（approved_by_user 必須）と 911 _resume_stale; 526-556 再開の監査"
[S10]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/activity_segments.py#L11-L57 "activity_segments.py:11-57 — activity の種類・理由・RECENT_SEGMENT_LIMIT"
[S11]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L2073-L2080 "mission-state.py:2073-2080 — _mission_started_at の fallback 順"
[S12]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/fresh_review.py#L19-L76 "mission_kernel/fresh_review.py:21-22 BUDGET_LIMITS、71-76 validate_budgets、111-115 request の予算 field"
[S13]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L16471-L16484 "mission-state.py:16471-16484 — fresh-review は prepare/status のみ"
[S14]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/fresh_review_runtime.py#L44-L60 "fresh_review_runtime.py:44-60 — adapter protocol（51 observe_parent、53 launch、56 collect、58 cancel、60 recover。同期呼出しで締切なし）"
[S15]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/verifier_command.py#L25-L39 "verifier_command.py:25-39 — timeout_sec は 1〜3600 の int"
[S16]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/verification_runner.py#L279-L420 "mission_application/verification_runner.py:279-420 — execute_candidate（329-332 start_new_session、356 締切、357-372 締切で killpg、380-388 0.2 秒の wait と再送、405-419 status/block_reason）"
[S17]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/verification_execution.py#L34-L161 "mission_application/verification_execution.py:34-82 CLI（起動前の intent なし、58 で run_contract_verifier）、85-161 run_contract_verifier"
[S18]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/command_provider.py#L185-L340 "mission_application/command_provider.py:185-192 _provider_timeout、310-340 timeout と Section 1: Reservation（324 requested_phase=request.phase）"
[S19]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L10224-L10247 "mission-state.py:10224-10247 — _run_strict_provider_backend（in-process、締切なし、10233 dispatch_prepared_packet）; command_provider.py:446-456 から呼ぶ"
[S20]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/command_provider.py#L518-L574 "mission_application/command_provider.py:520 Popen（新 session なし）、527-562 起動後の記録 commit、566-570 communicate(timeout) と kill() 後の timeout の無い communicate()"
[S21]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/provider_preflight.py#L192-L236 "provider_preflight.py:192-236 — strict_spawn / dispatch_prepared_packet"
[S22]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L10333-L10433 "mission-state.py:10335-10338 _approval_verifier_child の最初の setsid、10390-10403 _stop_approval_verifier_child（SIGTERM/SIGKILL と各 0.2 秒）、10406-10433 _run_approval_verifier（fork、5 秒 join、finally の再回収）; 10089-10090 定数"
[S23]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/next_action.py#L179-L185 "mission_application/next_action.py:179-185 — stagnation の助言; mission-state.py:13808-13821 更新、guidance.py:683-687"
[S24]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L11057-L11061 "mission-state.py:11057-11061 — max_iter は早期停止の報告に使う"
[S25]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/planning_provider_metrics.py#L8-L17 "planning_provider_metrics.py:8-17 — 件数と率だけの KPI"
[S26]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/model.py#L97-L106 "mission_kernel/model.py:97-106 — HaltCategory"
[S27]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L803-L830 "mission_kernel/transitions.py:803-830 _mark_halt"
[S28]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/json_codec.py#L13-L13 "mission_kernel/json_codec.py:13 — STATE_LIMIT 4 MiB"
[S29]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/command_owners.py#L10-L95 "mission_application/command_owners.py:10-95 — A1.lifecycle / A2.review / A3.evidence（52 verification run）/ A4（66 invoke-command）/ R1.query"
[S30]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_persistence/fenced_commit.py#L1886-L1895 "mission_persistence/fenced_commit.py:1886-1895 — repository lock（5 秒で lock-timeout）"
[S31]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L16803-L16870 "mission-state.py:16803-16827 provider invoke の引数（16806 --phase の choices、16817 --timeout）、16854-16861 verify-approval、reconcile-invocation"
[S32]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/integration_gate.py#L60-L77 "integration_gate.py:60-77 — timeout の無い subprocess.run（67, 79。171 は probe）; mission-state.py:7337 cmd_gate_and_merge"
[S33]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L5686-L5770 "mission-state.py:5686-5770 — cmd_invoke_command_provider の配線（5748 strict_dispatch・5751 Popen）"
[S34]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/AGENTS.md#L119-L128 "AGENTS.md:119-128 — 600 / 1,400 の閾値"
[S35]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/review.py#L503-L585 "mission_application/review.py:503 pass の transaction、516-521 contract がある場合だけの preflight、523 force の承認 verifier（spawn）、585 MarkPass"
[S36]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L987-L1075 "mission_kernel/transitions.py:987 _acceptance_completion_ready、994 contract が無ければ戻る、1052 acceptance-fresh-review-pending、1068 _mark_pass、1072 guard 呼出し、1075 force の分岐"
[S37]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/provider_eligibility.py#L700-L750 "provider_eligibility.py:15-21 MISSION_PHASE_TO_PROVIDER_PHASE、700-750 validate_provider_application（747-749 state の phase と requested_phase の一致、mission-phase-mismatch）"
[S39]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/command_provider.py#L530-L552 "mission_application/command_provider.py:536, 551 — terminate() の後の wait(timeout=5)"
[S40]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L5492-L5552 "mission-state.py:5492 cmd_verify_provider_approval、5525 lock を持つ transaction、5550 _run_approval_verifier（spawn）"
[S41]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L10436-L10446 "mission-state.py:10436-10446 verify_force_approval（10445 で spawn）; 14123 force pass からの呼出し、14241 MarkPassServices への注入"
[S42]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/verification_runner.py#L47-L49 "mission_application/verification_runner.py:47-49 — timeout の無い git ls-files; 356 Popen の後に monotonic の締切"
[S43]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/worktree_archive.py#L115-L115 "git/ps の短い呼出し: worktree_archive.py:115, 655; mission_application/evidence.py:191; mission-state.py:1150, 2033, 2038, 6029, 6049, 6058, 6102, 10515, 10538, 14495; mission_python_inventory.py:153"
[S46]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L1120-L1135 "mission_kernel/transitions.py:1123, 1132 — passes=True を書く唯一の kernel 経路"
[S47]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/benchmarks/mission-vs-goal/run_native_goal_probe.py#L33-L36 "run_native_goal_probe.py:33-36 — RpcProcess は起動時刻から単一の monotonic deadline を持つ"
[S48]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L1471-L1530 "mission-state.py:1471 _resolve_goal_dispatch（inline / host-native の選択）、1521 _goal_dispatch_guidance（host への案内文）"
[S49]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/planning.py#L1078-L1139 "mission_application/planning.py:1078 record_dispatch_intent、1120 reconcile_dispatch_unknown（1139 redispatch=False）— host が起動した specialist の記録"
[S50]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/fresh_review_runtime.py#L158-L271 "fresh_review_runtime.py:158-195 _load_pinned_factory（167-168 sys.modules にあれば拒否、digest の照合と compile）、241-243 resolve_adapter（import しない）、246-271 load_adapter（247-251 callback 子で呼ぶ契約、254-256 pin の比較）。main（9c948878）でも同じ行で、load_adapter の呼出し箇所は無い"
[S51]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_persistence/local_uow.py#L339-L361 "mission_persistence/local_uow.py:339-361 _write_private_file（O_CREAT|O_EXCL|O_NOFOLLOW、0600、fsync、作成後の regular・link 数 1・mode・内容の検査）"
[S52]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_persistence/fenced_commit.py#L1624-L1640 "mission_persistence/fenced_commit.py:1624-1640 _ensure_directory（0700 の作成と directory の検査。owner は検査しない）"
[T01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue238_budget_pressure.py#L41-L140 "test_issue238_budget_pressure.py:41-140 — 9 件"
[T02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue878_candidate_snapshot.py#L74-L74 "test_issue878_candidate_snapshot.py:74 — runner の timeout"
[T03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue878_verification_runner.py#L327-L342 "test_issue878_verification_runner.py:327-342 — 同じ operation の再実行"
[T04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue895_fresh_review.py#L90-L194 "test_issue895_fresh_review.py:90, 194 — request の予算の閉じた検査"
[T05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue742_stop_guard_timeout.py#L368-L393 "test_issue742_stop_guard_timeout.py:368-393 — stop guard の予算"
[D01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/689-fresh-review-receipt.md#L66-L71 "docs/design/689-fresh-review-receipt.md:66-71 — request の予算上限、金額予算と repair 予約は後続へ; 181-186 coverage を contract へ書かない理由"
[D02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/689-fresh-review-receipt.md#L211-L238 "docs/design/689-fresh-review-receipt.md:211-238 — terminal variant（budget_used・cancel_result）と dispatch saga（D2c 以降は未 merge）"
[D03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L48-L59 "docs/design/880-repair-lineage.md:48-59 — E の旧版の容量予約と公開の回復; 25 保存方式（改訂版は D05）"
[D04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L155-L166 "docs/design/880-repair-lineage.md:155-166 — repair の公開 command（未 merge）; 112 再検証の intent→実行→公開"
[D05]: https://github.com/tackeyy/mission/blob/8b3bf2361334bf10c8679be6bd06d200872930bc/docs/design/880-repair-lineage.md#L54-L90 "docs/design/880-repair-lineage.md（main 8b3bf236）:54-90 — 容量予約（書込み種別と Δ、state から導出する予約、受付時の全段確保、検査式と S_sys_remaining、state_capacity_verdict と全 writer、予約を持たない B receipt、E と F の境界、停止系、移行と legacy-full）"
[D06]: https://github.com/tackeyy/mission/blob/aa6047923e6e7aefd263014e72a8217521b98831/docs/design/884-evaluation-aggregation.md#L22-L61 "docs/design/884-evaluation-aggregation.md（docs/884-eval-prereg）:22 RpcProcess の単一 deadline を予算の共通単位に、35 全 arm 同じ wall-clock 上限、61 予算内の定義; 178 T は smoke 後に凍結"

### 決定（orchestrator, 2026-10-04）

§8 の未決事項は、設計レビューの前に次のとおり決めた（いずれも推奨案を採用）。設計レビュー round 1（changes-requested、High 5 件・Medium 1 件）を受けて、下の「round 1 後の改訂」で一部を置き換えた。

1. 保護しない 3 phase（計画・実装・検証）は 1 つの共有 pool として強制する。
2. halt 中の時間は経過時間に数えない。
3. D2c（#912）の dispatch envelope に `deadline_at` を今の段階で入れる。D の閉じた schema を後から版上げしないためで、D2c の実装範囲に加える。
4. budget 付き session では strict-isolation provider を拒否する（包む方式は後続で検討する）。
5. final phase の latch は初版では一方向とする。
6. `gate-and-merge` は対象外とする。
7. （下で置き換え）分割は F1 → F2a → F2b の 3 PR とする。F の着手は E3 の merge 後とする。
8. budget の延長は F に含めない。必要になれば別 Issue とする。
9. J の verified-complex profile は budget policy を必須とする（外部評価の公平条件として wall-clock 上限 T を全 arm で共通にするため。I の事前登録と整合）。

#### round 1 後の改訂（designer の提案。下の決定で確定）

- 決定 2 に追加: halt を数えない稼働時計に加え、`external_deadline_at` で外部の壁時計上限に揃える（§2.2）。I の T は harness が強制し、F は置き換えない。
- 決定 3 に追加: D2c は全 adapter 呼出しを 1 つの seam から行う（§4.2）。
- 決定 7 を置き換え: F1 → F2a → F2b → F2c の 4 PR（§7）。F1 は E0 の merge 後、F2b は D2c・E2・E3 が main に揃った後。有効化（`init --budget-policy`）は F2c だけに置く。
- 新規 10〜12（§8）: I/G への要求、承認 verifier の予約、保証の狭め（host の inline 作業と host が起動する subagent は強制の外）。12 は #881 の完了条件「初回phaseが修復/最終検証の予約枠を消費し切らない」の読み方を変えるので owner の確認が要る。
- 予算 policy の無い session は `advisory-only` のまま通す（旧決定 9 の J 必須は維持。F 自身は policy を必須にしない）。

### 決定（orchestrator / owner, 2026-10-04。設計レビュー round 1 の後）

- 10（I/G との結合、orchestrator 決定）: 外部評価の harness が Mission arm に budget policy と `external_deadline_at` を渡す。後処理の余白の値は smoke の実測後に I の事前登録で固定する。halt 後の reactivate は評価中は行わない。
- 11（承認 verifier の予約、orchestrator 決定）: 予約の対象にする。
- 12（保証の範囲、owner 決定）: 保証を「予算 policy を持つ session で、mission の spawn 入口から起動した final 以外の作業（repair 系と非保護の分類の dispatch）は final の予約を使えない」に狭める（round 3 で、final 分類の dispatch を含んでいた文言を狭めた。趣旨は変えない）。host が起動する subagent と host の inline 作業の時間は強制の外とし、`reserve_erosion_sec` として記録し、exhaustion 後は force を含めて pass に到達させない。Issue #881 の完了条件をこの範囲に合わせて更新する。
- 決定 7 の置き換え: F は F1 → F2a → F2b → F2c の 4 PR とする（各 PR は較正済み見積りで 1,400 行未満）。

### 決定（owner 指示により推奨案を採用, 2026-10-04。設計レビュー round 2 の後）

- 13（回復の予約）: 推奨案を採る。`recover` は回復する dispatch の分類で予約し、締切はその分類の締切まで。分類の時間が尽きた後と exhaustion 後は、final 枠から切り出した固定の回復専用枠だけを使う（§3.6）。
- 14（B の監督の process group）: 推奨案を採る。監督が最初に `setsid` し、verifier はその group を継承する（§4.2）。pgid を pipe で渡す旧方式は撤回。
- 15（final が実行できなくなった時点）: 推奨案を採る。`final_infeasible` を exhaustion の原因 3 として加え、contract の有無に関係なく pass（force を含む）を拒否する（§5）。

### 決定（owner 指示により推奨案を採用, 2026-10-04。設計レビュー round 3 の後）

- 16（process group の確保と掃除）: 推奨案を採る。group は親が起動の時点で確保し（readiness の handshake、provider は `start_new_session=True`）、全ての終了経路で reap の前に group を掃除してから精算する。ESRCH のとき監督の pid へ送る旧 fallback は撤回。承認 verifier の起動と掃除も同じ規則へ変える（F2p）。G を自ら抜ける子孫は残る限界とする（§4.2）。
- 12 の文言: 「final の予約を使えない」作業を final 以外の作業（repair 系と非保護の分類の dispatch）に狭めた（§4.4）。

### 決定（owner 指示により推奨案を採用, 2026-10-04。設計レビュー round 4 の後）

- 17（予算付きの子の起動方式）: 推奨案を採る。予算付きの子は全て exec で起動し（`start_new_session=True`・`close_fds=True`・`preexec_fn` 無し・`-I`・固定の trampoline）、Python の process の fork を使わない。round 3 の readiness と go の handshake は撤回し、go は job の受け渡しに置き換える。全ての終了経路での掃除の規則は変えない（§4.2）。

#### round 4 後の改訂（designer の提案と orchestrator の決定）

- 18（5 分割。F2p の新設）と 19（callable の承認 verifier と `-I` の環境差）は推奨案を書いた。いずれも 17 の帰結で、F2p の着手前に決める必要がある。

- 18・19a・19b（orchestrator 決定・推奨採用）: 分割は F1 → F2p → F2a → F2b → F2c の 5 PR とする（F2p は main だけに依存し F1 と並行実装できる。merge は直列）。process 内で登録した callable の承認 verifier は、予算付き session では拒否し、policy の無い session だけ fork 経路を残す。`-I` により `PYTHONPATH` や user site からしか見えない adapter・verifier の distribution は子で見えず、拒否として fail-closed とする（親の `sys.path` を渡す案は採らない）。

### 決定（owner 指示により推奨案を採用, 2026-10-04。設計レビュー round 6 の後）

- job file の残留の削除: 名前に作成した親の pid と process の開始時刻を入れ、予約が開いておらず所有者が生存していない file だけを削除する（予約の有無に関係なく適用。開始時刻が読めなければ削除しない）。書込みの失敗は部分 file を消し、`budget-job-write-failed` で拒否・未起動として精算する（§4.2）。
- 20（I の要求。I の設計 PR #914）: `mission-budget-policy/1` に `reactivate`（`allowed` / `forbidden`）を足し、`forbidden` の session の Reactivate を flag に関係なく `budget-reactivate-forbidden` で state を変えずに拒否する。F2c に置く（§3.3・§7）。I はこれに依存し、F2c の merge 前は fail-closed の代替を使う。
