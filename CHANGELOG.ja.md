# 変更履歴

**日本語** | [English](CHANGELOG.md)

本プロジェクトの主要な変更を記録します。

フォーマットは [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) に準拠し、
バージョニングは [Semantic Versioning](https://semver.org/lang/ja/) に従います。

## [Unreleased]

## [2.7.0] - 2026-08-18

Wave 3 では Typed Mission Kernel と UnitOfWork の基盤を導入しつつ、新規 session は schema v4 のまま維持します。versioned aggregate / decoder、private staging と fenced CAS、crash recovery、generation GC、統合 transition table、そしてその上に載る repository / authoritative reader の結合を段階的に入れました。

- feat: authoritative state consumer を versioned reader へ移行し、読取側が段階的な state format 変化に追従できるようにしました。live の v4 session cutover は変えていません (#541)。

- docs: P1 と C1 の migration boundary を同期し、文書上の handoff を現在の実装分割に合わせました (#540)。

- feat: repository を format-pinned storage と v5 UnitOfWork contract に束縛し、既存 session の互換を保ったまま段階移行の土台を入れました (#538)。

- refactor: runtime guard の observation writer を独立した境界へ抽出し、観測出力を guard の周辺処理から分離しました (#537)。

- refactor: artifact / progress / context evidence を専用 use case へ抽出し、これらの evidence 経路を高位の orchestration から切り分けました (#536)。

- refactor: plan / handoff / provider evidence を authority を移さずに専用 use case へ抽出しました (#535)。

- refactor: review / score / pass の authority boundary を抽出し、decision gate を明示的で追いやすい形にしました (#534)。

- refactor: lifecycle use case を v4 互換 repository boundary へ抽出し、内部構造を進めながら既存 session の可読性を保ちました (#532)。

- test: reference 保護の物理確認と error boundary への例外変換を追加し、failure boundary を観測可能にしました (#533)。

- feat: reference-safe な generation garbage collection を追加し、古い generation を削除しても reference 保護を弱めないようにしました (#531)。

- test: pregate テストを clock injection で日付非依存にし、release 検証がカレンダーに左右されないようにしました (#530)。

- feat: UnitOfWork の deterministic な crash recovery を追加し、中断された write が部分 state を残さず再開できるようにしました (#527)。

- refactor: transition と next の処理を単一の transition table に統合し、重複した遷移ロジックを減らしました (#526)。

- docs: ADR-005 §3 / §5 を K2 の guidance authority 判断に合わせて改訂し、文書上の decision boundary を実装と揃えました (#525)。

- docs: K2 の guidance authority 判断を transition layer で確定し、実装契約を明確にしました (#522)。

- feat: fenced generation CAS と immutable commit/head record を追加し、同時 generation 更新で shared state が壊れないようにしました (#524)。

- feat: UnitOfWork の private staging と immutable generation 公開を追加し、state を見える前に準備できるようにしました (#521)。

- feat: versioned MissionState aggregate と v1-v4 decoder を導入し、段階移行中も legacy session を読み続けられるようにしました (#520)。

- docs(design): versioned aggregate と decoder 経路の K1 実装設計を確定しました (#519)。

- test: scaling 検証を load-independent な計測へ変更し、テスト環境ではなく system の挙動を測るようにしました (#518)。

## [2.6.0] - 2026-08-14

- test: schema v1-v4 の golden snapshot を既知 version まで揃え、未知 schema version は validation で fail-closed にしました (#483)。

- docs: state-management と ADR-002 を、初期ドラフトではなく実装に沿った記述へ更新しました (#484)。

- docs: ADR-005 に Typed Kernel / UnitOfWork への移行経路と移行計画を記述しました (#485)。

- fix: `plan-import` と `adopt-core` が lease validation 完了前に evidence を公開しないようにし、早出しの窓を塞ぎました (#498)。

- chore: distribution mirror と Python compatibility gate を package tree 再帰探索にし、Wave 3 / D1 の期待に合わせました (#499)。

## [2.5.0] - 2026-08-14

- feat: policy v1 の core planning が canonical `mission-plan/1` を検証・登録し、既存の execution gate を通過して `next` と失敗ガイダンスを受け取れるようにしました (#465)。

- fix: `planning adopt-core` のガイダンスで、inline / planner の両経路とも実行前に `planning adopt-core` を要求するようにし、不正な iteration や planning 以外の phase は state を変更せず拒否するようにしました (#469)。

- fix: `iteration=0` の初期化直後セッションでも `planning adopt-core` が受理されるようにし、実際の `init` state をすべて拒否していた回帰を修正しました (#471)。

- fix: publish transaction の失敗時に比較値と不一致だった項目を出力するようにし、拒否された publish の診断を明確にしました (#468)。

- test: FIFO artifact テストで timeout 失敗を独立した観測結果として分類できるようにし、timeout exit と他の失敗を切り分けられるようにしました (#480)。

- docs: ADR-003 で review tier の境界契約と、その境界を較正する FP/FN コーパスを定義しました (#481)。

- fix: artifact 系コマンドが lease 検証前にファイルを公開しないようにし、`init` / `render` / `export` / `publish` で artifact が早出しされる窓を塞ぎました (#475)。

- fix: review tier のキーワード判定に lexical boundary を導入し、部分一致で tier が誤昇格しないようにしました (#482)。

- fix: `MISSION_CLI_VERSION` と配布 version を単一の管理元にまとめ、CLI 定数と公開パッケージ version の不一致を解消しました (#479)。

- fix: evidence completion の成功 terminal を `next_action` に記録するようにし、evidence 作業完了時に `report-terminal` が出るようにしました (#477)。

- fix: specialist evidence を invocation ID + content digest で一意参照するようにし、各 invocation に content-addressed な識別子を持たせました (#478)。

- fix: Stop hook の freshness 判定を 1 つの Python 実装へ集約し、経路ごとの差をなくしました (#476)。

## [2.4.0] - 2026-08-14

- feat: `stats` と `audit` に `iteration_recovery` 集計を追加し、recovery 件数を #461 として要約できるようにしました。

- fix: learning brief が worktree の main root に自動 fallback するようにし、明示上書き用の `--root` オプションを追加しました (#463)。

- test: `iteration_recovery` 周辺の境界値テストと意図コメント / docstring を強化しました (#462)。

## [2.3.0] - 2026-08-13

- review import/finalize、transition gate、command provider に bounded な command outcome telemetry を追加した。`ok` / `expected-gate` / `invalid-input` / `external` / `internal-error` を opaque な retry lineage とともに記録し、拒否 gate では state bytes を変更せず、`stats` と audit で root-event/retry/corrupt 件数を観測する (#386)。

- 採点項目を4軸に統一し、reviewer の合意度を独立して記録するようにしました。手動採点の取り込みには typed かつ content-addressed な専用経路を用意し、全スコアを bool ではない有限の範囲内数値、open High 件数を bool ではない 0 以上の整数として検証します。audit と stats で採点 provenance を確認できます (#383)。

- review aggregate は write / pass 境界で archive `inputs` から全 gate 値を再導出し、claim 欠落・自己整合だが偽の claim を拒否するようにした。force approval は versioned terminal-state projection に bind し、audit は state のコピー・改ざんを検出する。approval verifier の trust root は user-only `$XDG_CONFIG_HOME/mission/approval-verifiers.json` の `mission-approval-verifier-registry/2` とし、唯一の登録手順で `entry_point`、`distribution`、`version`、`source_digest` を pin する。parent/child が同じ pin を再照合し、load と callback を reaped child process 内で時間制限付き実行する (#383)。

- schema marker の欠落・改ざんにかかわらず新規 score 書込みに content-addressed な不変 scoring/review provenance を必須化し、成功した書込みだけが state を schema v4 へ上げます。既存 terminal state は破壊的変更せず read-only の legacy として扱います。force pass は boolean 宣言ではなく、canonical request/response/receipt と consumed marker を返す host 登録 verifier callback を要求し、audit も同じ envelope を検証してから verified と分類します (#383)。

### 追加

- learning brief が failure_ledger の general_fix_rule を sessions と archive の terminal state から横断集計し、read-only の `learning brief` コマンドで再発回数降順の guidance を返すようになった。SKILL の規律で planner / executor brief へ注入し、reviewer の独立性は維持する (#457)。
- planning-provider の KPI conformance、structured plan contract の validation/import、lifecycle handoff、provider preflight/application の hardening により、plan evidence・provider identity・invocation packet を fail-closed に保つようにしました (#417, #418, #415, #414, #413)。
- host execution correlation、review-generation lineage、immutable audit snapshot、benchmark calibration、specialist checkpoint、review-import の structured outcome、resume 文書化をまとめて反映しました (#410, #409, #408, #407, #406, #405, #404)。
- Pre-Gate 評価キャッシュで `pregate record/check` を記録し、`issue_ref` をキーに `subject_digest` の hit / miss / stale / fail-safe を追跡して、`init` の参照記録を安定化しました (#430)。
- pregate digest コマンドと事前充填レシピで、次回チェック前に digest を先読みしてキャッシュを温めるようにしました (#437)。
- accepted 以外の pregate verdict では `init` と `next` に警告を出し、続行前に不一致が見えるようにしました (#439)。
- checker evidence handoff はローカルの `publish` / `await` / `verify` に切り替え、atomic write・digest 照合・timeout exit 3 で GitHub コメントポーリングを不要にしました (#427)。
- 並列 mission 向け merge queue は `enqueue` / `status` / `next` / `verify` / `mark` を備え、base 移動時は invalidated にして refreeze を促すようにしました (#431)。
- `queue enqueue --from-state` で `revision_scope` を自動突合し、後続 queue entry が元 state とずれないようにしました (#440)。
- lane-report は `session_role` ごとの wall-clock / 実働 / 待ちを分けて SLO 判定し、`root_run_id` 単位で grouping して bench 回収と集計に渡すようにしました (#429, #441, #438)。
- invalid-input と expected-gate 失敗には、正しい呼び出し例・`--json` guidance 配列・telemetry マーキングを含む自己修復 HINT を返すようにしました (#428)。
- 小さめの release-window follow-up も traceability のためにここへ含めています: #432, #433, #434, #435, #436, #444, #446, #447, #448, #449, #450, #454。
- リポジトリ管理の `make test-smoke` / `make test` / `make test-e2e` がCIと同じ入口を使い、必要な場合はpinned test環境を作成して exact tree SHA と test manifest を出力するようにした (#391)。
- state archive compaction が canonical / superseded relation を immutable content-addressed generation へ記録し、physical lineage を削除せず維持するようにした。audit は既定で materialized canonical record のみを読み、`--forensic` で full lineage を再現する (#391)。
- Stop hookのblock出力がunfinished session set・phase・leaseのdigestを記録し、変化時またはheartbeat TTL経過時だけ詳細を再表示するようにした。同一状態ではblocker categoryと次の1 commandだけへ圧縮し、fenced session本体を変更せずproject-localな`mission-stop-guard/1` stateへblock/reinjection/detail/heartbeat countersをatomic保存する (#389)。
- 並列 mission がversioned planned-child manifestを作成し、各childを planned / running / waiting / pass / halt に分類できるようにした。全planned childがterminalかつlease解放済みの場合だけgroupをcloseし、artifact・activity・review provenanceの実観測coverageを保存する。重複・manifest外child・malformed・unsafe manifestは拒否時のstateを書き換えずfail-closedとする (#388)。
- specialist selection / invocation に provider-neutral な lifecycle 契約を追加した。新規 session は明示 checkpoint から開始し、recommendation record は同じ opaque `selection_id`、invocation record は started から terminal まで一意な `invocation_id` を使う。pending selection、または一致する terminal evidence のない selected provider は `--specialist-waiver` で理由を明示しない限り `mark-passes` が拒否する。legacy の checkpoint 欠落は物理 rewrite せず `missing-legacy` として別集計する (#387)。
- planning provider registry の選択が、未知 complexity、不正な version 2 契約、provider-id tombstone のずれ、hardlink/symlink を含む同一物理 input、変更中・非 regular・oversize の registry snapshot、非 portable な command 実行設定を fail-closed に扱うようになった。JSON/YAML number は bounded strict parser を共有し、不正 numeric input は上位 valid input を全体 abort せず通常の precedence barrier として扱う。nested flow collection も共通の depth contract で拒否する。command timeout は範囲内の exact integer のみ、v1 source は discovery 所有値のみとし、FD read/metadata error は structured diagnostic にする。不可逆な external locator には `explicit-resupply-required` を記録する。#395 で current registry の runtime 再検証が入るまで、command provider は bare PATH command、空 args/env、explicit result contract なしの場合だけ一時的に選択可能とする。recursive public record allowlist を全 specialist surface の出力・atomic state write 前に適用し、private path、raw nested provider config、unsafe legacy state、POSIX/Windows evidence path が stdout、backup、archive、audit snapshot、invocation evidence へ漏れることも防止する。invocation の pending entry・selection metadata・iteration/timeout・予定 evidence locator は state 変更、process spawn、archive 公開より前に検証し、staged archive は state 公開失敗時に rollback する。evidence input は単一 FD で bounded・stable・non-symlink regular file として読み、separator 有無を問わない Windows network/device/drive-relative とbare/path付きhome locatorを含む共通path sanitizerを適用する。bare drive label、埋込みの一般tilde文字列、通常のmulti-letter colon token、strict allowlistに合致するHTTP(S) URLは保持する。URL保護はASCII DNS/IPv4/bracketed IPv6 authority、任意のbounded numeric port、valid percent escape、userinfoなしに限定する。path/query/fragmentもraw RFC 3986 component grammarで検証し、Unicode componentは明示的に保持する。malformed componentはfail-closedにし、全ての`file:`形式をlocal locatorとして扱う。区切り文字後の local path も拒否・redact し、candidate score は overflow しない 0..1 検証に限定する (#394)。
- activity 計測を planning の自動開始から全非終端 phase の portable default まで連動させ、`advance --phase` は明示 override がなければ既定 activity を使うようにした。review 集計は scoring の計測を atomic に開始し、terminal writer は open segment を閉じ、recovery は bounded な unobserved-gap reason を記録する。audit は elapsed conservation を明示し、coverage 70% 未満では slow-run finding より instrumentation-gap を優先する (#382)。

- artifact production を end-to-end の portable contract にした。init は applicability を `pending` として記録し、executor handoff が review 前に解消する。nested `artifact` identity は repository-relative path、SHA-256 digest、byte size、producer run id を保持する。aggregate review と pass marking は bounded regular non-symlink validator を共有し、mutation / substitution を reject する。stats と audit は terminal outcome・profile 別 coverage を、eligible / observed / missing / invalid と clean / findings の conservation 付きで報告する。明示 not-applicable は clean ではなく skipped とし、profile coverage 95% 未満は WARN-only、到達後は現行観測 gate を有効化する。top-level `artifact_path` は legacy の read-only fallback として維持する (#381)。
- session state schema v3 に、state writer・`stats`・audit で共有する role-aware な `terminal_outcome` taxonomy を追加した。implementer pass rate は implementer の `completed_pass`・`failed`・`incomplete` のみを比較し、checker/planning/analyze の evidence completion を分離して報告する。`routed-goal` は中立な `routed_elsewhere` outcome とし、active/release/non-comparative record が `low-pass-rate` を歪めない。legacy v1/v2 record は書き換えず読み取り時に導出し、明示 outcome と control state の矛盾は fail-closed、31 件の匿名化 evidence fixture は actionable halt を増やさず conservation を満たす (#380)。
- bounded context の発火条件・fallback・review gate を変えず観測可能にした。`context-manifest` は iteration ごとの path・SHA-256 digest・生成時刻を session state へ記録し、`aggregate-reviews` は期待 context mode と manifest 生成有無を evidence archive へ保存、bounded 期待時の未生成を exit 0 の WARN にする。`stats --json` は期待・生成・full fallback 件数を集計し、bounded review の mission-reviewer は notes に `context: bounded` を明記する。aggregate / stats が manifest 観測を有効と数えるには、iteration が bool・float を除く正の整数で、生成時刻が timezone 付き ISO 形式であることを必須とする。embedded NUL 改ざんを含む不正・読取不能 path は未生成扱いにし、aggregate / stats を中断しない (#352)。
- Simple task の adaptive routing に設定可能な goal dispatch provider を追加した (#355)。portable な `inline` を既定に保ち、mission 内の明示指示、`--goal-dispatch`、project `.mission/routing.yml`、user 設定から `host-native` を選べる。init / set / next verdict は実効 dispatch を記録し、host 不明・設定不正時は WARN して inline へ fail-safe する。routing gate と `--force-mission` の挙動は変更しない。

### CI / Testing

- `pytest-xdist` によってテストスイートを並列化し、今回の release 実行では実測 5.5 倍速になりました (#451)。
- docs/results-only の PR は guard 限定 fast path に乗るようにし、merge 手順も更新内容を明文化しました (#453)。
- Quality timeout を 25 分にし、pregate digest の未検証経路を公開 digest helper でテスト補強しました (#445)。
- 2026-08-13 の SLO 実測結果を記録し、lane-report の bench 収集連携を維持しました (#443)。

### Documentation

- worktree 実行 mission の init 配置規律を `SKILL.md` 参照先として明文化しました (#455)。
- auto-merge と `update-branch` の merge 運用を docs-only fast path の注意書きとあわせて整理しました (#453)。

### 修正

- stop-guard の CWD 解決は hook input を優先するようにし、agent CLI 配下でのテスト誤失敗を解消しました (#442)。
- `review_tier` の導出は「公開」などの複合技術名詞に誤発火しないように較正しました (#452)。
- `archive-worktree` は repo-managed artifact にも対応し、repo-artifact reference、index 非依存検証、staged 診断、symlink 拒否を行うようにしました (#456)。
- state に `artifact_path` がある場合、`aggregate-reviews` が WARN-only の構造 lint を実行するようにした。H1〜H3 の空節と英日 forward-reference のみの stub を検出し、reviewer finding・score・exit status は変更しない。結果は review aggregate evidence と state に記録し、`stats --json` が empty-section / stub-forward-reference / clean の件数を返す。embedded NUL 改ざんや非 regular file を含む path の resolve・relative 判定・read 失敗は `artifact_lint_status=skipped` として fail-open し、過去の lint 観測を削除して stale stats を防ぐ。成功した `clean` 観測とも区別する。空の ATX 見出し、閉じ hash 列、backtick を含む無効な backtick fence info は Markdown 構文に従って解釈する (#351)。
- `aggregate-reviews` は reviewer が 2 名以上の場合、全 perspective の `--reviewer-window` 報告を必須化し、不足 perspective と報告書式を示して exit 2 とするようにした。`review-finalize` もこの fail-closed gate を継承し、集計失敗後に score を push しない。単一 reviewer は対象外のまま、報告済みの直列実行も従来どおり WARN のみ (#350)。
- reviewer 出力境界を品質 gate にせず観測可能にした (#353)。`aggregate-reviews` は入力ごとの `mission-review/1` JSON byte 数とテンプレ外散文の byte 数・比率を計測し、evidence と session 横断 p50/p90 stats に記録する。暫定閾値 20 KB / 0.7 超過は exit 0 の WARN に留め、score・finding・agreement の集計結果は変更しない。
- session ownership の管理元を PID から既定 15 分の fenced lease CAS (`owner_session_id`、random `lease_id`、単調増加 `fencing_epoch`、`lease_expires_at`) へ移した。mutating command は renew し、read-only command は renew しない。期限内 foreign writer と stale token は exit 2、期限切れ takeover は epoch を増加して `lease_history` を追記し、`resume` が takeover を実行する。lease のない legacy state は拒否せず epoch 1 を取得する。`cleanup-stale` は lease 付き state で「期限切れ、かつ期限後の activity heartbeat なし」を優先し、legacy state は従来の PID 規則を維持する (#354)。
  lease 付き state は session ID や PID fallback が一致しても `MISSION_LEASE_ID` の明示を必須とする。Stop hook と `resume` は診断用 PID の生死を lease ownership より優先せず、clock rollback 時の renew も expiry を短縮しない。token なしの legacy 初回取得は atomic publish 成功後にのみ machine-readable な `MISSION_LEASE_CARRIER` を出力し、次の独立 process が state を読まず発行 token を明示的に引き回せるようにした。expired idle lease の Stop hook cleanup は `cleanup-stale --execute` の janitor CAS を再利用し、lock 内で expiry と heartbeat を再検証して、並行 renew/takeover 済みなら owner token を偽装せず halt を拒否する。

- mission audit が owner による明示的な凍結・意図的 close・replacement issue への切替を示す halt reason を非 actionable の `intentional-freeze-switch` として分類するようにした。raw halt count は保持しつつ、運用 state debt 監査での P1 `halted-runs` false positive を減らす (#347)。

## [2.2.0] - 2026-08-02

### 追加

- critic scope 記録を hard gate 化した (#326)。`aggregate-reviews` (内部呼び出しの `review-finalize` も継承) は `iteration >= 2` で state に `critic_has_new_scope` が無い場合 exit 2 とし、判定基準と `set` コマンドをエラーで案内する。disc-v3 で #309 の guidance 層が `next` を呼ばない orchestrator に bypass された実測への対策で、集計側 gate は fail-closed・escape hatch なし。iteration 1 は不変。

- mission-vs-goal ベンチマークに `portfolio` cohort (`tasks.portfolio.json`、8 tasks: Simple 3 / Standard 3 / Complex 2) を追加した。mission 入口の routing 込み実効オーバーヘッドを測定する: Simple は adaptive routing (#276) の goal 契約直行を発火させ、Standard は discriminating fixture の focused サブセットを、Complex は fail-first 監査をそのまま再利用する。自身の answer key と再利用元 (tasks.discriminating.json) の answer key の両方を run clone から隠蔽する。構造・complexity 構成・隠蔽・fixture 実在・marker 発見可能性はテストで強制する。

- 不可逆キーワードエスカレータが "release" の名詞参照を suppress するようになった。直後が数字・版番号・`brief`・`notes`・`mission` のマッチ (例: "Release 6"、"Release brief") は Standard を full tier へ誤昇格させず、`noun-reference-non-operation` として監査記録される。2026-08-01 の実運用監査で文書名・版名由来の FP 36% を実測したための対策。動詞用法 ("release the hotfix") は従来どおりエスカレートする (#313)。

- session に `session_role` (`implementer`/`checker`/`planning`/`analyze`/`release`、`init --role` で指定、既定 `implementer`) を追加し、Checker の正規出口を示す halt カテゴリ `evidence-submitted` と、`summarize_pass_rate_population` の additive な `role_counts` + implementer 限定 pass rate を導入した。2026-08-01 の実運用監査で sessions の 75% が Checker 系役割であり、設計どおりの iter=0 終了が pass-rate 統計を汚染していたための対策。既存フィールドは全 role 対象の意味を維持し、フィールドを持たない旧 state は implementer 扱い (#311)。

### 変更

- `next` が `command_sequence` (現 phase から `closeout` までの happy-path コマンド列) を返すようになり、ゲート失敗 (exit 2) がない限り orchestrator は `next` を再参照せず連続実行してよい。また Standard 複雑度の iteration 1 は mission-planner subagent を起動せず inline 計画 (`plan-inline`) になる (成果物要件は同一) (#339)。根拠は portfolio-v4 実測: mission 19-31 turns vs goal 5、時間比 (6.9-14.5x) > トークン比 (4.0-4.7x) の差分は毎ターンの context 再処理。Complex / full tier / iteration>=2 は従来どおり。ゲート意味論は不変。

- ベンチマークが adaptive routing を抑制せず観測できるようになった (#333)。mission arm プロンプトは routing verdict への追従 (goal 契約成果物 + Evidence に routed 明記) を許容しループを強制せず、record に第一級の `mission_routed` (routed-goal halt、および init 経路 routing による Simple + state 不在完走 — 後者は #261 ガードの invalid 対象から除外) を記録、アーム別 summary に `routed_records` を追加。portfolio cohort の Simple 3 tasks は分単位版 (120 定数参照監査 / 90 SKU 照合 / 43 予約重複検出) に差し替え、routing の入口オーバーヘッドが V1 パリティ帯に収まる現実的なサイズにした。

### 修正

- レビュアー並列実行が検証・追跡可能になった (#338): `aggregate-reviews` は 2 名以上で実行時間帯が未申告なら WARN (portfolio-v4 で Standard 3 run すべて直列・API 時間 ≒ wall 時間を実測)、観測結果 (true/false/unknown) を state の `last_parallel_execution` へ永続化、`stats` が `parallel_review_counts` を集計、`next` は run-reviewers に `parallel_spawn_required` を付与、SKILL 契約は Claude Code での単一メッセージ並列 spawn を必須化 (直列の実測コスト付き)。ゲート意味論は不変。

- ベンチの mission アームが implementer 契約を固定するようになった (#341): プロンプトが停止前に最低 1 回の scored review iteration 完了を要求する (portfolio-v4 の cx-ledger record は checker 挙動の evidence 提出で halt し、ゲート付きループを測っていなかった)。record は halt category から抽出した第一級の `mission_evidence_only` を持ち、アーム別 summary は `evidence_only_records` を集計、該当 record があれば limitations に比較可能性警告を追記する。

- adaptive routing をコマンド層で強制するようにした (#330)。条件充足時は `set complexity=Simple` 自身が routing verdict を実行し、state を `routed-goal` で atomic に halt して goal 契約の guidance を出力する — orchestrator の `next` 消費 (portfolio-v2 実測で発火 1/3) に依存しない。除外条件 (シグナル・`--issue-ref`・`--force-mission`・checker 系 role・user 指定 tier・採点済み) は不変で、#325 の next 層 gate は defense-in-depth として残る。

- adaptive routing が init 後の complexity 確定経路もカバーするようになった (#325)。portfolio-v1 で #276 を素通りしていた「init → `set complexity=Simple`」フローに対し、planning の `next` が `route-to-goal` を返す — 新設の `routed-goal` halt カテゴリ (completed / implementer pass-rate 分母から除外し `routed_count` として別計上) で state を閉じ、goal 契約で直接完遂する。シグナル・`--issue-ref`・`--force-mission` (state に記録)・checker 系 role・user 指定 tier・採点済み mission はループを維持する。

- activity segment のカバレッジが実行エージェントに依存しないようにした (#312)。`next` の command hint が phase 遷移を atomic な `advance --phase --activity` で案内するようになり (従来は segment 非記録の `set phase=` 経路を案内しており、2026-08-01 監査の CC 13% vs Codex 86% の乖離の原因)、さらに `set phase=<非終端>` は open segment が無い場合に phase 相応の segment を fallback で開く — set 時点から開始し過去を塗らず、既存 open segment を置き換えない。

- `cleanup-stale` が checker 系 role (`session_role != implementer`) に live-PID no-score stale 判定を適用しないようになった。設計上 score を書かない Checker session が親プロセスの PID 共有下で一括 stale 化されていた実害 (7/25-27 に 7 件) への対策で、dead-PID の orphan 回収は従来どおり機能する。また同一 PID を複数 active session が共有する場合に `duplicate-pid` warning を出力し、親管理の並列 mission を可観測にした (#314)。

## [2.1.0] - 2026-08-02

### 追加

- `mission-state.py resolve-archive` で `.mission-state` 配下の terminal halted record に監査可能な resolution metadata (`resolved`/`superseded`/`closed`、owner issue、evidence URL、note) を `halt_reason` を変更せず付与できるようにした。対象パスと record 状態は fail-closed に検証する (#301)。

- `issue_ref` を保存時に正規化し、形式差による重複着手検出のすり抜けを塞いだ (#295)。S3 の重複 WARN は halt 中の未完了 session も対象になり stale 注記が付く (#296)。

- `resolve-archive` に `--frozen-snapshot` フラグを追加した (#318)。archive/ 配下の frozen snapshot（loop_active=true または halt_reason 空のまま保存された mid-flight record）に対して、対応する live session（sessions/<session_id>.json）が存在しないか terminal（loop_active=false）であることを検証したうえで resolution を付与できる。live session が loop_active=true の場合は opt-in フラグがあっても拒否（fail-closed）。sessions/ 配下への適用は引き続き拒否。`_validate_resolve_archive_record` の project_root チェックを緩和し、record の project_root が cwd と一致するか cwd の配下パス（worktree 等）に解決される場合も許可する（文字列 prefix 比較ではなく `Path.relative_to` による安全なパス比較）。`is_stale_active_no_score` に resolution_status 除外を追加し、resolved/superseded/closed の archive record を stale-active-no-score の集計から除外する (#318)。

- `mission-state.py review-finalize` が aggregate-reviews → push-score を 1 コマンドで transactional に実行し (集計失敗時は score を push しない)、`closeout` が mark-passes → next を順に実行して結合 JSON を返す (gate 未達は mark-passes の exit code を維持し、next 相当の guidance を出力、state 不変)。既存 validator (min-reviewers / strict review 検証 / findings gate / 再 push 保護 / threshold / agreement / specialist accounting) を複製せず再利用し、標準フローの orchestration turn を iteration あたり 2 turn 削減する。分割実行も後方互換で維持 (#283)。

- `aggregate-reviews` に `--reviewer-window <perspective>=<start>..<end>` (複数指定可・optional) を追加。orchestrator が各 reviewer の実行時間帯を申告し、`parallel_execution: true|false|"unknown"` と申告 window を aggregate evidence と結果 JSON に記録する。時間帯が重ならない場合は WARN (直列実行検出) を出すが exit 0 のまま — 観測のみでゲート不変、review JSON の verbatim 契約も不変。形式不正・未知 perspective・重複・end<start は strict に拒否し、naive な時刻は UTC に正規化する (#282)。

- discriminating-v2 実測 (実行時間 ≒ 総生成トークン量) に基づく出力・turn 規律 3 件を prompt 層に追加: 最終報告と artifact は evidence テーブル + tool-computed ゲート値 + archive 参照パスに限定 (レビュアー出力の逐語再掲禁止、#280)、mission-reviewer の出力は採点/Issues テンプレート + `mission-review/1` JSON に限定し再導出は内部化 (#281)、相互依存のない tool 呼び出しは単一メッセージで並列発行 (依存操作は順次、#284)。

- session state に `last_activity_at` を追加した (#310)。エージェント活動による session 書き込みでは `atomic_write_json` が自動で刻み、`cleanup-stale` / `halt --all` の terminalize 等の janitor 書き込みは `administrative=True` で明示的に刻まない。`duration_sec` と両方の age 連鎖は `last_activity_at` を `updated_at` より優先し、resolution/batch 書き込みが `updated_at` を上書きすることで生じていた壁時計膨張 (company-os #583 で最大 500 倍) を止める。フィールドを持たない旧 state は従来動作を維持する。

- `next` が #258 の critic scope 記録を機械的に強制するようになった (#309)。`phase=reviewing` かつ `iteration >= 2` で `critic_has_new_scope` 未設定のとき、`run-reviewers` ではなく判定基準付きの `record-critic-scope` を返し、prose 指示をエージェントが実行しない経路を塞ぐ (2026-08-01 の実運用監査で 115 sessions 中設定 0 件、#240/#241 の diff-review 最適化が永久休眠状態だった)。記録後は #240 の reviewer 削減と #241 の context mode 付きで run-reviewers guidance が再開する。iteration 1 と pass gate 意味論は不変。

- adaptive routing (#276) を `--issue-ref` 付き `init` で無効化した (#304)。Issue-bound な作業は統治 wrapper の契約 (company-os の `mission-company-os` は init 直後の strict preflight で active state を要求) を意味するため、Simple 判定でも goal 契約へルーティングせず mission ループを維持する。routing の発火条件は「issue-ref なし・Simple・シグナルなし・強制なし」に限定される。

- `mission-state.py init` に adaptive routing を追加した (#276)。リスクシグナル (不可逆・security) のない Simple complexity の mission は `route: \"goal\"` を返して session state を作らず、orchestrator は mission ループを完全にスキップして goal 契約 (Goal / Result / Evidence / Assumptions / Stop Condition) でタスクを直接完遂する。最終報告にルーティングした旨を明記し、mission の pass は主張しない。クリーン測定 discriminating-v2 (品質同点・mission 5.4x 時間/4.9x コスト) と実運用の約 95% が iteration 1 で素通しする観測に基づく。シグナル付き Simple は mission ループを維持 (安全側)、`--review-tier` 明示はユーザー意思としてループ維持、`--force-mission` で無条件にループを強制できる。canonical skills 共有により Claude Code / Codex で同一に動作する (#276)。

- mission-vs-goal ベンチマーク runner に `--parallel N` (default 1、完全後方互換) を追加した。record は per-record clone で隔離済みのため worker pool で並列実行でき、10 records の run が約 2.8 時間 → 3 workers で約 1 時間に短縮される。JSONL append と進捗出力は lock で直列化し、worker 例外は伝播、`--stop-on-blocked` は blocked 検出後の新規起動を止め実行中 entry は完走させる (#270)。

- ベンチマーク runner が permission-mode 汚染を検出・防止するようになった (#268)。子 `claude` プロセスを `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0` で起動し、CC セッションからの実行でも `--permission-mode acceptEdits` が default へ暗黙降格しないようにした。各 record は stderr から検出した `permission_mode_degraded` フラグを第一級で持ち、アーム別 summary が degraded 数を集計、1 件でもあれば limitations に run 間比較可能性の警告を追加する。

- mission-vs-goal ベンチマークに `discriminating` cohort（`tasks.discriminating.json`、5 tasks）を追加した。openworld-v1 で確認した品質天井の解消を目的に、各 task 7-9 個の quality markers を 3-5 fixture に分散させ、documented-override / permitted-difference / valid-but-suspicious の decoy を forbidden markers で採点する。`fail_first` 2 tasks（36 セル構成監査・5 文書台帳照合）は単一パスの網羅が review で fail する設計で `iteration >= 2` を強制し、#240/#241 の diff-review 経路に初の実運用観測を与える。構造・marker 密度・fail-first の存在・fixture 実在・marker の fixture 発見可能性はテストで強制する。N>=10 採用判定 runbook（`discriminating-cohort-runbook.ja.md` / `.md`）に smoke gate・本 run コマンド・機械的な採用ゲートを定義した (#262)。

- mission-vs-goal ベンチマーク runner が、mission ループを初期化しなかった mission arm record を無効化するようになった。mission arm 実行後に `.mission-state` が不在の場合、record を `run_status=failed` / `failure_kind=mission_loop_not_initialized` / `comparable_attempt=false` に再分類する（外的要因の blocked は元の分類を保持し、破損 state はループ開始の証拠があるため無効化しない）。アーム別 summary に comparable record のみで計算する `comparable_average_quality_score` / `comparable_average_elapsed_minutes` / `comparable_cost_usd_mean` を追加し、無効 record による速度・コスト比較の希釈を構造的に防ぐ。既存フィールドは全 records の歴史的意味を維持する (#261)。

- mission orchestrator（`skills/mission/SKILL.md`）に #240/#241 の state 契約をプロンプト層へ配線した。Planner spawn 判定が `critic_has_new_scope` も記録し（critic の全計画ステップが既存 finding id のみなら false、`new` を含むなら true）、差分レビュー節は `next` の `details.reviewer_count`（state-driven の独立 2 名。矛盾していた「検証 1 名」記述を置換）に従い `aggregate-reviews` へ `--min-reviewers` を必ず付ける。新設の bounded context 節は `details.context_mode == "bounded"` のとき context manifest を生成して reviewer へ渡し、生成失敗時は full context へ fail-safe fallback する。`mission-reviewer` には context manifest 入力（manifest + 指定 diff を一次スコープ、採点基準と Step 0 テスト実行義務は不変）を明文化した (#258)。

- `mission-state.py context-manifest --iteration N --out <path>` で bounded context manifest JSON（`mission-context-manifest/1` スキーマ）を生成できるようにした。mission goal、iteration、`score_history` から抽出した prior findings を含む。`_derive_next_action` の reviewing ブロックが details に `context_mode` を返すようになり、`iteration >= 2` かつ `critic_has_new_scope is False` の場合は `"bounded"`、それ以外は `"full"` を返す。reviewer fork がフル parent history ではなく evidence manifest のみを受け取れるようにし、diff レビューでのコンテキスト浪費を削減する (#241)。

- mission-vs-goal ベンチマークに `openworld-discovery` cohort（`tasks.openworld.json`）を追加した。open-world の finding 発見をテストする 3 タスクで構成され、solver は事前列挙なしで divergence・contradiction・root cause を独立に発見する必要がある。タスク設計: constant-hunt（canonical default に対するサービス横断 timeout 監査）、contradiction-chain（real contradiction + 注意深く読むと整合する decoy）、incremental-reveal（最初の仮説が誤りである時系列 incident log）。scoring は tail cohort と同じ `quality_markers` / `forbidden_markers` / `hidden_paths` infrastructure を使う (#251)。

- `_derive_next_action` が `iteration >= 2` かつ `critic_has_new_scope=false` のとき `reviewer_count: 2` を返すようになり、diff-only review のオーバーヘッドを最大 1/3 削減する。`critic_has_new_scope` フィールドは `set` で設定可能、未設定時は full count（安全側）。`aggregate-reviews` に `--min-reviewers N` を追加し、N 未満の reviewer JSON 入力を exit 2 で reject する（合意偽装防止）。`next` の command_hint は effective reviewer count >= 2 のとき自動的に `--min-reviewers` を含む (#240)。

- `mission-state.py init --budget-minutes <N>` で時間予算 (wall-clock) を宣言できるようにし、read-only の `next` が `started_at` から導出する `budget_pressure` シグナルを返すようにした。80% で `warn` (optional specialist / critic の新規 spawn を控える advisory)、100% 超で `exceeded` となり、spawn 系の next action (`run-planner`/`run-executor`/`run-reviewers`) を「成果物を確定して `mark-halt --category partial-done` で終了する」`consider-halt` 案内へ差し替える。安価なローカル完結手 (`aggregate-reviews`・`mark-passes`)・terminal 報告・`await-user` は差し替えず、ゲート意味論は不変。2026-07-22 に実測された「USD 予算を使い切って成果物ゼロで kill される全損」の再発を防ぐ。ベンチマーク runner には `--mission-budget-minutes` を追加して `/mission` プロンプトへ予算を渡せるようにし、`total_cost_usd` を第一級フィールドとして記録して blocked/failed run の全損コストを集計可能にした (#238)。

- `mission-state.py advance --phase <phase> --activity <kind>:<reason>` が phase 遷移と activity 切替を単一 lock・単一 atomic write で行い、「phase だけ進んで activity が空」の state を作れなくした (2026-07-22 実行速度監査で実測された activity coverage 9.96% の構造要因への対策)。検証 (phase 正規化・kind/reason enum) は lock 取得前に行い、不正入力では一切 write しない。`done`/`halted` への遷移は従来どおり `mark-passes`/`mark-halt` 専用であり、advance を pass gate の迂回路にはできない。現在と同じ phase を指定した場合は activity 切替のみ行う (#237)。

- local authoring が mission state 初期化前に fail-closed な source bootstrap を実行するようになりました。`origin/main` を取得し、clean な `main` だけを fast-forward で更新して local と remote-tracking commit の一致を検証し、更新済み `SKILL.md` の読み直しを要求します。dirty、`main` 以外、detached、ahead/diverged、remote branch 欠落、network failure では、古い版への fallback や history 書き換えを行わず停止します (#229)。

- `mission-state.py stats` と `mission-audit.py` が排他的な pass-rate health 分類を共有し、finite な `raw_pass_rate` / `completed_pass_rate` と明示的な分子・分母を出力するようになりました。fresh active は可視化したまま completed population から外し、stale active は actionable な未合格 health debt として completed population に含めます。active、active-no-score、stale、halt、abandoned の件数は JSON と console に常に表示します。deprecated な `pass_rate` alias は各 command の従来の意味を維持し、stats は audit と同じ current immutable worktree archive generation を読み込みます (#208)。

- `mission-audit.py --current-since` が検出済みrecord/itemをregistry駆動の共通finding modelへ変換し、forced pass、halt/slow/scoring、specialist provenanceのriskを同じUTC inclusive cutoffで分類するようになりました。`--since` / `--until` / `--current-since`の日付・ISO boundは一つのparserで扱います。JSONはall/current/historicalの基準evidence一覧、severity・code別の保存則count、code別のcompactなcount/indexを、Markdownはcurrent P0/P1/P2をhistorical riskより先に表示します。historical evidenceは元severity/provenanceを保持しますが現行改善promptには渡しません。timestamp欠落・不正はcurrentに残し、cutoff未指定は従来の全期間表示を維持します。pass severity、required specialist result gate、force approval gateは変更しません (#207)。

- mission state に active work・external wait・approval wait・reviewer wait・idle を明示する bounded activity segment を追加しました。`mission-state.py stats` と `mission-audit.py` は同じ reducer で task/phase の R7 p50/p90、kind/reason totals、coverage、unclassified time、anomaly count を集計します。crash/resume gap は分類せず、既存 phase duration と review/pass gate を維持します (#211)。

- `mission-state.py archive-worktree` を追加しました。終端済み worktree session と state が参照する evidence を、同じ Git common directory に属する既存の別 checkout へコピーします。更新は content-addressed な immutable generation を publish してから `current.json` を atomic に進めるため、crash や parallel reader が旧有効世代を見失いません。`mission-worktree-archive/1` manifest は session/mission/iteration identity、evidence type、機密を含まない relative source/archive reference、SHA-256、size を記録し、重複 path、path escape、symlink、evidence 欠落、integrity 不整合を fail-closed にします。`mission-audit.py` は discovery 時の generation を固定して state のロード前に preflight し、検証済み manifest から scoring / specialist evidence を解決して、同一 record の検証を cache します。`.mission-state` は降下前に readiness を確認し、後続の walk access error も収集します。directory 以外・読取不能・symlink の `.mission-state` / archive root、bundle / generation ancestor の symlink、通常 archive root 外へ解決される bundle、archive / pointer / generation の access failure、不正・危険な pointer、archived state の欠落・不正 JSON、generation manifest の欠落・不整合は、root 外読込・archive の黙示的除外・stale file fallback をせず、重複排除した `invalid-worktree-archive` finding として明示し、pointer 不在を `lstat` で確認できた既存 bundle だけ互換性を維持します (#212)。

### 変更

- `skills/mission/SKILL.md` を 217 → 約 202 行に圧縮し、260 行未満の regression guard を追加。あわせて per-turn context 規律を追加: state 全文 echo 禁止 (`get --field` / `next` の JSON のみ)、reviewer JSON はパス受け渡しのみで再読しない、refs は lazy-load (#285)。

- 不可逆・security シグナルのない Complex mission の `review_tier` 導出を `full` (3名) から `standard` (独立2名) に再調整した。Critical とシグナルでエスカレートした Complex は従来どおり `full` (3名) を維持し、pass gate の意味論 (threshold / open_high / findings evidence / agreement) は不変。discriminating-v1 の実測で品質同点タスクの壁時計の 62% が reviewer-wait であり 3 人目の限界検出価値がゼロだったこと、#240 の「独立 2 名 = agreement 成立の最小構成」の先例に基づく (#266)。

- mission-vs-goal ベンチマークの runner に `--repeats N` を追加し、各 (task, arm) セルを N 回反復して record に `run_index` を記録できるようにした。summary にはアーム別の marker スコア分散と `total_cost_usd` の合計/平均 (blocked run の全損コストを含む) を追加し、flaky・ノイズと実力差を分離できるようにした (#249)。mission arm の record には、実行後の mission state から fail-open で抽出した `mission_review_tier` / `mission_iterations` / `mission_complexity` / `mission_passes` / `mission_halt_category` を記録し、tier 別のコスト・品質帰属を可能にした (#250)。

- mission-vs-goal ベンチマークの scorer が、完走した markered record を全て 5.0 天井に張り付かせる問題を解消した。markered task は `1.0 + 1.0 × validator_fraction + 3.0 × marker_score`（gradient v2）となり内容 recall が支配項になる。marker なし task は legacy 二値 1.0/4.0 の歴史的意味を維持する。新 record は `quality_score_method`（`..._gradient_v2_...`）で機械的に区別でき、既存 JSONL は不変 (#247)。validator gate はアーム対称化し、両アーム共通見出し（Evidence/Assumptions）のみが `validator_pass` を決める。アーム固有見出し（goal 3 個 / mission 6 個）の欠落は `missing_arm_specific_headings` として記録するが gate しない — 見出し数の非対称による完走難易度差と「冗長に書くほど有利」の歪みを除去した (#248)。両 runner の `score_from_signals` は同一意味論をテストで強制している。

### 修正

- ベンチマーク runner が child `claude` に `--allowedTools` を明示するようにした。Claude Code 2.1.219 の hardening で `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0` の opt-out が無効化されたための対応 (#292)。

- 同一 session 内で Mission を切り替えた際に旧 assumptions を再利用せず、世代分離して安全に archive するようにした (#302)。

- `cleanup-stale` が `getppid()` fallback で記録された PID の mission を即座に orphan halt しなくなった（agent CLI がプロセスツリーで発見できない場合）。`find_agent_pid()` が state に `pid_source` を `"fallback"` または `"agent"` として記録し、`cleanup-stale` は fallback 由来の PID が最近消滅した場合（age < stale threshold）はスキップする。真に放置された fallback セッション（age >= threshold）は従来どおり halt する。`pid_source` なしの旧 state は既存の即時 halt 動作を維持する (#239)。

- `mission-audit.py` が実ログ由来の委譲 handoff と明示的な merge 承認待ちの halt reason を認識し、P1 `halted-runs` の actionable 判定に反映するようになりました。raw halt 件数は維持し、stale/orphan は引き続き安全側で actionable に残します。日本語の root 引き渡し・承認待ち文言で actionable pass rate が過度に下がる問題を修正しました (#233)。

- `mission-audit.py` が raw halt 件数を保持したまま、原因調査が必要な終端状態だけから P1 `halted-runs` と別指標 `actionable_pass_rate` を導出するようになりました。構造化された承認待ち、委譲済み partial completion、ユーザー中断、明示的な解消・置換証跡、限定的に認識した外部待ちは内訳に残しつつ actionable 品質を押し下げません。stale、stagnation、競合 gate、未知・曖昧な halt は安全側で actionable に残します。deprecated な `pass_rate` alias は completed-session rate のまま維持します (#221)。

- 非対話の mission 起動時に、orchestrator が必要とする配布版・リポジトリ内の state CLI コマンドだけを許可するようにしました。`mission-state.py init` は実作業前に session state ディレクトリと assumptions 証跡へ内容保持・fsync 付きの実書き込み probe を行います。probe 失敗時は exit 2 と構造化された `blocked-external` halt を返し、state 自体も保存不能な場合は同じ構造化証跡を stdout に残して承認質問を行いません。明示診断用に同じ検査を行う `permission-preflight --json` も追加しました (#220)。

- 不可逆操作の `review_tier` キーワードを、operation に anchor した clause と構造 unit の文脈で出現ごとに評価するようにしました。否定は文字 window 内の cue ではなく対象 operation への直接的な文法 anchor を必須とし、短縮形・`cannot`・active な `not perform/execute`・passive な `will/should not be performed/executed`・日本語の qualifier 付き否定も扱います。明示的に否定された実操作は Simple/Standard を昇格させず、条件例外、非実行 intent 自体の否定、不確実表現は安全側で採用し、複数否定 cue は次の operation より前にある場合だけ反転否定として扱います。global 非実行 marker は、候補自身の context が meta/non-operation intent と証明できる場合だけ抑制し、同じ logical unit の execution cue が別の named operation に直接係ると証明できない場合は曖昧照応として採用します。quote-only intent は、引用符直前・直後の直接実行または引用直後の passive modal だけが上書きし、引用内や別の明示 operation の execution wording では上書きしません。segment・operation start・quote・meta/non-operation・否定 operation・否定 cue・global marker の索引を cache し、全文・dense context の反復走査を避けます。既存の順序付き文字列 signals は変えず、state に出現単位の `review_tier_signal_details` provenance を追加し、security・high-risk・Complex/Critical の挙動も維持します (#209)。
  meta/non-operation の証明は候補 context 全体が strict meta-only 文法へ一致することを要求し、未知の後段句があれば抑制しません。quote span 内の execution cue は曖昧照応 veto の対象外です。quote-only も marker・無害終端・別 named operation への明示 action を除いた外側残余が空の場合だけ抑制します。
  modal / contraction で始まる `not not` と、`not the case that` / `not saying that` / `cannot say that` などの外側否定を二重否定として扱います。`except when` / `until` / approval 待ち / passive な緊急時例外は条件付きのままです。文をまたぐ `follow/apply + pronoun` と日本語の `適用` / `従う` は曖昧な実行照応として global meta-only 抑制を veto します。
  `例外なく` / `緊急時にも` / `原則ではなく絶対に` という強い無条件否定は、広い例外 marker を発火しないようにしました。単純な operation 否定の後に続く因果的な安心表明は、独立した述語否定を誤って二重否定にしません。
  短縮 auxiliary と `never` を operation scope 付きの単純/二重否定で共通化し、外側の報告否定の短縮形も扱います。approval gate は `before` / `prior to` / `while ... is pending` を追加し、曖昧実行照応は pronoun または named procedure に対する `follow` / `apply` / `proceed with` まで認識します。日本語の因果的な安心表明に影響表現を追加しました。
  外側の不確実表現に `not true that` と `no guarantee/assurance/certainty that` を追加し、内側 operation clause の modal 否定が短縮形でも展開形と同じ文法で扱います。

### セキュリティ

- 手動 halt した mission の再開を、専用の `reactivate --approved-by-user --expected-category ... --reason ...` 遷移に限定しました。停止カテゴリを検証し、旧停止理由・カテゴリ・承認理由を append-only の `reactivation_history` に残したうえで、current の停止フィールドをクリアし、activity 計測を同一 lock 内で再開します。汎用 `set` では halt の解除や承認監査の書き換えができません。自動 stale/orphan 復旧は引き続き `resume` / `refresh-pid` を使い、復旧時に current の `halt_category` もクリアします。

- `codex-preflight --strict` が deprecated な `MISSION_REQUIRE_SCORING_EVIDENCE=0` escape hatch を検出して reject する (exit 2)。あわせて実行結果を not ok として報告する。この環境変数は legacy な `push-score --items` 経路で scoring-evidence gate をバイパスするため、有効なまま実作業へ進んではならない。escape hatch 自体は当面機能を維持するが、文言を `DEPRECATED ESCAPE HATCH` に変更し、次のマイナーリリースで削除予定とした (#226)。

## [2.0.0] - 2026-07-20

### 破壊的変更

- `mark-passes --force` に `--approved-by-user` が必須になりました (未指定は exit 2)。このフラグは「ユーザーが明示的に override を指示した」という宣言であり、バリデーション回避のスイッチではありません。orchestrator が自律的に付けてはならず、ユーザーの明示指示がある場合にのみ使用します。state には従来の `force_reason` に加えて `force_approved_by_user` を記録し、`mission-audit.py` はこれを欠く forced pass を新しい P0 finding として報告します (#185, #193)。
- `set phase=` を phase enum で検証するようになりました。未知の値は exit 2 とし、既知の 4 エイリアス (`execution` / `review` / `plan` / `score`) は警告付きで正規形へ正規化します。実運用で `phase=execution` (typo) が無検証で通り、`phase_duration_totals` を汚染した実害への対処です (#188, #191)。

### 追加

- `mission-state.py stats` に `by_review_tier` (`by_complexity` と同形) と `iteration_by_review_tier` を追加しました。light tier が手戻りを生んでいないかをコマンド一発で監視できます。tier 導入前の state は `unknown` に集計されます (#180, #182)。
- state に `cli_version` を記録し、Claude Code / Codex の plugin cache を走査して実行中 CLI との version skew (古い install) を検出するようになりました (#186, #195)。
- `mark-halt` / `halt --all` が `--category` を受け付けるようになりました (共有 enum `HALT_CATEGORIES`: `blocked-external` / `awaiting-approval` / `partial-done` / `stagnation` / `user-abort` / `stale` / `other`)。未指定・不正値は警告付きで `other` にフォールバックします — 緊急停止パスが category の不備で失敗してはならないためです。自動 halt は `stale` を記録します (#190, #192)。
- optional specialist を選択したまま invocation を一度も記録せずに pass しようとした場合、`mark-passes` が警告と、閉じるための `specialists log-invocation --status skipped` コマンドを表示します。pass gate 自体は変更ありません (#189, #194)。

### 変更

- 「リリースして」「本番へデプロイして」などの明示的なユーザー指示を、対象が一致する不可逆操作の事前承認として扱うようにしました。対象・scope・rollback 条件や必要な破壊的操作に実質的な差分がない限り、実行直前に同じ確認を繰り返しません (#197)。
- `_derive_next_action` が「`score_source=scoring-json` なのに `findings_evidence_path` を欠く」score entry を検出し、再試行を instruction 頼みではなく state 駆動で促すようになりました。実運用の Codex run が `aggregate-reviews` の出力を得られず `--force` へ逃げた一方、同時期の別 run は自己回復していた、という実害への対処です (#187, #196)。

### 修正

- `task_profile.risk=high` のキーワードを #174 と同一ポリシーで較正しました。`prod` を削除 (`production` があり冗長、`product`/`productivity` への誤発火源)、`auth` を `authenticat`/`authoriz`/`oauth` へ、単独の `token` を複合語 6 種へ置換。506 mission の遡及実測で `risk=high` は 72→53 件、risk 起因のエスカレーションは 17→9 件、見逃しは 3 件のまま不変です (#175, #183)。
- `mission-audit.py` が archived worktree bundle 内の `iter-N-<mission8>/scoring.{json,md}` に保存された scoring evidence を認識するようになり、worktree cleanup 後に historical `missing-scoring-evidence` が誤検出される問題を修正しました (#201)。

## [1.2.0] - 2026-07-10

### 追加
- `mission-state.py init` と `mission-state.py set` が session の complexity とミッション記述から `review_tier`（`light`/`standard`/`full`）を導出・保存するようになりました。risk escalator（high-risk profile・不可逆/本番/security キーワード）は昇格のみ行い、降格しません。`reviewer_count` は tier に連動し、pass gate と scoring threshold は不変です。ユーザー指定の override は記録された `source` と `signals` で監査できます (#168, #171)。
- ADR-003 を追加しました。adaptive review gating の決定（tier 導出テーブル・escalator 意味論・ゲート不変宣言）と、tail-v1 実測および 451 mission 本番集計を context として記録しています (#169, #172)。
- `docs/CASE_STUDIES.md` と `docs/CASE_STUDIES.ja.md` を追加しました。451 件の採点済み本番 mission から匿名化した実測エビデンスとして、pass rate 分布・24 件の強制 iteration・7 件の不可逆操作への承認ゲート halt・6 件の代表事例サマリを、出典・限定事項・比較品質主張なしで収録しています (#155, #158)。
- benchmark runner に planted-defect タスク fixture を用いた tail-first-failure cohort を追加しました。quality marker が defect 特有のコンテンツトークンであり、`forbidden_markers` でネットスコアを減算し、`hidden_paths` により answer key（task 定義ファイル）を clone 済み worktree から両アーム実行前に削除し、`markers_hidden` により両アームの prompt に marker 名を出しません (#153, #156)。
- tail-v1 paired run（10 タスク × 2 アーム、claude-sonnet-5、2026-07-07）のベンチマーク報告を追加しました。両アームの quality score は同点で、mission アームは goal アームの約 5.8 倍の時間・約 7.4 倍のコストを要しました。全 5 件の mission run で iteration-1 の self-gate が pass しました (#162)。
- benchmark smoke-v2（N=1、2026-07-10）を追加し、health-interval marker pattern 修正を実証しました。goal アームのスコアは 0.86 から 1.00 に回復し、mission アームは `api_usage_limit` blocked のため品質比較から除外しています (#170, #173)。

### 変更
- benchmark runner が form-stripped scoring を marker マッチング前に適用するようになりました。`strip_form` が見出し・ラベル行・水平線・表の区切り行を除去することで、テンプレート構造が marker クレジットを得なくなります。除去前のスコアは `quality_marker_score_raw` として保存し、`quality_score_method` を `automated_heuristic_form_stripped_not_blind_human` に更新しています (#154, #157)。
- SKILL.md に light tier 運用規律（reviewer 1 名・required のみの specialist・critic は失敗時のみ）を追記し、README に adaptive gating の要約段落を追加しました。pass gate threshold は不変です (#169, #172)。
- README に実測エビデンスの位置づけを追記しました。tail-v1 run では両アームの quality score が同点で、mission アームは約 5.8 倍の時間・約 7.4 倍のコストを要したこと、および本番価値が約 5% の強制 iteration tail と承認ゲート halt に集中することを明記しています (#161)。

### 修正
- `review_tier` の escalator キーワードを 505 mission の遡及分析で較正しました。`push`/`merge` を除外（標準 dev フロー記述への誤発火）、単体 `token`/`auth` を複合語・語幹に置換、単体 `削除` をデータ削除系の複合語に置換。Simple/Standard の過剰エスカレーションは 39.1% から 32.2% に低下し、低スコアミッションの見逃しは増えていません (#174, #178)。
- `mission-audit.py` の `specialist-invocation-gap` 判定では `specialists_phase_plan` の provider を advisory な scheduling hint として扱い、計画だけされた provider が terminal invocation 欠落として誤検出されないようにしました (#176)。
- specialist phase plan の provider を shared accounting 上の selected evidence provider として扱うようにし、計画済みの execution / review / synthesis provider を実行した場合に `unselected-specialist-invocation` が誤検出される問題を防ぐようにしました (#165)。
- `mission-audit.py` と `mission-state.py stats` が archived worktree の `aggregate.json` など session ではない metadata JSON を無視するようになり、`unknown` の abandoned session や low-pass-rate finding の誤検出を防ぐようにしました (#163)。
- `mission-audit.py --since` / `--until` が日付だけでなく ISO timestamp も受け付けるようになり、automation cutoff と同じ日の後続 state が監査から黙って除外される問題を修正しました (#159)。
- `mission-audit.py` が `mission-archive/` worktree パスに保存された scoring evidence を認識するようになり、worktree cleanup 後に `missing-scoring-evidence` が誤検出される問題を修正しました (#151, #152)。
- benchmark の health-interval marker pattern を拡張し、`HEALTH_CHECK_INTERVAL_SECONDS=75`、`(75`、`` 75` `` の引用形式にも一致するようにしました。既存の記録スコアは変更せず、今後の run のみに適用されます (#162)。

## [1.1.1] - 2026-07-06

### 修正
- command provider が `result_contract.awaiting_input_markers` または `result_contract.awaiting_input_exit_codes` に一致した場合、明示承認・人間入力待ちを generic failure ではなく `awaiting-input` として記録できるようにしました (#145)。

### 変更
- specialist registry の文書で、外部送信、browser automation、browser session material、paid quota の承認スコープを分離し、first-use consent が session cookie 再利用や paid model 利用の包括承認ではないことを明記しました (#146)。
- Oracle command-provider の safe default 文書を追加し、local wrapper は manual login または `awaiting-input` を既定にし、明示的な browser-session-material 承認後だけ `--copy-profile` を渡す方針を明記しました (#147)。

## [1.1.0] - 2026-07-05

### 追加
- `mission-state.py aggregate-reviews` を追加し、strict な `mission-review/1` reviewer JSON から、rubric cap・reviewer consensus・open High 件数・findings evidence archive を含む決定論的な `push-score --scoring-json` payload を生成できるようにしました (#119)。
- `mission-state.py specialists log-invocation --selection-source task-required` を追加し、タスク上必須の情報取得・証跡 provider を、private skill 名をハードコードせず selected specialist として記録できるようにしました (#115)。
- `mission-state.py resume` を追加し、active session の復帰時に current mission state、latest artifact、next action、progress checkpoint、stale-session hint を含む復旧順序を表示できるようにしました (#123)。
- benchmark runner に arm-blind scoring、counterbalanced order、明示的な `model_id` 記録、mission-vs-goal 比較用の result/report schema 更新を追加しました (#129, #130)。

### 変更
- `aggregate-reviews` が reviewer agreement を score `items` から外し、独立した `review_agreement` と `agreement_detail` として記録するようになりました。`mark-passes` は極端に低い合意 (`max-min > 1.5`) を pass 前に拒否します (#126)。
- `mark-passes` が、`score_source=scoring-json` の pass 判定で機械由来の findings evidence を primary gate として扱うようになりました。`findings_evidence_path` 欠落や High finding 件数の不一致は、score threshold 判定前に拒否します (#121)。
- 標準 Phase 5 が reviewer の `mission-review/1` JSON、`aggregate-reviews`、`push-score --scoring-json` で進むようになり、`mission-scorer` を spawn しない運用にしました。`mission-scorer` は散文レビューを JSON に変換する fallback 専用として文書化しました (#120)。
- `mission-state.py` と `mission-audit.py` が、mission state の分類・duration・specialist checkpoint・preparation marker ロジックを `skills/mission/lib/mission_common.py` で共有するようにし、audit と state tool の drift リスクを下げました (#127)。
- `mark-passes` が、新規 Standard / Complex / Critical session で `task_profile` と `specialists_decision.policy` の checkpoint がない場合に完了を拒否するようにしました。fallback / degraded の明示 decision は有効な checkpoint として扱います (#112)。
- `cleanup-stale` が、記録された agent PID が生存していても、`MISSION_STALE_ACTIVE_SECONDS` を超えた active no-score session を stale として halt できるようにしました (#113)。
- public ref docs と packaged plugin mirror を OSS portability の観点で見直し、配布される setup 例から maintainer-local home path と private skill 名を除去しました (#118, #132)。
- README、Codex setup docs、critic/planner handoff guidance、軽量化した mission skill instructions を現行 source の scoring flow に合わせました。`mission-review/1`、`aggregate-reviews`、`push-score --scoring-json`、独立した review-agreement gate、`open_high` / findings evidence の pass check を前提にしています (#128, #134, #137, #140, #141, #142)。

### 修正
- mission audit が `score_history[].scoring_evidence_path` の明示パスと、通常または archived worktree の `.mission-state` に保存された JSON scoring evidence を認識するようにしました (#111)。
- mission audit が、fresh な active no-score planning session を specialist accounting debt から分離し、stale な active no-score session は JSON / Markdown で明示的に報告するようにしました (#113, #114)。
- `push-score --scoring-json` が inflated self-reported scalar score を拒否し、同一 iteration の score を置き換える場合は `--resubmit-reason` を必須にしました。これにより score evidence の silent overwrite と転記 inflation を防ぎます (#122, #131)。
- documentation consistency guard が、`open_high` gate、`findings_evidence_path`、`--scoring-json`、`--root`、README test count の鮮度、v1.1.0 release theme を検査するようになりました (#128, #134)。

## [1.0.7] - 2026-07-03

### 修正
- `mission-state.py` と `mission-migrate.py` に `from __future__ import annotations` を追加し、PEP 604 union 注釈が Python 3.9 (macOS Xcode CLT の `python3`) でモジュール読み込み時にクラッシュして全コマンドが使えなくなる問題を修正しました (#99)。

### 追加
- `mission-state.py codex-preflight` を追加しました。現在の Codex `/mission` session に active state があるか、user Stop hook に `mission-stop-guard.sh` が登録されているか、`mission-state.py next` fallback で継続できるかを診断します。skills-only の Codex run では警告に留め、`--require-stop-hook` では hook 未設定を failure にできるため、Issue #108 の「state なし・guard なし・未完了 final」パターンを検出できます。
- `specialists recommend --user-specified <skill,skill>` を追加しました。ミッション本文でユーザーが名指ししたスキルを confirmed 扱いにし、high-risk task profile でも `selection_source: user-specified` の selected として記録するため、以後の `log-invocation` が `--selection-source confirmed-user` 要求で reject されなくなります (#100)。名指しの中に first-use consent が必要な provider が混在する場合、または required specialist が未インストールの場合は、全体を従来の確認フローに倒します。
- `mission-state.py push-score --scoring-json <path>` (ADR-002 Stage 1) を追加しました。scorer の構造化 JSON ファイルから items を読み、`composite`/`min_item` を CLI 側で再計算し、未知キー・範囲外値を reject し、payload を `_meta` 付きで `iter-N-<mid8>-scoring.json` として archive し、score entry に `score_source`/`scoring_evidence_path` を記録します (orchestrator のスコア転記レイヤを排除)。
- `push-score` が「全 items スコアが 1.0 以下」の入力を 0-1 正規化スケール混入の疑いとして reject するようにしました (実ログで composite 0.96 = 4.8/5 が push された事例の回帰ガード)。
- `mission-state.py next` (ADR-002 Stage 3) を追加しました。session state から次の 1 手 (`run-planner`/`run-reviewers`/`run-scorer`/`mark-passes`/`report-blocker` 等) を決定論的に導出し、Stop hook が使えない Codex セッションや compaction 復帰時に、散文指示に依存しないハーネス非依存の進行ガイドを提供します。

### 変更
- scoring evidence なしの `push-score` は default で hard reject するようにしました。`--scoring-json` (推奨) または `--scoring-output` を指定してください。移行専用の一時 escape hatch として `MISSION_REQUIRE_SCORING_EVIDENCE=0` は残しています (#105)。
- evidence なし `push-score` の generated scoring evidence fallback を削除し、reviewer 本文のない `generated=true` archive file で score entry を裏付ける挙動を廃止しました (#105)。

## [1.0.6] - 2026-07-02

### 修正
- `mission-state.py init` が破損した session JSON を隔離するようになり、同一セッションでの mission 変更時にクラッシュしないようにしました。
- `mission-state.py set` が pass・score history・threshold 系フィールドを凍結するようになり、raw な state 更新で完了ゲートをバイパスできないようにしました。
- `mission-state.py push-score` が、渡されたスカラースコアと items 明細のスコアが乖離している場合に警告を出すようにしました。
- Stop hook の CWD 探索が遅い `lsof` によるハングを避け、Linux では `/proc/<pid>/cwd` を優先し、自セッションの直接参照を先に行い、`awaiting_user` セッションの stale auto-halt をスキップするようにしました。
- specialist の同点処理が、インストール済みで optional な low/medium リスク provider を決定論的に自動選択し、tie-break 理由を記録するようにしました。
- mission executor が `Agent` や `rm` を含まない bounded な allowed tools を宣言するようにしました。
- specialist の task_profile 分類が architecture / system design 系 mission を認識するようになり、architecture 専用の project / user provider が documentation fallback に隠れて選ばれない問題を修正しました。
- mission audit が archived worktree の `iteration-archive/` ディレクトリに保存された scorer evidence を認識するようになり、scoring artifact が存在する場合の `missing-scoring-evidence` 誤検出を防ぐようにしました。
- mission audit が JSON として完全一致する archive-only の worktree state copy を resolved duplicate として分類するようにし、cross-root audit で想定内の archive/archive copy が P1 `duplicate-state` と誤報告されないようにしました。

### 追加
- ADR-002 として、local JSON + flock ストレージを維持したまま Finding / Score / Decision / Action を段階的に型付き state オブジェクト化するロードマップを定義しました。
- local-first な mission artifact を archived evidence 付きで管理する `mission-state.py artifact` CLI を追加しました（`docs/MISSION_ARTIFACTS.ja.md` 参照）。
- specialist registry の `kind: command` provider に `env` と `timeout` の runtime 設定を宣言できるようにしました。`env` はその provider プロセスにのみ渡され、CLI の `--timeout` は registry の値より優先されます。

## [1.0.5] - 2026-06-26

### 追加
- `ask-user` 後に confirmed selection metadata が残っていない specialist 適用を、unselected invocation とは別の audit finding として報告するようにしました。
- phase duration がある一方で経過時間の大半が planning に粗く帰属している slow session を mission audit が報告するようにしました。
- mission audit の self-improvement prompt に、agent が GitHub Issue を作成する前の重複 issue 確認と development/tech-lead review 証跡の記録を必須化する指示を追加しました。
- `mission-state.py push-score` が `--scoring-output` 未指定時にも generated scoring evidence を保存するようになり、すべての score history entry に監査可能な archive artifact が残るようになりました。
- `mission-state.py specialists log-invocation --selection-source` を追加し、inline / tool invocation evidence の記録時に、明示・手動選択された specialist の selection metadata も同時に残せるようにしました。
- final report 用に selected / used / degraded / unselected-manual を provider の `kind` と registry/source metadata 付きで出力する `mission-state.py specialists summary` を追加しました。
- 通常の merge release と意図的な distribution release を分離する versioning policy を文書化し、PR を merge するたびに plugin version を上げない運用を明確化しました。
- `AGENTS.md`、`CLAUDE.md`、ADR-001 に OSS portability guardrail を追加し、個人/private specialist skill を public default ではなく user / project registry に置く方針を明確化しました。
- 完了前の warning として、terminal decision trail がない available specialist/provider candidate を表示する `mission-state.py specialists accounting --json` を追加しました。
- `mission-state.py` と `scripts/mission-audit.py` で candidate accounting ロジックを共有し、実行中チェックと事後監査で同じルールを使うようにしました。
- 正典の state CLI に委譲する repository root の安定 wrapper `scripts/mission-state.py` を追加しました。
- 長時間 batch 向けに `mission-state.py progress update/get/clear` checkpoint を追加し、進捗証跡を archive に保存して slow-session の audit 行にも表示できるようにしました。
- maintainer-local な skill 名を組み込まず、development / strategy 系 registry の段階的な利用順を示す `specialists_phase_plan` を recommendation に追加しました。
- mission audit が不正な score iteration と空の specialist invocation record を明示的な finding として報告するようにしました。
- mission audit に `--current-since` を追加し、historical audit debt を可視化したまま current regression と分離して判定できるようにしました。
- distribution release では、対応する git tag の作成・push、GitHub Release の作成または更新、両方の再照合まで完了条件とする release guardrail を追加しました。

### 変更
- mission orchestrator の運用指針に、`phase=executing` / `phase=reviewing` の明示更新と長時間作業の progress checkpoint を必須化しました。
- Complex mission の specialist accounting を、リスクを持つ candidate だけに explicit terminal decision を求める形へ調整し、ユーザー plugin をデフォルトでは optional evidence source として扱うハッカブルな拡張性を維持しました。
- database/backend candidate は schema / migration / query / SQL / persistence などの強い database signal がある場合だけ high-risk accounting candidate として扱うようにしました。
- command provider の `result_contract` により、準備完了バナーだけ、または短すぎる出力を `prepared` と分類し、完了済みレビュー証跡として扱わないようにしました。
- `oracle-reviewer` に browser-review の準備完了バナー向け default result contract を適用し、`ask-user` 後の specialist confirmation は `--selection-source confirmed-user` で永続化してから selected evidence として扱うようにしました。
- broad orchestrator specialist は non-execution の evidence use に限定し、plan/review などの適用済み証跡には `--bounded-purpose` を必須にしました。
- Standard / Complex の監査・自己改善 mission では、利用可能な testing / security / risk specialist candidate に explicit accounting を求めるようにしました。

### 修正
- command provider invocation が `completed` と記録されていても、archive evidence が Oracle / browser review の準備パケットだけの場合に mission audit が検出するようにしました。
- mission audit が、ユーザー判断待ちの active な `ask-user` specialist wait を、decision 記録前の candidate-only specialist debt として誤検出しないようにしました。
- core mission subskill の呼び出しを external specialist の unselected invocation として誤検出しないようにしました。
- marketplace 配布版の `mission-state.py` wrapper から specialist accounting / result-contract marker が欠落しないよう、同期テストで保護しました。
- mission audit の pass rate 計算から active no-score checkpoint を分母除外しつつ、incomplete active session としては引き続き報告するようにしました。
- mission audit が nested `archive/worktree-*/sessions/*.json` copy を resolved archive duplicate として分類するようにし、cross-root audit で live/archive の完全一致 copy が P1 `duplicate-state` と誤報告されないようにしました。
- `mission-state.py mark-passes` が required specialist provider の適用済み結果証跡を確認するようにし、`prepared` / `skipped` / `failed` だけでは strict required-provider gate を満たせないようにしました。
- `mission-state.py push-score` が 1 未満の iteration を拒否するようにし、監査不能な `score_history` entry を防ぐようにしました。
- `mission-state.py specialists log-invocation` が空の `role` / `skill` を保存前に拒否するようにしました。
- `mission-state.py stats` が nested `archive/worktree-*/sessions/*.json` を含めて集計し、audit discovery と session count が揃うようにしました。

## [1.0.4] - 2026-06-22

### 追加
- README で `mission` を loop engineering の品質ゲートとして位置づけ、launch positioning guidance へのリンクを追加しました。
- `mission-state.py stats` が repeated `--root` を受け付け、複数 root を集約し、scan root の一覧を出力し、重複する state identity を二重計上しないようにしました。
- specialist invocation logging が `skill-tool-applied` を受け付け、skipped / unavailable / failed の判断理由を必須化し、高リスク candidate accounting を文書化しました。
- specialist candidate が存在する一方で selection / invocation / skip の decision trail が記録されていない場合、mission audit が `candidate-only-specialists` として可視化するようにしました。
- terminal evidence はあるが Phase 1 selection metadata と対応しない specialist invocation を mission audit が可視化するようにしました。
- mission の最終報告に selected / used / degraded / unselected-manual の短い specialist summary を追加し、`codex-inline` を実 Skill tool 呼び出しと誤表現しない文言を明確化しました。
- specialist registry を project / user / skill/plugin manifest から自動 discovery し、project 側の `enabled: false` で user default を無効化できるようにしました。
- specialist provider schema が `kind: skill` と `kind: command`、first-use risk consent、command provider evidence invocation に対応し、`oracle` など特定 provider を mission core に hard-code せず扱えるようにしました。

## [1.0.3] - 2026-06-20

### 追加
- Phase 1 specialist selection checkpoint rollout 後に開始された session で selection metadata が欠落している場合、mission audit が可視化するようになりました。
- release 完了前に `git log <previous-tag>..HEAD --oneline` と英日 changelog entry を突合する手順を release checklist に追加しました。
- v1.0.2 の release theme が future changelog edit で欠落しないように documentation consistency test を追加しました。

### 修正
- v1.0.2 changelog entry に Phase 1 specialist selection checkpoint、specialist registry、file-overlap warning、audit CLI、GitHub Flow guidance、contributors、Reviewer/Scorer safeguards、audit diagnostics、Codex hook-packaging validation を追記しました。

## [1.0.2] - 2026-06-20

### 追加
- 任意の specialist registry を追加し、mission が task_profile を分類して利用可能な専門 skill を自動選定し、evidence provider として利用し、呼び出し証跡を記録できるようにしました。
- Phase 1 で mission state 初期化後に `specialists recommend --record-state --json` の結果を記録する specialist selection checkpoint を必須化しました。
- `mission-state.py init` に `--files` を追加し、別の active session と対象ファイルが重複する場合に警告できるようにしました。
- read-only な `scripts/mission-audit.py` CLI を追加し、local mission state の監査、self-improvement prompt 生成、forced/ungated pass、duplicate state、halt、slow session、low-score pass の bucket 可視化ができるようにしました。
- mission audit が nested worktree archive session を検出し、missing scoring evidence と specialist invocation gap を可視化するようになりました。
- slow session report に phase duration の観測可否 breakdown を分離して追加しました。
- issue 連携 mission、PR 本文の `Closes #N`、merge による issue 自動クローズを GitHub Flow として明文化しました。
- README に contributors と contribution type の表示を追加しました。

### 修正
- Reviewer / Scorer の安全策を強化し、merge-base 基準の diff 確認とテスト真正性チェックで誤った退行判定や浅いテスト検証を減らしました。
- 同一 logical mission run について、stale halt copy より完了済み pass/done record を優先して dedupe するようにしました。
- audit diagnostics が halt/incomplete の root cause、slow session bucket、low-score pass risk bucket を分類できるようにしました。
- Codex plugin の hook packaging contract が崩れた場合に release validation で検出できるようにしました。

## [1.0.1] - 2026-06-17

### 追加
- **Q11 – stagnation 自動カウント**: `push-score` で composite の改善幅 (`cur − prev`) が `[0, 0.1)` の場合に `stagnation_count` を自動インクリメント。後退（スコア低下）と初回 push は停滞と見なさず 0 にリセット。
- **S3 – 重複 issue-ref 警告**: `init` に `--issue-ref <ref>` オプションを追加。同プロジェクト内の active session に同一 `issue_ref` が存在する場合は stderr に `WARNING [S3]` を表示（reject しない）。同一 `session_id` での resume は自己検出として除外。

### 修正
- **Q11 後退ロジック修正**: 負の delta（スコア後退）が誤って stagnation として計上されていたバグを修正。条件を `0 <= delta < 0.1` に限定し、`_is_valid_composite()` による型チェックも追加。
- コピー配布用の Codex marketplace wrapper（`plugins/mission/`）を正典の `skills/` / `scripts/` と同期し、最新の stale auto-halt、High gate、stats、scoring rubric 修正を含めました。
- Codex wrapper が正典実装から drift した場合に失敗する回帰テストを追加しました。

## [1.0.0] - 2026-06-15

初の公開リリース。

### 追加
- ミッション・オーケストレーター skill と 5 つの補助 skill（planner / executor / reviewer / critic / scorer）。
- `.mission-state` セッション状態 CLI（`mission-state.py`）。Claude Code / Codex のマルチセッション分離に対応。
- スコア履歴とレビュー/critic ループを伴う閾値ゲート付き完了判定。
- ミッション実行中の早期終了を防ぐ Stop hook。stale-state のタイムスタンプ解釈は macOS（BSD `date`）と Linux（GNU `date`）の両対応。
- Claude Code プラグインメタデータとローカルプラグインマーケットプレイス manifest。
- Codex プラグインパッケージ（`plugins/mission/`）と skill symlink ガイド（Stop hook は opt-in）。
- 状態ルーティング・スコアゲート・hook 挙動をカバーする Python テストスイート。
- GitHub Actions CI（`push` / `pull_request` / `workflow_dispatch`）。pytest と ShellCheck を実行。

[2.7.0]: https://github.com/tackeyy/mission/releases/tag/v2.7.0
[2.6.0]: https://github.com/tackeyy/mission/releases/tag/v2.6.0
[2.5.0]: https://github.com/tackeyy/mission/releases/tag/v2.5.0
[2.4.0]: https://github.com/tackeyy/mission/releases/tag/v2.4.0
[2.3.0]: https://github.com/tackeyy/mission/releases/tag/v2.3.0
[2.0.0]: https://github.com/tackeyy/mission/releases/tag/v2.0.0
[1.2.0]: https://github.com/tackeyy/mission/releases/tag/v1.2.0
[1.1.1]: https://github.com/tackeyy/mission/releases/tag/v1.1.1
[1.1.0]: https://github.com/tackeyy/mission/releases/tag/v1.1.0
[1.0.7]: https://github.com/tackeyy/mission/releases/tag/v1.0.7
[1.0.6]: https://github.com/tackeyy/mission/releases/tag/v1.0.6
[1.0.5]: https://github.com/tackeyy/mission/releases/tag/v1.0.5
[1.0.4]: https://github.com/tackeyy/mission/releases/tag/v1.0.4
[1.0.3]: https://github.com/tackeyy/mission/releases/tag/v1.0.3
[1.0.2]: https://github.com/tackeyy/mission/releases/tag/v1.0.2
[1.0.1]: https://github.com/tackeyy/mission/releases/tag/v1.0.1
[1.0.0]: https://github.com/tackeyy/mission/releases/tag/v1.0.0
