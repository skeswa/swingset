# Parsing

## Contract

Each page kind has two pure functions:

```python
def extract(body: bytes) -> Extract
def parse(extract: Extract, ctx: ParseContext) -> ParseResult
```

`extract` turns bytes into the page's content in the source's own
vocabulary, with no context: for an EEPro round, the contests with
their header rows and cells; for a DCN page, the evaluated `results`
subtree; for a WDR file, the decoded JSON. It is the only code that
reads the body. Its result is a plain, canonically serializable value
(sorted keys, no floats as text), and `sha256(canonical(extract))` is
the fingerprint used by the fetch layer. Because the fingerprint is
the hash of everything `parse` will consume, attributes included, a
change the parser would see is always a change the fetcher sees, and
nothing is parsed twice.

`parse` attaches provenance and builds records. `ParseContext` carries
`snapshot_id`, `url`, `source`, `kind`, `event_id`, `fetched_at`.
`ParseResult` carries raw records, new watches to create, and warnings.

Order at fetch time: fetch, `extract`, compare fingerprint. Unchanged:
discard the body, record `checked_at`. Changed: archive the body, store
the extract as a derived blob, then `parse` later from the derived
blob. `extract` raising `ExtractError` (missing table, unknown column,
payload that does not evaluate) is treated as changed: the body is
archived with `extract_status = failed` so it can be diagnosed, the
watch keeps its previous fingerprint, and the run counts the failure.
Neither function does I/O.

Each page kind has an `EXTRACT_VERSION` and a `PARSER_VERSION`. Records
store both. Bumping `EXTRACT_VERSION` re-extracts every archived body
of that kind and recomputes fingerprints; bumping `PARSER_VERSION`
re-parses from the stored extracts.

## Parsers to build, in order

| Parser | Input | Output records |
|---|---|---|
| `wsdc_registry.dancer` | `/lookup2020/find` JSON | dancer, registry_placement |
| `wsdc_calendar.print_list` | print list HTML | calendar_event |
| `eepro.index` | `event.php` | source_event, round watches |
| `eepro.round` | `<contest><round>.html` | contest, round, entry, judge, callback_mark, final_mark, placement |
| `scoringdance.recent` | `/enUS/recent`, sitemap | source_event, watches |
| `scoringdance.event` | `/events/<id>/results/` | contest, round, round watches |
| `scoringdance.round` | `/results/<roundId>.html` | entry (with wsdc_id), judge, callback_mark, final_mark, placement |
| `dcn.list` | upcoming / archive HTML | source_event |
| `dcn.event_results` | results tab HTML | contest, round, ranking (names, place), PDF watches |
| `dcn.round_pdf` | roundscores PDF | entry (with bib), judge, callback_mark, final_mark |

## Parsing rules

- HTML is parsed with `selectolax` (fast, lenient). Tables are read by
  header text, never by column position alone, because judge counts vary.
- Judge columns are identified by header text or `title` attribute. A
  judge who appears as "Judge 1" is stored with `anonymous = true`.
- Marks are stored as the source printed them (`Y`, `A1`, `10`, `4.5`,
  `1`, `2.1`) in `mark_raw`, and as a normalized enum plus numeric value.
  Both legends (10/4.5/4.3/4.2/0 and 1/2.1/2.2/2.3/3) map to the same
  enum: `yes`, `alt1`, `alt2`, `alt3`, `no`.
- Names are stored exactly as printed in `name_raw`. Normalization is a
  separate step.
- The DCN Nuxt payload is evaluated by a `node` subprocess from nixpkgs
  running a small fixed script (`sources/dcn/nuxt_eval.js`) that defines
  `window`, evaluates the payload, and prints JSON to stdout. The
  subprocess gets the payload on stdin, no arguments, no network, no
  filesystem beyond the script, and a 5-second timeout. The Python side
  is a pure function `evaluate_nuxt(body: bytes) -> dict` so the engine
  can be swapped without touching the parser.
- A contest whose table layout the parser does not understand is still
  emitted as a `contests` row with `parse_status = unsupported` and no
  rounds, so coverage gaps are visible in the data.
- PDFs are read with `pdfplumber`. Tables are located by the "Result"
  header and judge legend. Each PDF parser fixture must include at least
  one prelims and one finals sheet.
- A parser that returns zero records from a body that previously
  produced records raises. Silent emptiness is a bug.

## Fixtures and tests

Every parser has a `fixtures/` folder with real archived bodies (trimmed
of nothing, so the test is honest) and the expected records as JSON.
Fixture bodies come from our own archive so tests never hit the network.
`pytest` runs them all. A parser change that alters expected output must
update the fixture in the same commit.
