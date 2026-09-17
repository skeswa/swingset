# Proposed normalized snapshot lookup

Date: 2026-09-16 UTC  
Type: Proposed investigation; not implemented  
Related: [Evidence bundle](../../evidence/runtime/normalized-request-lookup-20260916/), [local unavailable accounting](event-unavailable-accounting-2026-09-16.md)

No source/tests, retained operating state, VM jobs, network, or migrations were
changed. Disposable fixtures were deleted. No storage/API choice or ADR is
established by this note. The existing conservative unknown result is correct.

## Reproduction

[The retained reproduction](../../evidence/runtime/normalized-request-lookup-20260916/candidate-limit-reproduction.py) exercises the actual current Session and normalization code.
[Exact results](../../evidence/runtime/normalized-request-lookup-20260916/candidate-limit-results.json) and [source/environment receipt](../../evidence/runtime/normalized-request-lookup-20260916/receipt.json) preserve the run. The recorded Jujutsu revision is a reference; file hashes pin the dirty working source.

| Retained evidence                                                 | Result                                   | Rows loaded |
| ----------------------------------------------------------------- | ---------------------------------------- | ----------- |
| Exact origin Gone 404 with verified body                          | unavailable true                         | 2           |
| Same response plus 63 unrelated same-source snapshots             | unavailable true                         | 65          |
| Same response plus 64 unrelated snapshots                         | acquired/interpreted/unavailable unknown | 65          |
| A usable normalized alias older than those 64 unrelated rows      | still unknown                            | 65          |
| Remove 2 unrelated rows so alias is inside a complete 63-row pass | acquired true, unavailable false         | 64          |

The current unknown is correct: the older alias demonstrates why the exact 404
cannot authorize skipping the remaining domain. Runtime for these tiny synthetic
queries was roughly 1–2 ms, not a retained-state throughput measurement.

## Proposed lookup contract

Retain `event_evidence.request()` as the only URL/form normalization rule. An
internal lookup key should normalize method, URL and form without source; join
`snapshots.watch_id -> watches.source` at verification time. Public request IDs
still include source. Watch source changes then need no mass key rewrite.

The query must return every snapshot in that normalized request domain under
the existing cutoff rule, regardless of watch kind, declared membership, raw URL
spelling or archive transport. Recompute identity on each loaded snapshot before
using it. Keep the present artifact, admission and cutoff checks unchanged.

An absent, incompatible, pending or invalid lookup entry cannot be treated as
nonmatching. Such entries in the applicable source/method/cutoff domain make
negative acquisition and unavailability unknown. A discovered usable positive
can still establish acquisition despite an incomplete lookup. Fallback to the
existing bounded scan is safe; an empty partial index is never absence proof.

The candidate cap then applies to matching history, not unrelated source pages.
It still cannot prove absence when matching history itself reaches the cap.
Interpretation's separate generation-candidate bounds are also unchanged.

## Options and actual compatibility costs

### Deterministic SQLite function index

An expression index on `normalized_request_v1(method,url,form)` gives SQLite
transactional index maintenance. It avoids backfill bookkeeping and automatically
covers every inserted/updated row. Its function must use the same normalization
recipe, returning a distinct invalid-identity sentinel rather than silently
omitting bad inputs. Readers must check that invalid domain too.

A local SQLite 3.50.4 probe in
[the compatibility evidence](../../evidence/runtime/normalized-request-lookup-20260916/sqlite-index-options.json) confirmed indexed alias
matching and an indexed SEARCH plan. It also confirmed that a connection lacking
the UDF fails both snapshot insertion and `PRAGMA integrity_check`. Plain reads
and unrelated writes still work. Adding a STORED generated column to a populated
table failed; a computed-column design is not a small ALTER migration.

This reaches beyond `state.db.open_database()`. Checkpoint validation uses a raw
connection and integrity_check; checkpoint/recovery tools, snapshot-writing test
fixtures, and external SQLite writers must have compatible function registration.
Index creation normalizes all existing rows while holding the migration writer
lock. Changing HTTPX normalization behavior requires a versioned function/index
and rebuild. Verifying an immutable older checkpoint would still need the exact
older function semantics; silently registering a changed implementation under
the old deterministic name is unsafe.

