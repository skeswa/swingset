# Overview

## Purpose

`swingset` is a Hugging Face dataset of competitive West Coast Swing (WCS)
data. It covers:

1. Event participation: who entered which contest at which event.
2. Heat lists: which heat each competitor danced in, when known.
3. Callbacks: per-judge marks and who advanced from each preliminary round.
4. Scoring results: per-judge ranks and final placements.
5. The WSDC registry: every dancer's WSDC number, name, points, and
   placements, mirrored from the official registry.

History starts on **2010-01-01**. `swingset` aims to trace every event
from that date onward and none before it: an event that ended earlier
gets no rows, while the registry mirror is published whole and earlier
registry placements keep a null event id. The date is the project's
enshrined start ([backfill](backfill.md#the-start-date-rule),
[glossary](glossary.md)); it is restated in `config/sources.toml`, checked
at build, and printed in the dataset card and manifest.

The dataset updates quickly. Most WCS events run Friday to Sunday and post
results during and just after the weekend. `swingset` polls harder on
weekends and rests on weekdays.

This repository holds the code that collects, cleans, links, and publishes
the data. The data itself lives on Hugging Face.

## Guiding rules

These rules decide every trade-off in this design.

1. **Prefer an API. Scrape only when there is none.** Today there is no
   public API for any of the sources. So we scrape, but we treat every
   source as if its owner is watching, because they might be.
2. **Spend compute, not bandwidth.** Fetch a page again only when we have
   reason to believe it changed. Parse and re-parse from our own archive
   as often as we like.
3. **Be a good guest.** Obey `robots.txt`. Identify ourselves. One request
   at a time per host. Back off on any error. Never log in. Never use
   endpoints meant for the event's mobile app unless they are clearly
   public.
4. **Best effort, corrected over time.** Matching bib numbers to names to
   WSDC numbers is guesswork at first and gets confirmed later. Every guess
   carries a confidence and a method. Every correction is recorded.
5. **Reproducible.** Every published row can be traced to a raw snapshot
   in our archive and to the parser version that read it. Deleting the
   whole output and rebuilding from the archive gives the same output.
6. **Boring technology.** Python, SQLite, Parquet, systemd, Hugging Face
   Hub. One Linux box we already run, and nothing else to operate.

## Non-goals (for now)

- No user-facing website or API. Hugging Face's viewer, SQL console, and
  `hf://` paths are the interface.
- No scraping of Facebook, Instagram, or photos of printed sheets.
- No scraping of pages behind a login or a mobile-app-only channel.
- No published point computations. We store what the registry says. The
  only derived field is `points_matches_expected`, a check flag that
  appears after the registry has posted ([data model](data-model.md#tables)).
- No swingset-minted person ids. The WSDC number is the only identity.
  Entries without a confident match stay unmatched ([identity linking](identity-linking.md)).
- No live "who is dancing right now" feed. Minimum poll interval during an
  event is 15 minutes.
- No non-WCS dances, even though some sources (and the registry) also
  carry Lindy or Country. We keep those rows if they come for free but do
  not chase them.
- No events before 2010-01-01. Earlier history exists in the registry
  mirror only ([backfill](backfill.md#the-start-date-rule)).
