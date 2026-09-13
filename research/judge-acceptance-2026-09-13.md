# H3 judge acceptance packet

The retained evidence supplies concrete judge candidate counterexamples. It
does not supply false published judge IDs: all 4,919 judge IDs in baseline
`997945b6bd8c48b950faa397051aa892f53d7935` are null. The earlier audit likewise
reported 3,791 judges with null IDs. V1's requirement to remove known false
published judge links cannot literally be demonstrated against either state.
The [baseline receipt](../tests/fixtures/identity/judge-audit-2026-09-13/baseline-summary.json)
pins the checked parquet digest.

## Evidence for review

Each judge name appears in a retained round's judge header. A separate results
sheet from the same event prints that full name beside the supported WSDC ID.
The two registry responses name different dancers. This is independent source
support for the comparison, not a human adjudication and not an ID printed on
the judge header itself.

| Judge and event                             | Source-supported identity | Competing registry candidate | Why the candidate needs less confidence                                                  |
| ------------------------------------------- | ------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------- |
| Tze Ming Wee, Asia WCS Open 2026            | 9285, Tze Ming Wee        | 8785, Tze Yi Wee             | The old nickname signal ignores the conflicting given-name tokens Ming and Yi.           |
| Emily Huang, City of Angels WCS 2026        | 16071, Emily J. Huang     | 18139, Emily Hung            | The surnames differ. A missing middle initial is compatible with the supported identity. |
| Camille Lange, Augsburg Westie Station 2025 | 8395, Camille Lange       | 495, Camille Lane            | The surnames differ; the supported identity matches exactly.                             |

The [fixture manifest](../tests/fixtures/identity/judge-audit-2026-09-13/manifest.json)
records source URLs, snapshot IDs, capture times, all original candidate
signals, complete registry records, and each body's SHA-256. Twelve complete
response bodies are retained beside it. No source was fetched for this review.

The specific pages are:

| Comparison    | Judge roster                                                                      | Independently printed entrant ID                                                  |
| ------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| Tze Ming Wee  | [Event 333, result 5654](https://scoring.dance/enUS/events/333/results/5654.html) | [Event 333, result 5660](https://scoring.dance/enUS/events/333/results/5660.html) |
| Emily Huang   | [Event 315, result 5697](https://scoring.dance/enUS/events/315/results/5697.html) | [Event 315, result 5702](https://scoring.dance/enUS/events/315/results/5702.html) |
| Camille Lange | [Event 310, result 4364](https://scoring.dance/enUS/events/310/results/4364.html) | [Event 310, result 4366](https://scoring.dance/enUS/events/310/results/4366.html) |

The source-supported Tze Ming record has Advanced points and is excluded by
the existing judge candidate pool. The Tze Yi record has Champion points and
enters that pool. H3 lowers the contradictory candidate's score without
silently expanding that pool. This separate recall gap remains visible for
the later accuracy work.

## Implemented check

Linker version 8 keeps every candidate and original name signal. For judges,
single-token names, different surnames, and conflicting given-name tokens
score at most 0.89. Exact names, ordered omissions of middle tokens, and
curated first-name nicknames keep their evidence. The unavailable-role weight
is still omitted. The global acceptance threshold stays 0.9.

The three source-supported identities score 1.0 when evaluated. Their
competing candidates score below 0.9. The full event linker creates no default
judge IDs for any comparison. Scoring still produces only `probable` or lower
statuses; it cannot create `confirmed` by itself.

```sh
uv run pytest tests/test_judge_audit.py tests/test_identity_corrections.py tests/test_link.py -q
```

The 50 tests passed on 2026-09-13. Tests independently parse the judge headers,
printed entrant IDs, and registry bodies, verify every used body digest,
rescore both candidates, and check null default judge IDs. Synthetic controls
remain separate from these retained-source cases.

## Owner review

Sandile Keswa read and approved this packet in the working session on
2026-09-13 UTC (2026-09-12 in America/Denver). The three supported identities
and contrasting negative candidates are accepted for H3 regression review.
The owner also noted that some judges have no WSDC numbers. Such judges
remain valid named source records with null IDs; this approval does not
authorize matching every judge or expanding default joins.

The [V1 criterion](../design/implementation-plan-v2.md#6-decisions-this-plan-makes)
now names the actual wrong paired-name dancer joins and preserves the
withheld judge IDs. The absence of published judge IDs is not counted as a
removal. No H8 journal rows were created before its gate. The separate H17
representative-sample review remains pending.
