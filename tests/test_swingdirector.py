import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from swingset.model.observations import decode_payload, encode_payload
from swingset.sources import get_page_kind
from swingset.sources.base import ExtractError, ParseContext, ParseError
from swingset.sources.swingdirector import adapter
from swingset.sources.swingdirector.adapter import SOURCE, RoundPdf, watches_from_overrides

FIXTURES = Path("src/swingset/sources/swingdirector/fixtures")
POSITIVE = sorted(path for path in FIXTURES.glob("*.body") if "image" not in path.name)
PAGE = RoundPdf()


def meta(path: Path) -> dict:
    return json.loads(Path(f"{path}.json").read_text())


def context(path: Path, ref: str | None = "swingdirector:fixture-event") -> ParseContext:
    return ParseContext(
        "snap", "watch", meta(path)["url"], "swingdirector", PAGE.kind, ref, "2026-10-15T00:00:00Z"
    )


def parsed(name: str):
    path = FIXTURES / f"{name}-2026-10-15.body"
    return PAGE.parse(PAGE.extract(path.read_bytes()), context(path))


@pytest.mark.parametrize("path", POSITIVE, ids=lambda path: path.name)
def test_fixture_bodies_parse_to_their_recorded_sheets(path: Path) -> None:
    body = path.read_bytes()
    assert hashlib.sha256(body).hexdigest() == meta(path)["body_sha256"]
    result = PAGE.parse(PAGE.extract(body), context(path))
    expected = json.loads(Path(str(path).removesuffix(".body") + ".expected.json").read_text())
    assert [json.loads(encode_payload(o.payload)) for o in result.observations] == expected[
        "observations"
    ]
    assert [{"code": w.code, "evidence": w.evidence} for w in result.warnings] == expected[
        "warnings"
    ]
    for observation in result.observations:
        payload = observation.payload
        assert decode_payload(payload.kind, encode_payload(payload)) == payload
        assert observation.scope.ref == "swingdirector:fixture-event"


def test_final_keeps_rotated_judges_wrapped_entries_and_printed_places() -> None:
    sheet = parsed("hddc2026-nov-finals").observations[0].payload
    assert sheet.layout == "final"
    assert (sheet.contest_name_raw, sheet.round_name_raw, sheet.role_raw) == (
        "WSDC Novice Jack & Jill",
        "Scores",
        None,
    )
    assert sheet.judges_raw == ("C. REED", "M. KIELBASA", "M. ADAMS", "O. GONZALEZ", "Y. BENAVIDEZ")
    first = sheet.rows[0]
    assert (first.bib_raw, first.entry_raw, first.place_raw) == (
        "174",
        "MARK NARANG / NINA CHERE NORGAARD",
        "1",
    )
    assert first.marks_raw == ("9", "1", "1", "1", "4")
    assert first.tally_raw[:4] == ("3(3)", "3(3)", "3(3)", "4(7)")
    assert [row.place_raw for row in sheet.rows] == [str(n) for n in range(1, 12)]


def test_wrapped_tally_cells_are_joined_under_their_column() -> None:
    sheets = parsed("cds2026-finals-merged").observations
    novice = sheets[1].payload
    assert novice.contest_name_raw == "[WCS] WSDC Jack n' Jill - Novice"
    assert novice.rows[0].tally_raw[2:4] == ("6(12)", "7(16)")
    assert len(novice.judges_raw) == 7


def test_prelim_spans_pages_and_reports_that_callbacks_are_not_printed() -> None:
    result = parsed("sbdf2026-nov-prelims-leaders")
    sheet = result.observations[0].payload
    assert (sheet.round_name_raw, sheet.role_raw, sheet.pages) == (
        "Preliminaries",
        "Leaders",
        (1, 2),
    )
    assert len(sheet.rows) == 45
    assert {mark for row in sheet.rows for mark in row.marks_raw} <= {"Y", "N", "A1", "A2", "A3"}
    assert sheet.judges_raw[2] == "P. NUÑEZ"
    assert "swingdirector_callbacks_not_printed" in {w.code for w in result.warnings}
    hidden = [row for row in sheet.rows if row.entry_raw == "(Name hidden for discretion)"]
    # Four print hidden at the source; the fixture also hides four bibs that
    # print hidden in the semifinal, so it never carries their names.
    assert len(hidden) == 8 and sum(row.marks_raw == ("N",) * 4 for row in hidden) == 4


