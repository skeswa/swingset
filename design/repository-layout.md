# Repository layout

```
swingset/
  design/                         these design documents
  README.md
  LICENSE                         MIT (code)
  pyproject.toml
  uv.lock
  uv.toml
  flake.nix                       devshell: python 3.12, uv, node, system libs
  flake.lock
  nix/module.nix                  NixOS module: user, state dir, timers, env file
  nix/hosts/orb.nix               v1 VM host configuration
  config/
    hosts.toml                    politeness values per host
    sources.toml                  enable flags, index URLs, intervals
  overrides/                      hand-maintained CSVs, versioned in git
    event_aliases.csv
    source_urls.csv               event_id, source, kind, url, parser: WDR and long-tail watches
    identity_overrides.csv
    suppressions.csv
    nicknames.csv
  docs/
    enums.md
    sources/<source>.md           per-source playbook: URLs, selectors, terms read, quirks
    runbook.md
  src/swingset/
    cli.py
    config.py
    clock.py
    log.py
    fetch/     client.py classify.py politeness.py robots.py archive.py wayback.py
    schedule/  watches.py policy.py discover.py cycle.py
    sources/
      base.py                     source and page-kind contracts
      wsdc_registry/  parse.py fixtures/
      wsdc_calendar/  parse.py fixtures/
      eepro/          discover.py parse.py fixtures/
      scoringdance/   discover.py parse.py fixtures/
      dcn/            discover.py parse.py nuxt.py nuxt_eval.js pdf.py fixtures/
      wdr/            discover.py parse.py fixtures/
      generic/        html_table.py pdf_table.py google_drive.py swingfiction.py fixtures/
    model/     schema.py enums.py ids.py observations.py canonical.py
    project/   writer.py events.py registry.py contests.py
    normalize/ names.py divisions.py events.py
    link/      candidates.py score.py assign.py confirm.py overrides.py weights.toml
    build/     materialize.py invariants.py suppress.py review.py changelog.py manifest.py
    publish/   hub.py card.py candidate.py card_template.md
    backup/    push.py restore.py
    state/     db.py work.py findings.py migrations/
  tests/
  .github/
    workflows/ci.yml              tests and lint only
    ISSUE_TEMPLATE/removal-request.md
    ISSUE_TEMPLATE/site-operator.md
```

v1 creates the shared modules and the calendar, registry, EEPro,
scoring.dance, and WDR adapters. `dcn/`, `generic/`, and `wayback.py` wait
for later milestones. Module ownership and contracts are defined in
[architecture](architecture.md#module-boundaries); work order lives in
[implementation plan](implementation-plan.md).
