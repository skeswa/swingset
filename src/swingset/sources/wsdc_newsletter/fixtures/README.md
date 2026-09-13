# WSDC newsletter fixture

`newsletter-vol6.pdf` is the unmodified Volume 6 newsletter dated 2018-04-09.
Fetched on 2026-09-13 UTC through the project FetchClient and host gate;
`newsletter-vol6.json` records the original URL and request provenance.

The parser finds 37 sidebar listings and four dated approval notices.
Columns and wrapped names are preserved; article bullets do not become
listings. Member activities shown in purple are not yet distinguished from
registry listings, as allowed by the backfill contract. The parser emits
`newsletter_colour_unverified` and tags affected sidebar rows; it does not
claim to have recovered purple trial status. Other issue layouts
remain unverified until captured and reviewed.

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

Vol14/20/21 contain policy letters rather than event sidebars. They currently
remain non-admitting zero-row interpretations until the intake owner records
an exact reviewed-empty disposition; an unknown layout is never accepted as
empty merely because parsing returned no rows.

Volumes 3, 7, and 13 retain actual phase-1 PDF bodies and snapshot metadata
from 2026-09-13 UTC. Parser 6 handles spaces before date commas, printed
asterisk footnotes, a wrapped name sharing its date line, and the right
sidebar date beside article text. These add eleven dated rows across the
three issues without removing existing rows. Hiatus, malformed years, and
comma-separated dates without an explicit range remain review findings.
