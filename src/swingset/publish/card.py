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
    inventory = [row for row in _rows(data, "coverage") if row.get("scope_kind") in (None, "year")]
    if inventory:
        lines = [
            "| Source | Via | Year | Events | Day precision | Listed only | Registry only | Index only | Partial sheets | Complete sheets | Events accepted |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for row in sorted(inventory, key=lambda row: (row["year"], row["source"], row["via"])):
            values = [
                row.get(field, 0)
                for field in (
                    "source",
                    "via",
                    "year",
                    "events",
                    "events_day_precision",
                    "events_listed_only",
                    "events_registry_only",
                    "events_index_only",
                    "events_sheets_partial",
                    "events_sheets_complete",
                )
            ]
            values.append("yes" if row.get("events_accepted") else "no")
            lines.append("| " + " | ".join(str(value) for value in values) + " |")
        return "\n".join(lines)
    counts: Counter[tuple[str, str]] = Counter()
    for event in _rows(data, "events"):
        year = str(event.get("year") or "unknown")
        sources = event.get("sources") or [event.get("source") or "unknown"]
        for source in sources:
            label = (
                f"{source} (override placeholder)"
                if event.get("snapshot_id") == "override"
                else str(source)
            )
            counts[label, year] += 1
    if not counts:
        return "No events are published yet."
    lines = ["| Source | Year | Events |", "|---|---:|---:|"]
    lines.extend(
        f"| {source} | {year} | {count} |" for (source, year), count in sorted(counts.items())
    )
    return "\n".join(lines)


def _results_coverage(data: BuildInput) -> str:
    placements = _rows(data, "placements")
    sources = sorted({str(row.get("source", "unknown")) for row in placements})
    lines = ["| Results source | Events with placements | Placements |", "|---|---:|---:|"]
    for source in sources:
        records = [row for row in placements if row.get("source", "unknown") == source]
        lines.append(
            f"| {source} | {len({row.get('event_id') for row in records if row.get('event_id')})} | {len(records)} |"
        )
    return "\n".join(lines) if sources else "No placements are published yet."


def _quality(data: BuildInput) -> str:
    entries = _rows(data, "entries")
    statuses = Counter(str(row.get("link_status")) for row in entries)
    reviews = Counter(str(row.get("kind")) for row in _rows(data, "review_queue"))
    linked = sum(row.get("wsdc_id") is not None for row in entries)
    lines = [
        f"{linked} of {len(entries)} entry records carry a WSDC ID. Entries represent contest participation, sometimes couples, and are not unique people.",
        "Default WSDC IDs contain confirmed links only. Probable matches remain candidate evidence in `identity_links` and `link_candidates`; they do not populate default entry or judge joins. Confirmed source IDs identify what the source asserted. An ID can precede the corresponding registry fetch. Registry coverage does not establish complete dancer histories.",
        "| Entry link status | Records |",
        "|---|---:|",
    ]
    lines.extend(f"| {status} | {count} |" for status, count in sorted(statuses.items()))
    lines.extend(["", "| Review kind | Findings |", "|---|---:|"])
    lines.extend(f"| {kind} | {count} |" for kind, count in sorted(reviews.items()))
    lines.extend(
        [
            "",
            "Findings are not an error-rate estimate. Unknown callback outcomes are withheld, so callback rows alone are not a promotion-rate denominator. For entrants appearing with multiple partners, outcomes summarize their strongest reported result. Conflicting entrant/judge marks and their dependent summaries are withheld with findings; partner-specific score histories are not fully represented. `alternate` preserves an unranked source alternate status. `rounds.judge_count` is the round-wide judge roster, not each entrant's voting-panel size. Registry divisions `PRO` and `TCH` retain literal source codes with unverified meanings; `unknown` levels mean unavailable interpretation, not no points. Conflicting registry claims are withheld with review findings.",
        ]
    )
    return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])


def _table_counts(data: BuildInput) -> str:
    lines = ["| Table | Rows |", "|---|---:|"]
    lines.extend(f"| `{name}` | {len(_rows(data, name))} |" for name in PUBLISHED_TABLES)
    return "\n".join(lines)


