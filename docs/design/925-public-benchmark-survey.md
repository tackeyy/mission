# #925 公開 benchmark 調査: 確認実験用 cohort の実現可能性

対象: [Issue 925](https://github.com/tackeyy/mission/issues/925)（親 [#884](https://github.com/tackeyy/mission/issues/884)、全体の親 [#876](https://github.com/tackeyy/mission/issues/876)）の分割。本書は文書のみで、実装・benchmark の実行・有料 model の呼び出しは行っていない。

**凍結済みの基準**（`docs/design/884-evaluation-aggregation.md` §5.1・§5.0・§6.3 より）:

- **独立単位 = upstream project**（1 project 1 task）。必要な K は §6.3 の見込みで Goal 失敗率の仮定ごとに **約 180（30%）/ 約 275（20%）/ 約 560（10%）/ 約 620（verified-complex の真の失敗率 1% を仮定する場合）**。
- **Lic**: benchmark のデータ license と各 upstream repository の license の両方が複製・実行・（bundle に含める場合は）保存を許す。
- **Det**: task ごとに evaluator 所有の fail-to-pass・pass-to-pass 検査と、固定された（pinned）評価環境があり、network を遮断してオフラインで再実行できる。
- **Cx**（複雑タスク。Cx1〜Cx4 すべて）: test 以外の source file 2 件以上変更、検査 3 件以上（うち fail-to-pass 1 件以上）、test 以外の変更行 10 行以上、課題文が upstream の自然な依頼に由来。
- **Con**（汚染）: pool の `task_id` と upstream の `owner/repo` が mission repository に文字列として現れない。
- **G1〜G3**（系譜）: 同一の正規化 identifier、共通の root commit（fork）、20% 以上の file 内容の重なり（vendoring）を持つ repository は 1 単位にまとめる。

**本書の調査範囲の限界**: web 検索 14 回・WebFetch 数回に限定した短時間調査であり、各候補の正確な repository 一覧や Cx/Lic/Det の実測フィルタは実行していない。数値は出典のある **検証済み事実** と、文献の統計から導いた **推定（estimate と明記）** を区別する。

---

## 1. 候補 benchmark 一覧

### 1.1 SWE-bench（オリジナル／Verified／Lite）

- **repository 数**: **12**（検証済み。2 つの独立検索で一致。[原論文](https://arxiv.org/pdf/2310.06770) Table 12 に license 一覧がある）。Verified（500 件）・Lite（300 件）はこの 12 repo のサブセットで、repo 数は増えない。
- **task 数**: full 2,294 / Verified 500 / Lite 300。
- **言語**: Python のみ。
- **license**: dataset 自体は公開 OSS（SWE-bench リポジトリは MIT。出典: HuggingFace/GitHub の一般的表記。本調査では個々の repo license を 1 件ずつは確認していない — **UNKNOWN**（ただし原論文が「使用許諾のある public repo のみ」と明記）。
- **Det**: あり。公式 `swebench` パッケージが各 instance に Dockerfile・FAIL_TO_PASS/PASS_TO_PASS を提供し、オフライン再実行が前提の設計。
- **control（正解開始）**: あり。gold patch（reference）がデータに含まれる。
- **汚染**: 最も強い懸念がある候補。2023 年公開のため多くの model の学習データに混入している可能性が高く、派生 benchmark（Verified・Live 等）はこの懸念への対策として作られた。
- **Cx 推定**: SWE-bench Verified の分析（Ganhotra のブログ、検証済み引用）で **500 件中 161 件（32.2%）が「1〜2 行・単一ファイルの trivial」** と報告されている。残り約 68% が Cx1/Cx3 の候補になりうるが、Cx2・Cx4 を考慮した正確な通過率は **推定**。
- **判定**: **repo 数 12 は 180 に遠く及ばず単独では使えない**。他候補との重複排除（G1〜G3）の基準点としては有用。

### 1.2 SWE-bench Multilingual

- **repository 数**: **41〜42**（検証済み。公式サイトは 9 言語・41 repo、HuggingFace の個票は言語別内訳の合計で同程度）。
- **task 数**: 300（9 言語: Ruby 44/6repo, Rust 43/7repo, PHP 43/4repo, Java 43/6repo, Go 42/5repo, C 30/4repo, JS 26/3repo, TS 17/4repo, C++ 12/2repo）。
- **license**: SWE-bench と同じ構築方式（**UNKNOWN**、個別確認なし）。
- **Det**: あり（SWE-bench と同じ Docker ベース評価）。
- **判定**: 41〜42 repo も 180 に届かない。SWE-bench 本体との重複（同一言語圏・類似収集手順）の有無は未確認。

### 1.3 SWE-Gym / SWE-Gym-Raw

- **SWE-Gym**: **11 repository**（検証済み）、2,438 instance、Python のみ、実行環境あり（Det 候補）。
- **SWE-Gym-Raw**: **358 repository**（検証済み。GitHub org 上では 341〜343 repo が公開）、64,689 instance だが **「実行環境なし」**（検証済み）= Det を満たさない。Det を満たすには環境構築が必要で、その作業量は **UNKNOWN**（本調査では見積もっていない）。
- **判定**: SWE-Gym 単独は 11 repo で不足。SWE-Gym-Raw は repo 数こそ 358 と豊富だが Det を満たさないため、そのまま使えない。

### 1.4 SWE-smith

- **repository 数**: **128**（検証済み。Python のみ）。
- **task 数**: 50,137（合成生成。1 repo あたり多数の task）。
- **実行環境**: 「repository 全体の実行環境を構築してから合成 task を生成する」（検証済み）ので Det の土台はある。ただし評価の決定性（flaky test の扱い等）は個別確認していない。
- **license**: 上位 PyPI パッケージ（ダウンロード数上位 5,000・star 1,000 超でフィルタ）から選定（検証済み）。個々の license は多様で、確認は未実施（**UNKNOWN**）。
- **判定**: 128 repo は 180 に対して **約 52 repo 不足**。SWE-Gym（11 repo、同じ Python 上位パッケージ圏）との重複は G1〜G3 で要確認。単独では不足だが、他候補と合わせる土台としては大きい。

### 1.5 Multi-SWE-bench

- **repository 数**: **39**（検証済み。arxiv HTML 本文「1,632 instance が 39 の diverse repository から」）。
- **task 数**: 1,632（2,456 候補から 68 名の専門家が精査）。
- **言語**: Java・TypeScript・JavaScript・Go・Rust・C・C++（Python を含まない 7 言語）。
- **license**: dataset は CC BY 4.0（検証済み、arxiv ページのヘッダー表示）。これは **データセット自体の license** であり、各 upstream repository の license（facebook・grpc・elastic 等が含まれる — GPL/Apache/MIT 混在と推定）は個別確認が必要（**UNKNOWN**）。
- **Det**: あり。PR ごとに Dockerfile を自動生成し、再現可能な実行環境を構築（検証済み）。
- **判定**: 39 repo。180 に対して大きく不足するが、非 Python 言語を増やす用途で他候補と組み合わせる価値がある。

### 1.6 SWE-PolyBench（Amazon）

- **repository 数**: **21**（検証済み）。
- **task 数**: 2,110（Java・JavaScript・TypeScript・Python の 4 言語。JS 1,017・TS 729・Python 199・Java 165）。
- **license**: **MIT**（検証済み。HuggingFace の AmazonScience 配下 3 データセットすべて）。
- **Det**: 明言した一次情報は未取得だが、VentureBeat の報道が「repository level 評価」と説明しており、構造的に SWE-bench 系と同様の評価環境を持つと推定（**UNKNOWN**、未検証）。
- **判定**: 21 repo。単独では不足。license が明確な点は他候補より扱いやすい。

### 1.7 SWE-rebench（Nebius）

- **repository 数**: **3,468**（検証済み。「21,336 件の verifiable task が 3,400 超の Python repository から」）。**本調査で確認した候補の中で最大の repo 数**。
- **task 数**: 21,336（継続更新。SWE-rebench V2 では言語を跨ぐ拡張も進行中だが詳細未調査）。
- **license**: dataset は **CC-BY-4.0**（検証済み）。各 instance が「commit 時点の各 repository の license」を保持している（検証済み）ため、Lic の機械判定に使える構造がある。ただし「license が複製・実行・保存を許すものだけに絞り込まれているか」は個別に再確認が必要（**UNKNOWN**。保持しているだけで除外フィルタの有無は未確認）。
- **Det**: 21,336 件中 **7,500 件に事前構築済み Docker image が公開**（検証済み）。残り約 13,836 件は image が無く、構築しなければ Det を満たさない。したがって **Det を満たす候補の repo 数は、7,500 件が何 repo 分かで決まる**（本調査では未算出。**UNKNOWN**、I2b の後続調査または pilot での実測が必要）。
- **汚染対策**: 「continuously updated」「decontaminated evaluation」を標題に掲げる（検証済み、詳細な手法は個別確認していない）。
- **Cx 推定**: 複雑度の分布は未調査（**UNKNOWN**）。SWE-bench 系と同様の収集手順（実際の GitHub issue/PR）のため、SWE-bench Verified の trivial 比率（32%）に近いと仮定すれば **約 60〜70% が Cx 候補**と推定できるが、確証はない。
- **判定**: **repo 数の天井は最も高いが、Det を満たす正確な repo 数が未確定**という最大の不確定要素を持つ。pilot での実測が必須。

### 1.8 SWE-bench-Live / SWE-bench-Live/MultiLang（Microsoft, NeurIPS 2025）

- **repository 数**: MultiLang で **431**（検証済み。2026-08-21 時点の web 情報）、Python 主系列は 2025-06-30 時点で 164 repo・1,565 instance（検証済み）、Windows 系列は 48 repo・66 instance（検証済み）。**継続的に毎月 Python task を約 50 件追加**（検証済み）。
- **言語**: MultiLang で 8 言語（C/C++, C#, Java, TypeScript/JavaScript, Go, Rust, + Python）。
- **license**: **MIT**（検証済み）。
- **Det**: あり。RepoLaunch という LLM エージェントが各 GitHub repository から「テスト可能な container 化環境」を自動生成し（検証済み）、"executable docker sandbox" で評価する。
- **汚染対策**: 評価時に agent へ `FAIL_TO_PASS`/`test_patch` 等の解答フィールドを渡さない設計、月次更新で leaderboard 比較用に分割（いずれも検証済み）。収集対象が「2024 年以降に作られた実際の GitHub issue」である点は、学習データのカットオフが古い model に対しては汚染リスクを下げる（ただし本調査の "Con" 基準＝ mission repository への文字列の非存在、とは別物）。
- **Cx 推定**: gold patch の統計が**検証済み**で取得できた唯一の候補: **median 2 files・3 hunks・24 lines**。これは Cx1（≥2 files）・Cx3（≥10 lines）の閾値とほぼ一致する境界値であり、「半数前後が通過」と推定するのが妥当（中央値が境界に乗っているため、ちょうど半数強が Cx1/Cx3 を満たすと推定）。別の分析（同じ調査で得た引用）では「単一ファイル・5 行未満の修正の成功率が 48%」であり、逆に言えば単一ファイル小修正が相当数含まれることも示唆する。
- **判定**: **431 repo は 180 を上回り 560 に近い**。license・Det・汚染対策が最も整備されている候補。Cx 通過率が約 50% だと仮定すると、Cx 後に残る repo 数は **約 215**（推定）で、180 はクリアできるが 560 には届かない可能性が高い。Lic（各 upstream の license）・Con（mission repo との文字列一致）・G1〜G3 でさらに減る。

### 1.9 SWE-bench Pro（Scale AI）

- **repository 数**: **41**（検証済み）。うち **public 11・held-out 12・commercial 18**。
  - public（11）: 完全に公開・OSS。**license は「強い copyleft（GPL 系）」**（検証済み）。GPL は複製・実行・保存を許すが、§5.1 の Lic 判定で「bundle に内容を含める場合はその保存を許す」の解釈次第で追加の法的検討が必要になりうる（**要確認。本調査では法的判断はしていない**）。
  - held-out（12）: leaderboard 不正防止のため非公開（検証済み。「held-out」の語義から推定、詳細未確認）。公開 benchmark として扱えるかは **UNKNOWN**。
  - commercial（18）: 18 社のスタートアップとの提携による非公開 proprietary codebase。**Lic・Con（契約上の秘密保持）の両方で使用不可の可能性が高い**。
- **Cx 推定**: SWE-bench Pro は明示的に「trivial（1〜10 行）を除外し、平均 **107.4 行・4.1 file** の修正のみを採用」（検証済み）。**この候補は Cx 通過率が最も高いと推定される**（ほぼ全件が Cx1・Cx3 を満たすと見込める）。
- **判定**: Cx の質は最良だが、確実に使える repo 数は public 11 のみ（held-out を含めても最大 23）で、180 には遠く及ばない。**複雑タスクの「質」の参考にはなるが、数の確保には使えない。**

### 1.10 その他見つけた候補（未深掘り・UNKNOWN）

検索で名前が挙がったが本調査では repo 数・license・Det を確認していない候補:

- **SWE-bench Multimodal**（視覚的ソフトウェア領域への一般化を検証。言語・repo 数未調査）
- **SWE-Bench++**（"Framework for the Scalable Generation of SE Benchmarks from Open-Source Repositories"。名称から本調査の目的に合致する可能性が高いが未調査）
- **GSO**（Challenging Software Optimization Tasks。難度は高いと推定されるが repo 数未調査）
- **SWE-Sharp-Bench**（Microsoft、C# 向け。repo 数未調査）
- **SWE-InfraBench**（クラウド infra コード向け。repo 数未調査）
- **DeNovoSWE**（repository を一から生成する系列で、本調査の「既存 upstream project」の枠組みと馴染むか不明）
- **nebius/SWE-bench-extra**（SWE-rebench の関連データセットらしいが repo 数未調査）

これらは K を 180 止まりではなく 275・560 まで伸ばす際の追加候補になりうるが、**本調査の web 検索予算（14 回）内では確認できなかった**。

---

## 2. drand randomness beacon（§5.0 の seed 取得元）

**chain（検証済み・2 系統が存在）**:

| chain | 周期 | mode | chain hash |
|---|---|---|---|
| League of Entropy default（mainnet） | 30 秒 | chained | `8990e7a9aaed2ffed73dbd7092123d6f289930540d7651336225dc172e51b2ce` |
| League of Entropy quicknet（mainnet） | 3 秒 | unchained | `52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971` |

- **運営者（threshold）**: League of Entropy は 2019 年に本番稼働を開始し、2020 年に 14 パートナーへ拡大した自発的コンソーシアム（検証済み）。quicknet は現在 **22 ノード中 12 の threshold**（検証済み。今後パートナー増加で threshold も上がる見込みと明記）。運営団体には Cloudflare・EPFL・Kudelski Security・Protocol Labs・Celo・UCL・UIUC 等が含まれる（検証済み）。
- **取得方法**: drand の公開 HTTP API（`docs.drand.love/developer/API-v2/`）から round 番号を指定して randomness を取得できる（API の存在は検証済み。具体的なエンドポイント URL・レスポンス形式は本調査では個票を確認していない — **UNKNOWN**、I2d 実装時に公式ドキュメントを読む必要あり）。
- **署名検証**: drand は閾値暗号（threshold cryptography・bilinear pairing）で各 round の randomness に署名し、公開鍵で第三者が検証できる（検証済み、一般的仕組み）。chain ごとの公開鍵の具体値は本調査では取得していない（**UNKNOWN**）。
- **§5.0 の要件への適合**: 「pool を固定した commit を時刻証明つきで公開した後に初めて値が分かる」という要件に対し、chained mainnet（周期 30 秒）・quicknet（周期 3 秒）のどちらも **運営者が公開前に round の値を知ることは（threshold 未満の共謀では）できない** 設計であり、要件を満たす候補として妥当（ただし脅威モデルの §5.0「残る信頼の前提」が明記するとおり、threshold 以上の共謀は排除できない。**UNKNOWN**）。

---

## 3. OpenTimestamps（§5.0 の時刻証明）

- **仕組み（検証済み）**: 対象 bytes の SHA-256 を取り、複数の hash を Merkle tree に集約し、root を Bitcoin トランザクションに刻む。calendar server はこの集約を代行するだけで、検証には **`.ots` proof と Bitcoin の block header chain だけ**が必要（calendar server や OpenTimestamps 自身を信頼する必要がない）。
- **Bitcoin の確定遅延（検証済み）**: 「典型的には 1〜2 時間」で block に取り込まれ確定する。
- **§5.0 の Δ（推奨 24 時間）との関係**: 1〜2 時間の確定遅延は Δ=24 時間に対して **十分な余裕がある**（確定遅延がさらに数時間ずれても 24 時間の margin を食い尽くす可能性は低いと推定できる）。ただし「Bitcoin の block 時刻が実時刻からどれだけずれうるか」の仕様上の上限（block timestamp の許容ずれ幅。Bitcoin protocol の `MAX_FUTURE_BLOCK_TIME` 等）は本調査では確認していない（**UNKNOWN**。884 本文も同じ点を UNKNOWN と明記しており、I2b の担当範囲として引き継がれている）。

---

## 4. 実現可能性のまとめ（単位数の見積り）

| 候補 | 検証済み repo 数 | 言語 | Det | license | Cx 通過率（推定） | 180 を単独で満たすか |
|---|---:|---|---|---|---|---|
| SWE-bench（full/Verified/Lite） | 12 | Python | ○（検証済み） | OSS（個別未確認） | ~68%（検証済み統計からの推定） | 否 |
| SWE-bench Multilingual | 41〜42 | 9言語 | ○（推定。SWE-bench と同方式） | 未確認 | UNKNOWN | 否 |
| SWE-Gym | 11 | Python | ○（検証済み） | 未確認 | UNKNOWN | 否 |
| SWE-Gym-Raw | 358 | Python | **×（検証済み。環境なし）** | 未確認 | UNKNOWN | 否（Det 不成立） |
| SWE-smith | 128 | Python | ○（検証済み。構築済み環境） | 未確認（多様） | UNKNOWN | 否（52 不足） |
| Multi-SWE-bench | 39 | 7言語（非Python） | ○（検証済み） | データ: CC BY 4.0／repo: 未確認 | UNKNOWN | 否 |
| SWE-PolyBench | 21 | 4言語 | 推定（未確認） | **MIT（検証済み）** | UNKNOWN | 否 |
| SWE-rebench | **3,468** | Python | 部分的（7,500/21,336 instance に image あり。repo数換算は UNKNOWN） | CC-BY-4.0（検証済み）＋per-repo license 保持 | UNKNOWN | **repo数は十分だが Det 母数が未確定** |
| SWE-bench-Live/MultiLang | **431** | 8言語 | ○（検証済み） | **MIT（検証済み）** | ~50%（推定。gold patch 中央値から） | **おそらく可（約215 推定、560には届かない）** |
| SWE-bench Pro（public のみ） | 11（held-out含め最大23） | 多言語 | ○（検証済み） | GPL系（検証済み。Lic判定は要確認） | 最高（検証済み統計） | 否 |

**180〜560 という要求数に対する結論**:

1. **単独で 180 を満たす確度が最も高いのは SWE-bench-Live/MultiLang（431 repo）**。license・Det・継続更新・decontamination 対策がすべて検証済みで揃っている。Cx 通過率を楽観的に 50% と見ても約 215 repo が残り、180 はクリアできる見込みだが、**Lic（upstream ごとの license 未確認）・Con（mission repo との文字列一致）・G1〜G3（系譜の重複排除）の減少分を考慮すると、余裕は大きくない**。560 には明らかに届かない。
2. **SWE-rebench（3,468 repo）は repo 数の天井としては唯一 560 を大きく超える候補**だが、**Det を満たす repo 数が「7,500/21,336 instance」から逆算できておらず未確定（最大の UNKNOWN）**。この数字が判明しない限り、560 を狙う根拠にできない。
3. **SWE-smith（128）・Multi-SWE-bench（39）・SWE-PolyBench（21）・SWE-bench Multilingual（41）を合算**すれば単純合計で約 229 repo になるが、**Python 上位パッケージ圏での重複（SWE-smith と SWE-Gym・SWE-bench-Live の Python 側）、license・Cx 通過率の未確認分を考えると、G1〜G3 適用後に 180 を割り込むリスクがある**。
4. **SWE-bench Pro の public 11 repo は Cx の質（平均107行・4.1ファイル）が最も高く、Cx の閾値設計を検証する際の参考データとして有用**だが、数の確保には使えない。

---

## 5. 推奨と owner が決めるべきこと

### 推奨

**第一候補は SWE-bench-Live/MultiLang（431 repo、MIT、Det あり、decontamination あり）を主要 pool とし、SWE-rebench の「Docker image 公開済み 7,500 instance」のうち何 repo 分に当たるかを次の調査ステップで確定したうえで、不足分の補完に使う二段構成を推奨する。** Multi-SWE-bench・SWE-PolyBench・SWE-bench Multilingual は、G1〜G3 の重複排除後に単位を上積みする第三の供給源として扱う。SWE-bench（12 repo）・SWE-Gym（11 repo）・SWE-bench Pro public（11〜23 repo）は数が小さすぎて単独では使えないが、汚染対策・Cx 品質の参考データとして残す。

### 180 / 560 とのギャップ

- **K=180（Goal失敗率30%想定）**: SWE-bench-Live/MultiLang 単独（431 repo）で、Cx・Lic・Con・G1〜G3 を通過する実測値が **repo数の約42%以上**残れば届く。現時点の推定（50%）はこのラインの近傍にあり、**pilot での実測なしに K=180 を保証できない**。
- **K=560（Goal失敗率10%想定）**: SWE-bench-Live/MultiLang 単独では届かない可能性が高い（431 repo の全件が通過しても 560 には届かず、通過率を考慮すれば更に厳しい）。SWE-rebench の Det 母数が判明し、かつそこから十分な数が Cx・Lic・Con・G1〜G3 を通過しない限り、560 は現時点では **達成の見込みが立たない**。
- **K=620（verified-complex の真の失敗率を1%と仮定する場合）**: 580 本文に記載の通りさらに厳しく、現状確認した候補の中で単独達成可能なものは無い。

### owner が決めるべきこと（ギャップが埋まらない場合の選択肢）

1. **複数 benchmark を合算する**: SWE-bench-Live/MultiLang を主、SWE-rebench（Det母数確定後）・Multi-SWE-bench・SWE-PolyBench・SWE-bench Multilingual を副として合算し、G1〜G3 の重複排除後に K を確定する。この場合、`report_kind=confirmatory` の母集団が複数 benchmark の混合になることを事前登録に明記する必要がある（884 §5.1 は「どの benchmark を選ぶかは owner が選ぶ」としているが、複数選択を想定した記述は無く、**本書の範囲外の追加決定が必要**）。
2. **K を小さく（180 付近）に抑え、Goal 失敗率が高い（30%程度）前提で確認実験を行う**: この場合、verified-complex の真の失敗率が 1% 程度ある場合（K=620 相当）の検出力は確保できない。「検出できなかった」ことを「verified-complex が十分良い」と読み替えない運用上の注意が必要（884 §3.2 の estimand の限定と整合）。
3. **ギャップが埋まらないと判断し、確認実験に進まず #884 を「未検証」のまま終える**（884 §6.3 の手順4「pool の単位数が足りない場合は確認実験に進まず owner へ上げる」に該当）。この場合、品質改善の主張は H（開発用 12 件）の診断結果に限られ、母集団への一般化は行わない。

### 明示する UNKNOWN（本書で確認できなかった事項）

- 各候補の **upstream repository 個々の license**（SWE-bench系・Multi-SWE-bench・SWE-PolyBench・SWE-rebench のいずれも、dataset licenseと個別 repo license を網羅的に照合していない）。
- SWE-rebench の **7,500 件の pre-built Docker image が何 repo 分に相当するか**。
- 各候補の **Cx1〜Cx4 通過率の実測値**（SWE-bench-Live の gold patch 統計以外はすべて推定）。
- **G1〜G3 を候補間（特にPython系: SWE-bench/SWE-Gym/SWE-smith/SWE-bench-Live/SWE-rebench）に適用した際の重複排除後の正確な単位数**。
- drand の **具体的な公開鍵値とAPI endpoint の仕様**（I2d 実装時に公式ドキュメントで確認する必要がある）。
- Bitcoin block timestamp の **実時刻からのずれの仕様上の上限**（884 本文も同じ点を UNKNOWN としており、Δ=24h の十分性の最終判断に必要）。
- repository の管理者が merged PR を削除できるか、fork・GH Archive 等の外部写しで main の履歴を照合できるか（884 §5.0 が I2b に要求した項目だが、本書では調査していない。**次の調査ステップとして持ち越す**）。
- 全単位の組を比較する G1〜G3 の計算量と clone の容量（884 §5.1 が I2b に要求した項目だが、本書では見積もっていない）。
- Det の全件再実行に要る計算量（監査標本への切替が必要かの判断材料。本書では見積もっていない）。

---

## 出典（本文中で参照した URL・アクセス日はすべて 2026-10-04）

- [SWE-bench: Can Language Models Resolve Real-World GitHub Issues?](https://arxiv.org/pdf/2310.06770)
- [SWE-bench repositories · GitHub](https://github.com/orgs/swe-bench/repositories)
- [FAQ - SWE-bench](https://www.swebench.com/SWE-bench/faq/)
- [SWE-bench/SWE-bench_Multilingual · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-bench/SWE-bench_Multilingual)
- [SWE-Gym/SWE-Gym · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-Gym/SWE-Gym)
- [SWE-Gym-Raw · GitHub](https://github.com/SWE-Gym-Raw)
- [SWE-smith: Scaling Data for Software Engineering Agents (arxiv 2504.21798)](https://arxiv.org/pdf/2504.21798)
- [SWE-bench/SWE-smith · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-bench/SWE-smith)
- [Multi-SWE-bench: A Multilingual Benchmark for Issue Resolving (arxiv 2504.02605 HTML)](https://arxiv.org/html/2504.02605v1)
- [Multi-SWE-bench · GitHub](https://github.com/multi-swe-bench)
- [GitHub - amazon-science/SWE-PolyBench](https://github.com/amazon-science/SWE-PolyBench)
- [AmazonScience/SWE-PolyBench · Datasets at Hugging Face](https://huggingface.co/datasets/AmazonScience/SWE-PolyBench)
- [nebius/SWE-rebench · Datasets at Hugging Face](https://huggingface.co/datasets/nebius/SWE-rebench)
- [SWE-rebench dataset blog (nebius.com)](https://nebius.com/blog/posts/swe-rebench-dataset)
- [GitHub - microsoft/SWE-bench-Live](https://github.com/microsoft/swe-bench-live)
- [SWE-bench-Live/SWE-bench-Live · Datasets at Hugging Face](https://huggingface.co/datasets/SWE-bench-Live/SWE-bench-Live)
- [SWE-Bench Pro: Can AI Agents Solve Long-Horizon Software Engineering Tasks? (arxiv 2509.16941)](https://arxiv.org/html/2509.16941v2)
- [SWE-Bench Pro: Raising the Bar for Agentic Coding (scale.com)](https://scale.com/blog/swe-bench-pro)
- [Jatin Ganhotra: SWE-bench Verified easy/medium/hard](https://jatinganhotra.dev/blog/swe-agents/2025/04/15/swe-bench-verified-easy-medium-hard.html)
- [SWE-bench Goes Live! (arxiv 2505.23419)](https://arxiv.org/html/2505.23419v2)
- [drand: quicknet is live on the League of Entropy mainnet](https://docs.drand.love/blog/2023/10/16/quicknet-is-live/)
- [drand Explained](https://docs.drand.love/about/)
- [drand HTTP API](https://docs.drand.love/developer/API-v2/drand-http-api/)
- [OpenTimestamps: a step-by-step tutorial](https://www.dgi.io/ots-tutorial/)
- [Timestamps Without Trust: How OpenTimestamps Democratized Cryptographic Proof of Existence](https://stampd.org/opentimestamps-trustless-proof/)

(本書作成に使用した web 検索は 14 回。候補一覧に名前のみ挙がった SWE-Bench++・GSO・SWE-Sharp-Bench・SWE-InfraBench・DeNovoSWE は検索予算の制約で深掘りしていない。)

## orchestrator による一次確認（2026-10-04）

- SWE-bench-Live の README（[microsoft/SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live)、2026-09-25 時点の main）で、MultiLang が 2026-08-21 時点で 1,077 task・431 repository・8 言語であること、評価 code の license が MIT であることを確認した。dataset 自体の license と、各 upstream repository の license（Lic の判定対象）は未確認で、I2c・I2d の機械判定で確かめる。
- 同 README は、agent の prompt・skill に task 固有の解を含めないこと、`problem_statement` 以外の field（`FAIL_TO_PASS`・`test_patch` 等）に触れないことを定めている。これは事前登録の worker と evaluator の分離と矛盾しない。
