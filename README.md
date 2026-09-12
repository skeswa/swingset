# swingset

swingset collects public competitive West Coast Swing results into a structured
Hugging Face dataset. It keeps source snapshots so corrections can be checked
and rebuilt. The [dataset is published](https://huggingface.co/datasets/skeswa/swingset);
source coverage and identity links are still being verified.

The dataset includes events, contests, rounds, competitors, judges, marks,
callbacks, placements, and WSDC registry records. Name matches carry an explicit
confidence and status. A likely match is not proof of identity.

History starts on 2010-01-01. swingset aims to trace every event from that date
onward; events that ended earlier get no rows, and the registry mirror is
published whole. See `design/backfill.md` for the start-date rule.

The collector identifies itself as
`swingset/0.1.0 (+https://github.com/skeswa/swingset)`. It honors robots.txt,
conditional requests, host budgets, and one request at a time per host. The
normal minimum gap is five seconds. Only an explicitly started registry sweep
uses the registry playbook's two-second exception.

Site operators can [open an issue](https://github.com/skeswa/swingset/issues/new/choose)
to ask us to slow down or stop. A `User-agent: swingset` robots group takes
precedence over `*` and is checked at most once a day. A block or challenge
pauses that host; the collector does not try to get around it.

To request removal of your personal data, use the removal-request issue
at the same link. Please identify the affected event or public result page;
do not post identity documents or other private information. Suppressions apply
to rebuilt public data. Contact us about affected test fixtures too.

Development requires Nix. Run:

```sh
nix develop
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run mypy
uv run swingset doctor --state ./tmp/state
uv run swingset cycle --dry-run --state ./tmp/state
```

Before committing hand edits, format them. The formatter toolchain is pinned
in `mise.toml`; `mise install` fetches it once per machine. Neither command
needs `nix develop`.

```sh
mise install
mise run fmt
```

`config/sources.toml` enables the calendar, registry, EEPro, scoring.dance, and
WDR sources and restates the history start date (`history_start = 2010-01-01`). The registry bootstrap requires an archived comparison dump and an
explicitly seeded sweep. Adapters can be tested offline. Publication requires
an explicit `--publish` (or `swingset publish`) and a provisioned token. The
reusable NixOS module defaults to dry runs; the selected OrbStack writer enables
publication.

See the [runbook](docs/runbook.md), [implementation status](docs/implementation-status.md),
and [design index](design/README.md). Code is MIT licensed. Source data has
separate provenance; the code license does not relicense third-party results.