def _corrections(data: BuildInput) -> str:
    policy = data.release_policy
    if not policy:
        return ""
    lines = [
        "### Identity corrections",
        "",
        "Default entry and judge IDs have been rechecked against the accepted decision journal, source evidence and suppression policy. `identity_links.acceptance_state` describes publication acceptance; probable scores remain in `link_candidates`. Named judges without WSDC numbers retain null IDs.",
        "",
        "New default joins remain withheld pending the reviewed accuracy gate. Published baseline interpretations may retain disclosed legacy support; this does not grant admission or removal authority. The manifest pins the journal and admission policy digests. Suppressed personal records are omitted from correction history.",
    ]
    if policy.get("mode") == "correction_only":
        pending = policy.get("pending_work", {})
        lines.extend(
            [
                "",
                "This correction uses the acknowledged published baseline and includes no new source generations. Pending work remains: "
                + ", ".join(f"{stage} {count}" for stage, count in sorted(pending.items()))
                + ".",
            ]
        )
    counts = policy.get("default_join_counts", {})
    if counts:
        lines.extend(["", "| Identity policy outcome | Records |", "|---|---:|"])
        lines.extend(f"| {reason} | {count} |" for reason, count in sorted(counts.items()))
    omissions = policy.get("support_withdrawals", {}).get("removed_rows", {})
    if any(omissions.values()):
        lines.extend(
            [
                "",
                "Rows omitted after support revocation: "
                + ", ".join(
                    f"{table} {count}" for table, count in sorted(omissions.items()) if count
                )
                + ".",
            ]
        )
    return "\n".join(lines)


def _phase1_gaps(data: BuildInput) -> list[str]:
    """Describe only findings in these public tables, including frozen corrections."""
    findings = _rows(data, "review_queue")
    gated = [row for row in findings if row.get("kind") == "acquisition_gate"]
    archive_gaps = [row for row in findings if row.get("kind") == "history_archive_gap"]
    site_reviews = [row for row in findings if row.get("kind") == "history_event_site_review"]
    unmapped_sheets = [row for row in findings if row.get("kind") == "history_unmapped_sheet"]
    incomplete = [row for row in findings if row.get("kind") == "phase1_incomplete"]
    aliases = [
        row
        for row in findings
        if row.get("kind") in {"series_alias", "event_alias"}
        and str(row.get("subject_id", ""))[:4].isdigit()
    ]
    warnings = [
        row
        for row in findings
        if row.get("kind") == "parse_warning"
        and str(row.get("summary") or "").startswith(
            (
                "unknown calendar date:",
                "Listed event has no safely parsed date;",
                "Newsletter page has no safely dated event rows;",
                "Prose approval notice needs a dated event interpretation",
                "Trial or member activity colour was not recovered;",
            )
        )
    ]
    maps = [
        row
        for row in findings
        if row.get("kind") == "parse_failure"
        and str(row.get("summary") or "").startswith(
            "captured map marker lacks a name or printed date"
        )
    ]
    if not (
        incomplete
        or aliases
        or warnings
        or maps
        or gated
        or archive_gaps
        or unmapped_sheets
        or site_reviews
    ):
        return []

    def years(rows: Sequence[Mapping[str, Any]]) -> str:
        values = sorted(
            {
                str(row.get("subject_id", ""))[:4]
                for row in rows
                if str(row.get("subject_id", ""))[:4].isdigit()
            }
        )
        return ", ".join(values) or "not identified in the public finding rows"

    result = []
    if site_reviews:
        result.append(
            f"- {len(site_reviews)} event-site result findings await human override review for {years(site_reviews)}. "
            "These are retained CDX candidates or evidence gaps; they do not establish parsed results, admit a parser, or start acquisition."
        )
    if archive_gaps or unmapped_sheets:
        result.append(
            f"- Historical results have {len(archive_gaps)} archive-gap findings and {len(unmapped_sheets)} unmapped-sheet findings. "
            f"Event years identified in these public findings: {years(archive_gaps + unmapped_sheets)}. "
            "Counts describe findings, not distinct missing rounds. Missing or incomplete archive evidence does not authorize origin acquisition; year, source-kind, archive-proof and host limits still apply."
        )
    if gated:
        result.append(
            f"- {len(gated)} newly discovered sheet links await event-year acceptance and the historical acquisition gates. "
            "Their index evidence is retained; acquisition has not started for these links."
        )
    if incomplete:
        result.append(
            f"- Event-list completion has {len(incomplete)} open year-scope findings for {years(incomplete)}. "
            "These combine pending captures (including any waits for host budgets), unresolved source interpretations, and incomplete discovery. "
            "The public year findings do not provide a distinct pending-capture count."
        )
    if aliases:
        series = sum(row.get("kind") == "series_alias" for row in aliases)
        result.append(
            f"- Event-list identity review has {series} series-alias and {len(aliases) - series} event-alias findings across {years(aliases)}. "
            "Printed names and ambiguous editions remain unresolved until reviewed; these counts are findings, not distinct people or series."
        )
    if warnings or maps:
        result.append(
            f"- Event-list sources have {len(warnings)} warning findings for dates, hiatus/cancellation notices, newsletter approvals or unrecovered status colours, "
            f"and {len(maps)} archived-map parse findings for missing printed names or dates. "
            "Unsupported dates and undated listings remain findings; they are not silently assigned dates."
        )
    coverage = [row for row in _rows(data, "coverage") if row.get("scope_kind") in (None, "year")]
    unaccepted = sorted(
        {
            str(row["year"])
            for row in coverage
            if row.get("year") is not None and not row.get("events_accepted")
        }
    )
    if unaccepted:
        result.append(
            f"- Event-list acceptance remains open for {', '.join(unaccepted)}. "
            "Publishing this partial inventory does not accept a year or authorize its historical score-sheet intake."
        )
    return result


