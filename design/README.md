# swingset design

Status: draft v0.2, 2026-09-04. Owner: Sandile Keswa.

These documents are the foundational design for `swingset`, a Hugging
Face dataset of competitive West Coast Swing data and the code that
collects it. They are written to be exhaustive. Where a later, more
specific document (a source playbook, the runbook, the schema reference)
disagrees with one of these, the later one wins and the design document
must be updated.

Facts about third-party sites were checked on 2026-09-04 and, for the
fetch layer, again on 2026-09-08 (`research/verification/`). Facts we
could not check are marked **unverified**. Per-site operating detail
lives in the playbooks under `docs/sources/`, which win over these
documents.

## Read in this order

| Document | What it covers |
|---|---|
| [overview](overview.md) | Purpose, guiding rules, non-goals |
| [glossary](glossary.md) | Terms used exactly as defined, in code and data |
| [sources](sources.md) | Every data source: what it gives, how we read it, URL patterns, registry JSON shape |
| [architecture](architecture.md) | The pipeline stages and how they connect |
| [fetching](fetching.md) | Identity, politeness rules, change detection, raw archive |
| [scheduling](scheduling.md) | Watches, polling states and intervals, discovery, backfill |
| [parsing](parsing.md) | Parser contract, parsers to build, parsing rules, fixtures |
| [data model](data-model.md) | Identifiers and every published table |
| [identity linking](identity-linking.md) | Bib to name to WSDC number, confidence, retroactive correction |
| [build](build.md) | Parquet materialization, invariants, suppression, changelog |
| [publishing](publishing.md) | Hub repos, layout, commit strategy, dataset card, consumer examples |
| [technology](technology.md) | Language, libraries, packaging |
| [operations](operations.md) | Host, systemd timers, backup and restore, secrets, observability |
| [ethics and legal](ethics-and-legal.md) | Public data, personal data, terms, license of scraped content |
| [repository layout](repository-layout.md) | Where code, config, overrides, and docs live |
| [milestones](milestones.md) | M0 through M6 and their done criteria |
| [scraping plan](scraping-plan.md) | Phase-by-phase plan for the fetch and parse side, per-host budgets, operator conversation |
| [implementation plan](implementation-plan.md) | v1 scope (M0 to M3b), the OrbStack NixOS VM, toolchain decisions, SQLite schema, work packages, order, and done criteria |
| [open questions](open-questions.md) | Decisions still open and facts to verify |
| [decision log](decision-log.md) | Index of decisions from the design review |
| [WSDC rules](wsdc-rules.md) | Callback legends, points tiers, level thresholds we encode |
| [references](references.md) | Standards, docs, and prior art |
