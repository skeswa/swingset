from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from swingset.clock import FakeClock
from swingset.link import link_event
from swingset.link.service import _seed_confirmation_watches, _update_registry_points
from swingset.model.ids import observation_id
from swingset.model.observations import encode_payload
from swingset.project.process import process_unit
from swingset.schedule.watches import upsert_watch
from swingset.sources.records import Cell, DancerLookup, ResultRow, ResultTable, RoundSheet
from swingset.sources.records import RegistryPlacement as RawPlacement
from swingset.sources.wsdc_registry import DancerPage
from swingset.sources.wsdc_registry.adapter import SOURCE as REGISTRY_SOURCE
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, bump_revision, enqueue


class Bundle:
    files = MappingProxyType(
        {"link/weights.toml": b"name=0.65\nrole=0.10\nrecency=0.05\ndivision=0.20\nsource_id=1.0\n"}
    )

    def __init__(self, overrides: list[dict[str, str]] | None = None) -> None:
        self.overrides = overrides or []

    def csv(self, name: str) -> list[dict[str, str]]:
        if name == "identity_overrides.csv":
            return self.overrides
        return []


def seed(conn: object) -> None:
    conn.execute(
        "INSERT INTO events(event_id,series_id,name,year,start_date,end_date,wsdc_status,sources,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('event','wsdc-1','Event',2026,'2026-01-01','2026-01-04','registry','[]','test','snap','1','t','t','run')"
    )
    for contest in ("c1", "c2"):
        conn.execute(
            "INSERT INTO contests(contest_id,event_id,name_raw,division,age_division,contest_type,partner_mode,dance_style,wsdc_points_eligible,combined_from,parse_status,source_contest_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,'event','Novice J&J','novice','none','jack_and_jill','random_partner','wcs',1,'[]','parsed',?,'test','snap','1','t','t','run')",
            (contest, contest),
        )
    for dancer_id in (1, 2):
        conn.execute(
            "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,'Alex','Lee','alex lee',0,'leader','novice','advanced','novice','advanced','novice',1,'novice',1,2026,?,'t','test','snap','1','t','t','run')",
            (dancer_id, dancer_id),
        )


def entry(conn: object, entry_id: str, contest: str, bib: str, name: str = "Alex Lee") -> None:
    conn.execute(
        "INSERT INTO entries(entry_id,contest_id,event_id,role,bib,name_raw,name_norm,link_status,link_confidence,rounds_danced,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,'event','leader',?,?,?,'unmatched',0,'[]','test','snap','1','t','t','run')",
        (entry_id, contest, bib, name, name.casefold()),
    )


def owned_source_sheet(conn, rows, *, contest="c1", snapshot="owned-source"):
    """Explicit synthetic owned cells; no event-wide name-to-ID injection."""
    conn.execute(
        "INSERT OR IGNORE INTO runs(run_id,started_at,dry_run) VALUES ('run','2026-01-05',1)"
    )
    source_ref = "scoringdance:event"
    conn.execute(
        "INSERT OR IGNORE INTO source_event_map VALUES ('scoringdance',?,'event','override',1)",
        (source_ref,),
    )
    conn.execute(
        "INSERT INTO watches(watch_id,source,kind,method,url,parser,source_ref,state) VALUES (?,'scoringdance','round','GET',?,'scoringdance.round',?,'live')",
        (snapshot, f"https://example/{snapshot}", source_ref),
    )
    conn.execute(
        "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES (?,?,'GET',?,'2026-01-05',200,1,1,'run','Ok')",
        (snapshot, snapshot, f"https://example/{snapshot}"),
    )
    table = ResultTable(
        "prelim",
        (Cell("Bib"), Cell("Leader")),
        tuple(
            ResultRow(
                (
                    Cell(bib),
                    Cell(name, (("data-wsdc", str(number)),)) if number is not None else Cell(name),
                )
            )
            for bib, name, number in rows
        ),
    )
    payload = RoundSheet("round_sheet", source_ref, contest, "Novice J&J", "Prelim", (table,))
    conn.execute(
        "INSERT INTO observations VALUES (?,?,?,'round_sheet','source_event',?,0,'1','1',?)",
        (snapshot, snapshot, snapshot, source_ref, encode_payload(payload)),
    )
    for bib, _name, _number in rows:
        conn.execute(
            "UPDATE entries SET snapshot_id=?,source='scoringdance' WHERE contest_id=? AND bib=?",
            (snapshot, contest, bib),
        )


