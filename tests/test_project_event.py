import sqlite3
from pathlib import Path

from swingset.model.canonical import (
    CallbackMark,
    Contest,
    Entry,
    FinalMark,
    Judge,
    Placement,
    Round,
)
from swingset.model.observations import encode_payload
from swingset.project.contests import project_event
from swingset.project.writer import replace_scope, replace_source_event_map
from swingset.sources.base import ParseContext
from swingset.sources.eepro import RoundPage as EEProRoundPage
from swingset.sources.records import Cell, ResultRow, ResultTable, RoundSheet
from swingset.sources.wdr.adapter import RoundsPage
from swingset.state.db import open_database

EVENT = "2026-08-summer-hummer"


def sheet(
    round_name: str,
    name: str = "A Person",
    mark: str = "Y",
    contest: str = "Novice Jack & Jill",
) -> RoundSheet:
    headers = (Cell("Bib"), Cell("Leader"), Cell("J1", (("title", "Jane Doe"),)))
    row = ResultRow((Cell("255"), Cell(name), Cell(mark)))
    table = ResultTable(round_name, headers, (row,))
    return RoundSheet("round_sheet", "eepro:hummer", round_name, contest, round_name, (table,))


def seed(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-08T00:00:00Z',0)"
    )
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            EVENT,
            "slug-summer-hummer",
            "Summer Hummer",
            2026,
            "2026-08-27",
            "2026-08-30",
            "registry",
            "[]",
            "wsdc_calendar",
            "calendar",
            "1",
            "2026-09-08",
            "2026-09-08",
            "run_a",
        ),
    )
    conn.execute(
        "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) VALUES ('eepro','eepro:hummer',?,'override',1)",
        (EVENT,),
    )


def add(
    conn: sqlite3.Connection,
    watch: str,
    snapshot: str,
    payload: RoundSheet,
    fetched: str,
    page_kind: str = "round",
) -> None:
    url = f"https://example/{watch}"
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) VALUES (?,'eepro',?,'GET',?,'eepro.round','eepro:hummer','live')",
        (watch, page_kind, url),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET',?,?,200,1,1,'run_a','Ok')",
        (snapshot, watch, url, fetched),
    )
    conn.execute(
        "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json) VALUES (?,?,?,'round_sheet','source_event','eepro:hummer',0,'1','1',?)",
        (f"obs-{snapshot}", watch, snapshot, encode_payload(payload)),
    )


def records(projection: object, cls: type[object]) -> list[object]:
    return [row for row in projection.rows if isinstance(row, cls)]  # type: ignore[attr-defined]


def test_full_event_projection_and_round_union(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "prelim", "snap_prelim", sheet("Prelim"), "2026-09-01T00:00:00Z")
        add(db.connection, "final", "snap_final", sheet("Final", mark="1"), "2026-09-02T00:00:00Z")
        projected = project_event(db.connection, EVENT, "2026-09-08T00:00:00Z", "run_a")
        assert len(records(projected, Contest)) == 1
        assert len(records(projected, Round)) == 2
        entry = records(projected, Entry)[0]
        assert isinstance(entry, Entry)
        assert entry.rounds_danced == ("prelim", "final")
        assert entry.best_round == "final"
        assert len(records(projected, Judge)) == 1
        mark = records(projected, CallbackMark)[0]
        assert isinstance(mark, CallbackMark) and mark.mark == "yes"
        assert len(records(projected, Placement)) == 1
        assert len(records(projected, FinalMark)) == 1


