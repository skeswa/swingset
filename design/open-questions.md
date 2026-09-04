# Open questions and unverified items

Decisions still open:

1. Whether to pay for the scoring.dance API if it covers what we need.
2. Where the daily summary goes (journal only, or a webhook).
3. The hostname and time zone of the box, for the backup timer.

Things to verify during M0 to M2:

- WSDC calendar: does it emit `ETag` or `Last-Modified`? Is there a
  usable WP REST endpoint?
- Registry: response for a non-existent id; meaning of `adv_sliding` and
  `as_sliding`; whether a merged number is retired or redirected.
- EEPro: meaning of the prelims "Count" column (heat number or ordinal?).
- scoring.dance: whether round pages show heats; stability of Cloudflare
  behavior on `/enUS/events/` paths at our rate.
- DCN: whether the heats WebView is public; whether `ETag` on results
  pages changes only when content changes.
- Timing: how soon after scoring each platform posts a round. Measure it
  from our own snapshots over the first month and tune `live` intervals.
