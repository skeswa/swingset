from __future__ import annotations

from swingset.build.builder import PUBLISHED_TABLES, BuildInput


def render_card(data: BuildInput) -> bytes:
    configs = []
    for table in PUBLISHED_TABLES:
        default = "\n  default: true" if table == "placements" else ""
        configs.append(f'- config_name: {table}{default}\n  data_files: "data/{table}/*.parquet"')
    schemas = "\n".join(
        f"- `{name}`: " + ", ".join(field.name for field in data.schemas[name])
        for name in PUBLISHED_TABLES
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
SELECT * FROM 'hf://datasets/skeswa/swingset/data/placements/*.parquet';
```

Polars, pandas, and Hugging Face `datasets` can read each table from the paths above.

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

The compilation and structure are ODC-By 1.0. Attribute Swingset, cite this repository,
and pin the Hub commit used. Underlying facts may have separate source terms.

## Schema versions

The schema is pre-1.0. Migration notes will appear here when its version changes.
""".encode()
