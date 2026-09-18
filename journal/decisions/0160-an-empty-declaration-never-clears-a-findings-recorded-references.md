# D-0160: An empty declaration never clears a finding's recorded references

Recorded: 2026-09-18  
Decided by: agent  
Topic: State retention  
Supersedes: —  
Superseded by: —

## Decision

A caller that passes no references to `replace_findings` is saying nothing about
support, not saying "keep none of it". So an empty declaration never deletes a
finding's `finding_support_references` rows and never counts as a change on its
own. A caller that does declare references replaces that finding's rows with
exactly what it declared, as before.

Clearing a declaration is therefore not something a caller can do by omission.
Closing the finding is what stops it pinning anything, and the rows stay so that
reopening it does not have to rediscover what it was about.

Event-site review declares what it relies on: every
`history_event_site_review` proposal names `Reference('body', hit.body_sha256)`
for every capture in its group, the retained CDX response body the proposal was
read from. It does not declare `receipt_sha256`. That digest is of the request
receipt, which is stored under `archive-cdx/` and never under `blobs/`, and a
declaration naming a file that is not there is a reported defect
([D-0144](0144-unowned-bytes-are-unknown-and-a-wrong-declaration-is-reported.md)).
Every other caller whose evidence carries a file digest already declared it;
registry crosscheck is the only other one, and it does.

## Why

Migration 31 backfilled a row for every digest an existing finding's evidence
named and a file existed for, so that nothing pinned before the migration
stopped being pinned by it
([D-0139](0139-findings-declare-the-support-they-rely-on.md)). About twenty
callers write findings and almost none of them declare anything. The first
implementation deleted every row for a finding whenever anything about that
finding changed, so the next ordinary rewrite of an unchanged-in-spirit finding
threw the backfill away.

Event-site review is the case that showed it. Its evidence carries the capture
records, digests included, so the migration pinned the CDX bodies; its rewrite
happens on a rotating cadence, so the rows would have gone within days, and the
files with them the first time a plan was applied after that. The proposal asks
a human to read exactly those pages.

Both halves are needed. Declaring makes the pin a stated reason rather than a
leftover of a scan, which is the whole point of schema 31. The empty rule stops
the same loss happening in the nineteen callers nobody has been through yet, and
in any evidence a future caller writes that the migration already pinned.

## Alternatives

- Treat an empty declaration as an empty set and delete. Rejected: it makes
  forgetting destructive, silently, in the one direction the plan's safety rules
  say never to be silent about. A caller that means to keep nothing changes
  nothing today, because nothing declared anything before schema 31.
- Keep deleting, and fix the callers one at a time. Rejected: the rows go the
  moment a caller is missed, and there is no test that fails for a caller nobody
  thought of.
- Declare `receipt_sha256` as a body as well. Rejected: no such file exists
  under `blobs/`, so every event-site proposal would add a line to doctor's
  unknown section forever. The receipt is kept because `archive-cdx/` is copied
  into every checkpoint whole, not because anything declares it.

## Consequences

A wrong or stale reference can only be removed by closing the finding or by
deleting the row by hand, which is the same cost as a wrong declaration has
already ([D-0144](0144-unowned-bytes-are-unknown-and-a-wrong-declaration-is-reported.md)).
A finding that stops needing a file keeps it local until it closes; findings are
closed when their subject is resolved, so this is bounded by the same thing that
bounds the finding.

Event-site proposals now pin their CDX bodies for a stated reason, so those
bytes are on the local list and in every checkpoint while the proposal is open.
They were already pinned through the backfill; the change is that the plan can
say why.

## Links

- [Implementation plan](../../docs/plans/bounded-state-and-archive.md)
- [D-0139](0139-findings-declare-the-support-they-rely-on.md)
- [D-0144](0144-unowned-bytes-are-unknown-and-a-wrong-declaration-is-reported.md)
- [D-0162](0162-the-residency-check-covers-only-newly-declared-generations.md)
- [State contract](../../docs/reference/state.md)
- Renumbered: declared references are schema 30, not 31, since [D-0167](0167-intern-derivation-payloads-in-the-last-migration.md).
