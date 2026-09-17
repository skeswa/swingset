# Step Right next control and admission gaps, 2026-09-17

## Finding

The retained controls now include one results-bearing event page. The archived
Asian Open 2013 event body remains a useful metadata-only control: its
capture is `20130401090810` (2013-04-01), before its printed April 25–28 dates,
and it contains no round links. It cannot establish contest-to-round listing
behavior. The 2013 preliminary/final pair controls selected cross-page facts,
but not the general promotion rule or final-bib ownership rule.

The retained Step Right CDX corpus supplies one narrow candidate locator for a
next event-body control: `20150711035813` for
`http://steprightsolutions.com:80/events/asianopen2015`. Its exact replay URL is
`https://web.archive.org/web/20150711035813id_/http://steprightsolutions.com:80/events/asianopen2015`.
The same CDX file contains 12 Asian Open 2015 round-page rows. The separately
acquired event body now verifies that it links exactly those 12 round IDs under
six contest headings and prints April 23–26, 2015. A responsive sidebar repeats
the links. This result comes from the body itself; the earlier CDX rows alone
did not prove it. The bounded locator audit is preserved in
[`locator-audit.json`](../../evidence/admission/stepright-next-controls-2026-09-17/attempt-001/locator-audit.json),
generated from the retained corpus and fixture receipt. Its generator records
zero network requests and zero production reads.

The reviewed robots refresh and final body operation are complete. The first
body request remains a failed `stopped_incomplete` attempt after a decoded-body
header bug; packet 003 was blocked before execution. D-0118 authorized a fresh
attempt after repair. Packet 004 made one direct request, retained the complete
33,253-byte body at SHA-256
`bc699e4e88dd8af53f495276dde4e3a2618e65b1f64cf7932d815cef00012357`,
and passed independent post-operation audit. No child, alternate or origin
request was made. The compact
[operation receipt](../../evidence/admission/stepright-body-2026-09-17/receipt.json)
preserves all attempts and boundaries.

## Current state and gaps

| Area                 | Implemented                                                                                                                                     | Tested / reviewed                                                                                                                                                  | Still missing                                                                                                                           |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| Step Right parsing   | Index, event, and round parsers emit source-specific records. The local event extractor scopes the dedicated header and main result panel.      | Five byte-exact bodies include a results-bearing event, metadata-only event, index and rounds 507/508. The correction passed focused tests and independent review. | Deploy through a new exact source candidate after the integrated gate passes.                                                           |
| Canonical projection | Projector 20 routes event details through source indexing and reconciles normalized Step Right round evidence in the common event contest path. | Focused dispatch/projection tests and independent review pass, including real 507/508 legends and mixed-source duplicate keys.                                     | Freeze, run full validation, deploy under hold and accept the exact production inputs.                                                  |
| Admission            | The index, event and round kinds each have contract version 1, exact body-shape and coverage witnesses, and no removal authority.               | Five retained controls pass independent contract review and a disposable schema-29 activation/admission rehearsal with zero guards or removals.                    | Keep production policies inactive until an exact authorized historical operation supplies its reviewed corpus.                          |
| History              | WP14 is scoped to 2010–2016. No year is accepted.                                                                                               | None of the parser/fixture tests accepts a year or authorizes historical watches.                                                                                  | Resolve year inventory and review; explicit year acceptance remains a separate owner decision before that year's historical processing. |

The main contract requirements are in
[`admission.md`](../../../docs/reference/recovery/admission.md): unit boundary,
expected body shapes, interpretation accounting, a coverage witness, guards,
removal authority, and maintenance. A real event page should therefore test
its declared child-link coverage, not just produce a successful parse. The
source guide also says the source is archive-only and explicitly lists the
results-bearing event page, canonical integration, kind enforcement, and
historical coverage as open.

The relevant code paths confirm these are activation gaps, rather than missing
URL recognition alone:

- `StepRightSource` registers three kinds and seeds no watches.
- `admission.contracts.KINDS` has no Step Right kinds; they therefore have no
  assessed contract version or enforce-mode policy path.
- `history.acquisition.phase_two_gate` blocks an unassessed kind, absent
  accepted year, or absent matching enforce policy.
- Canonical source-event projection consumes the existing `SourceEventRow`
  payload, while Step Right emits `StepRightIndexRow` and `StepRightEventSheet`.
  Canonical contest evidence consumes `RoundSheet`, while Step Right emits
  `StepRightRoundSheet`. These payloads need explicit reviewed projection
  mappings; recognition by the parser registry is insufficient.
- `PHASE1_KINDS` currently excludes Step Right, and `seed_watches()` is empty.
  The next implementation plan must specify how the first index observation is
  admitted and how its event-page children are scheduled without bypassing
  phase-one or phase-two controls.

## Bounded next work

1. Independently design source-kind contracts for index, event, and round
   units. Declare known omissions (including non-enumeration, mutable listings,
   mark-2 sub-values, promotion, and final-bib ownership) rather than silently
   mapping them. Tie coverage claims to retained inputs and explicit witnesses.
2. Implement canonical projection in a separate reviewed increment, with
   source-owned evidence and no inferred promotion, named-judge attribution,
   or final partner bib ownership. Add exact-kind enforce-mode tests only after
   contracts and independent reports are accepted.
3. Keep WP14 year review/acceptance and watch activation as later gates. Do not
   treat fixture acceptance, parser tests, or a complete local listing as year
   acceptance or publication approval.

## Evidence and limits

The candidate locator audit binds the retained CDX SHA-256
`b9562bc37c62b14161dafd34c570ad55e229a1dff44a833e573f7bf84bf77b46`, the
fixture-exception receipt SHA-256
`195c2ddcff02401f780c1591031125fe7d254d0a251179654dc448d55e1ae99a`, and the
real-fixture provenance SHA-256
`8323e486edb3eb8a4bcde7ba02e5ce1d45179889005c7ace75228224d73fac05`. It
records 21 relevant CDX rows checked and the one-body stop condition. This is
retained-metadata evidence only. The later body operation is separately bound
and independently reviewed in the compact receipt linked above.

The three Step Right module hashes in the earlier source-003 receipt were
`__init__.py` `651d361b3bdee0b64b464e8e6c6d97f3471d65c8321b1b6b0fcea1ca65fee64b`,
`adapter.py` `2b0a55d3321ac596b1cf7cddefde610ae9657e86a699a1539f2e475562414f71`,
and `records.py`
`c1ca6b73b1a53a573c1e68df3460a164fa9ecdc1cb3fc5e50f1b6e519e872508`. They
match the retained source-003 inventory bound by the DCN origin packet
(`source_receipt_sha256`
`60cdfe64004a0ba5a9aee207bcb089d6d4c81f5ca58169ccdb88a5dfab8146d6`). That is
historical inventory evidence. Candidate 006 is now the deployed held runtime;
its exact current source and system pins are in the
[body-runner design](stepright-body-runner-design-2026-09-17.md). The Step Right
kinds remain unassessed and inactive in admission; no Step Right historical
year or output is accepted or published. The earlier 35-test/Ruff/mypy receipt
describes its then-recorded validation snapshot and does not certify the later
parser correction. The body operation adds one reviewed quarantine capture,
but no source admission, runtime activation, year acceptance or publication.
