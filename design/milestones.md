# Milestones

Each milestone ends with a published dataset that is strictly better than
the last one.

| # | Deliverable | Done when |
|---|---|---|
| M0 | Skeleton: flake, NixOS module, CLI, config, fetch layer with politeness and archive, SQLite state, timers, backup and restore | a cycle runs every 15 min on the box; a restore on a clean VM works |
| M1 | Registry mirror: bootstrap sweep with dump cross-check, weekly probe, trickle refresh, `dancers` and `registry_placements` published | dataset viewer shows both tables; card written |
| M2 | EEPro: discovery, round parser, canonical model, `events` through `placements` published, name-only linking, `link_candidates` and `review_queue` | a full past event is queryable end to end |
| M3 | scoring.dance: parser with WSDC ids; linker weights fit on its data; registry confirmation loop; `points_matches_expected` | `confirmed` links appear automatically within 7 days of an event |
| M4 | DCN: node-evaluated payload and PDF parsers, bib backfill from PDFs | DCN events reach parity with EEPro |
| M5 | Heats where public, judge linking, `changelog` documented with examples, suppression path tested end to end, issue templates live | every data kind listed in [overview](overview.md#purpose) is represented |
| M6 | Backfill: EEPro year indexes, scoring.dance archive, DCN archive years, newest first at lowest priority; schema declared 1.0 | coverage table in the card lists every year and source |