def test_semifinal_keeps_a_judge_name_the_sheet_wraps() -> None:
    sheet = parsed("hddc2026-nov-semis-followers").observations[0].payload
    assert sheet.round_name_raw == "Semifinals"
    assert sheet.judges_raw[2] == "T. BECKMANN KING"


def test_merged_file_splits_by_title_and_keeps_foreign_pages_visible() -> None:
    # The captured Calgary prelims file (1.3 MB, over the fixture limit) holds
    # eight SwingDirector sheets and four pages from another tool. The merged
    # finals file and an injected foreign page stand in for it.
    path = FIXTURES / "cds2026-finals-merged-2026-10-15.body"
    extract = PAGE.extract(path.read_bytes())
    pages = extract["pages"]
    assert len({page["title"] for page in pages}) == 6
    extract["pages"] = [*pages[:2], {"page": 3, "layout": "unsupported"}, *pages[2:]]
    result = PAGE.parse(extract, context(path))
    assert len(result.observations) == 6
    assert len({o.payload.source_round_ref for o in result.observations}) == 6
    assert {w.code: w.evidence for w in result.warnings}["swingdirector_page_unsupported"] == {
        "pages": [3]
    }
    extract["pages"] = [pages[0], {"page": 2, "layout": "unsupported"}, pages[0]]
    with pytest.raises(ParseError, match="repeats"):
        PAGE.parse(extract, context(path))


def test_a_pdf_without_any_swingdirector_page_fails_closed() -> None:
    body = (FIXTURES / "sbdf2025-nov-finals-image-2026-10-15.body").read_bytes()
    with pytest.raises(ExtractError, match="footer"):
        PAGE.extract(body)
    with pytest.raises(ExtractError):
        PAGE.extract(b"<html>not a pdf</html>")


def test_ordinals_must_sit_under_their_own_judge_and_match_the_tallies() -> None:
    path = FIXTURES / "hddc2026-allstar-finals-2026-10-15.body"
    extract = PAGE.extract(path.read_bytes())
    row = extract["pages"][0]["rows"][0]
    original = list(row["marks"])
    assert original[0] != original[1]
    # A swap keeps every tally, so only the per-judge ranking check sees it.
    row["marks"] = [original[1], original[0], *original[2:]]
    with pytest.raises(ParseError, match="judge column"):
        PAGE.parse(extract, context(path))
    row["marks"] = list(original)
    row["tallies"][0] = "9(9)"
    with pytest.raises(ParseError, match="tally"):
        PAGE.parse(extract, context(path))


def test_an_unreadable_page_is_reported_and_the_rest_is_kept(monkeypatch) -> None:
    path = FIXTURES / "sbdf2026-nov-prelims-leaders-2026-10-15.body"
    original = adapter._sheet_page

    def second_fails(items, number):
        if number == 2:
            raise ExtractError("SwingDirector header layout is unknown")
        return original(items, number)

    monkeypatch.setattr(adapter, "_sheet_page", second_fails)
    result = PAGE.parse(PAGE.extract(path.read_bytes()), context(path))
    (sheet,) = [o.payload for o in result.observations]
    assert sheet.pages == (1,) and len(sheet.rows) == 24
    assert {w.code: w.evidence for w in result.warnings}["swingdirector_page_failed"] == {
        "pages": [{"page": 2, "reason": "SwingDirector header layout is unknown"}]
    }
    monkeypatch.setattr(adapter, "_sheet_page", lambda items, number: second_fails(items, 2))
    with pytest.raises(ExtractError, match="readable"):
        PAGE.extract(path.read_bytes())


def test_time_and_content_bounds_fail_the_whole_file(monkeypatch) -> None:
    body = (FIXTURES / "hddc2026-nov-prelims-leaders-2026-10-15.body").read_bytes()
    ticks = iter([0.0, 0.0, 1000.0])
    monkeypatch.setattr(adapter, "_clock", lambda: next(ticks, 1000.0))
    with pytest.raises(ExtractError, match="time bound"):
        PAGE.extract(body)
    monkeypatch.undo()
    monkeypatch.setattr(adapter, "MAX_CONTENT_BYTES", 100)
    with pytest.raises(ExtractError, match="content exceeds"):
        PAGE.extract(body)


