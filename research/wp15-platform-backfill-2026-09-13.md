# WP15 generic platform backfill

This implementation is preparation and offline verification. V2/V4 are published;
all 17 event years remain unaccepted. No historical sheet watch, request, year
acceptance or source policy activation was performed by this work.

## Interfaces

`history.platform.plan_platform(events, captures, declared_pages=...)` is pure.
`KnownEvent` carries an existing canonical event/source binding. CDX paths can
identify platform locators, never create an event or accept a mapping. The plan
orders newest event years first, then sources with the most distinct captured
events, then every event document before round documents within that year/source.
It selects at most three successful distinct CDX digests per original URL with
the existing corrected-capture preference. Non-document assets are ignored.
Unmapped sheets and advertised pages without captures become findings.

`retained_plan(conn, history_start=...)` reads canonical `source_event_map`, retained
CDX rows and selected generation child declarations. It neither queries CDX nor
writes watches. EEPro directory/file paths and scoring.dance event paths have
known grammars. Scoring.dance round ownership must come from a retained event
page. WDR's known rounds document and DCN event metadata can be proposed only
against an existing source-event binding. DCN's missing adapter/contract remains
a hard gate; no PDF locator or event identity is guessed. SRS belongs to WP14 and
is outside this planner's platform set.

`history.acquisition.archive_watch_gate(...)` is shared by watch insertion and
transport. Only origin reads and the four true phase-1 catalog parser kinds are
exempt from phase-2 checks. Relabeling a results page `kind=index` does not bypass
anything. `phase_two_gate(conn, source=..., source_ref=..., page_kind=...)` returns
`None` or a stable reason: unmapped event, unaccepted/stale year, mismatched source,
unavailable/unassessed kind, non-enforcing policy or stale reviewed contract.
The selected kind's current contract version must match its enforcing policy and
review receipt. Global H7/H10 flags alone do not grant acquisition.

`history.backfill.dispatch_one(database, config, clock, run_id, deadline=...,
fetcher=...)` performs at most one ordinary `FetchClient.fetch`. It requires the
caller's exclusive database process lock, no runnable parse/project/link work, no
other due watch, and more than 120 seconds remaining. It rechecks those conditions,
the current event binding/dates and exact gates in the same transaction that
creates or advances a priority-6 archive watch. It preserves explicit watch pauses,
same-capture retry timers and operator controls. An old origin polling timer does
not delay a distinct fallback capture after an incomplete interpretation. The fetch deadline reserves the final two minutes. Host budgets,
robots, redirects, backoff and pauses remain owned by `FetchClient` and `Gate`.

The cycle routes priority-6 archive work through this dispatcher after runnable
ordinary work has drained. The usual parse/project/link stages handle its snapshot
on the next cycle. This module does not add its own scheduler or network client.

## Resume, fallback and child acquisition

Snapshots and selected generations are the resume ledger. No separate queue
migration is required. A retained successful HTTP response is never requested
again. Diagnostic transport failures preserve eligibility for that capture under
its existing host/watch cooldown; they do not count as distinct interpreted bodies.
Extraction failures, unadmitted/incomplete interpretations, empty result tables,
unsupported scoring layouts and advertised children without retained captures
permit the next distinct capture. Three alternatives exhausted produces a source
event gap. A complete admitted capture ends fallback. H12 retains blocked and
superseded pending tokens for review, but an unchanged failed input is not runnable
and cannot starve a different capture. Those tokens still prevent an ordinary full
build; fallback does not silently resolve them.

Historical event parsing retains FileRows and immutable generation declarations,
then records acquisition-intent findings without creating origin child controls.
Existing live/paused children remain intact. The dispatcher materializes a child
only after its parent has been assessed and the child's own exact contract and
year gates pass. An incomplete parent whose alternatives are exhausted can still
support independently admitted advertised children; arbitrary inferred children
remain blocked. There is no automatic origin fallback in this package.

Explicitly selected older archive captures use admission's shared
`selected_snapshot` predicate when replacing observations. Memento time records
historical provenance; it cannot cause a failed newer capture to veto every older
fallback. Origin ordering and independent phase-1 capture retention remain intact.

## Verification and remaining acceptance

`tests/test_platform_backfill.py` exercises ordering, the history floor, missing
mappings, gate combinations, atomic rollback, wall-clock reserve, due work,
cooldowns, actual host quota refusal, three distinct failed captures, and the
complete retained seven-file Freedom Swing index. The index regression checks
that selected declarations survive while no origin child watch/request is created,
and that a round cannot borrow its parent's admission policy. The cycle tests
exercise the two-minute reserve, next-cycle parsing, and an independently blocked
partial index followed by a complete older capture while the old token stays
visible and non-runnable.

WP15 operational completion still needs the accepted-year inputs and bounded live
runs through the integrated scheduler, followed by coverage versus retained evidence
review. No year is marked complete by these tests. DCN and SRS real control bodies
remain pending the separately proposed owner fixture exception and archive quota;
this work does not activate those sources or expand that exception.
