# Ethics, privacy, and legal

- **Public data only.** Every page we read is public, unauthenticated,
  and allowed by robots.txt at the time of fetch. We record the
  robots.txt snapshot alongside our fetches.
- **Names are personal data.** Competition results are published by the
  events with names, and the WSDC registry is public by design. We still
  provide a removal path: a `suppressions.csv` keyed by `wsdc_id` or by
  exact `name_raw` plus event. Suppressed rows keep their structural data
  (bib, marks, place) with name and `wsdc_id` nulled, so contest results
  stay consistent. Judges get the same path. Removal requests are GitHub
  issues using the removal-request template, and are applied within one
  publish cycle of being added to `suppressions.csv`.
- **GDPR.** Some competitors are in the EU. Whether GDPR binds a US hobby
  project with no EU presence is **unverified**. We act as if it does:
  legitimate-interest basis, public notice in the dataset card, erasure
  on request, minimal fields (no addresses, no birthdates).
- **Terms of service.** We never accept a clickwrap. We read each site's
  posted terms if any and record the date read in `docs/sources/`. If a
  site owner asks us to stop, we stop that source and say so in the card.
- **Load.** The politeness rules in [fetching](fetching.md) are stricter than what the
  sites already tolerate from search engines and other hobby scrapers.
- **Attribution.** The dataset card names every source. Row-level
  `source` and `snapshot_id` columns say where each fact came from.
- **License of scraped content.** Facts are not copyrightable in the US.
  Score sheets as arranged tables might be, in theory. We publish
  restructured facts, not the sheets. This is a risk we accept and note
  in the card.
