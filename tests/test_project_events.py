from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from swingset.clock import FakeClock
from swingset.model.observations import encode_payload
from swingset.project.events import project_source_index
from swingset.project.process import process_unit
from swingset.sources.records import SourceEventRow
from swingset.state.db import Database, open_database
from swingset.state.work import accept_input, affected_work, next_work


@dataclass(frozen=True)
class Bundle:
    files: Mapping[str, bytes]


def add(
    database: Database,
    watch: str,
    page_kind: str,
    scope: str,
    snapshot: str,
    payload: SourceEventRow,
    fetched: str,
) -> None:
    conn = database.connection
    url = f"https://example/{watch}"
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,state) VALUES (?,'scoringdance',?,'GET',?,?,'live')",
        (watch, page_kind, url, f"scoringdance.{page_kind}"),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET',?,?,200,1,1,'run_a','Ok')",
        (snapshot, watch, url, fetched),
    )
    conn.execute(
        "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json) VALUES (?,?,?,'source_event_row','source_index',?,0,'1','1',?)",
        (f"obs-{snapshot}", watch, snapshot, scope, encode_payload(payload)),
    )


def test_sparse_sitemap_cannot_erase_richer_event_metadata(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        rich = SourceEventRow(
            "source_event_row",
            "scoringdance:418",
            "Bristol Swing Fiesta 2026",
            "08/28/2026",
            "https://scoring.dance/event/418",
        )
        sparse = SourceEventRow(
            "source_event_row", "scoringdance:418", None, None, "https://scoring.dance/event/418"
        )
        add(database, "event", "event", "event:418", "snap_event", rich, "2026-09-01T00:00:00Z")
        add(
            database,
            "sitemap",
            "sitemap",
            "sitemap",
            "snap_sitemap",
            sparse,
            "2026-09-02T00:00:00Z",
        )
        with database.transaction():
            project_source_index(database.connection, "event:418", "2026-09-08T00:00:00Z", "run_a")
            project_source_index(database.connection, "sitemap", "2026-09-08T00:00:00Z", "run_a")
        row = database.connection.execute(
            "SELECT name_raw,start_date FROM source_events"
        ).fetchone()
        assert tuple(row) == ("Bristol Swing Fiesta 2026", "2026-08-28")
        database.connection.execute("DELETE FROM observations WHERE watch_id='event'")
        with database.transaction():
            project_source_index(database.connection, "event:418", "2026-09-09T00:00:00Z", "run_a")
        row = database.connection.execute(
            "SELECT name_raw,start_date FROM source_events"
        ).fetchone()
        assert tuple(row) == (None, None)


def test_projector_version_bump_repairs_stale_materialized_metadata(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        rich = SourceEventRow(
            "source_event_row",
            "scoringdance:418",
            "Bristol Swing Fiesta 2026",
            "08/28/2026",
            "https://scoring.dance/event/418",
        )
        add(
            database,
            "event",
            "event",
            "scoringdance:418",
            "snap_event",
            rich,
            "2026-09-01T00:00:00Z",
        )
        database.connection.execute(
            "INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,location_raw,url,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('scoringdance','scoringdance:418',NULL,NULL,NULL,NULL,?,'snap_event','1','2026-09-01','2026-09-01','run_a')",
            (rich.url,),
        )
        units = tuple(affected_work(database.connection, "version/projector"))
        assert any(unit.unit_kind == "source_index" for unit in units)
        accept_input(
            database,
            "pipeline",
            "version/projector",
            "4",
            units,
            accepted_at="2026-09-08T00:00:00Z",
        )
        bundle = Bundle(MappingProxyType({}))
        while (unit := next_work(database.connection, "project")) is not None:
            process_unit(database, unit, bundle, FakeClock(), "run_a")
        row = database.connection.execute(
            "SELECT name_raw,start_date FROM source_events"
        ).fetchone()
        assert tuple(row) == ("Bristol Swing Fiesta 2026", "2026-08-28")
