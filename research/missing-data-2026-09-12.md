# Missing data after the first registry sweep

**As of 2026-09-12, 10:37 a.m. Mountain / 16:37 UTC.** This report audits
[public revision `845d92a0`](https://huggingface.co/datasets/skeswa/swingset/tree/845d92a04e30b03d4a7081655e754ec2cea323aa),
built at 10:33 a.m. Mountain. The initial registry sweep finished at 7:09 a.m.
The report is a dated snapshot; later collection can change these counts.

**The registry intake is complete for the initial sweep, but the dataset is
still missing substantial event and score-sheet coverage.** The largest known
collection backlog is 4,482 scoring.dance round URLs. Historical event mapping
and individual identity resolution are the other large gaps. The published
files pass integrity checks; their contents remain incomplete.

All inventories and query outputs are in
[the evidence directory](verification/2026-09-12/missing-data/). Counts below
have different units and overlap. They must not be added into a single
“missing records” total or an overall completeness percentage.

## What is missing, at a glance

| Area                             |                                                                    Observed gap | What the count means                                                                                                                                            |
| -------------------------------- | ------------------------------------------------------------------------------: | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Known score sheets               |                  **4,482 unfetched round URLs across 259 scoring.dance events** | Captured event pages actually list these URLs. Their contents have not been fetched.                                                                            |
| Ended event coverage             |              **267 of 404 ended catalog events have no contests or placements** | Some index entries may be workshops, other dance styles, or events without published competitions. This is a catalog gap, not proof that 267 result sets exist. |
| Historical event catalog         |                                                 **No event rows for 2010–2017** | The draft historical target begins in 2010. Registry history exists, but canonical event/result history has not been built for those years.                     |
| Registry-to-event links          | **138,280 of 159,115 WCS registry placements from 2010 onward lack `event_id`** | Result and point data are present, but cannot be joined directly to canonical events.                                                                           |
| Individual identities            |                     **25,523 of 69,248 individual entry records lack WSDC IDs** | Contest-participation rows, not unique people. Missing IDs can reflect unresolved links or dancers who have not received a number.                              |
| Judge identities                 |                          **All 3,791 event-scoped judge records lack WSDC IDs** | All have names; identity across events remains unresolved.                                                                                                      |
| Historical registry identities   |                        **136 reference IDs are absent from the current mirror** | Every one returned a verified current `not_found`; none is an unfetched ID.                                                                                     |
| Heat sheets                      |                                                                 **0 heat rows** | No canonical heat assignments are published. This does not mean the events had no heats.                                                                        |
| Withheld or unsupported evidence |                                                **4,750 public review findings** | Includes overlapping conflicts, unknown values, unsupported contests, aliases, and reference differences. This is not an error rate.                            |

The pinned publication contains 573 events, 2,312 contests, 3,625 rounds,
76,343 entries, 3,791 judges, 18,005 placements, 380,244 callback marks,
101,647 final marks, 27,784 dancers, and 197,990 registry placements.
The [published manifest](verification/2026-09-12/missing-data/published-manifest.json)
contains all 17 table counts and file hashes.

## 1. Event and score-sheet coverage

### Ended events versus future schedules

Of the 573 catalog events, 404 had ended, four were underway, and 165 were
future. Results exist for **137 of the 404 ended events: 33.9%**. The other
267 have no published contests or placements. Having some results does not
establish that every contest or round at those 137 events is complete.

The four current events are Bavarian Open West Coast Swing Championships,
Sea Dance Fest, SwingTime Denver, and Trilogy Swing. None has published results
in this revision. They and the 165 future events are excluded from the ended-event
gap denominator; absence of final results while an event is underway is not
itself a collection defect.

| Year      | Ended catalog events | Events with results | Events without results |
| --------- | -------------------: | ------------------: | ---------------------: |
| 2018      |                    5 |                   1 |                      4 |
| 2019      |                   13 |                   0 |                     13 |
| 2020      |                    3 |                   2 |                      1 |
| 2021      |                    3 |                   0 |                      3 |
| 2022      |                   21 |                   2 |                     19 |
| 2023      |                   34 |                   5 |                     29 |
| 2024      |                   66 |                  15 |                     51 |
| 2025      |                  146 |                  56 |                     90 |
| 2026      |                  113 |                  56 |                     57 |
| **Total** |              **404** |             **137** |                **267** |

There are no canonical event rows for 2010–2017. Only 25 of the 145 catalog
events from 2018–2024 have results. The registry's much longer point history
cannot supply preliminary sheets, non-point-winning entrants, heats, or judge
marks. [The historical backfill design](../design/backfill.md) describes planned
coverage, not delivered coverage.

Full inventory: [every published event and its coverage](verification/2026-09-12/missing-data/events-coverage.csv),
[coverage by year](verification/2026-09-12/missing-data/events-by-year.csv).

### Captured source coverage

| Source        |           Captured event denominator | Events with contests |                                   Event-derived round URLs fetched / listed |
| ------------- | -----------------------------------: | -------------------: | --------------------------------------------------------------------------: |
| EEPro         |         72 indexed and mapped events |                   72 |                                                                   442 / 442 |
| scoring.dance | 383 indexed; 319 mapped, 64 unmapped |     52 mapped events |                                                                 896 / 5,378 |
| WDR           |      13 manually supplied event URLs |                   13 | Both JSON endpoints fetched for all 13; these are not individual round URLs |

EEPro also has five manually seeded Summer Hummer round watches; scoring.dance
has 12 manually seeded Bristol Swing Fiesta round watches. All were fetched.
Including them gives 447 EEPro round watches and 5,390 scoring.dance round
watches. A URL can contain multiple canonical rounds, so URL counts and the
3,625 published round rows are not interchangeable.

EEPro's result is complete against **the captured index and listed URLs**.
It does not establish that older, unlisted EEPro event slugs were discovered.
WDR's 13-event denominator is the override list, not a complete platform index.
The discovery limits remain under [#12](https://github.com/skeswa/swingset/issues/12),
[#14](https://github.com/skeswa/swingset/issues/14), and
[#21](https://github.com/skeswa/swingset/issues/21).

The **4,482 never-fetched scoring.dance round URLs** span 259 source events:
258 have no fetched round at all and one has partial coverage. Nine additional
mapped event pages list no round URL. Those nine need investigation before
claiming their score sheets are missing.

| Example event            | Listed round URLs not fetched |
| ------------------------ | ----------------------------: |
| Ceroc Champs 2025        |                            53 |
| SwingVester 2025/26 WSDC |                            43 |
| SwingCouver 2025         |                            42 |
| French Open 2025         |                            39 |
| German Open 2026         |                            38 |
| Boogie By the Bay 2025   |                            38 |

The unfetched URLs break down by source-event year as 128 in 2019, 11 in 2020,
42 in 2021, 267 in 2022, 482 in 2023, 893 in 2024, 1,644 in 2025, and 1,015
in 2026. These are captured leads, not estimates of how many useful rows will
be recovered.

At capture time scoring.dance had used **800 of its 800 daily requests**.
Fetching 4,482 URLs requires at least 5.6 full daily request allowances—six
allowances in practice—even if all requests go to new sheets. Index polling,
rechecks, failures, and newly discovered URLs make elapsed time longer. This
is a capacity lower bound, not a completion date.

There is also a separate mapping gap: **64 indexed scoring.dance events lack
accepted dates and remain unmapped**. Examples include All-Star Swing Jam 2024
(`scoringdance:123`), Seattle Ticket Kickoff (`:38`), and WCS Magyar Kupa 2026
(`:303`). Their identities and dates need evidence-backed reconciliation.

One event-page parser failure remains: Hippmann Fun-Competition 2024
(`scoringdance:179`) returned HTTP 200 but lacked the expected links. Its
archived snapshot is `snap_20260911T043231Z_0859efa5a48f`. It is included in
the 64 unmapped events. No fetched event-derived round watch lacks a current
observation or has a failing latest parse; the known 4,482-round gap is an
intake backlog.

Full inventories: [source event gaps](verification/2026-09-12/missing-data/events-source-gaps.csv)
and [source-level counts](verification/2026-09-12/missing-data/events-by-source.csv).
[Every never-fetched URL](verification/2026-09-12/missing-data/operations-unfetched-watches.csv)
is listed with its watch ID and parent.

### Additional platforms and research leads

The earlier research census contains 181 event-edition leads. Exact keys
match 91 published events; 90 do not. **82 unmatched keys have a results URL**,
but four have conservative alias candidates and further aliases may exist.
These 82 must not be reported as 82 confirmed missing events.

Four research editions are explicitly marked not held: Sea to Sky Seattle 2025,
The Australian Classic WCSDC 2026, Dance N Play 2026, and Toronto Open 2026.
Exclude them from missing-result claims. Recent leads to reconcile include
Jax Westie Fest 2026, Korea Westival 2026, Chicagoland Country and Swing Dance
Festival 2026, Shakedown Swing 2026, Grand Party Sofia 2026, and New England
Dance Festival 2026.

The enabled collector sources are the WSDC calendar and registry, EEPro,
scoring.dance, and WDR. DanceConvention.net, event-site documents, public
Drive/Sheets results, and other platforms in the research census do not have
an enabled production results adapter. Known URLs on those sources therefore
need a collection and parsing path. Historical Wayback discovery and the
2010-on event enumeration are also pending; see
[#20](https://github.com/skeswa/swingset/issues/20) and
[#21](https://github.com/skeswa/swingset/issues/21).

Full lead list, URLs, confidence, aliases, and exclusions:
[research leads](verification/2026-09-12/missing-data/events-research-leads.csv).
These earlier research claims were not reverified against live sites in this audit.

## 2. Registry identities and historical claims

### The 136 absent reference IDs were checked

The archived comparison contains 27,039 dancer IDs. The publication contains
27,784. Their intersection is 26,903: **136 reference IDs are absent locally,
and 881 local IDs are absent from the reference**.

Every one of the 136 absent reference IDs has a successful, verified missing-ID
lookup. There are **zero unfetched IDs in this set**. No explicit merge target
was captured, so we cannot label them merged, deleted, or retired with certainty.

Those IDs have **177 placement claims in the archived reference**, dated
2001-04 through 2026-08. Of these, 141 claims concern 118 IDs with activity in
2024 or later; 15 IDs have their latest reference placement in 2026. Examples:

| Reference identity | WSDC ID | Example reference result edition       |
| ------------------ | ------: | -------------------------------------- |
| Alice Stratmann    |   27513 | Freedom Swing Dance Challenge, 2026-01 |
| Kathryn Noel       |   27690 | New York Flow Festival, 2026-03        |
| Amandine Leborgne  |   28285 | French Open WCS, 2026-05               |

This is a historical evidence gap, not an unfinished numeric sweep. Preserve
these discrepancies for review and seek dated primary records or explicit
merge evidence. The comparison dump remains comparison evidence and should
not be copied into canonical records or used to guess replacement IDs.

The 881 extra local IDs are additional origin coverage. Only 29 exceed the
reference maximum ID, 28,998. The other 852 are lower-numbered IDs absent
from the archived reference; this audit does not establish whether that reflects
timing, restoration, or omissions in the reference.

Inventory: [all 136 IDs with lookup and reference evidence](verification/2026-09-12/missing-data/registry-reference-missing-dancers.csv).

### Most name differences are cosmetic

Of 214 raw name differences for shared IDs, **212 are only leading or trailing
whitespace**. Two differ beyond whitespace:

- WSDC 1843: reference `Niki Kontoulas`; current `Nikki Kontoulas`.
- WSDC 18986: reference `Dennis Hulboj`; current `Dennis Laskewitz`.

These may matter for historical name search. They do not justify replacing the
current lookup's name. Full list:
[name differences](verification/2026-09-12/missing-data/registry-name-differences.csv).

### Missing and withheld placement claims

The reference has 196,679 placement keys. Its schema has no dance-style field,
so this bounded comparison **assumes the reference claims are WCS** and uses
the publication's **196,536 WCS registry rows**. The reference cannot independently
verify dance style. The 1,454 published Lindy rows are kept separate; the key
and value comparison counts below depend on this stated assumption.

| Reference claims absent from canonical WCS rows                           |   Count |
| ------------------------------------------------------------------------- | ------: |
| Belong to the 136 currently `not_found` IDs                               |     177 |
| Deliberately withheld because source claims conflict on the canonical key |     257 |
| Other claim absent from the latest lookup                                 |       1 |
| **Total**                                                                 | **435** |

The one other claim concerns WSDC 26385, German Open 2025-08 (`wsdc-282`),
newcomer leader, result `F`, zero points. Its removal is unresolved. Across
**196,244 shared keys**, results and points agree exactly; there are also
292 additional local WCS keys.

There are **303 conflicting registry key groups** overall, including the 257
that explain absent reference claims. The other 46 are outside that reference
missing-claim set. Competing claims remain in archived evidence and are
withheld from canonical placement rows. Resolving them requires stronger
source evidence or a reviewed representation for multiple claims.

Inventories: [435 absent reference claims](verification/2026-09-12/missing-data/registry-reference-missing-placement-claims.csv),
[303 withheld conflict groups](verification/2026-09-12/missing-data/registry-withheld-conflicts.csv).

### Historical results are present but mostly unlinked to events

Of **159,115 WCS registry placements dated 2010 onward**, only **20,835
(13.1%)** have canonical event IDs; **138,280** do not. At edition level,
**197 of 1,888 series/month occurrences map**, leaving 1,691 without a
canonical event link. Even from 2018 onward, only 197 of 1,004 occurrences
map. Since 2025, 142 of 297 map.

These rows retain their series, event name, month, role, division, result,
and points. The missing join prevents event-level confirmation and use alongside
score sheets. Missing historical event rows are a major cause; aliases and
registry-month versus event-date differences can also prevent mapping.
Examples include editions of MADjam, French Open WCS, BudaFest, Wild Wild
Westie, Liberty Swing, DCSX, and German Open. Relevant work is under
[#18](https://github.com/skeswa/swingset/issues/18) and
[#22](https://github.com/skeswa/swingset/issues/22).

Another **38,875 registry rows from 776 pre-2010 occurrences** have no event
ID. This is consistent with the draft 2010 event-catalog boundary and is kept
separate from the actionable 2010-on gap. All 1,454 Lindy rows are pre-2010.
Across both eras, 177,155 of 197,990 registry rows have null event IDs.

Every one of the reference's 362 series and 2,653 occurrences exists in the
local registry data. Local coverage adds two series and 11 occurrences.
The gap is chiefly event representation and mapping, not absent registry
series extraction. The literal division labels `PRO` (2,010 rows) and `TCH`
(100 rows) are preserved but their standard meanings remain unverified.

Inventories: [mapping by year](verification/2026-09-12/missing-data/registry-event-mapping-by-year.csv)
and [unmapped series/month occurrences](verification/2026-09-12/missing-data/registry-unmapped-occurrences.csv).

## 3. Participant and judge identities

| Published entry status |    Records | WSDC ID published?                  |
| ---------------------- | ---------: | ----------------------------------- |
| Confirmed              |     22,763 | Yes                                 |
| Probable               |     20,962 | Yes, inferred                       |
| Possible               |      7,479 | No                                  |
| Unmatched              |     25,139 | No                                  |
| **Total**              | **76,343** | **43,725 with IDs; 32,618 without** |

The 32,618 null IDs include **7,095 couple entries**, for which a single
individual ID would be inappropriate. Among **69,248 leader/follower entry
records**, **25,523 (36.9%)** lack IDs. These are repeated contest entries,
not a count of unique unidentified people. A probable ID is also not a
confirmed identity.

A separate consistency gap affects **26 source-confirmed scoring.dance entries
whose 13 asserted IDs have no dancer row**. For 21 entries, the ten IDs returned
verified `not_found` responses. The remaining five entries assert IDs **64,292,
94,704, and 236,870**, none of which has a captured lookup. These are unverified
outliers beyond the sequential sweep's range, not proof that valid registry
numbers extend that high. Here, `confirmed` means the results source supplied
the number; it does not mean the registry independently verified it. Review the
source assertions and perform targeted, gated lookups before changing links
or extending the sweep. These 26 rows are included in the 43,725 entries with
IDs, not in the null-ID count.

Inventory: [asserted IDs without a dancer record](verification/2026-09-12/missing-data/registry-entry-ids-without-dancer.csv).

Some Newcomer/Novice first-point finalists legitimately lack a number in the
original results. The owner's roughly one-week issuance delay is an
expectation, not a deadline. Recent unresolved eligible finalists need daily
new-ID discovery; older entries must remain available for later relinking.

Other identity gaps:

- **1,149 entry names are missing**, all in WDR data: 974 individual entries
  and 175 couple entries. Source masking and ambiguous attribution limit what
  can be recovered safely.
- **5,567 entry bibs are missing.** WDR finals bib ownership is unresolved in
  captured evidence, so a printed partner bib cannot safely be assigned to
  both people.
- **4,061 individual-role EEPro entries contain `and` in the source name.**
  This is a mixed-name risk signal, not proof every row is wrong. Of these,
  1,196 have probable IDs, 334 are possible, and 2,531 are unmatched. Resolve
  the source's partner/role structure before strengthening these links
  ([#17](https://github.com/skeswa/swingset/issues/17)).
- The unrestricted-division issue affects an audit set of 16,552 individual
  entries with `division=none`: 4,482 confirmed, 1,975 probable, 482 possible,
  and 9,613 unmatched. This is the set to inspect, not a count of incorrect
  identities ([#16](https://github.com/skeswa/swingset/issues/16)).
- **All 3,791 judge records have names and none is marked anonymous, but all
  lack WSDC IDs.** The linker has 2,562 possible and 1,229 unmatched judge
  records. Judge names are available for event-level analysis; identifying the
  same person across events still needs reconciliation. Not every judge is
  guaranteed to possess a WSDC number.

Inventories: [entry completeness by source and role](verification/2026-09-12/missing-data/details-entry-completeness.csv),
[identity status](verification/2026-09-12/missing-data/details-identity-status.csv),
[mixed-name entries](verification/2026-09-12/missing-data/details-role-ambiguity.csv),
[judge completeness](verification/2026-09-12/missing-data/details-judge-completeness.csv).

## 4. Missing result detail and withheld interpretations

The publication has 77,624 callback rows, 380,244 callback marks, 18,005
placements, and 101,647 final marks. It has **zero heat assignments**.
All 3,625 rounds also lack a structured chief-judge ID and `score_sheet_url`;
source URLs remain recoverable through snapshot/watch provenance, so a null
convenience URL is not lost archived evidence.

Twenty-eight published rounds have no canonical detail rows: 18 WDR finals, five
EEPro preliminaries, three scoring.dance preliminaries, and two WDR preliminaries.
Seventy-five rounds have no canonical judge marks: 71 WDR finals, two
scoring.dance finals, and two WDR preliminaries. These sets overlap. Empty or
unsupported source structures and intentionally withheld interpretations must
be checked before asserting how many rows should exist.

For diagnosis, the round inventory compares marks with detail rows multiplied
by the declared round judge roster. This is **not a valid missing-cell rate**:
the roster can include separate leader/follower panels, and sources can omit
cells or expose only selected outcomes. Neither those ratios nor
`entry_count × judge_count` establish the number of missing marks.

The public review queue contains:

| Finding kind        | Count | Meaning for missing data                                                                 |
| ------------------- | ----: | ---------------------------------------------------------------------------------------- |
| Unknown enum/value  | 2,506 | Uninterpreted values, including numeric/unsupported callback marks and WDR outcome codes |
| Registry difference | 1,231 | The reference differences explained above; many are additional IDs or whitespace         |
| Conflict            |   653 | Includes 305 entrant/judge callback-mark conflicts and 303 registry key conflicts        |
| Unsupported contest |   168 | EEPro formats retained without claiming full supported interpretation                    |
| Ambiguous entry     |   100 | Multiple entries share contest, role, and normalized name                                |
| Event alias         |    64 | Unmapped source events                                                                   |
| Parse warning       |    26 | Includes WDR outcome and finals-bib uncertainty                                          |
| Missing identity    |     1 | A redacted WDR entrant without a bib was omitted                                         |
| Parse failure       |     1 | The scoring.dance event-page layout failure described above                              |

The single explicit omitted entrant is in Desert City Swing 2026,
Intermediate J&J preliminary, source row 70. It cannot be reconstructed by
inventing an identity. Other withheld data includes 167 WDR `S<n>` callback
outcome findings and 15 WDR Am/Pro role-ambiguity findings. Their raw values
remain archived. The 653 conflicts include other categories beyond the two
large groups; findings and affected rows are not one-to-one.

For **6,141 placements in contests marked WSDC-points-eligible**:

- 1,720 have the published `registry_confirmed` flag.
- `points_matches_expected` is true for 1,796, false for zero, and null for
  **4,345 (70.8%)**. Null means unassessed or insufficient evidence, not zero
  points and not an observed mismatch.
- The registry preserves **77,896 `F` results**. Those records include their
  registry points, but do not establish an exact finishing rank. Mapped `F`
  evidence appears for 1,735 eligible detailed placements. Keeping exact-place
  point attachment unset can be correct in these cases; the points already
  exist in `registry_placements`.

Inventories: [per-round detail coverage](verification/2026-09-12/missing-data/details-round-detail-coverage.csv),
[placement completeness groups](verification/2026-09-12/missing-data/details-placement-completeness.csv),
[active findings](verification/2026-09-12/missing-data/details-active-findings.csv).

## 5. Metadata and operational gaps

The [field-by-field profile](verification/2026-09-12/missing-data/field-completeness.csv)
records null and blank counts for every published column. Highlights:

| Field                                 |      Missing / total | Interpretation                                                           |
| ------------------------------------- | -------------------: | ------------------------------------------------------------------------ |
| Event city                            |            404 / 573 | All non-calendar event rows lack this enrichment                         |
| Event region                          |            424 / 573 | Geographic coverage incomplete                                           |
| Event country                         |            412 / 573 | Limits geographic analyses                                               |
| Event website                         |            404 / 573 | Event homepages absent; source results provenance still exists           |
| Entry city and country                | 76,343 / 76,343 each | No entrant geography published                                           |
| Round chief judge and score-sheet URL |   3,625 / 3,625 each | Structured fields unpopulated                                            |
| Dancer merge target                   |      27,784 / 27,784 | No merge relationships represented; not evidence that no merges occurred |

Two dancer first names and two last names are blank. Not every null is a defect:
optional partner links, future-event results, ineligible-contest points, and
pre-2010 canonical event links have different expectations. Event override
placeholders also use approximate dates; the current schema does not expose
the draft `date_precision` field.

There was **no pending parse/project/link work and no active host pause** at
capture. This does not mean no collection remains: due watches are a separate
queue. The scoring.dance request limit was exhausted, leaving its 4,482 listed
rounds unfetched. The registry had spent 17,712 requests under the temporary
20,000-request sweep allowance; normal collection uses a 1,500-request limit
against the same daily counter. **155 confirmation watches and 20 new-ID probe
watches were due but could not fetch again until the UTC daily reset.** The
20 probe watches revisit IDs 29,029–29,048; they are fresh-check work, not
20 proven missing dancers. The next reset after capture is 6 p.m. Mountain
on September 12.

The last recorded private backup at capture was 6:04 a.m. Mountain, before the
sweep finished and before this publication. A backup matching this exact
publication was not yet recorded. This is a recovery-coverage gap, separate
from the contents already published on Hugging Face.

Integrity checks found **no missing or corrupt published files, schema or
manifest-count mismatches, duplicate primary keys, broken checked foreign
references, or registry-point evidence orphans**. Separately, all **29,622
referenced archived response bodies** and **29,616 referenced extracts** were
present and passed their SHA-256 checks. This verifies captured artifacts,
not the existence of undiscovered source pages or completeness of source claims.

Evidence: [publication integrity](verification/2026-09-12/missing-data/integrity.json),
[archive integrity](verification/2026-09-12/missing-data/archive-summary.json),
[operational state](verification/2026-09-12/missing-data/operations-summary.json).

## 6. Recommended order of work

| Priority                   | Work                                                                                                                                                                                                                                            | Expected effect and completion evidence                                                                                                                                                |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1                          | Repair mixed-person role attribution and unrestricted-division identity behavior ([#17](https://github.com/skeswa/swingset/issues/17), [#16](https://github.com/skeswa/swingset/issues/16))                                                     | Reduce wrong-link risk before expanding identity assertions; review affected rows and rerun linking with regressions                                                                   |
| 2                          | Drain already-listed scoring.dance round watches within source limits                                                                                                                                                                           | Highest-confidence collection gap: reduce 4,482 unfetched URLs to zero or individually explained outcomes; measure each event's remaining listed sheets                                |
| 3                          | Build 2010-on registry occurrence event rows, then reconcile aliases/month offsets ([#18](https://github.com/skeswa/swingset/issues/18), [#22](https://github.com/skeswa/swingset/issues/22))                                                   | Give 138,280 currently unlinked in-scope registry rows an evidence-backed path to canonical events; distinguish registry-only from score-sheet coverage                                |
| 4                          | Resolve 64 date-less source events and the event-179 parser failure                                                                                                                                                                             | Recover accepted metadata and discovered sheets; keep noncompetition/empty pages explicitly classified                                                                                 |
| 5                          | Investigate 136 current-absent historical IDs, 303 registry conflicts, and 13 asserted IDs without dancer records ([#10](https://github.com/skeswa/swingset/issues/10))                                                                         | Resolve or explicitly retain uncertainty for 435 reference claim gaps and 26 source-asserted entries; target the three unqueried outlier IDs without importing unverified replacements |
| 6                          | Implement missing source and historical adapters, then reconcile research leads ([#14](https://github.com/skeswa/swingset/issues/14), [#20](https://github.com/skeswa/swingset/issues/20), [#21](https://github.com/skeswa/swingset/issues/21)) | Reach history and platforms outside the present discovery paths; exclude cancelled/not-held editions and deduplicate aliases                                                           |
| 7                          | Expand supported score semantics, heat data, judge identities, and geographic enrichment                                                                                                                                                        | Reduce withheld detail with explicit source evidence; retain legitimate unknowns                                                                                                       |
| Operational follow-through | Verify daily probes resume after budget reset, complete a post-sweep backup, and collect live acceptance evidence ([#13](https://github.com/skeswa/swingset/issues/13))                                                                         | Establish freshness and recoverability without conflating the completed sweep with completed acceptance                                                                                |

These actions are recommendations from this audit. No production data, source
configuration, or issue status was changed to produce the report.

## 7. Method and reproducibility

The primary denominator is the immutable published Parquet candidate
`cand_9671fadae32d4be2`, public commit
`845d92a04e30b03d4a7081655e754ec2cea323aa`, built from repository revision
`064066d0ec64516ac96b2c3954971050df8219f3`.
A SQLite backup was captured at 16:37 UTC; the public baseline stayed unchanged
through capture, and counts match in all 15 shared public/state tables.
The public candidate and SQLite backup are separate snapshots; Parquet supplies
public metrics, while SQLite supplies watch, lookup, and finding evidence.

The reference dump's SHA-256 is
`ae7f2b9d688b69b49d08dfc718f60e5ef6b4b6561054d2503e8c94b8ae5e1d53`.
The captured SQLite SHA-256 is
`165dc25256c448f8a5b2c1f9183c45af9e07f60560be99f446192907bcd48ee9`.
[Capture metadata](verification/2026-09-12/missing-data/capture.json) and
[the captured issue list](verification/2026-09-12/missing-data/issues.json)
preserve the audit boundary. The large SQLite/candidate inputs remain in
`tmp/missing-data-2026-09-12/`; generated inventories and scripts are retained
under `research/`.

Capture a new local writer snapshot with
[`capture_missing_data.py`](capture_missing_data.py), supplying the state and
an empty output directory. It uses SQLite's backup API and copies the resolved
immutable publication. It does not pause collection or fetch origin pages.
Then run these offline audit scripts with the capture and output directories:

```sh
nix develop --command uv run python research/missing_data_registry.py tmp/missing-data-2026-09-12 research/verification/2026-09-12/missing-data
nix develop --command uv run python research/missing_data_events.py tmp/missing-data-2026-09-12 research/verification/2026-09-12/missing-data
nix develop --command uv run python research/missing_data_details.py tmp/missing-data-2026-09-12 research/verification/2026-09-12/missing-data
nix develop --command uv run python research/missing_data_operations.py tmp/missing-data-2026-09-12 research/verification/2026-09-12/missing-data
nix develop --command uv run python research/missing_data_fields.py tmp/missing-data-2026-09-12 research/verification/2026-09-12/missing-data
nix develop --command uv run python research/audit_candidate.py tmp/missing-data-2026-09-12/candidate tmp/missing-data-2026-09-12/integrity.json
```

[`missing_data_archive.py`](missing_data_archive.py) additionally accepts the
local archive root and an output JSON path. It verifies only artifacts
referenced by the captured SQLite state. The report does not use fresh third-party
scraping, extrapolate the total population of competitions or dancers, infer
hidden heat sheets, or treat research leads as verified source results.
