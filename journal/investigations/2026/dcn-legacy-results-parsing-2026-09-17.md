# DCN legacy results parsing, 2026-09-17

Status: Implemented locally, tested offline and independently reviewed. No kind activation, canonical projection, deployment or publication is
claimed. [D-0080](../../decisions/0080-parse-only-owned-legacy-dcn-results-tables.md)
records the interpretation boundary.

The [independently audited acquisition](../../evidence/admission/dcn-results-body-2026-09-17/independent-review-001/checks.json)
retained the exact approved Riga results capture `20190719204919` with observed
Memento `2019-07-19T20:49:19+00:00`. The complete 16,693-byte HTML body has SHA-256
`487b38c2ff264e62bf2e6de8e2b24cd5aff2ceab40856a56c1ac8539a4aad573`.
A byte-exact test copy and separate receipt/audit provenance live under
`tests/fixtures/sources/dcn/`; no retained acquisition evidence was changed.

The selected contest is `Jack'n'Jill Newcomer`, source contest ID `2196606`.
Eight printed contest selectors are retained, but only this contest has result
tables in the body. The finals table has nine pair rows. The preliminary leader
table has no printed rows, while the follower table has eight. Source row bibs,
verbatim name-cell text, explicit pair members and placement integers/intervals
remain typed source observations. Hyphens inside a name do not split people.
Pair order does not establish individual leader/follower identity, and finals
bibs remain attached to the row. Empty printed roles do not establish zero
entrants, complete acquisition, or promotion.

The parser validates the original event ID across context, canonical URL,
OpenGraph URL, results navigation and all selector context parameters. The one
selected heading must identify exactly one selector. HTML5 foster-parenting
moves `<legend>` outside its invalid table position: each exact sibling column
must own one legend and one table. The reviewed shape has one Finals table,
two role-specific Prelims tables, and Bib/Names/Placement headers. Unknown
columns, roles, ownership, merged cells, malformed intervals, repeated bibs,
unowned tables and changed pair shapes fail extraction.

The source prints two inert score locators:

- Finals: `https://danceconvention.net/eventdirector/en/roundscores/3451330.pdf`.
- Both preliminary roles: `https://danceconvention.net/eventdirector/en/roundscores/3451331.pdf`.

No request to those URLs or the selector endpoints was made by this parser
branch. They provide evidence for a later exact fixture proposal, not authority
to acquire anything. PDF scores, panels, judge names and promotion semantics
remain unassessed. Existing source-host robots restrictions remain applicable.
The new `dcn.legacy_results` kind is available only through the isolated DCN
package, not the ordinary acquisition registry. Its observations carry explicit
population, score-locator and admission-pending warnings and create no watches.

The initial combined DCN checks passed 97 tests in 1.16 seconds; an additional
paired-name rejection was then added for single-role preliminary rows. Final
source-bound validation and independent review follow below. The controls
include the real full body, synthetic malformed/empty bodies, changed names,
bibs, placement bounds and PDF locators, foreign event/context failures,
HTML5-reparented legends, empty observed tables, row/body bounds, unexecuted
scripts, exact-kind isolation and fresh-process typed decoding.

Final author checks passed **98 tests in 1.10 seconds**, Ruff, and mypy over five
DCN source files. The [source-bound receipt](../../evidence/admission/dcn-legacy-results-parsing-2026-09-17/offline-check-001/checks.json)
records exact source, test and fixture hashes. All checks were offline; this is
not a repository-wide full run or source-kind admission receipt.

[Independent parser review](../../evidence/admission/dcn-results-parser-2026-09-17/independent-review-001/checks.json)
passed **98 tests in 1.13 seconds** and Ruff, with source, test and fixture hashes
unchanged. It found no blocker in event/contest ownership, HTML5 sibling legends,
row-level bib handling, unknown empty-role populations or the no-activation
boundary. These receipts validate offline source interpretation only.
