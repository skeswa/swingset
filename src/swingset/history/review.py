"""A reproducible year-by-year owner review pack; never automatic sign-off."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from swingset.fetch.archive import canonical, durable_write
from swingset.history.catalog import load_catalog
from swingset.history.closure import synchronize_year_findings, year_gaps
from swingset.model.history import HISTORY_START
from swingset.project.history import finalize_history, prepare_inventory, reconcile_history
from swingset.project.materialization import helper_recipe, materializing
from swingset.state.db import Database
from swingset.state.derivations import available, pending_units
from swingset.state.work import WorkUnit


def review_pack(
    database: Database,
    output: Path,
    *,
    now: str,
    aliases: bytes = b"",
    history_start: date = HISTORY_START,
    reconcile: bool = True,
) -> dict[str, Any]:
    run_id = "read-only-review"
    if reconcile:
        run_id = database.start_run(datetime.fromisoformat(now), dry_run=True)
        with database.transaction() as conn:
            if available(conn):
                recipe = helper_recipe(conn, {"overrides/series_aliases.csv": aliases}, "project")
                with materializing(
                    conn,
                    WorkUnit("project", "inventory", "all"),
                    now=now,
                    run_id=run_id,
                    recipe=recipe,
                ):
                    prepare_inventory(
                        conn, now=now, run_id=run_id, aliases=aliases, history_start=history_start
                    )
                with materializing(
                    conn,
                    WorkUnit("project", "history", "all"),
                    now=now,
                    run_id=run_id,
                    recipe=recipe,
                ):
                    finalize_history(conn, now=now, history_start=history_start)
            else:
                reconcile_history(
                    conn, now=now, run_id=run_id, aliases=aliases, history_start=history_start
                )
    ledger_path = database.state_dir / "phase1-ledger.json"
    ledger = json.loads(ledger_path.read_bytes()) if ledger_path.exists() else {"targets": {}}
    targets = ledger["targets"]
    catalog_path = database.state_dir / "phase1-catalog.json"
    if catalog_path.exists():
        for target in load_catalog(catalog_path):
            targets.setdefault(
                target.target_id,
                {
                    "target_id": target.target_id,
                    "source": target.source,
                    "url": target.url,
                    "timestamp": target.timestamp,
                    "status": "pending",
                },
            )
    gaps_by_year = year_gaps(
        database.connection,
        load_catalog(catalog_path) if catalog_path.exists() else (),
        targets,
        final_year=int(now[:4]),
        history_start=history_start,
    )
    if reconcile:
        with database.transaction() as conn:
            synchronize_year_findings(conn, gaps_by_year, now=now, run_id=run_id)
    years = []
    conn = database.connection
    projection_pending = (
        next(pending_units(conn, "project"), None) is not None
        if available(conn)
        else bool(
            conn.execute("SELECT 1 FROM pending_work WHERE stage='project' LIMIT 1").fetchone()
        )
    )
    for year in range(history_start.year, int(now[:4]) + 1):
        counts = conn.execute(
            "SELECT COUNT(*),SUM(date_precision='day'),SUM(date_precision='month'),SUM(held='listed'),SUM(held='cancelled') FROM events WHERE year=?",
            (year,),
        ).fetchone()
        occurrences = conn.execute(
            "SELECT COUNT(*) FROM (SELECT series_id,event_month FROM registry_placements WHERE substr(event_month,1,4)=? GROUP BY series_id,event_month)",
            (str(year),),
        ).fetchone()[0]
        unmatched = conn.execute(
            "SELECT COUNT(*) FROM (SELECT series_id,event_month FROM registry_placements WHERE substr(event_month,1,4)=? GROUP BY series_id,event_month HAVING COUNT(DISTINCT event_id)!=1 OR SUM(event_id IS NULL)>0)",
            (str(year),),
        ).fetchone()[0]
        open_findings = conn.execute(
            "SELECT COUNT(*) FROM findings WHERE owner_kind IN ('history_year','phase1_year') AND owner_id=? AND closed_at IS NULL",
            (str(year),),
        ).fetchone()[0]
        capture_gaps = [gap.get("target_id", f"cdx:{year}") for gap in gaps_by_year[year]]
        years.append(
            {
                "year": year,
                "events": counts[0],
                "registry_occurrences": occurrences,
                "unassociated_occurrences": unmatched,
                "events_day_precision": counts[1] or 0,
                "events_month_precision": counts[2] or 0,
                "events_listed": counts[3] or 0,
                "events_cancelled": counts[4] or 0,
                "event_findings": open_findings,
                "capture_gaps": capture_gaps,
                "ready_for_owner_review": not unmatched
                and not open_findings
                and not capture_gaps
                and not projection_pending,
                "events_accepted": bool(
                    conn.execute(
                        "SELECT 1 FROM coverage WHERE year=? AND events_accepted=1", (year,)
                    ).fetchone()
                ),
            }
        )
    findings = [
        dict(row)
        for row in conn.execute(
            "SELECT kind,subject_kind,subject_id,summary,evidence_json,suggested_override,owner_kind,owner_id,snapshot_id FROM findings WHERE closed_at IS NULL AND (owner_kind IN ('history_year','phase1_capture','phase1_year') OR (owner_kind IN ('parse','parse_failure') AND snapshot_id IN (SELECT snapshot_id FROM snapshots WHERE via='wayback' OR watch_id IN (SELECT watch_id FROM watches WHERE source='wsdc_newsletter')))) ORDER BY owner_kind,owner_id,kind,subject_id"
        )
    ]
    alias_groups: dict[str, dict[str, Any]] = {}
    for finding in findings:
        if finding["kind"] != "series_alias":
            continue
        evidence = json.loads(finding["evidence_json"])
        name = evidence["printed_name"]
        group = alias_groups.setdefault(
            name,
            {
                "printed_name": name,
                "years": [],
                "candidate_series": [],
                "suggested_override": finding["suggested_override"],
                "snapshot_ids": [],
            },
        )
        group["years"] = sorted(set(group["years"]) | {evidence["year"]})
        group["candidate_series"] = sorted(
            set(group["candidate_series"]) | set(evidence.get("candidates", []))
        )
        group["snapshot_ids"] = sorted(set(group["snapshot_ids"]) | {finding["snapshot_id"]})
    event_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT event_id,series_id,name,year,event_month,start_date,end_date,date_precision,held,wsdc_status,history_source,source,snapshot_id FROM events WHERE year>=? ORDER BY year,event_month,event_id",
            (history_start.year,),
        )
    ]
    occurrence_rows = [
        dict(row)
        for row in conn.execute(
            "SELECT series_id,event_month,MIN(series_name_raw) AS printed_name,COUNT(DISTINCT event_id) AS event_count,MIN(event_id) AS event_id,COUNT(*) AS placements FROM registry_placements WHERE event_month>=? GROUP BY series_id,event_month ORDER BY event_month,series_id",
            (history_start.isoformat(),),
        )
    ]
    report = {
        "generated_at": now,
        "run_id": run_id,
        "catalog_targets": len(targets),
        "target_status_counts": {
            status: sum(row["status"] == status for row in targets.values())
            for status in ("pending", "parsed", "empty", "duplicate", "finding")
        },
        "years": years,
        "request_budgets": [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM host_budget WHERE host IN ('web.archive.org','worldsdc.com','www.worldsdc.com') ORDER BY day,host"
            )
        ],
        "cdx_queries": [
            dict(row)
            for row in conn.execute(
                "SELECT source,prefix,year,next_page,total_pages,completed_at FROM archive_queries ORDER BY year,prefix"
            )
        ],
        "findings": findings,
        "acceptance": "Owner review is required. This pack does not set events_accepted or authorize phase2.",
    }
    output.mkdir(parents=True, exist_ok=True)
    durable_write(output / "review.json", canonical(report))
    for filename, rows in [
        ("years.csv", years),
        ("events.csv", event_rows),
        ("series-review.csv", [alias_groups[name] for name in sorted(alias_groups)]),
        ("occurrences.csv", occurrence_rows),
        ("findings.csv", findings),
        ("targets.csv", list(targets.values())),
    ]:
        if not rows:
            continue
        fields = sorted({field for row in rows for field in row})
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(
            {
                key: json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            }
            for row in rows
        )
        durable_write(output / filename, stream.getvalue().encode())
    return report
