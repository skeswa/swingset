import sqlite3
from pathlib import Path

from swingset.model.canonical import Callback, CallbackMark, Entry, Round
from swingset.model.observations import encode_payload
from swingset.project.contests import project_event
from swingset.sources.base import ParseContext
from swingset.sources.records import Cell, ResultRow, ResultTable, RoundSheet
from swingset.sources.scoringdance.adapter import RoundPage
from swingset.state.db import open_database

EVENT = "2026-08-callback-quality"


def _seed(conn: sqlite3.Connection, source: str, source_ref: str) -> None:
    conn.execute(
        "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run','2026-09-10T00:00:00Z',0)"
    )
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            EVENT,
            "slug-callback-quality",
            "Callback Quality",
            2026,
            "2026-08-01",
            "2026-08-02",
            "registry",
            "[]",
            source,
            "event-snapshot",
            "1",
            "2026-09-10",
            "2026-09-10",
            "run",
        ),
    )
    conn.execute(
        "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) VALUES (?,?,?,'override',1)",
        (source, source_ref, EVENT),
    )


def _add(
    conn: sqlite3.Connection,
    source: str,
    source_ref: str,
    sheet: RoundSheet | tuple[RoundSheet, ...],
) -> None:
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) VALUES ('watch',?,'round','GET','https://example/round','round',?,'live')",
        (source, source_ref),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('snapshot','watch','GET','https://example/round','2026-09-10T00:00:00Z',200,1,1,'run','Ok')"
    )
    sheets = sheet if isinstance(sheet, tuple) else (sheet,)
    for sequence, item in enumerate(sheets):
        conn.execute(
            "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,scope_id,seq,extract_version,parser_version,payload_json) VALUES (?, 'watch','snapshot','round_sheet','source_event',?,?,'1','1',?)",
            (f"observation-{sequence}", source_ref, sequence, encode_payload(item)),
        )


def _records(projection: object, cls: type[object]) -> list[object]:
    return [row for row in projection.rows if isinstance(row, cls)]  # type: ignore[attr-defined]


def test_archived_scoringdance_states_drive_outcomes_and_promoted_count(tmp_path: Path) -> None:
    fixture = Path("src/swingset/sources/scoringdance/fixtures/round-6023-2026-09-09.body")
    page = RoundPage()
    context = ParseContext(
        "snapshot",
        "watch",
        "https://scoring.dance/enUS/events/418/results/6023.html",
        "scoringdance",
        page.kind,
        "scoringdance:418",
        "2026-09-09T14:15:25Z",
    )
    parsed = page.parse(page.extract(fixture.read_bytes()), context)
    sheets = [item.payload for item in parsed.observations if isinstance(item.payload, RoundSheet)]

    with open_database(tmp_path, lock=False) as db:
        _seed(db.connection, "scoringdance", "scoringdance:418")
        _add(db.connection, "scoringdance", "scoringdance:418", tuple(sheets))
        projection = project_event(db.connection, EVENT, "2026-09-10T00:00:00Z", "run")

    callbacks = _records(projection, Callback)
    outcomes = {callback.outcome for callback in callbacks}  # type: ignore[attr-defined]
    assert {"promoted", "alternate_1", "alternate_2", "eliminated"} <= outcomes
    round_ = _records(projection, Round)[0]
    assert round_.promoted_count == sum(  # type: ignore[attr-defined]
        callback.outcome == "promoted"
        for callback in callbacks  # type: ignore[attr-defined]
    )
    assert round_.promoted_count > 0  # type: ignore[attr-defined]
    prelim_entries = [
        entry
        for entry in _records(projection, Entry)
        if "prelim" in entry.rounds_danced  # type: ignore[attr-defined]
    ]
    assert round_.entry_count == len(prelim_entries)  # type: ignore[attr-defined]


def test_repeated_partner_rows_keep_promoted_evidence_and_withhold_conflicting_marks(
    tmp_path: Path,
) -> None:
    state = (("row-data-state", "CB"),)
    table = ResultTable(
        "Prelim",
        (Cell("Bib"), Cell("Leader"), Cell("J1")),
        (
            ResultRow((Cell("42", state), Cell("A Dancer", state), Cell("Y", state))),
            ResultRow((Cell("42"), Cell("A Dancer"), Cell("N"))),
        ),
    )
    sheet = RoundSheet(
        "round_sheet",
        "scoringdance:quality",
        "prelim",
        "Open Jack & Jill Leader",
        "prelim",
        (table,),
    )
    with open_database(tmp_path, lock=False) as db:
        _seed(db.connection, "scoringdance", "scoringdance:quality")
        _add(db.connection, "scoringdance", "scoringdance:quality", sheet)
        projection = project_event(db.connection, EVENT, "2026-09-10T00:00:00Z", "run")

    round_ = _records(projection, Round)[0]
    assert round_.entry_count == 1  # type: ignore[attr-defined]
    assert round_.promoted_count == 1  # type: ignore[attr-defined]
    assert _records(projection, CallbackMark) == []
    assert _records(projection, Callback) == []
    assert any(
        finding.summary == "Conflicting callback marks for one entry and judge"
        for finding in projection.findings
    )  # type: ignore[attr-defined]


