# D-0259: SwingDirector callbacks come from the next round; initial-form judges follow name suppressions

Recorded: 2026-10-15  
Decided by: agent, 2026-10-15, under D-0172; source: session instruction of 2026-10-15 to build the SwingDirector projector  
Topic: Source projection and suppression  
Supersedes: in part, D-0258's note that the 2026 rows need event alias rows  
Superseded by: —

## Decision

- SwingDirector prelim and semifinal sheets print marks, not who advanced.
  The projector takes the outcome from the contest's next round: a leader
  advanced when the next round prints their bib, a follower entering a final
  when the final prints their name. It derives only when every next-round
  entrant matches one row and at most one non-advancing row outscores the
  lowest advancing row (Y 10, A1 4.5, A2 4.3, A3 4.2). Rows below that cut
  are `eliminated`; non-advancing rows at or above it have no outcome.
- A bib printed `(Name hidden for discretion)` in any round is nameless in
  every round of its contest and role, keeps its bib, and publishes no
  marks, as WDR's redacted rows do.
- A name suppression also covers a judge printed as initial and surname
  (`C. REED` for Carol Reed), at build time, for judges only.
- A `swingdirector` row in `source_urls.csv` maps its event by `event_id`,
  as WDR rows map by URL; no alias rows are needed.

## Why

The sheets of three 2026 events (D-0258) print every judge as initial and
surname, so a full-name suppression would miss them. Every captured
prelim-to-next-round pair matches by bib or name, and the advancing rows are
the top scorers except one Calgary follower who did not dance the final
(a scratch, **unverified**) and one tie at the cut. Skipping a missing semifinal makes many semifinalists outscore
the finalists, which the one-row limit detects.

## Alternatives and consequences

Leaving callbacks empty loses the most-used prelim fact. Treating every
non-finalist as eliminated would mislabel scratches and tie-breaks. A
wider initial match on entries would hide unrelated dancers who share an
initial and surname. Cost: a small missing round can still pass the limit
(**unverified** on events not yet captured), and `promoted_count` leaves out
hidden rows.

## Links

[Long-tail guide, SwingDirector PDFs](../../docs/reference/sources/long-tail.md#swingdirector-score-pdfs)
