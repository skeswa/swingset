# Glossary

Terms are used exactly as defined here, in code and in data.

| Term | Meaning |
|---|---|
| WSDC | World Swing Dance Council. Runs the points registry and sets contest rules. |
| Registry | The WSDC points registry at `points.worldsdc.com`. |
| WSDC number / `wsdc_id` | The integer a dancer gets after their first point. Public, stable, and the best identity key we have. |
| Event | One weekend-long competition, e.g. "Swingtacular 2026". |
| Series | The recurring event across years, e.g. "Swingtacular". The registry has an id per series. |
| Contest | One competition inside an event, e.g. "Novice Jack & Jill". Some sources call this "division" or "competition". |
| Division | The WSDC skill level of a contest: Newcomer, Novice, Intermediate, Advanced, All-Star, Champion. Age divisions (Juniors, Sophisticated, Masters) are separate flags. |
| Contest type | Jack & Jill (random partner), Strictly Swing (chosen partner, improvised), Classic / Showcase (choreographed), Pro-Am, other. |
| Round | One stage of a contest: prelims, quarterfinals, semifinals, finals. |
| Entry | One competitor (or couple) in one contest, identified by bib and role. |
| Bib | The number pinned to a competitor. Assigned per event, usually per person for the whole weekend. Not related to the WSDC number. |
| Role | Leader or Follower. A couple entry (Strictly, Classic) has both people in one entry. |
| Heat | A group of entries that dance at the same time in one round. |
| Callback mark | A judge's mark in a non-final round: Yes, Alt1, Alt2, Alt3, or No. |
| Callback | The outcome for an entry in a non-final round: promoted, alternate, or eliminated. |
| Final mark | A judge's rank (1, 2, 3, ...) for a couple in the final. |
| Placement | A couple's final place in a contest, computed by Relative Placement. |
| Relative Placement | The majority-based ranking system used for WCS finals. |
| Tier | The WSDC bracket that sets points per placement, based on how many unique competitors danced in each role. |
| Snapshot | One archived HTTP response body with its headers and fetch time. |
| Watch | One request we monitor, including method and form data, plus its polling policy and parser. |
| Link | An assertion that an entry is a specific WSDC dancer, with method and confidence. |
| Run | One execution of the pipeline cycle. Has a `run_id`. |
| Observation | A source's statement about a calendar row, event, round sheet, or registry dancer, before canonical matching. |
| Source reference | An event or other subject's identity in its source, such as `eepro:asc2025`; it does not change when canonical matching changes. |
| Matching map | The current assignment of source events to canonical events, incorporating reviewed overrides. |
| Finding | Evidence needing human attention, such as a parse failure or conflicting source statements. |
| Publication candidate | One complete proposed version of the dataset, including its change history relative to a specific baseline. |
| Baseline | The last public dataset version acknowledged locally; the reference for the next changelog delta. |
