from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from swingset.clock import FakeClock
from swingset.model.observations import encode_payload
from swingset.project.events import nullable_date_range, project_source_index
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


def test_empty_event_detail_preserves_durable_index_date_on_rebuild(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        indexed = SourceEventRow(
            "source_event_row",
            "scoringdance:2",
            "Carnival Swing 2018",
            "02/03/2018",
            "https://scoring.dance/enUS/events/2/results/",
        )
        empty_detail = SourceEventRow(
            "source_event_row",
            "scoringdance:2",
            "Carnival Swing 2018",
            None,
            "https://scoring.dance/enUS/events/2/results/",
        )
        add(
            database,
            "recent",
            "recent",
            "scoringdance",
            "snap_index",
            indexed,
            "2026-09-01T00:00:00Z",
        )
        add(
            database,
            "event",
            "event",
            "scoringdance:2",
            "snap_event",
            empty_detail,
            "2026-09-02T00:00:00Z",
        )

        with database.transaction():
            project_source_index(database.connection, "scoringdance", "2026-09-08", "run_a")
            project_source_index(database.connection, "scoringdance:2", "2026-09-08", "run_a")
        assert tuple(
            database.connection.execute(
                "SELECT name_raw,start_date,end_date,snapshot_id FROM source_events"
            ).fetchone()
        ) == ("Carnival Swing 2018", "2018-02-03", "2018-02-03", "snap_index")

        database.connection.execute("DELETE FROM source_events")
        with database.transaction():
            project_source_index(database.connection, "scoringdance", "2026-09-09", "run_a")
        assert tuple(
            database.connection.execute(
                "SELECT name_raw,start_date,end_date FROM source_events"
            ).fetchone()
        ) == ("Carnival Swing 2018", "2018-02-03", "2018-02-03")


def test_newer_dated_event_detail_wins_over_index_date(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
        )
        indexed = SourceEventRow(
            "source_event_row",
            "scoringdance:2",
            "Carnival Swing 2018",
            "02/03/2018",
            "https://example/2",
        )
        detail = replace(indexed, date_raw="02/04/2018")
        add(
            database,
            "recent",
            "recent",
            "scoringdance",
            "snap_index",
            indexed,
            "2026-09-01T00:00:00Z",
        )
        add(
            database,
            "event",
            "event",
            "scoringdance:2",
            "snap_event",
            detail,
            "2026-09-02T00:00:00Z",
        )

        with database.transaction():
            project_source_index(database.connection, "scoringdance", "2026-09-08", "run_a")
        assert (
            database.connection.execute("SELECT start_date FROM source_events").fetchone()[0]
            == "2018-02-04"
        )


def test_eepro_printed_date_ranges_preserve_both_event_boundaries() -> None:
    assert nullable_date_range("August 20-23, 2026") == ("2026-08-20", "2026-08-23")
    assert nullable_date_range("July 30-Aug 2, 2026") == ("2026-07-30", "2026-08-02")
    assert nullable_date_range("Dec 31-Jan 4, 2026") == ("2025-12-31", "2026-01-04")
    assert nullable_date_range("Dec 28-Jan 4, 2026") == ("2025-12-28", "2026-01-04")


def test_source_title_year_date_contradiction_creates_finding(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-10',0)"
        )
        payload = SourceEventRow(
            "source_event_row",
            "scoringdance:315",
            "City Of Angels 2026",
            "04/29/2027",
            "https://scoring.dance/enUS/events/315/results/",
        )
        add(
            database,
            "city",
            "event",
            "scoringdance:315",
            "snap_city",
            payload,
            "2026-09-10T00:00:00Z",
        )
        with database.transaction():
            project_source_index(database.connection, "scoringdance:315", "2026-09-10", "run_a")
        finding = database.connection.execute(
            "SELECT summary,evidence_json FROM findings WHERE closed_at IS NULL"
        ).fetchone()
    assert finding[0] == "Source event edition year contradicts its date"
    assert '"date_raw":"04/29/2027"' in finding[1]


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
