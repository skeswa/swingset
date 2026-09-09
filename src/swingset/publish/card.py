from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from swingset.build.builder import PUBLISHED_TABLES, BuildInput

SOURCE_LINKS = {
    "wsdc_calendar": ("WSDC event calendar", "https://www.worldsdc.com/events/"),
    "wsdc_registry": ("WSDC points registry", "https://points.worldsdc.com/"),
    "eepro": ("EEPro", "https://eepro.com/results/"),
    "scoringdance": ("scoring.dance", "https://scoring.dance/"),
    "dcn": ("danceconvention.net", "https://danceconvention.net/"),
    "wdr": ("World Dance Registry", "https://scores.worlddanceregistry.com/"),
}


def _rows(data: BuildInput, table: str) -> Sequence[Mapping[str, Any]]:
    return data.tables.get(table, ())


def _coverage(data: BuildInput) -> str:
    counts: Counter[tuple[str, str]] = Counter()
    for event in _rows(data, "events"):
        year = str(event.get("year") or "unknown")
        sources = event.get("sources") or [event.get("source") or "unknown"]
        for source in sources:
            label = f"{source} (override placeholder)" if event.get("snapshot_id") == "override" else str(source)
            counts[label, year] += 1
    if not counts:
        return "No events are published yet."
    lines = ["| Source | Year | Events |", "|---|---:|---:|"]
    lines.extend(
        f"| {source} | {year} | {count} |"
        for (source, year), count in sorted(counts.items())
    )
    return "\n".join(lines)


def _table_counts(data: BuildInput) -> str:
    lines = ["| Table | Rows |", "|---|---:|"]
    lines.extend(f"| `{name}` | {len(_rows(data, name))} |" for name in PUBLISHED_TABLES)
    return "\n".join(lines)


def _gaps(data: BuildInput) -> str:
    events = _rows(data, "events")
    covered_event_ids = {row.get("event_id") for row in _rows(data, "contests")}
    calendar_only = sum(1 for row in events if row.get("event_id") not in covered_event_ids)
    gaps = [
        f"- {calendar_only} of {len(events)} events currently have event metadata but no parsed contest results.",
        f"- `heats` has {len(_rows(data, 'heats'))} rows; public heat assignments are rarely available.",
    ]
    if not _rows(data, "dancers") or not _rows(data, "registry_placements"):
        gaps.append(
            "- The registry mirror is incomplete while `dancers` or `registry_placements` is empty."
        )
    gaps.append(
        "- Rows with `snapshot_id = override` are URL-override placeholders, not fetched source evidence. Their dates span the month encoded in the override ID; they are not verified event dates. Do not infer result coverage from these rows."
    )
    return "\n".join(gaps)


def render_card(data: BuildInput) -> bytes:
    configs = []
    for table in PUBLISHED_TABLES:
        default = "\n  default: true" if table == "placements" else ""
        configs.append(f'- config_name: {table}{default}\n  data_files: "data/{table}/*.parquet"')
    schemas = "\n".join(
        f"- `{name}`: " + ", ".join(field.name for field in data.schemas[name])
        for name in PUBLISHED_TABLES
    )
    source_lines = "\n".join(
        f"- [{label}]({url}) (`{source}`)" for source, (label, url) in SOURCE_LINKS.items()
    )
    return f"""---
configs:
{chr(10).join(configs)}
license: odc-by
---

# Swingset

Swingset is an evidence-preserving dataset of competitive West Coast Swing results.

## Load it

```sql
SELECT e.name AS event, p.place, leader.name_raw AS leader, follower.name_raw AS follower
FROM 'hf://datasets/skeswa/swingset/data/placements/*.parquet' p
JOIN 'hf://datasets/skeswa/swingset/data/events/*.parquet' e USING (event_id)
LEFT JOIN 'hf://datasets/skeswa/swingset/data/entries/*.parquet' leader
  ON leader.entry_id = p.leader_entry_id
LEFT JOIN 'hf://datasets/skeswa/swingset/data/entries/*.parquet' follower
  ON follower.entry_id = p.follower_entry_id;
```

DuckDB can run this query directly. Polars, pandas, and Hugging Face `datasets` can
read each table from the paths above. `placements` is the default config; for a
calendar-only snapshot, load `events` explicitly.

## Published coverage

This table is computed from the event rows in this exact dataset version. A source on
an event identifies its evidence source or an explicitly labeled URL-override placeholder; it does not establish complete contest or round coverage.

{_coverage(data)}

### Row counts

{_table_counts(data)}

### Known gaps

{_gaps(data)}

## Sources and attribution

{source_lines}

These sites remain the sources of their facts. Row-level `source` and `snapshot_id`
fields identify the captured evidence where the table supports them. Coverage varies
by source, event, round, and date; this card makes no claim of complete source or
registry coverage.

## Schema

{schemas}

## Updates and corrections

Collection runs every 15 minutes when work is due. `identity_links` carries current
identity confidence; `changelog` explains published changes. File corrections or removal
requests at https://github.com/skeswa/swingset/issues.

## Collection and personal data

Public calendars, registries, and score sheets are fetched serially per host, respecting
robots, Retry-After, and a five-second request floor. Results include public competitor
and judge names. Suppressed people keep structural rows with identity fields removed.
Raw bodies stay private. Source terms continue to apply.

## License and citation

The compilation and structure are ODC-By 1.0. This license does not replace source
terms or grant rights in source material. Attribute Swingset, cite this repository,
and pin the Hub commit used.

## Schema versions

The schema is pre-1.0. Migration notes will appear here when its version changes.
""".encode()
