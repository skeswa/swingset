# Parsing

## Contract

A parser is a pure function:

```python
def parse(body: bytes, ctx: ParseContext) -> ParseResult
```

`ParseContext` carries `snapshot_id`, `url`, `source`, `kind`, `event_id`,
`fetched_at`. `ParseResult` carries a list of raw records in the source's
own vocabulary, a list of new watches to create, and a list of warnings.
Parsers raise `ParseError` on structural surprise (missing table, unknown
column). They never do I/O.

Each parser has a `PARSER_VERSION` string. Records store it. Bumping the
version marks all snapshots of that kind for re-parse.

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
