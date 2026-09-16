# What Swingset does

Dance results are spread across websites, files, and the World Swing Dance
Council (WSDC) points registry. Swingset brings those public records together
in a dataset that people can query and study.

It aims to cover events from January 2010 onward. The registry is also mirrored
as a whole, including earlier records. This is a coverage goal, not a claim
that every result has been found. See the [history rules](reference/backfill.md#the-start-date-rule).

## A small example

Imagine a result sheet for “Autumn Swing 2026.” It lists a Novice contest,
several rounds, and a dancer wearing bib 42. Another page names the dancer.
The registry may later list that dancer's final placement and WSDC number.

Swingset saves these sources, reads the facts on each page, and works out
which records belong together. A bib number identifies an entry in an event;
it is not a permanent person ID. A likely name match remains a candidate until
the evidence supports a confirmed identity.

## What you can find in the data

- **Events and contests:** where and when competitions happened.
- **Entries and rounds:** who entered and which stages they danced in.
- **Scores and results:** judges' marks, advancement to later rounds, and placements.
- **Registry records:** WSDC numbers, names, points, and placements.
- **Quality information:** gaps, uncertain matches, and evidence needing review.

An event can have a registry record without its detailed result sheets.
Judges can be named without a known WSDC number. These are useful partial
records; Swingset should make their limits clear.

## Guiding rules

Save source evidence. Keep uncertain matches visible. Correct mistakes without
losing their history. Avoid unnecessary requests to source sites. Check a
proposed dataset before making it public.

## Non-goals (for now)

The public interface is the [Hugging Face dataset](https://huggingface.co/datasets/skeswa/swingset),
rather than a separate website or live competition feed. The project does not
seek private pages, social-media photos, or login-only results. It stores
registry points rather than publishing its own replacement points system.
The WSDC number is its person identifier; it does not mint new person IDs.
It keeps non-WCS rows when they come with source results, but does not seek them.

Follow the [pipeline walkthrough](how-it-works/README.md) to see how this works,
or read the [status page](status.md) for current limits.