def run(db: object, bundle: Bundle) -> None:
    db.connection.execute(
        "INSERT OR IGNORE INTO runs(run_id,started_at,dry_run) VALUES ('run','2026-01-05',1)"
    )
    with db.transaction() as conn:
        enqueue(conn, (WorkUnit("link", "event", "event"),), enqueued_at="2026-01-05T00:00:00Z")
    link_event(db, "event", bundle, FakeClock(datetime(2026, 1, 5, tzinfo=UTC)), "run")  # type: ignore[arg-type]


def test_same_bib_across_contests_does_not_merge_identities(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        entry(db.connection, "event/c2/L-7", "c2", "7", "Different Person")
        run(db, Bundle())
        rows = db.connection.execute(
            "SELECT wsdc_id,method FROM identity_links ORDER BY subject_id"
        ).fetchall()
        assert tuple(rows[0]) == (1, "assignment")
        assert tuple(rows[1]) == (None, "none")


def test_registry_addition_reaches_previously_unmatched_entry(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        db.connection.execute(
            "UPDATE entries SET name_raw='New Person',name_norm='new person' WHERE entry_id='event/c1/L-7'"
        )
        run(db, Bundle())
        assert (
            db.connection.execute("SELECT status FROM identity_links").fetchone()[0] == "unmatched"
        )
        db.connection.execute(
            "INSERT INTO dancers(wsdc_id,first_name,last_name,name_norm,is_pro,primary_role,leader_required_level,leader_allowed_level,follower_required_level,follower_allowed_level,leader_highest_level,leader_highest_points,follower_highest_level,follower_highest_points,recent_year,registry_internal_id,registry_fetched_at,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (3,'New','Person','new person',0,'leader','novice','advanced','novice','advanced','novice',1,'novice',1,2026,3,'t','test','snap','1','t','t','run')"
        )
        # Model the registry writer's semantic revision alongside this direct
        # fixture insertion; queue timestamps alone are no longer inputs.
        bump_revision(db.connection, "dancers")
        run(db, Bundle())
        row = db.connection.execute("SELECT wsdc_id,status FROM identity_links").fetchone()
        assert tuple(row) == (3, "probable")


@pytest.mark.parametrize(
    "now", [datetime(2026, 1, 12, tzinfo=UTC), datetime(2026, 2, 12, tzinfo=UTC)]
)
def test_later_registry_finalist_confirms_unmatched_entry_without_inventing_points(
    tmp_path,
    now,
) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        db.connection.execute(
            "INSERT INTO rounds(round_id,contest_id,round_type,round_index,name_raw,scoring_method,callback_legend,judge_count,entry_count,source_round_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('c1/final','c1','final',1,'Final','relative_placement','{}',5,6,'final','test','snap','1','t','t','run')"
        )
        entry(db.connection, "event/c1/F-7", "c1", "7", "New Person")
        db.connection.execute(
            "UPDATE entries SET role='follower',rounds_danced='[\"final\"]' WHERE entry_id='event/c1/F-7'"
        )
        db.connection.execute(
            "INSERT INTO placements(placement_id,round_id,contest_id,event_id,place,follower_entry_id,tally,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('place','c1/final','c1','event',6,'event/c1/F-7','','test','snap','1','t','t','run')"
        )
        run(db, Bundle())
        assert tuple(
            db.connection.execute(
                "SELECT wsdc_id,status FROM identity_links WHERE subject_id='event/c1/F-7'"
            ).fetchone()
        ) == (None, "unmatched")
        assert db.connection.execute("SELECT COUNT(*) FROM link_candidates").fetchone()[0] == 0

        spec = REGISTRY_SOURCE.watch(3)
        upsert_watch(db.connection, spec, now)
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('registry-run',?,1)",
            (now.isoformat(),),
        )
        db.connection.execute(
            "INSERT INTO snapshots(snapshot_id,watch_id,method,url,fetched_at,http_status,body_bytes,content_changed,run_id,classification) VALUES ('registry-snap',?,'POST',?,?,200,1,1,'registry-run','Ok')",
            (spec.watch_id, spec.url, now.isoformat()),
        )
        payload = DancerLookup(
            "dancer_lookup",
            "found",
            3,
            3,
            first_name="New",
            last_name="Person",
            primary_role_raw="F",
            follower_required_raw="NOV",
            follower_allowed_raw="ADV",
            follower_highest_raw="NOV",
            recent_year=2026,
            placements=(
                RawPlacement(
                    "follower",
                    "NOV",
                    "1",
                    "Event",
                    "January 2026",
                    "F",
                    1,
                    "West Coast Swing",
                ),
            ),
        )
        page = DancerPage()
        db.connection.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                observation_id(spec.watch_id, "registry-snap", payload.kind, 0),
                spec.watch_id,
                "registry-snap",
                payload.kind,
                "dancer",
                "3",
                0,
                str(page.EXTRACT_VERSION),
                str(page.PARSER_VERSION),
                encode_payload(payload),
            ),
        )
        with db.transaction() as conn:
            enqueue(conn, (WorkUnit("project", "dancer", "3"),), enqueued_at=now.isoformat())
        assert process_unit(
            db,
            WorkUnit("project", "dancer", "3"),
            Bundle(),
            FakeClock(now),
            "registry-run",
        )
        assert db.connection.execute(
            "SELECT 1 FROM pending_work WHERE stage='link' AND unit_kind='event' AND unit_id='event'"
        ).fetchone()

        link_event(db, "event", Bundle(), FakeClock(now), "run")
        assert tuple(
            db.connection.execute(
                "SELECT wsdc_id,status,method FROM identity_links WHERE subject_id='event/c1/F-7'"
            ).fetchone()
        ) == (3, "confirmed", "registry_placement")
        assert tuple(
            db.connection.execute(
                "SELECT follower_wsdc_id,registry_points_follower,registry_confirmed FROM placements WHERE placement_id='place'"
            ).fetchone()
        ) == (3, None, 0)