def test_unknown_marks_and_missing_event_refs_are_parse_errors() -> None:
    path = FIXTURES / "hddc2026-nov-semis-followers-2026-10-15.body"
    extract = PAGE.extract(path.read_bytes())
    with pytest.raises(ParseError, match="event reference"):
        PAGE.parse(extract, context(path, None))
    extract["pages"][0]["rows"][0]["marks"][0] = "A4"
    with pytest.raises(ParseError, match="legend"):
        PAGE.parse(extract, context(path))


def test_the_kind_is_registered_and_declares_contract_1() -> None:
    assert get_page_kind("swingdirector.round") is SOURCE.page_kinds["swingdirector.round"]
    path = FIXTURES / "hddc2026-nov-finals-2026-10-15.body"
    result = PAGE.parse(PAGE.extract(path.read_bytes()), context(path))
    assert result.interpretation is not None
    assert result.interpretation.contract_version == "1"
    assert not result.watches
    assert PAGE.change_mode == "validators"


def test_override_rows_seed_one_watch_per_pdf_under_the_event_reference() -> None:
    rows = [
        {
            "event_id": "2026-03-high-desert-dance-classic",
            "source": "swingdirector",
            "kind": "event",
            "url": "https://highdesertdanceclassic.com/pdf/Results%202026/WSDC%20Nov%20JJ%20Finals.pdf",
            "parser": "swingdirector.round",
            "notes": "reviewed",
        },
        {"event_id": "x", "source": "wdr", "url": "https://example.com/a", "parser": "wdr.rounds"},
        {"event_id": "", "source": "swingdirector", "url": "https://sbdf.dance/a.pdf"},
    ]
    (spec,) = SOURCE.seed_watches(None, rows)
    assert spec.source_ref == "swingdirector:2026-03-high-desert-dance-classic"
    assert (spec.parser, spec.kind, spec.method) == ("swingdirector.round", "event", "GET")
    assert spec.watch_id == replace(spec, source_ref="other").watch_id
    assert watches_from_overrides(None) == []


# Projection: parse -> source_event_map -> project_event (D-0259).

HDDC = "2026-03-high-desert-dance-classic"
SBDF = "2026-09-south-bay-dance-fling"


def _sheets(name: str, event: str):
    path = FIXTURES / f"{name}-2026-10-15.body"
    result = PAGE.parse(PAGE.extract(path.read_bytes()), context(path, f"swingdirector:{event}"))
    return meta(path)["url"], [observation.payload for observation in result.observations]


def _project(tmp_path: Path, event: str, names: list[str]):
    from swingset.project.contests import project_event
    from swingset.state.db import open_database

    ref = f"swingdirector:{event}"
    with open_database(tmp_path, lock=False) as db:
        conn = db.connection
        conn.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('run','2026-10-15T00:00:00Z',0)"
        )
        conn.execute(
            "INSERT INTO source_event_map(source,source_ref,event_id,match_method,match_confidence) "
            "VALUES ('swingdirector',?,?,'override',1)",
            (ref, event),
        )
        for number, name in enumerate(names):
            url, sheets = _sheets(name, event)
            watch, snapshot = f"watch-{number}", f"snap-{number}"
            conn.execute(
                "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) "
                "VALUES (?,'swingdirector','event','GET',?,'swingdirector.round',?,'live')",
                (watch, url, ref),
            )
            conn.execute(
                "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,"
                "body_bytes,content_changed,run_id,classification) VALUES "
                "(?,?,'GET',?,?,200,1,1,'run','Ok')",
                (snapshot, watch, url, f"2026-10-15T00:00:0{number}Z"),
            )
            for seq, sheet in enumerate(sheets):
                conn.execute(
                    "INSERT INTO observations(observation_id,watch_id,snapshot_id,kind,scope_kind,"
                    "scope_id,seq,extract_version,parser_version,payload_json) VALUES "
                    "(?,?,?,?,'source_event',?,?,'1','1',?)",
                    (
                        f"{snapshot}-{seq}",
                        watch,
                        snapshot,
                        sheet.kind,
                        ref,
                        seq,
                        encode_payload(sheet),
                    ),
                )
        return project_event(conn, event, "2026-10-15T01:00:00Z", "run")