def test_real_eepro_numeric_prelim_is_retained_raw_but_not_projected(tmp_path: Path) -> None:
    page = EEProRoundPage()
    body = Path(
        "src/swingset/sources/eepro/fixtures/round-aa-summerhummer2026-2026-09-09.body"
    ).read_bytes()
    parsed = page.parse(
        page.extract(body),
        ParseContext(
            "snap_aa",
            "watch_aa",
            "https://eepro.com/results/summerhummer2026/aa.html",
            "eepro",
            page.kind,
            "eepro:hummer",
            "2026-09-09T14:19:48.391680+00:00",
        ),
    )
    raw = parsed.observations[0].payload
    assert isinstance(raw, RoundSheet)
    assert raw.tables[0].rows[0].cells[1].text == "97"

    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "aa", "snap_aa", raw, "2026-09-09T14:19:48.391680+00:00")
        projected = project_event(db.connection, EVENT, "2026-09-09T15:00:00Z", "run_a")
        contest = records(projected, Contest)[0]
        assert isinstance(contest, Contest) and contest.parse_status == "unsupported"
        assert not records(projected, Round)
        assert not records(projected, Entry)
        assert not records(projected, Judge)
        assert not records(projected, CallbackMark)
        assert not records(projected, Placement)
        assert not records(projected, FinalMark)


