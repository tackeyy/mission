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
- A mechanical comparison against the deleted `HEAD` fixtures passed: spawn union matched all 197 candidates including `count`, design `rows`, and `reason`; writer union matched all 83 modules and 449 calls. The conversion only repartitions approved entries and adds no allowlist or classification.

## Targeted verification

`/tmp/mission-995-fuzz.py`による独立探索は100入力（生成94件とAST・非干渉検査）を実施した。従来の全検査を通過した5件は、spawnの`count: true`、spawnの`rows: [true]`、writerの`count: true`、各manifestのJSON重複キーだった。新しい回帰テストはloader修正前に3件がRedとなり、厳密な`int`判定と`json.loads(..., object_pairs_hook=...)`による重複キー拒否後にGreenとなった。

`python3 -m pytest skills/mission/tests/test_issue921_spawn_inventory.py skills/mission/tests/test_issue918_writer_inventory.py skills/mission/tests/test_codex_wrapper_sync.py skills/mission/tests/test_artifact_hygiene.py -q` passed: 583 passed in 22.42s.

削除前の`HEAD` fixtureとの変更後union比較も再度成功した。spawnの197候補metadataとwriterの83modules/449callsが完全一致した。`git diff --check`も成功した。対象検査はASTinventory、malformed/misplaced/unrecognized/duplicate fragmentの拒否、配布wrapper同期、tracked artifact hygieneを含む。full suite実行は本実装タスクの対象外である。
