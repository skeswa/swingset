# Repository layout

```
swingset/
  design/                         these design documents
  README.md
  LICENSE                         MIT (code)
  pyproject.toml
  uv.lock
  flake.nix                       devshell: python 3.12, uv, node, system libs
  flake.lock
  nix/module.nix                  NixOS module: user, state dir, timers, env file
  config/
    hosts.toml                    politeness values per host
    sources.toml                  enable flags, index URLs, intervals
  overrides/                      hand-maintained CSVs, versioned in git
    event_aliases.csv
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
    fetch/     client.py politeness.py robots.py archive.py
    schedule/  watches.py policy.py discover.py
    sources/
      wsdc_registry/  parse.py fixtures/
      wsdc_calendar/  parse.py fixtures/
      eepro/          discover.py parse.py fixtures/
      scoringdance/   discover.py parse.py fixtures/
      dcn/            discover.py parse.py nuxt.py nuxt_eval.js pdf.py fixtures/
    model/     schema.py enums.py ids.py
    normalize/ names.py divisions.py events.py
    link/      candidates.py score.py assign.py confirm.py overrides.py
    build/     materialize.py invariants.py suppress.py review.py changelog.py manifest.py
    publish/   hub.py card.py
    backup/    push.py restore.py
    state/     db.py migrations/
  tests/
  .github/
    workflows/ci.yml              tests and lint only
    ISSUE_TEMPLATE/removal-request.md
    ISSUE_TEMPLATE/site-operator.md
```
