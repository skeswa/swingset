# 9. After the switch

Read [8. Stage 3](8-stage-3.md) first. This is the last page.

Each item is its own production change, with its own receipts, in any
order unless stated. None blocks the others, and none has a date.

## Storage and backups

1. **A second provider** for the backup bucket, filled by a separate job
   with its own keys; the worker cannot delete anything there.
2. **Files into object storage** ([4. Hosting](4-hosting.md#files-in-object-storage)):
   - price it (storage, uploads, the second copy);
   - turn on the storage interface: write-once uploads, a checked read, a
     size-limited local cache, a rule for every top-level state file;
   - copy about 150,000 files plus the candidates with a resumable copier,
     recording each key and hash, then switch reads to the bucket;
   - delete the local copies once a restore drill from the buckets passes,
     with an independent check.
3. **The full rebuild drill:** restore the latest backup into a scratch
   database, re-derive everything, and build a release; it must match the
   live one, except named columns. Record its time: it is the real recovery
   time.
4. **Delete the Mac copies,** the local checkpoints and the VM, with an
   independent check, once the rebuild drill passes. Until then they are
   the fallback.

## Decisions into `gov`

5. **Overrides move into `gov` together.** All eight override tables switch
   in one deploy. The one-time old-to-new hash map is a re-key, so nothing
   re-runs ([2. Schema](2-schema.md#gov-decisions-as-rows)).
6. **Scraping and safety rules, holds and exceptions next** ([2. Schema](2-schema.md#scraping-and-safety-rules-as-rows)): `gov.sources`, `gov.host_policy`, `gov.fetch_rules` and `gov.settings` replace `hosts.toml`, the policy parts of `sources.toml`, the request floor and User-Agent constants, and the publish switch. Exceptions are the decisions that
   let something through or keep it out: finding dismissals, admission and
   contract reviews, retention holds, approved release decreases and
   requeues.
7. **The approval tables last:** contract policies, year acceptance and
   pauses. Their stored hashes are checked on every run, so each gets a
   planned hash migration under a hold; where the hashed bytes are
   unchanged, it is a re-key.

## Queryable data

8. **The catalog fills** from published releases, newest first, after a
   measurement of how many published rows change between releases fixes
   its version layout.
9. **An API** is separate work, decided by the owner when the catalog is
   ready. Until then the owner queries with SQL through the SSH tunnel.

## Hardening and speed

10. **The remaining hard-case tests:** recreating containers on the same
    volumes, a Postgres restart and minor upgrade (with the major-upgrade
    path written down), losing the backup connection partway, a crash
    between restore steps, a full release build within its memory limit.
11. **Scope splits:** give `project.process` a dispatch table for
    projectors, so a projector edit verifies one kind instead of the whole
    graph.
12. **Production throughput,** from the stage 0 measurement: compute each
    unit's dependency fingerprints once instead of about four times, and
    make choosing the next unit cheaper. Batched commits would gain at most
    1.5%.
13. **Clean up the schema misfits** listed in
    [2. Schema](2-schema.md#known-misfits-left-for-later), one at a time.

## Housekeeping

14. **Docs and tooling:** update the operation, deployment, backup and
    testing guides and AGENTS.md's watching rules; point
    `docs/reference/fetching.md` at the live rows; retire `nix/`; set log
    retention.
15. **What waited** can resume: the calendar contract, danceconvention.net
    and SwingDirector activations.
16. **The deferred proof pieces** stay deferred unless their trigger happens
    ([5. Stage 0](5-stage-0.md#deferred-unless-a-measurement-says-otherwise)).
