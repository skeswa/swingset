from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from swingset.clock import FakeClock
from swingset.project.process import process_unit
from swingset.sources.base import ParseContext, ParseResult
from swingset.sources.records import SourceEventRow
from swingset.sources.steprightsolutions import EventPage, IndexPage
from swingset.sources.steprightsolutions.records import StepRightEventSheet
from swingset.state.db import Database, open_database
from swingset.state.observations import store_parse_result
from swingset.state.work import WorkUnit, next_work

SOURCE_REF = "steprightsolutions:srs-event2015"
EVENT_URL = "http://steprightsolutions.com/events/srs-event2015"
NOW = datetime(2026, 9, 18, tzinfo=UTC)


@dataclass(frozen=True)
class Bundle:
    files: Mapping[str, bytes]


def store(
    database: Database,
    *,
    page: IndexPage,
    extract: object,
    watch_id: str,
    snapshot_id: str,
    fetched_at: str,
    source_ref: str | None,
    run_id: str,
) -> ParseResult:
    database.connection.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) "
        "VALUES (?,'steprightsolutions',?,'GET',?,?,?,'live')",
        (watch_id, page.kind.rsplit(".", 1)[-1], EVENT_URL, page.kind, source_ref),
    )
    database.connection.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
        "body_bytes,content_changed,run_id,classification) "
        "VALUES (?,?,'GET',?,?,200,1,1,?,'Ok')",
        (snapshot_id, watch_id, EVENT_URL, fetched_at, run_id),
    )
    context = ParseContext(
        snapshot_id,
        watch_id,
        EVENT_URL,
        "steprightsolutions",
        page.kind,
        source_ref,
        fetched_at,
    )
    result = page.parse(extract, context)
    with database.transaction():
        store_parse_result(
            database.connection,
            context,
            result,
            extract_version=page.EXTRACT_VERSION,
            parser_version=page.PARSER_VERSION,
            parsed_at=fetched_at,
            run_id=run_id,
        )
    return result


def drain_projects(database: Database, clock: FakeClock, run_id: str) -> None:
    bundle = Bundle({})
    completed = 0
    while (unit := next_work(database.connection, "project", now=clock.now())) is not None:
        process_unit(database, unit, bundle, clock, run_id)
        completed += 1
        assert completed < 20


def test_event_detail_dispatch_refreshes_source_event_and_map(tmp_path: Path) -> None:
    clock = FakeClock(NOW)
    with open_database(tmp_path, lock=False) as database:
        run_id = database.start_run(clock.now())
        index = IndexPage()
        store(
            database,
            page=index,
            extract={
                "rows": [
                    {
                        "ref": SOURCE_REF,
                        "series": "Asia West Coast Swing Open",
                        "location": "Vancouver, BC, Canada",
                        "year": "2015",
                        "url": EVENT_URL,
                    }
                ],
                "contract_witness": {
                    "event_block_count": 1,
                    "date_anchor_count": 1,
                    "external_event_sites": [],
                },
            },
            watch_id="step-right-index",
            snapshot_id="step-right-index-snapshot",
            fetched_at="2026-09-18T00:00:00Z",
            source_ref=None,
            run_id=run_id,
        )
        drain_projects(database, clock, run_id)
        indexed = database.connection.execute(
            "SELECT name_raw,start_date,end_date,location_raw FROM source_events "
            "WHERE source='steprightsolutions' AND source_ref=?",
            (SOURCE_REF,),
        ).fetchone()
        assert tuple(indexed) == (
            "Asia West Coast Swing Open",
            None,
            None,
            "Vancouver, BC, Canada",
        )
        map_revision_before = database.connection.execute(
            "SELECT value FROM revisions WHERE name='source_event_map'"
        ).fetchone()[0]

        event = EventPage()
        detail_result = store(
            database,
            page=event,
            extract={
                "name": "Asia West Coast Swing Open 2015",
                "date": "April 23 - 26, 2015",
                "links": [],
                "round_listing_status": "no_round_links",
                "contract_witness": {
                    "main_panel_count": 0,
                    "contest_count": 0,
                    "main_round_link_count": 0,
                    "main_unique_round_link_count": 0,
                    "sidebar_duplicate_count": 0,
                    "sidebar_round_links_match_main": True,
                    "unique_round_links_outside_main": [],
                },
            },
            watch_id="step-right-event",
            snapshot_id="step-right-event-snapshot",
            fetched_at="2026-09-18T01:00:00Z",
            source_ref=SOURCE_REF,
            run_id=run_id,
        )

        detail_observations = database.connection.execute(
            "SELECT kind,scope_kind,scope_id,payload_json FROM observations "
            "WHERE snapshot_id='step-right-event-snapshot' ORDER BY seq"
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in detail_observations] == [
            ("step_right_event_sheet", "source_event", SOURCE_REF),
            ("source_event_row", "source_index", SOURCE_REF),
        ]
        assert isinstance(detail_result.observations[0].payload, StepRightEventSheet)
        assert isinstance(detail_result.observations[1].payload, SourceEventRow)
        pending = {
            WorkUnit(str(row[0]), str(row[1]), str(row[2]))
            for row in database.connection.execute(
                "SELECT stage,unit_kind,unit_id FROM pending_work WHERE stage='project'"
            )
        }
        assert {
            WorkUnit("project", "source_event", SOURCE_REF),
            WorkUnit("project", "source_index", SOURCE_REF),
            WorkUnit("project", "map", "all"),
        }.issubset(pending)

        drain_projects(database, clock, run_id)
        merged = database.connection.execute(
            "SELECT name_raw,start_date,end_date,location_raw,url,snapshot_id,parser_version "
            "FROM source_events WHERE source='steprightsolutions' AND source_ref=?",
            (SOURCE_REF,),
        ).fetchone()
        assert tuple(merged) == (
            "Asia West Coast Swing Open 2015",
            "2015-04-23",
            "2015-04-26",
            "Vancouver, BC, Canada",
            EVENT_URL,
            "step-right-event-snapshot",
            str(event.PARSER_VERSION),
        )
        mapping = database.connection.execute(
            "SELECT event_id,match_method,match_confidence FROM source_event_map "
            "WHERE source='steprightsolutions' AND source_ref=?",
            (SOURCE_REF,),
        ).fetchone()
        assert tuple(mapping)[1:] == ("name_date", 0.5)
        assert (
            database.connection.execute(
                "SELECT value FROM revisions WHERE name='source_event_map'"
            ).fetchone()[0]
            > map_revision_before
        )