def test_scoringdance_unknown_layout_withholds_outcomes(tmp_path: Path) -> None:
    table = ResultTable(
        "Prelim",
        (Cell("Bib"), Cell("Leader"), Cell("J1")),
        (ResultRow((Cell("42"), Cell("A Dancer"), Cell("Y"))),),
    )
    sheet = RoundSheet(
        "round_sheet",
        "scoringdance:quality",
        "prelim",
        "Novice Jack & Jill Leader",
        "prelim",
        (table,),
    )
    with open_database(tmp_path, lock=False) as db:
        _seed(db.connection, "scoringdance", "scoringdance:quality")
        _add(db.connection, "scoringdance", "scoringdance:quality", sheet)
        projection = project_event(db.connection, EVENT, "2026-09-10T00:00:00Z", "run")

    assert _records(projection, Callback) == []
    assert _records(projection, Round)[0].promoted_count is None  # type: ignore[attr-defined]


def test_scoringdance_preserves_unranked_and_third_alternates(tmp_path: Path) -> None:
    def row(bib: str, name: str, state: str) -> ResultRow:
        attributes = (("row-data-state", state),)
        return ResultRow((Cell(bib, attributes), Cell(name, attributes), Cell("Alt1", attributes)))

    table = ResultTable(
        "Prelim",
        (Cell("Bib"), Cell("Leader"), Cell("J1")),
        (row("42", "A Dancer", "Alt"), row("43", "B Dancer", "Alt3")),
    )
    sheet = RoundSheet(
        "round_sheet",
        "scoringdance:quality",
        "prelim",
        "Novice Jack & Jill Leader",
        "prelim",
        (table,),
    )
    with open_database(tmp_path, lock=False) as db:
        _seed(db.connection, "scoringdance", "scoringdance:quality")
        _add(db.connection, "scoringdance", "scoringdance:quality", sheet)
        projection = project_event(db.connection, EVENT, "2026-09-10T00:00:00Z", "run")

    assert {callback.outcome for callback in _records(projection, Callback)} == {
        "alternate",
        "alternate_3",
    }


def test_eliminated_outcome_is_withheld_when_same_entry_danced_later_round(
    tmp_path: Path,
) -> None:
    promoted = (("row-data-state", "CB"),)
    prelim = ResultTable(
        "Prelim",
        (Cell("Bib"), Cell("Leader"), Cell("J1")),
        (
            ResultRow((Cell("42"), Cell("A Dancer"), Cell("Y"))),
            ResultRow((Cell("43", promoted), Cell("B Dancer", promoted), Cell("Y", promoted))),
        ),
    )
    final = ResultTable(
        "Final",
        (Cell("Bib"), Cell("Leader"), Cell("Place")),
        (ResultRow((Cell("42"), Cell("A Dancer"), Cell("1"))),),
    )
    sheets = (
        RoundSheet(
            "round_sheet",
            "scoringdance:quality",
            "prelim",
            "Novice Jack & Jill Leader",
            "prelim",
            (prelim,),
        ),
        RoundSheet(
            "round_sheet",
            "scoringdance:quality",
            "final",
            "Novice Jack & Jill Leader",
            "final",
            (final,),
        ),
    )
    with open_database(tmp_path, lock=False) as db:
        _seed(db.connection, "scoringdance", "scoringdance:quality")
        _add(db.connection, "scoringdance", "scoringdance:quality", sheets)
        projection = project_event(db.connection, EVENT, "2026-09-10T00:00:00Z", "run")

    callbacks = _records(projection, Callback)
    assert [(callback.entry_id, callback.outcome) for callback in callbacks] == [
        (f"{EVENT}/novice-jj/L-43", "promoted")
    ]
    contradiction = next(
        finding
        for finding in projection.findings  # type: ignore[attr-defined]
        if finding.summary == "Eliminated callback conflicts with later-round participation"
    )
    assert contradiction.evidence["later_round_ids"] == [f"{EVENT}/novice-jj/final"]


def test_callback_aggregate_uses_marks_from_all_tables(tmp_path: Path) -> None:
    headers_a = (Cell("Bib"), Cell("Leader"), Cell("J1", (("t", "9"),)))
    headers_b = (Cell("Bib"), Cell("Leader"), Cell("J2", (("t", "9"),)))
    outcome = (("t", "2"),)
    tables = (
        ResultTable(
            "Prelim", headers_a, (ResultRow((Cell("7"), Cell("A"), Cell("Y"), Cell("Y", outcome))),)
        ),
        ResultTable(
            "Prelim", headers_b, (ResultRow((Cell("7"), Cell("A"), Cell("N"), Cell("Y", outcome))),)
        ),
    )
    sheet = RoundSheet(
        "round_sheet", "wdr:quality", "prelim", "Novice Jack & Jill Leader", "prelim", tables
    )

    with open_database(tmp_path, lock=False) as db:
        _seed(db.connection, "wdr", "wdr:quality")
        _add(db.connection, "wdr", "wdr:quality", sheet)
        projection = project_event(db.connection, EVENT, "2026-09-10T00:00:00Z", "run")

    marks = _records(projection, CallbackMark)
    callback = _records(projection, Callback)[0]
    assert len(marks) == 2
    assert callback.score_sum == sum(mark.mark_value for mark in marks)  # type: ignore[attr-defined]
    assert (callback.yes_count, callback.alt_count, callback.no_count) == (1, 0, 1)  # type: ignore[attr-defined]
    assert _records(projection, Round)[0].promoted_count == 1  # type: ignore[attr-defined]
