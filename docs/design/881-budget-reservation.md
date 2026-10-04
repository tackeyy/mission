# 修復と最終検証の予算を予約して実行を制御する設計

決定案: 既存の `budget_minutes` を総枠とし、型付きの予算 policy と ledger を新しい projection に置く。全体の消費は「稼働中の時計」1 本で数え、並行 child の時間を足し合わせない。修復と最終検証の枠だけを保護枠とし、mission が起動する全ての dispatch 入口で、起動の前に durable な予約を取る。予約できなければ起動せず理由付きで拒否し、予算切れは typed な partial-done 停止で終わる。成功を作らない。

対象: [Issue 881: 修復と最終検証の予算を予約して実行を制御する](https://github.com/tackeyy/mission/issues/881)（親 [Issue 876: 品質改善の全体追跡](https://github.com/tackeyy/mission/issues/876)）。本書は設計のみ。実装、テスト追加、Issue 起票・本文変更、Git 操作による公開は行わない。
照合 head: `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`（依頼で固定）。本書の「現状」はこの head のソースを指す。執筆時に `git ls-remote` で見た remote の main は `9c948878d878bbadbfb98a1d62a43d67fcc700c7` へ進んでおり、[D2b #909](https://github.com/tackeyy/mission/issues/909)（PR #911）の merge を含む。その差分は本書で照合していない（**UNKNOWN**）。実装の着手時に最新 main で再照合する。執筆時点で open PR は 0 件だった（`gh pr list --state open --limit 100`）。

引用 [S*] / [T*] / [D*] は末尾の固定 head の `file:line` とリンクへ結ぶ。テスト引用は保持するソース上の契約であり、本ステップのテスト実行結果ではない。

## 1. 採用する境界と既存状態

### 1.1 いまある予算・時間の扱い

| 照合した現状 | 種別 | F の扱い |
|---|---|---|
| `init --budget-minutes` は正の有限数だけを受理し、`budget_minutes` として保存する。[S01][S04][S05] | 宣言のみ | 総枠の唯一の入力として使う。新しい総枠の入力経路を作らない |
| `_budget_pressure` は `started_at` から現在までの壁時計を `budget_minutes` で割り、80% で warn、100% で exceeded とする。halt 中の時間も数える。[S02] | 観測のみ | legacy の表示は変えない。ledger がある session だけ ledger の時計で表示する（§2.5） |
| `next` は exceeded のときだけ `run-planner/run-executor/run-reviewers` を `consider-halt` へ差し替える。read-only で state を変えない。[S03] | 助言のみ | 差し替えの条件を ledger の保護枠に合わせる。助言であって強制ではないことは変えない |
| guidance parity は `application.clock-budget-override` を `outside-parity` に置き、入力を `$.budget_minutes`・`$.started_at`・`iso_now()` と列挙する。[S06] | 境界の宣言 | 入力に `$.budget_ledger` を足し、outside-parity のまま保つ |
| `budget_minutes` は generic set の frozen 集合にも dedicated 集合にも含まれない。[S07] generic `set` で書き換えられるかは実行で確かめていない（**UNKNOWN**） | 保護なし | ledger がある session では generic set を拒否する（§2.4） |
| 稼働の記録は `activity_segments`（active・各種 wait・idle）と rollup。種類と理由は caller が申告する。[S10] reactivate が書き換えてよいのは timing/activity 系の field と `reactivation_history` だけで、`started_at` は動かない。[S08][S09][S11] | 観測のみ | caller 申告の activity を予算の差し引きに使わない（§2.2） |
| 独立レビューの request は `wall_time_sec`（上限 300）・`max_tool_calls`・`max_replays`・`max_output_bytes`・`max_packet_bytes` を閉じた int として持つ。[S12] 公開 CLI は `prepare/status` だけで、起動は未実装。[S13] D 設計は金額予算と repair 予約を「後続 budget 作業」へ残している。[D01] | request ごとの上限 | request の値は変えない。dispatch の締切を F が別に与える（§3） |
| runtime adapter protocol の `launch/collect` は締切を引数に持たない。`cancel` はある。[S14] | 強制なし | dispatch envelope に締切を載せる（§4、判断事項 3） |
| 凍結 verifier の `timeout_sec` は 1〜3600 の int で、definition digest に含まれる。[S15] runner は `start_new_session=True` で起動し、締切で process group へ SIGKILL を送る。[S16] | 強制あり（policy の timeout） | 凍結値を変えずに、より短い実効締切を渡す（§4） |
| `verification run` は spawn の前に durable な intent を保存しない。実行後に receipt を 1 回公開する。[S17] | 記録なし | 起動前の予約 commit を足す（§3） |
| command provider は `--timeout`（無ければ provider 値、さらに無ければ 120 秒）を使う。[S18][S31] `Popen` に新しい session を指定せず、timeout では直下の child だけを `kill()` し、その後の `communicate()` に timeout が無い。[S20] | 強制あり（不完全） | process group で回収し、回収待ちにも上限を置く（§4） |
| strict 隔離の backend は in-process の呼出しで、締切も取消しも渡らない。[S19][S21] | 強制なし | 予算付き session では起動前に拒否する（§4、判断事項 4） |
| 承認 verifier は fork した child を 5 秒で打ち切り、process group へ送信して回収する。[S22] | 強制あり（固定 5.2 秒以内） | admission の対象外とし、固定上限を margin に含める（§3） |
| 停滞は score に基づく `stagnation_count` で数え、3 以上で `consider-halt` を助言する。[S23] `max_iter` は早期停止の報告に使われる。本書で照合した範囲では、`max_iter` で dispatch を拒否する箇所は見つからない。[S24] | 助言のみ | 証拠の増えない dispatch を admission で止める（§3.4） |
| token・費用の producer は `skills/mission/lib` と `skills/mission/bin` に無い（`input_tokens|output_tokens|token_usage|cost_usd|total_cost` の検索で 0 件）。planning provider の KPI も件数と率だけを持つ。[S25] | 未測定 | 強制値にしない。`unmeasured` として表示し、0 と書かない（§2.1） |

**結論**: 現在の予算は「宣言」と「助言」だけで、どの dispatch 入口も残り時間を見ずに起動する。実行中の打ち切りは policy の timeout だけで、全体の期限とは結びついていない。

### 1.2 層の分担

決定: kernel は閉じた型・decoder・純粋な admission 判定・reducer を持ち、時計を読まない（時刻は command の field として受け取る）。application は時計の観測、入口ごとの予約→起動→回収→精算の調整、締切の受け渡しを担う。persistence は既存の fenced commit と repository の lock をそのまま使う。CLI は typed request の構築と 1 use case の呼出し、描画、終了コード変換だけを担う。予約の直列化は repository lock（[S30] の 5 秒 lock）と fence で行い、新しい lock 機構を作らない。

## 2. Typed policy と ledger

### 2.1 単位

| 単位 | 強制 | 定義 |
|---|---|---|
| 稼働時計（秒） | する | `loop_active` の区間だけを数える壁時計（§2.2）。全体の消費はこれ 1 本 |
| dispatch 時間（秒） | する | 各 dispatch の予約から精算までの時間。保護枠の消費として使う |
| 同時 dispatch 数・phase ごとの dispatch 回数 | する | 子の無制限な増加を止める |
| tool calls・replays・output bytes | D の request ごと | adapter が強制する。F は精算時に観測値を telemetry として残すだけ |
| token・費用 | しない | producer が無いので `{"status": "unmeasured", "reason": "no-producer"}`。測れた場合も強制値にしない |

### 2.2 稼働時計

決定: 稼働時計は `started_at`（無ければ `_mission_started_at` と同じ順の fallback）から始まり、`MarkHalt` と `MarkPass` で閉じ、`Reactivate` と `ResumeStale` で開く区間の和とする。[S08][S09][S11] 既存の budget pressure は halt 中も数えるが、reactivate は利用者の明示承認を要求する（[S09] の `approved_by_user`）。承認済みの再開後に、停止中の時間で即座に予算切れになる状態を避けるため、停止区間は数えない（判断事項 2）。

- caller が申告する activity（idle・wait）は差し引きに使わない。差し引きを申告できると、申告だけで予算を延ばせる。
- stale halt（process が消えて halt が記録されない場合）は、`ResumeStale` までの時間をすべて消費として数える。区間を知る手段が無いため、少なく数える側へ倒さない。
- 時計の後退（直前に記録した時刻より前の `at`）は `budget-clock-regressed` で拒否し、state を変えない。
- 並行 child の時間は稼働時計へ足さない。全体の消費は時計そのものである。

### 2.3 Policy

決定: `mission-budget-policy/1` を閉じた構造で一度だけ import し、以後変更しない。保存先は acceptance contract の外とする。contract に入れると `canonical_contract_digest` が変わり、B の既存 receipt が全て stale になる（D が同じ理由で coverage を contract へ書き戻さなかった判断と同じ。[D01]）。

| field | 型・初期値 | 意味 |
|---|---|---|
| `schema` | `mission-budget-policy/1` | — |
| `total_sec` | int。`budget_minutes × 60` と一致しなければ拒否 | 総枠。policy 側で延長できない |
| `reserve_basis_points` | `{planning: 1000, implementation: 4000, verification: 2000, repair: 2000, final: 1000}`。合計 10000 | phase の配分 |
| `protected_phases` | `["repair", "final"]` 固定 | 強制する保護枠 |
| `max_concurrent_dispatches` | int、初期値 2 | 同時に開いている予約の上限 |
| `max_dispatches_per_phase` | int、初期値 32 | phase ごとの予約回数の上限 |
| `kill_grace_sec` / `commit_margin_sec` | int、初期値 5 / 10 | 回収と終端記録のために締切の手前へ残す時間 |
| `min_dispatch_sec` | int、初期値 1 | これ未満の実効締切では起動しない |
| `no_progress_limit` | int、初期値 2 | §3.4 |
| `provenance` | `experimental-initial` 固定 | 初期値が実証済みではないことを保存面に残す |

**計画 10 / 実装 40 / 検証 20 / 修復 20 / 最終 10 %、および上表の他の初期値は実験用の初期値であり、効果が実証された既定値ではない。** status にも `provenance` をそのまま出す。値の妥当性は I（[Issue 884](https://github.com/tackeyy/mission/issues/884)）の計測で判断する。

決定（強制する範囲）: 保護枠として強制するのは repair と final だけとする。planning・implementation・verification の 3 つは「非保護の共有枠」として合算で強制し、個別の配分は status の目標値として表示する。理由: 実装時間の大半は host の inline 作業で、mission はその時間を dispatch として観測できない。観測できない作業に phase ごとの上限を課しても強制にならず、表示と実態がずれる（判断事項 1）。

### 2.4 Ledger と保存場所

決定: `MissionState.budget: BudgetProjection` を追加し、保存 schema を `mission-budget-ledger/1` とする。v4 document の予約キー `budget_ledger` と closed v5 の `extensions.budget_ledger` を同じ閉じた decoder で復元する。D の `fresh_review`、E の `repair_lineage` と同じ方式で、v5 の top-level field は増やさない。[S12][D03]

| 型 | 内容 |
|---|---|
| `BudgetPolicy` | §2.3 と、その canonical digest |
| `ActiveClock` | 閉じた区間の合計秒、開いている区間の開始時刻（または null）、最後に観測した時刻 |
| `DispatchReservation` | `reservation_id`（kernel が operation identity から domain-separated に導出）、`entry`（§3.1 の閉じた列挙）、`phase`、`target`（criterion ID・request ID・attempt ID・provider ID のいずれか）、`operation_id`/`fencing_epoch`、`reserved_at`、`deadline_at`、`reserved_sec` |
| `PhaseCharge` | phase ごとの精算済み dispatch 秒の合計、予約回数、開いている予約数 |
| `Settlement` | `reservation_id`、`outcome`（`settled`/`charged-full-unknown`）、`charged_sec`、`observed`（終了・締切到達・回収確認の観測事実）、`telemetry` |
| `ProgressSignature` | `(entry, target)` ごとの最後の `candidate_digest`・結果 digest・連続回数 |
| `FinalLatch` | final へ移った時刻と理由（明示 / 導出） |
| `BudgetStop` | §5 の停止記録 |

- state に置くのは上限付きの要約だけとする。開いている予約は `max_concurrent_dispatches` 件以下、精算済みは phase ごとの合計と直近 32 件（activity と同じ上限の考え方。[S10] の `RECENT_SEGMENT_LIMIT`）だけを持つ。dispatch の詳細な事実は、各入口が既に公開している receipt（B の verification receipt、D の terminal の `budget_used`、E の reverification receipt）に残る。[S16][D02]
- 1 行あたりの保存 bytes の上限を型で固定し、E の容量予約（「その attempt 自身の終端前の書込み（種類ごとに固定の上限サイズ）＋終端要約」）へ F の予約行と精算行を 1 種類として加える。[D03] state 全体の上限 4 MiB は緩めない。[S28]
- キー欠落だけが「予算 policy なし」。null・未知 schema・未知 field・重複 ID・開いている予約の上限超過・時刻の逆転は理由コード付きで拒否し、空の ledger と読み替えない。
- `budget_ledger` とその子孫は generic set・init/reinit・compatibility delta・review/score import・specialist evidence・downgrade から保護する（D・E と同じ集合を拡張する。[S07]）。ledger がある session では `budget_minutes` の generic set も拒否する。ledger の無い legacy session の挙動は変えない。

### 2.5 Status の表示

決定: `budget status`（R1.query、read-only）を追加する。`next` には ledger がある session だけ `budget` ブロックを足し、`budget_pressure` を ledger の稼働時計から計算して `basis: "active-clock"` を付ける。ledger の無い session の出力は変えない（[T01] を保持）。

表示する項目: policy の値と `provenance`、総枠・消費・残り、phase ごとの目標・精算済み・開いている予約、保護枠の未使用分、非保護枠・修復・最終それぞれの締切、開いている予約の一覧、直近の拒否理由、`ProgressSignature` の連続回数、final latch、停止記録、token・費用の `unmeasured`。**inline 作業で保護枠がすでに削られていること**（非保護枠の締切を過ぎてなお非保護 phase にいる時間）を `reserve_erosion_sec` として別に出す。

## 3. 全 dispatch 入口での admission

### 3.1 締切の計算

kernel の純粋関数 `admit(policy, ledger, at, request) -> Admission | Refusal` が次を計算する。`overall_deadline` は稼働時計が `total_sec` に達する時刻（loop が active の間）。

- `final_unspent = final_reserve − (final の精算済み + 開いている final 予約)`、`repair_unspent` も同様（0 未満は 0）
- 非保護枠の締切 = `overall_deadline − repair_unspent − final_unspent`
- repair の締切 = `overall_deadline − final_unspent`
- final の締切 = `overall_deadline`
- 実効締切 = `min(at + policy_timeout, その phase の締切 − kill_grace_sec − commit_margin_sec)`
- 実効締切 − `at` が `min_dispatch_sec` 未満、同時予約数が上限、phase の予約回数が上限、§3.4 の無進捗に当たる、のいずれかなら `Refusal`

**final の予約は repair が使えない。** repair の締切は常に `final_unspent` を差し引いた値で、repair の dispatch はこれを越える締切を得られない。repair は自身の予約と非保護枠の残りを使えるが、final の予約には届かない。final は自身の予約と他の残りを使えるが、総枠は越えない。

保護枠の消費は、精算済み dispatch 時間の**合計**で数える（並行に走った 2 本は両方とも数える）。これは並行時に多く数える側へ倒れる保守的な近似で、少なく数えることはない。全体の消費は §2.2 の時計で、こちらは足し合わせない。

phase の割当は入口から決まり、caller の指定を受け付けない。final latch の後は verification の入口が final として数えられ、repair の入口は `budget-final-latched` で拒否される。latch は `budget enter-final`（明示）か、`at ≥ repair の締切`（導出）で成立し、初版では戻らない（判断事項 5）。一方向にすることで、早すぎる latch は自分の修復枠を失う結果になり、final 枠を探索に流用する誘因が無くなる。

### 3.2 入口の一覧

予約は spawn の前に fenced commit で保存する（`ReserveDispatchBudget`）。予約 commit が失敗したら起動しない。精算（`SettleDispatchBudget`）は、各入口の終端公開と同じ commit に入れる。

| 入口 | locator | phase | 起動前の検査と予約 | 精算 |
|---|---|---|---|---|
| `verification run` | [S17] `run_verification_receipt_cli` → `run_contract_verifier` → `execute_candidate`、所有は A3.evidence [S29] | 非保護（final latch 後は final） | 新しい予約 commit を spawn 前に追加する。実効締切を `execute_candidate` へ渡す | receipt の公開 commit に入れる |
| `verification run --repro-input` | 同上。replay は同じ経路を通る [S17] | 同上 | 同上 | 同上 |
| `fresh-review run`（[D2c #912](https://github.com/tackeyy/mission/issues/912)、未 merge） | D 設計 §4 の `BeginFreshReviewDispatch`。[D02] 本 head に実装は無い [S13] | 非保護（latch 後は final） | dispatch intent と同じ commit で予約する。締切を dispatch envelope に載せる | terminal（D2b の variant）を公開する commit |
| `fresh-review reconcile`（同上） | [D02] | — | 新しい spawn をしないので予約しない。回復の mutation として扱う | 開いている予約を精算する |
| `repair begin`（[E2 #906](https://github.com/tackeyy/mission/issues/906)、未 merge） | E 設計 §8。[D04] | repair | process を起動しないので時間の予約はしない。attempt の開始を phase の予約回数として数え、final latch 後は拒否する | — |
| `repair reverify`（同上） | E 設計 §4 の「短い fenced intent 保存→B 実行→公開」[D04] | repair | E の実行 intent と同じ commit で予約する | `CommitFindingReverification` の commit |
| `repair reconcile`（同上） | [D04] | — | 予約しない。回復の mutation | 開いている予約を精算する |
| `repair disposition run/reconcile`（[E3 #907](https://github.com/tackeyy/mission/issues/907)、未 merge） | [D04] | repair | `run` は D と同じ dispatch 規律で予約する。`reconcile` は予約しない | terminal の commit |
| `specialists invoke-command` / `invoke-prepared` | [S33] `cmd_invoke_command_provider` → `_invoke_command_provider` の「Section 1: Reservation」[S18]、所有は A4 [S29] | provider の `--phase` から決める: planning→planning、execution→implementation、review/scoring/critic→verification [S31] | 既存の reservation commit に予約を入れる（新しい commit を足さない） | 既存の terminal 更新の commit |
| strict 隔離の provider | [S19][S21] | 同上 | 予算付き session では `budget-deadline-unenforceable` で拒否する（§4） | — |
| `specialists verify-approval` | [S22] | 対象外 | 固定 5 秒＋0.2 秒の猶予で回収済み。予約しない。時間は `commit_margin_sec` に含まれる範囲として扱う | — |
| `specialists reconcile-invocation` | [S31] | — | spawn しない。回復の mutation | 開いている予約を精算する |

- 予算 policy の無い session では、どの入口も予約 commit を作らず、従来どおり動く。
- 本書で照合した範囲では、上表以外に session の品質ループから child を起動する入口は見つからない（`Popen|subprocess.run|os.fork|multiprocessing` を `skills/mission/lib` と `skills/mission/bin/mission-state.py` で検索）。`gate-and-merge` は timeout の無い `subprocess.run` で suite を回すが [S32]、mission repository 自身の merge 工程であり session の dispatch ではないので対象外とする（判断事項 6）。git・ps の短い呼出しは dispatch として数えない。
- 未 merge の D2c・E2・E3 の入口は、その merge 後に最新 main で locator を取り直す。それまでの間、本書の契約は「その入口が予約・精算を同じ commit に入れること」を要求するだけで、型や関数名を決めない。

### 3.3 直列化・再試行・再開

- 予約の判定は repository lock の中で現在の ledger に対して行う。並行する 2 つの入口は lock で直列になり、同じ未使用枠を二重に配らない。fence の古い書き手は予約できない。
- 同じ operation の再応答: B の `verification run` は同じ operation でも再実行する契約で [T03]、F は再実行ごとに新しい予約を取る（予約 ID は operation identity と実行回の連番から導出）。D/E の同じ operation の再応答は再 spawn しないので、新しい予約を取らず、保存済みの予約・精算を返す。こうして retry の消費が欠落も二重計上もしない。
- crash 後に開いたまま残った予約は、その `deadline_at + kill_grace_sec + commit_margin_sec` を過ぎた後の最初の mutation（予約・reconcile・`budget reconcile`）が `charged-full-unknown`（予約秒をすべて消費として計上）で精算する。観測できなかった消費を 0 にしない。締切前の予約は開いたまま残し、同時予約数に数え続ける。
- 再開（Reactivate/ResumeStale）は ledger を codec から復元し、空にしない。開いている予約・精算・latch・停止記録をそのまま残す。

### 3.4 証拠の増えない反復を止める

決定: 精算のたびに `(entry, target)` ごとの `ProgressSignature` を更新する。同じ `candidate_digest` で同じ結果 digest（B は status・exit・count・output digest、D は terminal の output digest、E は reverification receipt digest、provider は outbound packet digest と exit）が `no_progress_limit` 回続いたら、同じ `(entry, target, candidate_digest)` の次の予約を `budget-no-new-evidence` で拒否する。候補が変われば連続回数は 0 に戻る。score に基づく既存の `stagnation_count` [S23] はそのまま残し、置き換えない。

## 4. Timeout と bounded kill

| 入口 | 現状 [根拠] | 決定 |
|---|---|---|
| `verification run` | 締切は凍結 `timeout_sec`。process group へ SIGKILL し、0.2 秒待って再送する。[S16] | `execute_candidate` に `deadline` 引数を足し、`min(凍結 timeout, 実効締切)` で打ち切る。凍結定義は変えない（definition digest を保つ）。F の締切で打ち切った場合は status を `blocked`、`block_reason` を `budget-deadline` とし、`timeout`（policy の判定）と区別する。B の receipt の閉じた `block_reason` 集合へ 1 値を足す |
| command provider | `kill()` は直下の child だけ、`communicate()` は無期限。[S20] | `start_new_session=True` で起動し、締切で process group へ SIGTERM→`kill_grace_sec` 後に SIGKILL、出力の回収も `kill_grace_sec` で打ち切る。回収を確認できなければ terminal に `kill-unconfirmed` を記録し、予約は全額計上のまま同時予約数の枠も解放しない |
| strict backend | in-process の呼出しで、締切も取消しも無い。[S19][S21] | 予算付き session では起動前に拒否する。backend の protocol に締切を足すのは F の範囲外（判断事項 4） |
| fresh review / disposition の adapter | `launch/collect` に締切が無い。`cancel` はある。[S14] | dispatch envelope に `deadline_at` を入れる。application は締切で `cancel` を呼び、その結果を D の `blocked`/`failed` terminal の `cancel_result` に記録する。[D02] 締切後に届いた output は consumed の request として拒否される（D の既存規則） |

**終端は必ず記録できる。** 時間の面では、各予約の締切を phase の締切から `kill_grace_sec + commit_margin_sec` だけ手前に置き、回収と終端 commit の時間を予約の中に確保する。state 容量の面では、E の容量予約がこれを保証する。[D03] F の予約行・精算行は attempt の予約に含まれる 1 種類として数え、§5 の予算停止は E の「停止と回復の mutation」（system 予約を使えるもの）に加える。2 つの予約は別の資源（時間と bytes）を守り、F は E の予約量を変えない。

**限界（成功として扱わないもの）**:

- 親 process が crash した後の orphan child は、mission から回収できない。予約は全額計上され、結果は記録されない。B の runner は一時ディレクトリの複製で実行するので候補を書き換えないが、一時ディレクトリが残るかは実装で確かめる（**UNKNOWN**）。
- `start_new_session` を自ら抜ける孫 process（再度 setsid するもの）は process group の送信から漏れうる。OS の権限境界の代替を主張しない。
- host の inline 作業は mission の dispatch ではないので、F は止められない。止められるのは mission の入口だけで、inline の超過は `reserve_erosion_sec` として表示し、`next` の助言で止めるよう促す。

## 5. 停止と完了 gate

- admission の拒否は state の終端ではない。拒否は理由コード（`budget-exhausted`、`budget-protected-reserve`、`budget-final-latched`、`budget-concurrency-limit`、`budget-dispatch-limit`、`budget-no-new-evidence`、`budget-deadline-unenforceable`、`budget-clock-regressed`）と次の action を返し、拒否回数と直近の理由を ledger に記録する。記録の commit が容量で失敗した場合も起動しない。
- 予算停止は `budget stop` という typed command で行う。kernel が ledger から「必要な次の dispatch がどれも予約できない」ことを導出できる場合だけ受け付け、同じ transition で `MarkHalt(category=partial-done)` と `BudgetStop{scope, reason_code, at, ledger_digest, open_reservations}` を保存する。[S26][S27] 予算が残っているのに `budget stop` を使うことは `budget-not-exhausted` で拒否する。既存の `mark-halt --category partial-done` はそのまま使えるが、`BudgetStop` は残さない（I が「予算による停止」と「他の理由の partial-done」を区別できるようにするため）。新しい HaltCategory は足さない（`terminal_outcome_for_halt` の対応表を変えないため）。
- `next` の差し替えは、非保護枠の締切を過ぎたら spawn 系の action を「final へ移る」助言へ、全体が尽きたら `budget stop` の助言へ変える。terminal・await-user・安価な確定手を差し替えない既存の規則は保つ。[S03][T01]
- **予算は完了を許可しない。** mark-passes・closeout・force の経路は既存 gate を一切緩めない。F が完了 gate に足すのは 1 条件だけ: 契約付き session で開いている予約があれば `acceptance-dispatch-unsettled` で拒否する（走っている child の結果を待たずに完了しない）。C/D の gate と同じ guard（pure kernel と application preflight の両方）で、force より前に評価する。[S27] 理由コードの優先順は D の既定の後ろに置く。予算切れの session は passes を持たずに partial-done で終わり、成功を偽らない。

## 6. 変更面、保持する保証、検証方針

### 6.1 変更面

| 層 | 追加・変更 |
|---|---|
| kernel | 新 module（policy/ledger の型・decoder・`admit`・reducer）。commands union と transition registry に `ImportBudgetPolicy`・`ReserveDispatchBudget`・`SettleDispatchBudget`・`EnterFinalPhase`・`BudgetStop`。lifecycle の reducer（MarkHalt・MarkPass・Reactivate・ResumeStale）に ledger がある場合だけ時計の区間を開閉する処理。generic set の保護集合の拡張 [S07] |
| codec | v4 の `budget_ledger` と v5 の `extensions.budget_ledger` の閉じた復元と、保存面との一致検査 |
| application | `budget policy import/status/enter-final/stop/reconcile` の use case。§3.2 の各入口での予約・締切の受け渡し・精算。runner の `deadline` 引数。command provider の process group 回収 |
| CLI | parser と 1 use case 呼出しだけ。thin-adapter の baseline を増やさない |
| inventory | `command_owners.py` に mutation を A1.lifecycle（`budget stop`・`budget enter-final`）と A3.evidence（`budget policy import`・`budget reconcile`）へ、`budget status` を R1.query へ登録する [S29]。guidance parity の入力に `$.budget_ledger` [S06]。配布 mirror の同期 |
| 他の設計への要求 | D2c の dispatch envelope に `deadline_at`（判断事項 3）。E2・E3 の容量予約の種類に F の予約行（[D03]） |

### 6.2 保持する保証（緩めない）

| `skills/mission/tests/` 配下の file::test | 保持する保証 |
|---|---|
| `test_issue238_budget_pressure.py` の全 9 件 [T01] | ledger の無い session の budget pressure・warn・差し替え・非差し替えを変えない |
| `test_issue878_candidate_snapshot.py::test_runner_bounds_output_times_out_and_rejects_zero_test_count` [T02] | policy の timeout は `timeout` のまま blocked。F の締切は別の理由で区別する |
| `test_issue878_verification_runner.py::test_same_operation_retry_runs_the_verifier_again_and_rejects_a_different_receipt` [T03] | B の再実行の契約。F は回ごとに予約を取る |
| `test_issue895_fresh_review.py::test_budget_and_criterion_rejection_has_no_public_effect` / `test_packet_budget_and_runtime_commands_remain_closed` [T04] | D の request の予算 field の閉じた検査 |
| `test_issue742_stop_guard_timeout.py` の budget 系 [T05] | stop guard の 8 秒予算は別の仕組みで、F は触れない |
| E 設計 §8 の保持表と D 設計 §7 の保持表 [D03][D04] | completion gate・force・codec・fence・operation 再応答の保証 |

本書の検索（`timed_out|killpg|command provider timed out` を `skills/mission/tests` で）では、command provider の timeout 経路を直接検査するテストは見つからない。F2 で Red から足す。

### 6.3 検証方針

- TDD で、現実的な故障を先に Red にする: 初回 phase の dispatch が repair・final の予約を食い尽くす、retry の二重計上と欠落、crash 後の予約の 0 計上、halt 中の時間の計上、時計の後退、並行予約の二重配賦、締切後も child が残る（孫 process を含む）、command provider の回収が終わらない、`budget stop` が予算の残る session で通る、開いている予約がある状態での mark-passes。
- 変異（検出できることを確かめる対象）: 保護枠の差し引きを外す、`<` と `≤` の取り違え、repair の締切から `final_unspent` を外す、開いている予約を unspent から外す、`charged-full-unknown` を 0 にする、精算を 2 回適用する、実効締切を `policy_timeout` だけにする、並行 dispatch を稼働時計へ足す、kill を直下の child だけにする、時計の後退を受け付ける、no-progress の候補比較を外す。各変異で少なくとも 1 件のテストが落ちること、正常系が通ることの両方を固定する。
- 抜け穴探索: `admit`、policy decoder、ledger decoder に対し、正常 / 拒否を合わせて 50 入力以上を独立に作って通す（境界の秒、bool を int として渡す、配分の合計が 9999/10001、未知 field、null、重複 ID、上限超過の開いた予約、逆順の時刻、締切ちょうど、`min_dispatch_sec` ちょうど、latch 後の repair、同時上限ちょうど、候補だけ違う無進捗）。件数と発見数を PR 本文に書く。本設計では実施していない。
- 公開 CLI の end-to-end（fixture の verifier と provider）: 予約→実行→精算、締切での回収、crash 後の精算、予算停止、legacy の不変。制御された時計の fixture で「repair 枠が残ること」と「総枠を越えないこと」を検査する。
- 新規テストの費用は **UNKNOWN**。実装後に対象実行で計測する。CI は shard 経由で tracked tests を選ぶ既存経路を使う。

## 7. Reviewed lines と分割

全体の見積（追加+削除、配布 mirror 除外、未実測）。過去の設計見積が実測の約 1/1.6 だったため、素の見積と ×1.6 を併記する。repo の閾値は 600 行で説明、1,400 行で分割必須。[AGENTS.md:119-128][S34]

| PR | 範囲 | 素の見積 | ×1.6 |
|---|---|---:|---:|
| F1: typed policy・ledger・phase reserve・status | kernel の型・decoder・`admit`・全 reducer（予約・精算・latch・停止を含む純粋部分）、codec、generic set 保護、時計の区間の開閉、`budget policy import/status`、`next` の表示。入口はまだ予約しない（観測と表示だけの安全な中間状態） | 700〜850 | 1,120〜1,360 |
| F2: 全入口の admission・timeout・bounded kill（Closes #881） | §3.2 の全入口の予約・精算、runner の締切、provider の process group 回収、strict の拒否、crash 後の精算、`budget enter-final/stop/reconcile`、完了 gate の 1 条件、no-progress、end-to-end | 950〜1,150 | 1,520〜1,840 |

**F2 は依頼どおりの 2 分割では 1,400 を越える見込みである。** 加えて F2 は未 merge の D2c・E2・E3 の入口に依存する。推奨は F2 を次の 2 つに分けること（判断事項 7）。

| PR | 範囲 | 素の見積 | ×1.6 |
|---|---|---:|---:|
| F2a: 既存入口の admission と回収 | `verification run`・command provider・strict の拒否、締切と process group 回収、crash 後の精算、`budget reconcile`、完了 gate の 1 条件、no-progress | 550〜650 | 880〜1,040 |
| F2b: D/E 入口の統合と停止（Closes #881） | fresh-review run/reconcile・repair reverify/reconcile・disposition run/reconcile の予約と精算、`enter-final`・`budget stop`、`next` の差し替え、end-to-end | 420〜520 | 670〜830 |

F1 は 600 行を越えるので、PR 本文に「型・decoder・reducer・codec を同時に成立させないと保存面の検査が空回りする」ことを分割しない理由として書く。行数は実 diff を `scripts/pr_size.py` で再計測する。

## 8. 判断が必要な事項と対象外

### 判断事項（orchestrator / owner）

1. **非保護 3 phase を合算で強制するか、個別に強制するか。** 推奨は合算（§2.3）。個別に強制すると、観測できない inline 作業との差が表示と実態のずれになる。個別強制を選ぶ場合、inline 時間を phase へ割り当てる規則（`control.phase` に従う等）の決定が要る。
2. **halt 中の時間を数えるか。** 推奨は数えない（§2.2）。既存の budget pressure（halt 中も数える）と定義が変わるため、ledger の無い session の表示は変えない。数える場合、承認済みの reactivate の直後に予算停止が起きうる。
3. **締切を D2c の dispatch envelope に入れるか。** 推奨は D2c（[#912](https://github.com/tackeyy/mission/issues/912)）の実装時に `deadline_at` field を envelope の閉じた集合へ含めておくこと。後から F2b で足すと D の閉じた schema を版上げすることになる。
4. **strict 隔離の provider をどう扱うか。** 推奨は予算付き session で拒否（§4）。代替は、承認 verifier と同じ fork と process group 回収 [S22] で backend を包むことだが、host が提供する隔離の前提を変えうるため F では行わない。planning を strict provider に頼る構成では、予算付き session で planning provider が使えなくなる。
5. **final latch を一方向にするか。** 推奨は初版では一方向（§3.1）。最終検証で新しい失敗が見つかった場合は partial-done で終わる。戻せるようにすると、final 枠を探索に流用できる。
6. **`gate-and-merge` を対象外にしてよいか。** 推奨は対象外（session の dispatch ではない）。対象に含めるなら、timeout の無い suite 実行 [S32] への締切の導入が別途必要。
7. **分割**。依頼は F1/F2 の 2 分割だが、F2 は ×1.6 で 1,400 を越える見込み。推奨は F1 → F2a → F2b の 3 分割。F2b は D2c・E2・E3 の merge 後、F1・F2a は Issue の「E の merge 後」という依存を保つか、D/E と独立なので先行させるかを決める（先行させる場合も、F2b の締切 field の形は判断事項 3 で先に固定する）。
8. **予算の延長**。F では延長の手段を作らない（policy は一度だけ import）。延長が要るなら、reactivate と同じく利用者の明示承認と監査記録を持つ typed command を別 Issue で設計する。
9. **予算 policy を必須にするか**。F は policy の無い契約付き session を従来どおり通す。J（[#885](https://github.com/tackeyy/mission/issues/885)）の verified-complex profile で必須にするかは J で決める。

### 対象外

- 既存 review tier の降格（旧 #611 は再開しない）、既存 gate・mark-passes の緩和。
- token・費用の推定値を強制に使うこと。値の producer の新設。
- host の inline 作業の強制停止、親 crash 後の orphan の回収、strict backend の protocol 変更。
- 予算延長の command、phase 配分の自動調整、初期値の最適化（I の計測の後に別途）。
- event/export の新設（E4 の `mission-repair-event/1` と I の集計に任せ、F は status と停止記録を供給する）。
- closed v5 の public bridge（D・E と同じく対象外）。

この設計は設計レビューの対象であり、実装・CI の受入ではない。

## 固定 head の出典

リンクは全て `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`。各ラベルの file:line はこの head で読んだ範囲。テストの引用はコードの保証を指し、本ステップで実行した結果を指さない。

[S01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L8403-L8420 "mission-state.py:8403-8420 — _validated_budget_minutes"
[S02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L8423-L8463 "mission-state.py:8423-8463 — BUDGET_PRESSURE_WARN_PCT・BUDGET_SPAWN_ACTIONS・_budget_pressure（started_at からの壁時計）"
[S03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L8666-L8718 "mission-state.py:8666-8718 — cmd_next の read-only な差し替え"
[S04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L16112-L16114 "mission-state.py:16112-16114 — init --max-iter / --budget-minutes"
[S05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/lifecycle.py#L356-L366 "mission_application/lifecycle.py:356-366 — new mission の予算検証; legacy_initialization.py:187 で保存"
[S06]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/guidance.py#L256-L260 "mission_kernel/guidance.py:256-260 — application.clock-budget-override は outside-parity"
[S07]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/commands.py#L386-L455 "mission_kernel/commands.py:386-455 — GENERIC_SET_FROZEN_FIELDS / GENERIC_SET_DEDICATED_FIELDS（budget_minutes を含まない）"
[S08]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L282-L334 "mission_kernel/transitions.py:282-296 timing/activity field、334 Reactivate が書き換えてよい field"
[S09]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L869-L947 "mission_kernel/transitions.py:869-947 — _reactivate（approved_by_user 必須）と _resume_stale; 526-556 再開の監査"
[S10]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/activity_segments.py#L11-L57 "activity_segments.py:11-57 — activity の種類・理由・RECENT_SEGMENT_LIMIT"
[S11]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L2073-L2080 "mission-state.py:2073-2080 — _mission_started_at の fallback 順"
[S12]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/fresh_review.py#L19-L76 "mission_kernel/fresh_review.py:21-22 BUDGET_LIMITS、71-76 validate_budgets、111-115 request の予算 field"
[S13]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L16471-L16484 "mission-state.py:16471-16484 — fresh-review は prepare/status のみ"
[S14]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/fresh_review_runtime.py#L44-L58 "fresh_review_runtime.py:44-58 — adapter protocol（launch/collect に締切なし、cancel あり）"
[S15]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/verifier_command.py#L25-L39 "verifier_command.py:25-39 — timeout_sec は 1〜3600 の int"
[S16]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/verification_runner.py#L279-L420 "mission_application/verification_runner.py:279-420 — execute_candidate（329-332 start_new_session、356-372 締切で killpg、380-388 再送、405-419 status/block_reason）"
[S17]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/verification_execution.py#L34-L161 "mission_application/verification_execution.py:34-82 CLI（起動前の intent なし）、85-161 run_contract_verifier"
[S18]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/command_provider.py#L187-L340 "mission_application/command_provider.py:187-193 _provider_timeout、310-340 timeout と Section 1: Reservation"
[S19]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L10224-L10247 "mission-state.py:10224-10247 — _run_strict_provider_backend（in-process、締切なし）; command_provider.py:446-456 から呼ぶ"
[S20]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/command_provider.py#L518-L574 "mission_application/command_provider.py:518-574 — Popen（新 session なし）、kill() と timeout の無い communicate()"
[S21]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/provider_preflight.py#L192-L236 "provider_preflight.py:192-236 — strict_spawn / dispatch_prepared_packet"
[S22]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L10390-L10431 "mission-state.py:10390-10431 — 承認 verifier の fork・5 秒打切り・process group 回収; 10089-10090 定数"
[S23]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/next_action.py#L179-L185 "mission_application/next_action.py:179-185 — stagnation の助言; mission-state.py:13808-13821 更新、guidance.py:683-687"
[S24]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L11057-L11061 "mission-state.py:11057-11061 — max_iter は早期停止の報告に使う"
[S25]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/planning_provider_metrics.py#L8-L17 "planning_provider_metrics.py:8-17 — 件数と率だけの KPI"
[S26]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/model.py#L97-L106 "mission_kernel/model.py:97-106 — HaltCategory"
[S27]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/transitions.py#L803-L830 "mission_kernel/transitions.py:803-830 _mark_halt; 987-1052 契約 gate（最後に acceptance-fresh-review-pending）; 1068 _mark_pass"
[S28]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_kernel/json_codec.py#L13-L13 "mission_kernel/json_codec.py:13 — STATE_LIMIT 4 MiB"
[S29]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_application/command_owners.py#L10-L95 "mission_application/command_owners.py:10-95 — A1.lifecycle / A2.review / A3.evidence（52 verification run）/ A4（66 invoke-command）/ R1.query"
[S30]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/mission_persistence/fenced_commit.py#L1886-L1895 "mission_persistence/fenced_commit.py:1886-1895 — repository lock（5 秒で lock-timeout）"
[S31]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L16815-L16870 "mission-state.py:16815-16870 — specialists invoke-command / invoke-prepared / prepare-invocation --phase / reconcile-invocation"
[S32]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/lib/integration_gate.py#L60-L77 "integration_gate.py:60-77 — timeout の無い subprocess.run; mission-state.py:7337 cmd_gate_and_merge"
[S33]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L5686-L5770 "mission-state.py:5686-5770 — cmd_invoke_command_provider の配線（strict_dispatch・Popen・TimeoutExpired）"
[S34]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/AGENTS.md#L119-L128 "AGENTS.md:119-128 — 600 / 1,400 の閾値"
[T01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue238_budget_pressure.py#L41-L140 "test_issue238_budget_pressure.py:41-140 — 9 件"
[T02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue878_candidate_snapshot.py#L74-L74 "test_issue878_candidate_snapshot.py:74 — runner の timeout"
[T03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue878_verification_runner.py#L327-L342 "test_issue878_verification_runner.py:327-342 — 同じ operation の再実行"
[T04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue895_fresh_review.py#L90-L194 "test_issue895_fresh_review.py:90, 194 — request の予算の閉じた検査"
[T05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue742_stop_guard_timeout.py#L368-L393 "test_issue742_stop_guard_timeout.py:368-393 — stop guard の予算"
[D01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/689-fresh-review-receipt.md#L66-L71 "docs/design/689-fresh-review-receipt.md:66-71 — request の予算上限、金額予算と repair 予約は後続へ; 181-186 coverage を contract へ書かない理由"
[D02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/689-fresh-review-receipt.md#L211-L238 "docs/design/689-fresh-review-receipt.md:211-238 — terminal variant（budget_used・cancel_result）と dispatch saga（D2c 以降は未 merge）"
[D03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L48-L59 "docs/design/880-repair-lineage.md:48-59 — E の容量予約（attempt 予約と system 予約）と公開の回復; 25 保存方式"
[D04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L155-L166 "docs/design/880-repair-lineage.md:155-166 — repair の公開 command（未 merge）; 112 再検証の intent→実行→公開"

### 決定（orchestrator, 2026-10-04）

§8 の未決事項は、設計レビューの前に次のとおり決める（いずれも推奨案を採用）。設計レビューで異論が出た場合は見直す。

1. 保護しない 3 phase（計画・実装・検証）は 1 つの共有 pool として強制する。
2. halt 中の時間は経過時間に数えない。
3. D2c（#912）の dispatch envelope に `deadline_at` を今の段階で入れる。D の閉じた schema を後から版上げしないためで、D2c の実装範囲に加える。
4. budget 付き session では strict-isolation provider を拒否する（包む方式は後続で検討する）。
5. final phase の latch は初版では一方向とする。
6. `gate-and-merge` は対象外とする。
7. 分割は F1 → F2a → F2b の 3 PR とする（較正済み見積りで F2 が 1,400 行を超えるため。分割の追加は owner 承認済みの方針に従う）。F の着手は owner 決定どおり E3 の merge 後とし、F1 は E3 merge 後、F2a/F2b は D2c・E2・E3 の入口が main に揃ってから着手する。
8. budget の延長は F に含めない。必要になれば別 Issue とする。
9. J の verified-complex profile は budget policy を必須とする（外部評価の公平条件として wall-clock 上限 T を全 arm で共通にするため。I の事前登録と整合）。
