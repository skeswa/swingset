# Open questions and unverified items

> Historical record. Dates, status claims, and instructions below describe the
> original work. See [current status](../../docs/status.md) before acting.
> [Project journal](../README.md)

Decisions still open:

1. Whether to pay for the scoring.dance API if it covers what we need.
2. Where the daily summary goes (journal only, or a webhook).
3. The hostname and time zone of the box, for the backup timer.

Things to verify during M0 to M2:

- WSDC calendar: no `ETag` or `Last-Modified` (checked 2026-09-08);
  fingerprint the table. Is there a usable WP REST endpoint, or does
  the Yoast sitemap `lastmod` track calendar edits?
- Registry: response for a non-existent id; meaning of `adv_sliding` and
  `as_sliding` (most likely the 36-month window state used 2018-01-03 to
  2023-07-05, see [WSDC rules history](../../docs/reference/wsdc-rules-history.md); contents
  still unverified); whether a merged number is retired or redirected.
- EEPro: meaning of the prelims "Count" column (heat number or ordinal?).
- scoring.dance: whether round pages show heats; stability of Cloudflare
  behavior on `/enUS/events/` paths at our rate.
- DCN: whether the heats WebView is public. The `ETag` changes on every
  response (checked 2026-09-08), so it is useless; the open question is
  now whether the Nuxt client's tab-navigation JSON endpoint is public
  and allowed, which would cut DCN load about 50×.
- World Dance Registry: meaning of `S<n>` in the callback column, which
  partner's bib the finals show, whether the registration `euid` maps
  to the scores UUID, and whether the operator would publish an index.
- Timing: how soon after scoring each platform posts a round. Measure it
  from our own snapshots over the first month and tune `live` intervals.

Ideas from prior art (`journal/investigations/2026/prior-art-registry-analyses.md`), not
decided:

- A derived `dancer_milestones` table (first point per division and
  role, by month) for the progression questions every prior analysis
  computed by hand. Months only, no points, so it stays inside the
  no-derived-points rule.
- A worked card example for time between first points in consecutive
  divisions by cohort.

Things to verify for the 2010 backfill ([backfill](../../docs/reference/backfill.md#things-to-verify)):

- Step Right Solutions: promotion marking, sub-values of mark `2`, finals bib.
- Whether DCN `roundscores/*.pdf` captures exist in the archive.
- The archive's `X-RL` and `X-NA` headers; its terms wording, read by hand.
- How often the registry month differs from the end-date month.
- Whether an older WSDC domain carried the calendar before 2016.
- Which pre-2018 slugs EEPro still serves (from the operator).
