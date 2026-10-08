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

Parallel child work uses `mission-state.py parallel-init --group-id <opaque> --issue-ref <ref>`
followed by child `init --logical-group-id <opaque>`. Use `parallel-status` before
`parallel-closeout`; closeout requires every planned child terminal and no active lease.
Status classifies each planned child as planned, running, waiting, pass, or halt and reports
artifact, activity, and review-provenance coverage. Unplanned or duplicate children are
fail-closed; a rejected closeout does not rewrite the manifest.

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

## State capacity and manual recovery

State writes reserve space for unfinished review items, one halt slot, and the
remaining bounded lease takeovers within the 4 MiB limit. `fresh-review status`
reports `capacity`: the `limit`, the actual `encoded_len`, item `reserved`
bytes, `system_remaining`, `headroom`, `excess_bytes`, `remaining_takeovers`,
`mode` (`normal`, `excess` or `legacy-full`), `code` (for example
`state-capacity-exhausted` in `excess` mode), and pending
`withdraw_candidates`. Reading status does not acquire a lease or write state.
For a v5 session it still needs the current holder's lease token
(`MISSION_LEASE_ID`) while the lease is live; after the lease expires it can be
read without one. A lease with no remaining takeover allowance reports zero;
attempts to add another takeover are rejected with `state-capacity-exhausted`
even when physical bytes remain.

In this version an over-capacity session can write only stopping mutations
(halt). The capacity-aware withdrawal command is tracked in Issue #912 and is
not yet available. Legacy pretty JSON cannot advance a D item beyond pending.
Reservations are derived from state; no capacity marker is saved.

`state-capacity-legacy-full` means even replacing every pending request with a
smaller withdrawn record cannot leave room for the halt slot. For a session
that has not stopped, every mutation is refused, including halt, takeover,
resume and reinitialization. A session that has already stopped (v4
`loop_active` false, or a terminal v5 session) and holds no `fresh_review`
records can be replaced in place by `init` (v5: `init --new-mission`); the
replacement is judged on its own capacity and the old state or generation is
moved to the archive directory. A session that holds `fresh_review` records
(including pending requests) refuses reinitialization even after it stops. In
every other case the owner must close the legacy-full session manually:

1. Stop its agent/writer processes and inspect `fresh-review status`. Keep the
   original session read-only; do not truncate history or edit request nonces.
2. Make an offline archive of the entire `.mission-state` directory, including
   session heads, objects, generations, commits, operations and evidence. Also
   archive the repository artifacts that the evidence refers to outside
   `.mission-state` (artifact and publication paths recorded in the state), and
   keep the Git objects they reference reachable. Verify the archive's bytes
   against the original before changing any location. A standalone head JSON is
   insufficient for a fenced session.
3. Preserve the archived session as stopped/unresolved, and record why it was
   closed in the owner's operational record. An archive is not a successful
   mission or fresh-review receipt.
4. Use a separate working directory with a fresh state store and a distinct
   session ID. Initialize with `MISSION_SESSION_ID=<new-id>` and the normal
   `mission-state.py init` command; keep the old store and working directory
   read-only until the archive is verified. Re-establish requirements and
   acceptance evidence; do not copy the old lease or claim its unfinished work
   completed. Retain the archived lineage.

`state-capacity-invariant-broken` rejects a write that breaks a reservation or
stored-field bound; `state-capacity-withdraw-not-needed` refuses withdrawal of
an in-capacity request. These codes do not authorize discarding evidence.

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
