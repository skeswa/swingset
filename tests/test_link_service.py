from datetime import UTC, datetime
from types import MappingProxyType
from unittest.mock import patch

from swingset.clock import FakeClock
from swingset.link import link_event
from swingset.state.db import open_database
from swingset.state.work import WorkUnit, enqueue


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


def run(db: object, bundle: Bundle) -> None:
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


def test_manual_none_and_manual_id_win(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        overrides = [
            {
                "entry_id": "event/c1/L-7",
                "wsdc_id": "NONE",
                "reason": "review",
                "author": "test",
                "date": "2026-01-05",
            }
        ]
        run(db, Bundle(overrides))
        row = db.connection.execute("SELECT wsdc_id,method,status FROM identity_links").fetchone()
        assert tuple(row) == (None, "manual", "confirmed")
        overrides[0]["wsdc_id"] = "2"
        run(db, Bundle(overrides))
        row = db.connection.execute("SELECT wsdc_id,method FROM identity_links").fetchone()
        assert tuple(row) == (2, "manual")


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
        run(db, Bundle())
        row = db.connection.execute("SELECT wsdc_id,status FROM identity_links").fetchone()
        assert tuple(row) == (3, "probable")


def test_source_id_can_link_same_dancer_across_contests(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        entry(db.connection, "event/c2/L-8", "c2", "8")
        with patch("swingset.link.service._source_ids", return_value={"alex lee": 1}):
            run(db, Bundle())
        ids = [row[0] for row in db.connection.execute("SELECT wsdc_id FROM identity_links")]
        assert ids.count(1) == 2


def test_manual_override_beats_source_id(tmp_path) -> None:
    with open_database(tmp_path, lock=False) as db:
        seed(db.connection)
        entry(db.connection, "event/c1/L-7", "c1", "7")
        override = [
            {
                "entry_id": "event/c1/L-7",
                "wsdc_id": "2",
                "reason": "review",
                "author": "test",
                "date": "2026-01-05",
            }
        ]
        with patch("swingset.link.service._source_ids", return_value={"alex lee": 1}):
            run(db, Bundle(override))
        assert tuple(
            db.connection.execute("SELECT wsdc_id,method FROM identity_links").fetchone()
        ) == (2, "manual")