This is the smallest SQL implementation, but not the smallest recovery-compatible
change. Do not choose it without accepting connection-factory and historical
normalizer support as part of the work.

### Ordinary derived lookup with invalidation and bounded backfill

This preserves ordinary SQLite readers, integrity checks and checkpoint copies.
Python computes keys using the captured normalizer recipe; SQL triggers invalidate
them on raw identity changes. Existing rows remain explicitly unindexed until a
bounded pass processes them. Fetch writes compute new keys in the same transaction
as the snapshot, including error responses. Raw/legacy writers leave pending work
through the trigger instead of silently introducing invisible snapshots.

Two storage forms are viable:

- Nullable derived key/recipe/error columns on snapshots make missing coverage
  inherent: every snapshot has either a current key or an unknown state. Partial
  indexes can expose unknown rows. This is the smallest coverage model, but adds
  derived bookkeeping to the evidence table and current broad snapshot-update
  triggers conservatively dirty event pressure during backfill.
- A sidecar table keeps evidence rows unchanged. It additionally needs complete
  base coverage plus trigger-maintained dirty IDs (or an equivalent all-snapshot
  pending-row inventory). A cursor alone is insufficient: inserts before the
  cursor, identity rewrites, deletions and restart must be covered atomically.
  Missing or damaged sidecar rows must invalidate coverage, not disappear from a
  negative query. This has more maintenance states than nullable columns.

The cache is an acceleration structure maintained by trusted writer code, not a
new source authority. Returned rows must have their raw identity/recipe rechecked.
Completeness rests on the backfill and trigger invariant; a mere manually writable
`complete=true` is insufficient. Arbitrary forged wrong-key cache writes cannot
be detected by checking only returned matches. If the threat model requires the
database itself to enforce key correctness against such writes, use the UDF
index or pay for independent whole-index verification; a cache cannot provide
that guarantee by declaration.

Do not extend the ordinary successful-progress fence with a new per-insert global
epoch. Index maintenance alone must not create success, invalidate genuine
missing-to-success baselines, or schedule source requests.

The compatibility probe was repeated from [its retained script](../../evidence/runtime/normalized-request-lookup-20260916/sqlite-index-probe.py); [the repeat receipt](../../evidence/runtime/normalized-request-lookup-20260916/sqlite-repeat-receipt.json) confirms byte-identical results.

## Minimal storage/API sketches, not an approved schema

The nullable-column option needs three fields: `request_lookup_key`,
`request_lookup_recipe`, and `request_lookup_error`. Default all to null. Add an
index for compatible matching keys and an index for pending/invalid entries by
watch. An index on `watches(source,watch_id)` can support the authoritative source
join. SQLite index construction still visits existing rows; only normalization
and artifact work are deferred. No artifact work is needed to compute a key.

An insert starts unindexed even for a raw SQLite writer. A trigger clears cache
fields whenever snapshot method, URL, or form changes. The normalizer fills a
key or a bounded error marker in the same transaction that checks the captured
raw inputs. Source moves require no key rewrite because source remains a live
join. An invalid entry keeps a null key and blocks negative proof; it is not
retried forever while unchanged. A changed raw identity clears its error. A
changed recipe makes older keys unknown until rebuilt.

Bounded backfill selects unprocessed or incompatible entries, using a durable
cursor/high-water pass for fairness. Entries inserted before the cursor remain
null and therefore cannot disappear from the verifier's unknown-domain guard.
No `complete=true` flag is necessary for the nullable-column design. Never infer
completeness just because a batch reached its cursor limit. Recipe identity
must cover existing normalization code, canonical JSON/hash behavior and the
relevant HTTPX dependency. Cache-only updates currently trigger conservative
snapshot pressure bookkeeping; measure that cost before choosing columns.

