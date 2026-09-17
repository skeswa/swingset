# Undated calendar map controls, 2026-09-17

The four acquired map bodies that still fail parsing contain no printed event
dates. They are available inputs with an interpretation gap. They are not four
unfetched pages, and their Archive capture years do not date their events.
No parser or production state was changed by this investigation.

A read-only query selected the four snapshots named by the current retained
year review. Their complete compressed production blobs were copied and
decompressed locally; each body matched its recorded SHA-256. New adjacent
fixture metadata preserves the original snapshot and Archive provenance.
No third-party request was made and no existing evidence was edited.

| Capture    | Markers | Printed date lines | Complete control                                                                        |
| ---------- | ------: | -----------------: | --------------------------------------------------------------------------------------- |
| 2016-07-10 |      59 |                  0 | [Body](../../../src/swingset/sources/wsdc_calendar/fixtures/calendar-map-20160710.html) |
| 2016-09-09 |      51 |                  0 | [Body](../../../src/swingset/sources/wsdc_calendar/fixtures/calendar-map-20160909.html) |
| 2016-11-10 |     112 |                  0 | [Body](../../../src/swingset/sources/wsdc_calendar/fixtures/calendar-map-20161110.html) |
| 2016-12-12 |     126 |                  0 | [Body](../../../src/swingset/sources/wsdc_calendar/fixtures/calendar-map-20161212.html) |

Every decoded popup is exactly one event-name anchor. The marker icon supplies
no date. Each body additionally has one three-argument example call prefixed
with `//`; it is not a live marker. The
[inspection report](../../evidence/collection/calendar-map-review-2026-09-17/review.json)
retains all decoded popup strings, icons, snapshot IDs and body hashes. Its
regular-expression inspection does not execute scripts and does not establish
full JavaScript interpretation or source-kind acceptance.

The existing parser rejects a marker without its printed date. That finding
must remain visible until a reviewed representation of undated listings and
independent dated matches exists. Do not count the bodies as successfully
interpreted, silently clear their findings, or claim a historical year complete.
This resolves the cause of these four failures, not the year gate.
See [D-0071](../../decisions/0071-retain-undated-map-markers-as-review-gaps.md).

Independent inspection also found a malformed printed locator in the December
body: New Zealand Open Swing Dance Championships uses
`www.nzoswing.com#http://www.nzoswing.com#`. Preserve that literal as source
evidence; it is not a verified event-site mapping and must not be silently
normalized into acquisition authority.

The [independent review](../../evidence/collection/calendar-map-review-2026-09-17/independent-review-001/checks.json)
verified all four body hashes and sizes against original snapshot metadata,
Memento timestamps and retained capture targets. A separate quote-aware scanner
confirmed the live marker counts, anchor-only popups and commented examples.
The actual current parser still reports the missing printed date. This closes
the cause investigation only; parser acceptance and year review remain open.
