"""Offline V4 parsing while new sheet acquisition remains gated.

Construct one BootstrapParser per exclusively locked replay database, then call
parse(unit) for each retained snapshot. The caller owns admission activation and
later projection. This helper never fetches and never accepts a historical year.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from swingset.clock import Clock
from swingset.fetch.archive import Archive
from swingset.schedule.parse import ParseAttempt, parse_snapshot
from swingset.state.db import Database
from swingset.state.findings import Finding, replace_findings
from swingset.state.work import WorkUnit


@dataclass(frozen=True)
class BootstrapAttempt:
    attempt: ParseAttempt
    gated_watch_ids: tuple[str, ...]


class BootstrapParser:
    def __init__(self, database: Database, archive: Archive, clock: Clock, run_id: str) -> None:
        self.database = database
        self.archive = archive
        self.clock = clock
        self.run_id = run_id
        self.initial_ids = {
            row[0] for row in database.connection.execute("SELECT watch_id FROM watches")
        }

    def parse(self, unit: WorkUnit) -> BootstrapAttempt:
        if unit.stage != "parse" or unit.unit_kind != "snapshot":
            raise ValueError("bootstrap expects retained snapshot parse work")
        with self.database.transaction() as conn:
            parent = conn.execute(
                "SELECT watch_id FROM snapshots WHERE snapshot_id=?", (unit.unit_id,)
            ).fetchone()
            if parent is None:
                raise ValueError("bootstrap snapshot is missing")
            parent_id = str(parent[0])
            with _preserve_controls(conn, parent_id):
                highwater = conn.execute("SELECT coalesce(max(rowid),0) FROM watches").fetchone()[0]
                attempt = parse_snapshot(self.database, self.archive, unit, self.clock, self.run_id)
                created = list(conn.execute("SELECT * FROM watches WHERE rowid>?", (highwater,)))
                gated = []
                for child in created:
                    child_id = str(child["watch_id"])
                    if child_id in self.initial_ids:
                        raise ValueError("bootstrap unexpectedly recreated an existing watch")
                    if child["kind"] != "round" or child["parent_watch_id"] != parent_id:
                        raise ValueError(
                            "bootstrap unexpectedly created a non-round or foreign control"
                        )
                    if (
                        child["ever_ok"]
                        or child["body_sha256"]
                        or conn.execute(
                            "SELECT 1 FROM snapshots WHERE watch_id=? LIMIT 1", (child_id,)
                        ).fetchone()
                    ):
                        raise ValueError("bootstrap refuses to remove a fetched control")
                    replace_findings(
                        conn,
                        owner_kind="acquisition_gate",
                        owner_id=child_id,
                        findings=(
                            Finding(
                                "acquisition_gate",
                                "source_event",
                                str(child["source_ref"] or parent_id),
                                "warning",
                                "New sheet link awaits year acceptance and the historical acquisition gates; index evidence is retained and acquisition has not started.",
                                {
                                    "intended_watch_id": child_id,
                                    "url": child["url"],
                                    "source": child["source"],
                                    "source_event_ref": child["source_ref"],
                                    "parser": child["parser"],
                                    "method": child["method"],
                                    "form": child["form"],
                                    "archive_url": child["archive_url"],
                                    "parent_watch_id": parent_id,
                                    "parent_snapshot_id": unit.unit_id,
                                    "required_gates": ["G2", "G3"],
                                },
                                watch_id=parent_id,
                                snapshot_id=unit.unit_id,
                            ),
                        ),
                        opened_at=self.clock.now().isoformat(),
                        run_id=self.run_id,
                    )
                    # H14 records scheduling metadata when a child is declared.
                    # This child did not exist before the outer transaction; remove
                    # only its new metadata before deleting the gated control.
                    for table, column in (
                        ("scheduler_parent_links", "child_watch_id"),
                        ("scheduler_watch_state", "watch_id"),
                    ):
                        if conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                        ).fetchone():
                            conn.execute(f"DELETE FROM {table} WHERE {column}=?", (child_id,))
                    conn.execute("DELETE FROM watches WHERE watch_id=?", (child_id,))
                    gated.append(child_id)
                return BootstrapAttempt(attempt, tuple(gated))


@contextmanager
def _preserve_controls(conn: sqlite3.Connection, parent_id: str) -> Iterator[None]:
    """Journal actual writes to preexisting controls, within the parse transaction.

    TEMP triggers observe this connection only. No full-watch scan per unit,
    persistent schema changes, or interception of the normal parser is needed.
    """
    highwater = conn.execute("SELECT coalesce(max(rowid),0) FROM watches").fetchone()[0]
    columns = [row[1] for row in conn.execute("PRAGMA table_info(watches)")]
    tables = ["bootstrap_watch_before"]
    triggers = ["bootstrap_watch_update", "bootstrap_watch_delete"]
    conn.execute("CREATE TEMP TABLE bootstrap_watch_before AS SELECT * FROM watches WHERE 0")
    conn.execute("CREATE UNIQUE INDEX bootstrap_watch_key ON bootstrap_watch_before(watch_id)")
    conn.execute(
        "CREATE TEMP TRIGGER bootstrap_watch_update BEFORE UPDATE ON main.watches "
        f"WHEN OLD.rowid<={int(highwater)} BEGIN INSERT OR IGNORE INTO bootstrap_watch_before VALUES ("
        + ",".join('OLD."' + name + '"' for name in columns)
        + "); END"
    )
    conn.execute(
        "CREATE TEMP TRIGGER bootstrap_watch_delete BEFORE DELETE ON main.watches "
        f"WHEN OLD.rowid<={int(highwater)} BEGIN SELECT RAISE(ABORT,'bootstrap cannot delete a preexisting watch'); END"
    )
    scheduler = (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='scheduler_parent_links'").fetchone()
        is not None
    )
    if scheduler:
        tables.extend(("bootstrap_parent_insert", "bootstrap_metadata_before"))
        triggers.extend(
            (
                "bootstrap_parent_inserted",
                "bootstrap_metadata_insert",
                "bootstrap_metadata_update",
                "bootstrap_metadata_delete",
            )
        )
        conn.execute(
            "CREATE TEMP TABLE bootstrap_parent_insert(parent_watch_id TEXT,child_watch_id TEXT,PRIMARY KEY(parent_watch_id,child_watch_id))"
        )
        conn.execute(
            "CREATE TEMP TABLE bootstrap_metadata_before(watch_id TEXT PRIMARY KEY,existed INTEGER,metadata_attempts INTEGER,last_metadata_attempt_at TEXT)"
        )
        existing = f"EXISTS(SELECT 1 FROM watches WHERE watch_id=NEW.child_watch_id AND rowid<={int(highwater)})"
        conn.execute(
            "CREATE TEMP TRIGGER bootstrap_parent_inserted AFTER INSERT ON main.scheduler_parent_links "
            f"WHEN {existing} BEGIN INSERT OR IGNORE INTO bootstrap_parent_insert VALUES (NEW.parent_watch_id,NEW.child_watch_id); END"
        )
        existing = (
            f"EXISTS(SELECT 1 FROM watches WHERE watch_id=NEW.watch_id AND rowid<={int(highwater)})"
        )
        conn.execute(
            "CREATE TEMP TRIGGER bootstrap_metadata_insert BEFORE INSERT ON main.scheduler_watch_state "
            f"WHEN {existing} BEGIN INSERT OR IGNORE INTO bootstrap_metadata_before SELECT NEW.watch_id,"
            "EXISTS(SELECT 1 FROM scheduler_watch_state WHERE watch_id=NEW.watch_id),"
            "(SELECT metadata_attempts FROM scheduler_watch_state WHERE watch_id=NEW.watch_id),"
            "(SELECT last_metadata_attempt_at FROM scheduler_watch_state WHERE watch_id=NEW.watch_id); END"
        )
        for action in ("UPDATE", "DELETE"):
            conn.execute(
                f"CREATE TEMP TRIGGER bootstrap_metadata_{action.lower()} BEFORE {action} ON main.scheduler_watch_state "
                f"WHEN EXISTS(SELECT 1 FROM watches WHERE watch_id=OLD.watch_id AND rowid<={int(highwater)}) "
                "BEGIN INSERT OR IGNORE INTO bootstrap_metadata_before VALUES (OLD.watch_id,1,OLD.metadata_attempts,OLD.last_metadata_attempt_at); END"
            )
    try:
        yield
        # Stop journaling before restoration; restore precisely the first values
        # seen before this parse, including controls owned by another parent.
        for trigger in triggers:
            conn.execute("DROP TRIGGER " + trigger)
        triggers.clear()
        for row in conn.execute("SELECT * FROM bootstrap_watch_before").fetchall():
            ignored = {"watch_id"}
            if row["watch_id"] == parent_id:
                ignored.update(
                    {"current_observation_snapshot_id", "fingerprint", "extract_version"}
                )
            fields = [name for name in columns if name not in ignored]
            conn.execute(
                "UPDATE watches SET "
                + ",".join(name + "=?" for name in fields)
                + " WHERE watch_id=?",
                (*[row[name] for name in fields], row["watch_id"]),
            )
        if scheduler:
            conn.execute(
                "DELETE FROM scheduler_parent_links WHERE (parent_watch_id,child_watch_id) IN (SELECT parent_watch_id,child_watch_id FROM bootstrap_parent_insert)"
            )
            conn.execute(
                "DELETE FROM scheduler_watch_state WHERE watch_id IN (SELECT watch_id FROM bootstrap_metadata_before WHERE existed=0)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_watch_state SELECT watch_id,metadata_attempts,last_metadata_attempt_at FROM bootstrap_metadata_before WHERE existed=1"
            )
    finally:
        for trigger in triggers:
            conn.execute("DROP TRIGGER IF EXISTS " + trigger)
        for table in reversed(tables):
            conn.execute("DROP TABLE IF EXISTS " + table)
