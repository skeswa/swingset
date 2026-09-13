from pathlib import Path
from types import MappingProxyType

import httpx
import pytest

from swingset.clock import FakeClock
from swingset.config import Config, HostConfig, SourceConfig
from swingset.fetch.archive import Archive
from swingset.fetch.client import FetchClient
from swingset.model.observations import encode_payload
from swingset.project.map import project_map
from swingset.schedule.parse import parse_snapshot
from swingset.schedule.watches import upsert_watch
from swingset.sources.base import ExtractError, ParseContext, WatchSpec
from swingset.sources.eepro import RoundPage as EEProRoundPage
from swingset.sources.records import (
    Cell,
    EventSheet,
    ResultRow,
    ResultTable,
    RoundSheet,
    SourceEventRow,
)
from swingset.sources.scoringdance import EventPage as ScoringDanceEventPage
from swingset.state.db import open_database
from swingset.state.work import next_work


def context(kind: str, source_ref: str) -> ParseContext:
    return ParseContext(
        "snapshot",
        "watch",
        "https://scoring.dance/enUS/events/315/results/",
        "scoringdance",
        kind,
        source_ref,
        "2026-09-10T00:00:00Z",
    )


def test_scoringdance_exact_unpublished_page_retains_event_metadata() -> None:
    body = b"""
    <meta property="og:title" content="Future Swing 2027 results">
    <main><h1 class="h4">Future Swing 2027 results</h1>
    <p>Sorry, the results aren't published yet. Please wait until the awards are finished.</p>
    </main>
    """
    page = ScoringDanceEventPage()
    result = page.parse(page.extract(body), context(page.kind, "scoringdance:500"))
    event_sheet = result.observations[0].payload
    source_event = result.observations[1].payload
    assert isinstance(event_sheet, EventSheet) and event_sheet.round_links == ()
    assert isinstance(source_event, SourceEventRow) and source_event.name_raw == "Future Swing 2027"
    assert result.watches == ()


def test_scoringdance_unknown_linkless_layout_still_fails() -> None:
    body = b'<meta property="og:title" content="Broken Event results"><main></main>'
    with pytest.raises(ExtractError, match="expected scoring.dance links are missing"):
        ScoringDanceEventPage().extract(body)


def test_eepro_exact_coming_soon_page_is_empty_but_unknown_layout_fails() -> None:
    page = EEProRoundPage()
    assert page.extract(b"<html><h1>Coming soon</h1></html>") == []
    with pytest.raises(ExtractError, match="EEPro round has no result table"):
        page.extract(b"<html><h1>Unexpected response</h1></html>")


def test_eepro_coming_soon_snapshot_completes_as_legitimate_empty(tmp_path: Path) -> None:
    clock = FakeClock()
    config = Config(
        {"eepro.com": HostConfig()},
        {"eepro": SourceConfig(True)},
    )
    spec = WatchSpec(
        "",
        "eepro",
        "round",
        "GET",
        "https://eepro.com/results/example/routines.html",
        EEProRoundPage.kind,
        source_ref="eepro:example",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=b"<html><h1>Coming soon</h1></html>")

    with open_database(tmp_path) as database:
        run_id = database.start_run(clock.now())
        upsert_watch(database.connection, spec, clock.now())
        client = FetchClient(
            database.connection,
            config,
            clock,
            Archive(tmp_path),
            transport=httpx.MockTransport(handler),
        )
        client.fetch(spec.watch_id, EEProRoundPage(), run_id)
        unit = next_work(database.connection, "parse")
        assert unit is not None
        attempt = parse_snapshot(database, Archive(tmp_path), unit, clock, run_id)
        assert not attempt.failed
        assert database.connection.execute("SELECT count(*) FROM observations").fetchone()[0] == 0
        assert (
            database.connection.execute("SELECT parse_status FROM snapshots").fetchone()[0] == "ok"
        )
        client.close()