def _rows(projection, name: str) -> list:
    return [row for row in projection.rows if type(row).__name__ == name]


def test_hddc_novice_projects_rounds_placements_marks_and_follower_callbacks(tmp_path) -> None:
    projection = _project(
        tmp_path,
        HDDC,
        ["hddc2026-nov-prelims-leaders", "hddc2026-nov-semis-followers", "hddc2026-nov-finals"],
    )
    (contest,) = _rows(projection, "Contest")
    assert (contest.contest_id, contest.division, contest.contest_type) == (
        f"{HDDC}/novice-jj",
        "novice",
        "jack_and_jill",
    )
    assert contest.wsdc_points_eligible and contest.parse_status == "parsed"
    rounds = {row.round_type: row for row in _rows(projection, "Round")}
    assert [
        (t, r.round_index, r.name_raw)
        for t, r in sorted(rounds.items(), key=lambda i: i[1].round_index)
    ] == [
        ("prelim", 1, "Preliminaries"),
        ("semifinal", 2, "Semifinals"),
        ("final", 3, "Final"),
    ]
    assert rounds["final"].scoring_method == "relative_placement"
    # Leader semifinals were not captured, so the leader prelim has no outcomes.
    assert rounds["prelim"].promoted_count is None
    assert rounds["semifinal"].promoted_count == 11
    placements = sorted(_rows(projection, "Placement"), key=lambda row: row.place)
    assert [row.place for row in placements] == list(range(1, 12))
    first = placements[0]
    assert first.leader_entry_id == f"{HDDC}/novice-jj/L-174"
    # The final prints only the leader's bib; the follower joins her semifinal
    # entry by name.
    assert first.follower_entry_id == f"{HDDC}/novice-jj/F-360"
    assert first.tally == (3, 3, 3, 4, 4, 4, 4, 4, 5, 5, 5)
    entries = {row.entry_id: row for row in _rows(projection, "Entry")}
    nina = entries[f"{HDDC}/novice-jj/F-360"]
    assert (nina.name_raw, nina.rounds_danced, nina.best_round) == (
        "NINA CHERE NORGAARD",
        ("semifinal", "final"),
        "final",
    )
    assert nina.partner_entry_id == f"{HDDC}/novice-jj/L-174"
    final_marks = _rows(projection, "FinalMark")
    assert len(final_marks) == 11 * 5
    judges = {row.judge_id: row for row in _rows(projection, "Judge")}
    assert f"{HDDC}/judge/c-reed" in judges and judges[f"{HDDC}/judge/c-reed"].name_raw == "C. REED"
    assert {mark.rank for mark in final_marks if mark.judge_id.endswith("c-reed")} == set(
        range(1, 12)
    )
    callbacks = {
        row.entry_id.rsplit("/", 1)[1]: row
        for row in _rows(projection, "Callback")
        if row.round_id.endswith("/semifinal")
    }
    assert callbacks["F-360"].outcome == "promoted"
    assert callbacks["F-111"].outcome == "eliminated"  # 14.5, below the 20.0 cut
    assert sum(row.outcome == "promoted" for row in callbacks.values()) == 11
    # Hidden rows keep their bib, lose the name, and publish no marks.
    hidden = entries[f"{HDDC}/novice-jj/F-118"]
    assert hidden.name_raw is None and "F-118" not in callbacks
    assert not [m for m in _rows(projection, "CallbackMark") if m.entry_id.endswith("/F-118")]
    marks = [m for m in _rows(projection, "CallbackMark") if m.entry_id.endswith("/F-360")]
    assert sorted(m.mark_raw for m in marks) == ["A3", "Y", "Y", "Y"]