def _gaps(data: BuildInput) -> str:
    events = _rows(data, "events")
    covered_event_ids = {row.get("event_id") for row in _rows(data, "contests")}
    calendar_only = sum(1 for row in events if row.get("event_id") not in covered_event_ids)
    unsupported = sum(row.get("parse_status") == "unsupported" for row in _rows(data, "contests"))
    gaps = [
        f"- {unsupported} contests have unsupported scoring layouts. Their raw evidence is retained privately; see `review_queue` for details.",
        f"- {calendar_only} of {len(events)} events currently have event metadata but no parsed contest results.",
        f"- `heats` has {len(_rows(data, 'heats'))} rows; public heat assignments are rarely available.",
    ]
    if not _rows(data, "dancers") or not _rows(data, "registry_placements"):
        gaps.append(
            "- The registry mirror is incomplete while `dancers` or `registry_placements` is empty."
        )
    gaps.append(
        "- Unverified callback codes and numeric marks remain in private evidence; they do not become negative callbacks or zero marks. Shared finals bibs stay null where partner ownership is unknown; a unique same-contest, same-role preliminary identity can supply an independently observed bib. Couple names remain a single couple entry when the source does not establish individual roles."
    )
    gaps.append(
        "- Rows with `snapshot_id = override` are URL-override placeholders, not fetched source evidence. Their dates span the month encoded in the override ID; they are not verified event dates. Do not infer result coverage from these rows."
    )
    gaps.extend(_phase1_gaps(data))
    return "\n".join(gaps)


def render_card(data: BuildInput) -> bytes:
    default_table = "placements" if _rows(data, "placements") else "events"
    configs = []
    for table in PUBLISHED_TABLES:
        default = "\n  default: true" if table == default_table else ""
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
It traces history from {data.history_start.isoformat()} onward. Events that ended
before that date get no rows in `events` or the tables under it. The registry mirror
(`dancers`, `registry_placements`) is published whole, back to the registry's own
beginning; a registry placement from before the start date keeps a null `event_id`.
That absence is the start-date rule, not missing data.
Month-only events have null start and end dates and retain their registry month.
Editions with neither a registry occurrence nor a public listing are outside the
discoverable event inventory.

## Load it

```sql
SELECT e.name AS event, p.place, leader.name_raw AS leader,
       follower.name_raw AS follower, couple.name_raw AS couple
FROM 'hf://datasets/skeswa/swingset/data/placements/*.parquet' p
JOIN 'hf://datasets/skeswa/swingset/data/events/*.parquet' e USING (event_id)
LEFT JOIN 'hf://datasets/skeswa/swingset/data/entries/*.parquet' leader
  ON leader.entry_id = p.leader_entry_id
LEFT JOIN 'hf://datasets/skeswa/swingset/data/entries/*.parquet' follower
  ON follower.entry_id = p.follower_entry_id
LEFT JOIN 'hf://datasets/skeswa/swingset/data/entries/*.parquet' couple
  ON couple.entry_id = p.couple_entry_id;
```

DuckDB can run this query directly. Polars, pandas, and Hugging Face `datasets` can
read each table from the paths above. `{default_table}` is the default config in this
version. Hugging Face cannot stream an empty Parquet config, so calendar-only releases
default to `events`; after results arrive, the default changes to `placements`.

## Published coverage

This table is computed from the event rows in this exact dataset version. A source on
an event identifies its evidence source or an explicitly labeled URL-override placeholder; it does not establish complete contest or round coverage.
The `coverage` table separates archive and origin evidence. Counts across sources
can overlap. Event-list acceptance requires recorded owner review; an empty findings
queue alone does not approve a year. Sheet completeness remains unknown until the
source index and all expected rounds have been checked.

{_coverage(data)}

### Result coverage

{_results_coverage(data)}

Event metadata includes future schedules and index-only events. Results coverage counts only events with placements. The manifest separates `calendar_horizon` from `latest_event_covered`, the latest dated event with placements; placeholder dates remain approximate.

### Data quality

{_quality(data)}

{_corrections(data)}

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
robots, Retry-After, and a five-second request floor. An explicitly seeded registry
sweep uses its documented two-second exception. Results include public competitor
and judge names. Suppressed people keep structural rows with identity fields removed.
Raw bodies stay private. Source terms continue to apply.

## License and citation

The compilation and structure are ODC-By 1.0. This license does not replace source
terms or grant rights in source material. Attribute Swingset, cite this repository,
and pin the Hub commit used.

## Schema versions

The schema is pre-1.0. This version adds event precision, held status, history
sources, archive capture times, and the `coverage` table. Probable identity links
are excluded from default entry and judge WSDC IDs.
""".encode()
