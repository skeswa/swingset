# Open questions and unverified items

Decisions still open:

1. Whether to pay for the scoring.dance API if it covers what we need.
2. Where the daily summary goes (journal only, or a webhook).
3. The hostname and time zone of the box, for the backup timer.

Things to verify during M0 to M2:

- WSDC calendar: no `ETag` or `Last-Modified` (checked 2026-09-08);
  fingerprint the table. Is there a usable WP REST endpoint, or does
  the Yoast sitemap `lastmod` track calendar edits?
- Registry: response for a non-existent id; meaning of `adv_sliding` and
  `as_sliding`; whether a merged number is retired or redirected.
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
