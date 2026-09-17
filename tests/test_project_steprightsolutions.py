import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import cast

from swingset.model.canonical import (
    Callback,
    CallbackMark,
    Contest,
    Entry,
    FinalMark,
    Judge,
    Placement,
    Round,
)
from swingset.model.observations import Observation, encode_payload
from swingset.project.contests import project_event
from swingset.project.events import project_source_index
from swingset.project.process import PROJECTOR_VERSION
from swingset.project.writer import Projection
from swingset.sources.base import ParseContext
from swingset.sources.records import Cell, ResultRow, ResultTable, RoundSheet
from swingset.sources.steprightsolutions import RoundPage
from swingset.sources.steprightsolutions.records import (
    StepRightEventSheet,
    StepRightIndexRow,
    StepRightRoundSheet,
)
from swingset.state.db import open_database

FIXTURES = Path(__file__).parent / "fixtures" / "sources" / "steprightsolutions"
EVENT = "2010-12-example-open"
SOURCE_REF = "steprightsolutions:example2010"


def add_observation(
    conn: sqlite3.Connection,
    *,
    watch: str,
    snapshot: str,
    payload: Observation,
    scope_kind: str,
    scope_id: str,
    fetched_at: str,
    kind: str,
    source: str = "steprightsolutions",
    source_ref: str = SOURCE_REF,
) -> None:
    url = (
        "http://steprightsolutions.com/events/example2010"
        if kind != "steprightsolutions.round"
        else f"http://steprightsolutions.com/events/example2010/round/{watch}"
    )
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) "
        "VALUES (?,?,?,'GET',?,?,?,'live')",
        (watch, source, kind, url, kind, source_ref),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
        "body_bytes,content_changed,run_id,classification) "
        "VALUES (?,?,'GET',?,?,200,1,1,'run_a','Ok')",
        (snapshot, watch, url, fetched_at),
    )
    conn.execute(
        "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,"
        "seq,extract_version,parser_version,payload_json) VALUES (?,?,?,?,?,?,0,'1','2',?)",
        (
            f"obs-{snapshot}",
            watch,
            snapshot,
            payload.kind,
            scope_kind,
            scope_id,
            encode_payload(payload),
        ),
    )


def parse_round(file: str, round_ref: str) -> StepRightRoundSheet:
    page = RoundPage()
    context = ParseContext(
        f"snap-{round_ref}",
        round_ref,
        f"http://steprightsolutions.com/events/example2010/round/{round_ref}",
        "steprightsolutions",
        page.kind,
        SOURCE_REF,
        "2026-09-17T00:00:00Z",
    )
    return cast(
        StepRightRoundSheet,
        page.parse(page.extract((FIXTURES / file).read_bytes()), context).observations[0].payload,
    )


def records[T](projection: Projection, cls: type[T]) -> list[T]:
    return [record for record in projection.rows if isinstance(record, cls)]


def test_projector_version_covers_step_right_projection() -> None:
    assert PROJECTOR_VERSION == 20


def test_index_year_stays_date_less_and_detail_adds_exact_dates_without_losing_location(
    tmp_path: Path,
) -> None:
    index = StepRightIndexRow(
        "step_right_index_row",
        SOURCE_REF,
        "Example Open",
        "Example City, Canada",
        "2010",
        "http://steprightsolutions.com/events/example2010",
    )
    detail = StepRightEventSheet(
        "step_right_event_sheet",
        SOURCE_REF,
        "Example Open 2010",
        "December 2 - 5, 2010",
        (),
        "no_round_links",
    )
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-17',0)")
        add_observation(
            conn,
            watch="index",
            snapshot="snap-index",
            payload=index,
            scope_kind="source_index",
            scope_id="steprightsolutions",
            fetched_at="2026-09-17T00:00:00Z",
            kind="steprightsolutions.index",
        )
        project_source_index(conn, "steprightsolutions", "2026-09-17", "run_a")
        first = conn.execute(
            "SELECT name_raw,start_date,end_date,location_raw,url FROM source_events"
        ).fetchone()
        assert tuple(first) == (
            "Example Open",
            None,
            None,
            "Example City, Canada",
            "http://steprightsolutions.com/events/example2010",
        )

        add_observation(
            conn,
            watch="event",
            snapshot="snap-event",
            payload=detail,
            scope_kind="source_event",
            scope_id=SOURCE_REF,
            fetched_at="2026-09-17T01:00:00Z",
            kind="steprightsolutions.event",
        )
        project_source_index(conn, SOURCE_REF, "2026-09-17", "run_a")
        merged = conn.execute(
            "SELECT name_raw,start_date,end_date,location_raw,url FROM source_events"
        ).fetchone()
        assert tuple(merged) == (
            "Example Open 2010",
            "2010-12-02",
            "2010-12-05",
            "Example City, Canada",
            "http://steprightsolutions.com/events/example2010",
        )