def test_city_of_angels_alias_uses_approximate_override_dates(tmp_path: Path) -> None:
    aliases = Path("overrides/event_aliases.csv").read_bytes()
    bundle_type = type(
        "Bundle",
        (),
        {},
    )
    common = {"overrides/source_urls.csv": b"event_id,source,kind,url,parser\n"}
    automatic = bundle_type()
    automatic.files = MappingProxyType(
        {**common, "overrides/event_aliases.csv": b"source,source_ref,event_id,note\n"}
    )
    reviewed = bundle_type()
    reviewed.files = MappingProxyType({**common, "overrides/event_aliases.csv": aliases})
    with open_database(tmp_path, lock=False) as database:
        database.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run','2026-09-10',0)"
        )
        database.connection.execute(
            "INSERT INTO source_events(source,source_ref,name_raw,start_date,end_date,url,"
            "snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES "
            "('scoringdance','scoringdance:315','City Of Angels 2026','2027-04-29',"
            "'2027-04-29','https://scoring.dance/enUS/events/315/results/','source','4',"
            "'2026-09-10','2026-09-10','run')"
        )
        result = RoundSheet(
            "round_sheet",
            "scoringdance:315",
            "final",
            "Novice Jack & Jill",
            "Final",
            (
                ResultTable(
                    "Final",
                    (Cell("Bib"), Cell("Leader"), Cell("Place")),
                    (ResultRow((Cell("1"), Cell("A Dancer"), Cell("1"))),),
                ),
            ),
        )
        database.connection.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) "
            "VALUES ('round','scoringdance','round','GET','https://example/final',"
            "'scoringdance.round','scoringdance:315','live')"
        )
        database.connection.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
            "body_bytes,content_changed,run_id,classification) VALUES "
            "('round-snapshot','round','GET','https://example/final','2026-09-10',200,1,1,"
            "'run','Ok')"
        )
        database.connection.execute(
            "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,"
            "scope_id,seq,extract_version,parser_version,payload_json) VALUES "
            "('round-observation','round','round-snapshot','round_sheet','source_event',"
            "'scoringdance:315',0,'1','1',?)",
            (encode_payload(result),),
        )
        with database.transaction():
            project_map(database.connection, automatic, "2026-09-10T00:00:00Z", "run", 11)
        old_event = database.connection.execute(
            "SELECT event_id FROM source_event_map WHERE source='scoringdance' "
            "AND source_ref='scoringdance:315'"
        ).fetchone()[0]
        old_contest = database.connection.execute(
            "SELECT contest_id FROM contests WHERE event_id=?", (old_event,)
        ).fetchone()[0]
        database.connection.execute(
            "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,"
            "leader_required_level,leader_allowed_level,follower_required_level,"
            "follower_allowed_level,leader_highest_level,leader_highest_points,"
            "follower_highest_level,follower_highest_points,recent_year,registry_internal_id,"
            "registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,"
            "run_id) VALUES (1,'A','Dancer','a dancer',0,'leader','novice','novice','novice',"
            "'novice','novice',1,'novice',1,2027,1,'2026-09-10','test','snapshot','1',"
            "'2026-09-10','2026-09-10','run')"
        )
        database.connection.execute(
            "INSERT INTO registry_placements(wsdc_id,role,dance_style,division,series_id,"
            "series_name_raw,event_month,event_id,result,points,source,snapshot_id,"
            "parser_version,first_seen_at,last_seen_at,run_id) VALUES "
            "(1,'leader','wcs','novice','slug-city-of-angels','City Of Angels 2026',"
            "'2027-04-01',?,'1',1,'test','snapshot','1','2026-09-10','2026-09-10','run')",
            (old_event,),
        )
        with database.transaction():
            project_map(database.connection, reviewed, "2026-09-10T00:00:00Z", "run", 11)
        mapping = database.connection.execute(
            "SELECT event_id,match_method FROM source_event_map WHERE source='scoringdance' "
            "AND source_ref='scoringdance:315'"
        ).fetchone()
        event = database.connection.execute(
            "SELECT start_date,end_date,wsdc_status,snapshot_id FROM events "
            "WHERE event_id='2026-04-city-of-angels-wcs'"
        ).fetchone()
        old_rows = database.connection.execute(
            "SELECT (SELECT count(*) FROM events WHERE event_id=?),"
            "(SELECT count(*) FROM contests WHERE contest_id=?)",
            (old_event, old_contest),
        ).fetchone()
        new_contests = database.connection.execute(
            "SELECT count(*) FROM contests WHERE event_id='2026-04-city-of-angels-wcs'"
        ).fetchone()[0]
        registry_event = database.connection.execute(
            "SELECT event_id FROM registry_placements WHERE wsdc_id=1"
        ).fetchone()[0]
    assert tuple(mapping) == ("2026-04-city-of-angels-wcs", "override")
    assert tuple(event) == ("2026-04-01", "2026-04-30", "unknown", "override")
    assert tuple(old_rows) == (0, 0)
    assert new_contests == 1
    assert registry_event is None
