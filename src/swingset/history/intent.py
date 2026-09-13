"""Retain historical child acquisition intent without creating origin controls."""

import sqlite3

from swingset.sources.base import ParseContext, WatchSpec
from swingset.state.findings import Finding, replace_findings


def retain_child_intent(
    conn: sqlite3.Connection, context: ParseContext, spec: WatchSpec, *, now: str, run_id: str
) -> None:
    if conn.execute("SELECT 1 FROM watches WHERE watch_id=?", (spec.watch_id,)).fetchone():
        return  # Existing live/operator controls are independent of historical interpretation.
    replace_findings(
        conn,
        owner_kind="acquisition_gate",
        owner_id=spec.watch_id,
        findings=(
            Finding(
                "acquisition_gate",
                "source_event",
                spec.source_ref or context.source_ref or context.watch_id,
                "warning",
                "Retained historical page advertises a child awaiting archive-first acquisition and exact year/source-kind gates.",
                {
                    "intended_watch_id": spec.watch_id,
                    "url": spec.url,
                    "source": spec.source,
                    "source_event_ref": spec.source_ref,
                    "parser": spec.parser,
                    "method": spec.method,
                    "form": spec.form,
                    "parent_watch_id": context.watch_id,
                    "parent_snapshot_id": context.snapshot_id,
                    "required_gates": ["G2", "G3"],
                },
                watch_id=context.watch_id,
                snapshot_id=context.snapshot_id,
            ),
        ),
        opened_at=now,
        run_id=run_id,
    )