def test_round_projection_withholds_prelim_semantics_and_does_not_cross_page_merge_names(
    tmp_path: Path,
) -> None:
    prelim = parse_round("synthetic-prelims.html", "507")
    final = replace(
        parse_round("synthetic-finals.html", "508"),
        contest_name_raw=prelim.contest_name_raw,
    )
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-17',0)")
        conn.execute(
            "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) "
            "VALUES ('steprightsolutions',?,?,'override',1)",
            (SOURCE_REF, EVENT),
        )
        add_observation(
            conn,
            watch="507",
            snapshot="snap-507",
            payload=prelim,
            scope_kind="source_event",
            scope_id=SOURCE_REF,
            fetched_at="2026-09-17T00:00:00Z",
            kind="steprightsolutions.round",
        )
        add_observation(
            conn,
            watch="508",
            snapshot="snap-508",
            payload=final,
            scope_kind="source_event",
            scope_id=SOURCE_REF,
            fetched_at="2026-09-17T01:00:00Z",
            kind="steprightsolutions.round",
        )
        projection = project_event(conn, EVENT, "2026-09-17T02:00:00Z", "run_a")

    rounds = records(projection, Round)
    assert [
        (
            round_.round_type,
            round_.callback_legend,
            round_.promoted_count,
            round_.score_sheet_url,
        )
        for round_ in rounds
    ] == [
        ("prelim", "legacy_3", None, None),
        ("final", "unknown", None, None),
    ]
    assert not records(projection, CallbackMark)
    assert not records(projection, Callback)

    entries = records(projection, Entry)
    preliminary_alex = next(
        entry for entry in entries if entry.name_raw == "Alex Example" and entry.bib
    )
    final_alex = next(
        entry for entry in entries if entry.name_raw == "Alex Example" and not entry.bib
    )
    assert preliminary_alex.role == "leader" and preliminary_alex.bib == "032"
    assert preliminary_alex.entry_id != final_alex.entry_id
    final_entries = [entry for entry in entries if entry.best_round == "final"]
    assert {(entry.role, entry.bib) for entry in final_entries} == {
        ("leader", None),
        ("follower", None),
    }

    judges = records(projection, Judge)
    assert all(judge.anonymous and judge.name_raw is None for judge in judges)
    assert len({judge.judge_id.rsplit("/judge/", 1)[0] for judge in judges}) == 2
    assert not any("Taylor" in (judge.name_raw or "") for judge in judges)
    placements = records(projection, Placement)
    assert len(placements) == 1 and placements[0].place == 1
    assert placements[0].leader_entry_id == final_alex.entry_id
    assert len(records(projection, FinalMark)) == 2


def test_real_round_legends_use_supported_judge_evidence_only(tmp_path: Path) -> None:
    prelim = parse_round("real-srs-round507-20260917.html", "507")
    final = parse_round("real-srs-round508-20260917.html", "508")
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-17',0)")
        conn.execute(
            "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) "
            "VALUES ('steprightsolutions',?,?,'override',1)",
            (SOURCE_REF, EVENT),
        )
        add_observation(
            conn,
            watch="real-507",
            snapshot="snap-real-507",
            payload=prelim,
            scope_kind="source_event",
            scope_id=SOURCE_REF,
            fetched_at="2026-09-17T00:00:00Z",
            kind="steprightsolutions.round",
        )
        add_observation(
            conn,
            watch="real-508",
            snapshot="snap-real-508",
            payload=final,
            scope_kind="source_event",
            scope_id=SOURCE_REF,
            fetched_at="2026-09-17T01:00:00Z",
            kind="steprightsolutions.round",
        )
        projection = project_event(conn, EVENT, "2026-09-17T02:00:00Z", "run_a")

    assert {round_.round_type: round_.callback_legend for round_ in records(projection, Round)} == {
        "prelim": "legacy_3",
        "final": "unknown",
    }
    assert not records(projection, CallbackMark)
    assert not records(projection, Callback)


def test_mixed_sources_share_common_contest_and_round_reconciliation(tmp_path: Path) -> None:
    step_right = parse_round("synthetic-prelims.html", "507")
    eepro_ref = "eepro:example2010"
    eepro = RoundSheet(
        "round_sheet",
        eepro_ref,
        "eepro-prelim",
        step_right.contest_name_raw,
        "Prelims",
        (
            ResultTable(
                "Prelims",
                (Cell("Bib"), Cell("Leader"), Cell("J1", (("title", "Jane Judge"),))),
                (ResultRow((Cell("999"), Cell("Other Example"), Cell("Y"))),),
            ),
        ),
    )
    with open_database(tmp_path, lock=False) as database:
        conn = database.connection
        conn.execute("INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run_a','2026-09-17',0)")
        conn.executemany(
            "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) "
            "VALUES (?,?,?,'override',1)",
            (
                ("steprightsolutions", SOURCE_REF, EVENT),
                ("eepro", eepro_ref, EVENT),
            ),
        )
        add_observation(
            conn,
            watch="mixed-step-right",
            snapshot="snap-mixed-step-right",
            payload=step_right,
            scope_kind="source_event",
            scope_id=SOURCE_REF,
            fetched_at="2026-09-17T00:00:00Z",
            kind="steprightsolutions.round",
        )
        add_observation(
            conn,
            watch="mixed-eepro",
            snapshot="snap-mixed-eepro",
            payload=eepro,
            scope_kind="source_event",
            scope_id=eepro_ref,
            fetched_at="2026-09-17T01:00:00Z",
            kind="eepro.round",
            source="eepro",
            source_ref=eepro_ref,
        )
        projection = project_event(conn, EVENT, "2026-09-17T02:00:00Z", "run_a")

    assert len(records(projection, Contest)) == 1
    assert len(records(projection, Round)) == 1
    keys = [(type(record), record.key()) for record in projection.rows]
    assert len(keys) == len(set(keys))
    assert {entry.name_raw for entry in records(projection, Entry)} >= {
        "Alex Example",
        "Other Example",
    }