For a sidecar instead, equivalent snapshot insert/identity-update/delete triggers
and complete pending coverage are additional requirements. Either populate one
pending row per snapshot or retain a separately fenced initial inventory pass
plus dirty IDs. A deleted/missing lookup row must not establish negative proof.
Do not add sidecar completeness assertions without tests for those omissions.

A possible small API is `record_snapshot(conn, snapshot_id, recipe)` for the
existing fetch transaction, `refresh_lookup(database, limit, recipe)` for bounded
metadata maintenance, and `lookup(session, request, cutoff)` returning loaded
candidates plus assessed-domain status. A schema/recipe mismatch returns unknown
or the current conservative scan. These are interface sketches, not new workers
or executable project APIs.

### Can Session accept a lookup without any new writer work?

Yes as an interface: a provider can use the existing Session's `read()`/reader,
cutoff and shared budget; Session still checks every returned candidate's actual
identity and support. A provider must remain bound to the caller's read snapshot.
Its completeness cannot come from user-supplied metadata or a Boolean assertion.

This alone does not solve the large-source case. An ephemeral provider must load
and normalize the entire applicable source domain within the current budget,
or report it incomplete. Caching that complete scan across requests in the same
Session saves repeated work for small domains, but cannot certify larger ones.
Enumeration membership, declared watches and successful-operation receipts do
not enumerate every retained snapshot alias. A durable complete lookup needs
writer/backfill maintenance; dependency injection only separates its interface.
Do not hide an unbounded UDF/full-source scan inside SQL to evade returned-row
budgets. Existing budgets are not hard limits on SQLite internal scan time.

## Recommended next increment

Prefer the UDF-free derived lookup for this repository's portable checkpoint and
frozen-runtime workflow. Before selecting its storage form, decide whether adding
derived snapshot columns is acceptable. Nullable columns offer the smallest
trustworthy coverage state machine under the existing trusted-writer model;
a sidecar is cleaner evidence separation but requires additional completeness
bookkeeping. Do not describe either as a one-query optimization.

Concrete seams are:

1. A small normalization/index module exposes recipe identity, bounded backfill,
   same-transaction snapshot recording, and read-only candidate-domain selection.
   The recipe must change automatically with normalization code/dependencies,
   including HTTPX behavior; a manual constant alone is insufficient.
2. Migration adds only empty/unindexed cache state, indexes and invalidation
   triggers. No network or large synchronous normalization walk is required.
3. `fetch.client` fills newly retained snapshot keys in its existing transaction.
   Bounded maintenance under the existing writer/admission protocol handles legacy
   and dirty rows. Session never performs backfill or changes connection settings.
4. `Session.verify_request()` uses the compatible complete index domain, or its
   existing conservative fallback. Malformed normalization entries remain unknown.
5. Backup copies the cache as ordinary data. Restore or a changed normalization
   recipe invalidates/rebuilds it conservatively without rewriting checkpoint
   evidence or requiring historic Python UDFs. Derivation replay is not required
   merely to reconstruct this metadata lookup. A later runtime/schema needs its
   own release; frozen schema 14 H16 evidence remains untouched.

Expect O(number of snapshots) normalization once, then work proportional to new
or changed identities, with O(number of snapshots) index storage. The actual
retained count, disk footprint, backfill rate and SQL plans have not been measured.
Existing Session budgets bound returned metadata/artifact work, not SQL internal
scan time; inspect plans for both matching-key and pending/invalid-domain guards.

Minimum tests: 64 unrelated rows no longer crowd out a proved 404; older usable
aliases still win; same-key histories over the cap stay unknown; form/case/port/
fragment normalization; malformed and stale keys; missing sidecar coverage if that
form is chosen; inserts/identity changes before and after a backfill cursor;
source moves; rollback/interruption; raw SQLite writer invalidation; read-only
no-backfill reports; checkpoint restore under a changed recipe; unchanged genuine
success qualification; and shared budget exhaustion without unfair retry loops.

No implementation or new schema is authorized by this proposal itself.