def test_source_id_can_link_same_dancer_across_contests(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        entry(db.connection, "event/c2/L-8", "c2", "8")
        owned_source_sheet(db.connection, [("7", "Alex Lee", 1)], contest="c1", snapshot="first")
        owned_source_sheet(db.connection, [("8", "Alex Lee", 1)], contest="c2", snapshot="second")
        run(db, Bundle())
        ids = [row[0] for row in db.connection.execute("SELECT wsdc_id FROM identity_links")]
        assert ids.count(1) == 2


def test_registry_finalist_claim_requires_one_exact_name(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        db.connection.execute(
            "INSERT INTO rounds(round_id,contest_id,round_type,round_index,name_raw,scoring_method,callback_legend,judge_count,entry_count,source_round_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('c1/final','c1','final',1,'Final','relative_placement','{}',5,6,'final','test','snap','1','t','t','run')"
        )
        db.connection.execute(
            "INSERT INTO placements(placement_id,round_id,contest_id,event_id,place,leader_entry_id,tally,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('place','c1/final','c1','event',6,'event/c1/L-7','','test','snap','1','t','t','run')"
        )
        for wsdc_id in (1, 2):
            db.connection.execute(
                "INSERT INTO registry_placements(wsdc_id,role,dance_style,division,series_id,series_name_raw,event_month,event_id,result,points,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,'leader','wcs','novice','wsdc-1','Event','2026-01','event','F',1,'test','snap','1','t','t','run')",
                (wsdc_id,),
            )
        run(db, Bundle())
        row = db.connection.execute(
            "SELECT method,status FROM identity_links WHERE subject_id='event/c1/L-7'"
        ).fetchone()
        assert row[0] != "registry_placement"
        assert row[1] != "confirmed"


def test_event_relink_preserves_daily_confirmation_cadence(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        db.connection.execute("UPDATE entries SET wsdc_id=1 WHERE entry_id='event/c1/L-7'")
        db.connection.execute(
            "INSERT INTO rounds(round_id,contest_id,round_type,round_index,name_raw,scoring_method,callback_legend,judge_count,entry_count,source_round_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('c1/final','c1','final',1,'Final','relative_placement','{}',5,6,'final','test','snap','1','t','t','run')"
        )
        db.connection.execute(
            "INSERT INTO placements(placement_id,round_id,contest_id,event_id,place,leader_entry_id,tally,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('place','c1/final','c1','event',6,'event/c1/L-7','','test','snap','1','t','t','run')"
        )
        first = datetime(2026, 1, 5, tzinfo=UTC)
        _seed_confirmation_watches(db.connection, "event", first)
        watch_id = REGISTRY_SOURCE.watch(1).watch_id
        db.connection.execute(
            "UPDATE watches SET last_checked_at='2026-01-05T06:00:00+00:00',next_check_at='2026-01-06T06:00:00+00:00' WHERE watch_id=?",
            (watch_id,),
        )

        _seed_confirmation_watches(db.connection, "event", datetime(2026, 1, 5, 12, tzinfo=UTC))

        assert (
            db.connection.execute(
                "SELECT next_check_at FROM watches WHERE watch_id=?", (watch_id,)
            ).fetchone()[0]
            == "2026-01-06T06:00:00+00:00"
        )


def test_registry_points_use_each_roles_prelim_field_and_dance_style(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        db.connection.execute(
            "INSERT INTO rounds(round_id,contest_id,round_type,round_index,name_raw,scoring_method,callback_legend,judge_count,entry_count,source_round_ref,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('c1/prelim','c1','prelim',1,'Prelim','relative_placement','{}',5,20,'prelim','test','snap','1','t','t','run'),('c1/final','c1','final',2,'Final','relative_placement','{}',5,10,'final','test','snap','1','t','t','run')"
        )
        for role, count in (("leader", 12), ("follower", 20)):
            for number in range(count):
                identifier = f"event/c1/{role[0]}-{number}"
                entry(db.connection, identifier, "c1", str(number))
                db.connection.execute(
                    "UPDATE entries SET role=?,rounds_danced='[\"prelim\"]' WHERE entry_id=?",
                    (role, identifier),
                )
        db.connection.execute("UPDATE entries SET wsdc_id=1 WHERE entry_id='event/c1/l-0'")
        db.connection.execute("UPDATE entries SET wsdc_id=2 WHERE entry_id='event/c1/f-0'")
        db.connection.execute(
            "INSERT INTO placements(placement_id,round_id,contest_id,event_id,place,leader_entry_id,follower_entry_id,tally,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES ('place','c1/final','c1','event',1,'event/c1/l-0','event/c1/f-0','','test','snap','1','t','t','run')"
        )
        for wsdc_id, role, style, points in (
            (1, "leader", "wcs", 6),
            (2, "follower", "wcs", 10),
            (1, "leader", "country", 25),
        ):
            db.connection.execute(
                "INSERT INTO registry_placements(wsdc_id,role,dance_style,division,series_id,series_name_raw,event_month,event_id,result,points,source,snapshot_id,parser_version,first_seen_at,last_seen_at,run_id) VALUES (?,?,?,'novice','wsdc-1','Event','2026-01','event','1',?,'test','snap','1','t','t','run')",
                (wsdc_id, role, style, points),
            )

        _update_registry_points(db.connection, "event")

        assert tuple(
            db.connection.execute(
                "SELECT registry_points_leader,registry_points_follower,registry_confirmed,points_matches_expected FROM placements WHERE placement_id='place'"
            ).fetchone()
        ) == (6, 10, 1, 1)

        db.connection.execute("UPDATE contests SET wsdc_points_eligible=0 WHERE contest_id='c1'")
        _update_registry_points(db.connection, "event")
        assert tuple(
            db.connection.execute(
                "SELECT registry_points_leader,registry_points_follower,registry_confirmed,points_matches_expected FROM placements WHERE placement_id='place'"
            ).fetchone()
        ) == (None, None, 0, None)

        db.connection.execute("UPDATE contests SET wsdc_points_eligible=1 WHERE contest_id='c1'")
        db.connection.execute(
            "UPDATE entries SET rounds_danced='[]' WHERE contest_id='c1' AND role='follower'"
        )
        _update_registry_points(db.connection, "event")
        assert tuple(
            db.connection.execute(
                "SELECT registry_points_leader,registry_points_follower,registry_confirmed,points_matches_expected FROM placements WHERE placement_id='place'"
            ).fetchone()
        ) == (6, 10, 1, None)

        db.connection.execute(
            "UPDATE entries SET rounds_danced='[\"prelim\"]' WHERE contest_id='c1' AND role='follower'"
        )
        db.connection.execute(
            "INSERT INTO runs(run_id,started_at,dry_run) VALUES ('finding-run','t',0)"
        )
        db.connection.execute(
            "INSERT INTO findings(finding_id,owner_kind,owner_id,kind,subject_kind,subject_id,severity,summary,evidence_json,opened_at,run_id) VALUES ('missing','event','event','missing_identity','round','c1/prelim','warning','Redacted entrant','{}','t','finding-run')"
        )
        _update_registry_points(db.connection, "event")
        assert (
            db.connection.execute(
                "SELECT points_matches_expected FROM placements WHERE placement_id='place'"
            ).fetchone()[0]
            is None
        )


def test_identical_names_use_their_own_printed_cells_and_do_not_borrow_ids(tmp_path):
    with open_database(tmp_path) as db:
        seed(db.connection)
        for bib in ("7", "8", "9"):
            entry(db.connection, f"entry-{bib}", "c1", bib)
        owned_source_sheet(
            db.connection, [("7", "Alex Lee", 1), ("8", "Alex Lee", 2), ("9", "Alex Lee", None)]
        )
        run(db, Bundle())
        rows = {
            row[0]: tuple(row[1:])
            for row in db.connection.execute("SELECT entry_id,wsdc_id,link_status FROM entries")
        }
        assert rows["entry-7"] == (1, "confirmed")
        assert rows["entry-8"] == (2, "confirmed")
        assert rows["entry-9"][0] is None
        assert (
            db.connection.execute(
                "SELECT method FROM identity_links WHERE subject_id='entry-9'"
            ).fetchone()[0]
            != "source_id"
        )


def test_conflicting_owned_printed_ids_withhold_and_retain_both_source_signals(tmp_path):
    with open_database(tmp_path) as db:
        seed(db.connection)
        entry(db.connection, "entry", "c1", "7")
        owned_source_sheet(db.connection, [("7", "Alex Lee", 1), ("7", "Alex Lee", 2)])
        run(db, Bundle())
        assert db.connection.execute("SELECT wsdc_id,link_status FROM entries").fetchone()[:] == (
            None,
            "unmatched",
        )
        links = db.connection.execute("SELECT wsdc_id,status FROM identity_links").fetchone()
        assert tuple(links) == (None, "unmatched")
        candidates = db.connection.execute(
            "SELECT wsdc_id,score,source_id_confirms,chosen FROM link_candidates ORDER BY wsdc_id"
        ).fetchall()
        assert [tuple(row) for row in candidates] == [(1, 1.0, 1, 0), (2, 1.0, 1, 0)]
        evidence = db.connection.execute(
            "SELECT evidence_json FROM findings WHERE kind='identity_decision' AND closed_at IS NULL"
        ).fetchone()[0]
        assert "contradictory_printed_source_identities" in evidence
        assert '"printed_wsdc_ids":[1,2]' in evidence
