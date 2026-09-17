# WSDC newsletter fixture

`newsletter-vol6.pdf` is the unmodified Volume 6 newsletter dated 2018-04-09.
Fetched on 2026-09-13 UTC through the project FetchClient and host gate;
`newsletter-vol6.json` records the original URL and request provenance.

The parser finds 37 sidebar listings and four dated approval notices.
Columns and wrapped names are preserved; article bullets do not become
listings. Through parser 7, member activities shown in purple were not distinguished from
registry listings, as allowed by the backfill contract. Those versions emit
`newsletter_colour_unverified` and tags affected sidebar rows; it does not
claim to have recovered purple trial status. Parser 8's five exact readings
are described below; other issue layouts require their own review.

The guarded parser recognizes the left or right registry sidebar headings and
the continuation region marked by the asterisk footnote. Article bullets in
other columns do not become events. A quarter-only approval remains an
`approval_notice_review` warning even beside successfully dated listings.
Unknown layouts never claim authoritative empty output, and invalid printed
dates fail with `newsletter_invalid_event_date` instead of silently vanishing.

Additional phase-1 captures (2026-09-13) retain the exact PDF and snapshot
metadata as `newsletter-vol{1,2,5,14,20,21,25}.{pdf,json}`. Capture used the
pipeline host gate; parser development made no new requests. Snapshot/body
and extract digests, URL, fetch time and capture classification are in each
metadata file.

Parser 4 uses Vol1/2/5/25 to check the older symbol-font bullet, right-column
and footer continuation, wrapped headings, explicit cross-year ranges, and
gray trial legends. Vol1 yields 39 dated rows; Vol2 38; Vol5 39; Vol25 28.
Undated hiatus/cancellation notices remain review warnings. Vol5's printed
2017 dates for Swingcouver and Tulsa are preserved as printed, not silently
changed to 2018. Colour alone never establishes an individual trial status.

Vol14/20/21 contain policy letters rather than event sidebars. They initially
remained non-admitting zero-row interpretations. `empty_review.py` now records
exact body- and extracted-pages-bound dispositions for Vol14–21; unknown or
changed bodies never become empty merely because parsing returned no rows.

Volumes 3, 7, and 13 retain actual phase-1 PDF bodies and snapshot metadata
from 2026-09-13 UTC. Parser 6 handles spaces before date commas, printed
asterisk footnotes, a wrapped name sharing its date line, and the right
sidebar date beside article text. These add eleven dated rows across the
three issues without removing existing rows. Hiatus, malformed years, and
comma-separated dates without an explicit range remain review findings.

Volume 23 is copied byte for byte from the retained 2026-09-13 warning review,
with its original snapshot metadata. Parser 7 recognizes its calendar-planning
sentence about new events selecting locations and dates as administrative
prose. That sentence does not name an approval or an event edition. The parser
keeps all 39 dated rows and the undated-listing warning. Unknown prose and
quarter-only approvals still require review; this change does not classify
an otherwise unparsed page as empty.

Volumes 8, 9, 11 and 29 were copied with exact snapshot metadata from retained
production bodies on 2026-09-17, without a source request. Parser 7 also ignores
their specific administrative sentences about managing or running events,
membership applications, certification and the council's posting schedule.
Across all 21 PDF fixtures, all dated rows and legitimate-empty outcomes match
parser 6. Eight false prose warnings disappear across five bodies; real approval,
unknown-page, date and status warnings remain. See the
[comparison](../../../../../journal/investigations/2026/newsletter-warning-2026-09-17.md).

Parser 8 adds exact body- and extracted-pages-bound colour dispositions for
five named, dated rows in Vol6/9/13/25. Purple or gray member-activity rows
retain `Member Activity`; two gray trial rows retain `Trial Event`. All other
rows retain their prior output, and the four bodies still warn about unreviewed
colour coverage. Changed bodies or extracted text never inherit these readings.
The [operator review](../../../../../journal/investigations/2026/newsletter-colour-review-2026-09-17.md)
records actual text-show colours, printed legends, positions and dates. This
does not claim a general PDF colour interpreter or accepted source policy.
