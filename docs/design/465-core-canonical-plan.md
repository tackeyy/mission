# Design: core planning の canonical plan 登録経路（Issue #465）

## 目的

`planning_policy_version=1` かつ core planning（`planning_strategy` が `None` / `"core"`）のセッションで `advance --phase executing` が canonical plan 不在により恒久的に失敗する問題を解消する。`next` が返す `command_sequence`（`plan-inline` → `advance --phase executing`）を実際に完走できる状態にする。

## 背景（現状の構造）

- `canonical_plan` を書き込むのは `cmd_planning_promote_provider_plan` の 1 箇所のみ。入口で `planning_strategy != "provider-primary"` を拒否する
- `advance --phase executing` のゲートは `planning_policy_version == 1` だけを条件に canonical plan を要求し、strategy を見ない
- 一方で受け入れ側の設計は既に非 provider 経路を想定している:
  - `_trusted_canonical_plan_binding` は `source != "provider"` のとき `planning_source_records[f"{source}:{source_id}"]` を参照する else 分岐を持つ
  - `derive_planning_lifecycle` は `canonical_plan` があれば strategy によらず `run-executor` を返し、`strategy == "core"` かつ canonical plan 不在なら `run-planner` を返す
  - 既存テスト `test_planning_provider_lifecycle.py::_canonical_core_state` は `source: "core"` + `planning_source_records["core:planner-1"]` の状態を組み立てている

つまり**書き込み経路だけが欠けている**。本 Issue はその欠落を埋めるものであり、ゲートを緩める方向の変更ではない。

## スコープ

やること:

- 新サブコマンド `planning adopt-core --input <plan-document.json>` の追加（`skills/mission/bin/mission-state.py`）
- 受理した plan document を既存の `_validate_document` で検証し、canonical bytes を `.mission-state/plans/<digest16>.json` へ publish、`canonical_plan` と `planning_source_records["core:<source_id>"]` を state へ記録
- 失敗時ガイダンス（`_raise_guided_failure` / HINT）を core planning の実情に合わせて更新する。現行 HINT が案内する `planning reselect` / `plan-import` は core planning では成立しないため、`adopt-core` を案内に含める
- `derive_planning_lifecycle` の core 経路の `next_action` を、計画未登録時に `adopt-core` へ誘導できる形にする（後述）
- テスト `skills/mission/tests/test_issue465_core_canonical_plan.py` 新規
- docs: 本設計書 + CHANGELOG（EN/JA）追記

やらないこと:

- `advance` のゲート自体の緩和（`planning_policy_version == 1` で canonical plan を要求する規律は維持する）
- provider 経路（`plan-import` / `promote-provider-plan`）の挙動変更
- `mission-plan/1` document schema の変更
- executor_handoff の schema 変更
- scoring / review / mark-passes 経路への変更

## インターフェース定義

### CLI

```
mission-state.py planning adopt-core --input <path> [--source-id <id>] [--json]
```

- `--input`: `mission-plan/1` の **document 本体**（`objective` / `scope` / `assumptions` / `steps` / `global_acceptance` / `stop_conditions`）を持つ JSON regular file。provider result envelope（`mission-provider-result/1`）ではない
- `--source-id`: 省略時は `core-<iteration>-<12 hex>` を生成する。同一 iteration 内で再登録する場合に世代管理へ使う
- 成功時 stdout: `{"ok": true, "canonical_plan": {...}}`

### 受理条件（すべて満たすときのみ登録・いずれか欠ければ fail-closed）

1. `planning_policy_version == 1`
2. `phase == "planning"`
3. `planning_strategy` が `None` または `"core"`（**`provider-primary` は拒否**。required provider の迂回路を作らない）
4. `planning_provider_required is not True`（provider 必須宣言があるセッションでは core 採用を許さない）
5. `--input` が strict に読める regular file であり、`_validate_document` を通過する（reserved field injection ガードを含む）

### 状態遷移

