# 9. After the switch

Read [8. Stage 3](8-stage-3.md) first; next is
[10. Local development](10-local-development.md).

Each item is its own production change, with its own receipts. Follow the
dependencies stated below; otherwise they can run in any order.

## Storage and backups

1. **A second provider** for the backup bucket, filled by a separate job
   with its own keys; the worker cannot delete anything there. A checked
   independently protected copy is required before deleting the Mac copies.
2. **Files into object storage** ([4. Hosting](4-hosting.md#files-in-object-storage)):
   - price it (storage, uploads, the second copy);
   - turn on the storage interface: write-once uploads, a checked read, a
     size-limited local cache, a rule for every top-level state file;
   - copy about 150,000 files plus the candidates with a resumable copier,
     recording each key and hash, then switch reads to the bucket;
   - delete the local copies once a restore drill from the buckets passes,
     with an independent check.
3. **The full rebuild drill again** whenever the table classes or the
   reference list change ([4. Hosting](4-hosting.md#backups-and-recovery)).
   The first ran in the dry run, before the switch.
4. **Delete the Mac copies,** the local checkpoints and the VM, with an
   independent check, once gate 3 and a current complete server backup's
   check have passed and its independently protected copy is verified
   ([page 4](4-hosting.md#backups-and-recovery)). Until then keep the Mac
   copies as evidence; they cannot resume fetching after cutover.

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

10. **The remaining hard-case test:** a Postgres minor upgrade on
    Rhuidean's schedule, with the major-upgrade path written down. The
    others ran in the dry run.
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

14. **Docs and tooling:** the operation, deployment, backup and testing
    guides and AGENTS.md's watching rules must be ready at the switch.
    Update them with each subsequent change; point
    `docs/reference/fetching.md` at the live rows; delete Nix: `nix/`, `flake.nix`,
    `flake.lock` and the nixfmt pin, and record that D-0170 no longer
    applies ([10. Local development](10-local-development.md#what-changes-in-the-repo));
    set log retention.
15. **Finish the developer tools.** Stage 1 already supplies `dev`, `db`,
    focused/watch tests, `check`, `dev-seed`, `dev-replay` and `dev-drop`,
    with measured feedback budgets. Deploy, tunnel and environment guards
    arrive before stage 2. Real-data `dev-export` and `dev-restore` can
    follow here, with the 30-day expiry on `swingset-dev`; `gov` commands
    arrive with their tables. See [local development](10-local-development.md).
16. **What waited** can resume: the calendar contract, danceconvention.net
    and SwingDirector activations.
17. **The deferred proof pieces** stay deferred unless their trigger happens
    ([5. Stage 0](5-stage-0.md#deferred-unless-a-measurement-says-otherwise)).