def test_sbdf_novice_derives_each_leader_callback_from_the_next_roster(tmp_path) -> None:
    projection = _project(
        tmp_path,
        SBDF,
        ["sbdf2026-nov-prelims-leaders", "sbdf2026-nov-semis-leaders", "sbdf2026-nov-finals"],
    )
    (contest,) = _rows(projection, "Contest")
    assert contest.contest_id == f"{SBDF}/novice-jj" and contest.wsdc_points_eligible
    rounds = {row.round_type: row for row in _rows(projection, "Round")}
    # 26 danced the semifinal; four of them print hidden there, and hidden
    # rows publish no callback, as WDR's redacted rows do.
    assert (rounds["prelim"].promoted_count, rounds["semifinal"].promoted_count) == (22, 15)
    assert rounds["semifinal"].entry_count == 26
    assert rounds["prelim"].entry_count == 45 and rounds["final"].entry_count == 30
    outcomes = {
        (row.round_id.rsplit("/", 1)[1], row.entry_id.rsplit("/", 1)[1]): row.outcome
        for row in _rows(projection, "Callback")
    }
    assert outcomes[("prelim", "L-400")] == "promoted"  # 34.5 with an A1
    assert outcomes[("prelim", "L-351")] == "eliminated"  # 24.5, below the 30.0 cut
    assert outcomes[("semifinal", "L-359")] == "promoted"  # 18.5, the cut
    assert outcomes[("semifinal", "L-198")] == "eliminated"
    placements = sorted(_rows(projection, "Placement"), key=lambda row: row.place)
    assert len(placements) == 15
    assert placements[0].leader_entry_id == f"{SBDF}/novice-jj/L-369"
    entries = {row.entry_id: row for row in _rows(projection, "Entry")}
    assert entries[f"{SBDF}/novice-jj/L-369"].rounds_danced == ("prelim", "semifinal", "final")
    assert entries[f"{SBDF}/novice-jj/L-336"].name_raw is None
    assert ("prelim", "L-336") not in outcomes
    followers = [row for row in entries.values() if row.role == "follower"]
    assert len(followers) == 15 and all(row.bib is None for row in followers)


def test_callbacks_are_withheld_when_the_next_roster_is_not_the_next_round() -> None:
    from swingset.project.swingdirector import derive

    _url, (prelim,) = _sheets("sbdf2026-nov-prelims-leaders", SBDF)
    _url, (semi,) = _sheets("sbdf2026-nov-semis-leaders", SBDF)
    _url, (final,) = _sheets("sbdf2026-nov-finals", SBDF)
    assert derive(prelim, [prelim, semi, final]).reason is None
    # Without the semifinal the final looks like the next round, but semifinal
    # dancers who missed the final outscore the finalists' cut.
    assert derive(prelim, [prelim, final]).reason == "next_round_not_successor"
    assert derive(final, [prelim, final]).reason == "not_a_callback_round"
    assert derive(semi, [semi]).reason == "no_next_round"
    # A finalist missing from the earlier round leaves every outcome unknown.
    stray = replace(final, rows=(*final.rows, replace(final.rows[0], bib_raw="999")))
    assert derive(semi, [semi, stray]).reason == "next_round_unmatched"


def test_one_scratch_above_the_cut_has_no_outcome_and_ties_stay_unknown() -> None:
    from swingset.project.swingdirector import derive

    _url, (semi,) = _sheets("hddc2026-nov-semis-followers", HDDC)
    _url, (final,) = _sheets("hddc2026-nov-finals", HDDC)
    # Drop Srishti Love (40.0) from the final, as if she had scratched.
    scratched = replace(
        final, rows=tuple(row for row in final.rows if "SRISHTI LOVE" not in row.entry_raw)
    )
    outcomes = derive(semi, [semi, scratched]).outcomes
    assert outcomes["315"] is None and outcomes["360"] == "promoted"
    assert outcomes["111"] == "eliminated"
    # Two non-finalists above the cut mean another round sat between them.
    twice = replace(
        scratched,
        rows=tuple(row for row in scratched.rows if "KATE SOLHAVEN" not in row.entry_raw),
    )
    assert derive(semi, [semi, twice]).reason == "next_round_not_successor"


def test_calgary_jack_n_jill_finals_are_points_eligible_contests(tmp_path) -> None:
    projection = _project(tmp_path, "2026-04-calgary-dance-stampede", ["cds2026-finals-merged"])
    contests = {row.contest_id.rsplit("/", 1)[1]: row for row in _rows(projection, "Contest")}
    assert set(contests) == {
        "newcomer-jj",
        "novice-jj",
        "intermediate-jj",
        "advanced-jj",
        "sophisticated-jj",
        "masters-jj",
    }
    assert contests["novice-jj"].wsdc_points_eligible
    assert len(_rows(projection, "Placement")) == 8 + 12 + 9 + 7 + 5 + 7


