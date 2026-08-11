# Reusable state snapshots

`mission-audit.py` captures an immutable snapshot by default. It stores the
content-addressed `snapshot_id` below the first root at
`.mission-state/audit-snapshots/<snapshot_id>.json`, and includes the ID and
digest in both Markdown and JSON reports. Reaggregate that frozen payload after
current state changes with `--from-snapshot <snapshot_id>`:

```bash
python3 scripts/mission-audit.py --root /path/to/projects --json
python3 scripts/mission-audit.py --root /path/to/projects --json --lineage
python3 scripts/mission-audit.py \
  --root /path/to/projects \
  --from-snapshot <snapshot_id> \
  --json
```

Default audit statistics use only the latest unambiguous review generation.
Use `--lineage` when raw review generations and explicit host/root/parent/child
correlation resolution are needed; it never guesses missing links.

`--privacy` replaces configured root prefixes in Markdown and JSON output with
anonymous `root-N` labels. Its default snapshot persists the same anonymous
root IDs, root-inventory content digests, canonical root identity digests, and
relative locators instead of source paths. On `--from-snapshot`, the requested
root identities must match; the roots then rehydrate locators only in memory,
and current state is not reread.

An explicit portable snapshot is still available for strict live-freshness
checking and later audit or `stats` windows:

```bash
python3 scripts/mission-audit.py \
  --root /path/to/projects \
  --snapshot-out /tmp/mission-state.snapshot.json \
  --snapshot-ttl-sec 300 \
  --json

python3 scripts/mission-audit.py \
  --snapshot-in /tmp/mission-state.snapshot.json \
  --since 2026-07-01 \
  --json

python3 skills/mission/bin/mission-state.py stats \
  --snapshot /tmp/mission-state.snapshot.json \
  --since 2026-07-01 \
  --json
```

`--snapshot` is an alias of `--snapshot-out`. Its output path must be outside
every scanned root. The command does not add snapshots to Git or another
artifact store; the default state-local directory is excluded from state
discovery so audit output cannot become audit input.

## Correctness contract

The snapshot stores every parsed record before period filtering or deduplication.
Each audit or stats invocation applies its own period filter and then deduplicates,
so a higher-ranked record outside a requested window cannot hide a record inside
that window. It also stores the ordered root multiset, record identity/index,
record and discovery counts, schema/CLI/record/discovery/dedupe contract versions,
invalid archive inventory, and one content digest.

`observed_at` freezes time-dependent health classification for every consumer.
`created_at` is the wall-clock cache age used with `ttl_seconds`; production
captures normally place the two timestamps close together, while deterministic
audit clocks may intentionally differ. Both timestamps must include a timezone.

Capture uses the audit's hardened archive discovery and semantic manifest
validation. It inventories each traversed directory and every `.mission-state`
file with path/type/device/inode/mode/size/mtime/ctime metadata. Scoring and
specialist evidence candidates outside the roots are inventoried separately,
including paths that do not yet exist. Capture repeats this metadata inventory
before the atomic write and rejects concurrent drift. The snapshot stores the
inventory count and digest, the external candidate paths needed to reproduce
it, and each record's source entry instead of duplicating the complete inventory.

`--snapshot-in` performs one metadata-only rewalk plus `lstat` checks for
external evidence. It does not reread, rehash, or reparse state/evidence
content. Only after the metadata matches exactly does it reuse the captured
semantic archive validation. State, directory, pointer, manifest, evidence,
legacy candidate, or generation changes therefore make that strict snapshot
stale.

`--from-snapshot` instead verifies the snapshot payload, digest, expiry, and
requested ordered roots, then aggregates the frozen payload without consulting
mutable current state. This is the reproducibility mode: later current-state
changes cannot alter past totals or findings.

Snapshots are written through a unique same-directory temporary file, mode
`0600`, file `fsync`, atomic replace, and directory `fsync`. Consumers reject
symlinks, non-regular files, group/world-readable files, expired/future
snapshots, and root/version/count/index/digest mismatches. Strict
`--snapshot-in` consumers also reject stale discovery. An invalid snapshot
never falls back to a live scan.

A snapshot is a local, owner-controlled trusted artifact, not an authenticated
exchange format. Mode `0600`, content digest checks, semantic self-consistency,
and live metadata freshness detect accidental or partial modification. Without
an authentication key they cannot protect against a malicious owner who rewrites
every related field and recomputes the digest. Do not accept snapshots from an
untrusted user or transport.

## Performance scope

The optimization removes repeated state/evidence byte reads, content hashing,
JSON parsing, and archive semantic validation from snapshot consumers. One
metadata rewalk remains mandatory for freshness. Period filtering and group
construction also remain per consumer because filter-before-dedupe is a
correctness requirement. Performance claims must be based on a representative
benchmark; the feature does not claim to eliminate filesystem traversal.

The final local benchmark for this implementation used a synthetic fixture of
80 projects, 660 state variants, 640 evidence files, and 3,200 unrelated files
on a warm APFS filesystem. After two warmups, 14 counterbalanced AB/BA runs
compared direct audit plus three direct stats windows with snapshot-out audit
plus three snapshot stats windows. All four JSON outputs matched in every run.
The direct median was 0.4515 s (MAD 0.0028 s); the snapshot median was 0.7039 s
(MAD 0.0050 s), or 1.56x slower. The snapshot was 1,223,615 bytes and represented
3,081 discovery entries.

Therefore this release does not claim an end-to-end speed improvement. Its
measured benefit is reproducible multi-window analysis with live-drift rejection,
while regression counters confirm that consumers skip candidate loading, state
and evidence content reads/hashes/parses, and archive semantic validation. The
benchmark is synthetic and warm-cache, so it does not establish cold-disk or
other-filesystem behavior. A future speed-oriented design should evaluate a
single-process batch command that validates once and emits all requested windows,
without weakening the freshness contract.
