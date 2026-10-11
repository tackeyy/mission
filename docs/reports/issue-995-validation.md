# Issue 995 validation

## Test list

- [x] Fragment loader rejects a missing module inventory through the existing exact-inventory equality check.
- [x] Fragment loader rejects an extra, duplicate, or malformed fragment.
- [x] Spawn inventory retains every candidate count, design row, and reason.
- [x] Writer inventory retains every candidate count.
- [x] A newly introduced spawn or writer remains an inventory mismatch until a reviewer updates its module fragment.
- [x] Boolean values and duplicate JSON keys cannot bypass fragment validation.
- [x] Canonical skill changes are synchronized to the distributed plugin wrapper.

## Baseline

`python3 -m pytest skills/mission/tests/test_issue921_spawn_inventory.py skills/mission/tests/test_issue918_writer_inventory.py -q` passed: 561 passed in 23.53s.

## Red

`python3 -m pytest skills/mission/tests/test_issue921_spawn_inventory.py -q` failed: 3 failed, 429 passed. The failure was `No spawn inventory fragments` after the loader required module fragments.

`python3 -m pytest skills/mission/tests/test_issue918_writer_inventory.py -q` failed: 1 failed, 136 passed. The failure was `No writer inventory fragments` after the loader required module fragments.

## Green

- Spawn inventory: 49 module fragments replaced the shared fixture; `432 passed in 7.16s`.
- Writer inventory: 83 module fragments replaced the shared fixture; `137 passed in 3.20s`.
- The loaders accept only a fragment at the path derived from its declared module, require nonempty valid metadata, and compare the reconstructed inventory exactly to AST results. A changed AST result still fails until a reviewer explicitly updates that module's fragment; no generator is added.
- A mechanical comparison against the deleted `HEAD` fixtures passed: spawn union matched all 197 candidates including `count`, design `rows`, and `reason`; writer union matched all 83 modules and 449 function/kind entries. The conversion only repartitions approved entries and adds no allowlist or classification.

## Main integration

- Source inventory: `origin/main` at `f66e778c568142ab7fbeb00e1c5cf12d951a4b5f`.
- The source additions are preserved in the module fragments: spawn `lib/ci_suite_evidence.py:verify_ci_suite:run` has count `13`, rows `[15]`, and its source reason; writer `lib/ci_suite_evidence.py:_normalise_log:replace` has count `1`.
- Loading the fragments and comparing their unions with the source inventories is exact: spawn has 198 candidates with all count/rows/reason metadata equal, and writer has 84 modules with 450 function/kind entries (599 total occurrences) equal. The existing AST inventory tests validate the same reconstructed fragments against the current source.

## Targeted verification

一時スクリプトによる独立探索は100入力（生成94件とAST・非干渉検査）を実施した。従来の全検査を通過した5件は、spawnの`count: true`、spawnの`rows: [true]`、writerの`count: true`、各manifestのJSON重複キーだった。新しい回帰テストはloader修正前に3件がRedとなり、厳密な`int`判定と`json.loads(..., object_pairs_hook=...)`による重複キー拒否後にGreenとなった。

`python3 -m pytest skills/mission/tests/test_issue921_spawn_inventory.py skills/mission/tests/test_issue918_writer_inventory.py skills/mission/tests/test_codex_wrapper_sync.py skills/mission/tests/test_artifact_hygiene.py -q` passed: 583 passed in 22.42s.

削除前の`HEAD` fixtureとの変更後union比較も再度成功した。spawnの197候補metadataとwriterの83modules/449entriesが完全一致した。`git diff --check`も成功した。対象検査はASTinventory、malformed/misplaced/unrecognized/duplicate fragmentの拒否、配布wrapper同期、tracked artifact hygieneを含む。上記の検証ではfull suiteは未実行。CIとmerge gateの結果はPRへ記録する。

## Latest main targeted verification

- 最新`origin/main`の共有inventoryをsourceとして、fragment unionを直接比較した。spawnは198候補の`count`・`rows`・`reason`が完全一致し、writerは84modules/450entries（総count599）が完全一致した。
- `test_issue921_spawn_inventory.py`、`test_issue918_writer_inventory.py`、`test_codex_wrapper_sync.py`を実行し、ASTからの現在ソース照合とdistributed plugin wrapper同期が成功した。
