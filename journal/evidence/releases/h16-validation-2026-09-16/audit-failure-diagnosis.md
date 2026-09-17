# H16 scratch audit diagnosis, 2026-09-16

The build completed, but its independent audit correctly rejected the candidate.
The failures come from existing projection and reconstruction behavior, not from
judges lacking WSDC numbers. No candidate, replay, baseline, or production state
was changed during this investigation.

## Bound artifacts

- Frozen source: `/nix/store/4c4q8rkiz6jqizcy93bid6cz8mfvp20b-source`.
- Source receipt SHA-256: `71dc14101ca19880f3addfa2dc0073dbe50bce0e4e2be27d7bc91811b6d1e338`.
- Scratch candidate: `/var/tmp/swingset-h16-proof-build/candidates/cand_cbbeeadd90634cc9`.
- Candidate manifest SHA-256: `cf0deef44cc7a71099fea643431b5f899e0e2f42b79f2ec8435074e4ed7c0c08`.
- Baseline candidate: `/var/tmp/swingset-h16-proof-build/candidates/cand_7f8cf9bcbf7e4a60`.
- Baseline public commit: `81121dcc7d62a1db9e76ee1c49a6b90e1f4c5653`.
- Accepted input bundle: `410bd096dec912921c41702e1dc8fa5ef610385fd07afef52104561069eadcb8`.

## Findings

1. Mapping overwrites 208 existing registry occurrence identities. The selected
   inventory output preserves their baseline `wsdc-*` series and reporting
   month. The subsequent map output replaces each with a provisional `slug-*`
   event, `held=listed`, and empty history. The map's no-candidate branch
   synthesizes a full event with an ID that can already belong to an enriched
   inventory event. Its candidate search excludes `wsdc_status=unknown`, and
   source dates can differ from registry reporting dates.
2. History association joins exact series and reporting month. After those
   overwrites, 22,201 registry placement rows have NULL `event_id` in canonical
   scratch state already. This is not a public-table audit comparison error.
   Of the 208 overwritten occurrences, 161 disappear from public output and 47
   remain with the wrong identity. This accounts for all 208 failed occurrence
   checks.
3. Reconstruction rejects 164 full event payloads: 161 map rows and three
   calendar rows. All 164 event IDs also have another admissible full event
   payload in the selected generation set. Rejections are accumulated without
   regard to that alternate valid base. `_close` treats any rejected row as
   grounds to omit the entire event and its descendants. All 164 are missing
   from public output.
4. All 1,513 absent baseline judge rows belong to 52 of those rejected events.
   Project and link generations still contain their judge records; canonical
   scratch state retains all 4,931 baseline judges. Public output has 3,418.
   Exactly 1,512 of the absent rows have retained source locator bindings. The
   remaining row is `2024-03-5280-westival/judge/unknown`, whose name is `---`;
   it has no matching judge locator in its retained snapshot. The audit's
   1,512-source-supported violation count is therefore correct.
5. The three rejected calendar rows have a selected `wsdc_status=registry`
   where the baseline has `unknown`: DC Swing eXperience (November 2026),
   Warsaw Halloween Swing (November 2026 ID), and the third event named in
   `calendar_rejected_examples`. Each also has an admissible inventory base.
   Fixing map projection alone does not resolve this reconstruction veto.

A concrete first row is `2020-01-municorn-swing`. Selected inventory generation
`dg_63021dc9385f4af64051562fb8313f7a4e76f000a52223779968d6bef81f8e50`
contains the baseline-matching `wsdc-291` event with newsletter provenance.
Selected map generation
`dg_3f7e85b70c428d5434f4f4d8da780a27b758cc8385e0d4f6469c3e29309b0a07`
changes it to `slug-municorn-swing`, scoringdance provenance, and a different
end date. This legacy-unassessed map payload fails exact baseline comparison.
Reconstruction removes the valid inventory event and its 25 judges anyway.
The 54 retained registry rows for `wsdc-291` / January 2020 become unassociated.

## Repair boundaries

- `src/swingset/project/map.py`: preserve an existing inventory-backed event
  when a synthetic unmatched ID collides with it. Preserve its registry
  identity, source facts, provenance, and durable structural ownership. Merely
  widening the candidate status filter does not cover differing date spans.
  Do not freeze updates to genuinely provisional source-only events.
- `src/swingset/build/closure_rows.py`: distinguish an inadmissible alternative
  structural overlay from a missing/revoked required source fact. A valid
  selected event base must not be erased by an unsupported alternative event
  payload. Keep explicit revocation and unsupported descendant handling
  covered by tests; do not loosen source admission or silently copy live facts.
- Semantic affected scopes: map, dependent event/source-event projection,
  history associations, linking, and release reconstruction. With the current
  whole-package runtime recipe, any new frozen code changes the recipe input
  for every project/link scope. A normal accepted source update therefore
  requires a fresh fully current replay, not merely replacing the map rows.
- Acceptance: 208 occurrence identities restored; matching registry event
  associations; all baseline judges preserved; all 54 independent audit checks
  pass; source withdrawal and source-only update regressions remain covered.

`diagnostic-source-equality.json` proves map, history, materialization, and
closure reconstruction files are byte-identical in original base source
`2d5q...` and proof-cache source `4c4q...`. These are pre-existing semantics
exposed by replay and the independent audit, not missing runtime overlay files.

## Retained evidence and execution limits

- `audit-row-diagnostic.json`: bounded first differing rows and public/canonical
  counts; completed in 0.34 seconds.
- `event-ownership-diagnostic-002.json`: selected event ownership and source
  support; completed in 3.05 seconds.
- `event-ownership-diagnostic-003.json`: all affected occurrence identities,
  alternate admissible bases, and examples; completed in 3.03 seconds.
- `judge-binding-gap.json`: exact retained-locator count difference; completed
  in 0.31 seconds with the reference cache cleared between snapshots.
- `event-ownership-diagnostic.json`: retained failed first diagnostic, caused
  by missing optional `pytz` when DuckDB decoded full timestamp rows. The next
  new script used PyArrow for those rows; no environment was changed.

Diagnostics ran sequentially with normal read-only SQLite connections,
`PRAGMA query_only`, one DuckDB thread, 256–512 MB DuckDB bounds, finite alarms,
and no network requests. Every successful receipt reports zero SQLite changes.
No replay, build, deployment, or publication was launched by this investigation.