def test_round_page_beats_later_event_page_and_records_conflict(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(
            db.connection,
            "event-page",
            "snap_old",
            sheet("Prelim", "A Persn"),
            "2026-09-03T00:00:00Z",
            "event",
        )
        add(
            db.connection,
            "round-page",
            "snap_new",
            sheet("Prelim", "A Person"),
            "2026-09-01T00:00:00Z",
            "round",
        )
        projected = project_event(db.connection, EVENT, "2026-09-08T00:00:00Z", "run_a")
        entry = records(projected, Entry)[0]
        assert isinstance(entry, Entry) and entry.name_raw == "A Person"
        assert projected.findings
        assert projected.findings[0].evidence["snapshots"] == ["snap_old", "snap_new"]  # type: ignore[index]


def test_prelim_entry_survives_when_final_observation_is_removed(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "prelim", "snap_prelim", sheet("Prelim"), "2026-09-01T00:00:00Z")
        add(db.connection, "final", "snap_final", sheet("Final", mark="1"), "2026-09-02T00:00:00Z")
        with db.transaction():
            replace_scope(
                db.connection,
                scope_kind="event",
                scope_id=EVENT,
                projection=project_event(db.connection, EVENT, "2026-09-08T00:00:00Z", "run_a"),
                run_id="run_a",
                projected_at="2026-09-08T00:00:00Z",
            )
        db.connection.execute("DELETE FROM observations WHERE watch_id='final'")
        with db.transaction():
            replace_scope(
                db.connection,
                scope_kind="event",
                scope_id=EVENT,
                projection=project_event(db.connection, EVENT, "2026-09-09T00:00:00Z", "run_a"),
                run_id="run_a",
                projected_at="2026-09-09T00:00:00Z",
            )
        entry = db.connection.execute("SELECT rounds_danced,best_round FROM entries").fetchone()
        assert tuple(entry) == ('["prelim"]', "prelim")
        assert db.connection.execute("SELECT count(*) FROM placements").fetchone()[0] == 0
        db.connection.execute("DELETE FROM observations WHERE watch_id='prelim'")
        with db.transaction():
            replace_scope(
                db.connection,
                scope_kind="event",
                scope_id=EVENT,
                projection=project_event(db.connection, EVENT, "2026-09-10T00:00:00Z", "run_a"),
                run_id="run_a",
                projected_at="2026-09-10T00:00:00Z",
            )
        assert db.connection.execute("SELECT count(*) FROM entries").fetchone()[0] == 0


def test_later_equal_specificity_snapshot_wins_spelling(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(
            db.connection,
            "old-round",
            "snap_old",
            sheet("Prelim", "A Persn"),
            "2026-09-01T00:00:00Z",
        )
        add(
            db.connection,
            "new-round",
            "snap_new",
            sheet("Prelim", "A Person"),
            "2026-09-02T00:00:00Z",
        )
        projected = project_event(db.connection, EVENT, "2026-09-08T00:00:00Z", "run_a")
        entry = records(projected, Entry)[0]
        assert isinstance(entry, Entry) and entry.name_raw == "A Person"


def test_named_judge_is_deduplicated_across_contests(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "novice", "snap_novice", sheet("Prelim"), "2026-09-01T00:00:00Z")
        add(
            db.connection,
            "advanced",
            "snap_advanced",
            sheet("Prelim", contest="Advanced Jack & Jill"),
            "2026-09-01T00:00:01Z",
        )
        projected = project_event(db.connection, EVENT, "2026-09-08T00:00:00Z", "run_a")
        assert len(records(projected, Contest)) == 2
        assert len(records(projected, Judge)) == 1


def test_alias_move_rekeys_all_event_rows_in_one_transaction(tmp_path: Path) -> None:
    moved = "2026-09-summer-hummer"
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "prelim", "snap_prelim", sheet("Prelim"), "2026-09-01T00:00:00Z")
        db.connection.execute(
            "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) SELECT ?,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id FROM events WHERE event_id=?",
            (moved, EVENT),
        )
        with db.transaction():
            replace_scope(
                db.connection,
                scope_kind="event",
                scope_id=EVENT,
                projection=project_event(db.connection, EVENT, "2026-09-08T00:00:00Z", "run_a"),
                run_id="run_a",
                projected_at="2026-09-08T00:00:00Z",
            )
        with db.transaction():
            affected = replace_source_event_map(
                db.connection, (("eepro", "eepro:hummer", moved, "override", 1.0),)
            )
            assert affected == (EVENT, moved)
            for event_id_ in affected:
                replace_scope(
                    db.connection,
                    scope_kind="event",
                    scope_id=event_id_,
                    projection=project_event(
                        db.connection, event_id_, "2026-09-09T00:00:00Z", "run_a"
                    ),
                    run_id="run_a",
                    projected_at="2026-09-09T00:00:00Z",
                )
        assert (
            db.connection.execute(
                "SELECT count(*) FROM contests WHERE event_id=?", (EVENT,)
            ).fetchone()[0]
            == 0
        )
        assert (
            db.connection.execute("SELECT contest_id FROM contests WHERE event_id=?", (moved,))
            .fetchone()[0]
            .startswith(f"{moved}/")
        )


def test_unchanged_full_event_reprojection_does_not_advance_revision(tmp_path: Path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "prelim", "snap_prelim", sheet("Prelim"), "2026-09-01T00:00:00Z")
        with db.transaction():
            replace_scope(
                db.connection,
                scope_kind="event",
                scope_id=EVENT,
                projection=project_event(db.connection, EVENT, "2026-09-08T00:00:00Z", "run_a"),
                run_id="run_a",
                projected_at="2026-09-08T00:00:00Z",
            )
        revision = db.connection.execute(
            "SELECT value FROM revisions WHERE name='canonical'"
        ).fetchone()[0]
        with db.transaction():
            changed = replace_scope(
                db.connection,
                scope_kind="event",
                scope_id=EVENT,
                projection=project_event(db.connection, EVENT, "2026-09-09T00:00:00Z", "run_b"),
                run_id="run_b",
                projected_at="2026-09-09T00:00:00Z",
            )
        assert not changed
        assert (
            db.connection.execute("SELECT value FROM revisions WHERE name='canonical'").fetchone()[
                0
            ]
            == revision
        )


def test_real_wdr_rounds_fixture_projects_complete_event_surface(tmp_path: Path) -> None:
    fixture = Path("src/swingset/sources/wdr/fixtures/rounds-2026-09-09.body")
    page = RoundsPage()
    url = "https://scores.worlddanceregistry.com/98011277-01cd-11f1-9a29-0aa72bbce9ea/rounds/routeInfo.json"
    source_ref = "wdr:98011277-01cd-11f1-9a29-0aa72bbce9ea"
    context = ParseContext(
        "snap_wdr", "wdr", url, "wdr", page.kind, source_ref, "2026-09-09T00:00:00Z"
    )
    parsed = page.parse(page.extract(fixture.read_bytes()), context)
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        db.connection.execute("DELETE FROM source_event_map")
        db.connection.execute(
            "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) VALUES ('wdr',?,?,'override',1)",
            (source_ref, EVENT),
        )
        db.connection.execute(
            "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) VALUES ('wdr','wdr','event','GET',?,'wdr.rounds',?,'live')",
            (url, source_ref),
        )
        db.connection.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('snap_wdr','wdr','GET',?,'2026-09-09T00:00:00Z',200,1,1,'run_a','Ok')",
            (url,),
        )
        for seq, observation in enumerate(parsed.observations):
            db.connection.execute(
                "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json) VALUES (?,'wdr','snap_wdr',?,?,?,?, '1','1',?)",
                (
                    f"wdr-{seq}",
                    observation.kind,
                    observation.scope.kind,
                    observation.scope.ref,
                    seq,
                    encode_payload(observation.payload),
                ),
            )
        projection = project_event(db.connection, EVENT, "2026-09-09T00:00:00Z", "run_a")
        assert len(records(projection, Contest)) == 13
        assert len(records(projection, Round)) == 32
        assert len(records(projection, Entry)) == 873
        assert len(records(projection, Judge)) == 23
        assert len(records(projection, CallbackMark)) == 2793
        assert len(records(projection, Placement)) == 160
        assert len(records(projection, FinalMark)) == 1106