def test_override_rows_map_to_their_event_without_alias_rows() -> None:
    from swingset.project.map import overrides
    from swingset.schedule.event_request_kind import is_source_event_request, purpose

    body = (
        "event_id,source,kind,url,parser,notes\n"
        f"{SBDF},swingdirector,event,https://sbdf.dance/a.pdf,swingdirector.round,reviewed\n"
    ).encode()
    assert overrides({"overrides/source_urls.csv": body}) == {
        ("swingdirector", f"swingdirector:{SBDF}"): SBDF
    }
    assert purpose("swingdirector.round") == "result"
    assert is_source_event_request(
        source="swingdirector",
        parser="swingdirector.round",
        watch_kind="event",
        source_ref=f"swingdirector:{SBDF}",
    )


def _report(path: Path, result=None, extract=None):
    from swingset.admission.contracts import inspect

    body = path.read_bytes()
    extract = PAGE.extract(body) if extract is None else extract
    result = PAGE.parse(extract, context(path)) if result is None else result
    return inspect(context(path), body, extract, result)


@pytest.mark.parametrize("path", POSITIVE, ids=lambda path: path.name)
def test_contract_1_admits_every_captured_file(path: Path) -> None:
    report = _report(path)
    assert report.contract_version == "1" and not report.failures
    assert report.coverage.source_count == report.coverage.interpreted_count > 0
    assert report.proposed_removal == "watch"


def test_contract_1_blocks_lost_rows_bad_tallies_and_unreadable_pages() -> None:
    from swingset.admission import swingdirector_contract
    from swingset.sources.swingdirector.records import PRELIM_MARKS

    assert swingdirector_contract.PRELIM_MARKS == PRELIM_MARKS
    path = FIXTURES / "hddc2026-nov-finals-2026-10-15.body"
    extract = PAGE.extract(path.read_bytes())
    result = PAGE.parse(extract, context(path))
    observation = result.observations[0]
    sheet = observation.payload

    def with_sheet(changed):
        return replace(result, observations=(replace(observation, payload=changed),))

    # A row the parser lost is still counted from the extract.
    lost = _report(path, with_sheet(replace(sheet, rows=sheet.rows[:-1])), extract)
    assert "coverage_count_mismatch" in lost.failures
    # A tally that no longer recomputes from the ordinals blocks the file.
    first = replace(sheet.rows[0], tally_raw=("9(9)", *sheet.rows[0].tally_raw[1:]))
    bad = _report(path, with_sheet(replace(sheet, rows=(first, *sheet.rows[1:]))), extract)
    assert "swingdirector_final_tallies" in bad.failures
    # A SwingDirector page the extractor could not read blocks the file; a page
    # another tool printed does not.
    failed = {
        **extract,
        "pages": [*extract["pages"], {"page": 2, "layout": "failed", "reason": "x"}],
    }
    assert "critical_unknown" in _report(path, extract=failed).failures
    foreign = {**extract, "pages": [*extract["pages"], {"page": 2, "layout": "unsupported"}]}
    assert not _report(path, extract=foreign).failures


def test_a_bib_hidden_in_one_round_is_hidden_in_every_round_of_its_role() -> None:
    from swingset.project.swingdirector import REDACTED, round_sheets

    _url, (prelim,) = _sheets("sbdf2026-nov-prelims-leaders", SBDF)
    _url, (semi,) = _sheets("sbdf2026-nov-semis-leaders", SBDF)
    # The committed prelim already hides bib 336; a placeholder name stands in
    # for how the source printed it, so the fixture carries no real name.
    named = replace(
        prelim,
        rows=tuple(
            replace(row, entry_raw="PLACEHOLDER DANCER") if row.bib_raw == "336" else row
            for row in prelim.rows
        ),
    )
    projected, _semi = round_sheets([named, semi])
    (table,) = projected.tables
    row = next(row for row in table.rows if row.cells[0].text == "336")
    assert row.cells[1].text == REDACTED
    assert "PLACEHOLDER DANCER" not in repr(projected)