```python
document = _validate_document(_strict_load(raw), workspace=cwd)
candidate = {"schema": "mission-plan/1", **document, "mission_metadata": {
    "authority": {"owner": "mission", "may_write_state": False, "may_decide_review": False,
                  "may_decide_score": False, "may_decide_completion": False},
    "provenance": {"source": "core", "source_id": source_id, "iteration": iteration,
                   "raw_document_digest": digest},
    "capability_verification": {"selection_verified": False, "class_exact_match": False,
                                "variant_exact_match": False},
}}
canonical = canonical_plan_bytes(candidate)
# .mission-state/plans/<canonical_digest[7:23]>.json へ publish（provider 経路と同じ命名規約）
plan = {"schema": "mission-plan/1", "path": <relative>, "digest": <sha256 ref>,
        "source": "core", "source_id": source_id, "source_digest": <input raw digest>,
        "selection_source": "core", "iteration": iteration,
        "generation": <既存 record があれば +1、無ければ 1>, "validated_at": iso_now()}
canonical_plan_identity(cwd, plan, reader=_read_strict_review_file)   # 自己検証
data["canonical_plan"] = plan
data["planning_source_records"][f"core:{source_id}"] = {k: plan[k] for k in
    ("generation", "source", "source_id", "selection_source", "iteration")}
```

`planning_source_records` に書くキー集合は `_trusted_canonical_plan_binding` の else 分岐が読む 5 キーと厳密に一致させること。ここがずれると `advance` が `canonical-plan-<key>-mismatch` で落ちる。

### derive_planning_lifecycle の変更

現行は core かつ canonical plan 不在で `run-planner` を返す。これは「計画を作れ」までしか言わず、作った計画の登録手段を案内しない。`run-planner` の意味は保ちつつ、`next` 側の `command_sequence` に `planning adopt-core` を挟む。

- `derive_planning_lifecycle` の戻り値自体は変更しない（`run-planner` のまま。既存テストの期待値を壊さない）
- `next` の `command_sequence` 生成側で、`planning_policy_version == 1` かつ strategy が core 系のときに `mission-state.py planning adopt-core --input <plan.json>` を `advance --phase executing` の直前へ挿入する

## 受け入れ条件

- AC-1: core planning（strategy が `None` / `"core"`）のセッションで `planning adopt-core --input <valid document>` → `advance --phase executing` が成功し、`executor_handoff` が `plan_source: "core"` で生成される
- AC-2: `planning_strategy == "provider-primary"` のセッションで `adopt-core` が拒否される（provider 経路の迂回不可）
- AC-3: `planning_provider_required is True` のセッションで `adopt-core` が拒否される
- AC-4: 不正な plan document（steps 空・依存循環・未知依存・reserved field 混入・非 canonical JSON）が拒否され、state が変更されない
- AC-5: `advance --phase executing` の失敗ガイダンスが、core planning では `planning adopt-core` を案内する（`planning reselect` / `plan-import` 単独の案内を残さない）
- AC-6: `next` の `command_sequence` に `planning adopt-core` が含まれ、案内どおり実行して executing へ到達できる
- AC-7: 既存テスト全緑（`make test`）。provider 経路のテストは無変更で通る

## テストリスト

`skills/mission/tests/test_issue465_core_canonical_plan.py`

1. core strategy で adopt-core → canonical_plan と planning_source_records が期待形で書かれる
2. adopt-core 後に advance --phase executing が成功し executor_handoff.plan_source == "core" かつ step_ids が document の steps 順と一致する
3. provider-primary strategy では adopt-core が非 0 終了し state が不変
4. planning_provider_required=True では adopt-core が非 0 終了し state が不変
5. 不正 document 各種（steps 空 / 依存循環 / 未知依存 / reserved field / 非 UTF-8 / 巨大ファイル）で非 0 終了し state が不変
6. 同一 iteration で 2 回 adopt-core すると generation が 1 → 2 へ上がり、canonical_plan が最新を指す
7. adopt-core 直後の `next` が `run-executor` を返す
8. 登録した plan ファイルを改竄すると advance が `canonical-plan-digest-drift` で失敗する（自己検証が効いている）

## 非機能・規約

- OSS ポータビリティ（AGENTS.md）: 個人名・home path・私有 skill 名をコード・テスト・ドキュメントへ入れない。テストは中立な fixture 名を使う
- ベンダー固有語を導入しない（`test_vendor_fingerprint.py` が全 tracked file を走査する）
- 出力に lease token・credential を含めない