def test_real_eepro_finals_project_named_judges_and_entry_roles(tmp_path: Path) -> None:
    fixture_dir = Path("src/swingset/sources/eepro/fixtures")
    page = EEProRoundPage()

    def parsed_sheets(name: str) -> list[RoundSheet]:
        url = f"https://eepro.com/results/summerhummer2026/{name}.html"
        context = ParseContext(
            "snapshot", "watch", url, "eepro", page.kind, "eepro:hummer", "2026-09-09T00:00:00Z"
        )
        parsed = page.parse(
            page.extract(
                (fixture_dir / f"round-{name}-summerhummer2026-2026-09-09.body").read_bytes()
            ),
            context,
        )
        return [
            observation.payload
            for observation in parsed.observations
            if isinstance(observation.payload, RoundSheet)
        ]

    advanced = next(
        sheet
        for sheet in parsed_sheets("jjfinals")
        if sheet.contest_name_raw == "Jack & Jill Advanced"
    )
    strictly_advanced = next(
        sheet
        for sheet in parsed_sheets("strictly")
        if sheet.contest_name_raw == "Strictly Swing Advanced"
    )
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        add(db.connection, "jj-final", "snap-jj", advanced, "2026-09-09T00:00:00Z")
        add(
            db.connection,
            "strictly-final",
            "snap-strictly",
            strictly_advanced,
            "2026-09-09T00:00:01Z",
        )
        projection = project_event(db.connection, EVENT, "2026-09-09T00:00:00Z", "run_a")

    entries = [entry for entry in records(projection, Entry) if isinstance(entry, Entry)]
    advanced_entries = [entry for entry in entries if "advanced-jj" in entry.contest_id]
    advanced_by_name = {entry.name_raw: entry for entry in advanced_entries}
    assert (advanced_by_name["Caio Botelho"].role, advanced_by_name["Caio Botelho"].bib) == (
        "leader",
        "736",
    )
    assert (advanced_by_name["Sydney Taylor"].role, advanced_by_name["Sydney Taylor"].bib) == (
        "follower",
        "768",
    )
    strictly_entries = [entry for entry in entries if "advanced-strictly" in entry.contest_id]
    strictly_by_name = {entry.name_raw: entry for entry in strictly_entries}
    assert strictly_by_name["Gabe Ofordu and Annie Ogren"].role == "couple"
    assert strictly_by_name["Gabe Ofordu and Annie Ogren"].bib == "661"
    judges = [judge for judge in records(projection, Judge) if isinstance(judge, Judge)]
    assert any(judge.name_raw == "Arjay Centeno" and not judge.anonymous for judge in judges)
