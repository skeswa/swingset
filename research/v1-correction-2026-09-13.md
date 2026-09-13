# V1 correction acceptance

The offline audit is implemented in `research/v1_correction_acceptance.py`.
It reads a published baseline, settled scratch state, complete archived
bodies, and optionally a built candidate. It has no publication or state
mutation path. The final restricted candidate passes the offline audit.
Publication was verified at `2026-09-13T05:43:31.765426+00:00`: public
commit `7cfcf4ec5dbc994d91f3e4d816f43b3abe16637b`.

## Verified correction evidence

Baseline entries `L-291` at Swing Crush 2026 and `L-22` at Jack & Jill
O'Rama 2026 incorrectly identify the paired names as leader WSDC 26843.
The settled replay preserves them as `C-291` and `C-22`, role `couple`,
unmatched, with null individual IDs and no individual candidate edges.

| Pair                                  | Retained original source                                      | Digest-verified snapshot             | Raw callback marks preserved |
| ------------------------------------- | ------------------------------------------------------------- | ------------------------------------ | ---------------------------- |
| Gabi Wasserman and Lynne Yun, bib 291 | `https://eepro.com/results/swingcrush2026/hilo.html`          | `snap_20260910T035507Z_10bd27defbfd` | 5 of 5                       |
| Ben McHenry and Lynne Yun, bib 22     | `https://eepro.com/results/jjorama2026/nonwsdcjjprelims.html` | `snap_20260910T044231Z_2c0d0a4f4c03` | 7 of 7                       |

The raw HTML rows print each complete paired name and bib together. Both
are prelims entries. Neither has a placement row in the baseline or
replay; this comparison does not assert a finals result.

All four audited unrestricted Lynne Yun entries retain candidate 26843
at score 1.0, with unavailable division evidence (`division_ok = null`).
Their contests stay `division = none`, their roles stay follower, and
their default IDs stay null. All 4,919 named/anonymous judge rows remain;
all judge IDs remain null. The separately reviewed H3 packet establishes
the judge scoring corrections, not a removal of published judge IDs.

## Required release restriction

The first settled candidate, `cand_fb0ee969809a4d12`, failed the strict
no-expansion check. It contained 251 new or changed default entry IDs:

| Evidence method    | New subject ID | Previously null subject | Changed existing ID |
| ------------------ | -------------: | ----------------------: | ------------------: |
| Source-provided ID |            117 |                       1 |                   0 |
| Registry placement |              0 |                     131 |                   2 |

All 251 originate in scoring.dance observations. None is a scoring-only
confirmation. Nevertheless, V1 does not authorize these default joins.
The 117 new IDs are additional contest subjects. Some share a bib and
name with old subjects in other contests; those old subjects still
exist, so this is not evidence of a canonical subject remap.

The release helper `build/identity_restriction.py` intersects default
entry and judge IDs with the exact baseline subject/ID pair. It retains
identity assertions and candidate evidence, and clears affected
placement IDs, registry points, and combined confirmation flags. It
returns the baseline commit and file digests for the release driver to
fingerprint. It does not change canonical state or add a configuration
flag. This is the bounded V1 prerequisite restriction, not acceptance
of H10's later general correction policy.

The unrestricted scratch has 58,246 baseline versus 36,966 current
default entry IDs. The restricted candidate must retain the 36,715
unchanged supported joins, withhold all 251 additions/changes, preserve
candidate evidence, and pass the paired-name and judge checks again.

## Reproduction

```sh
python research/v1_correction_acceptance.py \
  --baseline /var/tmp/swingset-v1-correction/baseline \
  --state-dir /var/tmp/swingset-v1-correction \
  --candidate /path/to/restricted/candidate \
  --output /path/to/acceptance.json
```

The script exits nonzero if parse/project/link work remains, either
audited correction loses source evidence or raw marks, an unsupported
default join remains, or a state expansion was not withheld. An offline
pass alone does not establish publication; the publication commit must
be recorded separately after the exact candidate is published.

## Final restricted candidate

The exact production candidate `cand_6627a6c9aa324e34` passes every
acceptance check against settled `/var/lib/swingset` and baseline
`997945b6bd8c48b950faa397051aa892f53d7935`. The generated receipt is
[`verification/v1-correction-20260913.json`](verification/v1-correction-20260913.json).

- Manifest SHA-256: `0d8083b4b149475c63325a3632a036af963e08ad6e62f6dfec5b509a1f4119a6`.
- Content hash: `5c29abc99d9eeed88870178a524fecc0daec7b8c018e1a345dcf477b09c92a29`.
- Publication policy: `v1_baseline_default_joins_v1`.
- Default entry IDs: 36,715 retained; all 251 additions/changes withheld.
- Unsupported default IDs, stale placement IDs, and default expansions: zero.
- Judge rows: 4,919; judge IDs: all null.
- Candidate edges: 94,102, including all four unrestricted counterexamples.
- Both paired names, their source provenance, and all 12 raw callback marks
  remain intact. Neither audited prelims entry gained a placement result.

This is an offline acceptance of the exact candidate. The receipt records
`publication_verified = false`; no publication was performed by this audit.

The separate production publication receipt confirms that exact manifest and
candidate at public commit `7cfcf4ec5dbc994d91f3e4d816f43b3abe16637b`.
The original offline audit receipt remains unchanged; its false publication
flag describes the audit's scope, not the subsequent verified publication.
