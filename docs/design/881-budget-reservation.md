# 修復と最終検証の予算を予約して実行を制御する設計

決定案: 既存の `budget_minutes` を総枠とし、型付きの予算 policy と ledger を新しい projection に置く。policy は `init` の時点でだけ受け付け、**mission が起動する全ての spawn 入口に admission が入るまで受け付けない**（§3.5）。全体の消費は「稼働時計」1 本で数え、並行 child の時間を足し合わせない。修復と最終検証の枠だけを保護枠とし、全ての spawn 入口で起動の前に durable な予約（時間と state bytes の両方）を取る。予約できなければ起動せず理由付きで拒否する。予算切れは kernel が導出する一方向の exhaustion として扱い、pass に到達できない（force を含む）。

**保証の範囲（設計レビュー round 1 を受けて狭めた）**: F が強制するのは「policy を持つ session で、mission の spawn 入口から起動した child の時間と終端記録」だけである。host の inline 作業（mission を経由しない編集・実行と、host が自分で起動する planner・executor・reviewer の subagent。§3.2 #16）の時間は止められず、保護枠を侵食した量を記録して表示するだけにとどまる（§4.4）。policy の無い session は従来どおり助言のみで、status に `enforcement: advisory-only` と出す。

対象: [Issue 881: 修復と最終検証の予算を予約して実行を制御する](https://github.com/tackeyy/mission/issues/881)（親 [Issue 876: 品質改善の全体追跡](https://github.com/tackeyy/mission/issues/876)）。本書は設計のみ。実装、テスト追加、Issue 起票・本文変更、Git 操作による公開は行わない。
照合 head: `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`（依頼で固定）。本書の「現状」はこの head のソースを指す。remote の main は `9c948878d878bbadbfb98a1d62a43d67fcc700c7`（[D2b #909](https://github.com/tackeyy/mission/issues/909)、PR #911 の merge を含む）へ進んでいる。spawn 箇所の検索（§3.2）だけは両方の head で行い、同じ集合だった。それ以外の差分は照合していない（**UNKNOWN**）。実装の着手時に最新 main で再照合する。

関連設計: E は `origin/docs/880-e2e3-decisions`（`9e99f068`）の改訂版 [D05]、I は `origin/docs/884-eval-prereg`（`aa604792`）[D06] を読んだ。どちらも並行して改訂中で、本書はその時点の記述にだけ結ぶ。

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

決定: 稼働時計は `started_at`（無ければ `_mission_started_at` と同じ順の fallback）から始まり、`MarkHalt` と `MarkPass` で閉じ、`Reactivate` と `ResumeStale` で開く区間の和とする。[S08][S09][S11] 停止区間は数えない（判断事項 2）。

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
| `no_progress_limit` | int、初期値 2 | §3.4 |
| `provenance` | `experimental-initial` 固定 | 初期値が実証済みではないことを保存面に残す |

`budget_minutes` は float で保存され、`repr` は往復できる最短の 10 進表記を返すので、`Decimal(repr(...))` は利用者が渡した 10 進値と一致する（例: 1.1 分 → 66 秒、0.01 分 → 0 秒で拒否）。2 進の誤差で 65 秒へ落ちることはない。

**配分（計画 10 / 実装 40 / 検証 20 / 修復 20 / 最終 10 %）と上表の他の初期値は実験用の初期値であり、効果が実証された既定値ではない。** status にも `provenance` をそのまま出す。値の妥当性は I（[Issue 884](https://github.com/tackeyy/mission/issues/884)）の計測で判断する。

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
| `StopSlots` | 事前確保した固定長の slot: 拒否の計数と直近の理由、`FinalLatch`（時刻と理由）、`Exhaustion`（時刻と原因）、`BudgetStop`（§5）。policy の受付時に確保し、以後は上書きだけ（増分 0） |

- state に置くのは上限付きの要約だけとする。開いている予約は `max_concurrent_dispatches` 件以下、精算済みは分類ごとの合計と直近 32 件だけ。`ProgressSignature` の件数は予約回数の上限（分類 5 × `max_dispatches_per_phase`）で抑えられる。dispatch の詳細な事実は、各入口が既に公開している receipt に残る。[S16][D02]
- キー欠落だけが「予算 policy なし」。null・未知 schema・未知 field・重複 ID・開いている予約の上限超過・時刻の逆転は理由コード付きで拒否し、空の ledger と読み替えない。
- `budget_ledger` とその子孫は generic set・init/reinit・compatibility delta・review/score import・specialist evidence・downgrade から保護する（D・E と同じ集合を拡張する。[S07]）。reinit は ledger を変更・削除しない。ledger がある session では `budget_minutes` の generic set も拒否する。ledger の無い legacy session の挙動は変えない。

### 2.5 Status の表示

決定: `budget status`（R1.query、read-only）を追加する。`next` には ledger がある session だけ `budget` ブロックを足し、`budget_pressure` を ledger の稼働時計から計算して `basis: "active-clock"` を付ける。ledger の無い session の出力は変えず（[T01] を保持）、`budget status` は `enforcement: advisory-only` を返す。

表示する項目: `enforcement`（`enforced`/`advisory-only`）、policy の値と `provenance`、総枠・消費・残り・外部締切、分類ごとの目標・精算済み・開いている予約、保護枠の未使用分、非保護枠・修復・最終それぞれの締切、開いている予約の一覧、直近の拒否理由、`ProgressSignature` の連続回数、final latch、exhaustion、停止記録、`state_capacity_verdict` の残量（E0 の値を読むだけ）、token・費用の `unmeasured`、`reserve_erosion_sec`（§4.4）。

## 3. 全 spawn 入口での admission

### 3.1 締切の計算と予算分類

kernel の純粋関数 `admit(policy, ledger, state, at, request) -> Admission | Refusal` が次を計算する。

- `final_unspent = final_reserve − (final の精算済み + 開いている final 予約)`、`repair_unspent` も同様（0 未満は 0）
- 非保護枠の締切 = `overall_deadline − closeout_margin_sec − repair_unspent − final_unspent`
- repair の締切 = `overall_deadline − closeout_margin_sec − final_unspent`
- final の締切 = `overall_deadline − closeout_margin_sec`
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

provider の state の `phase` は、eligibility が caller の `--phase` との一致を要求する値である（`MISSION_PHASE_TO_PROVIDER_PHASE` と `mission-phase-mismatch`）。[S37] F はこの一致検査を残しつつ、分類の入力には `--phase` ではなく state の `phase` を使う。`phase` は generic set の dedicated 集合にあり、汎用書込みでは動かない。[S07] 非保護の分類はどれも同じ共有枠なので、`phase` の選び方で保護枠を得ることはできない。

final latch は `budget enter-final`（明示）か `at ≥ repair の締切`（kernel が導出）で成立し、初版では戻らない（判断事項 5）。

### 3.2 Spawn 箇所の一覧（同形検索の結果）

予約は spawn の前に fenced commit で保存する（`ReserveDispatchBudget`）。予約 commit が失敗したら起動しない。精算（`SettleDispatchBudget`）は、各入口の終端公開と同じ commit に入れる。

検索: `Popen|subprocess.run|os.fork|multiprocessing|run_contract_verifier|dispatch` に、`subprocess.(check|call|getoutput|getstatus)|os.system|posix_spawn|.fork(|pty.|asyncio.create_subprocess|get_context(` を加え、`skills/mission/bin/mission-state.py` と `skills/mission/lib` を `d25a66c6` と `9c948878` の両方で検索した（ファイルごとのヒット数は両 head で同じ）。下表は全ヒットを分類したもの。`dispatch` 語は 2 head とも多数ヒットするので、定義と呼出し（`def *dispatch*`・`dispatch*(`）に絞って分類した（#4・#16）。`skills/mission/bin/mission-migrate.py` は session を動かさない移行 tool なので検索対象外とした。

| # | spawn 箇所 | 入口 | 扱い | 予算分類 | 起動前の検査と予約 | 精算 |
|---|---|---|---|---|---|---|
| 1 | `verification_runner.py:329` の Popen [S16]、前段の `verification_runner.py:48` の `git ls-files` [S42] | `verification run`（`--repro-input` を含む）。`run_verification_receipt_cli` → `run_contract_verifier`（`verification_execution.py:58,85`）[S17]、所有 A3.evidence [S29] | 予約する | 表 §3.1 | spawn 前に予約 commit を追加し、監督 process ごと締切で包む（§4.2） | receipt の公開 commit |
| 2 | 同上（E の再検証が B を呼ぶ） | `repair reverify`（[E2 #906](https://github.com/tackeyy/mission/issues/906)、未 merge）[D04] | 予約する | repair | E の実行 intent と同じ commit で予約 | `CommitFindingReverification` の commit |
| 3 | `command_provider.py:520` の Popen [S20]（mission-state.py:5751 で注入 [S33]） | `specialists invoke-command` / `invoke-prepared` [S31]、所有 A4 [S29] | 予約する | 表 §3.1 | 既存の Section 1 reservation commit [S18] に予約を入れる | 既存の terminal 更新の commit |
| 4 | `mission-state.py:10233` の `dispatch_prepared_packet`（in-process）[S19][S21] | strict 隔離の provider | 予算付き session では `budget-deadline-unenforceable` で拒否 | — | — | — |
| 5 | `mission-state.py:10409` の fork [S22]、呼出し 5550 [S40] | `specialists verify-approval` | 予約する（旧版の対象外を撤回） | 表 §3.1（preflight packet の phase から state 経由で導出） | lock を取る既存の transaction（5525）の**前に**、別の短い transaction で予約 commit | 既存 transaction の commit |
| 6 | 同上、呼出し `mission-state.py:10445` ← 14123 ← `review.py:523` [S41] | `mark-passes --force` | 予約する | 表 §3.1 | 予算 guard（§5）を通った後、pass の transaction の前に予約 commit | pass の commit。pass が拒否された場合は拒否の後に精算だけを commit |
| 7 | adapter の `launch/collect/cancel` [S14] | `fresh-review run`（[D2c #912](https://github.com/tackeyy/mission/issues/912)、未 merge）[D02] | 予約する | 表 §3.1 | dispatch intent と同じ commit で予約。全 adapter 呼出しを締切付きの子で行う（§4.2） | terminal（D2b の variant）の commit |
| 8 | 同上 | `repair disposition run`（[E3 #907](https://github.com/tackeyy/mission/issues/907)、未 merge）[D04] | 予約する | repair | D と同じ規律 | terminal の commit |
| 9 | adapter の `recover` [S14] | `fresh-review reconcile`、`repair reconcile`、`repair disposition reconcile`（未 merge） | 回復の mutation。時間の予約はしないが、呼出しは `adapter_call_sec` の締切付き子で行う | — | exhaustion 後も許す（開いた予約を閉じる唯一の手段のため） | 開いている予約を精算 |
| 10 | — | `repair begin`（E2）[D04] | process を起動しない。分類の予約回数として数え、`repair の締切 − at` が B の最小実行（`min_dispatch_sec + cleanup_sec(B) + commit_margin_sec`）に満たなければ拒否 | repair | E の容量予約（[D05] の「受付時に全段を確保」）と同じ commit | — |
| 11 | — | `specialists reconcile-invocation` [S31] | spawn しない。回復の mutation | — | — | 開いている予約を精算 |
| 12 | `integration_gate.py:67,79,171` [S32] | `gate-and-merge` | 対象外（mission repository 自身の merge 工程で、session の dispatch ではない。判断事項 6） | — | — | — |
| 13 | git・ps の短い呼出し: `mission-state.py:1150, 2033, 2038, 6029, 6049, 6058, 6102, 10515, 10538, 14495`、`worktree_archive.py:115, 655`、`evidence.py:191` [S43] | 状態の観測・archive・revision scope の検証 | dispatch として数えない。agent の作業を起動しないため。時間は稼働時計に含まれる。timeout の無い git 呼出しが含まれるが、F の範囲外とする | — | — | — |
| 14 | `mission_python_inventory.py:153` [S43] | repository の import 検査 tool（mission-state.py から import されない） | 対象外 | — | — | — |
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

### 3.4 証拠の増えない反復を止める

決定: 精算のたびに `(entry, target)` ごとの `ProgressSignature` を更新する。同じ `candidate_digest` で同じ結果 digest（B は status・exit・count・output digest、D は terminal の output digest、E は reverification receipt digest、provider は outbound packet digest と exit）が `no_progress_limit` 回続いたら、同じ `(entry, target, candidate_digest)` の次の予約を `budget-no-new-evidence` で拒否する。候補が変われば連続回数は 0 に戻る。score に基づく既存の `stagnation_count` [S23] は残す。

### 3.5 有効化の fail-closed（設計レビュー round 1 の High 1）

決定: 予算の有効化（policy を持つ session を作ること）は、**main 上の全 spawn 入口に admission が入っていることを code で確かめてから**でないと成立しない。

- kernel に閉じた表 `BUDGET_SPAWN_ENTRIES` を置き、§3.2 の各入口を `covered` / `pending` / `excluded`（理由付き）のいずれかで持つ。`init --budget-policy` の use case は、表に `pending` が 1 件でもあれば `budget-admission-incomplete` で拒否し、session を作らない。
- inventory test を置く。§3.2 の文字列検索は偽陽性（#15）を含むので、test は AST で `subprocess` の各関数・`os.fork`/`os.system`/`os.posix_spawn*`・`multiprocessing` の context と `Process`・注入された `Popen` の呼出し・adapter protocol の method 呼出し [S14] を call node として拾い、`skills/mission/lib` と `skills/mission/bin/mission-state.py` を走査して、ヒットした全箇所が表のどれかに結ばれていること、`covered` の入口が実際に予約 use case を呼ぶこと（fixture で予約前に spawn しようとすると失敗すること）を検査する。新しい spawn 箇所が表に無ければ test が落ちる。後から merge される D/E の入口（D2c・E2・E3、その後の E4 など）は、その PR で表に `covered` として足さない限り CI を通らない。
- 段階との対応（§7）: F1・F2a・F2b の間は `init --budget-policy` の parser 自体を公開しない。F2a は既存入口を `covered`、D/E 入口を `pending` として表に入れる。F2c は `pending` を 0 にし、同じ PR で `init --budget-policy` を公開する。**policy を持つ session が存在しうる時点では、全入口が covered である。**
- 旧版の窓（F1 で import を公開、F2a 後も D/E 入口が F2b まで未対応）はこれで閉じる。

## 4. Timeout、bounded kill、終端の容量

### 4.1 入口ごとの締切後の待ち（`cleanup_sec`）

子の締切に達した後、親が順に行う待ちを全て数え、`cleanup_sec(entry)` とする。kernel は入口ごとの式を閉じた表で持つ。

| 入口 | 締切後の順序付きの待ち | `cleanup_sec(entry)` |
|---|---|---|
| B の verifier（#1・#2） | 監督 process の中で: SIGKILL→0.2 秒の wait→再送 [S16]（1 秒に切り上げ）→候補と報告の読取り（`post_run_sec` 以内）。監督 process が超過したら親が: 監督と verifier の両 process group へ SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec` | `1 + post_run_sec + term_grace_sec + kill_wait_sec` |
| command provider（#3） | process group へ SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec`→pipe の回収（`collect_sec` 以内） | `term_grace_sec + kill_wait_sec + collect_sec` |
| adapter（#7・#8） | 実行中の呼出し子へ SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec`→`cancel` 呼出し（`cancel_call_sec` 以内）→その超過時の SIGTERM→`term_grace_sec`→SIGKILL→`kill_wait_sec` | `2 × (term_grace_sec + kill_wait_sec) + cancel_call_sec` |
| 承認 verifier（#5・#6） | 締切を渡さない。5 秒の join と最大 4 回の 0.2 秒待ち [S22] | 固定。`reserved_sec = 6 + commit_margin_sec` として扱う |

初期値では B 14 秒、provider 5 秒、adapter 16 秒、承認 verifier 6 秒。これに `commit_margin_sec` を足した時間が、分類の締切の手前に確保される。

**親の待ちに無期限のものを残さない。** 現状の無期限の待ち（provider の `communicate()` [S20]、失敗経路の `wait(timeout=5)` の後に残る child [S39]）は、上の順序へ置き換える。SIGKILL の後 `kill_wait_sec` で終了を確認できない場合は、それ以上待たずに `kill-unconfirmed` を記録する（§3.3）。

### 4.2 締切付きの呼出し境界（設計レビュー round 1 の High 3）

- **B の verifier**: `run_contract_verifier` 全体（候補の採取、Popen、候補と報告の読取り）を fork した監督 process で実行する。承認 verifier と同じ仕組み（`multiprocessing.get_context("fork")`、pipe、process group の回収）を使う。[S22] 監督 process は verifier の pgid を起動直後に pipe で親へ送り、親は超過時に両方の group へ送信する。verifier の締切は、予約時に観測した wall 時刻と monotonic 時刻の組から `child_deadline_at` を monotonic へ換算して渡す（現状の runner は Popen の後に締切を決める [S42] ので、前段の時間を締切に含めるため）。凍結 `timeout_sec` は変えず、`min(凍結 timeout, 換算した締切)` で打ち切る。F の締切で打ち切った場合は status を `blocked`、`block_reason` を `budget-deadline` とし、`timeout`（policy の判定）と区別する。B の receipt の閉じた `block_reason` 集合へ 1 値を足す。fork が使えない host では予算付き session の `verification run` を `budget-deadline-unenforceable` で拒否する。
- **command provider**: `start_new_session=True` で起動し、締切を予約時刻からの絶対時刻にする（起動後の記録 commit の lock 待ちも締切に含める）。`communicate()` の timeout は「締切 − 現在」で毎回計算する。締切後は §4.1 の順序で回収する。
- **adapter（D2c・E3）**: `observe_parent/launch/collect/cancel/recover` の各呼出しを、fork した呼出し子の中で行う。呼出し子の締切は `min(adapter_call_sec, child_deadline_at − 現在)`（`cancel` は `cancel_call_sec`）。戻り値は `max_output_bytes` と閉じた observation の上限で pipe 越しに受け取り、親は締切付きで読む。締切で `collect` の呼出し子を回収した後、`cancel` を呼び、その結果を D の `blocked`/`failed` terminal の `cancel_result` に記録する。[D02] `cancel` の呼出しが超過した場合や、adapter が起動した reviewer の終了を観測できない場合は `kill-unconfirmed` とし、reconcile の `recover` が終了を観測するまで同時予約数を解放しない。D2c は全ての adapter 呼出しを 1 つの application の seam から行うこと（判断事項 3）。
- **承認 verifier**: 既存の固定上限をそのまま使う。

**前提（保証の外）**: 上の上限は、親 process が締切後の待ちを定数どおりに進められることを前提とする。親の停止・OS の stall・lock の 5 秒待ちが `commit_margin_sec` を越えて続く場合、精算は `settle_by` より遅れうる。そのときも子は回収済み（または `kill-unconfirmed`）で、遅れは `late_settlement_sec` として記録し、計上は予約秒を下回らない。

**限界（成功として扱わないもの）**:

- 親 process が crash した後の orphan は、mission から回収できない。予約は `settle_by` 後に全額計上され、結果は記録されない。B の runner は一時ディレクトリの複製で実行するので候補を書き換えないが、一時ディレクトリが残るかは実装で確かめる（**UNKNOWN**）。
- `start_new_session` を自ら抜ける孫 process（再度 setsid するもの）は process group の送信から漏れうる。OS の権限境界の代替を主張しない。
- adapter が host 側で起動した reviewer の停止は adapter の `cancel` に依存する。F は `cancel` の呼出しを有限にするだけで、その効果は観測結果として記録するにとどまる。

### 4.3 終端と精算の state 容量（設計レビュー round 1 の High 4）

決定: F の予約は、その dispatch の**終端と精算を書く bytes** も、spawn の前に E0 の機構で確保する。E の改訂設計は、書込み種別ごとの最大増分 Δ を最大形の encode で測って固定し、終端していない item ごとに「残りの段の Δ の和」を state から導出し、全 writer で `state_capacity_verdict(base, proposed, encoded_len)` 1 つで `len(encoded) + Σ残り予約 ≤ STATE_LIMIT − S_sys` を検査する。[D05]

- **開いている予約を E0 の「終端していない item」として扱う。** 予約 1 件の残り予約 = `Δ_settle + Δ_terminal(entry)`。
  - `Δ_settle`: F の精算行（`Settlement`・`PhaseCharge` の更新・`ProgressSignature` の新規行を含む最大形）
  - `Δ_terminal(entry)`: B の verification receipt（#1）、provider の terminal 更新（#3）、承認 verifier の receipt / force の `force_approval`（#5・#6）は F が新しく予約する。D/E の入口（#2・#7・#8）は E0 が既に D request・repair attempt・disposition の段として予約しているので 0 とし、二重に予約しない
- **予約 commit が容量の検査点になる。** 予約行自身の Δ と上の残り予約を足して `state_capacity_verdict` が通らなければ、`state-capacity-exhausted` で拒否し、起動しない。4 MiB [S28] の近くで予約が通った場合も、終端と精算の bytes は既に確保されているので、終端の commit は検査を通る（通らなければ Δ の定数の欠陥で、E0 の `state-capacity-invariant-broken`）。
- **予約を持たない B receipt の扱いを変える**: E の改訂設計は B receipt を予約された終端として扱わず、公開時に超えたら拒否する。[D05] F の policy を持つ session ではこれを置き換え、B receipt は予約に含まれる終端となる。policy の無い session では E の規則のまま。
- **停止系の書込み**: `StopSlots`（拒否の計数、final latch、exhaustion、`BudgetStop`）は policy の受付時に確保する固定長 slot で、以後は上書きだけ（増分 0）。`budget stop` の書込みは「halt の field と F の slot の上書きだけ」になるので、E0 が S_sys を使える停止系と判定する対象に含めてもらう（E0 への要求）。拒否の記録が容量で失敗した場合も起動しない。
- **所有**: Δ の表・最大形の encode test・`state_capacity_verdict`・S_sys・全 writer の検査は E0 が持つ。F は自分の書込み種別（予約行・精算行・slot・B receipt と provider terminal と承認 receipt の予約）の Δ 定数と最大形 test を、E0 の拡張点へ足す（F1 で型と Δ、F2a で入口の配線）。F は E0 の予約量を変えず、時間の予約は F、bytes の機構は E0 という分担を保つ。

### 4.4 修復が最終検証の時間を使わないこと（設計レビュー round 1 の High 2）

決定: **保証を「mission の spawn 入口（§3.2 #1〜#11）から起動した作業は final の予約を使えない」に狭め、host の inline 作業（host が起動する subagent を含む。§3.2 #16）は強制の外とし、量を記録する。** 選んだ理由: mission は host の inline 編集・実行と host の subagent を観測も中断もできない。repair の締切以降に state の mutation を拒否しても、ファイルの編集は止まらず、記録だけが失われる。止められないものを止めると称するより、止められるもの（spawn）を確実に止め、止められないものを数えて出すほうが正確である。

強制できる境界は次のとおり。

- repair 系の入口の `settle_by` は repair の締切を越えない（§3.1）。repair の締切以降、repair 系の dispatch は child の段階で既に回収されている。
- `at ≥ repair の締切` で final latch が導出され、repair 系の入口と `repair begin` は `budget-final-latched` で拒否される（§3.1）。`next` の助言に依存しない。
- `repair begin` は、B の最小実行が repair 枠に収まらない時点では拒否する（§3.2 #10）。時間の予約はしない（attempt 自体は process を起動しないため）。

強制の外にあるもの: repair の締切の後も host が inline で修正を続けた時間。これは final 枠を実質的に削る（overall_deadline までの残りが減る）。F はこの量を `reserve_erosion_sec`（final latch の後、または非保護枠の締切の後に、final 系の予約が開いていない稼働時間の合計）として status と停止記録に出す。final verification は残りが `min_dispatch_sec + cleanup_sec + commit_margin_sec` に満たなければ拒否され、session は exhaustion から partial-done で終わる。成功は作らない。

受入条件もこれに合わせて書く（§6.3）: 「repair の dispatch は final 予約の時間帯に実行されない」「inline の侵食は `reserve_erosion_sec` に現れ、その結果 final が実行できない場合は pass にならない」。旧版の「repair 枠が残ること」を inline 作業まで含めて保証する書き方はしない。

## 5. Exhaustion、停止、完了 gate（設計レビュー round 1 の High 5）

- **exhaustion は kernel が導出する一方向の状態とする。** `exhausted(ledger, at) = at ≥ overall_deadline − closeout_margin_sec かつ final 系の予約が開いていない、または at ≥ overall_deadline`。予算に触れる mutation（予約・精算・拒否の記録・`budget stop`・`mark-passes`・Reactivate）は、`at` で exhaustion が成立していれば `Exhaustion` slot に時刻と原因を書く。一度書いた slot は戻らない。slot が未記録でも、guard は `at` から同じ判定をするので、記録の有無で結論が変わらない。
- **exhaustion 後に許すもの**: 開いた予約の精算と reconcile（§3.2 #9・#11）、`budget stop`、`mark-halt`（任意の category）、read-only の query。**拒否するもの**: 新しい予約（全入口）、`repair begin`、`budget enter-final`、Reactivate、`mark-passes`（force を含む）。
- **完了 guard を全ての予算付き session に適用する。** kernel の `_mark_pass` に、`_acceptance_completion_ready`（contract が無いと何もせず戻る [S36]）とは別の guard `_budget_completion_ready` を足し、ledger を持つ全 session で評価する。条件は (1) 開いている予約がある → `budget-dispatch-unsettled`、(2) exhaustion が成立 → `budget-exhausted`。評価の位置は `_mark_pass` の先頭、force の分岐（transitions.py:1075）より前とする。[S36] application の preflight でも、contract の有無の分岐（review.py:516）の外で、force の承認 verifier の spawn（review.py:523）より前に同じ純関数を呼ぶ。[S35] これで、予算切れの session は force でも pass に到達せず、承認 verifier も起動しない。`passes` を書くのは `_mark_pass` だけなので [S46]、他の経路は無い。
- **予算停止**: `budget stop` は、exhaustion が成立しているか、kernel が ledger から「必要な次の dispatch がどれも予約できない」ことを導出できる場合だけ受け付け、同じ transition で `MarkHalt(category=partial-done)` と `BudgetStop{scope, reason_code, at, ledger_digest, open_reservations, reserve_erosion_sec}` を保存する。[S26][S27] 予算が残っているのに使うことは `budget-not-exhausted` で拒否する。既存の `mark-halt --category partial-done` はそのまま使えるが `BudgetStop` は残さない（I が「予算による停止」と「他の理由の partial-done」を区別できるようにするため）。新しい HaltCategory は足さない。
- **`next` は助言のまま**: 非保護枠の締切を過ぎたら spawn 系の action を「final へ移る」助言へ、exhaustion なら `budget stop` の助言へ変える。terminal・await-user・安価な確定手を差し替えない既存の規則は保つ。[S03][T01] 強制は上の guard と admission が持ち、`next` を読まない host でも結論は同じになる。
- **予算は完了を許可しない。** 既存の gate は一切緩めない。理由コードの優先順は、D の既定の後ろに `budget-dispatch-unsettled`、`budget-exhausted` の順で置く。

## 6. 変更面、保持する保証、検証方針

### 6.1 変更面

| 層 | 追加・変更 |
|---|---|
| kernel | 新 module（policy/ledger の型・decoder・`admit`・`budget_class`・`cleanup_sec` の表・exhaustion の導出・reducer・`BUDGET_SPAWN_ENTRIES`）。commands union と transition registry に `ReserveDispatchBudget`・`SettleDispatchBudget`・`EnterFinalPhase`・`BudgetStop`。lifecycle の reducer（MarkHalt・MarkPass・Reactivate・ResumeStale）に ledger がある場合だけ時計の区間の開閉と exhaustion の拒否。`_mark_pass` の予算 guard。generic set の保護集合の拡張 [S07]。E0 の種別表への F の Δ |
| codec | v4 の `budget_ledger` と v5 の `extensions.budget_ledger` の閉じた復元と、保存面との一致検査 |
| application | `init --budget-policy` の受付（coverage の検査付き）、`budget status/enter-final/stop/reconcile` の use case。§3.2 の各入口での予約・精算。監督 process（B）、adapter の呼出し子、provider の process group 回収と有限の待ち。mark-pass の preflight の予算 guard |
| CLI | parser と 1 use case 呼出しだけ。thin-adapter の baseline を増やさない |
| inventory | `command_owners.py` に mutation を A1.lifecycle（`budget stop`・`budget enter-final`）と A3.evidence（`budget reconcile`）へ、`budget status` を R1.query へ登録する [S29]。spawn 箇所の inventory test（§3.5）。guidance parity の入力に `$.budget_ledger` [S06]。配布 mirror の同期 |
| 他の設計への要求 | D2c: envelope に `deadline_at`、全 adapter 呼出しを 1 つの seam から（判断事項 3）。E0: F の書込み種別を Δ の表へ、F の slot の上書きを停止系の判定へ（§4.3）。I/G: Mission arm へ policy と `external_deadline_at` を渡す（判断事項 10） |

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

- TDD で、現実的な故障を先に Red にする: 初回の dispatch が repair・final の予約を食い尽くす、retry の二重計上と欠落、crash 後の予約の 0 計上、halt 中の時間の計上、時計の後退、並行予約の二重配賦、締切後も child が残る（孫 process を含む）、provider の回収が終わらない、adapter の `collect` と `cancel` が戻らない、B の監督 process の読取りが戻らない、4 MiB の近くで予約した dispatch の終端が書けない、`budget stop` が予算の残る session で通る、開いている予約がある状態での mark-passes（**契約の無い session を含む**）、exhaustion 後の force pass、`--phase` を偽った provider が final へ計上される、`pending` の入口が残る状態での `init --budget-policy`、表に無い spawn 箇所の追加。
- 受入（§4.4 の狭めた保証に合わせる）: 制御された時計の fixture で、(a) mission の spawn 入口から起動した repair 系の dispatch は final の予約の時間帯に実行されず、`settle_by ≤ repair の締切` であること、(b) inline の時間を模した稼働時間が `reserve_erosion_sec` に現れ、final が実行できない場合は exhaustion から partial-done になり pass にならないこと、(c) 外部締切が稼働時計より早い場合に外部締切で exhaustion になること。
- 変異（検出できることを確かめる対象）: 保護枠の差し引きを外す、`<` と `≤` の取り違え、repair の締切から `final_unspent` を外す、開いている予約を unspent から外す、`cleanup_sec` の項を 1 つ落とす、`charged-full-unknown` を 0 にする、精算を 2 回適用する、子の締切を `policy_timeout` だけにする、並行 dispatch を稼働時計へ足す、kill を直下の child だけにする、provider の締切を起動後の相対時間に戻す、時計の後退を受け付ける、no-progress の候補比較を外す、予算 guard を contract 付き session だけにする、`budget_class` に `--phase` を使う、`Δ_terminal` を 0 にする、`total_sec` を切り上げる。各変異で少なくとも 1 件のテストが落ちること、正常系が通ることの両方を固定する。
- 抜け穴探索: `admit`・`budget_class`・exhaustion の導出・policy decoder・ledger decoder に対し、正常 / 拒否を合わせて 50 入力以上を独立に作って通す（境界の秒、bool を int として渡す、配分の合計が 9999/10001、未知 field、null、重複 ID、上限超過の開いた予約、逆順の時刻、締切ちょうど、`min_dispatch_sec` ちょうど、latch 後の repair、同時上限ちょうど、候補だけ違う無進捗、`budget_minutes` が 0.01/1.1/大きな値、外部締切が過去・null・稼働時計より後）。件数と発見数を PR 本文に書く。本設計では実施していない。
- 公開 CLI の end-to-end（fixture の verifier・provider・adapter）: 予約→実行→精算、締切での回収、crash 後の精算、予算停止、legacy の不変。
- 新規テストの費用は **UNKNOWN**。実装後に対象実行で計測する。CI は shard 経由で tracked tests を選ぶ既存経路を使う。

## 7. Reviewed lines と分割

全体の見積（追加+削除、配布 mirror 除外、未実測）。過去の設計見積が実測の約 1/1.6 だったため、素の見積と ×1.6 を併記する。repo の閾値は 600 行で説明、1,400 行で分割必須。[AGENTS.md:119-128][S34] 設計レビュー round 1 の決定（init 時だけの受付と coverage の検査、監督 process と adapter の呼出し子、承認 verifier 2 入口、容量の予約、全 session の完了 guard と exhaustion）で範囲が増え、3 分割では F2b が 1,400 を越える見込みになったため 4 分割にする（判断事項 7 の改訂）。

| PR | 範囲 | 依存 | 素の見積 | ×1.6 |
|---|---|---|---:|---:|
| F1: 予算の kernel | policy/ledger の型・decoder・codec、generic set 保護、時計の区間と外部締切、`admit`・`budget_class`・`cleanup_sec` の表・exhaustion の導出、全 reducer、`_mark_pass` の予算 guard（純関数）、`StopSlots`、F の Δ 定数と最大形 test（E0 の拡張点）、`BUDGET_SPAWN_ENTRIES`（全て `pending`）、`budget status`。policy を作る経路が無いので inert | E0 | 650〜800 | 1,040〜1,280 |
| F2a: 既存入口の admission と bounded kill | #1・#3・#4・#5・#6 の予約と精算、B の監督 process、provider の絶対締切・process group・有限の待ち、strict の拒否、crash 後の精算と `budget reconcile`、mark-pass の preflight、no-progress、spawn 箇所の inventory test（既存入口を `covered`、D/E を `pending`） | F1 | 600〜750 | 960〜1,200 |
| F2b: D/E 入口の admission | #2・#7〜#10 の予約と精算、adapter の呼出し子と `cancel` の順序、`repair begin` の検査、表の D/E を `covered` | F2a、D2c・E2・E3 が main にあること | 450〜600 | 720〜960 |
| F2c: 有効化と停止（Closes #881） | `init --budget-policy`（coverage の検査付き、`pending` 0 を要求）、`budget enter-final`・`budget stop`、exhaustion 後の Reactivate 拒否、`next` の差し替え、end-to-end と受入 fixture | F2b | 450〜600 | 720〜960 |

合計 2,150〜2,750 行（×1.6 で 3,440〜4,400 行）。旧版は 1,650〜2,000 行。各 PR は 600 行を越えうるので、PR 本文に分割しない理由（F1 は型・decoder・reducer・codec を同時に成立させないと保存面の検査が空回りする、F2a は予約と回収を同じ入口で同時に入れないと予約だけの中間状態ができる）を書く。行数は実 diff を `scripts/pr_size.py` で再計測する。**有効化は F2c だけに置くので、F1〜F2b の中間状態はどれも policy を持つ session を作れない**（§3.5）。

## 8. 判断が必要な事項と対象外

### 判断事項（orchestrator / owner）

1. **非保護 3 phase を合算で強制するか。** 推奨は合算（§2.3）。
2. **halt 中の時間を数えるか。** 推奨は数えない（§2.2）。外部の壁時計上限との整合は `external_deadline_at` で取る。
3. **D2c への要求。** `deadline_at` を envelope の閉じた集合へ今の段階で含めること、全 adapter 呼出しを 1 つの application の seam から行うこと（F2b がそこを締切付きの子で包むため）。
4. **strict 隔離の provider。** 推奨は予算付き session で拒否（§4）。
5. **final latch を一方向にするか。** 推奨は一方向（§3.1）。
6. **`gate-and-merge` を対象外にしてよいか。** 推奨は対象外。
7. **分割（改訂）**。F1 → F2a → F2b → F2c の 4 分割。旧決定の 3 分割から F2b を「D/E 入口」と「有効化と停止」に分ける。
8. **予算の延長**。F では作らない。exhaustion 後の Reactivate は拒否する。
9. **予算 policy を必須にするか**。F は policy の無い session を `advisory-only` で通す。J の verified-complex profile で必須にするかは J で決める（旧決定 9 で必須と決定済み）。
10. **（新規）I/G への要求**。Mission arm の harness が `init --budget-policy` と `external_deadline_at = 起動時刻 + T − 後処理の余白` を渡すこと、余白の値、Mission arm が halt 後に reactivate するか。I の事前登録の凍結項目に含めるかは I で決める。
11. **（新規）承認 verifier を予約の対象にすること**。推奨は対象にする（§3.2 #5・#6）。予約のために verify-approval の lock 区間の前へ短い transaction を 1 つ足す。
12. **（新規）保証を狭めること**。§4.4 の「mission の spawn 入口から起動した作業だけが final 予約を使えない。host の inline 作業と host が起動する subagent は強制の外で、侵食量を記録する」を、#881 の受入条件の読み方として採るか。Issue 本文は「reserveはguidanceだけでなくrunner/provider/retryの全dispatch入口で強制する」「初回phaseが修復/最終検証の予約枠を消費し切らない」と書いている。前者は mission の spawn 入口として満たせるが、後者の「初回phase」の主な消費者である host の planner・executor の subagent（§3.2 #16）は mission から止められない。採る場合は、Issue 本文の完了条件を「mission が起動する dispatch は予約枠を消費し切らない。host 側の消費は `reserve_erosion_sec` で可視化し、final が実行できなければ pass にならない」へ更新する必要がある（Issue の更新は orchestrator が行う）。

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

[S*][T*] のリンクは全て `d25a66c6abed33a4c0fbd1036e22bdf49da3c606`。各ラベルの file:line はこの head で読んだ範囲。テストの引用はコードの保証を指し、本ステップで実行した結果を指さない。[D05][D06] は各 branch の commit に固定する。

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
[S22]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/bin/mission-state.py#L10390-L10433 "mission-state.py:10390-10403 _stop_approval_verifier_child（SIGTERM/SIGKILL と各 0.2 秒）、10406-10433 _run_approval_verifier（fork、5 秒 join、finally の再回収）; 10089-10090 定数"
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
[T01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue238_budget_pressure.py#L41-L140 "test_issue238_budget_pressure.py:41-140 — 9 件"
[T02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue878_candidate_snapshot.py#L74-L74 "test_issue878_candidate_snapshot.py:74 — runner の timeout"
[T03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue878_verification_runner.py#L327-L342 "test_issue878_verification_runner.py:327-342 — 同じ operation の再実行"
[T04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue895_fresh_review.py#L90-L194 "test_issue895_fresh_review.py:90, 194 — request の予算の閉じた検査"
[T05]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/skills/mission/tests/test_issue742_stop_guard_timeout.py#L368-L393 "test_issue742_stop_guard_timeout.py:368-393 — stop guard の予算"
[D01]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/689-fresh-review-receipt.md#L66-L71 "docs/design/689-fresh-review-receipt.md:66-71 — request の予算上限、金額予算と repair 予約は後続へ; 181-186 coverage を contract へ書かない理由"
[D02]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/689-fresh-review-receipt.md#L211-L238 "docs/design/689-fresh-review-receipt.md:211-238 — terminal variant（budget_used・cancel_result）と dispatch saga（D2c 以降は未 merge）"
[D03]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L48-L59 "docs/design/880-repair-lineage.md:48-59 — E の旧版の容量予約と公開の回復; 25 保存方式（改訂版は D05）"
[D04]: https://github.com/tackeyy/mission/blob/d25a66c6abed33a4c0fbd1036e22bdf49da3c606/docs/design/880-repair-lineage.md#L155-L166 "docs/design/880-repair-lineage.md:155-166 — repair の公開 command（未 merge）; 112 再検証の intent→実行→公開"
[D05]: https://github.com/tackeyy/mission/blob/9e99f068199373045b1383d530c65caf9838ff4a/docs/design/880-repair-lineage.md#L54-L69 "docs/design/880-repair-lineage.md（docs/880-e2e3-decisions）:54-69 — 容量予約の再設計（56 書込み種別と Δ、61 state から導出する予約、62 受付時の全段確保、63 検査式と S_sys、65 state_capacity_verdict と全 writer、66 予約を持たない B receipt、68 E と F の境界）; 400 E0 の順序の決定"
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

#### round 1 後の改訂（designer の提案。orchestrator の確認待ち）

- 決定 2 に追加: halt を数えない稼働時計に加え、`external_deadline_at` で外部の壁時計上限に揃える（§2.2）。I の T は harness が強制し、F は置き換えない。
- 決定 3 に追加: D2c は全 adapter 呼出しを 1 つの seam から行う（§4.2）。
- 決定 7 を置き換え: F1 → F2a → F2b → F2c の 4 PR（§7）。F1 は E0 の merge 後、F2b は D2c・E2・E3 が main に揃った後。有効化（`init --budget-policy`）は F2c だけに置く。
- 新規 10〜12（§8）: I/G への要求、承認 verifier の予約、保証の狭め（host の inline 作業と host が起動する subagent は強制の外）。12 は #881 の完了条件「初回phaseが修復/最終検証の予約枠を消費し切らない」の読み方を変えるので owner の確認が要る。
- 予算 policy の無い session は `advisory-only` のまま通す（旧決定 9 の J 必須は維持。F 自身は policy を必須にしない）。

### 決定（orchestrator / owner, 2026-10-04。設計レビュー round 1 の後）

- 10（I/G との結合、orchestrator 決定）: 外部評価の harness が Mission arm に budget policy と `external_deadline_at` を渡す。後処理の余白の値は smoke の実測後に I の事前登録で固定する。halt 後の reactivate は評価中は行わない。
- 11（承認 verifier の予約、orchestrator 決定）: 予約の対象にする。
- 12（保証の範囲、owner 決定）: 保証を「予算 policy を持つ session で、mission の spawn 入口から起動した作業は final の予約を使えない」に狭める。host が起動する subagent と host の inline 作業の時間は強制の外とし、`reserve_erosion_sec` として記録し、exhaustion 後は force を含めて pass に到達させない。Issue #881 の完了条件をこの範囲に合わせて更新する。
- 決定 7 の置き換え: F は F1 → F2a → F2b → F2c の 4 PR とする（各 PR は較正済み見積りで 1,400 行未満）。
